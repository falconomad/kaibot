// v4 Home (docs/design/mobile_first_v4.md, "Home" and mockups/v4/home.png): status rows that open sheets, the paper
// pot, top holdings and a Details section. One thing per screen: the pot; the facts above it stay visible (a halt, the
// engine being off, the experiment), and their explanations sit one tap away in a bottom sheet.
import { Suspense, lazy, memo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Overview, Run } from "../lib/api";
import { type Book, bookChecks, holdingView } from "../lib/book";
import { TEST_PHASE, ruleName } from "../lib/explain";
import { experimentsFrom, multiDayFrom, noDecisionText, parseRetired, retiredText } from "../lib/experiment";
import { money, px, signedMoney, timeBoth } from "../lib/fmt";
import { LEVELS } from "../lib/model";
import { ExperimentSheetBody, engineDetail } from "./Experiment";
import { Sheet } from "./Sheet";
import { StockBadge } from "./StockBadge";
import { CA_REASON, caNotes, onDay } from "../lib/corpactions";
import { exitKinds } from "../lib/engineevents";
import { type PricedBook, markOf } from "../lib/marks";
import { DayChange } from "./Marks";
import { Tick } from "./Tick";
import { NAME, REPO_DIR } from "../brand/brand";

type Tone = "breach" | "warn" | "info" | "neutral" | "exp";

/** One status row: the fact in a few words; the explanation opens in a sheet. */
export function StatusRow({ tone, text, title, children, phoneDup = false }: { tone: Tone; text: ReactNode; title: string; children: ReactNode;
  /** v4 PR 6: the phone halt bar already says this on every page, so on phones the row is not shown a second time. */
  phoneDup?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div role={tone === "breach" ? "alert" : tone === "warn" ? "status" : undefined} className={phoneDup ? "dup-phone" : undefined}>
      <button type="button" className={`status-row ${tone}`} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {tone === "breach" && <i className="status-dot" aria-hidden="true" />}
        <span className="status-text">{text}</span>
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={title}>{children}</Sheet>
    </div>
  );
}

/** N2: the steps after a damaged-journal stop, in the lazy trading-state chunk (shown only inside the row's sheet). */
const JournalSteps = lazy(() => import("./TradingState").then((m) => ({ default: m.JournalSteps })));

/** True when the phone halt bar shows every item of the risk row (all of them are red kinds). */
export const allRed = (risk: { items: string[]; red?: string[] }) =>
  risk.items.length > 0 && risk.items.every((t) => risk.red?.includes(t));

/** The rows above the pot, in order: a halt or breach, the engine off, a broken safety check of the multi-day book,
 *  then the experiment (or the test phase when no experiment runs). Never hidden; only their explanations are.
 *  Memoised: a streamed price re-renders Home about once a second and none of these rows depends on it. Of the engine
 *  chip only `asleep` and `note` are read here (its `sub` counts seconds). */
export const HomeStatus = memo(function HomeStatus({ run, overview, risk, engineLive, engine, book }: {
  run: Run; overview: Overview; risk: { severity: "ok" | "warn" | "breach"; items: string[]; red?: string[] } | undefined;
  engineLive: boolean; book: Book | undefined;
  /** The header chip's state (Shell.engineChip): asleep outside market hours is a calm row, not the warning. */
  engine?: { asleep: boolean; note: string; journal?: boolean };
}) {
  const active = run.kind === "paper" && ["scheduled", "running", "paused"].includes(run.status);
  const exps = experimentsFrom(engineDetail(overview.heartbeat)).filter((e) => e.experiment);
  const md = multiDayFrom(engineDetail(overview.heartbeat));
  const retired = md?.rules.find((r) => r.retired) ?? null;
  const checks = book && book.session ? bookChecks(book, exitKinds(md)) : null;
  const unprotected = checks ? checks.missing : [];
  const offBooks = checks?.reconciled && !checks.reconciled.ok;
  const rows: ReactNode[] = [];
  if (risk && risk.severity !== "ok") {
    const first = risk.items[0] ?? (risk.severity === "breach" ? "A limit or safety halt is in force" : "Heads up");
    rows.push(
      <StatusRow key="risk" tone={risk.severity === "breach" || risk.red?.length ? "breach" : "warn"} phoneDup={allRed(risk)}
        title={risk.severity === "breach" || risk.red?.length ? "Limit or safety halt in force" : "Heads up"}
        text={<>{first}{risk.items.length > 1 ? <span className="muted"> · {risk.items.length - 1} more</span> : null}</>}>
        <ul className="sheet-list">{risk.items.map((t) => <li key={t}>{t}</li>)}</ul>
        <p className="muted">{NAME} applies its limits by itself. Open trades keep their stop at the broker.</p>
        <p><Link to="/health">Engine health</Link></p>
      </StatusRow>,
    );
  }
  if (active && engine?.journal)
    rows.push(
      <StatusRow key="off" tone="breach" phoneDup title="Stopped: its journal is damaged" text={engine.note}>
        <Suspense fallback={null}><JournalSteps /></Suspense>
        <p><Link to="/health">Engine health</Link></p>
      </StatusRow>,
    );
  else if (active && !engineLive && engine?.asleep)
    rows.push(
      <StatusRow key="off" tone="neutral" title="The engine is asleep" text={engine.note}>
        <p>The market is closed, so nothing would trade now anyway. The Mac that runs the engine is off on purpose at night: it shuts down
          at 22:20 CET and starts again on weekdays at 13:45 CET, before the market opens at 15:30 CET.</p>
        <p className="muted">The stocks held overnight stay as they are. The multi-day rule sells only at a close, by its own list.</p>
      </StatusRow>,
    );
  else if (active && !engineLive)
    rows.push(
      <StatusRow key="off" tone="warn" title="The engine isn't running" text={engine?.note || "Engine offline · no new trades until it is back"}>
        <p>The market is open, but nothing new trades until the engine runs again. Start it on your Mac: open Terminal in
          the <code className="num">{REPO_DIR}</code> folder and run <code className="num">./scripts/run_engine.sh</code>.</p>
        <p className="muted">Stocks already held stay held. Stops already at the broker stay there.</p>
      </StatusRow>,
    );
  if (unprotected.length || offBooks)
    rows.push(
      <StatusRow key="book" tone="breach" title="The multi-day book needs attention" phoneDup
        text={unprotected.length ? `${unprotected.length} stock${unprotected.length === 1 ? " has" : "s have"} no safety stop` : `${NAME}'s books and the broker disagree`}>
        {unprotected.length > 0 && <p>No safety stop at the broker for {unprotected.join(", ")}. {NAME} places it again.</p>}
        {offBooks && <p>{NAME}'s books and the broker disagree. Buying stops until the two match again.</p>}
        <p><Link to="/positions">Positions</Link></p>
      </StatusRow>,
    );
  rows.push(...noDecisionRows(md));
  if (run.kind === "replay")
    rows.push(
      <StatusRow key="replay" tone="neutral" title="Replay, not live trading" text="Replay on past data, not live trading">
        <p>This run was played on past market data with the Balanced rules and the shadow cost model.</p>
      </StatusRow>,
    );
  else if (retired) {
    const r = parseRetired(retired.note ?? "");
    rows.push(
      <StatusRow key="ended" tone="warn" title={ruleName(retired.rule)} text={`Experiment ended: ${ruleName(retired.rule)} retired`}>
        <p>{retiredText(ruleName(retired.rule), r.fellPct, r.session)}</p>
        {exps[0] && <ExperimentSheetBody e={exps[0]} />}
      </StatusRow>,
    );
  } else if (exps.length) {
    rows.push(
      <StatusRow key="exp" tone="exp" title={ruleName(exps[0].strategy)} text={exps[0].label}>
        <ExperimentSheetBody e={exps[0]} md={md} />
      </StatusRow>,
    );
  } else if (run.kind === "paper")
    rows.push(
      <StatusRow key="phase" tone="info" title="Test phase" text="Test phase: paper only, testing the systems">
        <p>{TEST_PHASE.text}</p>
        <p><a href={TEST_PHASE.url} target="_blank" rel="noopener noreferrer">{TEST_PHASE.link}<span className="sr-only"> (opens in a new tab)</span></a></p>
      </StatusRow>,
    );
  return rows.length ? <div className="status-rows">{rows}</div> : null;
}, (a, b) => a.run === b.run && a.overview === b.overview && a.risk === b.risk && a.engineLive === b.engineLive && a.book === b.book
  && a.engine?.asleep === b.engine?.asleep && a.engine?.note === b.engine?.note);

/** Product, 16 Nov 2026: a skipped decision (the daily prices were not refreshed, or their check failed) is a fact the
 *  stakeholder sees without a tap: the rule's decision for that session did not happen. Home and Engine. */
export function noDecisionRows(md: ReturnType<typeof multiDayFrom>): ReactNode[] {
  return [...caRows(md), ...(md?.rules ?? []).filter((r) => !r.retired && r.note && /^no decision today/i.test(r.note)).map((r) => (
    <StatusRow key={`nd-${r.rule}`} tone="warn" title="No decision today" text={`No decision today: ${ruleName(r.rule)} skipped its decision`}>
      <p>{noDecisionText(r.note!)}</p>
      <p className="muted">{ruleName(r.rule)} decides after a close and trades at the next close. This session's decision was skipped, so it bought and
        sold nothing new; its stocks keep their safety stops.</p>
      <p className="muted" style={{ fontSize: 14 }} title="engine note">{r.note}</p>
    </StatusRow>
  ))];
}

/** #266: a held stock with a split, spin-off, merger or ticker change announced, and a session the list couldn't be
 *  read (no new buys then). Amber: something to know, not a halt. */
function caRows(md: ReturnType<typeof multiDayFrom>): ReactNode[] {
  const { held, unread } = caNotes(md?.raw);
  return [
    ...held.map((a) => (
      <StatusRow key={`ca-${a.symbol}`} tone="warn" title={`${a.symbol}: ${a.action} coming`} text={`${a.symbol}: ${a.action} on ${onDay(a.on)}`}>
        <p>{a.symbol}, which {NAME} holds, has a {a.action} announced for {onDay(a.on)}. The engine does not handle this yet, so check the holding before then.</p>
        <p className="muted">{CA_REASON} It keeps the stocks it already holds, with their safety stops.</p>
        <p className="muted" style={{ fontSize: 14 }} title="engine note">{a.line}</p>
      </StatusRow>
    )),
    ...unread.slice(0, 1).map((u) => (
      <StatusRow key="ca-unread" tone="warn" title="No new buys this session" text="No new buys this session: corporate actions couldn't be read">
        <p>The list of announced corporate actions couldn't be read, so the multi-day rule buys nothing new this session. Sales and stops go on.</p>
        <p className="muted" style={{ fontSize: 14 }} title="engine note">{u}</p>
      </StatusRow>
    )),
  ];
}

/** Stakeholder, 16 Nov 2026: an ended run is on screen while a newer run is scheduled (or already running). One row
 *  says so; its sheet can switch to the new run. */
export function NewRunRow({ next, overview, onShow }: { next: Run; overview: Overview; onShow?: () => void }) {
  const d = engineDetail(overview.heartbeat) as { next_open?: unknown } | null;
  const open = typeof d?.next_open === "string" && Number.isFinite(Date.parse(d.next_open)) ? d.next_open : null;
  const lv = next.level ? LEVELS[next.level].name : null;
  const when = next.status === "scheduled" ? (open ? `starts at the open, ${timeBoth(open)}` : "starts at the next open") : "running now";
  return (
    <div className="status-rows">
      <StatusRow tone="info" title="New run" text={`New run: ${money(next.amount_usd).replace(".00", "")}${lv ? ` · ${lv}` : ""} · ${when}`}>
        <p>A new paper run of {money(next.amount_usd)}{lv ? ` at ${lv}` : ""} {next.status === "scheduled" ? `is scheduled: it ${when}` : "is running"}.
          The figures below belong to the last run, which has ended.</p>
        {onShow && <button type="button" className="primary sheet-cta" onClick={onShow}>Show the new run</button>}
      </StatusRow>
    </div>
  );
}

// ---------------------------------------------------------------- top holdings

export interface HoldingRowData { key: string; symbol: string; value: number; gain: number | null; to: string;
  bar: { stop: number; now: number; target: number } | null; sub: string | null;
  /** Today's change against the previous close, in percent: only while the prices are live (lib/marks.ts). */
  day?: number | null;
  /** A multi-day holding (its figures can be live); a day position's come from the engine's ledger. */
  book?: boolean }

type IntradayPos = { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number };

/** Intraday positions (the ledger) and the multi-day book's stocks, biggest first. With `live` (lib/marks.liveBook) a
 *  book row's value and gain are at the price now and it carries today's change; without it they are at the close
 *  mark. The order is by the value at the booked close either way, so rows do not swap places as prices move. */
export function holdingRows(positions: Record<string, IntradayPos>, book: Book | undefined, live?: PricedBook | null): HoldingRowData[] {
  const rows: [number, HoldingRowData][] = Object.entries(positions).map(([s, p]) => {
    const now = p.last ?? p.fill, value = p.qty * (now ?? 0);
    return [value, { key: `d-${s}`, symbol: s, value, gain: p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null,
      to: `/positions/${p.trade_id}`, bar: now !== null && p.target > p.stop ? { stop: p.stop, now, target: p.target } : null,
      sub: p.status === "pending" ? "filling" : null }];
  });
  for (const p of book?.positions ?? []) {
    const v = holdingView(p, book!, markOf(live, p.symbol));
    rows.push([p.qty * p.mark, { key: `m-${p.rule}-${p.symbol}`, symbol: p.symbol, value: v.value, gain: v.gain, to: "/positions", bar: null,
      sub: `${ruleName(p.rule)}${v.days !== null ? ` · ${v.days} d` : ""}`, day: v.dayPct, book: true }]);
  }
  return rows.sort((a, b) => b[0] - a[0]).map((r) => r[1]);
}

export function TopHoldings({ rows, max, top = 3, waiting = false, from }: { rows: HoldingRowData[]; max: number | null; top?: number;
  /** The first live prices are on their way: keep the place of each book row's "today" line. */ waiting?: boolean;
  /** Where the book rows' figures come from ("live" or "close"), for Tick: a change of source does not tint. */ from?: string }) {
  return (
    <section aria-labelledby="hold-top-h" className="home-holdings">
      <Link to="/positions" className="home-section-link">
        <h2 id="hold-top-h">{rows.length ? `Holding ${rows.length}${max ? ` of ${max}` : ""}` : "Holding nothing"}</h2>
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
      </Link>
      {rows.length ? (
        <ul className="hold-rows">
          {rows.slice(0, top).map((r) => (
            <HoldRow key={r.key} r={r} waiting={waiting && !!r.book} from={from} />
          ))}
        </ul>
      ) : <p className="muted" style={{ margin: 0, fontSize: 15 }}>Nothing held right now. {NAME} is flat by 15:55 every day; multi-day stocks show here once bought.</p>}
    </section>
  );
}

/** One holding row. Memoised on its data (plain values, compared as JSON): a streamed price re-renders only the rows
 *  whose figures moved, never the whole list. */
const HoldRow = memo(function HoldRow({ r, waiting, from }: { r: HoldingRowData; waiting: boolean; from?: string }) {
  const nav = useNavigate();
  return (
    <li>
      <button type="button" className="hold-row" onClick={() => nav(r.to)}>
        <StockBadge symbol={r.symbol} size={40} />
        <span className="hold-mid"><b>{r.symbol}</b>
          {r.bar ? <PriceBar {...r.bar} /> : r.sub ? <span className="muted">{r.sub}</span> : null}</span>
        <span className="hold-end num"><b><Tick value={r.value} from={from}>{money(r.value)}</Tick></b>
          {r.gain !== null && <Tick value={r.gain} from={from} className={r.gain > 0 ? "gain" : r.gain < 0 ? "loss" : "flat"}>{signedMoney(r.gain)}</Tick>}
          <DayChange pct={r.day} waiting={waiting} /></span>
      </button>
    </li>
  );
}, (a, b) => JSON.stringify(a) === JSON.stringify(b));

/** Where the price is between the stop and the target. */
function PriceBar({ stop, now, target }: { stop: number; now: number; target: number }) {
  const at = Math.max(0, Math.min(1, (now - stop) / (target - stop)));
  return (
    <span className="pbar" role="img" aria-label={`Price ${px(now)} between stop ${px(stop)} and target ${px(target)}`}>
      <i style={{ left: `${at * 100}%` }} />
    </span>
  );
}
