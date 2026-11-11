import { useQuery } from "@tanstack/react-query";
import { api, type Heartbeat } from "../lib/api";
import { ageS, ageText, budgetText, haltText, scanSummary, sessionTime } from "../lib/derive";
import { dateEt, timeBoth, timeEt } from "../lib/fmt";
import { Skeleton } from "../components/Skeleton";

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

const STATE_TAG = { ok: ["allow", "ok"], rules_only: ["downgrade", "rules-only"], no_entries: ["veto", "no entries"] } as const;
const usd = (v: number) => `$${v.toFixed(v < 10 ? 3 : 2)}`;

/** S32/S36: today's LLM spend against the caps. */
function LlmBudgetCard() {
  const q = useQuery({ queryKey: ["llm_budget"], queryFn: api.llmBudget, refetchInterval: 30_000 });
  if (q.isPending) return <section className="card"><h2>LLM budget</h2><p className="skeleton">Loading…</p></section>;
  if (q.isError) return <section className="card"><h2>LLM budget</h2><p className="loss">{String(q.error)}</p></section>;
  const b = q.data, t = budgetText(b), [cls, label] = STATE_TAG[b.state] ?? STATE_TAG.ok;
  const bar = (frac: number) => <div className="prog bar-row" aria-hidden="true"><i style={{ width: `${Math.min(100, frac * 100)}%`,
    background: frac >= 1 ? "var(--fail)" : frac >= 0.8 ? "var(--warn)" : undefined }} /></div>;
  const purposes = Object.entries(b.by_purpose);
  return (
    <section className="card" aria-labelledby="llm-h">
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <h2 id="llm-h" style={{ margin: 0 }}>LLM budget</h2><span className={`tag ${cls}`}>{label}</span>
      </div>
      <p style={{ fontSize: 13 }} className={t.tone === "breach" ? "loss" : undefined}>{t.text}</p>
      <div className="kv">
        <span>Today</span><span /><span className="v">{usd(b.day_usd)} / ${b.caps.daily_soft}</span>{bar(b.day_usd / b.caps.daily_soft)}
        <span>This month</span><span /><span className="v">{usd(b.month_usd)} / ${b.caps.monthly_hard}</span>{bar(b.month_usd / b.caps.monthly_hard)}
        <span>Calls today</span><span /><span className="v">{b.calls_today}{b.open_reserved_usd > 0 ? ` · ${usd(b.open_reserved_usd)} reserved` : ""}</span>
        {purposes.length > 0 && (<><span>By purpose</span><span /><span className="v">{purposes.map(([k, v]) => `${k} ${usd(v)}`).join(" · ")}</span></>)}
      </div>
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Above ${b.caps.daily_soft} a day the rules decide alone; ${b.caps.monthly_alert} a month raises an alert;
        at ${b.caps.monthly_hard} no new entries. Each call reserves its worst case first, so the caps can't be overshot.</p>
    </section>
  );
}

function ScanCard({ beat }: { beat: Heartbeat | undefined }) {
  const d = beat?.detail ?? null;
  const { headline, lines } = scanSummary(d?.scan, d?.entries_today ?? 0);
  const near = d?.scan?.recent_near ?? [];
  return (
    <section className="card" aria-labelledby="scan-h">
      <h2 id="scan-h">Today's scan</h2>
      <p style={{ fontWeight: 600, marginTop: 0 }}>{headline}</p>
      <ul style={{ fontSize: 13, margin: "0 0 8px", paddingLeft: 18 }}>{lines.map((l) => <li key={l}>{l}</li>)}</ul>
      {near.length > 0 && (
        <div>
          <table className="list">
            <thead><tr><th>Near-miss</th><th>Time (ET)</th><th>Dip (σ)</th><th>Why it didn't qualify</th></tr></thead>
            <tbody>{near.map((n, i) => <tr key={`${n.symbol}-${n.m}-${i}`}><td>{n.symbol}</td><td>{sessionTime(n.m)}</td><td>{n.z.toFixed(2)}</td><td>{n.why.replace(/ sigma/g, "σ")}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>The dip rule buys only a fall of at least 1.5σ that its sector doesn't explain, on calm volume, with no company news in 24 h, after a 5-minute higher low. Quiet up-days often have none.</p>
    </section>
  );
}

export function Health() {
  const q = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15_000 });
  if (q.isPending) return <Skeleton />;
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

      <ScanCard beat={h.heartbeat.find((b) => b.process === "engine")} />

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
        <LlmBudgetCard />
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
