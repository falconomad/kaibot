// What Home says about a new paper run between "Start paper run" and the run's first session. Pure: unit-tested in
// startstate.test.ts. Sources: the overview's unapplied commands (kb_overview lists only commands with
// applied_at null), the run's status, and the engine heartbeat (`ts`, `detail.state`, `detail.next_open`).
// Only a stale heartbeat makes it say the run waits for the engine.
import type { Overview, Run } from "./api";

export const ENGINE_FRESH_S = 90; // the engine chip's limit (Shell.engineChip)

export type StartState =
  | { kind: "none" }
  | { kind: "queued_waiting"; amount: number | null; level: string | null }   // command not applied, engine stale
  | { kind: "queued_online"; amount: number | null; level: string | null }    // command not applied, engine fresh
  | { kind: "queued_selling"; amount: number | null; level: string | null }   // seen by the engine: the old run is being sold
  | { kind: "scheduled"; online: boolean; nextOpen: string | null; marketOpen: boolean }; // applied: run scheduled

export function engineOnline(o: Pick<Overview, "heartbeat"> | undefined, now = Date.now()): boolean {
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  if (!beat?.ts) return false;
  const t = Date.parse(beat.ts);
  return Number.isFinite(t) && (now - t) / 1000 <= ENGINE_FRESH_S && beat.detail?.state !== "stopped";
}

/** M9 1.2 (#525): the engine reads the commands about every 10 seconds and leaves "Flatten and stop", or a new run
 *  that sells the old one first, unapplied until every position is sold. A command still unapplied while the engine
 *  has checked in SEEN_AFTER_S or more after it was written is one the engine has read and is working on; the
 *  overview carries no other sign of it (the engine's `command_waiting` event is in kb_health only). */
export const SEEN_AFTER_S = 30;
export function commandSeen(o: Pick<Overview, "heartbeat"> | undefined, cmdTs: string, now = Date.now()): boolean {
  const beat = Date.parse(o?.heartbeat.find((h) => h.process === "engine")?.ts ?? ""), sent = Date.parse(cmdTs);
  return engineOnline(o, now) && Number.isFinite(sent) && (beat - sent) / 1000 >= SEEN_AFTER_S;
}

/** "Flatten and stop" from the click to the run's end. */
export function flattenState(o: Pick<Overview, "heartbeat" | "pending_commands"> | undefined, now = Date.now()): "none" | "sent" | "waiting" | "engine_off" {
  const cmd = o?.pending_commands.find((p) => p.kind === "flatten_and_stop");
  if (!cmd) return "none";
  return !engineOnline(o, now) ? "engine_off" : commandSeen(o, cmd.ts, now) ? "waiting" : "sent";
}

export function startState(run: Run, o: Overview | undefined, now = Date.now()): StartState {
  if (run.kind !== "paper" || !o) return { kind: "none" };
  const online = engineOnline(o, now);
  const cmd = o.pending_commands.find((p) => p.kind === "new_run");
  if (cmd) {
    const amount = Number(cmd.payload?.amount_usd);
    const level = typeof cmd.payload?.level === "string" ? cmd.payload.level : null;
    return { kind: commandSeen(o, cmd.ts, now) ? "queued_selling" : online ? "queued_online" : "queued_waiting", amount: Number.isFinite(amount) ? amount : null, level };
  }
  if (run.status !== "scheduled") return { kind: "none" };
  const d = o.heartbeat.find((h) => h.process === "engine")?.detail;
  const next = typeof d?.next_open === "string" && Number.isFinite(Date.parse(d.next_open)) ? d.next_open : null;
  const marketOpen = online && (d?.state === "trading" || d?.state === "managing");
  return { kind: "scheduled", online, nextOpen: online ? next : null, marketOpen };
}
