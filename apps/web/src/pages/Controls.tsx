// The run's controls (Settings): pause or resume entries, flatten and stop, the level from the next session.
// Its own file so that only Settings loads it: pages/Live.tsx is part of the first load (#297).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type CommandKind, type Level, type Overview, type Run } from "../lib/api";
import { levelPlan } from "../lib/derive";
import { commandText } from "../lib/explain";
import { day } from "../lib/fmt";
import { LEVELS } from "../lib/model";
import { flattenState } from "../lib/startstate";

/** "Flatten and stop" between the click and the run's end (M9 1.2, #525): the engine keeps the command unapplied
 *  until every position is sold, so "waiting for the engine" would be wrong while the engine is online. */
const FLATTEN_TEXT = {
  sent: "Flatten and stop is sent. The engine picks it up within about 10 seconds.",
  waiting: "Flatten and stop is waiting: the sale is not complete yet, buys are blocked.",
  engine_off: "Flatten and stop waits for the engine: its last heartbeat is more than 90 seconds old.",
} as const;
const FLATTEN_MORE = "The engine sends the sales while the market is open and tries again about every 10 seconds. With the market closed they go out at the next open. The run ends when everything is sold.";

export function Controls({ run, overview }: { run: Run; overview: Overview }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const cmd = useMutation({
    mutationFn: ({ kind, payload }: { kind: CommandKind; payload?: Record<string, unknown> }) => api.command(kind, payload),
    onSuccess: () => { setConfirm(false); qc.invalidateQueries({ queryKey: ["overview"] }); },
  });
  const flat = flattenState(overview);
  // the flatten has its own line below; the others are listed as before
  const pending = overview.pending_commands.filter((p) => p.kind !== "flatten_and_stop");
  const plan = levelPlan(run, overview.segments);
  const paused = run.status === "paused";
  const ended = !["scheduled", "running", "paused"].includes(run.status);
  return (
    <section className="card" aria-labelledby="ctl-h">
      <h2 id="ctl-h">Controls</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: 14 }}>No trade-by-trade control. You can pause new entries, or close everything and stop the run.
        The engine applies a command within about 10 seconds; nothing here changes until it does.</p>
      {ended ? <p>This run has {run.status}{run.end_reason ? `: ${run.end_reason}` : ""}. Start a new run from the menu.</p> : (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <button className="full-sm" disabled={cmd.isPending} onClick={() => cmd.mutate({ kind: paused ? "resume" : "pause" })}>{paused ? "Resume entries" : "Pause entries"}</button>
          {flat !== "none" ? null : !confirm ? <button className="danger full-sm" onClick={() => setConfirm(true)}>Flatten and stop</button> : (
            <span className="banner swap full-sm" role="alertdialog" aria-label="Confirm flatten and stop" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: "var(--loss-tint)", borderColor: "var(--loss-line)" }}>
              Sell every position at market now and end this run?
              <button onClick={() => setConfirm(false)}>Cancel</button>
              <button className="primary" style={{ background: "var(--fail)", borderColor: "var(--fail)", color: "var(--paper)" }} disabled={cmd.isPending}
                onClick={() => cmd.mutate({ kind: "flatten_and_stop" })}>Flatten and stop</button>
            </span>
          )}
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}>Level from next session
            <select value={plan.next?.level ?? plan.current ?? "balanced"} disabled={cmd.isPending}
              onChange={(e) => cmd.mutate({ kind: "set_level", payload: { level: e.target.value as Level } })}>
              {(Object.keys(LEVELS) as Level[]).map((k) => <option key={k} value={k}>{LEVELS[k].name}</option>)}
            </select>
          </label>
        </div>
      )}
      {plan.next && <p className="muted" style={{ fontSize: 13 }}>{LEVELS[plan.current ?? "balanced"].name} today; {LEVELS[plan.next.level].name} from {day(plan.next.from_session)}.</p>}
      {flat !== "none" && !ended && (
        <p className={flat === "sent" ? "banner" : "banner warn"} role="status" style={{ marginBottom: 0 }}>
          <b>{FLATTEN_TEXT[flat]}</b>{flat === "waiting" ? <> {FLATTEN_MORE}</> : null}</p>
      )}
      {cmd.isError && <p className="loss" role="alert">{String(cmd.error)}</p>}
      {pending.length > 0 && <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>Waiting for the engine: {pending.map((p) => commandText(p.kind)).join(", ")}</p>}
    </section>
  );
}
