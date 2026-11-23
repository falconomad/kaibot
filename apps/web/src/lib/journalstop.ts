// N2 (docs/design/ui_after_engine_v3.md): a stop by a damaged journal is never shown as "asleep". In the first load
// (the top bar, the phone bar and Home read it); the rest of the trading-state line is lazy (lib/tradingstate.ts).
// Source: src/kaibot/engine/__main__.py, the main loop: on JournalCorrupt it writes `engine_error` {error:
// "JournalCorrupt: ..."}, then `state: stopped` and `engine_stop`, and exits with code 4; every later start refuses the
// file before it writes anything. So: the latest `engine_error` is a damaged journal and no `engine_start` came after.
import type { Heartbeat } from "./api";

export interface HealthEvent { type: string; ts: string; payload: Record<string, unknown> | null }
export const JOURNAL_REASON = "journal damaged · needs you at the Mac";

const t = (iso: unknown) => (typeof iso === "string" ? Date.parse(iso) : NaN);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);

/** Rule 1: the latest `engine_error` is a damaged journal and the engine has not started (or beaten live) since. */
export function journalDamaged(events: HealthEvent[] | undefined, beat?: Pick<Heartbeat, "ts" | "detail"> | null): boolean {
  // the latest engine_error decides: a later error of another kind means the engine ran again after the damage
  const last = (events ?? []).filter((e) => e.type === "engine_error" && Number.isFinite(t(e.ts)))
    .sort((a, b) => t(b.ts) - t(a.ts))[0];
  const err = obj(last?.payload)?.error;
  if (!last || typeof err !== "string" || !/^JournalCorrupt\b/.test(err)) return false;
  const bad = t(last.ts);
  if ((events ?? []).some((e) => e.type === "engine_start" && t(e.ts) > bad)) return false;
  // a live heartbeat written after the damage: the engine runs again (its start event may have scrolled away)
  const bt = t(beat?.ts);
  return !(Number.isFinite(bt) && bt > bad && beat?.detail?.state !== "stopped");
}

