// #130 (S90): the multi-day book for Home, Holdings and their states (design boards 11, 12 and 15, approved by
// Product for the stakeholder on 16 Nov 2026). Pure: unit-tested in book.test.ts.
//
// Data (contract: migrations/0018_m7_ledger.sql section 5, docs/ops/ledger.md):
// - kb_book(p_run, p_session?): the book at the latest close. With no ledger row yet: session, mtm, exposure and
//   reconciled are null and positions is [] (a new run, or no close since the ledger started).
// - kb_equity_series(p_run): one point per session, `marked` false for sessions closed before the ledger existed.
// - heartbeat `detail.experiments` (cap, stop line) and `detail.multi_day` (last decision, notes): lib/experiment.ts.
// Not in the contract, so not shown (the boards' fallbacks): sell-condition readings, the next close's planned
// orders, the book's own peak (so the distance to the stop line), a price during the day (the pot is "at the last
// close"). Every field is untrusted jsonb and read through the parsers below.
import type { Daily } from "./api";
import type { Curve } from "./model";

export interface BookPosition {
  rule: string; symbol: string; qty: number; avgCost: number;
  mark: number;            // the close; held at the last mark or cost when `priced` is false
  priced: boolean;
  opened: string | null;   // first buy, YYYY-MM-DD
  dividends: number;
  stop: { price: number; state: "resting" | "missing" | "re-arming" } | null;
  /** #229 (0020): the rule's sell readings at its last decision, and the sector its daily view uses (raw, read by lib/multiday). */
  sellWhen?: unknown; sector?: unknown;
}
export interface Book {
  runId: number; session: string | null; ts: string | null;
  cash: number | null; invested: number | null; equity: number | null; startEquity: number | null; prevClose: number | null;
  dividends: number;
  gross: number | null; overnight: number | null;
  byRule: { rule: string; value: number; share: number }[];
  positions: BookPosition[];
  reconciled: { ok: boolean; ts: string | null; cashDiff: number | null; diffs: { symbol: string; ledger: number | null; broker: number | null }[] } | null;
}
export interface SeriesPoint { session: string; closeEquity: number; marked: boolean }

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const str = (v: unknown, max = 40) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const STOP_STATES = ["resting", "missing", "re-arming"] as const;

export function parseBook(raw: unknown, runId = 0): Book {
  const r = obj(raw) ?? {};
  const m = obj(r.mtm), x = obj(r.exposure), rec = obj(r.reconciled);
  const positions: BookPosition[] = (Array.isArray(r.positions) ? r.positions : []).slice(0, 600).flatMap((p) => {
    const o = obj(p);
    const rule = str(o?.rule, 80), symbol = str(o?.symbol, 12), qty = num(o?.qty), avg = num(o?.avg_cost);
    const mark = num(o?.mark) ?? num(o?.last);
    if (!o || !rule || !symbol || qty === null || avg === null || mark === null) return [];
    // A row with no shares left (sold by the last fill, before the ledger drops it) is not a holding.
    if (qty <= 0) return [];
    const s = obj(o.stop), sp = num(s?.price), st = STOP_STATES.find((k) => k === s?.state);
    return [{ rule, symbol, qty, avgCost: avg, mark, priced: num(o.last) !== null, opened: str(o.opened_session, 10),
      dividends: num(o.dividends) ?? 0, stop: sp !== null && st ? { price: sp, state: st } : null, sellWhen: o.sell_when, sector: o.sector }];
  });
  const diffs = Object.entries(obj(rec?.positions) ?? {}).slice(0, 50).map(([symbol, v]) => {
    const d = obj(v);
    return { symbol, ledger: num(d?.ledger), broker: num(d?.broker) };
  });
  return {
    runId: num(r.run_id) ?? runId, session: str(r.session, 10), ts: str(m?.ts),
    cash: num(m?.cash), invested: num(m?.invested), equity: num(m?.equity), startEquity: num(m?.start_equity),
    prevClose: num(m?.prev_close_equity), dividends: num(m?.dividends_to_date) ?? 0,
    gross: num(x?.gross), overnight: num(x?.overnight),
    byRule: (Array.isArray(x?.by_rule) ? x.by_rule : []).flatMap((b) => {
      const o = obj(b), rule = str(o?.rule, 80), value = num(o?.value);
      return o && rule && value !== null ? [{ rule, value, share: num(o.share) ?? 0 }] : [];
    }),
    positions,
    reconciled: rec && typeof rec.ok === "boolean"
      ? { ok: rec.ok, ts: str(rec.ts), cashDiff: num(rec.cash_diff), diffs: diffs.filter((d) => d.ledger !== d.broker) } : null,
  };
}

export function parseSeries(raw: unknown): SeriesPoint[] {
  return (Array.isArray(raw) ? raw : []).flatMap((p) => {
    const o = obj(p), session = str(o?.session, 10), eq = num(o?.close_equity);
    return o && session && eq !== null ? [{ session, closeEquity: eq, marked: o.marked === true }] : [];
  });
}

/** The pot line marked to market (close_equity) next to SPY from the same start (daily_result spy_ret). */
export function mtmCurve(start: number, series: SeriesPoint[], daily: Pick<Daily, "session" | "spy_ret">[]): Curve[] {
  const spyRet = new Map(daily.map((d) => [d.session, d.spy_ret ?? 0]));
  let spy = start;
  return series.map((p) => {
    spy *= 1 + (spyRet.get(p.session) ?? 0);
    return { session: p.session, pot: p.closeEquity, spy };
  });
}

// ---------------------------------------------------------------- one holding

const DAY_MS = 86_400_000;
/** Calendar days from the first buy to the book's session (the boards' "63 days since 7 Jan"). */
export const daysHeld = (opened: string | null, session: string | null) => {
  if (!opened || !session) return null;
  const d = Math.round((Date.parse(session + "T00:00:00Z") - Date.parse(opened + "T00:00:00Z")) / DAY_MS);
  return Number.isFinite(d) && d >= 0 ? d : null;
};

export function holdingView(p: BookPosition, b: Pick<Book, "equity" | "session">) {
  const value = p.qty * p.mark;
  const cost = p.qty * p.avgCost;
  const gain = value - cost + p.dividends;
  return {
    value, gain, gainPct: cost > 0 ? (gain / cost) * 100 : null,
    share: b.equity && b.equity > 0 ? value / b.equity : null,
    days: daysHeld(p.opened, b.session),
    stopBelowPct: p.stop && p.mark > 0 ? ((p.mark - p.stop.price) / p.mark) * 100 : null,
  };
}

// ---------------------------------------------------------------- the book's checks (board 11 status strip)

export function bookChecks(b: Book) {
  const n = b.positions.length;
  const resting = b.positions.filter((p) => p.stop?.state === "resting").length;
  const missing = b.positions.filter((p) => !p.stop || p.stop.state === "missing").map((p) => p.symbol);
  const rearming = b.positions.filter((p) => p.stop?.state === "re-arming").map((p) => p.symbol);
  return { n, resting, missing, rearming, reconciled: b.reconciled };
}

/** Group by rule, biggest first inside each rule; rules by their value. */
export function byRule(b: Book) {
  const groups = new Map<string, BookPosition[]>();
  for (const p of b.positions) groups.set(p.rule, [...(groups.get(p.rule) ?? []), p]);
  return [...groups].map(([rule, ps]) => {
    const sorted = [...ps].sort((x, y) => y.qty * y.mark - x.qty * x.mark);
    const value = sorted.reduce((a, p) => a + p.qty * p.mark, 0);
    const gain = sorted.reduce((a, p) => a + holdingView(p, b).gain, 0);
    return { rule, positions: sorted, value, gain, share: b.equity && b.equity > 0 ? value / b.equity : null };
  }).sort((x, y) => y.value - x.value);
}

/** Totals checked against the server's numbers (a test asserts they agree on the sample). */
export function bookTotals(b: Book) {
  const invested = b.positions.reduce((a, p) => a + p.qty * p.mark, 0);
  return { invested, equity: (b.cash ?? 0) + invested, up: b.positions.filter((p) => p.mark > p.avgCost).length,
    down: b.positions.filter((p) => p.mark < p.avgCost).length };
}

/** When the rules decide next: after each close. While the market is open, after today's close; when it is closed,
 *  after the close of the next session (heartbeat next_open), unless tonight's decision is still to come. */
export function nextDecision(state: string | null | undefined, todayEt: string, nextOpen: string | null | undefined,
  lastDecision: string | null, lastClosed: string | null): string | null {
  if (state === "trading" || state === "managing" || state === "preparing") return todayEt;
  if (lastClosed && lastDecision !== null && lastDecision < lastClosed) return lastClosed; // tonight's is pending
  const t = nextOpen ? Date.parse(nextOpen) : NaN;
  if (!Number.isFinite(t)) return null;
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date(t));
}
