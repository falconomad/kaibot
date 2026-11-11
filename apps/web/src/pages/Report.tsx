import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { useRunCtx } from "../components/Shell";
import { api, type AttributionGroup } from "../lib/api";
import { DIM_LABEL, FLAG_TEXT, dayVsBenchmarks, evidenceVerdict, flagged, fmtR, haltText } from "../lib/derive";
import { BAR } from "../lib/model";
import { EXIT_LABEL, R, day, money, pct, px, signedMoney, timeEt, tone } from "../lib/fmt";

const STRAT: Record<string, string> = {
  dip_reversal: "Dip reversal", spy_late_momentum: "SPY end-of-day momentum",
  orb_stocks_in_play: "Opening-range breakout (shadow)", news_continuation: "News continuation (shadow)",
};
const strat = (s: string) => STRAT[s] ?? s.replace(/_/g, " ");

/** S30: the daily report card. Available once the engine closes the session (~16:00 ET, 22:00 CET). */
function DayCard({ runId }: { runId: number }) {
  const [session, setSession] = useState<string | undefined>();
  const q = useQuery({ queryKey: ["report", runId, session], queryFn: () => api.report(runId, session) });
  if (q.isPending) return <section className="card"><p className="skeleton">Loading the report…</p></section>;
  if (q.isError) return <section className="card"><p className="loss">{String(q.error)}</p></section>;
  const r = q.data;
  if (!r.session) return (
    <section className="card">
      <h2>Daily report card</h2>
      <p className="muted">No finished session yet. The first report appears after the engine closes a full session, about 16:00 ET (22:00 CET).</p>
    </section>
  );
  const b = dayVsBenchmarks(r);
  const trades = r.trades ?? [];
  const closed = trades.filter((t) => t.pnl_usd !== null);
  const best = [...closed].sort((a, c) => (c.pnl_usd ?? 0) - (a.pnl_usd ?? 0))[0];
  const worst = [...closed].sort((a, c) => (a.pnl_usd ?? 0) - (c.pnl_usd ?? 0))[0];
  return (
    <section className="card" aria-labelledby="day-h">
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id="day-h" style={{ margin: 0 }}>Report card · {day(r.session)}</h2>
        <span className="spacer" />
        <label className="muted" style={{ fontSize: 13 }}>Session{" "}
          <select value={r.session} onChange={(e) => setSession(e.target.value)}>
            {r.sessions.map((s) => <option key={s} value={s}>{day(s)}</option>)}
          </select>
        </label>
      </div>
      <div className="kv" style={{ marginTop: 12 }}>
        <span>Pot, after costs</span><span /><span className={`v ${tone(r.day?.pnl)}`}>{signedMoney(r.day?.pnl)} · {pct(b.potPct)}</span>
        <span>SPY (buy and hold)</span><span /><span className="v">{pct(b.spyPct)}</span>
        <span>Cash</span><span /><span className="v">{pct(b.cashPct, 3)}</span>
        <span>Verdict</span><span /><span className="v">{b.beatSpy && b.beatCash ? "beat SPY and cash" : b.beatCash ? "beat cash, not SPY" : b.beatSpy ? "beat SPY, not cash" : "behind SPY and cash"}</span>
        <span>Broker vs shadow P&amp;L</span><span /><span className="v">{signedMoney(r.day?.pnl_venue)} / {signedMoney(r.day?.pnl_shadow)} <span className="muted">(the worse is reported)</span></span>
        <span>Setups seen / taken</span><span /><span className="v">{r.signals?.live ?? 0} / {r.signals?.approved ?? 0}</span>
      </div>
      <p className="muted" style={{ fontSize: 12 }}>One day says almost nothing about an edge. Read the evidence counter, not the daily colour.</p>

      <h3 style={{ fontSize: 14, margin: "16px 0 6px" }}>Trades</h3>
      {trades.length ? (
        <table className="list">
          <thead><tr><th>Time (ET)</th><th>Stock</th><th>Strategy</th><th>Why it closed</th><th className="r">Entry → exit</th><th className="r">P&amp;L</th><th className="r">R</th></tr></thead>
          <tbody>{trades.map((t) => (
            <tr key={t.id}>
              <td className="num">{timeEt(t.entry_ts)}</td>
              <td><Link to={`/trades/${t.id}`}><b>{t.symbol}</b></Link> <span className="muted">· {t.qty} sh</span></td>
              <td className="muted">{strat(t.strategy)}</td>
              <td className="muted">{t.exit_reason ? EXIT_LABEL[t.exit_reason] ?? t.exit_reason : "open"}</td>
              <td className="r num">{px(t.entry_px)} → {t.exit_px === null ? "open" : px(t.exit_px)}</td>
              <td className={`r num ${tone(t.pnl_usd)}`}>{signedMoney(t.pnl_usd)}</td>
              <td className="r num">{R(t.pnl_usd, t.risk_usd)}</td>
            </tr>))}</tbody>
        </table>
      ) : <p className="muted">No trades this session. Most days with no qualifying setup end flat; that is the rules working.</p>}
      {best && worst && best.id !== worst.id && (
        <p className="muted" style={{ fontSize: 13 }}>Best {best.symbol} {signedMoney(best.pnl_usd)} · worst {worst.symbol} {signedMoney(worst.pnl_usd)}</p>
      )}

      {(r.vetoes?.length ?? 0) > 0 && (<>
        <h3 style={{ fontSize: 14, margin: "16px 0 6px" }}>Setups not taken, and why</h3>
        <div className="kv">{r.vetoes!.map((v) => (<Fragment key={v.reason}><span>{v.reason}</span><span /><span className="v">{v.n}</span></Fragment>))}</div>
      </>)}

      {(r.shadow?.length ?? 0) > 0 && (<>
        <h3 style={{ fontSize: 14, margin: "16px 0 6px" }}>Shadow strategies (never traded, counterfactual after costs)</h3>
        <table className="list">
          <thead><tr><th>Time (ET)</th><th>Strategy</th><th>Stock</th><th className="r">Would have made</th><th className="r">R</th></tr></thead>
          <tbody>{r.shadow!.map((s, i) => (
            <tr key={i}><td className="num">{timeEt(s.ts)}</td><td className="muted">{strat(s.strategy)}</td><td><b>{s.symbol}</b></td>
              <td className={`r num ${tone(s.pnl_usd)}`}>{s.pnl_usd === null ? (s.veto ?? "—") : signedMoney(s.pnl_usd)}</td>
              <td className="r num">{fmtR(s.r)}</td></tr>))}</tbody>
        </table>
      </>)}

      {(r.halts?.length ?? 0) > 0 && (<>
        <h3 style={{ fontSize: 14, margin: "16px 0 6px" }}>Halts</h3>
        <ul style={{ margin: 0, paddingLeft: 18 }}>{r.halts!.map((h, i) => <li key={i}><span className="num">{timeEt(h.ts)}</span> {haltText(h)}</li>)}</ul>
      </>)}
    </section>
  );
}

/** S29: the evidence counter with a confidence interval. */
function EvidenceCard({ runId }: { runId: number }) {
  const q = useQuery({ queryKey: ["evidence", runId], queryFn: () => api.evidence(runId), refetchInterval: 60_000 });
  const v = evidenceVerdict(q.data);
  const e = q.data;
  return (
    <section className="card" aria-labelledby="ev-h">
      <h2 id="ev-h">Evidence counter</h2>
      <div className="kv">
        <span>Closed trades</span>
        <div className="prog" aria-hidden="true"><i style={{ width: `${v.frac * 100}%` }} /></div>
        <span className="v">{e?.n ?? 0} / {BAR.trades}</span>
        <span>Average trade</span><span /><span className="v">{fmtR(e?.mean_r)}</span>
        <span>95% interval</span><span /><span className="v">{e?.ci_lo === null || e?.ci_lo === undefined ? "—" : `${fmtR(e.ci_lo)} … ${fmtR(e.ci_hi)}`}</span>
        <span>t-stat</span><span /><span className="v">{e?.t?.toFixed(2) ?? "—"} <span className="muted">/ {BAR.t}</span></span>
        <span>Sessions</span><span /><span className="v">{e?.sessions ?? 0}</span>
      </div>
      <p className={tone(v.tone === "gain" ? 1 : v.tone === "loss" ? -1 : 0)} style={{ fontSize: 13, marginBottom: 0 }}>{v.text}</p>
      {e && e.by_strategy.length > 1 && (
        <p className="muted" style={{ fontSize: 12 }}>{e.by_strategy.map((s) => `${strat(s.strategy)}: ${s.n} trades, ${fmtR(s.mean_r)}`).join(" · ")}</p>
      )}
    </section>
  );
}

function GroupTable({ rows }: { rows: AttributionGroup[] }) {
  return (
    <table className="list">
      <thead><tr><th>Group</th><th className="r">Trades</th><th className="r">P&amp;L</th><th className="r">Avg R</th><th className="r">Win rate</th><th className="r">t</th><th>Flag</th></tr></thead>
      <tbody>{rows.map((g) => (
        <tr key={g.dim + g.key}>
          <td>{g.dim === "strategy" ? strat(g.key) : g.dim === "exit_reason" ? (EXIT_LABEL[g.key] ?? g.key) : g.key}</td>
          <td className="r num">{g.n}</td><td className={`r num ${tone(g.pnl)}`}>{signedMoney(g.pnl)}</td>
          <td className="r num">{fmtR(g.mean_r)}</td><td className="r num">{(g.win * 100).toFixed(0)}%</td>
          <td className="r num">{g.t?.toFixed(2) ?? "—"}</td>
          <td>{g.flag && <span className={`tag ${g.flag === "kill" ? "veto" : "downgrade"}`} title={FLAG_TEXT[g.flag]}>{g.flag}</span>}</td>
        </tr>))}</tbody>
    </table>
  );
}

/** S31: weekly attribution with automatic flags. Flags inform the quant; they never switch anything off. */
function Attribution({ runId }: { runId: number }) {
  const [weeks, setWeeks] = useState(4);
  const [dim, setDim] = useState("strategy");
  const q = useQuery({ queryKey: ["attribution", runId, weeks], queryFn: () => api.attribution(runId, weeks) });
  const a = q.data;
  const flags = a ? flagged(a.groups) : [];
  const weeksList = a ? [...new Set(a.weekly.map((w) => w.week))] : [];
  return (
    <section className="card" aria-labelledby="attr-h">
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <h2 id="attr-h" style={{ margin: 0 }}>Where the P&amp;L comes from</h2>
        <span className="spacer" />
        <div className="tabs" role="tablist" aria-label="Period">
          {[[4, "4 weeks"], [13, "13 weeks"], [520, "All"]].map(([w, l]) => (
            <button key={w} role="tab" aria-selected={weeks === w} onClick={() => setWeeks(Number(w))}>{l}</button>))}
        </div>
      </div>
      {q.isPending ? <p className="skeleton">Loading…</p> : q.isError ? <p className="loss">{String(q.error)}</p> : !a || a.n === 0 ? (
        <p className="muted">No closed trades in this period.</p>
      ) : (<>
        <p className="muted" style={{ fontSize: 13 }}>{a.n} closed trades since {day(a.since.slice(0, 10))}. A <b>kill</b> flag means ≥ 20 trades with t ≤ −2;
          {" "}<b>watch</b> means losing on average over ≥ 10. Flags are prompts to investigate, never automatic changes.</p>
        {flags.length > 0 && (
          <div className="banner warn" role="status">
            <b>{flags.length} flagged:</b> {flags.slice(0, 6).map((f) => `${DIM_LABEL[f.dim] ?? f.dim} ${f.dim === "strategy" ? strat(f.key) : f.key} (${f.flag})`).join(", ")}
          </div>
        )}
        <div className="tabs" role="tablist" aria-label="Group by" style={{ margin: "12px 0" }}>
          {Object.entries(DIM_LABEL).map(([k, l]) => <button key={k} role="tab" aria-selected={dim === k} onClick={() => setDim(k)}>{l}</button>)}
        </div>
        <GroupTable rows={a.groups.filter((g) => g.dim === dim)} />
        {weeksList.length > 1 && (<>
          <h3 style={{ fontSize: 14, margin: "16px 0 6px" }}>By week</h3>
          <table className="list">
            <thead><tr><th>Week of</th>{[...new Set(a.weekly.map((w) => w.strategy))].map((s) => <th key={s} className="r">{strat(s)}</th>)}</tr></thead>
            <tbody>{weeksList.map((wk) => (
              <tr key={wk}><td className="num">{day(wk)}</td>
                {[...new Set(a.weekly.map((w) => w.strategy))].map((s) => {
                  const w = a.weekly.find((x) => x.week === wk && x.strategy === s);
                  return <td key={s} className={`r num ${tone(w?.pnl)}`}>{w ? `${signedMoney(w.pnl)} (${w.n})` : "—"}</td>;
                })}</tr>))}</tbody>
          </table>
        </>)}
      </>)}
    </section>
  );
}

export function Report() {
  const { run } = useRunCtx();
  return (
    <>
      {run.kind === "replay" && <div className="banner"><b>Replay.</b> These numbers come from past data, not live paper trading.</div>}
      <div className="grid">
        <EvidenceCard runId={run.id} />
        <section className="card" aria-labelledby="how-h">
          <h2 id="how-h">How to read this</h2>
          <p style={{ fontSize: 13, marginTop: 0 }}>The report card is ready after each session closes (16:00 ET, 22:00 CET). Every number is after costs and uses the
            worse of the broker's fills and Kaibot's shadow cost model.</p>
          <p className="muted" style={{ fontSize: 13, marginBottom: 0 }}>Expect 4–5 losing days in 10. What matters is the evidence counter: {BAR.trades}+ trades over {BAR.months}+ months with t &gt; {BAR.t}.</p>
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Pot start {money(run.amount_usd)}.</p>
        </section>
      </div>
      <DayCard runId={run.id} />
      <Attribution runId={run.id} />
    </>
  );
}
