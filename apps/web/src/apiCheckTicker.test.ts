// The /api/check-ticker serverless function (api/check-ticker.ts) with fetch stubbed: no network, no keys.
// 1. Parity: the verdict, stats and sector fund give exactly the answers of api/_tickercheck_parity.json, the file
//    tests/unit/test_tickercheck_parity.py pins engine/tickercheck.py to. The two implementations cannot drift.
// 2. The handler: owner only (kb_check_ticker first), "unavailable" without the Alpaca keys, a live URL refused, the
//    answer written with kb_ticker_check_answer, a failed read counted as an attempt, keys never logged or returned.
// 3. Paper and read-only: every Alpaca call in every test is a GET to an asset or bars endpoint, never orders.
import { afterEach, describe, expect, it, vi } from "vitest";
import { DATA_URL, PAPER_URL, RULES, fixed, handle, sectorEtf, stats, verdict } from "../api/check-ticker";

import SHARED_JSON from "../api/_tickercheck_parity.json";
import SOURCE from "../api/check-ticker.ts?raw";
import VERCEL from "../vercel.json";

const SHARED = SHARED_JSON as any;

describe("parity with engine/tickercheck.py (shared JSON)", () => {
  it("uses the shared thresholds, exchanges, reference funds and sector map", () => {
    expect(RULES).toEqual(SHARED.rules);
  });
  for (const c of SHARED.verdict)
    it(`verdict: ${c.name}`, () => {
      const f = c.floors ?? {};
      expect(verdict(c.symbol, c.asset, c.stats, { minAdv: f.min_adv, minPrice: f.min_price })).toEqual(c.expect);
    });
  for (const c of SHARED.stats)
    it(`stats: ${c.name}`, () => {
      const got = stats(c.bars, c.before);
      expect(got.sessions).toBe(c.expect.sessions);
      expect(got.last_close).toBe(c.expect.last_close);
      expect(got.adv20_usd).toBeCloseTo(c.expect.adv20_usd, 4);
    });
  it("prints numbers as Python's ,.2f and ,.0f do (exact binary value, ties to even)", () => {
    const bad = (SHARED.formats as [number, number, string][]).filter(([x, d, want]) => fixed(x, d) !== want);
    expect(bad).toEqual([]);
  });
  for (const c of SHARED.sector)
    it(`sector fund: ${JSON.stringify(c.industry)}`, () => expect(sectorEtf(c.industry)).toBe(c.expect));
});

// ------------------------------------------------------------------ the handler
const NOW = new Date("2026-11-18T20:00:00Z"); // 15:00 ET, market open: today's bar must not count
const SECRET = "paper-secret-never-shown", KEYID = "PKTESTKEYID000";
const ENV = { VITE_SUPABASE_URL: "https://db.example.co", VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  ALPACA_API_KEY: KEYID, ALPACA_SECRET: SECRET };
const ASSET = { symbol: "NBIS", class: "us_equity", exchange: "NASDAQ", status: "active", tradable: true, name: "Nebius Group N.V." };
const PENDING = { symbol: "NBIS", status: "pending", reason: null, etf: null, in_catalogue: false, checked_on: "2026-11-18", detail: {} };

/** Weekday daily bars before `last` (ET midnight timestamps, as Alpaca sends them), plus one for today if asked. */
function dailyBars(n: number, { price = 40, volume = 5e6, today = true } = {}) {
  const out: { t: string; o: number; h: number; l: number; c: number; v: number }[] = [];
  const d = new Date("2026-11-17T05:00:00Z");
  while (out.length < n) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.unshift({ t: d.toISOString().replace(".000", ""), o: price, h: price, l: price, c: price, v: volume });
    d.setUTCDate(d.getUTCDate() - 1);
  }
  if (today) out.push({ t: "2026-11-18T05:00:00Z", o: 1, h: 1, l: 1, c: 1, v: 1 }); // partial: must be ignored
  return out;
}

type Call = { url: string; method: string; body: Record<string, unknown> | null; headers: Record<string, string> };
function stub(o: { check?: unknown; checkError?: { status: number; code: string }; asset?: unknown; assetStatus?: number;
  bars?: unknown[]; barsStatus?: number; pageSize?: number; finnhub?: unknown; finnhubStatus?: number; answer?: (b: Record<string, unknown>) => unknown } = {}) {
  const calls: Call[] = [];
  const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const res = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } });
    if (url.includes("/rest/v1/rpc/")) {
      const fn = url.split("/rpc/")[1];
      if (fn === "kb_check_ticker") return o.checkError ? res({ code: o.checkError.code, message: "not authorised" }, o.checkError.status) : res(o.check ?? PENDING);
      if (fn === "kb_ticker_check_answer")
        return res(o.answer ? o.answer(body) : { ok: true, check: { symbol: body.p_symbol, status: body.p_status === "failed" ? "pending" : body.p_status,
          reason: body.p_reason, etf: body.p_etf, name: body.p_name, in_catalogue: false, checked_on: "2026-11-18", detail: body.p_detail } });
    }
    if (url.startsWith(`${PAPER_URL}/v2/assets/`)) return o.assetStatus ? res({ message: "x" }, o.assetStatus) : res(o.asset ?? ASSET);
    if (url.startsWith(`${DATA_URL}/v2/stocks/`)) {
      if (o.barsStatus) return res({ message: "x" }, o.barsStatus);
      const all = o.bars ?? dailyBars(100), size = o.pageSize ?? 10_000;
      const from = Number(new URL(url).searchParams.get("page_token") ?? 0);
      const page = all.slice(from, from + size);
      return res({ bars: page, symbol: "NBIS", next_page_token: from + size < all.length ? String(from + size) : null });
    }
    if (url.startsWith("https://finnhub.io/")) return o.finnhubStatus ? res({}, o.finnhubStatus) : res(o.finnhub ?? {});
    return res({ message: "not found" }, 404);
  }) as typeof fetch;
  return { f, calls };
}
const req = (body: unknown = { symbol: "NBIS" }, init: { method?: string; auth?: string | null } = {}) =>
  new Request("https://kaibot.example/api/check-ticker", { method: init.method ?? "POST",
    headers: { ...(init.auth === null ? {} : { Authorization: init.auth ?? "Bearer eyJhbGciOi.user.jwt" }), "content-type": "application/json" },
    body: (init.method ?? "POST") === "POST" ? JSON.stringify(body) : undefined });

const alpaca = (calls: Call[]) => calls.filter((c) => c.url.startsWith(PAPER_URL) || c.url.startsWith(DATA_URL) || c.url.includes("alpaca.markets"));
const rpcs = (calls: Call[]) => calls.filter((c) => c.url.includes("/rest/v1/rpc/")).map((c) => c.url.split("/rpc/")[1]);
/** Paper and read-only: only GETs, only the asset record and daily bars; never orders, positions or the account. */
function readOnly(calls: Call[]) {
  for (const c of alpaca(calls)) {
    expect(c.method).toBe("GET");
    expect(c.url).not.toMatch(/orders|positions|account|watchlists|(?<!paper-)api\.alpaca\.markets/);
    expect(c.url.startsWith(`${PAPER_URL}/v2/assets/`) || /^https:\/\/data\.alpaca\.markets\/v2\/stocks\/[A-Z.]+\/bars\?/.test(c.url)).toBe(true);
  }
}
const all: Call[] = [];
afterEach(() => { readOnly(all); all.length = 0; vi.restoreAllMocks(); });
async function run(o: Parameters<typeof stub>[0] = {}, env: Record<string, string | undefined> = ENV, r = req()) {
  const s = stub(o);
  const resp = await handle(r, env, s.f, NOW);
  all.push(...s.calls);
  const text = await resp.text();
  return { resp, text, body: JSON.parse(text), calls: s.calls };
}

describe("POST /api/check-ticker", () => {
  it("takes POST only, and a signed-in caller only", async () => {
    expect((await run({}, ENV, req(undefined, { method: "GET" }))).resp.status).toBe(405);
    const anon = await run({}, ENV, req({ symbol: "NBIS" }, { auth: null }));
    expect(anon.resp.status).toBe(401);
    expect(anon.calls).toHaveLength(0);
  });

  it("asks kb_check_ticker first: someone who is not the owner never reaches Alpaca", async () => {
    const r = await run({ checkError: { status: 403, code: "42501" } });
    expect(r.resp.status).toBe(403);
    expect(rpcs(r.calls)).toEqual(["kb_check_ticker"]);
    expect(alpaca(r.calls)).toHaveLength(0);
    // the caller's own JWT and the publishable key: no service-role key exists
    expect(r.calls[0].headers.Authorization).toBe("Bearer eyJhbGciOi.user.jwt");
    expect(r.calls[0].headers.apikey).toBe("sb_publishable_test");
  });

  it("an answer that is already there (catalogue stock or today's cache) is returned without reading Alpaca", async () => {
    const r = await run({ check: { symbol: "AAPL", status: "accepted", in_catalogue: true, reason: null } }, ENV, req({ symbol: "aapl" }));
    expect(r.body).toMatchObject({ status: "answered", check: { symbol: "AAPL", status: "accepted" } });
    expect(r.calls[0].body).toEqual({ p_symbol: "AAPL" });
    expect(alpaca(r.calls)).toHaveLength(0);
  });

  it("without the Alpaca keys: 'checks unavailable here', and the row stays pending for the Mac worker", async () => {
    const r = await run({}, { ...ENV, ALPACA_API_KEY: undefined, ALPACA_SECRET: undefined });
    expect(r.resp.status).toBe(200);
    expect(r.body).toMatchObject({ status: "unavailable", reason: "checks unavailable here", check: { status: "pending" } });
    expect(rpcs(r.calls)).toEqual(["kb_check_ticker"]);
    expect(alpaca(r.calls)).toHaveLength(0);
  });

  it("refuses a live (non-paper) Alpaca URL and reads nothing", async () => {
    for (const live of ["https://api.alpaca.markets", "https://paper-api.alpaca.markets.evil.example", "http://paper-api.alpaca.markets"]) {
      const err = vi.spyOn(console, "error").mockImplementation(() => {});
      const r = await run({}, { ...ENV, ALPACA_BASE_URL: live });
      expect(r.body).toMatchObject({ status: "unavailable", reason: "checks unavailable here" });
      expect(alpaca(r.calls)).toHaveLength(0);
      expect(rpcs(r.calls)).toEqual(["kb_check_ticker"]);
      expect(JSON.stringify(err.mock.calls)).not.toContain(live); // nothing from the environment is logged
      err.mockRestore();
    }
    const ok = await run({}, { ...ENV, ALPACA_BASE_URL: "https://paper-api.alpaca.markets/" });
    expect(ok.body.status).toBe("checked");
  });

  it("checks a pending ticker: paper asset record, completed daily SIP bars, verdict, written by the owner-only RPC", async () => {
    const r = await run();
    expect(r.body).toMatchObject({ status: "checked", check: { symbol: "NBIS", status: "accepted", etf: "SPY" } });
    const [asset, bars] = alpaca(r.calls);
    expect(asset.url).toBe(`${PAPER_URL}/v2/assets/NBIS`);
    const q = new URL(bars.url).searchParams;
    expect(bars.url.startsWith(`${DATA_URL}/v2/stocks/NBIS/bars?`)).toBe(true);
    expect(q.get("timeframe")).toBe("1Day");
    expect(q.get("feed")).toBe("sip");
    expect(q.get("adjustment")).toBe("split");
    // SIP only when at least 15 minutes old: the window ends 16 minutes ago, as tickercheck.check_symbol does
    expect(q.get("end")).toBe("2026-11-18T19:44:00Z");
    expect(q.get("start")).toBe("2026-06-21T19:44:00Z");
    expect(asset.headers["APCA-API-KEY-ID"]).toBe(KEYID);
    expect(asset.headers["APCA-API-SECRET-KEY"]).toBe(SECRET);
    expect(rpcs(r.calls)).toEqual(["kb_check_ticker", "kb_ticker_check_answer"]);
    const ans = r.calls.find((c) => c.url.endsWith("/kb_ticker_check_answer"))!.body!;
    expect(ans).toMatchObject({ p_symbol: "NBIS", p_status: "accepted", p_reason: null, p_etf: "SPY", p_name: "Nebius Group N.V.", p_industry: null });
    // today's partial bar (close 1) is not read: 100 completed sessions at $40
    expect(ans.p_detail).toMatchObject({ sessions: 100, last_close: 40, adv20_usd: 200e6, exchange: "NASDAQ", as_of: "2026-11-18",
      min_adv_usd: 50e6, min_price_usd: 5, min_sessions: 60, checked_by: "vercel" });
  });

  it("follows the bars' next_page_token", async () => {
    const r = await run({ pageSize: 30 });
    expect(alpaca(r.calls).filter((c) => c.url.includes("/bars?"))).toHaveLength(4);
    expect(r.calls.find((c) => c.url.endsWith("/kb_ticker_check_answer"))!.body!.p_detail).toMatchObject({ sessions: 100 });
  });

  it("refuses with the same reason strings as Python", async () => {
    const unknown = await run({ assetStatus: 404 });
    expect(unknown.body.check).toMatchObject({ status: "refused", reason: "We could not find NBIS at the broker. Check the spelling." });
    expect(alpaca(unknown.calls)).toHaveLength(1); // no bars for an unknown ticker
    const thin = await run({ bars: dailyBars(100, { volume: 1e6 }) });
    expect(thin.body.check).toMatchObject({ status: "refused", etf: null,
      reason: "NBIS trades about $40M a day; Kaibot needs more than $50M so orders fill at a fair price." });
    const young = await run({ bars: dailyBars(30) });
    expect(young.body.check.reason).toBe("NBIS has only 30 trading days of price history; Kaibot needs at least 60.");
  });

  it("an unreadable broker is a failed attempt (the RPC counts it and errors after 3), never a refusal", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const o of [{ assetStatus: 500 }, { barsStatus: 429 }]) {
      const r = await run(o);
      const ans = r.calls.find((c) => c.url.endsWith("/kb_ticker_check_answer"))!.body!;
      expect(ans).toMatchObject({ p_symbol: "NBIS", p_status: "failed", p_reason: null, p_etf: null });
      expect(r.body.status).toBe("failed");
    }
    expect(JSON.stringify(warn.mock.calls)).not.toContain(SECRET);
  });

  it("the sector fund comes from Finnhub only when its key is set; a failed lookup falls back to SPY", async () => {
    const tech = await run({ finnhub: { name: "Nebius Group", finnhubIndustry: "Technology" } }, { ...ENV, FINNHUB_API_KEY: "fh-test" });
    expect(tech.body.check).toMatchObject({ status: "accepted", etf: "XLK" });
    expect(tech.calls.find((c) => c.url.endsWith("/kb_ticker_check_answer"))!.body).toMatchObject({ p_name: "Nebius Group", p_industry: "Technology" });
    const down = await run({ finnhubStatus: 500 }, { ...ENV, FINNHUB_API_KEY: "fh-test" });
    expect(down.body.check).toMatchObject({ status: "accepted", etf: "SPY" });
    expect(down.calls.find((c) => c.url.endsWith("/kb_ticker_check_answer"))!.body!.p_detail).toMatchObject({ note: "industry lookup failed (HttpError): using the market fund" });
    const none = await run({ finnhub: { finnhubIndustry: "Technology" } });
    expect(none.calls.some((c) => c.url.includes("finnhub"))).toBe(false);
    // Finnhub is never asked about a refused ticker
    const refused = await run({ assetStatus: 404 }, { ...ENV, FINNHUB_API_KEY: "fh-test" });
    expect(refused.calls.some((c) => c.url.includes("finnhub"))).toBe(false);
  });

  it("an answer the RPC will not take (row no longer pending) returns what the database has", async () => {
    const r = await run({ answer: () => ({ ok: false, reason: "not pending", check: { symbol: "NBIS", status: "accepted", etf: "XLK", reason: null } }) });
    expect(r.body).toMatchObject({ status: "answered", check: { status: "accepted", etf: "XLK" } });
  });

  it("a bad symbol never reaches Alpaca", async () => {
    for (const s of ["", "TOOLONG", "AB1", "../v2/orders", 42]) {
      const r = await run({ check: { symbol: String(s), status: "refused", reason: "x" } }, ENV, req({ symbol: s }));
      expect([200, 400]).toContain(r.resp.status);
      expect(alpaca(r.calls)).toHaveLength(0);
    }
  });

  it("keys never appear in a response or a log line", async () => {
    const spies = (["log", "info", "warn", "error"] as const).map((k) => vi.spyOn(console, k).mockImplementation(() => {}));
    const outs = [await run(), await run({ assetStatus: 500 }), await run({ barsStatus: 401 }), await run({}, { ...ENV, ALPACA_BASE_URL: "https://api.alpaca.markets" })];
    for (const o of outs) { expect(o.text).not.toContain(SECRET); expect(o.text).not.toContain(KEYID); }
    const logged = JSON.stringify(spies.map((s) => s.mock.calls));
    expect(logged).not.toContain(SECRET);
    expect(logged).not.toContain(KEYID);
  });

  it("the source has no order, position or account endpoint and sends Alpaca nothing but GET", () => {
    const code = SOURCE.replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toMatch(/\/v2\/(orders|positions|account)/);
    expect(code).not.toMatch(/(?<!paper-)api\.alpaca\.markets/);
    expect(code).toContain(`const PAPER_URL = "https://paper-api.alpaca.markets"`);
  });
});

describe("vercel.json", () => {
  const cfg = VERCEL as { rewrites: { source: string }[]; functions: Record<string, { maxDuration: number }> };
  it("the SPA rewrite does not catch /api/*", () => {
    const re = new RegExp(`^${cfg.rewrites[0].source}$`);
    expect(re.test("/strategy")).toBe(true);
    expect(re.test("/api/check-ticker")).toBe(false);
    expect(cfg.functions["api/check-ticker.ts"].maxDuration).toBeLessThanOrEqual(30);
  });
});
