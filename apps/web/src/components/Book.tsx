// #130: the multi-day book's building blocks (boards 11, 12, 15). Small, shell-agnostic pieces: a data hook, a
// status strip, the invested meter with the limit, the next-decision card, a compact holding row and its detail.
// The v4 mobile shell (bottom tab bar, bottom sheets) can wrap them unchanged: on a phone each piece shows only its
// headline and opens its detail on a tap; nothing here sets page layout.
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Heartbeat } from "../lib/api";
export { useBook } from "./useBook";
import { type Book, type BookPosition, bookChecks, holdingView, nextDecision } from "../lib/book";
import { etToday } from "../lib/derive";
import { experimentsFrom, multiDayFrom } from "../lib/experiment";
import { ruleName } from "../lib/explain";
import { dateEt, day, money, px, signedMoney, timeBoth } from "../lib/fmt";
import { StockBadge, companyName } from "./StockBadge";
import { engineDetail } from "./Experiment";
import sectors from "../data/symbols.json";


export const sectorOf = (symbol: string) => (sectors as Record<string, { industry?: string }>)[symbol]?.industry ?? null;
const pctTxt = (v: number, d = 1) => `${v.toFixed(d)}%`;
const signedPct = (v: number | null) => (v === null ? "" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}%`);
const arrow = (v: number) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
const tone = (v: number) => (v > 0 ? "gain" : v < 0 ? "loss" : "flat");

/** The multi-day context from the engine heartbeat: limits per rule, the daily path's state and next decision. */
export function bookContext(beats: Heartbeat[] | undefined, book: Book | undefined) {
  const d = engineDetail(beats) as Record<string, unknown> | null;
  const exps = experimentsFrom(d);
  const md = multiDayFrom(d);
  const last = md?.rules.map((r) => r.lastDecision).filter((x): x is string => !!x).sort().at(-1) ?? null;
  const next = md ? nextDecision(typeof d?.state === "string" ? d.state : null, etToday(), typeof d?.next_open === "string" ? d.next_open : null,
    last, book?.session ?? null) : null;
  const capPct = exps.map((e) => e.capPct).filter((c): c is number => c !== null).sort((a, b) => a - b)[0] ?? null;
  return { exps, md, lastDecision: last, nextDecision: next, capPct };
}

// ---------------------------------------------------------------- status strip (board 11 top, board 15 problems)

export function BookChecks({ b }: { b: Book }) {
  const c = bookChecks(b);
  const stops = c.n === 0 ? { ok: true, text: "None needed yet" }
    : c.missing.length ? { ok: false, text: `${c.resting} of ${c.n} in place · ${c.missing.join(", ")} missing` }
    : c.rearming.length ? { ok: true, text: `${c.resting} of ${c.n} in place · ${c.rearming.join(", ")} being placed again` }
    : { ok: true, text: `${c.n} of ${c.n} in place` };
  const rec = c.reconciled;
  const books = !rec ? { ok: true, text: "Checked at the first close" }
    : rec.ok ? { ok: true, text: "Match the broker" }
    : { ok: false, text: rec.diffs.length ? rec.diffs.slice(0, 2).map((x) => `${x.symbol}: ${fmtQty(x.ledger)} here, ${fmtQty(x.broker)} at the broker`).join(" · ")
      : `Cash differs by ${money(Math.abs(rec.cashDiff ?? 0))}` };
  const bad = !stops.ok || !books.ok;
  return (
    <div className={`book-strip ${bad ? "bad" : ""}`} role={bad ? "alert" : "status"}>
      <Check ok={stops.ok} label="Safety stops" text={stops.text} />
      <Check ok={books.ok} label="Books" text={books.text} title={rec?.ts ? `checked ${dateEt(rec.ts)} ${timeBoth(rec.ts)}` : undefined} />
    </div>
  );
}
const fmtQty = (v: number | null) => (v === null ? "none" : `${v} sh`);
function Check({ ok, label, text, title }: { ok: boolean; label: string; text: string; title?: string }) {
  return (
    <span className="book-check" title={title}>
      <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke={ok ? "var(--pass)" : "var(--fail)"} strokeWidth="2.4" strokeLinecap="round">
        <circle cx="12" cy="12" r="9" />{ok ? <path d="M8 12.4 l2.7 2.7 L16 9.6" /> : <path d="M9 9 l6 6 M15 9 l-6 6" />}
      </svg>
      <span><span className="cap">{label}</span><span className="book-check-v">{ok ? "" : <span className="sr-only">Problem: </span>}{text}</span></span>
    </span>
  );
}

// ---------------------------------------------------------------- invested meter (board 11 right column)

export function Invested({ b, capPct }: { b: Book; capPct: number | null }) {
  const gross = Math.max(0, Math.min(1, b.gross ?? 0));
  const cash = b.equity && b.cash !== null ? Math.max(0, b.cash / b.equity) : 1 - gross;
  return (
    <div className="book-invested">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span>Invested</span><b className="num" style={{ fontSize: 24 }}>{pctTxt(gross * 100, 0)}</b>
      </div>
      <div className="book-meter" role="meter" aria-label="Invested share of the pot" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(gross * 100)}
        aria-valuetext={`${pctTxt(gross * 100, 0)} invested${capPct !== null ? `, limit ${capPct}%` : ""}`}>
        <i style={{ width: `${gross * 100}%` }} />
        {capPct !== null && <b className="book-limit" style={{ left: `${Math.min(100, capPct)}%` }} title={`Limit ${capPct}%`} />}
      </div>
      <div className="muted book-meter-ends"><span>All cash</span>{capPct !== null && <span>Limit {capPct}%</span>}<span>Fully invested</span></div>
      <div className="book-rules">
        {b.byRule.map((r) => <RuleShare key={r.rule} name={ruleName(r.rule)} share={r.share} />)}
        <RuleShare name="Cash" share={cash} />
      </div>
    </div>
  );
}
function RuleShare({ name, share }: { name: string; share: number }) {
  return (
    <div className="book-rule-row"><span>{name}</span><span className="book-mini" aria-hidden="true"><i style={{ width: `${Math.min(100, share * 100)}%` }} /></span>
      <b className="num">{pctTxt(share * 100, 0)}</b></div>
  );
}

// ---------------------------------------------------------------- next decision (board 11 "Next close", fallback)

export function NextDecision({ ctx }: { ctx: ReturnType<typeof bookContext> }) {
  const notes = ctx.md?.rules.filter((r) => r.note && !r.retired) ?? [];
  return (
    <div>
      <p style={{ margin: 0 }}>{ctx.nextDecision
        ? <>The rules decide after the close on <b>{day(ctx.nextDecision)}</b>; what they pick is bought at the next session's close.</>
        : "The rules decide after each close; what they pick is bought at the next session's close."}</p>
      {ctx.lastDecision && <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>Last decision: after the close on {day(ctx.lastDecision)}.</p>}
      {notes.length > 0 && <p className="exp-note" role="status">{notes.length} rule{notes.length === 1 ? "" : "s"} with a note: see the paper experiment card.</p>}
    </div>
  );
}

// ---------------------------------------------------------------- one holding: compact row and its detail

/** One stock. `compact` (Home's short list, and every phone): symbol, value and gain or loss; the rest on a tap. */
export function HoldingRow({ p, b, retirePct, compact }: { p: BookPosition; b: Book; retirePct: number | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const v = holdingView(p, b);
  const id = `h-${p.rule}-${p.symbol}`;
  return (
    <li className={`hrow ${open ? "open" : ""} ${compact ? "compact" : ""}`}>
      <button type="button" className="hrow-main" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <StockBadge symbol={p.symbol} size={40} />
        <span className="hrow-name"><b>{p.symbol}</b><span className="muted">{companyName(p.symbol)}</span></span>
        <span className="hrow-d d-only num"><span>{v.days !== null ? `${v.days} day${v.days === 1 ? "" : "s"}` : "—"}</span>
          <span className="muted">{p.opened ? `since ${day(p.opened)}` : ""}</span></span>
        <span className="hrow-d d-only num"><span>{v.share !== null ? pctTxt(v.share * 100) : "—"}</span><span className="muted">{p.qty} sh · {money(v.value)}</span></span>
        <span className="hrow-d d-only"><span>{sectorOf(p.symbol) ?? <span className="muted">sector not known</span>}</span></span>
        <span className="hrow-d d-only num"><span>{p.stop ? px(p.stop.price) : <span className="loss">none</span>}</span>
          <span className="muted">{stopText(p, v.stopBelowPct)}</span></span>
        <span className="hrow-v num"><span className="m-only">{money(v.value)}</span>
          <span className={tone(v.gain)}>{arrow(v.gain)} {signedMoney(v.gain)}</span><span className="muted d-only">{signedPct(v.gainPct)}</span></span>
      </button>
      {open && (
        <div className="hrow-detail" id={id}>
          <div className="kv">
            <span>Held</span><span /><span className="v">{v.days !== null ? `${v.days} days` : "—"}{p.opened ? ` · since ${day(p.opened)}` : ""}</span>
            <span>Shares</span><span /><span className="v">{p.qty} · bought at {px(p.avgCost)}</span>
            <span>Price at the close</span><span /><span className="v">{px(p.mark)}{p.priced ? "" : " (no price yet: last known)"}</span>
            <span>Size, of the pot</span><span /><span className="v">{v.share !== null ? pctTxt(v.share * 100) : "—"} · {money(v.value)}</span>
            <span>Gain or loss</span><span /><span className={`v ${tone(v.gain)}`}>{arrow(v.gain)} {signedMoney(v.gain)} {signedPct(v.gainPct)}</span>
            {p.dividends > 0 && <><span>Dividends</span><span /><span className="v">{money(p.dividends)}</span></>}
            <span>Sector</span><span /><span className="v">{sectorOf(p.symbol) ?? "not known"}</span>
            <span>Safety stop at the broker</span><span /><span className="v">{p.stop ? `${px(p.stop.price)} · ${stopText(p, v.stopBelowPct)}` : "none: Kaibot places it again"}</span>
            <span>Rule</span><span /><span className="v">{ruleName(p.rule)}</span>
          </div>
          {retirePct !== null && <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>The whole book stops if it falls {retirePct}% from its peak: then every stock is sold.</p>}
        </div>
      )}
    </li>
  );
}
function stopText(p: BookPosition, below: number | null) {
  if (!p.stop) return "missing";
  const st = p.stop.state === "resting" ? "in place" : p.stop.state === "re-arming" ? "being placed again" : "missing";
  return below !== null ? `${st} · ${pctTxt(below)} below` : st;
}

/** Home: the book's headline (board 11 on a phone: one number and today's change), with links to the detail. */
export function BookHero({ b }: { b: Book }) {
  const today = b.equity !== null && b.prevClose !== null ? b.equity - b.prevClose : null;
  const since = b.equity !== null && b.startEquity !== null ? b.equity - b.startEquity : null;
  return (
    <div className="book-hero">
      <span className="muted">Pot, at the last close{b.session ? ` (${day(b.session)})` : ""}</span>
      <div className="hero num">{money(b.equity)}</div>
      <div className="num book-hero-sub">
        {today !== null && <span className={tone(today)}>{arrow(today)} {signedMoney(today)} that day</span>}
        {since !== null && <span className={`d-only ${tone(since)}`}>{arrow(since)} {signedMoney(since)} since the start</span>}
      </div>
    </div>
  );
}

export const HoldingsLink = ({ n }: { n: number }) => <Link to="/holdings" className="book-link">{n ? `See all ${n}` : "Holdings"} →</Link>;

