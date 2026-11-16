// #130 (board 12): the stocks the multi-day book holds, grouped by rule, at the last close (kb_book).
// Desktop: one row per stock with days held, share of the pot, sector, the safety stop and gain or loss.
// Phone: symbol, value and gain or loss; the rest opens in place on a tap (HoldingRow).
import { useState } from "react";
import { useRunCtx } from "../components/Shell";
import { BookChecks, HoldingRow, bookContext, useBook } from "../components/Book";
import { ExperimentBadge } from "../components/Experiment";
import { Skeleton } from "../components/Skeleton";
import { Empty, ErrorState } from "../components/States";
import { type BookPosition, byRule, holdingView } from "../lib/book";
import { ruleName, RULES } from "../lib/explain";
import { experimentView } from "../lib/experiment";
import { day, money, signedMoney } from "../lib/fmt";

type Sort = "size" | "gain" | "loss" | "days";
const SORTS: [Sort, string][] = [["size", "Biggest first"], ["gain", "Best first"], ["loss", "Worst first"], ["days", "Held longest"]];

export function Holdings() {
  const { run, overview } = useRunCtx();
  const { book } = useBook(run);
  const [rule, setRule] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("size");
  if (run.kind !== "paper") return <section className="card"><Empty>Replays have no multi-day book: only paper runs hold stocks over a close.</Empty></section>;
  if (book.isPending) return <Skeleton />;
  if (book.isError) return <ErrorState what="The holdings" error={book.error} retry={() => book.refetch()} />;
  const b = book.data;
  const ctx = bookContext(overview?.heartbeat, b);
  const groups = byRule(b);
  const by = (p: BookPosition) => holdingView(p, b);
  const order = (ps: BookPosition[]) => [...ps].sort((x, y) =>
    sort === "gain" ? by(y).gain - by(x).gain : sort === "loss" ? by(x).gain - by(y).gain
      : sort === "days" ? (by(y).days ?? -1) - (by(x).days ?? -1) : by(y).value - by(x).value);
  const shown = groups.filter((g) => rule === null || g.rule === rule);
  const invested = b.invested ?? groups.reduce((a, g) => a + g.value, 0);
  return (
    <>
      <section className="card" aria-labelledby="hold-h">
        <h1 id="hold-h" style={{ margin: 0, fontSize: 28, letterSpacing: "-0.02em" }}>Holdings</h1>
        {b.session === null ? (
          <Empty>No stocks yet. Kaibot buys only at a close, after a rule has checked its list; what it picks shows up here with its safety stop.
            {ctx.nextDecision ? ` The next decision is after the close on ${day(ctx.nextDecision)}.` : ""}</Empty>
        ) : (
          <>
            <p className="muted num" style={{ margin: "4px 0 0" }}>{b.positions.length} stock{b.positions.length === 1 ? "" : "s"} · {money(invested)} in stocks
              {b.gross !== null ? `, ${Math.round(b.gross * 100)}% of the pot` : ""}<span className="d-only">{b.cash !== null ? ` · ${money(b.cash)} in cash` : ""}</span>
              {" "}· at the close on {day(b.session)}</p>
            <BookChecks b={b} />
            {groups.length > 0 && (
              <div className="hold-tools">
                <div className="chips" role="group" aria-label="Rule">
                  <button type="button" className="chip-btn" aria-pressed={rule === null} onClick={() => setRule(null)}>All · {b.positions.length}</button>
                  {groups.map((g) => <button key={g.rule} type="button" className="chip-btn" aria-pressed={rule === g.rule}
                    onClick={() => setRule(g.rule)}>{ruleName(g.rule)} · {g.positions.length}</button>)}
                </div>
                <label className="hold-sort">Sort <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
              </div>
            )}
          </>
        )}
      </section>
      {b.session !== null && !groups.length && <section className="card"><Empty>No stocks held at the last close: the book is all cash.</Empty></section>}
      {shown.map((g) => {
        const e = ctx.exps.find((x) => x.strategy === g.rule) ?? null;
        const v = e ? experimentView(e) : null;
        return (
          <section key={g.rule} className="card" aria-labelledby={`hr-${g.rule}`}>
            <div className="hold-group">
              <h2 id={`hr-${g.rule}`} style={{ margin: 0 }} title={`code: ${g.rule}`}>{ruleName(g.rule)}</h2>
              <span className="muted num">{g.positions.length} stock{g.positions.length === 1 ? "" : "s"}{g.share !== null ? ` · ${Math.round(g.share * 100)}% of the pot` : ""}</span>
              {e && <ExperimentBadge e={e} />}
              <span className="spacer" />
              <span className={`num ${g.gain > 0 ? "gain" : g.gain < 0 ? "loss" : "flat"}`}>{g.gain > 0 ? "▲" : g.gain < 0 ? "▼" : ""} {signedMoney(g.gain)} since bought</span>
            </div>
            <p className="muted d-only" style={{ fontSize: 13, margin: "4px 0 10px" }}>{RULES[g.rule] ? `It ${RULES[g.rule].what}.` : ""}
              {v && (v.cap || v.stop) ? ` Limits: ${[v.cap, v.stop].filter(Boolean).join(", ")}.` : ""}</p>
            <div className="hrow-head d-only" aria-hidden="true"><span /><span>Stock</span><span>Held</span><span>Size, of the pot</span><span>Sector</span>
              <span>Safety stop at the broker</span><span>Gain or loss</span></div>
            <ul className="hlist">{order(g.positions).map((p) => <HoldingRow key={p.symbol} p={p} b={b} retirePct={e?.retirePct ?? null} />)}</ul>
          </section>
        );
      })}
      <p className="muted" style={{ fontSize: 12, textAlign: "center" }}>Prices at the last close. What the rules look at to sell a stock and the plan for the next
        close show here once the engine reports them.</p>
    </>
  );
}
