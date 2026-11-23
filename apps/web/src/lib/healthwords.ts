// Plain words for the engine events of M9 whose meaning depends on what they carry (22 Nov 2026, main e949400).
// Pure: unit-tested in healthwords.test.ts. Every field is untrusted jsonb and read defensively.
//
// Sources (engine/core.py):
// - `halt_cleared` is written at three places: the session start ({kind, day}), `on_tick_ok` after five clean ticks
//   for an `engine_errors` halt ({kind, clean_ticks, day, why}; #483), and a late corporate action that makes the
//   books agree again for a `drift` halt ({kind, day, why}; #285). The halt row itself is never deleted.
// - `clock_skew` reports the start ({state: "skewed", seconds: host minus broker} or {state: "unreadable"}) and the
//   end ({state: "ok"}) under one name (#435).
// - `command_waiting` {command_id, kind, why, action} (#427) and `command_rejected` {command_id, kind, reason,
//   payload} (#440) start with "command_" but are not "you asked to ...": kind is the command they are about.
import { COMMAND_TEXT, commandText } from "./explain";

type Payload = Record<string, unknown> | null | undefined;
const str = (v: unknown, max = 40) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Why a halt ended. Only the session start clears every halt; the other two clear one kind during the session. */
export function haltClearedText(p: Payload): string {
  const kind = str(p?.kind), ticks = num(p?.clean_ticks), why = str(p?.why, 200);
  if (kind === "engine_errors" && ticks !== null && ticks > 0)
    return `Halt cleared after ${ticks} check${ticks === 1 ? "" : "s"} in a row without an error: buys are allowed again`;
  if (kind === "drift" && why) return "Halt cleared: the books agree with the broker again";
  if (why) return "Halt cleared"; // a reason this page does not know: the detail column shows it as the engine wrote it
  return "Halt cleared at the new session";
}

/** The engine's clock against the broker's: the start, the unreadable case and the end read differently. */
export function clockSkewText(p: Payload): string {
  const state = str(p?.state), s = num(p?.seconds);
  if (state === "ok") return "The engine's clock agrees with the broker's again: new orders go out";
  if (state === "unreadable") return "The broker's clock can't be read: new orders wait until it can";
  if (state === "skewed" && s !== null && s !== 0) {
    const by = Math.abs(s) >= 120 ? `${Math.round(Math.abs(s) / 60)} minutes` : `${Math.round(Math.abs(s))} seconds`;
    return `The engine's clock is ${by} ${s < 0 ? "behind" : "ahead of"} the broker's: new orders wait until they agree`;
  }
  return "The engine's clock differs from the broker's: new orders wait until they agree";
}

/** `command_waiting`, `command_rejected`, or null for a command the engine applied ("You asked to ..."). */
export function commandEventText(type: string, p: Payload): string | null {
  const kind = str(p?.kind);
  if (type === "command_waiting") {
    if (kind === "new_run") return "The new run is waiting: the old run's sale is not complete yet, buys are blocked";
    return "Flatten and stop is waiting: the sale is not complete yet, buys are blocked";
  }
  if (type === "command_rejected")
    return kind && Object.hasOwn(COMMAND_TEXT, kind) ? `The engine refused your request to ${commandText(kind)}`
      : "The engine refused a command it does not know";
  return null;
}

// ---- a halt the engine cleared: the halt row is never deleted, only a later `halt_cleared` of its kind says it is over

export interface Cleared { kind: string; ts: string }

/** The `halt_cleared` events of a list of events, as {kind, ts}. */
export const clearedFrom = (events: { type: string; ts: string; payload: Record<string, unknown> }[] | undefined): Cleared[] =>
  (events ?? []).flatMap((e) => (e.type === "halt_cleared" && typeof e.payload?.kind === "string" ? [{ kind: e.payload.kind, ts: e.ts }] : []));

/** The first clear of this halt's kind that came after it, or null (also for a time that cannot be read). */
export const clearedAt = (h: { kind: string; ts: string }, cleared: Cleared[]): string | null =>
  cleared.filter((c) => c.kind === h.kind && Date.parse(c.ts) > Date.parse(h.ts)).map((c) => c.ts).sort()[0] ?? null;

/** N3 (ui_after_engine_v3, #545): `multi_day_error` with `step: broker_payload` is a broker answer (a fill, an order
 *  view, a dividend) that failed its checks and was not booked. It is not a failed step: orders may have gone out.
 *  Null for every other event, which keeps its registry label. */
export function refusedPayloadText(type: string, p: Payload): string | null {
  if (type !== "multi_day_error" || str(p?.step) !== "broker_payload") return null;
  return "A broker answer was refused and not booked — orders may have gone out; check the books";
}
