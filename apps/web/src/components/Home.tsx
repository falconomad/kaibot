// v4 Home (docs/design/mobile_first_v4.md, "Home" and mockups/v4/home.png): status rows that open sheets, the paper
// pot, top holdings and a Details section. One thing per screen: the pot; the facts above it stay visible (a halt, the
// engine being off, the experiment), and their explanations sit one tap away in a bottom sheet.
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Overview, Run } from "../lib/api";
import { type Book, bookChecks, holdingView } from "../lib/book";
import { TEST_PHASE, ruleName } from "../lib/explain";
import { experimentView, experimentsFrom, multiDayFrom, parseRetired, retiredText } from "../lib/experiment";
import { money, px, signedMoney } from "../lib/fmt";
import { ExperimentCard, engineDetail } from "./Experiment";
import { Sheet } from "./Sheet";
import { StockBadge } from "./StockBadge";

type Tone = "breach" | "warn" | "info" | "neutral";

/** One status row: the fact in a few words; the explanation opens in a sheet. */
export function StatusRow({ tone, text, title, children }: { tone: Tone; text: ReactNode; title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div role={tone === "breach" ? "alert" : tone === "warn" ? "status" : undefined}>
      <button type="button" className={`status-row ${tone}`} aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {tone === "breach" && <i className="status-dot" aria-hidden="true" />}
        <span className="status-text">{text}</span>
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={title}>{children}</Sheet>
    </div>
  );
}

/** The rows above the pot, in order: a halt or breach, the engine off, a broken safety check of the multi-day book,
 *  then the experiment (or the test phase when no experiment runs). Never hidden; only their explanations are. */
export function HomeStatus({ run, overview, risk, engineLive, book }: {
  run: Run; overview: Overview; risk: { severity: "ok" | "warn" | "breach"; items: string[] } | undefined;
  engineLive: boolean; book: Book | undefined;
}) {
  const active = run.kind === "paper" && ["scheduled", "running", "paused"].includes(run.status);
  const exps = experimentsFrom(engineDetail(overview.heartbeat)).filter((e) => e.experiment);
  const md = multiDayFrom(engineDetail(overview.heartbeat));
  const retired = md?.rules.find((r) => r.retired) ?? null;
  const checks = book && book.session ? bookChecks(book) : null;
  const unprotected = checks ? checks.missing : [];
  const offBooks = checks?.reconciled && !checks.reconciled.ok;
  const rows: ReactNode[] = [];
  if (risk && risk.severity !== "ok") {
    const first = risk.items[0] ?? (risk.severity === "breach" ? "A limit or safety halt is in force" : "Heads up");
    rows.push(
      <StatusRow key="risk" tone={risk.severity === "breach" ? "breach" : "warn"} title={risk.severity === "breach" ? "Limit or safety halt in force" : "Heads up"}
        text={<>{first}{risk.items.length > 1 ? <span className="muted"> · {risk.items.length - 1} more</span> : null}</>}>
        <ul className="sheet-list">{risk.items.map((t) => <li key={t}>{t}</li>)}</ul>
        <p className="muted">Kaibot applies its limits by itself. Open trades keep their stop at the broker.</p>
        <p><Link to="/health">Engine health</Link></p>
      </StatusRow>,
    );
  }
  if (active && !engineLive)
    rows.push(
      <StatusRow key="off" tone="warn" title="The engine isn't running" text="Engine offline: nothing new trades">
        <p>Nothing trades until you start it on your Mac: open Terminal in the kaibot folder and run <code className="num">./scripts/run_engine.sh</code>.</p>
        <p className="muted">Open trades keep their stop-loss at Alpaca meanwhile.</p>
      </StatusRow>,
    );
  if (unprotected.length || offBooks)
    rows.push(
      <StatusRow key="book" tone="breach" title="The multi-day book needs attention"
        text={unprotected.length ? `${unprotected.length} stock${unprotected.length === 1 ? " has" : "s have"} no safety stop` : "Kaibot's books and the broker disagree"}>
        {unprotected.length > 0 && <p>No safety stop at the broker for {unprotected.join(", ")}. Kaibot places it again.</p>}
        {offBooks && <p>Kaibot's books and the broker disagree. Buying stops until the two match again.</p>}
        <p><Link to="/holdings">Holdings</Link></p>
      </StatusRow>,
    );
  if (run.kind === "replay")
    rows.push(
      <StatusRow key="replay" tone="neutral" title="Replay, not live trading" text="Replay on past data, not live trading">
        <p>This run was played on past market data with the Balanced rules and the shadow cost model.</p>
      </StatusRow>,
    );
  else if (retired) {
    const r = parseRetired(retired.note ?? "");
    rows.push(
      <StatusRow key="ended" tone="warn" title="Experiment ended" text={`Experiment ended: ${ruleName(retired.rule)} retired`}>
        <p>{retiredText(ruleName(retired.rule), r.fellPct, r.session)}</p>
        <ExperimentCard beats={overview.heartbeat} />
      </StatusRow>,
    );
  } else if (exps.length) {
    const v = experimentView(exps[0]);
    rows.push(
      <StatusRow key="exp" tone="info" title={v.text} text={exps[0].label}>
        <ExperimentCard beats={overview.heartbeat} />
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
}

// ---------------------------------------------------------------- top holdings

export interface HoldingRowData { key: string; symbol: string; value: number; gain: number | null; to: string;
  bar: { stop: number; now: number; target: number } | null; sub: string | null }

type IntradayPos = { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number };

/** Intraday positions (the ledger) and the multi-day book's stocks, biggest first. */
export function holdingRows(positions: Record<string, IntradayPos>, book: Book | undefined): HoldingRowData[] {
  const rows: HoldingRowData[] = Object.entries(positions).map(([s, p]) => {
    const now = p.last ?? p.fill;
    return { key: `d-${s}`, symbol: s, value: p.qty * (now ?? 0), gain: p.fill !== null && p.last !== null ? (p.last - p.fill) * p.qty : null,
      to: `/positions/${p.trade_id}`, bar: now !== null && p.target > p.stop ? { stop: p.stop, now, target: p.target } : null,
      sub: p.status === "pending" ? "filling" : null };
  });
  for (const p of book?.positions ?? []) {
    const v = holdingView(p, book!);
    rows.push({ key: `m-${p.rule}-${p.symbol}`, symbol: p.symbol, value: v.value, gain: v.gain, to: "/holdings", bar: null,
      sub: `${ruleName(p.rule)}${v.days !== null ? ` · ${v.days} d` : ""}` });
  }
  return rows.sort((a, b) => b.value - a.value);
}

export function TopHoldings({ rows, max, top = 3 }: { rows: HoldingRowData[]; max: number | null; top?: number }) {
  const nav = useNavigate();
  return (
    <section aria-labelledby="hold-top-h" className="home-holdings">
      <Link to="/positions" className="home-section-link">
        <h2 id="hold-top-h">{rows.length ? `Holding ${rows.length}${max ? ` of ${max}` : ""}` : "Holding nothing"}</h2>
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M9 6 L15 12 L9 18" /></svg>
      </Link>
      {rows.length ? (
        <ul className="hold-rows">
          {rows.slice(0, top).map((r) => (
            <li key={r.key}>
              <button type="button" className="hold-row" onClick={() => nav(r.to)} aria-label={`${r.symbol}: ${money(r.value)}${r.gain !== null ? `, ${signedMoney(r.gain)}` : ""}`}>
                <StockBadge symbol={r.symbol} size={40} />
                <span className="hold-mid"><b>{r.symbol}</b>
                  {r.bar ? <PriceBar {...r.bar} /> : r.sub ? <span className="muted">{r.sub}</span> : null}</span>
                <span className="hold-end num"><b>{money(r.value)}</b>
                  {r.gain !== null && <span className={r.gain > 0 ? "gain" : r.gain < 0 ? "loss" : "flat"}>{signedMoney(r.gain)}</span>}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="muted" style={{ margin: 0, fontSize: 14 }}>Nothing held right now. Kaibot is flat by 15:55 every day; multi-day stocks show here once bought.</p>}
    </section>
  );
}

/** Where the price is between the stop and the target. */
function PriceBar({ stop, now, target }: { stop: number; now: number; target: number }) {
  const at = Math.max(0, Math.min(1, (now - stop) / (target - stop)));
  return (
    <span className="pbar" role="img" aria-label={`Price ${px(now)} between stop ${px(stop)} and target ${px(target)}`}>
      <i style={{ left: `${at * 100}%` }} />
    </span>
  );
}
