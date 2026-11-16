import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Meter, engineChip, livePot, useRunCtx } from "../components/Shell";
import { PotChart } from "../components/PotChart";
import { api, type CommandKind, type Level, type Overview, type Run, type Trade } from "../lib/api";
import { BAR, LEVELS, curve, stats } from "../lib/model";
import { R, dateEt, day, money, pct, px, signedMoney, timeBoth, timeEt, tone } from "../lib/fmt";
import { commandText, exitText, ruleName } from "../lib/explain";
import { ageS, ageText, etToday, evidenceVerdict, levelPlan } from "../lib/derive";
import { StockCell } from "../components/StockBadge";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { STALE, poll } from "../lib/poll";
import { RuleExperiment } from "../components/Experiment";
import { HomeStatus, TopHoldings, holdingRows } from "../components/Home";
import { type StartState, startState } from "../lib/startstate";
import { useBook } from "../components/useBook";
import { mtmCurve } from "../lib/book";

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
  const beats = useRunCtx()?.overview?.heartbeat;
  return (
    <table className="list" tabIndex={0} aria-label="Trades">
      <thead><tr><th>Stock</th><th>Time (ET)</th><th className="hide-sm">Why it closed</th><th className="r hide-sm">Entry → exit</th><th className="r">P&amp;L</th><th className="r">R</th><th className="hide-sm">Room</th></tr></thead>
      <tbody>
        {rows.map((t) => (
          <tr key={t.id} className="click" tabIndex={0} aria-label={`${t.symbol} trade on ${dateEt(t.entry_ts)}: open details`}
            onClick={() => nav(`/positions/${t.id}`)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nav(`/positions/${t.id}`); } }}>
            <td title={`code: ${t.strategy}`}><StockCell symbol={t.symbol} sub={`${t.qty} sh · ${ruleName(t.strategy)}`} />
              <RuleExperiment code={t.strategy} beats={beats} /></td>
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


/** A new run between "Start paper run" and its first session: the true state, never "waits for the engine" while the
 *  engine's heartbeat is fresh. */
export function StartBanner({ state: s }: { state: StartState }) {
  if (s.kind === "none") return null;
  const what = s.kind !== "scheduled" && s.amount !== null
    ? <><b className="num">{money(s.amount)}</b>{s.level && LEVELS[s.level as Level] ? <> at <b>{LEVELS[s.level as Level].name}</b></> : null}</> : null;
  if (s.kind === "queued_waiting") return (
    <div className="banner warn" role="status">A new run{what ? <> of {what}</> : null} is queued. It waits for the engine: its last heartbeat is more than
      90 seconds old.</div>
  );
  if (s.kind === "queued_online") return (
    <div className="banner" role="status">A new run{what ? <> of {what}</> : null} is queued. The engine is online and picks it up within about 10 seconds.</div>
  );
  const when = s.marketOpen ? "the market is open, so it starts within a minute or two"
    : s.nextOpen ? `starts at the next open, ${dateEt(s.nextOpen)}, ${timeBoth(s.nextOpen)}` : "starts at the next open";
  return (
    <div className="banner" role="status">
      <b>The engine has accepted it.</b> {s.online ? <>Scheduled: {when}.</>
        : <>Scheduled: it starts at the next open, if the engine is running then (its last heartbeat is more than 90 seconds old).</>}
    </div>
  );
}


const RANGES = [["1W", 5, "1W"], ["1M", 21, "1M"], ["3M", 63, "3M"], ["ALL", 0, "All"]] as const;

export function Live() {
  const { run, overview, overviewError, retryOverview, meters, risk } = useRunCtx();
  const trades = useQuery({ queryKey: ["trades-all", run.id], queryFn: () => api.trades(run.id, 500, 0) });
  const evidence = useQuery({ queryKey: ["evidence", run.id], queryFn: () => api.evidence(run.id), ...poll(60_000) });
  // S48: chapter boundaries on the pot chart. Optional: if the RPC isn't there yet, the chart simply has no markers.
  const chapters = useQuery({ queryKey: ["chapters", run.id], queryFn: () => api.chapters(run.id), enabled: run.kind === "paper", staleTime: STALE.daily, retry: false });
  // #130: the pot line marked to market (kb_equity_series) once the ledger has closes; before that, realised results.
  const { series, book: bookQ } = useBook(run);
  const useBookData = bookQ.data;
  const lv = run.level ? LEVELS[run.level] : null;
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("ALL");
  if (overviewError) return <ErrorState what="The run overview" error={overviewError} retry={retryOverview} />;
  if (trades.isError) return <ErrorState what="Trades" error={trades.error} retry={() => trades.refetch()} />;
  if (!overview || trades.isPending) return <Skeleton variant="dashboard" />;

  const daily = overview.daily;
  const marked = (series.data ?? []).filter((p) => p.marked);
  const c = marked.length ? mtmCurve(run.amount_usd, series.data!, daily) : curve(run.amount_usd, daily);
  // Product, 16 Nov 2026: the line ends at the hero figure. Today's live pot (paper, ledger from today) is the last
  // point, marked live; the S&P 500 line holds its last close there.
  const liveNow = run.kind === "paper" ? livePot(run, overview) : null;
  const todayEt = etToday();
  const tail = c.at(-1);
  if (liveNow && overview.ledger && etToday(new Date(overview.ledger.ts)) === todayEt) {
    const point = { session: todayEt, pot: liveNow.pot, spy: tail?.spy ?? run.amount_usd, live: true };
    if (tail?.session === todayEt) c[c.length - 1] = { ...tail, pot: liveNow.pot, live: true };
    else c.push(point);
  }
  const keep = RANGES.find((r) => r[0] === range)![1];
  const shown = keep && c.length > keep ? c.slice(-keep - 1) : c;
  const s = stats(run.amount_usd, daily, trades.data.rows);
  const live = liveNow;
  const chip = engineChip(overview, run);
  const pot = live?.pot ?? c.at(-1)?.pot ?? run.amount_usd;
  const lastDay = [...daily].reverse().find((d) => d.trades > 0);
  const lastRows = lastDay ? trades.data.rows.filter((t) => t.entry_ts.slice(0, 10) === lastDay.session) : [];
  const vsSpy = s.netUsd - (c.at(-1) ? c.at(-1)!.spy - run.amount_usd : 0);
  const beatTs = overview.heartbeat.find((h) => h.process === "engine")?.ts;

  const book = useBookData;
  const rows = holdingRows(live?.positions ?? {}, book);
  const allTime = pot - run.amount_usd;
  const chartStart = range === "ALL" ? run.amount_usd : shown[0]?.pot ?? run.amount_usd;

  return (
    <>
      <HomeStatus run={run} overview={overview} risk={risk} engineLive={chip.live} book={book} />
      <StartBanner state={startState(run, overview)} />

      <div className="home-band">
        <section aria-labelledby="pot-h" className="home-hero">
          <h1 id="pot-h" className="home-label">{run.kind === "replay" ? "Pot at the end of the replay" : "Paper pot"}</h1>
          <div className="hero num">{money(pot)}</div>
          <div className="num home-change">
            <span className={tone(allTime)}>{signedMoney(allTime)} all time</span>
            {live && <span className={tone(live.dayPnl)}>{signedMoney(live.dayPnl)} today</span>}
            <span className="muted desk-only">{vsSpy >= 0 ? "Ahead of" : "Behind"} the S&amp;P 500 by {money(Math.abs(vsSpy))}</span>
          </div>
        </section>
        {lv && meters && run.kind === "paper" && (
          <div className="desk-only meters home-meters" aria-label="Limits">
            <Meter label="Day vs stop" value={`${pct(meters.dayPct)} / −${lv.stop.toFixed(2)}%`} frac={meters.dayPct < 0 ? -meters.dayPct / lv.stop : 0} />
            <Meter label="Run drawdown" value={`${meters.dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={meters.dd / lv.runDd} />
          </div>
        )}
      </div>

      <section aria-label="Pot over time" className="home-chart">
        <PotChart data={shown} start={chartStart} compact
          markers={(chapters.data ?? []).filter((c) => c.version > 0 && c.from).map((c) => ({ session: c.from.slice(0, 10), label: `v${c.version}` }))} />
        <div className="range" role="group" aria-label="Chart range">
          {RANGES.map(([k, , label]) => <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>{label}</button>)}
        </div>
      </section>

      {run.kind === "paper" && <TopHoldings rows={rows} max={book?.positions.length ? null : lv?.maxPositions ?? null} />}
      {run.kind === "replay" && (
        <section className="card" aria-labelledby="last-h">
          <h2 id="last-h">{lastDay ? `Trades on ${day(lastDay.session)}, the last trading day` : "Trades"}</h2>
          {lastRows.length ? <TradeRows rows={lastRows} /> : <Empty>No trades yet.</Empty>}
        </section>
      )}

      <details className="home-details">
        <summary><span>Details</span><span className="muted">vs S&amp;P 500, limits, evidence, rules</span></summary>
        <div className="home-details-body">
          <div className="kv">
            <span>S&amp;P 500 (SPY), same start</span><span /><span className="v num">{s.spyPct >= 0 ? "+" : ""}{pct(s.spyPct)}</span>
            <span>{vsSpy >= 0 ? "Ahead of" : "Behind"} the S&amp;P 500</span><span /><span className="v num">{money(Math.abs(vsSpy))}</span>
          </div>
          {lv && meters && run.kind === "paper" && (
            <div className="phone-only meters meters-home" aria-label="Limits">
              <Meter label="Day vs stop" value={`${pct(meters.dayPct)} / −${lv.stop.toFixed(2)}%`} frac={meters.dayPct < 0 ? -meters.dayPct / lv.stop : 0} />
              <Meter label="Run drawdown" value={`${meters.dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={meters.dd / lv.runDd} />
            </div>
          )}
          <section aria-labelledby="bar-h">
            <h3 id="bar-h">Evidence toward the go-live bar</h3>
            <div className="kv">
              <Progress label="Trades" now={evidence.data?.n ?? s.trades} bar={BAR.trades} />
              <Progress label="Months" now={s.months} bar={BAR.months} fmt={(v) => v.toFixed(1)} />
              <Progress label="t-stat (net)" now={evidence.data?.t ?? s.t} bar={BAR.t} fmt={(v) => v.toFixed(2)} />
              <span>Max drawdown</span><span /><span className="v">{s.maxDdPct.toFixed(1)}% / {lv?.runDd ?? "—"}%</span>
              <span>Hit rate</span><span /><span className="v">{(s.hitRate * 100).toFixed(1)}%</span>
            </div>
            {evidence.data && <p style={{ fontSize: 13, marginBottom: 0 }}>{evidenceVerdict(evidence.data).text}</p>}
          </section>
          {lv && (
            <section aria-labelledby="rules-h">
              <h3 id="rules-h">Rules for {lv.name}</h3>
              <div className="kv">
                <span>Risk per trade</span><span /><span className="v">{lv.risk}% of pot</span>
                <span>Daily stop (flatten and halt)</span><span /><span className="v">−{lv.stop}%</span>
                <span>Largest position</span><span /><span className="v">{lv.pos}%</span>
                <span>Run ends at drawdown</span><span /><span className="v">{lv.runDd}% from peak</span>
              </div>
            </section>
          )}
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Reported P&amp;L is the worse of broker fills and the stricter cost model.
            {run.kind === "paper" && <> Pause or stop the run in <Link to="/settings">Run &amp; settings</Link>.</>}</p>
          {beatTs && <p className="muted" style={{ fontSize: 12, margin: 0 }}>Updated {ageText(ageS(beatTs))}</p>}
        </div>
      </details>
    </>
  );
}
