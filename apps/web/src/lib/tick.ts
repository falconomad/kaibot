// The live tick (stakeholder, 23 Nov 2026: "the animation when a price changes could be better"). Pure parts of
// components/Tick.tsx, tested in lib/tick.test.ts:
//   - which way a figure moved (equal, unknown or non-finite values do not move);
//   - which characters of the formatted figure changed, so only those are coloured and rolled;
//   - the coalescing of bursts: at most one visual tick per figure every TICK_GAP_MS (tickDelay); a value that arrives
//     inside the window waits for its end, and the latest one is shown then (components/Tick.tsx).

/** The wash comes in over this long, */
export const TICK_IN_MS = 120;
/** then fades out over this long. */
export const TICK_OUT_MS = 560;
/** The whole tick, in and out: the figure is marked `tint-up` / `tint-down` for this long. */
export const TINT_MS = TICK_IN_MS + TICK_OUT_MS;
/** At most one visual tick per figure in this window; a value that arrives inside it waits for its end. */
export const TICK_GAP_MS = 250;

export type Dir = "up" | "down";

/** Which way a figure moved between two values; null when it didn't change or either side is unknown. */
export const tickDir = (was: number | null | undefined, now: number | null | undefined): Dir | null =>
  Number.isFinite(was) && Number.isFinite(now) && was !== now ? (now! > was! ? "up" : "down") : null;

/** How long a new value waits before it is shown with a tick: 0 when the last tick is TICK_GAP_MS old (or there was
 *  none), else the rest of the window. A clock that went back counts as a window that is over. */
export const tickDelay = (lastTickAt: number | null, now: number, gap = TICK_GAP_MS): number => {
  const since = lastTickAt === null ? gap : now - lastTickAt;
  return since < 0 || since >= gap ? 0 : gap - since;
};

/** The formatted figure split around what changed: [same start, changed middle, same end]. The end is only kept
 *  when the two have the same length (a digit that moved place is a change, "$999.99" -> "$1,000.01"). */
export function changedPart(was: string, now: string): [string, string, string] {
  let a = 0, b = now.length;
  while (a < b && was[a] === now[a]) a++;
  if (was.length === now.length) while (b > a && was[b - 1] === now[b - 1]) b--;
  return [now.slice(0, a), now.slice(a, b), now.slice(b)];
}
