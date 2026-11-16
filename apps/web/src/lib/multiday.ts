// #229 follow-ups on the data of PR #247 (migration 0020 and the engine's heartbeat). Every field is optional and read
// defensively: before 0020 is applied the new RPCs do not exist (they return null here, quietly), and before the engine
// code runs the new fields are absent. Shapes, from #247:
// - heartbeat detail.multi_day.book_line: { run_id, equity, peak, peak_ts, drawdown_pct, end_pct, ended }
// - kb_book.positions[].sell_when: [{ code: "rank", reading, threshold, of, met, as_of },
//     { code: "trend" | "gap", reading_pct, threshold_pct, window_days, met, as_of }]; .sector: text or null
// - kb_multiday_plan(p_run): { rules: [{ rule, decided_after_close_of, decided_at, fills_at_close_of, decides,
//     sell: [{symbol, why}], buy_candidates, orders: [{ symbol, shares_now, shares_target, action, why, coid, state }],
//     skipped, note }] }   state: planned | working | filled | canceled | refused | deferred
// - kb_book_closed(p_run, p_limit): [{ rule, symbol, opened_session, closed_session, qty, avg_cost, exit_px, pnl_usd,
//     dividends, days_held, reason }]
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const str = (v: unknown, max = 40) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const arr = (v: unknown, max = 200) => (Array.isArray(v) ? v.slice(0, max) : []);

// ---------------------------------------------------------------- 1. the stop line
export interface BookLine { equity: number; peak: number; drawdownPct: number; endPct: number; ended: boolean; peakTs: string | null }
export function bookLine(multiDay: unknown, runId: number): BookLine | null {
  const b = obj(obj(multiDay)?.book_line);
  const equity = num(b?.equity), peak = num(b?.peak), dd = num(b?.drawdown_pct), end = num(b?.end_pct);
  if (!b || equity === null || peak === null || dd === null || end === null || end <= 0) return null;
  if (num(b.run_id) !== null && num(b.run_id) !== runId) return null;
  return { equity, peak, drawdownPct: Math.max(0, dd), endPct: end, ended: b.ended === true, peakTs: str(b.peak_ts) };
}
const pctText = (v: number) => `${Number(v.toFixed(1))}%`;
/** "2.2% below its peak · retires at 21%" (#229 item 1). */
export function lineText(l: BookLine): string {
  if (l.ended) return `Stop line reached (${pctText(l.endPct)} below its peak): the book has closed`;
  return `${l.drawdownPct < 0.05 ? "At its peak" : `${pctText(l.drawdownPct)} below its peak`} · retires at ${pctText(l.endPct)}`;
}

// ---------------------------------------------------------------- 3. sells when
export interface Reading { code: string; met: boolean; text: string }
export function sellWhen(raw: unknown): Reading[] {
  return arr(raw, 10).flatMap((r) => {
    const o = obj(r), code = str(o?.code, 20);
    if (!o || !code) return [];
    const met = o.met === true;
    if (code === "rank") {
      const at = num(o.reading), cut = num(o.threshold), of = num(o.of);
      if (cut === null) return [];
      return [{ code, met, text: at === null ? `Not ranked today: sells when outside the top ${cut}`
        : `Ranked ${at}${of ? ` of ${of}` : ""}: sells when outside the top ${cut}` }];
    }
    const at = num(o.reading_pct), cut = num(o.threshold_pct), days = num(o.window_days);
    if (code === "trend") return [{ code, met, text: at === null ? `Sells when below its ${days ?? ""}-day average`.replace("  ", " ")
      : `${at >= 0 ? `${pctText(at)} above` : `${pctText(-at)} below`} its ${days ?? "recent"}-day average: sells when below it` }];
    if (code === "gap" && cut !== null) return [{ code, met, text: at === null ? `Sells after a daily move over ${pctText(cut)}`
      : `Largest daily move ${pctText(at)} in ${days ?? "recent"} days: sells after one over ${pctText(cut)}` }];
    return [];
  });
}
/** The sector the rule's daily view uses; "unknown" and empty read as not known. */
export const sectorText = (raw: unknown) => { const s = str(raw, 40); return s && s.toLowerCase() !== "unknown" ? s : null; };

// ---------------------------------------------------------------- 2. the next close's plan
export type OrderState = "planned" | "working" | "filled" | "canceled" | "refused" | "deferred";
const STATES: OrderState[] = ["planned", "working", "filled", "canceled", "refused", "deferred"];
export interface PlanOrder { symbol: string; action: "buy" | "sell" | "resize"; sharesNow: number; sharesTarget: number; why: string | null; state: OrderState }
export interface Plan { rule: string; decidedAfter: string | null; fillsAt: string | null; orders: PlanOrder[]; sellSoon: string[]; note: string | null }
export function parsePlan(raw: unknown): Plan[] {
  return arr(obj(raw)?.rules, 20).flatMap((r) => {
    const o = obj(r), rule = str(o?.rule, 80);
    if (!o || !rule) return [];
    const orders = arr(o.orders).flatMap((x) => {
      const q = obj(x), symbol = str(q?.symbol, 12), now = num(q?.shares_now), target = num(q?.shares_target);
      const action = ["buy", "sell", "resize"].includes(String(q?.action)) ? q!.action as PlanOrder["action"] : null;
      const state = STATES.find((s) => s === q?.state) ?? "planned";
      return q && symbol && now !== null && target !== null && action ? [{ symbol, action, sharesNow: now, sharesTarget: target, why: str(q.why, 40), state }] : [];
    });
    const sellSoon = arr(o.sell, 100).flatMap((s) => { const sym = str(obj(s)?.symbol, 12); return sym ? [sym] : []; });
    return [{ rule, decidedAfter: str(o.decided_after_close_of, 10), fillsAt: str(o.fills_at_close_of, 10), orders, sellSoon, note: str(o.note, 200) }];
  });
}
const OPEN: OrderState[] = ["planned", "working", "deferred"];
/** The tag on a held row: "Selling tonight", "Selling 3 tonight", "Adding 2 tonight"; null when nothing is planned. */
export function rowTag(plans: Plan[], rule: string, symbol: string): string | null {
  const p = plans.find((x) => x.rule === rule);
  const o = p?.orders.find((x) => x.symbol === symbol && OPEN.includes(x.state));
  if (o) {
    const d = o.sharesTarget - o.sharesNow;
    if (o.sharesTarget === 0) return "Selling tonight";
    return d > 0 ? `Adding ${d} tonight` : `Selling ${-d} tonight`;
  }
  return p && !p.orders.length && p.sellSoon.includes(symbol) ? "Selling tonight" : null;
}
export const STATE_TEXT: Record<OrderState, string> = { planned: "planned", working: "at the broker", filled: "filled", canceled: "canceled",
  refused: "refused by the broker", deferred: "waits for a sale first" };

// ---------------------------------------------------------------- 4. sold
export interface Sold { rule: string; symbol: string; opened: string | null; closed: string; qty: number; avgCost: number; exitPx: number | null;
  pnl: number | null; days: number | null; reason: string | null }
export function parseSold(raw: unknown): Sold[] {
  return arr(raw, 200).flatMap((r) => {
    const o = obj(r), rule = str(o?.rule, 80), symbol = str(o?.symbol, 12), closed = str(o?.closed_session, 10), qty = num(o?.qty), cost = num(o?.avg_cost);
    return o && rule && symbol && closed && qty !== null && cost !== null
      ? [{ rule, symbol, opened: str(o.opened_session, 10), closed, qty, avgCost: cost, exitPx: num(o.exit_px), pnl: num(o.pnl_usd),
        days: num(o.days_held), reason: str(o.reason, 40) }] : [];
  });
}
const REASON: Record<string, string> = { rank: "fell out of the top ranks", rank_below_cutoff: "fell out of the top ranks", trend: "fell below its average",
  below_average: "fell below its average", gap: "a big daily move", stop: "safety stop", book_drawdown: "the book reached its stop line",
  run_drawdown: "the run reached its stop line", flatten_and_stop: "you flattened and stopped", flatten: "flattened" };
export const reasonText = (code: string | null) => (code ? REASON[code] ?? code.replace(/_/g, " ") : "sold");
/** Why the rule buys or adds: the entry codes, in words. */
const BUY: Record<string, string> = { rank: "in the top ranks", size_check: "back to its size", resize: "back to its size" };
export const buyText = (code: string | null) => (code ? BUY[code] ?? code.replace(/_/g, " ") : "");
