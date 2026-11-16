// #130: Home's multi-day book (board 11), with board 15's loading, empty and problem states. Shown only when the run
// has a multi-day book or the engine runs a multi-day rule; an intraday-only run sees nothing new.
// Phone: the hero number, today's change and a short list; the meter and the next decision open on a tap.
import { useState } from "react";
import type { Heartbeat, Run } from "../lib/api";
import { byRule } from "../lib/book";
import { BookChecks, BookHero, HoldingRow, HoldingsLink, Invested, NextDecision, bookContext, useBook } from "./Book";
import { ErrorState } from "./States";

const SHORT = 5;

export function BookSection({ run, beats }: { run: Run; beats: Heartbeat[] | undefined }) {
  const { book } = useBook(run);
  const [more, setMore] = useState(false);
  const b = book.data;
  const ctx = bookContext(beats, b);
  const multiDay = ctx.exps.length > 0 || ctx.md !== null || (b?.positions.length ?? 0) > 0;
  if (run.kind !== "paper") return null;
  if (book.isError) return multiDay ? <ErrorState what="The multi-day book" error={book.error} retry={() => book.refetch()} /> : null;
  if (book.isPending) return multiDay ? <section className="card book-loading" role="status" aria-label="Loading the multi-day book"><div className="sk" style={{ height: 120 }} /></section> : null;
  if (!multiDay || !b) return null;
  const rows = byRule(b).flatMap((g) => g.positions).sort((x, y) => y.qty * y.mark - x.qty * x.mark);
  const retire = ctx.exps.map((e) => e.retirePct).find((r) => r !== null) ?? null;
  return (
    <section className="card book" aria-labelledby="book-h">
      <div className="book-head">
        <h2 id="book-h" style={{ margin: 0 }}>Stocks held for weeks</h2>
        <HoldingsLink n={b.positions.length} />
      </div>
      {b.session === null ? (
        <div className="book-empty">
          <p style={{ margin: 0 }}><b>No stocks yet.</b> Kaibot buys only at a close, after a rule has checked its list. What it picks shows up here with its
            safety stop.</p>
          <NextDecision ctx={ctx} />
        </div>
      ) : (
        <>
          <BookChecks b={b} />
          <div className="book-grid">
            <div>
              <BookHero b={b} />
              {rows.length ? (
                <ul className="hlist">{rows.slice(0, SHORT).map((p) => <HoldingRow key={`${p.rule}-${p.symbol}`} p={p} b={b} retirePct={retire} compact />)}</ul>
              ) : <p className="muted" style={{ fontSize: 14 }}>No stocks held at the last close: the book is all cash.</p>}
              {rows.length > SHORT && <p className="muted" style={{ fontSize: 13, margin: "6px 0 0" }}>and {rows.length - SHORT} more · <HoldingsLink n={rows.length} /></p>}
            </div>
            <button type="button" className="m-only book-more" aria-expanded={more} aria-controls="book-side" onClick={() => setMore(!more)}>
              {more ? "Less" : "Invested and next decision"}</button>
            <div id="book-side" className={`book-side ${more ? "open" : ""}`}>
              <Invested b={b} capPct={ctx.capPct} />
              <div className="book-next"><h3>Next decision</h3><NextDecision ctx={ctx} /></div>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
