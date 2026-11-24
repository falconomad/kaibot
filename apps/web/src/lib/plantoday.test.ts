// Product, 24 Nov 2026: only today's (or, before the open, the next session's) plan is shown; "today" is the New York
// session date; a past plan is one line on Activity (lib/plantoday.ts).
import { describe, expect, it } from "vitest";
import { parseBook } from "./book";
import type { Unfilled } from "./engineevents";
import { type Plan, type PlanOrder } from "./multiday";
import { carriesFrom } from "./carry";
import { carriedText, carriedToday, carryHold, carryHoldText, currentPlans, isCurrentPlan, lastPlan, sessionDate } from "./plantoday";

const buy = (symbol: string, state: PlanOrder["state"] = "canceled", now = 0): PlanOrder =>
  ({ symbol, action: "buy", sharesNow: now, sharesTarget: now + 10, why: "rank", state });
const plan = (fillsAt: string | null, orders: PlanOrder[] = [buy("APA")]): Plan =>
  ({ rule: "trend_momentum", decidedAfter: null, fillsAt, orders, sellSoon: [], note: null });
const expired = (symbol: string, filled = 0, session: string | null = "2026-11-18"): Unfilled =>
  ({ ts: "2026-11-18T21:01:00Z", rule: "trend_momentum", symbol, side: "buy", qty: 10, filled, left: 10 - filled, session });

describe("the session date is New York's", () => {
  it("00:30 in Berlin on Tue 24 Nov is still Mon 23 Nov in New York; 06:30 Berlin is Tue there", () => {
    expect(sessionDate(new Date("2026-11-23T23:30:00Z"))).toBe("2026-11-23"); // 00:30 CET Tue = 18:30 ET Mon
    expect(sessionDate(new Date("2026-11-24T04:59:00Z"))).toBe("2026-11-23"); // 05:59 CET = 23:59 ET Mon
    expect(sessionDate(new Date("2026-11-24T05:00:00Z"))).toBe("2026-11-24"); // 06:00 CET = 00:00 ET Tue
  });
  it("a plan for Tue is shown on Mon evening ET (Berlin already Tue), a plan for Mon still on Mon ET", () => {
    const today = sessionDate(new Date("2026-11-23T23:30:00Z"));
    expect(isCurrentPlan(plan("2026-11-24"), today)).toBe(true);
    expect(isCurrentPlan(plan("2026-11-23"), today)).toBe(true);
    // the next ET day: Monday's plan is over
    expect(isCurrentPlan(plan("2026-11-23"), sessionDate(new Date("2026-11-24T05:00:00Z")))).toBe(false);
  });
});

describe("which plan is shown", () => {
  const TODAY = "2026-11-24";
  it("a plan from a past session is hidden", () => {
    expect(isCurrentPlan(plan("2026-11-18"), TODAY)).toBe(false);
    expect(currentPlans([plan("2026-11-18")], TODAY)).toEqual([]);
  });
  it("today's plan is shown", () => { expect(currentPlans([plan(TODAY)], TODAY)).toHaveLength(1); });
  it("before the open (or on a weekend) the next session's plan is shown", () => {
    expect(isCurrentPlan(plan("2026-11-25"), TODAY)).toBe(true);
    expect(isCurrentPlan(plan("2026-11-30"), "2026-11-28")).toBe(true); // Saturday -> Monday's plan
  });
  it("a plan without a readable session date is not shown", () => {
    expect(isCurrentPlan(plan(null), TODAY)).toBe(false);
    expect(isCurrentPlan(plan("18 Nov"), TODAY)).toBe(false);
  });
});

describe("the last plan in one line", () => {
  const TODAY = "2026-11-24";
  const SYMS = Array.from({ length: 37 }, (_, i) => `S${i}`);
  it("18 Nov: 7 of 37 buys filled (some partly), 30 expired at the close", () => {
    const orders = SYMS.map((s, i) => buy(s, i < 5 ? "filled" : "canceled"));
    const unf = [...SYMS.slice(5, 7).map((s) => expired(s, 4)), ...SYMS.slice(7).map((s) => expired(s))];
    const l = lastPlan([plan("2026-11-18", orders)], unf, null, TODAY)!;
    expect(l).toMatchObject({ session: "2026-11-18", buys: 37, filled: 7, expired: 30 });
    expect(l.text).toBe("Last plan (18 Nov): 7 of 37 buys filled, 30 expired at the close");
  });
  it("without the expiry events: 'rest not filled', no number; a partial fill seen in the book counts as filled", () => {
    const book = parseBook({ run_id: 7, session: "2026-11-23", mtm: { equity: 1, equity_reported: 1 }, exposure: { by_rule: [] },
      reconciled: { ok: true, ts: null, positions: {}, cash_diff: 0 },
      positions: [{ rule: "trend_momentum", symbol: "B", qty: 3, avg_cost: 1, mark: 1, last: 1, opened_session: "2026-11-18", dividends: 0, stop: null }] }, 7);
    const l = lastPlan([plan("2026-11-18", [buy("A", "filled"), buy("B"), buy("C")])], [], book, TODAY)!;
    expect(l.text).toBe("Last plan (18 Nov): 2 of 3 buys filled, rest not filled");
  });
  it("some expiries known, some not: says both", () => {
    const l = lastPlan([plan("2026-11-18", [buy("A"), buy("B"), buy("C")])], [expired("A")], null, TODAY)!;
    expect(l.text).toBe("Last plan (18 Nov): 0 of 3 buys filled, 1 expired at the close, the rest not filled");
  });
  it("an expiry of another session does not count; today's plan is not a 'last plan'", () => {
    expect(lastPlan([plan("2026-11-18", [buy("A")])], [expired("A", 0, "2026-11-17")], null, TODAY)!.text)
      .toBe("Last plan (18 Nov): 0 of 1 buy filled, rest not filled");
    expect(lastPlan([plan(TODAY)], [], null, TODAY)).toBeNull();
    expect(lastPlan([], [], null, TODAY)).toBeNull();
  });
});

describe("buys carried into today (#366, QA on #620)", () => {
  const TODAY = "2026-11-24";
  const carry = (session: string, carried: [string, number, number][], of = "2026-11-18") => ({ type: "multi_day_carry", ts: `${session}T20:50:07Z`, run_id: 7,
    payload: { strategy: "trend_momentum", session, of, carried, skipped: {}, risk_off: false } });
  const p = plan("2026-11-18", [buy("APA"), buy("XOM"), buy("BBY")]);
  it("a carry event of today makes its buys current orders; one of an earlier session does not", () => {
    expect(carriedToday([p], carriesFrom([carry(TODAY, [["APA", 10, 0], ["XOM", 10, 4]])], 7), TODAY))
      .toEqual([{ rule: "trend_momentum", symbol: "APA", from: "2026-11-18", held: 0, target: 10, why: "rank" },
        { rule: "trend_momentum", symbol: "XOM", from: "2026-11-18", held: 4, target: 10, why: "rank" }]);
    expect(carriedToday([p], carriesFrom([carry("2026-11-23", [["APA", 10, 0]])], 7), TODAY)).toEqual([]);
    expect(carriedToday([p], carriesFrom([carry(TODAY, [["APA", 10, 0]], "2026-11-11")], 7), TODAY)).toEqual([]); // another decision's
    expect(carriedToday([plan(TODAY)], carriesFrom([carry(TODAY, [["APA", 10, 0]])], 7), TODAY)).toEqual([]); // today's plan is listed as itself
    expect(carriedText({ from: "2026-11-18" })).toBe("carried from 18 Nov, sent again at today's close");
  });
  it("carried buys are not counted as expired or not filled", () => {
    const c = carriedToday([p], carriesFrom([carry(TODAY, [["APA", 10, 0], ["XOM", 10, 0]])], 7), TODAY);
    const l = lastPlan([p], [expired("APA"), expired("XOM"), expired("BBY")], null, TODAY, c)!;
    expect(l).toMatchObject({ filled: 0, carried: 2, expired: 1 });
    expect(l.text).toBe("Last plan (18 Nov): 0 of 3 buys filled, 2 carried into today's close, 1 expired at the close");
  });
});

describe("carried buys on hold while the market filter is off (#621)", () => {
  const TODAY = "2026-11-24";
  const ev = (session: string, riskOff: boolean, carried: [string, number, number][] = [], of = "2026-11-18") => ({ type: "multi_day_carry",
    ts: `${session}T20:50:19Z`, run_id: 7, payload: { strategy: "trend_momentum", session, of, carried,
      skipped: riskOff ? { APA: "market filter off", XOM: "market filter off" } : {}, risk_off: riskOff } });
  const p = plan("2026-11-18", [buy("APA"), buy("XOM")]);
  it("filter off: the card says the carried buys are on hold, not 'sent again at today's close'", () => {
    // before today's near-close window the last carry-over (23 Nov) holds; today's, once it is there, too
    for (const evs of [[ev("2026-11-20", true), ev("2026-11-23", true)], [ev("2026-11-23", true), ev(TODAY, true)]]) {
      const c = carriesFrom(evs, 7);
      expect(carryHold([p], c, TODAY)).toBe("market filter off");
      expect(carriedToday([p], c, TODAY)).toEqual([]);
    }
    expect(carryHoldText("market filter off")).toBe("Carried buys on hold — market filter off");
  });
  it("filter on: no hold, and today's carried buys are sent again at today's close", () => {
    const c = carriesFrom([ev("2026-11-23", true), ev(TODAY, false, [["APA", 10, 0]])], 7);
    expect(carryHold([p], c, TODAY)).toBeNull();
    expect(carriedToday([p], c, TODAY).map((x) => carriedText(x))).toEqual(["carried from 18 Nov, sent again at today's close"]);
  });
  it("nothing to hold: no past plan, no short buy, no carry event, or another decision's event", () => {
    const off = carriesFrom([ev("2026-11-23", true)], 7);
    expect(carryHold([plan(TODAY)], off, TODAY)).toBeNull();
    expect(carryHold([plan("2026-11-18", [buy("APA", "filled", 10)].map((o) => ({ ...o, sharesTarget: 10 })))], off, TODAY)).toBeNull();
    expect(carryHold([p], [], TODAY)).toBeNull();
    expect(carryHold([p], carriesFrom([ev("2026-11-23", true, [], "2026-11-11")], 7), TODAY)).toBeNull();
  });
  it("QA on #626: after a late send the carry-overs name the late send's session (`of`); the newest one decides", () => {
    // the 18 Nov plan was sent late on 19 Nov with the filter off; the 20 Nov carry-over (of: 19 Nov) sends its buys
    const late = { ...ev("2026-11-19", true, [], "2026-11-18"), payload: { ...ev("2026-11-19", true, [], "2026-11-18").payload, late_of: "2026-11-18" } };
    const on = carriesFrom([late, ev("2026-11-20", false, [["APA", 10, 0]], "2026-11-19")], 7);
    expect(carryHold([p], on, "2026-11-23")).toBeNull();
    const off = carriesFrom([late, ev("2026-11-20", true, [], "2026-11-19")], 7);
    expect(carryHold([p], off, "2026-11-23")).toBe("market filter off");
    expect(carryHold([p], carriesFrom([late], 7), "2026-11-20")).toBe("market filter off"); // before 20 Nov's window
  });
  it("#627: after a late send, today's carry-over (of: the late send's session) is sent again at today's close", () => {
    const late = { ...ev("2026-11-19", true, [], "2026-11-18"), payload: { ...ev("2026-11-19", true, [], "2026-11-18").payload, late_of: "2026-11-18" } };
    const c = carriesFrom([late, ev("2026-11-20", false, [["APA", 10, 0]], "2026-11-19")], 7);
    expect(carriedToday([p], c, "2026-11-20").map((x) => [x.symbol, x.from, carriedText(x)]))
      .toEqual([["APA", "2026-11-18", "carried from 18 Nov, sent again at today's close"]]);
    // without the late send's event, a carry-over of another session's decision is still not this plan's
    expect(carriedToday([p], carriesFrom([ev("2026-11-20", false, [["APA", 10, 0]], "2026-11-19")], 7), "2026-11-20")).toEqual([]);
  });
  it("QA on #626: a weekend or a holiday reads the last session's carry-over; a new decision ends the hold", () => {
    const fri = carriesFrom([ev("2026-11-27", true)], 7);
    expect(carryHold([p], fri, "2026-11-28")).toBe("market filter off"); // Saturday
    expect(carryHold([p], carriesFrom([ev("2026-11-25", true)], 7), "2026-11-26")).toBe("market filter off"); // Thanksgiving
    expect(carryHold([p], carriesFrom([ev("2026-11-27", false)], 7), "2026-11-28")).toBeNull();
    // the next decision (kb_multiday_plan returns only the latest): on its fill session and the session after,
    // before that session's carry-over, the old plan's carry events say nothing
    const next = plan("2026-11-25", [buy("APA"), buy("XOM")]);
    expect(carryHold([next], fri, "2026-11-25")).toBeNull();
    expect(carryHold([next], carriesFrom([ev("2026-11-24", true)], 7), "2026-11-26")).toBeNull();
    expect(carryHold([next], carriesFrom([ev("2026-11-24", true), ev("2026-11-27", true, [], "2026-11-25")], 7), "2026-11-27")).toBe("market filter off");
  });
});
