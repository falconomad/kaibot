import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { supa } from "../lib/api";
import type { Level } from "../lib/api";
import { levelPlan } from "../lib/derive";
import { LEVELS } from "../lib/model";
import { day, money } from "../lib/fmt";
import { Controls } from "./Live";

const STATUS: Record<string, string> = {
  scheduled: "starts at the next open", running: "running", paused: "paused (no new entries)", stopped: "stopped by you",
  ended: "ended", halted: "halted",
};

export function Settings() {
  const { run, runs, overview } = useRunCtx();
  const plan = levelPlan(run, overview?.segments);
  const live = ["scheduled", "running", "paused"].includes(run.status);
  return (
    <>
      <section className="card" aria-labelledby="run-h">
        <h2 id="run-h">This run</h2>
        <div className="kv">
          <span>Run</span><span /><span className="v">{run.label ?? `Run ${run.id}`}</span>
          <span>Status</span><span /><span className="v">{STATUS[run.status] ?? run.status}{run.end_reason ? ` · ${run.end_reason}` : ""}</span>
          <span>Pot at start</span><span /><span className="v">{money(run.amount_usd)}</span>
          <span>Started / ended</span><span /><span className="v">{run.started_on ? day(run.started_on) : "—"} / {run.ended_on ? day(run.ended_on) : "—"}</span>
          <span>Level now</span><span /><span className="v">{plan.current ? LEVELS[plan.current].name : "—"}</span>
          <span>Scheduled change</span><span /><span className="v">{plan.next ? `${LEVELS[plan.next.level].name} from ${day(plan.next.from_session)}` : "none"}</span>
        </div>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>A level change never applies mid-session: it starts a new segment at the next open, so each
          level's statistics stay clean.</p>
      </section>

      {run.kind === "paper" && overview && live && <Controls run={run} overview={overview} />}

      <section className="card" aria-labelledby="lv-h">
        <h2 id="lv-h">The levels</h2>
        <table className="list">
          <thead><tr><th>Level</th><th className="r">Risk per trade</th><th className="r">Daily stop</th><th className="r">Largest position</th>
            <th className="r">Positions</th><th className="r">Run ends at</th><th>Strategies</th></tr></thead>
          <tbody>{(Object.keys(LEVELS) as Level[]).map((k) => {
            const l = LEVELS[k];
            return (
              <tr key={k} style={k === plan.current ? { background: "var(--paper)" } : undefined}>
                <td><b>{l.name}</b>{k === plan.current && <span className="muted"> · current</span>}</td>
                <td className="r num">{l.risk}% ({money((run.amount_usd * l.risk) / 100)})</td>
                <td className="r num">−{l.stop}%</td><td className="r num">{l.pos}%</td><td className="r num">{l.maxPositions}</td>
                <td className="r num">−{l.runDd}%</td><td className="muted">{l.strategies}</td>
              </tr>
            );
          })}</tbody>
        </table>
        <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>The engine enforces these from config/profiles.yaml; the dashboard only shows them. No level uses shorts,
          leverage or overnight holds.</p>
      </section>

      <section className="card" aria-labelledby="runs-h">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 id="runs-h" style={{ margin: 0 }}>All runs</h2>
          <span className="spacer" />
          <Link to="/start"><button className="primary">New run</button></Link>
        </div>
        <p className="muted" style={{ fontSize: 13 }}>Starting a new run closes the current run's positions first and keeps every old run read-only, so its record
          can't change. Pick a run from the menu at the top to look at it.</p>
        <table className="list">
          <thead><tr><th>Run</th><th>Kind</th><th>Status</th><th className="r">Amount</th><th>From</th><th>To</th></tr></thead>
          <tbody>{runs.map((r) => (
            <tr key={r.id} style={r.id === run.id ? { background: "var(--paper)" } : undefined}>
              <td><b>{r.label ?? `Run ${r.id}`}</b></td><td className="muted">{r.kind}</td>
              <td>{STATUS[r.status] ?? r.status}{["scheduled", "running", "paused"].includes(r.status) ? "" : <span className="muted"> · read-only</span>}</td>
              <td className="r num">{money(r.amount_usd)}</td>
              <td className="num">{r.started_on ? day(r.started_on) : "—"}</td><td className="num">{r.ended_on ? day(r.ended_on) : "—"}</td>
            </tr>))}</tbody>
        </table>
      </section>

      <section className="card" aria-labelledby="acct-h" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <div style={{ flex: "1 1 300px" }}>
          <h2 id="acct-h" style={{ marginBottom: 4 }}>Account</h2>
          <span className="muted" style={{ fontSize: 14 }}>Signed in as the owner · Alpaca paper account · data on Supabase</span>
        </div>
        <button className="full-sm" onClick={() => supa.auth.signOut()}>Sign out</button>
      </section>
    </>
  );
}
