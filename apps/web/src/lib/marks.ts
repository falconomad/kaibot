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
import type { Overview, Run } from "./api";
import { type Book, type LiveMark, type PotNow, type SeriesPoint, potNow } from "./book";

/** Prices older than this are not live any more (three missed polls). */
export const STALE_MS = 30_000;

export interface Mark { qty: number; price: number; prevClose: number | null }
/** `at`: when the poll's prices were read, on this device's clock (ms). `streamAt`: when streamed prices
 *  (lib/stream.ts) last replaced some of them; absent on the poll alone. */
export interface Marks { at: number; by: Record<string, Mark>; streamAt?: number }
export interface LiveBook extends Marks { pot: number; dayPnl: number; dayPct: number }
/** Where the pot on screen comes from: live prices, the engine's minute row, or a booked close. */
/** `feed` (live only): "stream" while streamed prices are in (about once a second), "poll" on the 10 s poll. */
export type Fresh = { kind: "live"; at: number; feed?: "stream" | "poll" } | { kind: "engine"; at: number } | { kind: "close"; session: string };

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
  return { at: Number.isFinite(server) ? received - Math.max(0, server - asOf) : asOf, by };
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

/** One stock's live price for lib/book.holdingView; null when the page is not live. */
export function markOf(live: LiveBook | null | undefined, symbol: string): LiveMark | null {
  const m = live?.by[symbol];
  return live && m ? { price: m.price, prevClose: m.prevClose, pot: live.pot } : null;
}

/** The pot for Home and Positions: live when the marks may be used, else exactly potNow. `fresh` says which. */
export function potLive(run: Pick<Run, "amount_usd">, o: Pick<Overview, "daily" | "ledger" | "today"> | undefined, book?: Book | null,
  series?: SeriesPoint[] | null, marks?: Marks | null, now = new Date()): PotNow & { live: LiveBook | null; fresh: Fresh | null } {
  const live = liveBook(book, marks, o, now.getTime());
  if (live) return { pot: live.pot, dayPnl: live.dayPnl, dayPct: live.dayPct, dayLabel: "today", today: true, positions: {}, source: "book", from: null,
    live, fresh: live.streamAt !== undefined ? { kind: "live", at: live.streamAt, feed: "stream" } : { kind: "live", at: live.at, feed: "poll" } };
  const p = potNow(run, o, book, series, now), f = p.from;
  return { ...p, live: null, fresh: !f ? null : f.kind === "engine" ? { kind: "engine", at: Date.parse(f.ts) } : f };
}

/** The one small line near the pot: "Live · prices 4 s ago", "Updated a minute ago by the engine", "At Wednesday's close". */
export function freshText(f: Fresh, now = Date.now()): string {
  if (f.kind === "close") {
    const t = Date.parse(f.session), days = (Date.parse(etDay(now)) - t) / 86_400_000;
    if (!Number.isFinite(t)) return "At the last close";
    const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { timeZone: "UTC", ...o }).format(t);
    return days === 0 ? "At today's close" : days > 0 && days < 7 ? `At ${fmt({ weekday: "long" })}'s close` : `At the close of ${fmt({ month: "short", day: "numeric" })}`;
  }
  const s = Math.max(0, now - f.at) / 1000;
  if (f.kind === "live") return `Live · prices ${Math.max(1, Math.round(s))} s ago`;
  const m = Math.round(s / 60), h = Math.round(m / 60);
  return `Updated ${m < 2 ? "a minute" : m < 60 ? `${m} minutes` : h < 2 ? "an hour" : `${h} hours`} ago by the engine`;
}
