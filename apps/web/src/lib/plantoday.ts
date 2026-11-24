// Product, 24 Nov 2026: Home and Positions show only what matters now. A multi-day plan is shown as planned orders
// only while its fill session is the US session of today (New York date, not the phone's) or a later one (before the
// open, the next session's plan). A plan whose fill session is over is history: it never shows as "planned buys", and
// its unfilled orders never appear in a list of planned or open orders. On Activity it becomes one quiet line,
// "Last plan (18 Nov): 7 of 37 buys filled, 30 expired at the close", counted from what the dashboard already reads
// (kb_multiday_plan's order states, the `multi_day_unfilled` events, kb_book). Pure: unit-tested in plantoday.test.ts.
// QA on #620 / Product, 24 Nov 2026: under #366 a past plan's short buys are carried and sent again at later closes
// until the next decision. A buy the engine carries into TODAY's session (a `multi_day_carry` event of today) is a
// current order: it shows in today's list ("carried from 18 Nov, sent again at today's close") and is kept out of the
// last plan's expired / not-filled count. Before today's carry event (it comes in the near-close window) there is no
// record that a buy is carried today, so it stays hidden.
// #621: while the market filter is off no carried buy is sent; the card then says "Carried buys on hold — market
// filter off" (carryHold), never "sent again at today's close".
import type { Book } from "./book";
import type { Carry } from "./carry";
import type { Unfilled } from "./engineevents";
import type { Plan, PlanOrder } from "./multiday";

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ET_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

/** The US session date (America/New_York) at `now`: "2026-11-24". 00:30 in Berlin is still the evening before there. */
export const sessionDate = (now: Date = new Date()) => ET_DAY.format(now);

/** True for a plan of today's session or a later one. A plan without a readable fill session is not shown: nothing
 *  says it is today's. */
export function isCurrentPlan(plan: Pick<Plan, "fillsAt">, today: string): boolean {
  return !!plan.fillsAt && DATE.test(plan.fillsAt) && plan.fillsAt >= today;
}

/** The plans to show as planned orders now (Home, Positions). */
export const currentPlans = <P extends Pick<Plan, "fillsAt">>(plans: P[], today: string): P[] => plans.filter((p) => isCurrentPlan(p, today));

const isBuy = (o: PlanOrder) => (o.action === "buy" || o.action === "resize") && o.sharesTarget > o.sharesNow;
const shortDay = (d: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(d + "T00:00:00Z"));

/** A past plan's buy carried into today's session: shown as a current order. */
export interface CarriedBuy { rule: string; symbol: string; from: string; held: number; target: number; why: string | null }

/** The buys of plans whose fill session is over that a carry event of `today` sends again at today's close. */
export function carriedToday(plans: Plan[], carries: Carry[], today: string): CarriedBuy[] {
  return plans.filter((p) => p.fillsAt && DATE.test(p.fillsAt) && p.fillsAt < today).flatMap((p) => {
    const c = carries.find((x) => x.rule === p.rule && x.session === today && (!x.of || x.of === p.fillsAt));
    return (c?.carried ?? []).filter((x) => x.target > x.held).map((x) => ({ rule: p.rule, symbol: x.symbol, from: p.fillsAt!, held: x.held, target: x.target,
      why: p.orders.find((o) => o.symbol === x.symbol)?.why ?? null }));
  });
}
/** "carried from 18 Nov, sent again at today's close" */
export const carriedText = (c: Pick<CarriedBuy, "from">) => `carried from ${shortDay(c.from)}, sent again at today's close`;

/** #621: while the market filter is off (SPY below its 200-day average) the engine sends no carried buy: each
 *  carry-over skips them all with `risk_off` (engine/daily.py carry_targets). The newest past plan of a rule with buys
 *  still short is on hold when its latest carry-over (today's, or before today's near-close window the last one) says
 *  so. The heartbeat does not carry the engine's entry block (ticket E1 / #446), so this reads the carry events only.
 *  Returns the reason in words, or null when nothing says the carried buys are held. */
export function carryHold(plans: Plan[], carries: Carry[], today: string): string | null {
  const latest = new Map<string, Plan>();
  for (const p of plans) {
    if (!p.fillsAt || !DATE.test(p.fillsAt) || p.fillsAt >= today || !p.orders.some(isBuy)) continue;
    const q = latest.get(p.rule);
    if (!q || q.fillsAt! < p.fillsAt) latest.set(p.rule, p);
  }
  for (const p of latest.values()) {
    // QA on #626: after a late send (M9 1.4, its event has `late_of` = the fill session) the carry-overs name the late
    // send's session as their `of` (engine/live_daily.py `_carry`: the plan's own session); they are this plan's too
    const late = carries.find((x) => x.rule === p.rule && x.lateOf === p.fillsAt && x.session > p.fillsAt!)?.session;
    const c = carries.find((x) => x.rule === p.rule && x.session > p.fillsAt! && x.session <= today
      && (!x.of || x.of === p.fillsAt || (!!late && x.of === late)));
    if (c?.riskOff) return "market filter off";
  }
  return null;
}
/** "Carried buys on hold — market filter off" */
export const carryHoldText = (why: string) => `Carried buys on hold — ${why}`;

export interface LastPlan { rule: string; session: string; buys: number; filled: number; carried: number; expired: number | null; text: string }

/** The newest plan whose fill session is over, in one line; null when there is none (or it had no buys). Buys carried
 *  into today (`carried`, from carriedToday) are current orders: counted apart, never as expired or not filled. */
export function lastPlan(plans: Plan[], unfilled: Unfilled[], book: Book | null | undefined, today: string, carried: CarriedBuy[] = []): LastPlan | null {
  const past = plans.filter((p) => p.fillsAt && DATE.test(p.fillsAt) && p.fillsAt < today)
    .sort((a, b) => b.fillsAt!.localeCompare(a.fillsAt!))
    .find((p) => p.orders.some(isBuy));
  if (!past) return null;
  const at = past.fillsAt!;
  const buys = past.orders.filter(isBuy);
  const mine = unfilled.filter((u) => u.rule === past.rule && u.side === "buy" && (u.session === null || u.session === at));
  const held = (s: string) => (book?.positions ?? []).filter((p) => p.rule === past.rule && p.symbol === s).reduce((a, p) => a + p.qty, 0);
  let filled = 0, expired = 0, carry = 0;
  for (const o of buys) {
    const u = mine.find((x) => x.symbol === o.symbol);
    if (carried.some((c) => c.rule === past.rule && c.from === at && c.symbol === o.symbol)) { carry += 1; continue; }
    // filled, even partly: the order says so, the expiry left shares filled, or the book holds more than before the plan
    if (o.state === "filled" || (u && u.filled > 0) || held(o.symbol) > o.sharesNow) filled += 1;
    else if (u) expired += 1;
  }
  const rest = buys.length - filled - carry;
  const tail = (carry ? `, ${carry} carried into today's close` : "") + (rest === 0 ? "" : expired === rest ? `, ${expired} expired at the close`
    : expired > 0 ? `, ${expired} expired at the close, the rest not filled` : ", rest not filled");
  return { rule: past.rule, session: at, buys: buys.length, filled, carried: carry, expired: expired || null,
    text: `Last plan (${shortDay(at)}): ${filled} of ${buys.length} buy${buys.length === 1 ? "" : "s"} filled${tail}` };
}
