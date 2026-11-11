import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { engineChip, livePot, useRunCtx } from "../components/Shell";
import { PotChart } from "../components/PotChart";
import { api, type CommandKind, type Level, type Overview, type Run, type Trade } from "../lib/api";
import { BAR, LEVELS, curve, stats } from "../lib/model";
import { EXIT_LABEL, R, dateEt, day, money, pct, px, signedMoney, timeEt, tone } from "../lib/fmt";
import { evidenceVerdict, levelPlan } from "../lib/derive";

function Progress({ label, now, bar, fmt = (v: number) => v.toFixed(0) }: { label: string; now: number; bar: number; fmt?: (v: number) => string }) {
  return (
    <>
      <span>{label}</span>
      <div className="prog" aria-hidden="true"><i style={{ width: `${Math.min(100, Math.max(0, (now / bar) * 100))}%` }} /></div>
      <span className="v">{fmt(now)} / {fmt(bar)}</span>
    </>
  );
}

export function TradeRows({ rows }: { rows: Trade[] }) {
  const nav = useNavigate();
  return (
    <table className="list">
      <thead><tr><th>Time (ET)</th><th>Stock</th><th>Why it closed</th><th className="r">Entry → exit</th><th className="r">P&amp;L</th><th className="r">R</th><th>Room</th></tr></thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id} className="click" tabIndex={0} onClick={() => nav(`/trades/${t.id}`)}
            onKeyDown={(e) => e.key === "Enter" && nav(`/trades/${t.id}`)}>
            <td className="num">{dateEt(t.entry_ts)} {timeEt(t.entry_ts)}</td>
            <td><b>{t.symbol}</b> <span className="muted">· {t.qty} sh</span></td>
            <td className="muted">{t.exit_reason ? EXIT_LABEL[t.exit_reason] ?? t.exit_reason : "open"}</td>
            <td className="r num">{px(t.entry_px)} → {t.exit_px === null ? "open" : px(t.exit_px)}</td>
            <td className={`r num ${tone(t.pnl_usd)}`}>{signedMoney(t.pnl_usd)}</td>
            <td className="r num">{R(t.pnl_usd, t.risk_usd)}</td>
            <td>{t.verdict && <span className={`tag ${t.verdict}`}>{t.verdict}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Controls({ run, overview }: { run: Run; overview: Overview }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const cmd = useMutation({
    mutationFn: ({ kind, payload }: { kind: CommandKind; payload?: Record<string, unknown> }) => api.command(kind, payload),
    onSuccess: () => { setConfirm(false); qc.invalidateQueries({ queryKey: ["overview"] }); },
  });
  const pending = overview.pending_commands;
  const plan = levelPlan(run, overview.segments);
  const paused = run.status === "paused";
  const ended = !["scheduled", "running", "paused"].includes(run.status);
  return (
    <section className="card" aria-labelledby="ctl-h">
      <h2 id="ctl-h">Controls</h2>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>No trade-by-trade control. You can pause new entries, or close everything and stop the run.
        The engine applies a command within about 10 seconds; nothing here changes until it does.</p>
      {ended ? <p>This run has {run.status}{run.end_reason ? `: ${run.end_reason}` : ""}. Start a new run from the menu.</p> : (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <button disabled={cmd.isPending} onClick={() => cmd.mutate({ kind: paused ? "resume" : "pause" })}>{paused ? "Resume entries" : "Pause entries"}</button>
          {!confirm ? <button onClick={() => setConfirm(true)} style={{ borderColor: "var(--fail)", color: "var(--fail)" }}>Flatten and stop</button> : (
            <span className="banner warn" role="alertdialog" aria-label="Confirm flatten and stop" style={{ display: "flex", gap: 10, alignItems: "center" }}>
              Sell every position at market now and end this run?
              <button onClick={() => setConfirm(false)}>Cancel</button>
              <button className="primary" style={{ background: "var(--fail)", borderColor: "var(--fail)" }} disabled={cmd.isPending}
                onClick={() => cmd.mutate({ kind: "flatten_and_stop" })}>Flatten and stop</button>
            </span>
          )}
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>Level from next session
            <select value={plan.next?.level ?? plan.current ?? "balanced"} disabled={cmd.isPending}
              onChange={(e) => cmd.mutate({ kind: "set_level", payload: { level: e.target.value as Level } })}>
              {(Object.keys(LEVELS) as Level[]).map((k) => <option key={k} value={k}>{LEVELS[k].name}</option>)}
            </select>
          </label>
        </div>
      )}
      {plan.next && <p className="muted" style={{ fontSize: 12 }}>{LEVELS[plan.current ?? "balanced"].name} today; {LEVELS[plan.next.level].name} from {day(plan.next.from_session)}.</p>}
      {cmd.isError && <p className="loss" role="alert">{String(cmd.error)}</p>}
      {pending.length > 0 && <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Waiting for the engine: {pending.map((p) => p.kind.replace(/_/g, " ")).join(", ")}</p>}
    </section>
  );
}

function Positions({ positions }: { positions: Record<string, { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number }> }) {
  const nav = useNavigate();
  const rows = Object.entries(positions);
  if (!rows.length) return <p className="muted" style={{ fontSize: 13 }}>No open positions. Kaibot is flat by 15:55 every day.</p>;
  return (
    <table className="list">
      <thead><tr><th>Stock</th><th className="r">Shares</th><th className="r">Entry</th><th className="r">Last</th><th className="r">Stop</th><th className="r">Target</th><th className="r">Open P&amp;L</th></tr></thead>
      <tbody>{rows.map(([s, p]) => {
        const pnl = p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null;
        return (
          <tr key={s} className="click" onClick={() => nav(`/trades/${p.trade_id}`)}>
            <td><b>{s}</b>{p.status === "pending" && <span className="muted"> · filling</span>}</td><td className="r num">{p.qty}</td>
            <td className="r num">{px(p.fill)}</td><td className="r num">{px(p.last)}</td><td className="r num">{px(p.stop)}</td>
            <td className="r num">{px(p.target)}</td><td className={`r num ${tone(pnl)}`}>{signedMoney(pnl)}</td>
          </tr>
        );
      })}</tbody>
    </table>
  );
}

export function Live() {
  const { run, overview } = useRunCtx();
  const trades = useQuery({ queryKey: ["trades-all", run.id], queryFn: () => api.trades(run.id, 500, 0) });
  const evidence = useQuery({ queryKey: ["evidence", run.id], queryFn: () => api.evidence(run.id), refetchInterval: 60_000 });
  const lv = run.level ? LEVELS[run.level] : null;
  if (!overview || trades.isPending) return <p className="skeleton">Loading…</p>;
  if (trades.isError) return <p className="loss">{String(trades.error)}</p>;

  const daily = overview.daily;
  const c = curve(run.amount_usd, daily);
  const s = stats(run.amount_usd, daily, trades.data.rows);
  const live = run.kind === "paper" ? livePot(run, overview) : null;
  const chip = engineChip(overview, run);
  const pot = live?.pot ?? c.at(-1)?.pot ?? run.amount_usd;
  const lastDay = [...daily].reverse().find((d) => d.trades > 0);
  const lastRows = lastDay ? trades.data.rows.filter((t) => t.entry_ts.slice(0, 10) === lastDay.session) : [];
  const vsSpy = s.netUsd - (c.at(-1) ? c.at(-1)!.spy - run.amount_usd : 0);
  const newRun = overview.pending_commands.find((p) => p.kind === "new_run");

  return (
    <>
      {run.kind === "replay" && (
        <div className="banner">
          <b>Replay, not live trading.</b> This is Variant A run on past market data from {day(run.started_on!)} to {day(run.ended_on!)}
          {" "}with the Balanced rules and the shadow cost model. The engine starts paper trading in M1; from then on this screen updates every 5 seconds.
        </div>
      )}
      {run.kind === "paper" && !chip.live && ["scheduled", "running", "paused"].includes(run.status) && (
        <div className="banner warn">
          <b>The engine isn't running.</b> Nothing trades until you start it on your Mac: open Terminal in the kaibot folder and run
          {" "}<code className="num">./scripts/run_engine.sh</code>. Open trades keep their stop-loss at Alpaca meanwhile.
        </div>
      )}
      {newRun && (
        <div className="banner warn">
          A new run is queued: <b className="num">{money(Number(newRun.payload.amount_usd))}</b> at <b>{LEVELS[newRun.payload.level as keyof typeof LEVELS]?.name}</b>.
          It starts when the engine next comes online.
        </div>
      )}

      <section className="card" aria-labelledby="pot-h">
        <h2 id="pot-h">{run.kind === "replay" ? "Pot at the end of the replay, after costs" : "Pot now, after costs"}</h2>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
          <div className="hero">{money(pot)}</div>
          <div className={`num ${tone(pot - run.amount_usd)}`}>{pot >= run.amount_usd ? "▲" : "▼"} {signedMoney(pot - run.amount_usd)} · {pct(((pot - run.amount_usd) / run.amount_usd) * 100)}</div>
          {live && <div className={`num ${tone(live.dayPnl)}`}>today {signedMoney(live.dayPnl)}</div>}
          <div className="num muted">start {money(run.amount_usd)}</div>
        </div>
        <p style={{ margin: "6px 0 12px" }}>
          {vsSpy >= 0 ? "Ahead of" : "Behind"} SPY by <b className="num">{money(Math.abs(vsSpy))}</b> over the same days
          {" "}(SPY {pct(s.spyPct)}). Worse-of shadow and broker P&amp;L is what's reported.
        </p>
        <PotChart data={c} start={run.amount_usd} />
      </section>

      <div className="grid">
        <section className="card" aria-labelledby="bar-h">
          <h2 id="bar-h">Evidence toward the go-live bar</h2>
          <div className="kv">
            <Progress label="Trades" now={evidence.data?.n ?? s.trades} bar={BAR.trades} />
            <Progress label="Months" now={s.months} bar={BAR.months} fmt={(v) => v.toFixed(1)} />
            <Progress label="t-stat (net)" now={evidence.data?.t ?? s.t} bar={BAR.t} fmt={(v) => v.toFixed(2)} />
            <span>Max drawdown</span><span /><span className="v">{s.maxDdPct.toFixed(1)}% / {lv?.runDd ?? "—"}%</span>
            <span>Hit rate</span><span /><span className="v">{(s.hitRate * 100).toFixed(1)}%</span>
            <span>Profit factor</span><span /><span className="v">{s.profitFactor?.toFixed(2) ?? "—"}</span>
            <span>Losing trading days</span><span /><span className="v">{(s.losingDayShare * 100).toFixed(0)}%</span>
          </div>
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            The bar is fixed in advance: ≥ {BAR.trades} trades over ≥ {BAR.months} months, t &gt; {BAR.t}, above the {BAR.randomPct}th
            percentile of random entries, and better risk-adjusted than SPY. {s.t < BAR.t ? "Not met." : ""}
          </p>
          {evidence.data && <p style={{ fontSize: 13, marginBottom: 0 }}>{evidenceVerdict(evidence.data).text}</p>}
        </section>

        {lv && (
          <section className="card" aria-labelledby="rules-h">
            <h2 id="rules-h">Rules for {lv.name}</h2>
            <div className="kv">
              <span>Risk per trade</span><span /><span className="v">{lv.risk}% of pot</span>
              <span>Daily stop (flatten and halt)</span><span /><span className="v">−{lv.stop}%</span>
              <span>Largest position</span><span /><span className="v">{lv.pos}%</span>
              <span>Positions at once</span><span /><span className="v">{lv.maxPositions}</span>
              <span>New trades per day</span><span /><span className="v">≤ {lv.entries}</span>
              <span>Run ends at drawdown</span><span /><span className="v">{lv.runDd}% from peak</span>
              <span>Entries / flat by</span><span /><span className="v">09:35–15:45 / 15:55 ET</span>
            </div>
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>No shorts, leverage or overnight holds. After 3 losses in a row, entries pause for 60 minutes.</p>
          </section>
        )}
      </div>

      {run.kind === "paper" && (
        <div className="grid">
          <section className="card" aria-labelledby="pos-h">
            <h2 id="pos-h">Open positions · {Object.keys(live?.positions ?? {}).length} / {lv?.maxPositions ?? "—"}</h2>
            <Positions positions={live?.positions ?? {}} />
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Every stop and target is held at Alpaca, so it fires even if your Mac is off.</p>
          </section>
          <Controls run={run} overview={overview} />
        </div>
      )}

      <section className="card" aria-labelledby="last-h">
        <h2 id="last-h">{run.kind === "paper" ? "Latest trades" : lastDay ? `Trades on ${day(lastDay.session)}, the last trading day` : "Trades"}</h2>
        {run.kind === "replay" && <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Open positions: none. Kaibot is flat by 15:55 every day. Select a trade for the full reasoning.</p>}
        {(run.kind === "paper" ? trades.data.rows.slice(0, 12) : lastRows).length
          ? <TradeRows rows={run.kind === "paper" ? trades.data.rows.slice(0, 12) : lastRows} />
          : <p className="muted">No trades yet. Kaibot checks for setups every 5 minutes from 09:35 to 15:45 ET; many days have none.</p>}
      </section>
    </>
  );
}
