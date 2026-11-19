// GET /api/marks — what the paper account holds and each stock's price now, so the dashboard can mark the book between
// two closes (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime").
//
// 1. Owner only. The caller's own Supabase session and the publishable key (no service-role key exists) call an
//    owner-only RPC, kb_runs, before anything else, on every request: a stranger gets 401 or 403 and Alpaca is never
//    asked. kb_runs only reads; its answer is not used.
// 2. No ALPACA_API_KEY / ALPACA_SECRET here (preview deployments, local dev), or a base URL that is not the paper one:
//    {available: false}. Nothing is read.
// 3. Otherwise ONE read of Alpaca PAPER, GET only: the open positions (paper-api /v2/positions). Nothing else of the
//    broker is touched: not the account, not the asset list, not market data.
// 4. The engine trades on the same paper account and shares its request budget (Alpaca: 200 calls a minute per
//    account; config/sources.yaml gives the engine 150 of them), so each instance of this function calls Alpaca at most
//    once in 10 seconds, whatever happens: requests inside that window get the last answer from memory, requests that
//    arrive together share one call, and a failed or timed-out read (4 s) is not tried again until the window is over.
//    After a failure the last good answer is served, with its own as_of, for at most 30 seconds; the page stops
//    calling it live at the same age.
// The answer is {available, as_of, positions: [{symbol, qty, price, prev_close}]} and nothing else: no account number,
// no cost, no key. This file cannot place, change or cancel anything at the broker: it sends Alpaca one GET to one
// address and holds no other. Keys are read from the environment only and never logged or returned.

declare const process: { env: Record<string, string | undefined> };

type Env = Record<string, string | undefined>;
type Fetch = typeof fetch;
type Obj = Record<string, unknown>;

export const PAPER_URL = "https://paper-api.alpaca.markets";
export const POSITIONS_URL = `${PAPER_URL}/v2/positions`;
/** At most one upstream call per instance in this many milliseconds. */
export const MIN_GAP_MS = 10_000;
/** The upstream read is given up after this long. */
export const TIMEOUT_MS = 4_000;
/** A last good answer older than this is not served any more. */
export const KEEP_MS = 30_000;

export interface Mark { symbol: string; qty: number; price: number; prev_close: number | null }
export interface Answer { available: boolean; as_of: string | null; positions: Mark[] }
/** One instance's memory: when Alpaca was last called, the last good answer, and the call under way. */
export interface Cache { called: number; good: { at: number; positions: Mark[] } | null; flight: Promise<Answer> | null }
export const newCache = (): Cache => ({ called: -Infinity, good: null, flight: null });
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

/** The only way this file talks to Alpaca: one GET of the paper account's open positions, given up after TIMEOUT_MS. */
async function positions(f: Fetch, keyId: string, secret: string): Promise<Mark[]> {
  const stop = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, no) => { timer = setTimeout(() => { stop.abort(); no(new TimedOut()); }, TIMEOUT_MS); });
  const read = (async () => {
    const r = await f(POSITIONS_URL, { method: "GET", signal: stop.signal,
      headers: { "APCA-API-KEY-ID": keyId, "APCA-API-SECRET-KEY": secret, Accept: "application/json" } });
    if (!r.ok) throw new HttpError(r.status);
    const rows: unknown = await r.json().catch(() => null);
    if (!Array.isArray(rows)) throw new Unreadable();
    return rows.map(mark);
  })();
  try { return await Promise.race([read, late]); } finally { clearTimeout(timer); }
}

/** The last good answer while it is young enough, else unavailable. */
const answer = (c: Cache, t: number): Answer =>
  c.good && t - c.good.at <= KEEP_MS ? { available: true, as_of: new Date(c.good.at).toISOString(), positions: c.good.positions } : NONE;

/** At most one upstream call per MIN_GAP_MS: the call under way is shared, and inside the window the cache answers. */
function marks(c: Cache, f: Fetch, keyId: string, secret: string, clock: () => number): Promise<Answer> {
  if (c.flight) return c.flight;
  const t = clock();
  if (t - c.called < MIN_GAP_MS) return Promise.resolve(answer(c, t));
  c.called = t;
  c.flight = positions(f, keyId, secret)
    .then((rows) => { c.good = { at: t, positions: rows }; },
      (e: unknown) => { console.warn("marks: read failed", e instanceof HttpError ? `HTTP ${e.status}` : e instanceof Error ? e.name : "error"); })
    .then(() => { c.flight = null; return answer(c, clock()); });
  return c.flight;
}

class RpcError extends Error { constructor(public status: number, public code: string) { super(`rpc ${status} ${code}`); } }

/** An owner-only RPC with the caller's own session; throws RpcError when the database refuses or cannot be read.
 *  4 s at most, so the check and the read together stay inside the function's 10 s (vercel.json). */
async function rpc(url: string, key: string, auth: string, f: Fetch, fn: string): Promise<void> {
  const r = await f(`${url.replace(/\/$/, "")}/rest/v1/rpc/${fn}`, { method: "POST", signal: AbortSignal.timeout(TIMEOUT_MS),
    headers: { apikey: key, Authorization: auth, "Content-Type": "application/json" }, body: "{}" });
  if (r.ok) return;
  const body = obj(await r.json().catch(() => null));
  throw new RpcError(r.status, String(body?.code ?? ""));
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
  try { await rpc(url, key, auth, fetchFn, "kb_runs"); } catch (e) {
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
