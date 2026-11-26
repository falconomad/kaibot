// GET /api/marks — what the paper account holds and each stock's price now, so the dashboard can mark the book between
// two closes (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime").
//
// 1. Owner only. The caller's own Supabase session and the publishable key (no service-role key exists) ask who the
//    caller is, kb_me (migration 0045: a few index lookups, no run data), before anything else, on every request:
//    anyone whose answer is not owner true gets 403, a session that is not valid 401, and Alpaca is never asked.
//    (#723, audit 2 8.5: until then this was kb_runs, which reads and returns every run row only to be thrown away.)
//    If kb_me is missing (a database before 0045: PostgREST 404) the check falls back to kb_runs, as before.
// 2. No ALPACA_API_KEY / ALPACA_SECRET here (preview deployments, local dev), or a base URL that is not the paper one:
//    {available: false}. Nothing is read.
// 3. Otherwise two reads of Alpaca, GET only, side by side: the paper account's open positions (paper-api /v2/positions)
//    and SPY's snapshot on the free IEX feed (data /v2/stocks/SPY/snapshot?feed=iex), so Home can move the S&P 500 side
//    of "Ahead of / Behind the S&P 500" with the pot (stakeholder, 24 Nov 2026). Nothing else of the broker is touched:
//    not the account, not the asset list, no other symbol. SPY is optional: a snapshot that fails or cannot be read
//    leaves the positions answer as it is, with the last SPY read kept (its own as_of says how old it is) or null.
// 4. The engine trades on the same paper account and shares its request budget (Alpaca: 200 calls a minute per
//    account; config/sources.yaml gives the engine 150 of them), so each instance of this function calls Alpaca at most
//    once in 10 seconds, whatever happens: requests inside that window get the last answer from memory, requests that
//    arrive together share one call, and a failed or timed-out read (4 s) is not tried again until the window is over.
//    After a failure the last good answer is served, with its own as_of, for at most 30 seconds; the page stops
//    calling it live at the same age.
// The answer is {available, as_of, positions: [{symbol, qty, price, prev_close}], spy: {price, as_of, prev_close,
// prev_session} | null} and nothing else: no account number, no cost, no key. spy.as_of is the time of SPY's last IEX
// trade (not of the read), so the page can apply its 30 s rule to the price itself; prev_close is the close of the
// latest daily bar of a session BEFORE that trade's session (ET), and prev_session that session, so the page can
// check it is the very close its S&P 500 line ends at (a holiday, a weekend, a missing close: it is not, and the page
// says "at last close" instead). This file cannot place, change or cancel anything at the broker: it sends Alpaca a
// GET to two fixed addresses and holds no other. Keys are read from the environment only and never logged or returned.

declare const process: { env: Record<string, string | undefined> };

type Env = Record<string, string | undefined>;
type Fetch = typeof fetch;
type Obj = Record<string, unknown>;

export const PAPER_URL = "https://paper-api.alpaca.markets";
export const POSITIONS_URL = `${PAPER_URL}/v2/positions`;
export const DATA_URL = "https://data.alpaca.markets";
/** SPY on the free IEX feed (SIP is only for data >= 15 min old). The data host's budget is the engine's `alpaca`
 *  budget in config/sources.yaml: this is one call in 10 s per instance, as the positions read is. */
export const SPY_URL = `${DATA_URL}/v2/stocks/SPY/snapshot?feed=iex`;
/** At most one upstream call per instance in this many milliseconds. */
export const MIN_GAP_MS = 10_000;
/** The upstream read is given up after this long. */
export const TIMEOUT_MS = 4_000;
/** A last good answer older than this is not served any more. */
export const KEEP_MS = 30_000;

export interface Mark { symbol: string; qty: number; price: number; prev_close: number | null }
export interface Spy { price: number; as_of: string; prev_close: number | null; prev_session: string | null }
export interface Answer { available: boolean; as_of: string | null; positions: Mark[]; spy?: Spy | null }
/** One instance's memory: when Alpaca was last called, the last good answer, the last good SPY, and the call under way. */
export interface Cache { called: number; good: { at: number; positions: Mark[] } | null; spy: Spy | null; flight: Promise<Answer> | null }
export const newCache = (): Cache => ({ called: -Infinity, good: null, spy: null, flight: null });
const CACHE = newCache();

const NONE: Answer = { available: false, as_of: null, positions: [] };
const SYMBOL = /^[A-Z0-9][A-Z0-9./]{0,11}$/;

class HttpError extends Error { constructor(public status: number) { super(`HTTP ${status}`); this.name = "HttpError"; } }
class Unreadable extends Error { constructor() { super("unexpected answer"); this.name = "Unreadable"; } }
class TimedOut extends Error { constructor() { super("timed out"); this.name = "TimedOut"; } }

const obj = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const num = (v: unknown): number => (typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

/** One row of Alpaca's list -> the four fields the page may see. A row that cannot be read fails the whole answer:
 *  a list with a holding missing would look like a book that matches when it does not. */
function mark(row: unknown): Mark {
  const r = obj(row), symbol = r?.symbol, qty = num(r?.qty), price = num(r?.current_price), prev = num(r?.lastday_price);
  if (typeof symbol !== "string" || !SYMBOL.test(symbol) || !Number.isFinite(qty) || !Number.isFinite(price) || price <= 0) throw new Unreadable();
  return { symbol, qty, price, prev_close: Number.isFinite(prev) && prev > 0 ? prev : null };
}

const etDay = (t: number) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(t);

/** SPY's snapshot -> its last trade and the close before that trade's session, or Unreadable. The close is the newer of
 *  dailyBar and prevDailyBar whose ET session is before the trade's: before the first IEX trade of a day dailyBar is
 *  still the last session's, after it the day's own. No such bar: prev_close and prev_session are null. */
export function spyOf(body: unknown): Spy {
  const b = obj(body), trade = obj(b?.latestTrade), price = num(trade?.p), ts = typeof trade?.t === "string" ? Date.parse(trade.t) : NaN;
  if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(ts)) throw new Unreadable();
  const day = etDay(ts);
  let prev: { c: number; session: string } | null = null;
  for (const bar of [obj(b?.dailyBar), obj(b?.prevDailyBar)]) {
    const c = num(bar?.c), t = typeof bar?.t === "string" ? Date.parse(bar.t) : NaN;
    if (!Number.isFinite(c) || c <= 0 || !Number.isFinite(t)) continue;
    const session = etDay(t);
    if (session < day && (!prev || session > prev.session)) prev = { c, session };
  }
  return { price, as_of: new Date(ts).toISOString(), prev_close: prev?.c ?? null, prev_session: prev?.session ?? null };
}

/** The only way this file talks to Alpaca: a GET of one of its two fixed addresses, given up after TIMEOUT_MS. */
async function alpacaGet(f: Fetch, url: typeof POSITIONS_URL | typeof SPY_URL, keyId: string, secret: string): Promise<unknown> {
  const stop = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, no) => { timer = setTimeout(() => { stop.abort(); no(new TimedOut()); }, TIMEOUT_MS); });
  const read = (async () => {
    const r = await f(url, { method: "GET", signal: stop.signal,
      headers: { "APCA-API-KEY-ID": keyId, "APCA-API-SECRET-KEY": secret, Accept: "application/json" } });
    if (!r.ok) throw new HttpError(r.status);
    return r.json().catch(() => null) as Promise<unknown>;
  })();
  try { return await Promise.race([read, late]); } finally { clearTimeout(timer); }
}

/** The paper account's open positions. */
async function positions(f: Fetch, keyId: string, secret: string): Promise<Mark[]> {
  const rows = await alpacaGet(f, POSITIONS_URL, keyId, secret);
  if (!Array.isArray(rows)) throw new Unreadable();
  return rows.map(mark);
}

const why = (e: unknown) => (e instanceof HttpError ? `HTTP ${e.status}` : e instanceof Error ? e.name : "error");

/** The last good answer while it is young enough, else unavailable. */
const answer = (c: Cache, t: number): Answer =>
  c.good && t - c.good.at <= KEEP_MS ? { available: true, as_of: new Date(c.good.at).toISOString(), positions: c.good.positions, spy: c.spy } : NONE;

/** At most one upstream call per MIN_GAP_MS: the call under way is shared, and inside the window the cache answers. */
function marks(c: Cache, f: Fetch, keyId: string, secret: string, clock: () => number): Promise<Answer> {
  if (c.flight) return c.flight;
  const t = clock();
  if (t - c.called < MIN_GAP_MS) return Promise.resolve(answer(c, t));
  c.called = t;
  // side by side: SPY never delays or fails the positions
  const spy = alpacaGet(f, SPY_URL, keyId, secret).then(spyOf)
    .then((s) => { c.spy = s; }, (e: unknown) => { console.warn("marks: SPY read failed", why(e)); });
  const held = positions(f, keyId, secret)
    .then((rows) => { c.good = { at: t, positions: rows }; }, (e: unknown) => { console.warn("marks: read failed", why(e)); });
  c.flight = Promise.all([held, spy]).then(() => { c.flight = null; return answer(c, clock()); });
  return c.flight;
}

class RpcError extends Error { constructor(public status: number, public code: string) { super(`rpc ${status} ${code}`); } }

/** An RPC with the caller's own session; its answer, or RpcError when the database refuses or cannot be read.
 *  4 s at most, so the check and the read together stay inside the function's 10 s (vercel.json). */
async function rpc(url: string, key: string, auth: string, f: Fetch, fn: string): Promise<unknown> {
  const r = await f(`${url.replace(/\/$/, "")}/rest/v1/rpc/${fn}`, { method: "POST", signal: AbortSignal.timeout(TIMEOUT_MS),
    headers: { apikey: key, Authorization: auth, "Content-Type": "application/json" }, body: "{}" });
  if (r.ok) return r.json().catch(() => null);
  const body = obj(await r.json().catch(() => null));
  throw new RpcError(r.status, String(body?.code ?? ""));
}

/** The owner check: kb_me's `owner` must be exactly true (anything else is 403). kb_me missing (404): kb_runs, which
 *  refuses a non-owner itself (42501); its answer is not used. */
async function ownerCheck(url: string, key: string, auth: string, f: Fetch): Promise<void> {
  let me: unknown;
  try { me = await rpc(url, key, auth, f, "kb_me"); } catch (e) {
    if (e instanceof RpcError && e.status === 404) { await rpc(url, key, auth, f, "kb_runs"); return; }
    throw e;
  }
  if (obj(me)?.owner !== true) throw new RpcError(403, "42501");
}

/** A paper base URL or none; anything else (a live URL) is refused. Same rule as engine/config.py and check-ticker. */
const paperOnly = (base: string | undefined) => base === undefined || base === "" || base.replace(/\/+$/, "") === PAPER_URL;

export async function handle(req: Request, env: Env, fetchFn: Fetch = fetch, clock: () => number = Date.now, cache: Cache = CACHE): Promise<Response> {
  if (req.method !== "GET") return json({ error: "method not allowed" }, 405);
  const auth = req.headers.get("authorization") ?? "";
  if (!/^Bearer [\w.-]+$/.test(auth)) return json({ error: "sign in first" }, 401);
  const url = env.SUPABASE_URL ?? env.VITE_SUPABASE_URL, key = env.SUPABASE_PUBLISHABLE_KEY ?? env.VITE_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) return json(NONE);

  // 1. owner only, before anything touches Alpaca, the cache included
  try { await ownerCheck(url, key, auth, fetchFn); } catch (e) {
    if (e instanceof RpcError && e.status === 401) return json({ error: "sign in first" }, 401);
    if (e instanceof RpcError && (e.status === 403 || e.code === "42501")) return json({ error: "not authorised" }, 403);
    console.error("marks: owner check failed", e instanceof RpcError ? e.message : e instanceof Error ? e.name : "error");
    return json({ error: "the database could not be read" }, 502);
  }

  // 2. no paper keys here, or not the paper endpoint: nothing is read
  const keyId = env.ALPACA_API_KEY, secret = env.ALPACA_SECRET;
  if (!keyId || !secret) return json(NONE);
  if (!paperOnly(env.ALPACA_BASE_URL)) {
    console.error("marks: ALPACA_BASE_URL is not the paper endpoint; refusing to read the broker");
    return json(NONE);
  }

  // 3. and 4. one GET, at most once in 10 seconds
  return json(await marks(cache, fetchFn, keyId, secret, clock));
}

export async function GET(request: Request): Promise<Response> {
  return handle(request, process.env);
}
