// S41: the go/no-go review. Read-only: the verdict is computed by the engine from stored trades against the
// pre-registered bar (config/gate.yaml) and written to an append-only table. This page has no controls that change
// anything, and there is no live-trading switch anywhere in Kaibot (paper only).
import { useQuery } from "@tanstack/react-query";
import { useRunCtx } from "../components/Shell";
import { api, type GateStrategy } from "../lib/api";
import { gateOverallText, gateRows, gateStatusText } from "../lib/gateview";
import { dateEt, day, money, timeBoth } from "../lib/fmt";
import { RULES, V3_RULES, ruleName } from "../lib/explain";
import { experimentsOf } from "../components/Experiment";
import { findExperiment, type Experiment } from "../lib/experiment";
import { Empty, ErrorState, RuleName } from "../components/States";
import { Skeleton } from "../components/Skeleton";
import { STALE, poll } from "../lib/poll";

const STATE_TEXT = { pass: "pass", fail: "fail", unknown: "unknown", provisional: "on track (provisional)", pending: "not yet" };

function StrategyCard({ name, st, exp }: { name: string; st: GateStrategy; exp: Experiment | null }) {
  const s = gateStatusText(st.status);
  const done = Math.min(st.n_trades / Math.max(st.n_trades + st.trades_to_go, 1), 1);
  return (
    <section className="card" aria-labelledby={`g-${name}`}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
        <h2 id={`g-${name}`} style={{ margin: 0 }}><RuleName code={name} exp={exp} /></h2>
        <span className={`tag gate-${s.tone}`}>{s.label}</span>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        {st.n_trades.toLocaleString("en-US")} trades on the current code · {money(st.pnl_usd)} after costs
        {st.trades_to_go > 0 && <> · {st.trades_to_go.toLocaleString("en-US")} more needed before it can pass</>}
      </p>
      <div className="bar-track" aria-label={`${Math.round(done * 100)}% of the required trades`}><i style={{ width: `${done * 100}%` }} /></div>
      {st.kills.length > 0 && <ul className="loss" style={{ fontSize: 13 }}>{st.kills.map((k) => <li key={k}>Kill: {k}</li>)}</ul>}
      <table className="list">
        <thead><tr><th>Criterion</th><th>Now</th><th>Bar</th><th>State</th></tr></thead>
        <tbody>
          {gateRows(st).map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td><td>{r.value}</td><td>{r.limit}</td>
              <td><span className={`gate-dot gate-${r.state}`}>{STATE_TEXT[r.state]}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
        {st.excluded_older_code > 0 && <>{st.excluded_older_code} older trades on earlier code don't count. </>}
        {st.random_missing_days && st.random_missing_days.length > 0 && <>No random baseline for {st.random_missing_days.length} trading day(s), so that row stays unknown. </>}
        Deflated against {Math.max(st.n_trials, 1)} logged trial{Math.max(st.n_trials, 1) === 1 ? "" : "s"}.
      </p>
    </section>
  );
}

/** #210 follow-up: the Engine v3 rules' ten-year verdicts (decision 15), each with its honest tag. */
function V3Verdicts({ exps }: { exps: Experiment[] }) {
  return (
    <section className="card" aria-labelledby="v3-h">
      <h2 id="v3-h">Engine v3 rules: the ten-year test</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Each rule is tested on ten years of past prices, after costs, before it may trade on paper.
        A rule that fails never trades real money.</p>
      <ul className="v3-list">
        {V3_RULES.map((k) => (
          <li key={k}><b><RuleName code={k} exp={findExperiment(k, exps)} limits /></b>
            <div className="muted" style={{ fontSize: 13 }}>{RULES[k].what}</div></li>
        ))}
      </ul>
    </section>
  );
}

export function Gate() {
  const { run, overview } = useRunCtx();
  const exps = experimentsOf(overview?.heartbeat);
  // The engine writes one report after each close: cache it for minutes, poll gently.
  const q = useQuery({ queryKey: ["gate", run.id], queryFn: () => api.gate(run.id), staleTime: STALE.daily, ...poll(300_000) });
  if (q.isPending) return <Skeleton />;
  if (q.isError) return <ErrorState what="The go / no-go review" error={q.error} retry={() => q.refetch()} />;
  const latest = q.data.latest;
  return (
    <>
      <section className="card" aria-labelledby="gate-h">
        <h2 id="gate-h">Go / no-go review</h2>
        <p style={{ marginTop: 0, fontWeight: 600 }}>{latest ? gateOverallText(latest.overall) : "No gate report yet. The engine writes one after each session closes."}</p>
        <p className="muted" style={{ fontSize: 13 }}>
          The bar was fixed before any forward data existed: ≥ 900 trades over ≥ 3 months, t &gt; 3 after costs, deflated Sharpe &gt; 0,
          above the 95th percentile of 1,000 random entries, a higher Sharpe than SPY, and drawdown within the cap. The engine computes this report
          and it can't be edited afterwards.
        </p>
        <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>
          <b>There is no go-live button.</b> Kaibot trades paper only. Clearing the bar opens a written discussion, never a switch.
        </p>
        {latest && (
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            Report for {day(latest.session)} · computed {timeBoth(latest.ts)} · code {latest.fingerprint.slice(0, 8)}
            {latest.report.frozen_since && <> frozen since {dateEt(latest.report.frozen_since)}</>} · bar <code>{latest.bar_sha.slice(0, 12)}</code>
          </p>
        )}
      </section>
      <V3Verdicts exps={exps} />
      {latest && (Object.keys(latest.report.strategies ?? {}).length
        ? Object.entries(latest.report.strategies).map(([k, st]) => <StrategyCard key={k} name={k} st={st} exp={findExperiment(k, exps)} />)
        : <section className="card"><Empty>The latest report judged no strategy: none traded on the current code yet.</Empty></section>)}
      {q.data.history.length > 1 && (
        <section className="card" aria-labelledby="gh-h">
          <h2 id="gh-h">Verdict history</h2>
          <table className="list">
            <thead><tr><th>Session</th><th>Verdict</th><th>Trades</th></tr></thead>
            <tbody>
              {q.data.history.slice(0, 20).map((h) => (
                <tr key={h.id}><td>{day(h.session)}</td><td>{h.overall}</td>
                  <td>{Object.entries(h.n_trades ?? {}).map(([k, n]) => `${ruleName(k)}: ${n}`).join(" · ")}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
