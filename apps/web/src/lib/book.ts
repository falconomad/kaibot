// #130 (S90): the multi-day book for Home, Holdings and their states (design boards 11, 12 and 15, approved by
// Product for the stakeholder on 16 Nov 2026). Pure: unit-tested in book.test.ts.
//
// Data (contract: migrations/0018_m7_ledger.sql section 5, docs/ops/ledger.md):
// - kb_book(p_run, p_session?): the book at the latest close. With no ledger row yet: session, mtm, exposure and
//   reconciled are null and positions is [] (a new run, or no close since the ledger started).
// - kb_equity_series(p_run): one point per session, `marked` false for sessions closed before the ledger existed.
// - heartbeat `detail.experiments` (cap, stop line) and `detail.multi_day` (last decision, notes): lib/experiment.ts.
// Not in the contract, so not shown (the boards' fallbacks): sell-condition readings, the next close's planned
// orders, the book's own peak (so the distance to the stop line). A price during the day is not in the contract
// either: it comes from the paper account (api/marks.ts, lib/marks.ts), and only while the account holds exactly this
// book. Every field is untrusted jsonb and read through the parsers below.
import type { Daily, LedgerPos, Overview, Run } from "./api";
import { exitOf } from "./exits";
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
  /** How the rule exits, when the source says so (kb_book 0028: "target_list" | "broker_stop"); see lib/exits.exitOf. */
  exit?: string | null;
  /** kb_book 0028: the row's stop_state as written ("resting" | "missing" | "re-arming" | "none_by_design"). */
  stopState?: string | null;
}
export interface Book {
  runId: number; session: string | null; ts: string | null;
  cash: number | null; invested: number | null; equity: number | null;
  /** ledger_day.close_equity (the reported figure, the same as kb_equity_series' close_equity); `equity` is the venue's. */
  equityReported: number | null; startEquity: number | null; prevClose: number | null;
  dividends: number;
  gross: number | null; overnight: number | null;
  byRule: { rule: string; value: number; share: number }[];
  positions: BookPosition[];
  reconciled: { ok: boolean; ts: string | null; cashDiff: number | null; diffs: { symbol: string; ledger: number | null; broker: number | null }[] } | null;
}
/** `exposure` (#719): the share of the pot invested at that close, kb_equity_series `gross` (invested / the venue's
 *  equity, as the ledger writes it), else invested / close_equity; absent when neither is sent. */
export interface SeriesPoint { session: string; closeEquity: number; marked: boolean; exposure?: number }

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
      dividends: num(o.dividends) ?? 0, stop: sp !== null && st ? { price: sp, state: st } : null, sellWhen: o.sell_when, sector: o.sector, exit: str(o.exit),
      stopState: str(o.stop_state, 20) ?? (s ? str(s.state, 20) : null) }];
  });
  const diffs = Object.entries(obj(rec?.positions) ?? {}).slice(0, 50).map(([symbol, v]) => {
    const d = obj(v);
    return { symbol, ledger: num(d?.ledger), broker: num(d?.broker) };
  });
  return {
    runId: num(r.run_id) ?? runId, session: str(r.session, 10), ts: str(m?.ts),
    cash: num(m?.cash), invested: num(m?.invested), equity: num(m?.equity), equityReported: num(m?.equity_reported), startEquity: num(m?.start_equity),
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
    if (!o || !session || eq === null) return [];
    const gross = num(o.gross), inv = num(o.invested);
    const e = gross !== null ? gross : inv !== null && eq > 0 ? inv / eq : null;
    return [{ session, closeEquity: eq, marked: o.marked === true, ...(e !== null && e >= 0 ? { exposure: e } : {}) }];
  });
}

/** The pot line marked to market (close_equity) next to SPY from the same start (daily_result spy_ret).
 *  #719: and SPY held at the pot's own exposure, as gate v3 measures it (evaluation/mtm.exposure_matched): each
 *  session's SPY return times the share invested at the close before it; the first session earns nothing. "What the
 *  same amount in the market, with no stock picking, would have made." Only when the series carries an exposure; a
 *  close without one (sessions before the ledger: the retired intraday rule was flat at every close) counts as 0. */
export function mtmCurve(start: number, series: SeriesPoint[], daily: Pick<Daily, "session" | "spy_ret">[]): Curve[] {
  const spyRet = new Map(daily.map((d) => [d.session, d.spy_ret ?? 0]));
  const matched = series.some((p) => p.exposure !== undefined);
  let spy = start, same = start, held = 0;
  return series.map((p) => {
    const r = spyRet.get(p.session) ?? 0;
    spy *= 1 + r;
    same *= 1 + held * r;
    held = p.exposure ?? 0;
    return { session: p.session, pot: p.closeEquity, spy, ...(matched ? { spyMatched: same, exposure: held } : {}) };
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

/** The paper account's price of a holding now (lib/marks.ts), with the live pot it is a share of. */
export interface LiveMark { price: number; prevClose: number | null; pot: number }

/** One holding's figures. With `live` (the account holds exactly what the book says, lib/marks.liveBook) every figure
 *  is at the price now: value, gain against the average cost, share of the live pot, and today's change against the
 *  previous close. Without it every figure is at the close mark and there is no change today. Never a mix. */
export function holdingView(p: BookPosition, b: Pick<Book, "equity" | "session">, live?: LiveMark | null) {
  const price = live ? live.price : p.mark;
  const value = p.qty * price;
  const cost = p.qty * p.avgCost;
  const gain = value - cost + p.dividends;
  const pot = live ? live.pot : b.equity;
  const prev = live?.prevClose ?? null;
  return {
    price, value, gain, gainPct: cost > 0 ? (gain / cost) * 100 : null,
    day: live && prev ? p.qty * (price - prev) : null, dayPct: live && prev ? (price / prev - 1) * 100 : null,
    share: pot && pot > 0 ? value / pot : null,
    days: daysHeld(p.opened, b.session),
    stopBelowPct: p.stop && price > 0 ? ((price - p.stop.price) / price) * 100 : null,
  };
}

// ---------------------------------------------------------------- the book's checks (board 11 status strip)

/** Stops count only for positions meant to carry one (lib/exits.exitOf): a rule that sells by its target list has none
 *  by design (`byDesign`), and is never "missing" a stop. `n` is every holding, `needed` those that need a stop.
 *  `kinds`: the heartbeat's rule -> exit kind (lib/engineevents.exitKinds), when the engine sends it. */
export function bookChecks(b: Book, kinds: Record<string, string> = {}) {
  const n = b.positions.length;
  // a stop that rests (or is being placed again) counts wherever it is; a rule without one by design is never "missing"
  const guarded = b.positions.filter((p) => exitOf(p, kinds) === "broker_stop" || (p.stop && p.stop.state !== "missing"));
  const byDesign = b.positions.filter((p) => !guarded.includes(p)).map((p) => p.symbol);
  const resting = guarded.filter((p) => p.stop?.state === "resting").length;
  const missing = guarded.filter((p) => !p.stop || p.stop.state === "missing").map((p) => p.symbol);
  const rearming = guarded.filter((p) => p.stop?.state === "re-arming").map((p) => p.symbol);
  return { n, needed: guarded.length, resting, missing, rearming, byDesign, reconciled: b.reconciled };
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

// ---------------------------------------------------------------- the pot now (Home hero, header meters, chart's end)

const etDay = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
const sessionDay = (s: string) =>
  new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(s + "T00:00:00Z"));

export interface PotNow {
  pot: number; dayPnl: number; dayPct: number;
  /** "today", or "on Wed, Nov 18" when the change shown is the last session's (before the next session starts). */
  dayLabel: string; today: boolean;
  /** The intraday ledger's positions (today's only). */
  positions: Record<string, LedgerPos>;
  /** "book": the marked multi-day book (ledger_day); "realised": closed trades and the intraday ledger only. */
  source: "book" | "realised";
  /** Where the pot shown comes from, so the page can say how fresh it is: the engine's minute row (its time), the
   *  booked close (its session), or null when it is only the sum of closed trades. */
  from: { kind: "engine"; ts: string } | { kind: "close"; session: string } | null;
}

/** Stakeholder, 18 Nov 2026: the pot is the marked book, never the sum of closed trades. With a ledger_day row the pot is
 *  its close_equity (kb_book mtm.equity_reported); the day's change is close_equity less the previous session's
 *  close_equity (kb_equity_series), or less the session's start equity on day one. During the next session, until its
 *  close, the live mark is today's minute ledger (kb_overview `ledger`: the whole run, the book included, at the latest
 *  prices), and today is it less the last close. The minute ledger is not used once the session's close is booked: its
 *  last row is from before the near-close fills (18 Nov: 95,000 in cash at 15:59 ET, the 7 buys filled at the close).
 *  Without a ledger_day row: the realised figures (closed P&L, the minute ledger), as before. */
export function potNow(run: Pick<Run, "amount_usd">, o: Pick<Overview, "daily" | "ledger"> | undefined, book?: Book | null,
  series?: SeriesPoint[] | null, now = new Date()): PotNow {
  const today = etDay(now);
  const daily = o?.daily ?? [];
  const before = run.amount_usd + daily.filter((d) => d.session < today).reduce((a, d) => a + d.pnl, 0);
  const closedAll = run.amount_usd + daily.reduce((a, d) => a + d.pnl, 0);
  const l = o?.ledger;
  const fresh = !!l && etDay(new Date(l.ts)) === today;
  const positions = fresh ? l!.positions : {};
  const ledgerPot = fresh ? Math.min(l!.equity_venue, l!.equity_shadow) : closedAll;
  const close = book?.session ? book.equityReported ?? book.equity : null;
  if (close === null || close === undefined || !book?.session) {
    const dayStart = fresh ? before : ledgerPot;
    const d = ledgerPot - dayStart;
    return { pot: ledgerPot, dayPnl: d, dayPct: dayStart ? (d / dayStart) * 100 : 0, dayLabel: "today", today: true, positions, source: "realised",
      from: fresh ? { kind: "engine", ts: l!.ts } : null };
  }
  if (book.session < today && fresh) {
    // in the session: the engine's minute ledger is the whole run marked live (its start equity is the last ledger
    // close, multi-day sleeves at the latest price: engine/core.py equity_now), so today = it less the last close
    const d = ledgerPot - close;
    return { pot: ledgerPot, dayPnl: d, dayPct: close ? (d / close) * 100 : 0, dayLabel: "today", today: true, positions, source: "book",
      from: { kind: "engine", ts: l!.ts } };
  }
  const prior = (series ?? []).filter((p) => p.session < book.session!).at(-1)?.closeEquity ?? book.prevClose ?? run.amount_usd;
  const d = close - prior;
  const isToday = book.session >= today;
  return { pot: close, dayPnl: d, dayPct: prior ? (d / prior) * 100 : 0, dayLabel: isToday ? "today" : `on ${sessionDay(book.session)}`,
    today: isToday, positions, source: "book", from: { kind: "close", session: book.session } };
}
