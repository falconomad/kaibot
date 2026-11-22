// The plan panel's two late states of M9 (Positions, above "At the close of ..."). Pure: unit-tested in
// planlate.test.ts. Only what the dashboard's RPCs return today is read:
//
// 1. A late decision (M9 1.3, #517). The engine takes a decision after a session's close; when it was not running
//    then, `catch_up` takes it during the next session, which is the decision's own fill session, and its orders go
//    out at that close as usual. kb_multiday_plan returns the decision's `decided_at` and `fills_at_close_of` (it
//    does not return the event's `late` flag): a decision taken on its fill session at or after the 09:30 ET open is
//    a late one, because on time means before that session opens.
// 2. A late send (M9 1.4, #518). When the 15:50-15:57 ET send window of the fill session was missed, the decision is
//    sent once at the close of the next session the engine is up, sized at that day's prices. The engine then writes
//    a `multi_day_carry` event {session: <the late send's session>, late_of: <the missed fill session>, skipped,
//    risk_off}, which kb_health returns. kb_multiday_plan does not return the late send's orders (it looks for the
//    plan under the fill session), so the panel can say that and when it happened, and what was left out, but not
//    list the orders. Before the late send there is no record at all that the window was missed.
import type { Carry } from "./carry";
import { skipText } from "./carry";
import type { Plan } from "./multiday";

const ET = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** {day: "2026-11-23", time: "10:01"} in New York, or null for a time that cannot be read. */
function et(iso: string | null | undefined): { day: string; time: string } | null {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return null;
  const p = Object.fromEntries(ET.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

/** The ET time of a decision taken during its own fill session, or null for one taken on time. */
export function lateDecision(plan: Pick<Plan, "decidedAt" | "fillsAt">): string | null {
  const at = et(plan.decidedAt);
  return at && plan.fillsAt && at.day === plan.fillsAt && at.time >= "09:30" ? at.time : null;
}

/** The late send of this plan's decision, or null. */
export function lateSend(plan: Pick<Plan, "rule" | "fillsAt">, carries: Carry[]): Carry | null {
  const due = plan.fillsAt;
  return (due && carries.find((c) => c.rule === plan.rule && c.lateOf === due && c.session > due)) || null;
}

const short = (d: string) => new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(d + "T00:00:00Z"));

export interface PlanNote { key: string; rule: string; title: string; text: string; skipped: { symbol: string; text: string }[] }

/** What the panel says, one note per rule and state. `today` is the ET date. */
export function planNotes(plans: Plan[], carries: Carry[], today: string): PlanNote[] {
  return plans.flatMap((pl): PlanNote[] => {
    const out: PlanNote[] = [];
    const time = lateDecision(pl);
    if (time && pl.fillsAt && pl.fillsAt >= today)
      out.push({ key: `${pl.rule}-decision`, rule: pl.rule, title: `Decided late, at ${time} ET`,
        text: `This decision is normally taken after the close${pl.decidedAfter ? ` of ${short(pl.decidedAfter)}` : ""}. It was taken during the session instead. Its orders go out at this close as usual.`,
        skipped: [] });
    const c = lateSend(pl, carries);
    if (c && pl.fillsAt)
      out.push({ key: `${pl.rule}-send`, rule: pl.rule, title: `Sent late, at the close of ${short(c.session)}`,
        text: `The orders were due at the close of ${short(pl.fillsAt)} and were not sent then. They ${c.session === today ? "go" : "went"} out once, at the close of ${short(c.session)}, sized at that day's prices.`,
        skipped: Object.entries(c.skipped).map(([symbol, why]) => ({ symbol, text: skipText(why, pl.rule, pl.fillsAt).text })) });
    return out;
  });
}
