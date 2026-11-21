// #366 carry-over on the plan panel (Positions "At the close of ..."). Between two decisions of a multi-day rule, the
// engine sends again, at each session's near-close window, what the latest decision's buys still lack, and journals
// each try as the event `multi_day_carry` (engine/live_daily.py `_plan`, `_carry`; engine/daily.py `carry_targets`):
//   { strategy, session, of: <the decision's fill session>, carried: [[symbol, target_shares, held_shares]],
//     skipped: { symbol: reason }, risk_off: bool, unread?: "<why the daily view was not there>", error?: "<...>" }
// It reaches the dashboard through kb_health's `events` (migration 0033 adds the type; until it is applied there are
// no such events and a buy still short after its fill session reads "waiting to be carried").
// The other sources: kb_multiday_plan (the latest decision's orders and their state on the fill session) and kb_book
// (the shares held at the last booked close). Pure: unit-tested in carry.test.ts.
import type { Book } from "./book";
import type { Plan, PlanOrder } from "./multiday";
import { reasonText } from "./multiday";
import { brandText } from "../brand/brand";
import { NAME } from "../brand/brand";

type Ev = { type: string; ts: string; run_id?: number | null; payload: Record<string, unknown> };
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const str = (v: unknown, max = 200) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const date = (v: unknown) => { const s = str(v, 10); return s && DATE.test(s) ? s : null; };

export interface Carry {
  ts: string; rule: string; session: string; of: string | null;
  carried: { symbol: string; target: number; held: number }[];
  skipped: Record<string, string>;
  riskOff: boolean;
  /** the carry-over did not run: the evening's daily view was missing (`unread`) or it failed (`error`) */
  failed: "unread" | "error" | null;
}

/** The run's `multi_day_carry` events, newest session first (a later event of the same session wins). */
export function carriesFrom(events: Ev[] | undefined, runId: number): Carry[] {
  const out = (events ?? []).filter((e) => e.type === "multi_day_carry" && (e.run_id === null || e.run_id === undefined || e.run_id === runId))
    .flatMap((e): Carry[] => {
      const p = obj(e.payload) ?? {}, rule = str(p.strategy, 80), session = date(p.session);
      if (!rule || !session) return [];
      const carried = (Array.isArray(p.carried) ? p.carried : []).slice(0, 60).flatMap((c) => {
        const a = Array.isArray(c) ? c : [], symbol = str(a[0], 12), target = num(a[1]);
        return symbol && target !== null ? [{ symbol, target, held: num(a[2]) ?? 0 }] : [];
      });
      const skipped: Record<string, string> = {};
      for (const [s, why] of Object.entries(obj(p.skipped) ?? {}).slice(0, 60)) {
        const w = str(why);
        if (str(s, 12) && w) skipped[s] = w;
      }
      return [{ ts: str(e.ts, 40) ?? "", rule, session, of: date(p.of), carried, skipped, riskOff: p.risk_off === true,
        failed: str(p.error) ? "error" : str(p.unread) ? "unread" : null }];
    });
  return out.sort((a, b) => b.session.localeCompare(a.session) || b.ts.localeCompare(a.ts));
}

/** When the rule decides again, in words ("Wednesday's decision"): its cadence (strategies/momentum.py) and the
 *  weekday of the plan's fill session. */
const WEEKLY = new Set(["trend_momentum"]), MONTHLY = new Set(["mom_12_1_band"]);
const weekday = (d: string) => new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "UTC" }).format(new Date(d + "T00:00:00Z"));
export function nextDecisionText(rule: string, fillsAt: string | null): string {
  if (WEEKLY.has(rule) && fillsAt) return `${weekday(fillsAt)}'s decision`;
  if (MONTHLY.has(rule)) return "next month's decision";
  return "the rule's next decision";
}

/** A skip reason (engine/daily.py carry_targets, engine/live_daily.py _carry, _carry_room) in plain words, and whether
 *  the next session tries again (`again`) or the buy waits for the next decision. */
export function skipText(reason: string, rule: string, fillsAt: string | null): { text: string; again: boolean } {
  const until = `it will try again next session until ${nextDecisionText(rule, fillsAt)}`;
  const never = `it is not bought before ${nextDecisionText(rule, fillsAt)}`;
  const r = reason.trim();
  const fixed: Record<string, [string, boolean]> = {
    "market filter off": ["the market check is off: the rule doesn't buy while the market trend is down", true],
    "daily prices failed the check": ["its daily prices failed the data check", true],
    "split today": ["it splits today, so the planned share count no longer fits", false],
    "split since the decision": ["it split since the decision, so the planned share count no longer fits", false],
    "no bar on the fill day": ["it had no price on the day of the plan", false],
    "not in the daily view": ["it is missing from the evening's daily prices", true],
    "sold since the decision": ["it was sold since the decision", false],
    "a fill the venue reported is not booked yet": ["a fill of it is still being booked", true],
    "corporate action or overnight cap": ["a corporate action or the overnight limit holds it back", true],
    "no cash room at the last close (the cash reserve is kept)": ["there was not enough cash at the last close (the cash reserve is kept)", true],
    "above the per-name cap at the last close": ["it would go above the limit for one stock", true],
    "its sector is at the sector cap at the last close": ["its sector is at its limit", true],
    "the book is at its exposure cap at the last close": ["the book is at its limit for money in stocks", true],
    "no price at the last close": ["it had no price at the last close", true],
    "the book's equity or cash cannot be read": [`${NAME} could not read the book's value or cash`, true],
  };
  const f = fixed[r];
  if (f) return { text: `${f[0]}; ${f[1] ? until : never}`, again: f[1] };
  if (/^order (canceled|rejected)$/.test(r)) {
    return { text: `its order was ${r.endsWith("rejected") ? "refused by the broker" : "canceled"}; ${never}`, again: false };
  }
  if (r.startsWith("the book cannot be valued")) return { text: `${NAME} could not value the book; ${until}`, again: true };
  if (r.startsWith("not buyable: ")) {
    const why = r.slice("not buyable: ".length);
    const w = why === "universe" ? "it is not in the list of stocks the rule may buy today (price, trading volume or index membership)"
      : why === "no score" ? "the rule has no score for it today" : `the rule may not buy it today: ${reasonText(why)}`;
    return { text: `${w}; ${until}`, again: true };
  }
  return { text: `${brandText(r)}; ${until}`, again: true };
}

export type BuyState =
  /** bought on the plan's own fill session */
  | { kind: "filled"; day: string | null }
  /** bought by a carry-over on `day` */
  | { kind: "carried_filled"; day: string }
  /** sent again at the close of `day` (today, or a session whose close is not booked yet) */
  | { kind: "carried"; day: string; today: boolean }
  /** short of its target after the fill session: the next session's carry-over tries again */
  | { kind: "waiting"; held: number; target: number }
  | { kind: "skipped"; day: string; today: boolean; text: string; again: boolean }
  /** the plan's fill session is not over: the order's own state (planned, at the broker, ...) */
  | { kind: "order" };

const isBuy = (o: PlanOrder) => (o.action === "buy" || o.action === "resize") && o.sharesTarget > o.sharesNow;

/** Each planned buy's state now. `today` is the ET date; `book` the book at the last booked close. */
export function buyState(plan: Plan, o: PlanOrder, carries: Carry[], book: Book | null | undefined, today: string): BuyState {
  const held = (book?.positions ?? []).filter((p) => p.rule === plan.rule && p.symbol === o.symbol).reduce((a, p) => a + p.qty, 0);
  const booked = book?.session ?? null;
  const mine = carries.filter((c) => c.rule === plan.rule && (!plan.fillsAt || !c.of || c.of === plan.fillsAt)
    && (!plan.fillsAt || c.session > plan.fillsAt));
  const lastCarried = mine.find((c) => c.carried.some((x) => x.symbol === o.symbol));
  const atTarget = held >= o.sharesTarget - 1e-9 && (booked !== null && (!plan.fillsAt || booked >= plan.fillsAt));
  if (atTarget) {
    return lastCarried && booked !== null && lastCarried.session <= booked ? { kind: "carried_filled", day: lastCarried.session }
      : { kind: "filled", day: plan.fillsAt };
  }
  if (!plan.fillsAt || today <= plan.fillsAt) return o.state === "filled" ? { kind: "filled", day: plan.fillsAt } : { kind: "order" };
  const c = mine[0]; // the latest try
  if (c) {
    const isToday = c.session === today;
    const open = booked === null || c.session > booked; // its close is not booked yet
    if (c.carried.some((x) => x.symbol === o.symbol) && open) return { kind: "carried", day: c.session, today: isToday };
    const why = c.skipped[o.symbol];
    if (why) return { kind: "skipped", day: c.session, today: isToday, ...skipText(why, plan.rule, plan.fillsAt) };
    if (c.failed && open) {
      const text = c.failed === "unread" ? "the evening's daily prices were not ready, so nothing was carried" : "the carry-over failed, so nothing was carried";
      return { kind: "skipped", day: c.session, today: isToday, text: `${text}; it will try again next session until ${nextDecisionText(plan.rule, plan.fillsAt)}`, again: true };
    }
  }
  return { kind: "waiting", held, target: o.sharesTarget };
}

/** The planned buys of a decision whose fill session is over, with their states; null when there is nothing to say:
 *  every buy is in, and none of them came in at the last booked close (they then stop showing). */
export function pastBuys(plan: Plan, carries: Carry[], book: Book | null | undefined, today: string) {
  if (!plan.fillsAt || plan.fillsAt >= today) return null;
  const rows = plan.orders.filter(isBuy).map((o) => ({ o, s: buyState(plan, o, carries, book, today) }));
  const open = rows.some((r) => r.s.kind !== "filled" && r.s.kind !== "carried_filled");
  const fresh = rows.some((r) => (r.s.kind === "carried_filled" ? r.s.day : r.s.kind === "filled" ? r.s.day : null) === book?.session);
  return rows.length && (open || fresh) ? rows : null;
}

const short = (d: string) => new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(d + "T00:00:00Z"));
const shares = (n: number) => `${Number.isInteger(n) ? n : Number(n.toFixed(4))} share${n === 1 ? "" : "s"}`;

/** The state as the panel says it: the short tag on the right, and the line under the symbol. */
export function buyStateText(s: BuyState): { tag: string; line: string | null; tone: "filled" | "canceled" | "" } {
  switch (s.kind) {
    case "filled": return { tag: "filled", line: s.day ? `bought at the close of ${short(s.day)}` : null, tone: "filled" };
    case "carried_filled": return { tag: `carried and filled on ${short(s.day)}`, line: null, tone: "filled" };
    case "carried": return { tag: "carried", line: `sent again at ${s.today ? "today's close" : `the close of ${short(s.day)}`}`, tone: "" };
    case "waiting": return { tag: "waiting to be carried", line: `${s.held ? `${shares(s.held)} of ${s.target} held; ` : "not filled; "}the next session's close tries again`, tone: "" };
    case "skipped": return { tag: s.today ? "skipped today" : `skipped on ${short(s.day)}`, line: s.text, tone: s.again ? "" : "canceled" };
    default: return { tag: "", line: null, tone: "" };
  }
}
