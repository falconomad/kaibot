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
  | { kind: "scheduled"; online: boolean; nextOpen: string | null; marketOpen: boolean }; // applied: run scheduled

export function engineOnline(o: Pick<Overview, "heartbeat"> | undefined, now = Date.now()): boolean {
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  if (!beat?.ts) return false;
  const t = Date.parse(beat.ts);
  return Number.isFinite(t) && (now - t) / 1000 <= ENGINE_FRESH_S && beat.detail?.state !== "stopped";
}

export function startState(run: Run, o: Overview | undefined, now = Date.now()): StartState {
  if (run.kind !== "paper" || !o) return { kind: "none" };
  const online = engineOnline(o, now);
  const cmd = o.pending_commands.find((p) => p.kind === "new_run");
  if (cmd) {
    const amount = Number(cmd.payload?.amount_usd);
    const level = typeof cmd.payload?.level === "string" ? cmd.payload.level : null;
    return { kind: online ? "queued_online" : "queued_waiting", amount: Number.isFinite(amount) ? amount : null, level };
  }
  if (run.status !== "scheduled") return { kind: "none" };
  const d = o.heartbeat.find((h) => h.process === "engine")?.detail;
  const next = typeof d?.next_open === "string" && Number.isFinite(Date.parse(d.next_open)) ? d.next_open : null;
  const marketOpen = online && (d?.state === "trading" || d?.state === "managing");
  return { kind: "scheduled", online, nextOpen: online ? next : null, marketOpen };
}
