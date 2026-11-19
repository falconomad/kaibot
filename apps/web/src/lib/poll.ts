// How often each screen asks the server again. One place, so polling stays calm (no refetch storms).
// - Polls run only while the browser tab is visible (react-query pauses refetchInterval in hidden tabs; we say so
//   explicitly with refetchIntervalInBackground: false).
// - The 5-second overview poll runs only while the engine is live in a session; closed market or offline engine: slower.
// - Data that only changes after the close (gate report, chapters, attribution, a past report card) is cached for minutes.
// - Live marks (the paper account's prices, api/marks.ts) are asked for every 10 s, only while the market is open.
import type { Overview, Run } from "./api";
import { marketHours } from "./engineclock";

export const MIN = 60_000;

/** react-query options for a poll every `ms` milliseconds, visible tab only. `false` turns polling off. */
export const poll = (ms: number | false) => ({ refetchInterval: ms, refetchIntervalInBackground: false }) as const;

/** Overview poll for the shell: 5 s while the engine trades or manages positions, 30 s otherwise (so a returning
 *  engine shows within half a minute), never for a replay or an ended run. */
export function overviewPollMs(run: Run | undefined, o: Overview | undefined, now = Date.now()): number | false {
  if (!run || run.kind !== "paper") return false;
  if (!["scheduled", "running", "paused"].includes(run.status)) return 5 * MIN;
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  if (!beat) return 30_000;
  const age = (now - new Date(beat.ts).getTime()) / 1000;
  const state = beat.detail?.state;
  if (age > 90 || state === "stopped") return 30_000;
  if (state === "market_closed" || state === "idle") return 30_000;
  return 5_000;
}

/** Live marks (api/marks.ts): the function calls Alpaca at most once in 10 s, so asking more often gains nothing. */
export const MARKS_MS = 10_000;

/** The live-marks poll: every 10 s while a paper run is trading and the market is open (lib/engineclock.ts
 *  marketHours), never otherwise. Outside market hours no call is made at all: the booked close is the truth then. */
export function marksPollMs(run: Pick<Run, "kind" | "status"> | undefined, o: Pick<Overview, "heartbeat"> | undefined, now = new Date()): number | false {
  if (!run || run.kind !== "paper" || !["running", "paused"].includes(run.status)) return false;
  return marketHours(now, o?.heartbeat.find((h) => h.process === "engine")) ? MARKS_MS : false;
}

/** Cache times (staleTime) by how often the data can change. */
export const STALE = {
  default: 30_000,
  /** written once after each session close */
  daily: 5 * MIN,
  /** never changes once written (a past session's report card, a closed trade) */
  immutable: Infinity,
} as const;
