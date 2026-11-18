// POST /api/check-ticker {symbol} — answers a 'My lists' ticker check on Vercel, so it works with the Mac off
// (stakeholder, 18 Nov 2026). The same check as src/kaibot/engine/tickercheck.py (check_symbol + verdict + stats +
// sector_etf); api/_tickercheck_parity.json pins both to the same thresholds and answers (two tests, one per language).
//
// 1. kb_check_ticker(symbol) with the caller's own session and the publishable key (no service-role key exists): it is
//    owner-only, queues today's row if there is none, and answers from the day's cache. Anything but `pending` is
//    returned as it is: no Alpaca call.
// 2. No ALPACA_API_KEY / ALPACA_SECRET here (preview deployments, local dev): "checks unavailable here"; the row stays
//    pending for the Mac worker (scripts/run_ticker_checks.py).
// 3. Otherwise read Alpaca PAPER, GET only: the asset record (paper-api /v2/assets/{symbol}) and completed daily SIP
//    bars (data /v2/stocks/{symbol}/bars), the window ending 16 minutes ago (SIP only when >= 15 min old) and only
//    sessions before today (ET) counted. Finnhub's industry gives the sector fund only when FINNHUB_API_KEY is set;
//    otherwise SPY, as Python does for an unknown industry.
// 4. Write the answer with kb_ticker_check_answer (owner-only SECURITY DEFINER; it updates only a pending or errored
//    row of today). A failed read is an attempt: after 3 the row becomes `error`, which the owner can retry.
// This never places, changes or cancels an order: no order endpoint exists in this file. Keys are never logged or
// returned. The engine checks every custom ticker again at each session open, whatever this cache says.

declare const process: { env: Record<string, string | undefined> };

type Env = Record<string, string | undefined>;
type Fetch = typeof fetch;
type Obj = Record<string, unknown>;

export const PAPER_URL = "https://paper-api.alpaca.markets";
export const DATA_URL = "https://data.alpaca.markets";
const FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2";

// ------------------------------------------------------------------ the shared rules (== _tickercheck_parity.json)
// Copied, not imported, so the function stays one self-contained file on Vercel; apiCheckTicker.test.ts checks this
// object equals the JSON's `rules`, and test_tickercheck_parity.py checks Python's constants equal the same JSON.
export const RULES = {
  LEVEL_MIN_ADV: 50_000_000, LEVEL_MIN_PRICE: 5, MIN_SESSIONS: 60, ADV_SESSIONS: 20, MAX_ATTEMPTS: 3, FALLBACK_ETF: "SPY",
  TICKER_RE: "[A-Z]{1,5}(\\.[A-Z])?",
  MAJOR_EXCHANGES: ["AMEX", "ARCA", "BATS", "NASDAQ", "NYSE"],
  REFERENCE_ETFS: ["SPY", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"],
  INDUSTRY_ETF: {
    "aerospace & defense": "XLI", airlines: "XLI", "auto components": "XLY", automobiles: "XLY", banking: "XLF",
    beverages: "XLP", biotechnology: "XLV", building: "XLI", chemicals: "XLB", "commercial services & supplies": "XLI",
    communications: "XLK", construction: "XLI", "consumer products": "XLP", distributors: "XLY",
    "diversified consumer services": "XLY", "electrical equipment": "XLI", "electronic equipment": "XLK", energy: "XLE",
    entertainment: "XLC", "financial services": "XLF", "food & staples retailing": "XLP", "food products": "XLP",
    "health care": "XLV", "hotels, restaurants & leisure": "XLY", "industrial conglomerates": "XLI", insurance: "XLF",
    "interactive media & services": "XLC", "it services": "XLK", "leisure products": "XLY",
    "life sciences tools & services": "XLV", "logistics & transportation": "XLI", machinery: "XLI", marine: "XLI",
    media: "XLC", "metals & mining": "XLB", packaging: "XLB", "paper & forest": "XLB", pharmaceuticals: "XLV",
    "professional services": "XLI", "real estate": "XLRE", retail: "XLY", "road & rail": "XLI", semiconductors: "XLK",
    software: "XLK", technology: "XLK", telecommunication: "XLC", "textiles, apparel & luxury goods": "XLY",
    tobacco: "XLP", "trading companies & distributors": "XLI", "transportation infrastructure": "XLI", utilities: "XLU",
  } as Record<string, string>,
};
const TICKER = new RegExp(`^(?:${RULES.TICKER_RE})$`);
const REFS = new Set(RULES.REFERENCE_ETFS);
const MAJOR = new Set(RULES.MAJOR_EXCHANGES);

// Python's number formats. `,.2f` / `,.0f` round the EXACT binary value, ties to even (1234.565 is stored as
// 1234.56500000000005... so it shows 1,234.57; 48.5 is exact so it shows 48). Intl rounds the shortest decimal
// instead, so this rounds the exact expansion (toFixed(100) is exact for these magnitudes) by hand.
export function fixed(x: number, d: number): string {
  if (!Number.isFinite(x)) return String(x);
  const [int, frac = ""] = Math.abs(x).toFixed(100).split(".");
  const rest = frac.slice(d);
  let n = BigInt(int + frac.slice(0, d));
  const tail = /[1-9]/.test(rest.slice(1));
  if (rest[0] > "5" || (rest[0] === "5" && (tail || n % 2n === 1n))) n += 1n;
  const digits = n.toString().padStart(d + 1, "0");
  const whole = d ? digits.slice(0, -d) : digits, part = d ? `.${digits.slice(-d)}` : "";
  return `${x < 0 || Object.is(x, -0) ? "-" : ""}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${part}`;
}
const g = (x: number) => String(Number(x.toPrecision(6)));
const num = (v: unknown) => (v === null || v === undefined || v === "" || v === false ? 0 : Number(v)); // Python's `x or 0`

export interface Stats { sessions: number; adv20_usd: number; last_close: number | null }
export interface Asset { symbol?: unknown; class?: unknown; exchange?: unknown; status?: unknown; tradable?: unknown; name?: unknown }

/** tickercheck.verdict: (accepted, plain reason when refused). The floors may only be raised above the level's. */
export function verdict(symbol: unknown, asset: Asset | null | undefined, stats: Partial<Stats> | Obj,
  floors: { minAdv?: number; minPrice?: number } = {}): { ok: boolean; reason: string | null } {
  const minAdv = Math.max(floors.minAdv ?? RULES.LEVEL_MIN_ADV, RULES.LEVEL_MIN_ADV);
  const minPrice = Math.max(floors.minPrice ?? RULES.LEVEL_MIN_PRICE, RULES.LEVEL_MIN_PRICE);
  const no = (reason: string) => ({ ok: false, reason });
  if (typeof symbol !== "string" || !TICKER.test(symbol)) return no("That does not look like a US stock ticker (1 to 5 letters, for example PLTR).");
  if (REFS.has(symbol)) return no(`${symbol} is a reference fund Kaibot compares stocks against, so it cannot be traded.`);
  if (!asset || asset.symbol !== symbol) return no(`We could not find ${symbol} at the broker. Check the spelling.`);
  if (asset.class !== "us_equity" || !MAJOR.has(String(asset.exchange))) return no(`${symbol} is not a stock listed on a major US exchange.`);
  if (asset.status !== "active" || asset.tradable !== true) return no(`${symbol} cannot be traded at the broker right now.`);
  const st = stats as Obj;
  const n = Math.trunc(num(st.sessions)), adv = num(st.adv20_usd), px = st.last_close as number | null | undefined;
  if (n < RULES.MIN_SESSIONS) return no(`${symbol} has only ${n} trading days of price history; Kaibot needs at least ${RULES.MIN_SESSIONS}.`);
  const known = px !== null && px !== undefined && Number.isFinite(Number(px));
  if (!known || Number(px) < minPrice) {
    const shown = known ? `$${fixed(Number(px), 2)}` : "an unknown price";
    return no(`${symbol} closed at ${shown}; Kaibot only trades stocks above $${g(minPrice)}.`);
  }
  if (!(adv > minAdv))
    return no(`${symbol} trades about $${fixed(adv / 1e6, 0)}M a day; Kaibot needs more than $${fixed(minAdv / 1e6, 0)}M so orders fill at a fair price.`);
  return { ok: true, reason: null };
}

export const etDay = (t: Date) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(t);

/** tickercheck.stats on daily bars ({t, c, v}): sessions, 20-session average $ volume and last close of sessions
 *  BEFORE `before` (an ET date, YYYY-MM-DD). Nothing from the decision day itself is read. */
export function stats(bars: { t: string; c: number; v: number }[] | null | undefined, before: string): Stats {
  const none: Stats = { sessions: 0, adv20_usd: 0, last_close: null };
  if (!bars?.length) return none;
  const dollars = new Map<string, number>();
  let last: { ts: number; c: number } | null = null;
  for (const b of bars) {
    const ts = Date.parse(b.t);
    const day = etDay(new Date(ts));
    if (!(day < before)) continue;
    dollars.set(day, (dollars.get(day) ?? 0) + Number(b.c) * Number(b.v));
    if (!last || ts > last.ts) last = { ts, c: Number(b.c) };
  }
  if (!last) return none;
  const days = [...dollars.keys()].sort();
  const tail = days.slice(-RULES.ADV_SESSIONS).map((d) => dollars.get(d)!);
  return { sessions: days.length, adv20_usd: tail.length === RULES.ADV_SESSIONS ? tail.reduce((a, b) => a + b, 0) / tail.length : 0, last_close: last.c };
}

/** tickercheck.sector_etf: the sector fund for the sector-adjusted features; SPY when the industry is unknown. */
export function sectorEtf(industry: string | null | undefined): string {
  const etf = RULES.INDUSTRY_ETF[(industry ?? "").trim().toLowerCase()] ?? RULES.FALLBACK_ETF;
  return REFS.has(etf) ? etf : RULES.FALLBACK_ETF;
}

// ------------------------------------------------------------------ vendors (GET only)
class HttpError extends Error { constructor(public status: number) { super(`HTTP ${status}`); this.name = "HttpError"; } }

/** The only way this file talks to Alpaca: GET, to the paper trading host's asset records or the data host's bars. */
async function alpacaGet(f: Fetch, url: string, keyId: string, secret: string): Promise<{ status: number; body: unknown }> {
  const ok = url.startsWith(`${PAPER_URL}/v2/assets/`) || (url.startsWith(`${DATA_URL}/v2/stocks/`) && url.includes("/bars?"));
  if (!ok) throw new Error("not an allowed Alpaca read");
  const r = await f(url, { method: "GET", signal: AbortSignal.timeout(8000),
    headers: { "APCA-API-KEY-ID": keyId, "APCA-API-SECRET-KEY": secret, Accept: "application/json" } });
  const body = await r.json().catch(() => null);
  return { status: r.status, body };
}

async function readAsset(f: Fetch, symbol: string, keyId: string, secret: string): Promise<Asset | null> {
  const r = await alpacaGet(f, `${PAPER_URL}/v2/assets/${encodeURIComponent(symbol)}`, keyId, secret);
  if (r.status === 404) return null; // unknown at the broker: refused, as AlpacaPaper.asset
  if (r.status < 200 || r.status >= 300) throw new HttpError(r.status);
  return r.body && typeof r.body === "object" ? (r.body as Asset) : null;
}

async function readBars(f: Fetch, symbol: string, start: Date, end: Date, keyId: string, secret: string) {
  const iso = (t: Date) => t.toISOString().replace(/\.\d{3}Z$/, "Z");
  const out: { t: string; c: number; v: number }[] = [];
  let token: string | null = null;
  for (let page = 0; page < 20; page++) {
    const q = new URLSearchParams({ timeframe: "1Day", start: iso(start), end: iso(end), limit: "10000", feed: "sip", adjustment: "split" });
    if (token) q.set("page_token", token);
    const r = await alpacaGet(f, `${DATA_URL}/v2/stocks/${encodeURIComponent(symbol)}/bars?${q}`, keyId, secret);
    if (r.status < 200 || r.status >= 300) throw new HttpError(r.status);
    const body = (r.body ?? {}) as { bars?: unknown; next_page_token?: unknown };
    for (const b of Array.isArray(body.bars) ? body.bars : []) {
      const x = b as Obj;
      if (typeof x.t === "string") out.push({ t: x.t, c: Number(x.c), v: Number(x.v) });
    }
    token = typeof body.next_page_token === "string" && body.next_page_token ? body.next_page_token : null;
    if (!token) break;
  }
  return out;
}

async function readIndustry(f: Fetch, symbol: string, key: string): Promise<[string | null, string | null] | null> {
  const r = await f(`${FINNHUB_PROFILE}?${new URLSearchParams({ symbol, token: key })}`, { method: "GET", signal: AbortSignal.timeout(6000) });
  if (!r.ok) throw new HttpError(r.status);
  const p = (await r.json().catch(() => null)) as Obj | null;
  if (!p || typeof p !== "object" || !Object.keys(p).length) return null;
  const s = (v: unknown) => (typeof v === "string" && v ? v.slice(0, 200) : null);
  return [s(p.name), s(p.finnhubIndustry)];
}

/** tickercheck.check_symbol, on Alpaca paper (GET only). Throws when the broker or the bars cannot be read. */
export async function checkSymbol(symbol: string, env: Env, f: Fetch, now: Date, keyId: string, secret: string) {
  const today = etDay(now);
  const asset = await readAsset(f, symbol, keyId, secret);
  const end = new Date(now.getTime() - 16 * 60_000); // SIP only when at least 15 minutes old
  const bars = asset ? await readBars(f, symbol, new Date(end.getTime() - 150 * 864e5), end, keyId, secret) : null;
  const st = stats(bars, today);
  const { ok, reason } = verdict(symbol, asset, st);
  let name = typeof asset?.name === "string" ? asset.name.slice(0, 200) : null, industry: string | null = null, note: string | null = null;
  const fh = env.FINNHUB_API_KEY;
  if (ok && fh) {
    try {
      const got = await readIndustry(f, symbol, fh);
      if (got) { name = got[0] || name; industry = got[1]; }
    } catch (e) { note = `industry lookup failed (${(e as Error).name}): using the market fund`; }
  }
  return { status: ok ? "accepted" : "refused", reason, etf: ok ? sectorEtf(industry) : null, name, industry,
    detail: { ...st, exchange: typeof asset?.exchange === "string" ? asset.exchange : null, as_of: today,
      min_adv_usd: RULES.LEVEL_MIN_ADV, min_price_usd: RULES.LEVEL_MIN_PRICE, min_sessions: RULES.MIN_SESSIONS,
      checked_by: "vercel", ...(note ? { note } : {}) } };
}

// ------------------------------------------------------------------ the handler
class RpcError extends Error { constructor(public status: number, public code: string) { super(`rpc ${status} ${code}`); } }
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const obj = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);

function rpcClient(url: string, key: string, auth: string, f: Fetch) {
  return async (fn: string, args: Obj): Promise<unknown> => {
    const r = await f(`${url.replace(/\/$/, "")}/rest/v1/rpc/${fn}`, { method: "POST", signal: AbortSignal.timeout(8000),
      headers: { apikey: key, Authorization: auth, "Content-Type": "application/json" }, body: JSON.stringify(args) });
    const text = await r.text();
    let body: unknown = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = null; }
    if (!r.ok) throw new RpcError(r.status, String(obj(body)?.code ?? ""));
    return body;
  };
}
const forbidden = (e: unknown) => e instanceof RpcError && (e.code === "42501" || e.status === 401 || e.status === 403);
const missingFn = (e: unknown) => e instanceof RpcError && (e.code === "PGRST202" || e.code === "42883");

/** A paper base URL or none; anything else (a live URL) is refused. Same rule as engine/config.py. */
const paperOnly = (base: string | undefined) => base === undefined || base === "" || base.replace(/\/+$/, "") === PAPER_URL;

export async function handle(req: Request, env: Env, fetchFn: Fetch = fetch, now: Date = new Date()): Promise<Response> {
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);
  const auth = req.headers.get("authorization") ?? "";
  if (!/^Bearer [\w.-]+$/.test(auth)) return json({ error: "sign in first" }, 401);
  const raw = obj(await req.json().catch(() => null))?.symbol;
  const symbol = typeof raw === "string" ? raw.trim().toUpperCase() : "";
  if (!TICKER.test(symbol)) return json({ error: "not a US stock symbol" }, 400);
  const url = env.SUPABASE_URL ?? env.VITE_SUPABASE_URL, key = env.SUPABASE_PUBLISHABLE_KEY ?? env.VITE_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) return json({ status: "unavailable", reason: "checks unavailable here", check: null });
  const rpc = rpcClient(url, key, auth, fetchFn);

  // 1. owner only, and today's answer if there is one (kb_check_ticker queues the row otherwise)
  let check: Obj | null;
  try { check = obj(await rpc("kb_check_ticker", { p_symbol: symbol })); } catch (e) {
    if (forbidden(e)) return json({ error: "not authorised" }, 403);
    console.error("check-ticker: kb_check_ticker failed", (e as Error).message);
    return json({ error: "the database could not be read" }, 502);
  }
  if (!check || check.status !== "pending") return json({ status: "answered", check });

  // 2. no paper keys here, or not the paper endpoint: leave the row pending for the Mac worker
  const keyId = env.ALPACA_API_KEY, secret = env.ALPACA_SECRET;
  if (!keyId || !secret) return json({ status: "unavailable", reason: "checks unavailable here", check });
  if (!paperOnly(env.ALPACA_BASE_URL)) {
    console.error("check-ticker: ALPACA_BASE_URL is not the paper endpoint; refusing to read the broker");
    return json({ status: "unavailable", reason: "checks unavailable here", check });
  }

  // 3. the check itself (GET only), then 4. the answer through the owner-only RPC
  let args: Obj;
  try {
    const r = await checkSymbol(symbol, env, fetchFn, now, keyId, secret);
    args = { p_symbol: symbol, p_status: r.status, p_reason: r.reason, p_etf: r.etf, p_name: r.name, p_industry: r.industry, p_detail: r.detail };
  } catch (e) {
    const name = e instanceof HttpError ? `HTTP ${e.status}` : (e as Error).name;
    console.warn("check-ticker: read failed", symbol, name);
    args = { p_symbol: symbol, p_status: "failed", p_reason: null, p_etf: null, p_name: null, p_industry: null, p_detail: { error: name, checked_by: "vercel" } };
  }
  let res: Obj | null;
  try { res = obj(await rpc("kb_ticker_check_answer", args)); } catch (e) {
    if (forbidden(e)) return json({ error: "not authorised" }, 403);
    if (missingFn(e)) return json({ status: "unavailable", reason: "database update not applied", check });
    console.error("check-ticker: kb_ticker_check_answer failed", (e as Error).message);
    return json({ error: "the answer could not be stored" }, 502);
  }
  const stored = obj(res?.check) ?? check;
  if (!res?.ok) return json({ status: "answered", check: stored });
  return json({ status: args.p_status === "failed" ? "failed" : "checked", check: stored });
}

export async function POST(request: Request): Promise<Response> {
  return handle(request, process.env);
}
