// #130 / v4: the multi-day book's shared pieces (the Holdings page is folded into Positions): the book's safety strip
// (stops in place, books against the broker) and the sector of a stock.
import { type Book, bookChecks } from "../lib/book";
import { dateEt, money, timeBoth } from "../lib/fmt";
import sectors from "../data/symbols.json";
export { useBook } from "./useBook";

export const sectorOf = (symbol: string) => (sectors as Record<string, { industry?: string }>)[symbol]?.industry ?? null;

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

