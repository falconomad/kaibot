// v4 Positions (docs/design/mobile_first_v4.md, "Positions" and "Bottom sheet (position)"): what Kaibot holds now,
// and what it held. Open: the day's positions (the ledger) and the multi-day book (kb_book, folded in from the old
// Holdings page) as rows: name, one plain line, one number. Closed: the run's closed trades. A tap opens the position
// sheet; the full trade page is one more tap. No table scrolls sideways.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { BookChecks } from "../components/Book";
import { ExperimentBadge, experimentsOf } from "../components/Experiment";
import { StatusRow } from "../components/Home";
import { Sheet } from "../components/Sheet";
import { livePot, useRunCtx } from "../components/Shell";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { StockBadge, companyName } from "../components/StockBadge";
import { useBook } from "../components/useBook";
import { onTabKeys, tabProps } from "../lib/a11y";
import { api, type Trade } from "../lib/api";
import { type Book, type BookPosition, holdingView } from "../lib/book";
import { RULES, exitText, ruleName } from "../lib/explain";
import { type Experiment, findExperiment } from "../lib/experiment";
import { R, dateEt, day, money, px, signedMoney, timeEt } from "../lib/fmt";
import { sectorOf } from "../components/Book";

const PAGE = 50;
type IntradayPos = { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number };
const tone = (v: number | null | undefined) => (!v ? "flat" : v > 0 ? "gain" : "loss");
const pctOf = (a: number, b: number) => (b ? `${a >= 0 ? "+" : "−"}${Math.abs((a / b) * 100).toFixed(1)}%` : "");

type Open =
  | { kind: "day"; symbol: string; p: IntradayPos; value: number; gain: number | null }
  | { kind: "book"; symbol: string; p: BookPosition; value: number; gain: number };

export function Positions() {
  const { run, overview } = useRunCtx();
  const [tab, setTab] = useState<"open" | "closed">("open");
  const [page, setPage] = useState(0);
  const [sheet, setSheet] = useState<Open | Trade | null>(null);
  const { book } = useBook(run);
  const closed = useQuery({ queryKey: ["trades", run.id, page], queryFn: () => api.trades(run.id, PAGE, page * PAGE), placeholderData: keepPreviousData,
    enabled: tab === "closed" || run.kind === "replay" });
  const exps = experimentsOf(overview?.heartbeat);
  const b = book.data;
  const live = run.kind === "paper" ? livePot(run, overview) : null;
  const open: Open[] = [
    ...Object.entries(live?.positions ?? {}).map(([symbol, p]): Open => {
      const now = p.last ?? p.fill ?? 0;
      return { kind: "day", symbol, p, value: p.qty * now, gain: p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null };
    }),
    ...(b?.positions ?? []).map((p): Open => { const v = holdingView(p, b!); return { kind: "book", symbol: p.symbol, p, value: v.value, gain: v.gain }; }),
  ].sort((x, y) => y.value - x.value);
  const invested = open.reduce((a, o) => a + o.value, 0);
  const pot = live?.pot ?? null;
  const expHeld = exps.find((e) => e.experiment && open.some((o) => o.kind === "book" && findExperiment(o.p.rule, [e])));
  const showClosed = tab === "closed" || run.kind === "replay";

  return (
    <>
      <section aria-labelledby="pos-h" className="pos-head">
        <h1 id="pos-h" className="page-title">Positions</h1>
        <p className="muted num" style={{ margin: 0 }}>{open.length} open{open.length ? ` · ${money(invested)} invested` : ""}
          {open.length && pot ? ` · ${Math.round((invested / pot) * 100)}% of the pot` : ""}</p>
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
            <StatusRow tone="info" title={expHeld.label} text={<>{ruleName(expHeld.strategy)} is an experiment: it failed the ten-year test</>}>
              <ExperimentInfo e={expHeld} />
            </StatusRow>
          )}
          {b?.session && b.positions.length > 0 && <BookChecks b={b} />}
          {book.isError && <ErrorState compact what="The multi-day book" error={book.error} retry={() => book.refetch()} />}
          {open.length ? (
            <ul className="rows" aria-label="Open now">
              {open.map((o) => (
                <li key={`${o.kind}-${o.symbol}-${o.kind === "book" ? o.p.rule : ""}`}>
                  <button type="button" className="row" aria-haspopup="dialog" onClick={() => setSheet(o)}>
                    <StockBadge symbol={o.symbol} size={40} />
                    <span className="row-mid">
                      <b>{o.symbol}</b>
                      <span className="muted">{o.kind === "day"
                        ? `${o.p.qty} shares · in at ${px(o.p.fill)}${o.p.status === "pending" ? " · filling" : ""}`
                        : `${ruleName(o.p.rule)} · ${o.p.qty} shares${holdingView(o.p, b!).days !== null ? ` · ${holdingView(o.p, b!).days} days` : ""}`}</span>
                      {o.kind === "book" && findExperiment(o.p.rule, exps) && <span className="row-tag">Experiment: failed the ten-year test</span>}
                    </span>
                    <span className="row-end num"><b>{money(o.value)}</b>
                      {o.gain !== null && <span className={tone(o.gain)}>{signedMoney(o.gain)}</span>}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : book.isPending && run.kind === "paper" ? <Skeleton /> : (
            <Empty>Nothing held right now. Day trades are sold by 15:55; multi-day stocks show here once bought at a close.</Empty>
          )}
        </>
      )}

      {showClosed && (closed.isPending ? <Skeleton /> : closed.isError ? <ErrorState compact what="Trades" error={closed.error} retry={() => closed.refetch()} /> : (
        <>
          {closed.data.rows.length ? (
            <ul className="rows" aria-label="Trades">
              {closed.data.rows.map((t) => (
                <li key={t.id}>
                  <button type="button" className="row" aria-haspopup="dialog" onClick={() => setSheet(t)}>
                    <StockBadge symbol={t.symbol} size={40} />
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
          : sheet.kind === "day" ? <DaySheet o={sheet} exps={exps} /> : <BookSheet o={sheet} b={b!} exp={findExperiment(sheet.p.rule, exps)} />)}
      </Sheet>
    </>
  );
}

function ExperimentInfo({ e }: { e: Experiment }) {
  return <><ExperimentBadge e={e} limits /><p className="muted" style={{ marginTop: 12 }}>It trades on paper only, to see how it behaves live; it never trades real money.</p></>;
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

function Figure({ value, gain, base }: { value: number; gain: number | null; base: number }) {
  return <p className="sheet-figure num"><b>{money(value)}</b>{gain !== null && <span className={tone(gain)}> {signedMoney(gain)} · {pctOf(gain, base)}</span>}</p>;
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
      <p className="muted" style={{ fontSize: 14 }}>Kaibot manages each trade itself; the stop and target rest at the broker.</p>
      <Link to={`/positions/${p.trade_id}`} className="btn sheet-cta">Full trade details</Link>
    </>
  );
}

/** A multi-day holding (the former Holdings page's detail). */
function BookSheet({ o, b, exp }: { o: Extract<Open, { kind: "book" }>; b: Book; exp: Experiment | null }) {
  const { p } = o;
  const v = holdingView(p, b);
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>{companyName(o.symbol)} · {p.qty} shares · {ruleName(p.rule)}</p>
      <Figure value={v.value} gain={v.gain} base={p.qty * p.avgCost} />
      {exp && <ExperimentInfo e={exp} />}
      <div className="kv">
        <span>Held</span><span /><span className="v">{v.days !== null ? `${v.days} days` : "—"}{p.opened ? ` · since ${day(p.opened)}` : ""}</span>
        <span>Bought at</span><span /><span className="v">{px(p.avgCost)} a share</span>
        <span>Price at the close</span><span /><span className="v">{px(p.mark)}{p.priced ? "" : " (last known)"}</span>
        <span>Size, of the pot</span><span /><span className="v">{v.share !== null ? `${(v.share * 100).toFixed(1)}%` : "—"}</span>
        <span>Sector</span><span /><span className="v">{sectorOf(p.symbol) ?? "not known"}</span>
        <span>Safety stop at the broker</span><span /><span className={`v ${p.stop ? "" : "loss"}`}>{p.stop
          ? `${px(p.stop.price)} · ${p.stop.state === "resting" ? "in place" : p.stop.state === "re-arming" ? "being placed again" : "missing"}${v.stopBelowPct !== null ? ` · ${v.stopBelowPct.toFixed(1)}% below` : ""}`
          : "none: Kaibot places it again"}</span>
        {p.dividends > 0 && <><span>Dividends</span><span /><span className="v">{money(p.dividends)}</span></>}
      </div>
      {RULES[p.rule] && <p className="muted" style={{ fontSize: 14 }}>The rule {RULES[p.rule].what}. Prices are at the last close.</p>}
    </>
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
      <Link to={`/positions/${t.id}`} className="btn sheet-cta">Full trade details</Link>
    </>
  );
}
