// Live marks (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime").
// The book (kb_book) is booked once per close, and the engine's minute ledger carries one figure for the whole run. In
// between, /api/marks (api/marks.ts) reads what the paper account holds and each stock's price now. The page uses those
// prices ONLY when the account holds exactly the book: the same stocks, the same share counts, no trade today, prices
// not older than 30 s. Then
//   live pot = the last close's equity + the sum over the book of shares x (price now - the close mark)
//   today    = live pot - the last close
// Anything else (the function unavailable or stale, a fill since the close, a count that differs, the market closed)
// falls back to what potNow does (the engine's minute ledger, else the booked close) for the pot and to the close marks
// for the rows. A number never mixes the two sources. Pure: unit-tested in marks.test.ts.
//
// The S&P 500 side (stakeholder, 24 Nov 2026: "Ahead of / Behind the S&P 500 by $X" and the chart legend did not move
// during the session). The same answer carries SPY's last IEX trade and the close before it (api/marks.ts). While the
// pot is live (fresh "live" or "engine") and that trade is at most 30 s old (STALE_MS, the prices' own rule) and its
// previous close is the very session the S&P 500 line ends at, the live point's SPY is
//   that close's SPY value x (SPY now / SPY's previous close)
// so the hero's gap, the chart's last S&P point and its legend move together (homeLine). Otherwise the live point's SPY
// stays at the last close, as before, and the page says so: "at last close" (vsSpyText, PotChart's legend).
import type { Overview, Run } from "./api";
import type { Curve } from "./model";
import { money } from "./fmt";
import { type Book, type LiveMark, type PotNow, type SeriesPoint, potNow } from "./book";

/** Prices older than this are not live any more (three missed polls). */
export const STALE_MS = 30_000;

export interface Mark { qty: number; price: number; prevClose: number | null }
/** `at`: when the poll's prices were read, on this device's clock (ms). `streamAt`: when streamed prices
 *  (lib/stream.ts) last replaced some of them; absent on the poll alone. */
export interface Marks { at: number; by: Record<string, Mark>; streamAt?: number; spy?: SpyMark | null }
/** SPY now (api/marks.ts `spy`). `at`: when the price was traded, on this device's clock (ms), measured on the server's
 *  clock as the poll's age is; `prevSession`: the ET session `prevClose` closed (null with it when unknown). */
export interface SpyMark { price: number; at: number; prevClose: number | null; prevSession: string | null }
export interface LiveBook extends Marks { pot: number; dayPnl: number; dayPct: number }
/** Where the pot on screen comes from: live prices, the engine's minute row, or a booked close. */
export type Fresh = { kind: "live"; at: number } | { kind: "engine"; at: number } | { kind: "close"; session: string };

const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const etDay = (t: number) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(t);

/** The function's answer -> marks, or null when it is unavailable or not what api/marks.ts sends. `sent` is the
 *  answer's Date header: the prices' age is measured on the server's clock (Date less as_of) and counted back from
 *  `received`, so a device whose own clock is wrong still tells live from stale. */
export function parseMarks(raw: unknown, received = Date.now(), sent?: string | null): Marks | null {
  const r = raw as { available?: unknown; as_of?: unknown; positions?: unknown } | null;
  const asOf = typeof r?.as_of === "string" ? Date.parse(r.as_of) : NaN;
  if (r?.available !== true || !Number.isFinite(asOf) || !Array.isArray(r.positions)) return null;
  const by: Record<string, Mark> = {};
  for (const p of r.positions as ({ symbol?: unknown; qty?: unknown; price?: unknown; prev_close?: unknown } | null)[]) {
    if (!p || typeof p.symbol !== "string" || !fin(p.qty) || !fin(p.price) || p.symbol in by) return null;
    by[p.symbol] = { qty: p.qty, price: p.price, prevClose: fin(p.prev_close) && p.prev_close > 0 ? p.prev_close : null };
  }
  const server = sent ? Date.parse(sent) : NaN;
  const back = (t: number) => (Number.isFinite(server) ? received - Math.max(0, server - t) : t);
  const out: Marks = { at: back(asOf), by };
  const spy = parseSpy((r as { spy?: unknown }).spy);
  if (spy) out.spy = { ...spy, at: back(spy.at) };
  return out;
}

const SESSION = /^\d{4}-\d{2}-\d{2}$/;
/** The answer's `spy` -> a SpyMark on the server's clock, or null when absent or not what api/marks.ts sends. A
 *  previous close without its session (or the other way round) is no previous close: both are null then. */
export function parseSpy(raw: unknown): SpyMark | null {
  const s = raw as { price?: unknown; as_of?: unknown; prev_close?: unknown; prev_session?: unknown } | null;
  const at = typeof s?.as_of === "string" ? Date.parse(s.as_of) : NaN;
  if (!s || typeof s !== "object" || !fin(s.price) || s.price <= 0 || !Number.isFinite(at)) return null;
  const ok = fin(s.prev_close) && s.prev_close > 0 && typeof s.prev_session === "string" && SESSION.test(s.prev_session);
  return { price: s.price, at, prevClose: ok ? (s.prev_close as number) : null, prevSession: ok ? (s.prev_session as string) : null };
}

/** SPY's move since its previous close, as a factor, when it may be used against `close` (the session the S&P 500
 *  line ends at): fresh (STALE_MS, and not from the future by more than a second), and its previous close is that
 *  very session. Else null: the S&P 500 side stays at the last close. */
export function spyFactor(spy: SpyMark | null | undefined, close: string | undefined, now = Date.now()): number | null {
  if (!spy || !close || spy.prevClose === null || spy.prevSession !== close) return null;
  if (!(now - spy.at <= STALE_MS && now - spy.at >= -1_000) || !(spy.price > 0) || !(spy.prevClose > 0)) return null;
  return spy.price / spy.prevClose;
}

/** Where the S&P 500 side of the last point comes from: none (no live point: closes on both sides), live (SPY now),
 *  or close (a live pot against SPY's last close: the page says "at last close"). */
export type SpyAt = "none" | "live" | "close";

/** Home's line and its end, the one point the hero, "Ahead of / Behind the S&P 500" and the chart's legend all read.
 *  `c`: the booked closes (mtmCurve or curve); `p`: potLive's answer (null for a replay). While the pot is live (fresh
 *  "live" or "engine") the last point is today's, at the live pot, marked live, and its SPY moves with SPY now when
 *  spyFactor allows it, else holds the last close (spyAt "close"). A booked close of the same session as the line's
 *  last point gives that point the book's figure (the hero's), so the two never differ. Pure; `c` is not changed. */
export function homeLine(c: Curve[], p: Pick<PotNow, "pot"> & { fresh: Fresh | null } | null, spy: SpyMark | null | undefined,
  today: string, start: number, now = Date.now()): { curve: Curve[]; end: Curve | undefined; spyAt: SpyAt } {
  const curve = c.slice();
  const tail = curve.at(-1);
  const f = p?.fresh;
  if (p && f && f.kind !== "close") {
    // the close the S&P 500 line ends at: the last point before today (today's own point is replaced below)
    const close = tail?.session === today ? curve.at(-2) : tail;
    const k = close && close.session < today ? spyFactor(spy, close.session, now) : null;
    const base = tail?.session === today ? tail.spy : tail?.spy ?? start;
    const point: Curve = { session: today, pot: p.pot, spy: k !== null ? close!.spy * k : base, live: true, ...(k !== null ? {} : { spyClose: true }) };
    if (tail?.session === today) curve[curve.length - 1] = point;
    else curve.push(point);
    return { curve, end: point, spyAt: k !== null ? "live" : "close" };
  }
  if (p && f?.kind === "close" && tail?.session === f.session && tail.pot !== p.pot) curve[curve.length - 1] = { ...tail, pot: p.pot };
  return { curve, end: curve.at(-1), spyAt: "none" };
}

/** "Ahead of the S&P 500 by $1,234", or "... the S&P 500 at last close by ..." when a live pot is measured against
 *  SPY's last close (spyAt "close"): it never reads as live when only one side is. */
export function vsSpyText(vs: number, spyAt: SpyAt, amount = true): string {
  return `${vs >= 0 ? "Ahead of" : "Behind"} the S&P 500${spyAt === "close" ? " at last close" : ""}${amount ? ` by ${money(Math.abs(vs))}` : ""}`;
}

/** The book marked at the account's prices, or null when they may not be used (see the top of this file).
 *  The engine's minute ledger is not read here on purpose: what the account holds is compared with the book itself, so
 *  a day position that filled shows as a stock or a count the book does not have, and this keeps working whatever the
 *  minute row carries (issue #384 will add the multi-day holdings to it). */
export function liveBook(book: Book | null | undefined, marks: Marks | null | undefined, o: Pick<Overview, "today"> | undefined,
  now = Date.now()): LiveBook | null {
  const close = book?.session ? book.equityReported ?? book.equity : null;
  if (!book?.session || close === null || !marks || !book.positions.length) return null;
  // stale; today's close already booked; or a trade today moved cash the close does not know (the same shares at the
  // end of a round trip would hide it)
  if (!(now - marks.at <= STALE_MS) || book.session >= etDay(now) || Number(o?.today?.trades ?? 0) > 0) return null;
  const held = new Map<string, number>();
  for (const p of book.positions) held.set(p.symbol, (held.get(p.symbol) ?? 0) + p.qty);
  if (held.size !== Object.keys(marks.by).length) return null;
  for (const [s, qty] of held) {
    const m = marks.by[s];
    if (!m || !fin(m.price) || m.price <= 0 || !(Math.abs(m.qty - qty) < 1e-6)) return null;
  }
  const dayPnl = book.positions.reduce((a, p) => a + p.qty * (marks.by[p.symbol].price - p.mark), 0);
  return { ...marks, pot: close + dayPnl, dayPnl, dayPct: close ? (dayPnl / close) * 100 : 0 };
}

// ---------------------------------------------------------------- the engine's minute row (#384)
// Contract (issue #384, docs/ops/ledger.md "The minute row"): kb_overview `ledger.multi_day` = { v: 1, cash_ledger,
// invested, settling: [symbol], positions: [{ rule, symbol, qty, avg_cost, mark, mark_ts, mark_from, value }] }.
// - Authoritative: the pot is the row's equity (potNow: min(equity_venue, equity_shadow)); the holdings' shares are
//   the booked book (kb_book); the row's marks are the prices that equity counts each holding at.
// - Day positions come ONLY from `ledger.positions`. `multi_day.positions` is never added to them: the same stock would
//   show twice (as a day position and as a book row). It only prices the book's own rows.
// - Used only as the fallback for the rows' prices when the account's live prices may not be (liveBook null), and only
//   when the row is today's, the book is the last close's, nothing is settling (orders filling, 15:50-15:57 ET) and
//   the row lists exactly the book's holdings with the same shares. Anything else: the close marks, as before.

/** A book priced from one source, as markOf reads it. */
export interface PricedBook { by: Record<string, Mark>; pot: number }
export interface EngineBook extends PricedBook { at: number }

interface MinutePos { rule: string; symbol: string; qty: number; mark: number }
/** `ledger.multi_day` -> its holdings, or null when it is absent, of another version, or not what the engine writes. */
export function parseMinuteBook(raw: unknown): { settling: string[]; positions: MinutePos[] } | null {
  const r = raw as { v?: unknown; settling?: unknown; positions?: unknown } | null;
  if (!r || typeof r !== "object" || r.v !== 1 || !Array.isArray(r.positions) || !Array.isArray(r.settling)) return null;
  const positions: MinutePos[] = [];
  for (const p of r.positions.slice(0, 600) as ({ rule?: unknown; symbol?: unknown; qty?: unknown; mark?: unknown } | null)[]) {
    if (!p || typeof p.rule !== "string" || typeof p.symbol !== "string" || !fin(p.qty) || !fin(p.mark) || p.mark <= 0) return null;
    positions.push({ rule: p.rule, symbol: p.symbol, qty: p.qty, mark: p.mark });
  }
  return { settling: r.settling.filter((s): s is string => typeof s === "string"), positions };
}

/** The book priced at the engine's minute marks, or null when they may not be used (see above). `prevClose` is the
 *  book's close mark, so a row's "today" is against the last close, as the pot's is. */
export function engineMarks(book: Book | null | undefined, o: Pick<Overview, "ledger"> | undefined, now = Date.now()): EngineBook | null {
  const l = o?.ledger;
  if (!book?.session || !book.positions.length || !l) return null;
  const at = Date.parse(l.ts), today = etDay(now);
  if (!Number.isFinite(at) || etDay(at) !== today || book.session >= today) return null;
  const m = parseMinuteBook(l.multi_day);
  if (!m || m.settling.length || m.positions.length !== book.positions.length) return null;
  const row = new Map(m.positions.map((p) => [`${p.rule}|${p.symbol}`, p]));
  if (row.size !== m.positions.length) return null;
  const by: Record<string, Mark> = {};
  for (const p of book.positions) {
    const x = row.get(`${p.rule}|${p.symbol}`);
    if (!x || !(Math.abs(x.qty - p.qty) < 1e-6)) return null;
    const q = (by[p.symbol]?.qty ?? 0) + p.qty;
    if (by[p.symbol] && by[p.symbol].price !== x.mark) return null; // one stock, one price
    by[p.symbol] = { qty: q, price: x.mark, prevClose: p.mark > 0 ? p.mark : null };
  }
  return { at, by, pot: Math.min(l.equity_venue, l.equity_shadow) };
}

/** One stock's live price for lib/book.holdingView; null when the page is not live. */
export function markOf(live: PricedBook | null | undefined, symbol: string): LiveMark | null {
  const m = live?.by[symbol];
  return live && m ? { price: m.price, prevClose: m.prevClose, pot: live.pot } : null;
}

/** The pot for Home and Positions: live when the marks may be used, else exactly potNow. `fresh` says which. */
/** `engine` (#384): when the pot is the engine's minute row, the book's rows priced at that same row's marks, else null.
 *  The rows' figures and the pot then come from one source, as with `live`. */
export function potLive(run: Pick<Run, "amount_usd">, o: Pick<Overview, "daily" | "ledger" | "today"> | undefined, book?: Book | null,
  series?: SeriesPoint[] | null, marks?: Marks | null, now = new Date()): PotNow & { live: LiveBook | null; engine: EngineBook | null; fresh: Fresh | null } {
  const live = liveBook(book, marks, o, now.getTime());
  if (live) return { pot: live.pot, dayPnl: live.dayPnl, dayPct: live.dayPct, dayLabel: "today", today: true, positions: {}, source: "book", from: null,
    live, engine: null, fresh: { kind: "live", at: live.streamAt ?? live.at } };
  const p = potNow(run, o, book, series, now), f = p.from;
  const engine = f?.kind === "engine" && p.source === "book" ? engineMarks(book, o, now.getTime()) : null;
  return { ...p, live: null, engine: engine && engine.pot === p.pot ? engine : null,
    fresh: !f ? null : f.kind === "engine" ? { kind: "engine", at: Date.parse(f.ts) } : f };
}

