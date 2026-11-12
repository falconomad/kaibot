import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { engineChip, livePot, useRunCtx } from "../components/Shell";
import { PotChart } from "../components/PotChart";
import { api, type CommandKind, type Level, type Overview, type Run, type Trade } from "../lib/api";
import { BAR, LEVELS, curve, stats } from "../lib/model";
import { R, dateEt, day, money, pct, px, signedMoney, timeEt, tone } from "../lib/fmt";
import { commandText, exitText, ruleName } from "../lib/explain";
import { evidenceVerdict, levelPlan } from "../lib/derive";
import { StockBadge, StockCell, companyName } from "../components/StockBadge";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState, TestPhaseBanner } from "../components/States";
import { STALE, poll } from "../lib/poll";

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
      <thead><tr><th>Stock</th><th>Time (ET)</th><th className="hide-sm">Why it closed</th><th className="r hide-sm">Entry → exit</th><th className="r">P&amp;L</th><th className="r">R</th><th className="hide-sm">Room</th></tr></thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id} className="click" tabIndex={0} aria-label={`${t.symbol} trade on ${dateEt(t.entry_ts)}: open details`}
            onClick={() => nav(`/trades/${t.id}`)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nav(`/trades/${t.id}`); } }}>
            <td title={`code: ${t.strategy}`}><StockCell symbol={t.symbol} sub={`${t.qty} sh · ${ruleName(t.strategy)}`} /></td>
            <td className="num muted">{dateEt(t.entry_ts)} {timeEt(t.entry_ts)}</td>
            <td className="muted hide-sm" title={t.exit_reason ? `code: ${t.exit_reason}` : undefined}>{exitText(t.exit_reason)}</td>
            <td className="r num hide-sm">{px(t.entry_px)} → {t.exit_px === null ? "open" : px(t.exit_px)}</td>
            <td className={`r num ${tone(t.pnl_usd)}`}>{signedMoney(t.pnl_usd)}</td>
            <td className="r num">{R(t.pnl_usd, t.risk_usd)}</td>
            <td className="hide-sm">{t.verdict && <span className={`tag ${t.verdict}`}>{t.verdict}</span>}</td>
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
          <button className="full-sm" disabled={cmd.isPending} onClick={() => cmd.mutate({ kind: paused ? "resume" : "pause" })}>{paused ? "Resume entries" : "Pause entries"}</button>
          {!confirm ? <button className="danger full-sm" onClick={() => setConfirm(true)}>Flatten and stop</button> : (
            <span className="banner swap full-sm" role="alertdialog" aria-label="Confirm flatten and stop" style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", background: "var(--loss-tint)", borderColor: "#5A2A2E" }}>
              Sell every position at market now and end this run?
              <button onClick={() => setConfirm(false)}>Cancel</button>
              <button className="primary" style={{ background: "var(--fail)", borderColor: "var(--fail)", color: "#0A0B0D" }} disabled={cmd.isPending}
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
      {pending.length > 0 && <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Waiting for the engine: {pending.map((p) => commandText(p.kind)).join(", ")}</p>}
    </section>
  );
}

function Positions({ positions }: { positions: Record<string, { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number }> }) {
  const nav = useNavigate();
  const rows = Object.entries(positions);
  if (!rows.length) return <p className="muted" style={{ fontSize: 14 }}>No open positions. Kaibot is flat by 15:55 every day.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {rows.map(([s, p]) => {
        const pnl = p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null;
        const span = p.target - p.stop || 1;
        const at = (v: number | null) => `${Math.max(0, Math.min(100, (((v ?? p.stop) - p.stop) / span) * 100)).toFixed(1)}%`;
        return (
          <button key={s} type="button" className="posrow" onClick={() => nav(`/trades/${p.trade_id}`)}
            style={{ display: "flex", alignItems: "center", gap: 16, padding: "12px", borderRadius: 16, border: 0, background: "transparent",
              textAlign: "left", height: "auto", fontWeight: 400, width: "100%" }}>
            <StockBadge symbol={s} size={44} />
            <span style={{ flex: "1 1 160px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
              <b style={{ fontSize: 15 }}>{s} <span className="muted" style={{ fontWeight: 400 }}>· {companyName(s)}</span></b>
              <span className="num muted" style={{ fontSize: 13 }}>{p.qty} sh · in at {px(p.fill)}{p.status === "pending" ? " · filling" : ""}</span>
            </span>
            <span className="hide-sm" style={{ flex: "1 1 220px", display: "flex", flexDirection: "column", gap: 6 }}
              aria-label={`Price ${px(p.last)} between stop ${px(p.stop)} and target ${px(p.target)}`}>
              <span className="ladder-row"><span className="entry" style={{ left: at(p.fill) }} /><span className="now" style={{ left: at(p.last) }} /></span>
              <span className="num muted" style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}><span>stop {px(p.stop)}</span><span>target {px(p.target)}</span></span>
            </span>
            <span style={{ flex: "0 0 110px", textAlign: "right", display: "flex", flexDirection: "column", gap: 2 }}>
              <span className="num" style={{ fontWeight: 600 }}>{px(p.last)}</span>
              <span className={`num ${tone(pnl)}`} style={{ fontSize: 13, fontWeight: 600 }}>{pnl === null ? "—" : `${pnl >= 0 ? "▲" : "▼"} ${signedMoney(pnl)}`}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

const RANGES = [["1W", 5], ["1M", 21], ["3M", 63], ["ALL", 0]] as const;

export function Live() {
  const { run, overview, overviewError, retryOverview } = useRunCtx();
  const trades = useQuery({ queryKey: ["trades-all", run.id], queryFn: () => api.trades(run.id, 500, 0) });
  const evidence = useQuery({ queryKey: ["evidence", run.id], queryFn: () => api.evidence(run.id), ...poll(60_000) });
  // S48: chapter boundaries on the pot chart. Optional: if the RPC isn't there yet, the chart simply has no markers.
  const chapters = useQuery({ queryKey: ["chapters", run.id], queryFn: () => api.chapters(run.id), enabled: run.kind === "paper", staleTime: STALE.daily, retry: false });
  const lv = run.level ? LEVELS[run.level] : null;
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("ALL");
  if (overviewError) return <ErrorState what="The run overview" error={overviewError} retry={retryOverview} />;
  if (trades.isError) return <ErrorState what="Trades" error={trades.error} retry={() => trades.refetch()} />;
  if (!overview || trades.isPending) return <Skeleton variant="dashboard" />;

  const daily = overview.daily;
  const c = curve(run.amount_usd, daily);
  const keep = RANGES.find((r) => r[0] === range)![1];
  const shown = keep && c.length > keep ? c.slice(-keep - 1) : c;
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
      {run.kind === "paper" && <TestPhaseBanner />}
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

      <section aria-labelledby="pot-h" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h1 id="pot-h" style={{ margin: 0, fontSize: 15, fontWeight: 500, color: "var(--slate)" }}>{run.kind === "replay" ? "Pot at the end of the replay, after costs" : "Pot, after costs"}</h1>
          <div className="hero">{money(pot)}</div>
          <div className="num" style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 16, fontWeight: 500 }}>
            <span className={tone(pot - run.amount_usd)}>{pot >= run.amount_usd ? "▲" : "▼"} {signedMoney(pot - run.amount_usd)} · {pct(((pot - run.amount_usd) / run.amount_usd) * 100)}</span>
            {live && <span className={tone(live.dayPnl)}>today {live.dayPnl >= 0 ? "▲" : "▼"} {signedMoney(live.dayPnl)}</span>}
            <span className="muted">SPY same start {s.spyPct >= 0 ? "▲" : "▼"} {pct(s.spyPct)}</span>
          </div>
        </div>
        <div className="range" role="group" aria-label="Chart range">
          {RANGES.map(([k]) => <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>{k}</button>)}
        </div>
        <div className="card" style={{ padding: "18px 18px 10px" }}>
          <PotChart key={range} data={shown} start={range === "ALL" ? run.amount_usd : shown[0]?.pot ?? run.amount_usd}
            markers={(chapters.data ?? []).filter((c) => c.version > 0 && c.from).map((c) => ({ session: c.from.slice(0, 10), label: `v${c.version}` }))} />
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {vsSpy >= 0 ? "Ahead of" : "Behind"} SPY by <b className="num" style={{ color: "var(--ink)" }}>{money(Math.abs(vsSpy))}</b> since the start.
          {" "}Reported P&amp;L is the worse of broker fills and the stricter cost model.
        </p>
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
          : <Empty>No trades yet. Kaibot checks for setups every 5 minutes from 09:35 to 15:45 ET; many days have none.</Empty>}
      </section>
    </>
  );
}
