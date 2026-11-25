import { skipToken, useQuery } from "@tanstack/react-query";
import { Suspense, lazy, memo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Meter, engineChip, useRunCtx } from "../components/Shell";
import { PotChart, RANGES, type RangeKey, chartCaption, rangesFor } from "../components/PotChart";
import { api, type Level, type Trade } from "../lib/api";
import { BAR, LEVELS, curve, stats } from "../lib/model";
import { R, dateEt, day, money, pct, px, signedMoney, timeBoth, timeEt, tone } from "../lib/fmt";
import { exitText, ruleName } from "../lib/explain";
import { etToday, evidenceVerdict } from "../lib/derive";
import { StockCell } from "../components/StockBadge";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { STALE, poll } from "../lib/poll";
import { RuleExperiment } from "../components/Experiment";
import { Tick } from "../components/Tick";
import { HomeStatus, NewRunRow, TopHoldings, holdingRows } from "../components/Home";
import { type StartState, startState } from "../lib/startstate";
import { useBook } from "../components/useBook";
import { mtmCurve } from "../lib/book";
import { DailyNoteCard } from "../components/DailyNote";
import { engineDetail } from "../components/Experiment";
import { dailyNote, showNote } from "../lib/engineevents";
import { multiDayFrom } from "../lib/experiment";
import { useMarks } from "../components/Marks";
import { homeLine, potLive, vsSpyText } from "../lib/marks";
import { FreshNote } from "../components/Fresh";
import { bookFreshness, holdingsFreshness, newsFreshness } from "../lib/freshness";
import { nextAction } from "../lib/nextaction";

// #378: the market notices are their own chunk (the first load's budget is nearly full); nothing shows until it loads.
// The market notices and the headline line (migration 0043): one chunk of their own, not first load
const Lines = lazy(() => import("../components/Lines"));

// Memoised with plain values (the formats are fixed functions): a streamed price does not draw the evidence rows again.
const [F0, F1, F2] = [0, 1, 2].map((d) => (v: number) => v.toFixed(d));
const Progress = memo(function Progress({ label, now, bar, fmt = F0 }: { label: string; now: number; bar: number; fmt?: (v: number) => string }) {
  return (
    <>
      <span>{label}</span>
      <div className="prog" aria-hidden="true"><i style={{ width: `${Math.min(100, Math.max(0, (now / bar) * 100))}%` }} /></div>
      <span className="v">{fmt(now)} / {fmt(bar)}</span>
    </>
  );
});

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
  if (s.kind === "queued_selling") return (
    <div className="banner" role="status">A new run{what ? <> of {what}</> : null} is waiting: the old run's sale is not complete yet, buys are blocked.</div>
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



export function Live() {
  const { run, runs, overview, overviewError, retryOverview, meters, risk, pickRun } = useRunCtx();
  const trades = useQuery({ queryKey: ["trades-all", run.id], queryFn: () => api.trades(run.id, 500, 0) });
  const evidence = useQuery({ queryKey: ["evidence", run.id], queryFn: () => api.evidence(run.id), ...poll(60_000) });
  // S48: chapter boundaries on the pot chart. Optional: if the RPC isn't there yet, the chart simply has no markers.
  const chapters = useQuery({ queryKey: ["chapters", run.id], queryFn: () => api.chapters(run.id), enabled: run.kind === "paper", staleTime: STALE.daily, retry: false });
  // #130: the pot line marked to market (kb_equity_series) once the ledger has closes; before that, realised results.
  const { series, book: bookQ } = useBook(run);
  const useBookData = bookQ.data;
  // Stakeholder, 19 Nov 2026: between two closes the book is marked at the paper account's prices (lib/marks.ts),
  // asked for every 10 s while the market is open. The book itself keeps its 5-minute cache.
  const mk = useMarks(run, overview);
  const lv = run.level ? LEVELS[run.level] : null;
  const [rangePick, setRange] = useState<RangeKey>("ALL");
  // #311: the daily note's event comes with kb_health's events. Home does not fetch them for it (the first load stays
  // lean); it reads them when Engine or Activity already did, and the heartbeat's copy otherwise.
  const healthCache = useQuery({ queryKey: ["health"], queryFn: api.health, enabled: false });
  // #604: the market calendar for the "Next" line, from the market notices' answer when that chunk has fetched it
  // (kb_market_notices `sessions`); never asked for here.
  const calendar = useQuery<{ sessions?: string[] } | null>({ queryKey: ["market-notices"], queryFn: skipToken });
  if (overviewError) return <ErrorState what="The run overview" error={overviewError} retry={retryOverview} />;
  if (trades.isError) return <ErrorState what="Trades" error={trades.error} retry={() => trades.refetch()} />;
  if (!overview || trades.isPending) return <Skeleton variant="dashboard" />;

  const daily = overview.daily;
  const marked = (series.data ?? []).filter((p) => p.marked);
  const closes = marked.length ? mtmCurve(run.amount_usd, series.data!, daily) : curve(run.amount_usd, daily);
  // Product, 16 Nov 2026: the line ends at the hero figure. Stakeholder, 18 Nov 2026: the hero and the line are the
  // marked book (kb_book close_equity, the same series), never the sum of closed trades. 19 Nov 2026: live prices when
  // the account holds exactly the book, else the engine's minute ledger (potLive); the earlier points stay the booked
  // closes. 24 Nov 2026: the hero, "Ahead of / Behind the S&P 500" and the chart's legend all read ONE last point
  // (lib/marks.homeLine). While the pot is live it is today's point, and its S&P 500 value moves with SPY now (the
  // last close's SPY value x SPY now / SPY's previous close, from the same /api/marks answer, at most 30 s old). With no
  // fresh SPY price it holds the last close, as before, and the text says "at last close": never a silent mix.
  const liveNow = run.kind === "paper" ? potLive(run, overview, useBookData, series.data, mk.marks) : null;
  const { curve: c, end, spyAt } = homeLine(closes, liveNow, mk.marks?.spy, etToday(), run.amount_usd);
  // Stakeholder, 19 Nov 2026: with few sessions every range drew the same line; only ranges that show less than All
  const ranges = rangesFor(c.length);
  const range = ranges.includes(rangePick) ? rangePick : "ALL";
  const keep = RANGES.find((r) => r[0] === range)![1];
  const shown = keep && c.length > keep ? c.slice(-keep - 1) : c;
  const s = stats(run.amount_usd, daily, trades.data.rows);
  const live = liveNow;
  const chip = engineChip(overview, run, healthCache.data?.events);
  const pot = end?.pot ?? live?.pot ?? run.amount_usd;
  const lastDay = [...daily].reverse().find((d) => d.trades > 0);
  const lastRows = lastDay ? trades.data.rows.filter((t) => t.entry_ts.slice(0, 10) === lastDay.session) : [];
  // the pot's gain (marked) against SPY's from the same start, both read from the line's last point
  const vsSpy = (live ? pot - run.amount_usd : s.netUsd) - (end ? end.spy - run.amount_usd : 0);
  const spyPct = end ? ((end.spy - run.amount_usd) / run.amount_usd) * 100 : s.spyPct;
  // Stakeholder, 16 Nov 2026: while a newer run is scheduled, an ended run's pot must not read as the current one.
  const ended = run.kind === "paper" && !["scheduled", "running", "paused"].includes(run.status);
  const newer = ended ? (runs ?? []).filter((r) => r.kind === "paper" && r.id !== run.id && r.created_at >= run.created_at
    && ["scheduled", "running", "paused"].includes(r.status)).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null : null;

  const book = useBookData;
  const rows = holdingRows(live?.positions ?? {}, book, live?.live ?? live?.engine);
  const noteNow = run.kind === "paper" && !ended
    ? dailyNote(multiDayFrom(engineDetail(overview.heartbeat)), (healthCache.data?.events ?? []).filter((e) => e.run_id === null || e.run_id === run.id)) : null;
  const note = noteNow && showNote(noteNow) ? noteNow : null;
  const allTime = pot - run.amount_usd;
  const src = live?.fresh?.kind ?? "none";
  // #604: one freshness rule for the pot, the holdings and the headlines (lib/freshness.ts); words only when not live
  const potFresh = run.kind === "paper" && !ended ? bookFreshness(live?.fresh, mk.watching, mk.pricesAt) : null;
  const rowsFresh = run.kind === "paper" && !ended ? holdingsFreshness(live, book?.session, mk.watching, mk.pricesAt) : null;
  const beat = overview.heartbeat.find((h) => h.process === "engine");
  const newsFresh = newsFreshness(beat?.detail, !chip.asleep);
  const next = nextAction({ run, detail: beat?.detail, engine: chip, held: book?.positions.length ?? 0,
    events: (healthCache.data?.events ?? []).filter((e) => e.run_id === null || e.run_id === run.id), calendar: calendar.data?.sessions });
  const chartStart = range === "ALL" ? run.amount_usd : shown[0]?.pot ?? run.amount_usd;
  // fewer than 5 closes: say why the line barely moves (the share of the pot in stocks at the last close)
  const caption = run.kind === "paper" ? chartCaption(c.filter((p) => !p.live).length,
    book?.session && book.invested !== null && book.equity ? book.invested / book.equity : null) : null;

  return (
    <>
      {newer && <NewRunRow next={newer} overview={overview} onShow={pickRun ? () => pickRun(newer.id) : undefined} />}
      <HomeStatus run={run} overview={overview} risk={risk} engineLive={chip.live} engine={chip} book={book} events={healthCache.data?.events} />
      <StartBanner state={startState(run, overview)} />
      {run.kind === "paper" && <Suspense fallback={null}><Lines headlines={!ended} fresh={newsFresh} /></Suspense>}

      <div className="home-band">
        <section aria-labelledby="pot-h" className="home-hero">
          <h1 id="pot-h" className="home-label">{run.kind === "replay" ? "Pot at the end of the replay" : newer?.status === "scheduled" ? "Last run (ended)"
            : ended ? "Paper pot · run ended" : "Paper pot"}</h1>
          {/* `from`: a change of source (live prices, the engine's row, a close) is not a move of the market: no tint */}
          <div className="hero num"><Tick value={pot} from={src}>{money(pot)}</Tick></div>
          <div className="num home-change">
            <Tick value={allTime} from={src} className={tone(allTime)}>{signedMoney(allTime)} all time</Tick>
            {live && <Tick value={live.dayPnl} from={src} className={tone(live.dayPnl)}>{signedMoney(live.dayPnl)} {live.dayLabel}</Tick>}
            <span className="muted desk-only">{vsSpyText(vsSpy, spyAt)}</span>
          </div>
          <FreshNote f={potFresh} what="Pot" />
          {next && <p className="muted home-next">{next}</p>}
        </section>
        {lv && meters && run.kind === "paper" && (
          <div className="desk-only meters home-meters" aria-label="Limits">
            <Meter label="Day vs stop" value={`${pct(meters.dayPct)} / −${lv.stop.toFixed(2)}%`} frac={meters.dayPct < 0 ? -meters.dayPct / lv.stop : 0} />
            <Meter label="Run drawdown" value={`${meters.dd.toFixed(1)} / ${lv.runDd.toFixed(1)}%`} frac={meters.dd / lv.runDd} />
          </div>
        )}
      </div>

      <section aria-label="Pot over time" className="home-chart">
        <PotChart data={shown} start={chartStart} compact caption={caption} quiet={!!live?.live}
          base={range === "ALL" || !shown.length ? { pot: run.amount_usd, spy: run.amount_usd } : { pot: shown[0].pot, spy: shown[0].spy }}
          markers={(chapters.data ?? []).filter((c) => c.version > 0 && c.from).map((c) => ({ session: c.from.slice(0, 10), label: `v${c.version}` }))} />
        {ranges.length > 1 && <div className="range" role="group" aria-label="Chart range">
          {RANGES.filter(([k]) => ranges.includes(k)).map(([k, , label]) => <button key={k} type="button" aria-pressed={range === k} onClick={() => setRange(k)}>{label}</button>)}
        </div>}
      </section>

      {run.kind === "paper" && <TopHoldings fresh={rowsFresh} from={live?.live ? "live" : live?.engine ? "engine" : "close"} rows={rows} max={book?.positions.length ? null : lv?.maxPositions ?? null} waiting={mk.waiting} />}
      {note && <DailyNoteCard note={note} />}
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
            <span>S&amp;P 500 (SPY), same start{spyAt === "close" ? " (last close)" : ""}</span><span /><span className="v num">{spyPct >= 0 ? "+" : ""}{pct(spyPct)}</span>
            <span>{vsSpyText(vsSpy, spyAt, false)}</span><span /><span className="v num">{money(Math.abs(vsSpy))}</span>
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
              <Progress label="Months" now={s.months} bar={BAR.months} fmt={F1} />
              <Progress label="t-stat (net)" now={evidence.data?.t ?? s.t} bar={BAR.t} fmt={F2} />
              <span>Max drawdown</span><span /><span className="v">{s.maxDdPct.toFixed(1)}% / {lv?.runDd ?? "—"}%</span>
              <span>Hit rate</span><span /><span className="v">{(s.hitRate * 100).toFixed(1)}%</span>
            </div>
            {evidence.data && <p style={{ fontSize: 14, marginBottom: 0 }}>{evidenceVerdict(evidence.data).text}</p>}
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
          <p className="muted" style={{ fontSize: 14, margin: 0 }}>Reported P&amp;L is the worse of broker fills and the stricter cost model.
            {run.kind === "paper" && <> Pause or stop the run in <Link to="/settings">Run &amp; settings</Link>.</>}</p>
        </div>
      </details>
    </>
  );
}
