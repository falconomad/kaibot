// Streamed prices (stakeholder, 20 Nov 2026: prices on the dashboard about once a second, not every 10 s).
// scripts/price_stream.py on the Mac reads Alpaca's free IEX trade stream for the stocks the paper account holds and
// broadcasts at most one message a second on a private Supabase Realtime channel that only the owner may join
// (migration 0032): {as_of, feed: "iex", prices: {SYM: price}}. The page keeps polling /api/marks every 10 s for what
// the stream does not carry (the share counts, the previous close, and any stock beyond the stream's 30). Here:
//   - a streamed price replaces the polled price of a stock the poll already lists, and nothing else;
//   - a stream with no message for 5 s is ignored: the page is back on the 10 s poll by itself, without a word;
//   - whether the prices may be used at all is still decided by lib/marks.liveBook, on the poll's own age, so a poll
//     that stops (a fill, the function down) ends the live figures even while prices keep streaming.
// IEX is one exchange: its last trade can differ from the consolidated price by a few cents. Pure: stream.test.ts.
import type { Marks } from "./marks";

/** The Realtime channel and event the relay broadcasts on (scripts/price_stream.py TOPIC / EVENT). */
export const STREAM_TOPIC = "kb:prices";
export const STREAM_EVENT = "prices";
/** A stream silent for longer than this is over: the polled prices are used again. */
export const STREAM_STALE_MS = 5_000;

/** One broadcast. `at`: when it arrived, on this device's clock (the relay sends within a second of the trades). */
export interface Stream { at: number; asOf: string; prices: Record<string, number> }

const SYMBOL = /^[A-Z0-9][A-Z0-9./]{0,11}$/;

/** The broadcast's payload -> a stream message, or null when it is not what the relay sends. */
export function parseStream(raw: unknown, received = Date.now()): Stream | null {
  const r = raw as { as_of?: unknown; feed?: unknown; prices?: unknown } | null;
  if (!r || typeof r !== "object" || r.feed !== "iex" || typeof r.as_of !== "string" || !Number.isFinite(Date.parse(r.as_of))) return null;
  if (!r.prices || typeof r.prices !== "object" || Array.isArray(r.prices)) return null;
  const prices: Record<string, number> = {};
  for (const [s, p] of Object.entries(r.prices as Record<string, unknown>)) {
    if (SYMBOL.test(s) && typeof p === "number" && Number.isFinite(p) && p > 0) prices[s] = p;
  }
  return Object.keys(prices).length ? { at: received, asOf: r.as_of, prices } : null;
}

/** Merge two messages: the newer one's prices win, a stock only the older one carries keeps its price. The relay
 *  sends every streamed stock each time, so this matters only for a message that lists fewer (a re-subscription). */
export function mergeStream(old: Stream | null, next: Stream): Stream {
  return old ? { ...next, prices: { ...old.prices, ...next.prices } } : next;
}

/** Is the stream live at `now`? */
export const streamLive = (s: Stream | null | undefined, now = Date.now()): s is Stream =>
  !!s && now - s.at >= -1_000 && now - s.at <= STREAM_STALE_MS;

/** The poll's marks with the streamed prices over them; exactly the poll's marks when the stream is silent, carries
 *  none of the polled stocks, or there is no poll. `streamAt` says that (and when) streamed prices are in. */
export function applyStream(marks: Marks | null, s: Stream | null | undefined, now = Date.now()): Marks | null {
  if (!marks || !streamLive(s, now)) return marks;
  let n = 0;
  const by: Marks["by"] = {};
  for (const [sym, m] of Object.entries(marks.by)) {
    const p = s.prices[sym];
    if (p !== undefined) { by[sym] = { ...m, price: p }; n++; } else by[sym] = m;
  }
  return n ? { ...marks, by, streamAt: s.at } : marks;
}
