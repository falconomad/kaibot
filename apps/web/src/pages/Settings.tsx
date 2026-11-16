import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { supa } from "../lib/api";
import type { Level } from "../lib/api";
import { levelPlan } from "../lib/derive";
import { LEVELS } from "../lib/model";
import { day, money } from "../lib/fmt";
import { ErrorState } from "../components/States";
import { Controls } from "./Live";

const STATUS: Record<string, string> = {
  scheduled: "starts at the next open", running: "running", paused: "paused (no new entries)", stopped: "stopped by you",
  ended: "ended", halted: "halted",
};

export function Settings() {
  const { run, runs, overview, overviewError, retryOverview, pickRun } = useRunCtx();
  const plan = levelPlan(run, overview?.segments);
  const live = ["scheduled", "running", "paused"].includes(run.status);
  return (
    <>
      <h1 className="page-title">Run &amp; settings</h1>
      {runs.length > 1 && pickRun && (
        <label className="run-pick">Run shown
          <select value={run.id} onChange={(e) => pickRun(Number(e.target.value))}>
            {runs.map((r) => <option key={r.id} value={r.id}>{r.label ?? `Run ${r.id}`}</option>)}
          </select>
        </label>
      )}
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

      {/* The controls need the overview: say so while it loads or if it failed, rather than leaving them out without a word. */}
      {run.kind === "paper" && live && (overview ? <Controls run={run} overview={overview} />
        : overviewError ? <ErrorState what="The run controls" error={overviewError} retry={retryOverview} />
        : <section className="card" aria-labelledby="ctl-h"><h2 id="ctl-h">Controls</h2><p className="skeleton" role="status">Loading…</p></section>)}

      <section aria-labelledby="lv-h" className="stack">
        <h2 id="lv-h" className="section-h">The levels</h2>
        <ul className="level-cards">{(Object.keys(LEVELS) as Level[]).map((k) => {
          const l = LEVELS[k];
          return (
            <li key={k} className={`level-card ${k === plan.current ? "on" : ""}`}>
              <div className="level-head"><b>{l.name}</b>{k === plan.current && <span className="tag info">current</span>}</div>
              <div className="kv">
                <span>Risk per trade</span><span /><span className="v num">{l.risk}% ({money((run.amount_usd * l.risk) / 100)})</span>
                <span>Daily stop</span><span /><span className="v num">−{l.stop}%</span>
                <span>Largest position</span><span /><span className="v num">{l.pos}%</span>
                <span>Positions at once</span><span /><span className="v num">{l.maxPositions}</span>
                <span>Run ends at</span><span /><span className="v num">−{l.runDd}% from the peak</span>
              </div>
              <p className="muted" style={{ fontSize: 13, margin: "8px 0 0" }}>{l.strategies}</p>
            </li>
          );
        })}</ul>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>The engine enforces these from config/profiles.yaml; the dashboard only shows them. No level uses shorts or leverage.</p>
      </section>

      <section aria-labelledby="runs-h" className="stack">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 id="runs-h" className="section-h" style={{ margin: 0 }}>All runs</h2>
          <span className="spacer" />
          <Link to="/start" className="btn">New run</Link>
        </div>
        <ul className="rows" aria-label="All runs">{runs.map((r) => (
          <li key={r.id}><div className={`row ${r.id === run.id ? "current" : ""}`}>
            <span className="row-mid"><b>{r.label ?? `Run ${r.id}`}</b>
              <span className="muted">{r.kind} · {STATUS[r.status] ?? r.status}{["scheduled", "running", "paused"].includes(r.status) ? "" : " · read-only"}
                {" · "}{r.started_on ? day(r.started_on) : "—"} to {r.ended_on ? day(r.ended_on) : "now"}</span></span>
            <span className="row-end num"><b>{money(r.amount_usd)}</b>{r.id === run.id && <span className="muted">shown</span>}</span>
          </div></li>))}</ul>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>Starting a new run closes the current run's positions first and keeps every old run read-only.</p>
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
