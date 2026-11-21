// v4 Positions (docs/design/mobile_first_v4.md, "Positions" and "Bottom sheet (position)"): what Kaibot holds now,
// and what it held. Open: the day's positions (the ledger) and the multi-day book (kb_book, folded in from the old
// Holdings page) as rows: name, one plain line, one number. Closed: the run's closed trades. A tap opens the position
// sheet; the full trade page is one more tap. No table scrolls sideways.
// Stakeholder, 19 Nov 2026: while the market is open and the paper account holds exactly the book, a multi-day holding's
// value, gain and change today are at the account's price now (lib/marks.ts); otherwise at its close mark, and the
// line under the count says which.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { BookChecks } from "../components/Book";
import { ExperimentSheetBody, engineDetail, experimentsOf } from "../components/Experiment";
import { StatusRow } from "../components/Home";
import { Tick } from "../components/Tick";
import { Sheet } from "../components/Sheet";
import { useRunCtx } from "../components/Shell";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { StockBadge, companyName } from "../components/StockBadge";
import { StockSummary } from "../components/StockSummary";
import { useBook } from "../components/useBook";
import { onTabKeys, tabProps } from "../lib/a11y";
import { api, type Trade } from "../lib/api";
import { type Book, type BookPosition, type LiveMark, holdingView } from "../lib/book";
import { RULES, exitText, ruleName } from "../lib/explain";
import { type Experiment, experimentView, findExperiment } from "../lib/experiment";
import { R, dateEt, day, money, pct, px, signedMoney, timeEt } from "../lib/fmt";
import { sectorOf } from "../components/Book";
import { NAME } from "../brand/brand";
import { type Plan, type Sold, STATE_TEXT, bookLine, buyText, lineText, parsePlan, parseSold, reasonText, rowTag, sectorText, sellWhen } from "../lib/multiday";
import { poll } from "../lib/poll";
import { etToday } from "../lib/derive";
import { type Unfilled, exitKinds, unfilledFrom, unfilledLine } from "../lib/engineevents";
import { exitOf } from "../lib/exits";
import { multiDayFrom } from "../lib/experiment";
import { DayChange, FreshLine, useMarks } from "../components/Marks";
import { type BuyState, buyState, buyStateText, carriesFrom, pastBuys } from "../lib/carry";
import { markOf, potLive } from "../lib/marks";

const PAGE = 50;
type IntradayPos = { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number };
const tone = (v: number | null | undefined) => (!v ? "flat" : v > 0 ? "gain" : "loss");
const pctOf = (a: number, b: number) => (b ? `${a >= 0 ? "+" : "−"}${Math.abs((a / b) * 100).toFixed(1)}%` : "");

/** `rank`: the value at the booked close, so rows keep their order while prices move. */
type Open =
  | { kind: "day"; symbol: string; p: IntradayPos; value: number; gain: number | null; rank: number }
  | { kind: "book"; symbol: string; p: BookPosition; value: number; gain: number; rank: number; day: number | null };

export function Positions() {
  const { run, overview } = useRunCtx();
  const [tab, setTab] = useState<"open" | "closed">("open");
  const [page, setPage] = useState(0);
  const [sheet, setSheet] = useState<Open | Trade | null>(null);
  const { book } = useBook(run);
  const mk = useMarks(run, overview);
  const closed = useQuery({ queryKey: ["trades", run.id, page], queryFn: () => api.trades(run.id, PAGE, page * PAGE), placeholderData: keepPreviousData,
    enabled: tab === "closed" || run.kind === "replay" });
  const exps = experimentsOf(overview?.heartbeat);
  const b = book.data;
  // #229 (PR #247): the next close's plan, the book's stop line, and the sold holdings. Absent until 0020 and the
  // engine code are live: then nothing new shows.
  const paper = run.kind === "paper";
  const planQ = useQuery({ queryKey: ["md-plan", run.id], queryFn: () => api.multidayPlan(run.id), enabled: paper, retry: false, ...poll(60_000) });
  const plans: Plan[] = parsePlan(planQ.data);
  const soldQ = useQuery({ queryKey: ["md-sold", run.id], queryFn: () => api.bookClosed(run.id), enabled: paper && tab === "closed", retry: false });
  const sold: Sold[] = parseSold(soldQ.data);
  // #333: orders the venue let expire at the close (kb_health's events; the same query as Activity's and Engine's)
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, enabled: paper, retry: false, ...poll(60_000) });
  const line = bookLine((engineDetail(overview?.heartbeat) as { multi_day?: unknown } | null)?.multi_day, run.id);
  // Only a plan whose fill session is today or later: an old plan is history, not "tonight".
  const today = etToday();
  const tonight = plans.filter((pl) => !pl.fillsAt || pl.fillsAt >= today).flatMap((pl) => pl.orders.map((o) => ({ ...o, rule: pl.rule, fillsAt: pl.fillsAt })));
  const current = plans.filter((pl) => !pl.fillsAt || pl.fillsAt >= today);
  const unfilled = unfilledFrom(health.data?.events, run.id).filter((u) => u.session === today);
  const matchU = (rule: string, symbol: string, at: string | null) => unfilled.find((u) => u.rule === rule && u.symbol === symbol && (!at || u.session === at)) ?? null;
  // #366: a decision's buys still short after their fill session are carried at the next sessions' closes until the
  // next decision (multi_day_carry events, kb_health); each planned buy says where it stands (lib/carry.ts)
  const carries = carriesFrom(health.data?.events, run.id);
  const earlier = plans.flatMap((pl) => (pastBuys(pl, carries, b, today) ?? []).map(({ o, s }) => ({ ...o, rule: pl.rule, fillsAt: pl.fillsAt, carry: s })));
  const withCarry = tonight.map((o) => {
    const pl = current.find((x) => x.rule === o.rule);
    return pl && (o.action === "buy" || o.action === "resize") ? { ...o, carry: buyState(pl, o, carries, b, today) } : o;
  });
  const unmatched = unfilled.filter((u) => ![...tonight, ...earlier].some((o) => o.rule === u.rule && o.symbol === u.symbol && (!o.fillsAt || o.fillsAt === u.session)));
  const exits = exitKinds(multiDayFrom(engineDetail(overview?.heartbeat)));
  const live = run.kind === "paper" ? potLive(run, overview, b, undefined, mk.marks) : null;
  const open: Open[] = [
    ...Object.entries(live?.positions ?? {}).map(([symbol, p]): Open => {
      const now = p.last ?? p.fill ?? 0;
      return { kind: "day", symbol, p, value: p.qty * now, rank: p.qty * now, gain: p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null };
    }),
    ...(b?.positions ?? []).map((p): Open => {
      const v = holdingView(p, b!, markOf(live?.live ?? live?.engine, p.symbol));
      return { kind: "book", symbol: p.symbol, p, value: v.value, gain: v.gain, day: v.dayPct, rank: p.qty * p.mark };
    }),
  ].sort((x, y) => y.rank - x.rank);
  // the rows' prices: live, else the engine's minute row when it lists exactly the book (#384), else the close marks
  const fresh = live?.live || live?.engine ? live.fresh : b?.session && b.positions.length ? { kind: "close" as const, session: b.session } : null;
  // for Tick: a change of source (live prices, the engine's row, close marks) replaces a figure without the tint of a move
  const from = live?.live ? "live" : live?.engine ? "engine" : "close";
  const invested = open.reduce((a, o) => a + o.value, 0);
  const pot = live?.pot ?? null;
  const expHeld = exps.find((e) => e.experiment && open.some((o) => o.kind === "book" && findExperiment(o.p.rule, [e])));
  const showClosed = tab === "closed" || run.kind === "replay";
  const countKnown = run.kind !== "paper" || book.isSuccess || open.length > 0;

  return (
    <>
      <section aria-labelledby="pos-h" className="pos-head">
        <h1 id="pos-h" className="page-title">Positions</h1>
        {/* #359: no count while the book loads or failed (a "0 open" there reads as data); a space keeps the line's height */}
        <p className="muted num" style={{ margin: 0 }}>{countKnown ? <>{open.length} open{open.length ? <> · <Tick value={invested} from={from}>{money(invested)}</Tick> invested</> : ""}
          {open.length && pot ? ` · ${Math.round((invested / pot) * 100)}% of the pot` : ""}</> : "\u00a0"}</p>
        <FreshLine fresh={fresh} />
        {line && <p className={`muted num ${line.ended ? "loss" : ""}`} style={{ margin: 0 }} title={`peak ${money(line.peak)}, now ${money(line.equity)}`}>Multi-day book: {lineText(line)}</p>}
        {run.kind === "paper" && (
          <div className="seg" role="tablist" aria-label="Show" onKeyDown={onTabKeys}>
            <button {...tabProps(tab === "open")} onClick={() => setTab("open")}>Open</button>
            <button {...tabProps(tab === "closed")} onClick={() => setTab("closed")}>Closed</button>
          </div>
        )}
      </section>

      {!showClosed && (
        <>
          {expHeld && (
            <StatusRow tone="exp" title={ruleName(expHeld.strategy)} text={<>{ruleName(expHeld.strategy)} is an experiment: it failed the ten-year test</>}>
              <ExperimentSheetBody e={expHeld} />
            </StatusRow>
          )}
          {b?.session && b.positions.length > 0 && <BookChecks b={b} exits={exits} />}
          {book.isError && <ErrorState compact what="The multi-day book" error={book.error} retry={() => book.refetch()} />}
          {open.length ? (
            <ul className="rows" aria-label="Open now">
              {open.map((o) => (
                <li key={`${o.kind}-${o.symbol}-${o.kind === "book" ? o.p.rule : ""}`}>
                  <button type="button" className="row" aria-haspopup="dialog" onClick={() => setSheet(o)}>
                    <StockBadge symbol={o.symbol} size={44} />
                    <span className="row-mid">
                      <b>{o.symbol}</b>
                      <span className="muted">{o.kind === "day"
                        ? `${o.p.qty} shares · in at ${px(o.p.fill)}${o.p.status === "pending" ? " · filling" : ""}`
                        : `${ruleName(o.p.rule)} · ${o.p.qty} shares${holdingView(o.p, b!).days !== null ? ` · ${holdingView(o.p, b!).days} days` : ""}`}</span>
                      {o.kind === "book" && (() => {
                        // one line of tags: the plan's tag joins the experiment's, so it adds no height when it arrives
                        const t = rowTag(current, o.p.rule, o.symbol), x = findExperiment(o.p.rule, exps);
                        return x || t ? <span className="row-tags">{x && <span className="row-tag">Experiment: failed the ten-year test</span>}{t && <span className="row-plan">{t}</span>}</span> : null;
                      })()}
                    </span>
                    <span className="row-end num"><b><Tick value={o.value} from={from}>{money(o.value)}</Tick></b>
                      {o.gain !== null && <Tick value={o.gain} from={from} className={tone(o.gain)}>{signedMoney(o.gain)}</Tick>}
                      {o.kind === "book" && <DayChange pct={o.day} waiting={mk.waiting} />}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : book.isPending && run.kind === "paper" ? <Skeleton /> : book.isError ? null : (
            <Empty>Nothing held right now. Day trades are sold by 15:55; multi-day stocks show here once bought at a close.</Empty>
          )}
          {/* below the list: the plan arrives after the rows, and must not push them down */}
          {(tonight.length > 0 || unmatched.length > 0) && <NextClose orders={withCarry.map((o) => ({ ...o, unfilled: matchU(o.rule, o.symbol, o.fillsAt) }))}
            extra={unmatched} today={today} />}
          {earlier.length > 0 && <NextClose orders={earlier} past />}
        </>
      )}

      {showClosed && (closed.isPending ? <Skeleton /> : closed.isError ? <ErrorState compact what="Trades" error={closed.error} retry={() => closed.refetch()} /> : (
        <>
          {sold.length > 0 && <SoldList sold={sold} />}
          {closed.data.rows.length ? (
            <ul className="rows" aria-label="Trades">
              {closed.data.rows.map((t) => (
                <li key={t.id}>
                  <button type="button" className="row" aria-haspopup="dialog" onClick={() => setSheet(t)}>
                    <StockBadge symbol={t.symbol} size={44} />
                    <span className="row-mid"><b>{t.symbol}</b>
                      <span className="muted">{ruleName(t.strategy)} · {t.exit_ts ? exitText(t.exit_reason) : "open"} · {dateEt(t.entry_ts)}</span>
                      {findExperiment(t.strategy, exps) && <span className="row-tag">Experiment: failed the ten-year test</span>}</span>
                    <span className="row-end num"><b className={tone(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</b><span className="muted">{R(t.pnl_usd, t.risk_usd)}</span></span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <Empty>No trades in this run yet. Each closed or open trade appears here with the reason it was made and how it closed.</Empty>}
          <nav aria-label="Pages" className="pager">
            <button disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
            <span className="num muted" aria-live="polite">{closed.data.total} total · page {page + 1} of {Math.max(1, Math.ceil(closed.data.total / PAGE))}</span>
            <button disabled={(page + 1) * PAGE >= closed.data.total} onClick={() => setPage(page + 1)}>Older</button>
          </nav>
        </>
      ))}

      <Sheet open={sheet !== null} onClose={() => setSheet(null)} title={sheet?.symbol ?? ""}>
        {sheet && ("id" in sheet ? <ClosedSheet t={sheet} exp={findExperiment(sheet.strategy, exps)} />
          : sheet.kind === "day" ? <DaySheet o={sheet} exps={exps} />
          : <BookSheet o={sheet} b={b!} exp={findExperiment(sheet.p.rule, exps)} exits={exits} live={markOf(live?.live ?? live?.engine, sheet.symbol)} />)}
      </Sheet>
    </>
  );
}

function ExperimentInfo({ e }: { e: Experiment }) {
  const v = experimentView(e);
  return <p style={{ margin: "0 0 12px" }}><span className="tag info exp-pill">{v.text}</span>{(v.cap || v.stop) && <span className="muted" style={{ display: "block", fontSize: 14, marginTop: 6 }}>{[v.cap, v.stop].filter(Boolean).join(" · ")}</span>}</p>;
}

function Bar({ stop, now, target }: { stop: number; now: number; target: number }) {
  const at = Math.max(0, Math.min(1, (now - stop) / (target - stop || 1)));
  return (
    <div className="sheet-bar">
      <span className="pbar wide" role="img" aria-label={`Price ${px(now)} between stop ${px(stop)} and target ${px(target)}`}><i style={{ left: `${at * 100}%` }} /></span>
      <div className="sheet-bar-ends num"><span>Stop {px(stop)}</span><span>Now {px(now)}</span><span>Target {px(target)}</span></div>
    </div>
  );
}

function Figure({ value, gain, base, from }: { value: number; gain: number | null; base: number; from?: string }) {
  return <p className="sheet-figure num"><b><Tick value={value} from={from}>{money(value)}</Tick></b>{gain !== null && <> <Tick value={gain} from={from} className={tone(gain)}>{signedMoney(gain)} · {pctOf(gain, base)}</Tick></>}</p>;
}

/** A day position: where the price is between stop and target, what it costs if the stop is hit, the rule. */
function DaySheet({ o, exps }: { o: Extract<Open, { kind: "day" }>; exps: Experiment[] }) {
  const { p } = o;
  const t = useQuery({ queryKey: ["trade", String(p.trade_id)], queryFn: () => api.trade(p.trade_id) });
  const rule = t.data?.trade.strategy;
  const now = p.last ?? p.fill ?? p.stop;
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{companyName(o.symbol)} · {p.qty} shares</p>
      <Figure value={o.value} gain={o.gain} base={(p.fill ?? now) * p.qty} />
      <Bar stop={p.stop} now={now} target={p.target} />
      {rule && findExperiment(rule, exps) && <ExperimentInfo e={findExperiment(rule, exps)!} />}
      <div className="kv">
        <span>Bought</span><span /><span className="v">{t.data ? `${dateEt(t.data.trade.entry_ts)} ${timeEt(t.data.trade.entry_ts)} ET` : ""} at {px(p.fill)}</span>
        {rule && <><span>Rule</span><span /><span className="v">{ruleName(rule)}</span></>}
        {rule && RULES[rule] && <><span>Why</span><span /><span className="v">It {RULES[rule].what}</span></>}
        {p.fill !== null && <><span>If it hits the stop</span><span /><span className="v loss">{signedMoney((p.stop - p.fill) * p.qty)}</span></>}
      </div>
      <p className="muted" style={{ fontSize: 15 }}>{NAME} manages each trade itself; the stop and target rest at the broker.</p>
      <StockSummary symbol={o.symbol} />
      <Link to={`/positions/${p.trade_id}`} className="btn sheet-cta">Full trade details</Link>
    </>
  );
}

/** A multi-day holding (the former Holdings page's detail). With `live` (the paper account's price now, lib/marks.ts)
 *  every figure is at that price and the day's change is shown; without it, at the close mark. */
export function BookSheet({ o, b, exp, exits, live }: { o: Pick<Extract<Open, { kind: "book" }>, "symbol" | "p">; b: Book; exp: Experiment | null;
  exits?: Record<string, string>; live?: LiveMark | null }) {
  const { p } = o;
  const v = holdingView(p, b, live);
  // #359: a rule that sells by its target list has no stop by design (lib/exits.exitOf, as the book strip says), so a
  // missing stop there is not a problem and nothing is placed again.
  const byList = !p.stop && exitOf(p, exits) === "target_list";
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{companyName(o.symbol)} · {p.qty} shares · {ruleName(p.rule)}</p>
      <Figure value={v.value} gain={v.gain} base={p.qty * p.avgCost} from={live ? "live" : "close"} />
      {exp && <ExperimentInfo e={exp} />}
      <div className="kv">
        <span>Held</span><span /><span className="v">{v.days !== null ? `${v.days} days` : "—"}{p.opened ? ` · since ${day(p.opened)}` : ""}</span>
        <span>Bought at</span><span /><span className="v">{px(p.avgCost)} a share</span>
        {live ? <><span>Price now</span><span /><span className="v num"><Tick value={v.price}>{px(v.price)}</Tick></span>
          {v.day !== null && v.dayPct !== null && <><span>Today</span><span /><span className={`v num ${tone(v.day)}`}><Tick value={v.day}>{signedMoney(v.day)} · {pct(v.dayPct)}</Tick></span></>}</>
          : <><span>Price at the close</span><span /><span className="v">{px(p.mark)}{p.priced ? "" : " (last known)"}</span></>}
        <span>Size, of the pot</span><span /><span className="v">{v.share !== null ? `${(v.share * 100).toFixed(1)}%` : "—"}</span>
        <span>Sector</span><span /><span className="v">{sectorText(p.sector) ?? sectorOf(p.symbol) ?? "not known"}</span>
        <span>Safety stop at the broker</span><span /><span className={`v ${p.stop || byList ? "" : "loss"}`}>{p.stop
          ? `${px(p.stop.price)} · ${p.stop.state === "resting" ? "in place" : p.stop.state === "re-arming" ? "being placed again" : "missing"}${v.stopBelowPct !== null ? ` · ${v.stopBelowPct.toFixed(1)}% below` : ""}`
          : byList ? "none by design: the rule sells by its list at a close" : `none: ${NAME} places it again`}</span>
        {p.dividends > 0 && <><span>Dividends</span><span /><span className="v">{money(p.dividends)}</span></>}
      </div>
      {(() => {
        const rs = sellWhen(p.sellWhen);
        return rs.length > 0 && (
          <>
            <h3 className="sheet-sub">Sells when</h3>
            <ul className="sell-when">{rs.map((r) => <li key={r.code} className={r.met ? "met" : ""}>{r.met && <b>Met: sells at the next close. </b>}{r.text}</li>)}</ul>
          </>
        );
      })()}
      {RULES[p.rule] && <p className="muted" style={{ fontSize: 15 }}>The rule {RULES[p.rule].what}. {live ? "Prices are live, from the paper account." : "Prices are at the last close."}</p>}
      <StockSummary symbol={o.symbol} />
    </>
  );
}

/** #229 item 2: what the multi-day rule sends at the next close, and where each order stands. */
function NextClose({ orders, extra = [], today, past = false }: { orders: (ReturnType<typeof parsePlan>[number]["orders"][number] & { rule: string; fillsAt: string | null; unfilled?: Unfilled | null; carry?: BuyState })[];
  /** #333: today's orders not filled at the close that the plan no longer lists */ extra?: Unfilled[]; today?: string;
  /** #366: the buys of a decision whose fill session is over (carried until the next decision) */ past?: boolean }) {
  const at = orders.find((o) => o.fillsAt)?.fillsAt ?? (extra.length ? today ?? null : null);
  const id = past ? "nc-past-h" : "nc-h";
  return (
    <section className="card next-close" aria-labelledby={id}>
      <h2 id={id} className="section-h" style={{ margin: 0 }}>{past ? `Planned buys for the close of ${at ? day(at) : "the last decision"}` : `At the ${at ? `close of ${day(at)}` : "next close"}`}</h2>
      <ul className="nc-list">{orders.map((o) => {
        const d = o.sharesTarget - o.sharesNow;
        const what = o.sharesTarget === 0 ? `Sell all ${o.sharesNow}` : d > 0 ? `${o.sharesNow ? "Add" : "Buy"} ${d}` : `Sell ${-d}`;
        const plan = <>{what} share{Math.abs(d) === 1 && o.sharesTarget !== 0 ? "" : "s"}{o.why ? ` · ${d < 0 || o.sharesTarget === 0 ? reasonText(o.why) : buyText(o.why)}` : ""}</>;
        // #366: once the fill session is over a buy says where it stands: filled, carried, waiting or skipped (and why)
        const c = o.carry && o.carry.kind !== "order" ? buyStateText(o.carry) : null;
        return (
          <li key={`${o.rule}-${o.symbol}`}>
            <StockBadge symbol={o.symbol} size={40} />
            <span className="nc-mid"><b>{o.symbol}</b><span className="muted">{c ? <>{plan}{c.line ? <> · {c.line}</> : null}</>
              : o.unfilled ? unfilledLine(o.unfilled) : plan}</span></span>
            {c ? <span className={`nc-state ${c.tone}`}>{c.tag}</span>
              : o.unfilled ? <span className="nc-state canceled">not filled</span>
              : <span className={`nc-state ${o.state}`}>{STATE_TEXT[o.state]}{o.state === "working" && o.sharesNow > 0 && o.sharesTarget > o.sharesNow ? ` · ${o.sharesNow} of ${o.sharesTarget} held` : ""}</span>}
          </li>
        );
      })}{extra.map((u) => (
        <li key={`u-${u.rule}-${u.symbol}-${u.ts}`}>
          <StockBadge symbol={u.symbol} size={40} />
          <span className="nc-mid"><b>{u.symbol}</b><span className="muted">{unfilledLine(u)}</span></span>
          <span className="nc-state canceled">not filled</span>
        </li>
      ))}</ul>
    </section>
  );
}

/** #229 item 4: the multi-day book's sold holdings, newest first (the Sold tab of the boards, under Closed). */
function SoldList({ sold }: { sold: Sold[] }) {
  return (
    <ul className="rows" aria-label="Sold from the multi-day book">
      <li><h2 className="day-head">Sold from the multi-day book</h2></li>
      {sold.map((x) => (
        <li key={`${x.rule}-${x.symbol}-${x.closed}`}><div className="row">
          <StockBadge symbol={x.symbol} size={44} />
          <span className="row-mid"><b>{x.symbol}</b>
            <span className="muted">{reasonText(x.reason)} · {day(x.closed)}{x.days !== null ? ` · held ${x.days} days` : ""}</span></span>
          <span className="row-end num"><b className={tone(x.pnl)}>{x.pnl === null ? "—" : signedMoney(x.pnl)}</b><span className="muted">{x.qty} sh</span></span>
        </div></li>
      ))}
    </ul>
  );
}

function ClosedSheet({ t, exp }: { t: Trade; exp: Experiment | null }) {
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{companyName(t.symbol)} · {t.qty} shares · {ruleName(t.strategy)}</p>
      <p className="sheet-figure num"><b className={tone(t.pnl_usd)}>{signedMoney(t.pnl_usd)}</b> <span className="muted">{R(t.pnl_usd, t.risk_usd)}</span></p>
      {exp && <ExperimentInfo e={exp} />}
      <div className="kv">
        <span>In</span><span /><span className="v">{dateEt(t.entry_ts)} {timeEt(t.entry_ts)} ET at {px(t.entry_px)}</span>
        <span>Out</span><span /><span className="v">{t.exit_ts ? `${timeEt(t.exit_ts)} ET at ${px(t.exit_px)}` : "still open"}</span>
        <span>Why it closed</span><span /><span className="v">{exitText(t.exit_reason)}</span>
        {t.verdict && <><span>Agent room</span><span /><span className="v">{t.verdict}</span></>}
      </div>
      <StockSummary symbol={t.symbol} />
      <Link to={`/positions/${t.id}`} className="btn sheet-cta">Full trade details</Link>
    </>
  );
}
