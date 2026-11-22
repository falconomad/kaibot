// M9 1.14 (#439): the engine's last ledger/broker check, as the heartbeat carries it (`detail.reconcile`,
// engine/reconcile.py `Reconciler.status`), and its differences in plain words. The owner sees the exact numbers
// before accepting; what the screen showed goes back with the command (`shown`), and the engine accepts only if it
// still finds exactly that at the broker.
import { brandText, NAME } from "../brand/brand";
import { money } from "./fmt";

export interface ReconDiff {
  kind: string; symbol: string | null;
  /** broker minus the books (shares, or dollars for cash). Null: this kind cannot be accepted. */
  offset: number | null;
  ledger: number | null; broker: number | null;
  ledgerChange: number | null; brokerChange: number | null; receivable: number | null;
  qty: number | null; side: string | null; error: string | null;
}
export interface ReconState {
  ok: boolean; ts: string | null; diffs: ReconDiff[]; more: number;
  refused: { ts: string | null; why: string } | null;
}
export type Shown = { kind: string; symbol: string | null; offset: number };

const fin = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** Null: the heartbeat says nothing (an engine before #439, or no check yet). Anything unreadable is left out, and a
 *  block that says "not ok" with no readable difference stays "not ok" (never shown as a match). */
export function reconcileFrom(detail: unknown): ReconState | null {
  const r = obj(obj(detail)?.reconcile);
  if (!r || typeof r.ok !== "boolean") return null;
  const diffs: ReconDiff[] = [];
  for (const x of Array.isArray(r.diffs) ? r.diffs : []) {
    const d = obj(x), kind = str(d?.kind);
    if (!d || !kind) continue;
    diffs.push({ kind, symbol: str(d.symbol), offset: kind === "quantity" || kind === "cash" ? fin(d.offset) : null,
      ledger: fin(d.ledger), broker: fin(d.broker), ledgerChange: fin(d.ledger_change), brokerChange: fin(d.broker_change),
      receivable: fin(d.receivable), qty: fin(d.qty) ?? (fin(d.venue) !== null && fin(d.booked) !== null ? fin(d.venue)! - fin(d.booked)! : null),
      side: str(d.side), error: str(d.error) });
  }
  const ref = obj(r.refused);
  return { ok: r.ok && diffs.length === 0, ts: str(r.ts), diffs, more: Math.max(0, fin(r.more) ?? 0),
    refused: ref && str(ref.why) ? { ts: str(ref.ts), why: str(ref.why)! } : null };
}

/** Shares without a trailing ".000000": 8, 2.5, 0.333333. */
export const shares = (v: number) => { const s = String(Number(v.toFixed(6))); return `${s} ${Math.abs(v) === 1 ? "share" : "shares"}`; };

export interface DiffView { head: string; rows: [string, string][]; note: string; canAccept: boolean }

/** One difference: what the engine's books say, what the broker says, and the gap, in words. */
export function diffView(d: ReconDiff): DiffView {
  if (d.kind === "quantity" && d.ledger !== null && d.broker !== null && d.offset !== null) {
    const gap = d.broker - d.ledger;
    return { head: d.symbol ?? "A stock", canAccept: true,
      rows: [[`${NAME}'s books`, shares(d.ledger)], ["The broker", shares(d.broker)],
        ["Difference", `${shares(Math.abs(gap))} ${gap < 0 ? "fewer" : "more"} at the broker`]],
      note: "" };
  }
  if (d.kind === "cash" && d.ledgerChange !== null && d.brokerChange !== null && d.offset !== null) {
    const gap = d.brokerChange - d.ledgerChange;
    const rows: [string, string][] = [[`${NAME}'s books: cash change since the run began`, money(d.ledgerChange)],
      ["The broker: cash change since the run began", money(d.brokerChange)],
      ["Difference", `${money(Math.abs(gap))} ${gap < 0 ? "less" : "more"} at the broker`]];
    if (d.receivable) rows.splice(2, 0, ["Dividends owed, not paid yet (already left out)", money(d.receivable)]);
    return { head: "Cash", canAccept: true, rows, note: "" };
  }
  if (d.kind === "foreign_fill") return { head: d.symbol ?? "A stock", canAccept: false,
    rows: d.qty !== null ? [["Filled at the broker", `${d.side === "sell" ? "sold" : d.side === "buy" ? "bought" : "traded"} ${shares(d.qty)}`]] : [],
    note: `The broker filled an order ${NAME} doesn't know, placed by hand or by another program. New buys in this stock wait. If you accept a difference on this page, this one is accepted with it; otherwise it clears by itself after three days.` };
  if (d.kind === "unbooked") return { head: d.symbol ?? "A stock", canAccept: false,
    rows: d.qty !== null ? [["Filled, not in the books yet", shares(d.qty)]] : [],
    note: "A fill hasn't reached the books yet. New buys in this stock wait. It usually clears by itself within minutes and can't be accepted here." };
  if (d.kind === "unavailable") return { head: "The broker's account couldn't be read", canAccept: false, rows: [],
    note: `${NAME} couldn't compare its books with the broker, so it treats that as a difference. It tries again every 15 minutes; nothing to accept.` };
  return { head: d.symbol ?? "A difference", canAccept: false, rows: [],
    note: "This difference can't be shown in full here, so it can't be accepted here." };
}

/** What goes back with the command: every difference that can be accepted, exactly as it was displayed. */
export const shownOf = (s: ReconState): Shown[] =>
  s.diffs.filter((d) => diffView(d).canAccept).map((d) => ({ kind: d.kind, symbol: d.symbol, offset: d.offset as number }));

/** The whole list can be accepted from here: at least one acceptable difference, none hidden beyond the list. */
export const acceptable = (s: ReconState) => !s.ok && s.more === 0 && shownOf(s).length > 0;

export const REASON_MIN = 3, REASON_MAX = 200;

/** The safety-check row's one line. */
export function reconLine(s: ReconState): string {
  if (s.ok) return "Match";
  const n = s.diffs.length + s.more;
  return `${n === 1 ? "1 difference" : n > 1 ? `${n} differences` : "Differ"} · new buys stopped`;
}

/** Why the engine said no to the last acceptance, in the owner's words. */
export function refusedText(why: string): string {
  if (/changed/.test(why)) return "The numbers changed after you looked, so nothing was accepted. Check the numbers below and accept again.";
  if (/healed/.test(why)) return "Part of the difference cleared by itself, so nothing was accepted. Check what is left and accept again.";
  if (/reason/.test(why)) return "The acceptance had no reason, so nothing was accepted.";
  return brandText(`Nothing was accepted: ${why}`);
}

/** Why the command could not be queued, in the owner's words. */
export function queueErrorText(e: unknown): string {
  const m = e instanceof Error ? e.message : String(e ?? "");
  if (/unknown command|command_kind_check/i.test(m)) return "This needs a database update that hasn't been applied yet (migration 0037). Nothing was sent.";
  if (/not authorised/i.test(m)) return "Only the owner can accept a difference. Nothing was sent.";
  return `The acceptance couldn't be sent, so nothing changed. ${m}`;
}
