import { useQuery } from "@tanstack/react-query";
import { api, type Heartbeat } from "../lib/api";
import { ageS, ageText, haltText } from "../lib/derive";
import { dateEt, timeBoth, timeEt } from "../lib/fmt";

const PROC: Record<string, { name: string; what: string; staleS: number }> = {
  engine: { name: "Engine", what: "trades the run: reads prices, decides, places bracket orders", staleS: 90 },
  watchdog: { name: "Watchdog", what: "separate process: cancels entries if the engine goes quiet, flattens at 15:55 ET", staleS: 60 },
};

function Proc({ h }: { h: Heartbeat }) {
  const p = PROC[h.process] ?? { name: h.process, what: "", staleS: 120 };
  const age = ageS(h.ts);
  const d = (h.detail ?? {}) as Record<string, unknown>;
  const ok = age <= p.staleS && d.state !== "stopped" && d.state !== "error";
  return (
    <tr>
      <td><div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={ok ? "var(--sea)" : age > p.staleS ? "var(--stale)" : "var(--warn)"} /></svg>
          <b>{p.name}</b><span className="sr-only">{ok ? "healthy" : "not healthy"}</span></div>
        <div className="muted" style={{ fontSize: 12 }}>{p.what}</div></td>
      <td className="num">{String(d.state ?? "—")}</td>
      <td className="num">{ageText(age)}</td>
      <td className="muted num" style={{ fontSize: 12 }}>
        {d.code_sha ? `code ${d.code_sha} · ` : ""}{d.host ? `host ${String(d.host).slice(0, 12)} · ` : ""}
        {d.account_clean === false ? "account NOT clean · " : ""}{d.halted ? "entries halted · " : ""}
        {typeof d.engine_heartbeat_age_s === "number" ? `sees engine ${Math.round(d.engine_heartbeat_age_s)} s` : ""}
      </td>
    </tr>
  );
}

const EV: Record<string, string> = {
  engine_start: "Engine started", engine_stop: "Engine stopped", engine_error: "Engine error (retrying)",
  session_start: "Session started", session_end: "Session closed", shadow_session_end: "Shadow strategies scored",
  halt: "Halt", halt_cleared: "Halt cleared at the new session", account_not_clean: "Paper account not clean",
  calendar_stale: "Event calendar out of date", run_paused: "Run paused", moc_refused: "Market-on-close refused",
  watchdog_flatten: "Watchdog flattened", watchdog_recovered: "Engine heartbeat back", shadow_error: "Shadow strategy error",
};

export function Health() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  if (q.isPending) return <p className="skeleton">Loading…</p>;
  if (q.isError) return <p className="loss">{String(q.error)}</p>;
  const h = q.data;
  const procs = h.heartbeat.filter((b) => b.process in PROC);
  const lastDead = h.deadman.runs[0];
  return (
    <>
      <section className="card" aria-labelledby="proc-h">
        <h2 id="proc-h">Processes</h2>
        <table className="list">
          <thead><tr><th>Process</th><th>State</th><th>Last heartbeat</th><th>Detail</th></tr></thead>
          <tbody>{procs.length ? procs.map((b) => <Proc key={b.process} h={b} />) : <tr><td colSpan={4} className="muted">No process has reported yet.</td></tr>}</tbody>
        </table>
        {h.pending_commands.length > 0 && <p className="muted" style={{ fontSize: 12 }}>Waiting for the engine: {h.pending_commands.map((c) => c.kind.replace(/_/g, " ")).join(", ")}</p>}
      </section>

      <div className="grid">
        <section className="card" aria-labelledby="dm-h">
          <h2 id="dm-h">Off-box dead-man</h2>
          <p style={{ fontSize: 13, marginTop: 0 }}>If the engine's heartbeat is more than 3 minutes old at 15:56 ET, Supabase closes every paper position by itself,
            even if your Mac is off.</p>
          <div className="kv">
            <span>Armed (paper keys in Vault)</span><span /><span className={`v ${h.deadman.armed ? "gain" : "loss"}`}>{h.deadman.armed ? "yes" : "no: see docs/ops/failsafe.md"}</span>
            <span>Schedules</span><span /><span className="v">{h.deadman.jobs.filter((j) => j.active).length} active</span>
            <span>Last check</span><span /><span className="v">{lastDead ? `${dateEt(lastDead.start)} ${timeEt(lastDead.start)} ET · ${lastDead.status}` : "not run yet"}</span>
          </div>
        </section>
        <section className="card" aria-labelledby="halt-h">
          <h2 id="halt-h">Recent halts</h2>
          {h.halts.length ? (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{h.halts.slice(0, 12).map((x, i) => (
              <li key={i}><span className="num">{dateEt(x.ts)} {timeEt(x.ts)}</span> {haltText(x)}
                {x.reason && <div className="muted" style={{ fontSize: 12 }}>{x.reason}</div>}</li>))}</ul>
          ) : <p className="muted">None.</p>}
        </section>
      </div>

      <section className="card" aria-labelledby="ev-h">
        <h2 id="ev-h">Operations log</h2>
        <table className="list">
          <thead><tr><th>When</th><th>What</th><th>Detail</th></tr></thead>
          <tbody>{h.events.map((e, i) => (
            <tr key={i}>
              <td className="num" title={timeBoth(e.ts)}>{dateEt(e.ts)} {timeEt(e.ts)}</td>
              <td>{EV[e.type] ?? e.type.replace(/_/g, " ")}</td>
              <td className="muted num" style={{ fontSize: 12, maxWidth: 560, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {summary(e.payload)}</td>
            </tr>))}</tbody>
        </table>
      </section>
    </>
  );
}

function summary(p: Record<string, unknown>) {
  return Object.entries(p ?? {}).filter(([, v]) => v !== null && typeof v !== "object").slice(0, 6)
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`).join(" · ");
}
