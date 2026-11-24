// The /api/marks serverless function (api/marks.ts) with fetch stubbed: no network, no keys.
// Stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime". The function reads what the
// paper account holds, with the price of each stock now, so the page can mark the book between two closes.
// 1. Owner only: the caller's own session is checked with an owner-only RPC before anything touches Alpaca, on every
//    request, a cached answer included.
// 2. Paper and read-only: every Alpaca call in every test is a GET to /v2/positions of the paper host or to SPY's IEX
//    snapshot on the data host, and nothing else.
// 3. The engine shares the account's request budget: at most one upstream call per 10 seconds per instance, never a
//    retry, a hard timeout.
// 4. The answer is {available, as_of, positions: [{symbol, qty, price, prev_close}], spy} and nothing else.
// 5. SPY (stakeholder, 24 Nov 2026): its last trade and the close of the session before it, side by side with the
//    positions; a SPY read that fails never fails or delays the positions.
import { afterEach, describe, expect, it, vi } from "vitest";
import { KEEP_MS, MIN_GAP_MS, PAPER_URL, POSITIONS_URL, SPY_URL, TIMEOUT_MS, handle, newCache, spyOf } from "../api/marks";

import SOURCE from "../api/marks.ts?raw";
import VERCEL from "../vercel.json";

const T0 = Date.parse("2026-11-19T17:40:00.000Z"); // Thursday 12:40 ET: the market is open
const SECRET = "paper-secret-never-shown", KEYID = "PKTESTKEYID000";
const ENV = { VITE_SUPABASE_URL: "https://db.example.co", VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  ALPACA_API_KEY: KEYID, ALPACA_SECRET: SECRET };
const NONE = { available: false, as_of: null, positions: [] };

// Two rows as Alpaca sends them (GET /v2/positions on the paper account, 19 Nov 2026): every number is a string, and
// there is far more in a row than the page may see.
const row = (symbol: string, qty: string, price: string, prev: string, over: Record<string, unknown> = {}) => ({
  asset_id: "0dd35bf7-a49c-4068-bce0-8e40b700454d", symbol, exchange: "NYSE", asset_class: "us_equity", asset_marginable: true, qty, qty_available: qty,
  avg_entry_price: "160.59", side: "long", market_value: "3173.6", cost_basis: "3211.8", unrealized_pl: "-38.2", unrealized_plpc: "-0.01189",
  unrealized_intraday_pl: "-38", unrealized_intraday_plpc: "-0.01183", current_price: price, lastday_price: prev, change_today: "-0.01183", ...over });
const ROWS = [row("XOM", "20", "158.68", "160.58"), row("ABBV", "5", "282.365", "286.01")];
const MARKS = [{ symbol: "XOM", qty: 20, price: 158.68, prev_close: 160.58 }, { symbol: "ABBV", qty: 5, price: 282.365, prev_close: 286.01 }];
// SPY's snapshot as the data host sends it (GET /v2/stocks/SPY/snapshot?feed=iex): the day's bar and the one before.
const bar = (t: string, c: number) => ({ t, o: c, h: c, l: c, c, v: 1000, n: 10, vw: c });
const snap = (p = 662.1, t = "2026-11-19T17:39:59.5Z", daily = bar("2026-11-19T05:00:00Z", 661), prev = bar("2026-11-18T05:00:00Z", 659.3)) => ({
  symbol: "SPY", latestTrade: { t, x: "V", p, s: 100, c: ["@"], i: 1, z: "B" }, latestQuote: { t, ax: "V", ap: p + 0.01, as: 1, bx: "V", bp: p - 0.01, bs: 1 },
  minuteBar: bar("2026-11-19T17:39:00Z", p), dailyBar: daily, prevDailyBar: prev });
const SPY = { price: 662.1, as_of: "2026-11-19T17:39:59.500Z", prev_close: 659.3, prev_session: "2026-11-18" };

type Call = { url: string; method: string; body: string | null; headers: Record<string, string>; signal: AbortSignal | null };
type Stub = { rows?: unknown; status?: number; hang?: boolean; throws?: Error; owner?: { status: number; code: string };
  spy?: unknown; spyStatus?: number; spyThrows?: Error };
function stub(o: Stub = {}) {
  const calls: Call[] = [];
  const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? String(init.body) : null, headers: (init?.headers ?? {}) as Record<string, string>, signal: init?.signal ?? null });
    const res = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } });
    if (url.includes("/rest/v1/rpc/")) return o.owner ? res({ code: o.owner.code, message: "not authorised" }, o.owner.status) : res([{ id: 7, kind: "paper" }]);
    if (url.includes("alpaca.markets")) {
      if (o.hang) return new Promise<Response>(() => {});
      if (o.throws) throw o.throws;
      if (url === SPY_URL) {
        if (o.spyThrows) throw o.spyThrows;
        return res(o.spy !== undefined ? o.spy : snap(), o.spyStatus ?? o.status ?? 200);
      }
      return res(o.rows !== undefined ? o.rows : ROWS, o.status ?? 200);
    }
    return res({ message: "not found" }, 404);
  }) as typeof fetch;
  return { f, calls, set: (next: Stub) => { Object.assign(o, { rows: undefined, status: undefined, hang: undefined, throws: undefined, owner: undefined, spy: undefined, spyStatus: undefined, spyThrows: undefined }, next); } };
}
const req = (init: { method?: string; auth?: string | null } = {}) => new Request("https://kaibo.example/api/marks", { method: init.method ?? "GET",
  headers: init.auth === null ? {} : { Authorization: init.auth ?? "Bearer eyJhbGciOi.user.jwt" } });

/** The positions reads (the paper account) and the SPY reads (the data host), counted apart. */
const alpaca = (calls: Call[]) => calls.filter((c) => c.url === POSITIONS_URL);
const spyReads = (calls: Call[]) => calls.filter((c) => c.url === SPY_URL);
const anyAlpaca = (calls: Call[]) => calls.filter((c) => c.url.includes("alpaca.markets"));
const rpcs = (calls: Call[]) => calls.filter((c) => c.url.includes("/rest/v1/rpc/")).map((c) => c.url.split("/rpc/")[1]);
/** Read-only: nothing but GET, nothing but the paper account's positions and SPY's snapshot; never an order, never the
 *  account, never another symbol. */
function readOnly(calls: Call[]) {
  for (const c of anyAlpaca(calls)) {
    expect(c.method).toBe("GET");
    expect(["https://paper-api.alpaca.markets/v2/positions", "https://data.alpaca.markets/v2/stocks/SPY/snapshot?feed=iex"]).toContain(c.url);
    expect(c.body).toBeNull();
  }
  // every positions read has its SPY read beside it, and no more: SPY adds one call per window, never a retry
  expect(spyReads(calls).length).toBeLessThanOrEqual(alpaca(calls).length);
}
const all: Call[] = [];
afterEach(() => { readOnly(all); all.length = 0; vi.useRealTimers(); vi.restoreAllMocks(); });

/** One instance of the function: its own cache, its own clock, one stubbed network. */
function instance(o: Stub = {}, env: Record<string, string | undefined> = ENV) {
  const s = stub(o), cache = newCache();
  let t = T0;
  const get = async (r = req(), e = env) => {
    const before = s.calls.length;
    const resp = await handle(r, e, s.f, () => t, cache);
    all.push(...s.calls.slice(before));
    const text = await resp.text();
    return { resp, text, body: JSON.parse(text) };
  };
  return { get, calls: s.calls, set: s.set, at: (ms: number) => { t = T0 + ms; }, raw: (r = req()) => handle(r, env, s.f, () => t, cache) };
}

describe("GET /api/marks: who may ask", () => {
  it("takes GET only, and a signed-in caller only: nothing is called for anyone else", async () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      const i = instance();
      expect((await i.get(req({ method }))).resp.status).toBe(405);
      expect(i.calls).toHaveLength(0);
    }
    for (const auth of [null, "", "Basic abc", "Bearer ", "Bearer a b"]) {
      const i = instance();
      const r = await i.get(req({ auth }));
      expect(r.resp.status).toBe(401);
      expect(r.body).toEqual({ error: "sign in first" });
      expect(i.calls).toHaveLength(0);
    }
  });

  it("asks the owner-only RPC first, with the caller's own JWT and the publishable key", async () => {
    const i = instance();
    const r = await i.get();
    expect(r.resp.status).toBe(200);
    expect(i.calls.map((c) => c.url)).toEqual(["https://db.example.co/rest/v1/rpc/kb_runs", SPY_URL, POSITIONS_URL]);
    expect(i.calls[0].headers.Authorization).toBe("Bearer eyJhbGciOi.user.jwt");
    expect(i.calls[0].headers.apikey).toBe("sb_publishable_test"); // no service-role key exists
    expect(i.calls[0].method).toBe("POST");
    expect(i.calls[0].body).toBe("{}"); // kb_runs takes nothing and only reads
    expect(i.calls[0].signal).toBeInstanceOf(AbortSignal);
    // the check (4 s at most) and the read (4 s at most) fit the function's limit
    expect(2 * TIMEOUT_MS).toBeLessThan((VERCEL as { functions: Record<string, { maxDuration: number }> }).functions["api/marks.ts"].maxDuration * 1000);
  });

  it("someone who is not the owner gets 403 and never reaches Alpaca", async () => {
    const i = instance({ owner: { status: 403, code: "42501" } });
    const r = await i.get();
    expect(r.resp.status).toBe(403);
    expect(r.body).toEqual({ error: "not authorised" });
    expect(rpcs(i.calls)).toEqual(["kb_runs"]);
    expect(anyAlpaca(i.calls)).toHaveLength(0);
  });

  it("an expired or made-up session gets 401; a database that cannot be read gets 502; neither reaches Alpaca", async () => {
    const expired = instance({ owner: { status: 401, code: "PGRST301" } });
    expect((await expired.get()).resp.status).toBe(401);
    expect(alpaca(expired.calls)).toHaveLength(0);
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const down = instance({ owner: { status: 500, code: "XX000" } });
    const r = await down.get();
    expect(r.resp.status).toBe(502);
    expect(r.body).toEqual({ error: "the database could not be read" });
    expect(alpaca(down.calls)).toHaveLength(0);
    expect(err).toHaveBeenCalled();
  });

  it("the owner check is made on every request: a cached answer is never served to anyone else", async () => {
    const i = instance();
    expect((await i.get()).body.available).toBe(true);
    i.at(1_000);
    i.set({ owner: { status: 403, code: "42501" } });
    const r = await i.get(req({ auth: "Bearer someone.else.jwt" }));
    expect(r.resp.status).toBe(403);
    expect(r.text).not.toContain("XOM");
    expect(rpcs(i.calls)).toEqual(["kb_runs", "kb_runs"]);
    expect(i.calls[3].headers.Authorization).toBe("Bearer someone.else.jwt");
    expect(alpaca(i.calls)).toHaveLength(1);
  });
});

describe("GET /api/marks: the answer", () => {
  it("is {available, as_of, positions:[{symbol, qty, price, prev_close}], spy} with numbers, and nothing else", async () => {
    const i = instance();
    const r = await i.get();
    expect(r.body).toEqual({ available: true, as_of: "2026-11-19T17:40:00.000Z", positions: MARKS, spy: SPY });
    expect(Object.keys(r.body)).toEqual(["available", "as_of", "positions", "spy"]);
    expect(Object.keys(r.body.spy)).toEqual(["price", "as_of", "prev_close", "prev_session"]);
    expect(r.text).not.toMatch(/latestQuote|minuteBar|"ap"|"bp"/);
    for (const p of r.body.positions) expect(Object.keys(p)).toEqual(["symbol", "qty", "price", "prev_close"]);
    // nothing else of the broker's row, the account or the keys
    expect(r.text).not.toMatch(/asset_id|0dd35bf7|avg_entry|cost_basis|market_value|unrealized|account|side|exchange/);
    expect(r.resp.headers.get("cache-control")).toBe("no-store"); // never kept by a CDN: it is the owner's alone
    expect(r.resp.headers.get("content-type")).toBe("application/json");
  });

  it("sends Alpaca one GET with the paper keys, a timeout signal and no body", async () => {
    const i = instance();
    await i.get();
    const [c] = alpaca(i.calls);
    expect(c.url).toBe(`${PAPER_URL}/v2/positions`);
    expect(c.method).toBe("GET");
    expect(c.headers["APCA-API-KEY-ID"]).toBe(KEYID);
    expect(c.headers["APCA-API-SECRET-KEY"]).toBe(SECRET);
    expect(c.signal).toBeInstanceOf(AbortSignal);
    expect(c.body).toBeNull();
  });

  it("an account that holds nothing is an answer: available, with no positions", async () => {
    const r = await instance({ rows: [] }).get();
    expect(r.body).toEqual({ available: true, as_of: "2026-11-19T17:40:00.000Z", positions: [], spy: SPY });
  });

  it("a previous close the broker does not give is null; a short position keeps its sign (the page refuses it)", async () => {
    const r = await instance({ rows: [row("NBIS", "3", "101.5", "", { lastday_price: null }), row("TSLA", "-2", "400", "410", { side: "short" })] }).get();
    expect(r.body.positions).toEqual([{ symbol: "NBIS", qty: 3, price: 101.5, prev_close: null }, { symbol: "TSLA", qty: -2, price: 400, prev_close: 410 }]);
  });

  it("a row it cannot read makes the whole answer unavailable: never a list with a holding missing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bad: unknown[] = [
      { message: "forbidden" }, null, "[]",
      [row("XOM", "20", "158.68", "160.58"), row("ABBV", "5", "n/a", "286.01")],
      [row("XOM", "20", "0", "160.58")], [row("XOM", "twenty", "158.68", "160.58")],
      [row("XOM", "20", "158.68", "160.58"), { qty: "5", current_price: "3" }],
      [row("XOM", "20", "158.68", "160.58", { symbol: "x".repeat(40) })],
    ];
    for (const rows of bad) expect((await instance({ rows }).get()).body, JSON.stringify(rows).slice(0, 60)).toEqual(NONE);
    expect(warn).toHaveBeenCalled();
  });
});

describe("GET /api/marks: the engine's request budget", () => {
  it("one upstream call per 10 seconds per instance: every request inside the window gets the cached answer", async () => {
    expect(MIN_GAP_MS).toBe(10_000);
    const i = instance();
    const first = await i.get();
    for (const ms of [1, 500, 4_000, 9_999]) {
      i.at(ms);
      expect((await i.get()).body).toEqual(first.body); // the same prices and the same as_of: the page can tell their age
    }
    expect(alpaca(i.calls)).toHaveLength(1);
    i.at(10_000);
    i.set({ rows: [row("XOM", "20", "159.10", "160.58")] });
    const next = await i.get();
    expect(next.body).toEqual({ available: true, as_of: "2026-11-19T17:40:10.000Z", positions: [{ symbol: "XOM", qty: 20, price: 159.1, prev_close: 160.58 }], spy: SPY });
    expect(alpaca(i.calls)).toHaveLength(2);
    expect(spyReads(i.calls)).toHaveLength(2); // SPY in the same window: one more call per 10 s, no more
    expect(rpcs(i.calls)).toHaveLength(6); // the owner check is not cached
  });

  it("requests that arrive together share one call", async () => {
    const i = instance();
    const answers = await Promise.all(Array.from({ length: 20 }, () => i.raw().then((r) => r.json())));
    all.push(...i.calls);
    expect(alpaca(i.calls)).toHaveLength(1);
    expect(spyReads(i.calls)).toHaveLength(1);
    for (const a of answers) expect(a).toEqual({ available: true, as_of: "2026-11-19T17:40:00.000Z", positions: MARKS, spy: SPY });
  });

  it("a failed read is not tried again: unavailable until the window is over, then one new call", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const o of [{ status: 500 }, { status: 429 }, { status: 401 }, { throws: new TypeError("fetch failed") }] as Stub[]) {
      const i = instance(o);
      expect((await i.get()).body).toEqual(NONE);
      expect(alpaca(i.calls)).toHaveLength(1);
      i.at(3_000);
      expect((await i.get()).body).toEqual(NONE);
      expect(alpaca(i.calls)).toHaveLength(1);
      i.at(10_000);
      i.set({});
      expect((await i.get()).body.available).toBe(true);
      expect(alpaca(i.calls)).toHaveLength(2);
    }
    expect(warn).toHaveBeenCalled();
  });

  it("after a good answer, a failed refresh serves that answer with its own as_of for 30 seconds, then nothing", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(KEEP_MS).toBe(30_000);
    const i = instance();
    const good = (await i.get()).body;
    i.set({ status: 503 });
    for (const ms of [10_000, 20_000, 30_000]) {
      i.at(ms);
      expect((await i.get()).body, String(ms)).toEqual(good); // as_of stays 17:40:00: the page sees it grow old
    }
    i.at(30_001);
    expect((await i.get()).body).toEqual(NONE);
    i.at(40_000);
    expect((await i.get()).body).toEqual(NONE);
    expect(alpaca(i.calls)).toHaveLength(5); // 0, 10, 20, 30 and 40 s: never two inside one window
  });

  it("two minutes of requests every second cost 12 calls, with Alpaca up or down", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const o of [{}, { status: 500 }, { throws: new Error("boom") }] as Stub[]) {
      const i = instance(o);
      for (let s = 0; s < 120; s++) { i.at(s * 1000); await i.get(); }
      expect(alpaca(i.calls)).toHaveLength(12);
      const starts = alpaca(i.calls).length;
      expect(starts).toBeLessThanOrEqual(120_000 / MIN_GAP_MS);
    }
  });

  it("a hard timeout: an Alpaca that never answers is given up after 4 seconds, once", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.useFakeTimers({ now: T0 });
    expect(TIMEOUT_MS).toBeLessThanOrEqual(5_000);
    const s = stub({ hang: true }), cache = newCache();
    let done: unknown = null;
    void handle(req(), ENV, s.f, Date.now, cache).then((r) => r.json()).then((b) => { done = b; });
    await vi.advanceTimersByTimeAsync(TIMEOUT_MS - 1);
    expect(done).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toEqual(NONE);
    expect(alpaca(s.calls)).toHaveLength(1);
    expect(alpaca(s.calls)[0].signal!.aborted).toBe(true);
    expect(spyReads(s.calls)[0].signal!.aborted).toBe(true);
    // and no second try inside the window
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await (await handle(req(), ENV, s.f, Date.now, cache)).json()).toEqual(NONE);
    expect(alpaca(s.calls)).toHaveLength(1);
    all.push(...s.calls);
  });
});

describe("GET /api/marks: where it cannot answer", () => {
  it("without the Alpaca keys (preview deployments): {available:false}, cleanly, and no Alpaca call", async () => {
    for (const env of [{ ...ENV, ALPACA_API_KEY: undefined, ALPACA_SECRET: undefined }, { ...ENV, ALPACA_SECRET: "" }, { ...ENV, ALPACA_API_KEY: undefined }]) {
      const i = instance({}, env);
      const r = await i.get();
      expect(r.resp.status).toBe(200);
      expect(r.body).toEqual(NONE);
      expect(rpcs(i.calls)).toEqual(["kb_runs"]); // still owner only: a stranger learns nothing about the deployment
      expect(anyAlpaca(i.calls)).toHaveLength(0);
    }
    const stranger = instance({ owner: { status: 403, code: "42501" } }, { ...ENV, ALPACA_API_KEY: undefined });
    expect((await stranger.get()).resp.status).toBe(403);
  });

  it("without the database's address: {available:false} and nothing is called", async () => {
    const i = instance({}, { ALPACA_API_KEY: KEYID, ALPACA_SECRET: SECRET });
    expect((await i.get()).body).toEqual(NONE);
    expect(i.calls).toHaveLength(0);
  });

  it("refuses a base URL that is not the paper one and reads nothing", async () => {
    for (const live of ["https://api.alpaca.markets", "https://paper-api.alpaca.markets.evil.example", "http://paper-api.alpaca.markets",
      "https://paper-api.alpaca.markets/v2", "https://data.alpaca.markets"]) {
      const err = vi.spyOn(console, "error").mockImplementation(() => {});
      const i = instance({}, { ...ENV, ALPACA_BASE_URL: live });
      expect((await i.get()).body).toEqual(NONE);
      expect(anyAlpaca(i.calls)).toHaveLength(0);
      expect(JSON.stringify(err.mock.calls)).not.toContain(live); // nothing from the environment is logged
      err.mockRestore();
    }
    for (const paper of ["https://paper-api.alpaca.markets", "https://paper-api.alpaca.markets/", ""]) {
      const ok = instance({}, { ...ENV, ALPACA_BASE_URL: paper });
      expect((await ok.get()).body.available).toBe(true);
      expect(alpaca(ok.calls)[0].url).toBe("https://paper-api.alpaca.markets/v2/positions"); // the constant, never the variable
    }
  });

  it("keys never appear in a response or a log line", async () => {
    const spies = (["log", "info", "warn", "error"] as const).map((k) => vi.spyOn(console, k).mockImplementation(() => {}));
    const outs = [await instance().get(), await instance({ status: 500 }).get(), await instance({ status: 401 }).get(),
      await instance({ throws: new Error(`request to ${POSITIONS_URL} with ${SECRET} failed`) }).get(),
      await instance({ rows: { message: SECRET } }).get(), await instance({}, { ...ENV, ALPACA_BASE_URL: "https://api.alpaca.markets" }).get(),
      await instance({ owner: { status: 500, code: "XX000" } }).get()];
    for (const o of outs) { expect(o.text).not.toContain(SECRET); expect(o.text).not.toContain(KEYID); }
    const logged = JSON.stringify(spies.map((s) => s.mock.calls));
    expect(logged).not.toContain(SECRET);
    expect(logged).not.toContain(KEYID);
  });
});

describe("GET /api/marks: SPY now (stakeholder, 24 Nov 2026)", () => {
  it("its last trade, with the close of the session before that trade's: the day's bar once the day has traded", () => {
    expect(spyOf(snap())).toEqual(SPY);
  });
  it("before the day's first trade the day's bar is still the last session's: that one is the previous close", () => {
    // Monday 09:30:01 ET, the last IEX trade is Friday's: dailyBar = Friday, prevDailyBar = Thursday
    const s = spyOf(snap(655, "2026-11-20T20:59:59Z", bar("2026-11-20T05:00:00Z", 655.5), bar("2026-11-19T05:00:00Z", 661)));
    expect(s).toEqual({ price: 655, as_of: "2026-11-20T20:59:59.000Z", prev_close: 661, prev_session: "2026-11-19" });
    // the bars in either order, a holiday between them: the newest session before the trade's
    const h = spyOf(snap(600, "2026-11-27T15:00:00Z", bar("2026-11-25T05:00:00Z", 590), bar("2026-11-27T05:00:00Z", 599)));
    expect(h).toMatchObject({ prev_close: 590, prev_session: "2026-11-25" });
  });
  it("no bar before the trade's session (or unreadable bars): prev_close and prev_session are null", () => {
    for (const [d, p] of [[bar("2026-11-19T05:00:00Z", 661), null], [null, null], [{ t: "x", c: 1 }, { t: "2026-11-18T05:00:00Z", c: "n/a" }],
      [bar("2026-11-19T05:00:00Z", 661), bar("2026-11-18T05:00:00Z", 0)]] as unknown[][]) {
      const body = { ...snap(), dailyBar: d, prevDailyBar: p };
      expect(spyOf(body)).toMatchObject({ price: 662.1, prev_close: null, prev_session: null });
    }
  });
  it("a snapshot without a usable last trade cannot be read", () => {
    for (const body of [null, {}, { message: "forbidden" }, { ...snap(), latestTrade: null }, { ...snap(), latestTrade: { t: "2026-11-19T17:39:59Z", p: 0 } },
      { ...snap(), latestTrade: { t: "soon", p: 662 } }, { ...snap(), latestTrade: { t: "2026-11-19T17:39:59Z", p: "x" } }])
      expect(() => spyOf(body)).toThrow();
  });

  it("a SPY read that fails or cannot be read never touches the positions: spy is null", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (const o of [{ spyStatus: 403 }, { spyStatus: 429 }, { spy: { message: "subscription does not permit" } }, { spyThrows: new TypeError("fetch failed") }] as Stub[]) {
      const r = await instance(o).get();
      expect(r.body, JSON.stringify(o)).toEqual({ available: true, as_of: "2026-11-19T17:40:00.000Z", positions: MARKS, spy: null });
    }
    expect(JSON.stringify(warn.mock.calls)).toContain("SPY read failed");
  });

  it("after a good SPY, a failed one keeps the last with its own as_of (the page judges its age); never retried inside a window", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const i = instance();
    expect((await i.get()).body.spy).toEqual(SPY);
    i.at(10_000);
    i.set({ spyStatus: 500 });
    const r = await i.get();
    expect(r.body.as_of).toBe("2026-11-19T17:40:10.000Z");
    expect(r.body.spy).toEqual(SPY); // as_of 17:39:59.5: 10.5 s old now, and the page will drop it at 30 s
    i.at(15_000);
    await i.get();
    expect(spyReads(i.calls)).toHaveLength(2);
  });

  it("a positions read that fails is unavailable, whatever SPY says: SPY alone is never an answer", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect((await instance({ rows: { message: "nope" } }).get()).body).toEqual(NONE);
  });

  it("the SPY read carries the paper keys, a timeout signal and no body", async () => {
    const i = instance();
    await i.get();
    const [c] = spyReads(i.calls);
    expect(c.method).toBe("GET");
    expect(c.headers["APCA-API-KEY-ID"]).toBe(KEYID);
    expect(c.signal).toBeInstanceOf(AbortSignal);
    expect(c.body).toBeNull();
  });
});

describe("api/marks.ts cannot place, change or cancel an order", () => {
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\s\/\/ .*$/gm, "");
  it("the source names two Alpaca addresses: the paper host's /v2/positions and SPY's IEX snapshot on the data host", () => {
    expect(code).toContain(`const PAPER_URL = "https://paper-api.alpaca.markets"`);
    expect(code).toContain("const POSITIONS_URL = `${PAPER_URL}/v2/positions`");
    expect(code).toContain(`const DATA_URL = "https://data.alpaca.markets"`);
    expect(code).toContain("const SPY_URL = `${DATA_URL}/v2/stocks/SPY/snapshot?feed=iex`");
    expect(code.match(/alpaca\.markets/g)).toHaveLength(2);
    // Alpaca's two paths, and the database's RPC path
    expect(code.match(/\/v\d[\w/]*/g)).toEqual(["/v2/positions", "/v2/stocks/SPY/snapshot", "/v1/rpc/"]);
    // the read takes one of the two constants, never a built address
    expect(code).toMatch(/url: typeof POSITIONS_URL \| typeof SPY_URL/);
    expect(code.match(/alpacaGet\(f, [A-Z_]+/g)).toEqual(["alpacaGet(f, POSITIONS_URL", "alpacaGet(f, SPY_URL"]);
  });
  it("no order endpoint string exists in the file, nor the account's, nor another host's", () => {
    expect(code).not.toMatch(/orders?\b/i);
    expect(code).not.toMatch(/\/v2\/(orders|account|watchlists|assets)/);
    expect(code).not.toMatch(/(?<!paper-)api\.alpaca\.markets/);
  });
  it("Alpaca is sent GET and nothing else; the one POST is the owner check to the database", () => {
    expect(code).not.toMatch(/DELETE|PATCH|PUT/); // DELETE on /v2/positions would sell everything: it is not in the file
    expect(code.match(/method: "[A-Z]+"/g)).toEqual(['method: "GET"', 'method: "POST"']);
    expect(code).toMatch(/f\(url, \{ method: "GET"/);
    expect(code).toMatch(/\/rest\/v1\/rpc\/\$\{fn\}`, \{ method: "POST"/);
    expect(code.match(/\bf\(/g)).toHaveLength(2); // two places call the network: those two
  });
  it("never retries in a loop: there is no loop around the read", () => {
    expect(code).not.toMatch(/\bwhile\s*\(|\bfor\s*\(\s*;;|retry|attempt/i);
  });
});

describe("vercel.json", () => {
  const cfg = VERCEL as { rewrites: { source: string }[]; functions: Record<string, { maxDuration: number }> };
  it("lists the function, with a short limit, and the SPA rewrite does not catch it", () => {
    expect(cfg.functions["api/marks.ts"].maxDuration).toBeLessThanOrEqual(10);
    expect(new RegExp(`^${cfg.rewrites[0].source}$`).test("/api/marks")).toBe(false);
  });
});
