// Freshness (#604 "now" items, ui_after_engine_v3 N7): ONE rule set for every figure on screen that can go stale (the
// pot, the holdings, the engine chip, the headlines), shown by ONE component (components/Fresh.tsx). Pure: unit-tested
// in freshness.test.ts. First load: Home and the top bar read it.
//
// Three states, and words only when a figure is not live (stakeholder, 23 Nov 2026: no "1 s ago" counters):
//   live    the source answered within its usual interval: no text at all
//   recent  late, by at most ten minutes: "A few minutes old"
//   stale   later than that: "Stale · 25 min old" (whole minutes, hours or days: it never counts seconds)
// The rule: a source that updates every `every` ms is live while its newest figure is at most max(60 s, 2 x every)
// old, recent for ten minutes after that, stale after. The intervals are in EVERY, the one table for all of them.
// A figure that is not expected to change now (the market is closed and the pot is the booked close; the engine is
// asleep on purpose) is not stale: callers pass no freshness then, and nothing shows.

export type FreshState = "live" | "recent" | "stale";
export interface Freshness { state: FreshState; text: string | null }

const MIN = 60_000;
/** How often each source normally updates, in ms. Prices: the 10 s poll (lib/poll.ts MARKS_MS), so a held stock
 *  without a price for a minute is late (the earlier "Prices paused" rule). The engine's heartbeat: about every 10 s;
 *  45 s here keeps the engine chip's 90 s limit (Shell.engineChip). The engine's minute row: once a minute. The news
 *  poller: the heartbeat's `news.every_s` when it says, else 5 minutes. */
export const EVERY = { prices: 10_000, heartbeat: 45_000, minute: MIN, news: 5 * MIN } as const;
export const LIVE_MIN_MS = MIN;
export const RECENT_MS = 10 * MIN;

export const LIVE: Freshness = { state: "live", text: null };

/** The live limit of a source that updates every `every` ms. */
export const liveFor = (every: number) => Math.max(LIVE_MIN_MS, 2 * every);

/** "25 min", "3 h", "2 d": whole units, rounded down. */
export function ageWords(ms: number): string {
  const m = Math.floor(Math.max(0, ms) / MIN);
  if (m < 120) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h` : `${Math.floor(h / 24)} d`;
}

/** The state of a figure whose newest data is from `at` (ms, this device's clock). No time at all reads as stale: the
 *  figure cannot say how old it is. A time a little in the future (a clock ahead) is live. */
export function freshness(at: number | null | undefined, every: number, now = Date.now()): Freshness {
  if (typeof at !== "number" || !Number.isFinite(at)) return { state: "stale", text: "Stale · age unknown" };
  const age = now - at, live = liveFor(every);
  if (age <= live) return LIVE;
  if (age <= live + RECENT_MS) return { state: "recent", text: "A few minutes old" };
  return { state: "stale", text: `Stale · ${ageWords(age)} old` };
}

/** When the state of a figure from `at` changes next (ms), for a page that has to draw again by itself; null when it
 *  is stale already (its age in words changes only by the minute, at the next draw). */
export function nextChange(at: number | null | undefined, every: number, now = Date.now()): number | null {
  if (typeof at !== "number" || !Number.isFinite(at)) return null;
  const live = at + liveFor(every);
  return now <= live ? live + 1 : now <= live + RECENT_MS ? live + RECENT_MS + 1 : null;
}

/** Where the book's figures on screen come from (lib/marks.ts potLive `fresh`). */
type Source = { kind: "live"; at: number } | { kind: "engine"; at: number } | { kind: "close"; session: string } | null | undefined;

/** The pot's and the holdings' freshness. `watching`: the market is open and the page is asking for prices (visible,
 *  not waiting for its first answer: components/Marks.useMarks); otherwise the booked close is the truth and nothing
 *  is said. `pricesAt`: when the held stock with the oldest price last got one (the poll or the stream). Live prices
 *  are as old as that; the engine's minute row as old as its row. A booked close shown while the market is open (a
 *  fill today, share counts that differ: lib/marks.ts) is the booked figure, not a late one (stakeholder, 23 Nov 2026:
 *  no "At the close of" line): only the prices' own age is said then, and nothing when the page has no prices. */
export function bookFreshness(src: Source, watching: boolean, pricesAt: number | null, now = Date.now()): Freshness | null {
  if (!watching) return null;
  if (src?.kind === "live") return freshness(pricesAt ?? src.at, EVERY.prices, now);
  if (src?.kind === "engine") return freshness(src.at, EVERY.minute, now);
  return src && pricesAt !== null ? freshness(pricesAt, EVERY.prices, now) : null;
}

/** The holdings' freshness: their rows are priced by the live prices, else the engine's minute row, else the booked
 *  close (pages/Live.tsx holdingRows, pages/Positions.tsx), so their source is not always the pot's. */
export function holdingsFreshness(p: { fresh?: Source; live?: unknown; engine?: { at: number } | null } | null | undefined,
  bookSession: string | null | undefined, watching: boolean, pricesAt: number | null, now = Date.now()): Freshness | null {
  const src: Source = p?.live ? p.fresh : p?.engine ? { kind: "engine", at: p.engine.at } : bookSession ? { kind: "close", session: bookSession } : null;
  return bookFreshness(src, watching, pricesAt, now);
}

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);

/** The headlines' freshness, from the heartbeat's `news` block {state, last_ok, every_s} (ingest/news_poll.py). Null
 *  when there is no block (an older engine, the poller off) or the engine is asleep on purpose (`expected` false). */
export function newsFreshness(detail: unknown, expected: boolean, now = Date.now()): Freshness | null {
  const n = obj(obj(detail)?.news);
  if (!n || !expected) return null;
  const every = typeof n.every_s === "number" && n.every_s > 0 ? n.every_s * 1000 : EVERY.news;
  return freshness(typeof n.last_ok === "string" ? Date.parse(n.last_ok) : null, every, now);
}
