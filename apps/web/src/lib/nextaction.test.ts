// #604 "Next action" (ui_after_engine_v3 N5) and #645's late daily prices (lib/nextaction.ts).
import { describe, expect, it } from "vitest";
import { dataLate, dayText, entryBlock, engineTrading, lateText, nextAction, nextDecisionFill, sessionsFrom, type NextInput } from "./nextaction";

const at = (iso: string) => Date.parse(iso);
const MORNING = at("2026-11-25T14:00:00Z");  // Wed 25 Nov, 09:00 New York (before the open)
const NOON = at("2026-11-25T17:40:00Z");     // 12:40 New York
const EVENING = at("2026-11-25T22:00:00Z");  // 17:00 New York, after the close
// kb_market_notices' sessions on 25 Nov 2026: Thanksgiving (Thu 26) is closed
const CAL = ["2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03"];
const LIVE = { live: true, asleep: false };
const run = { kind: "paper", status: "running" };

const detail = (rule: Record<string, unknown>, extra: Record<string, unknown> = {}, md: Record<string, unknown> = {}) =>
  ({ state: "trading", ...extra, multi_day: { book: "open", rules: { trend_momentum: { last_decision: "2026-11-24", ...rule } }, ...md } });
const next = (d: unknown, over: Partial<NextInput> = {}) => nextAction({ run, detail: d, engine: LIVE, calendar: CAL, held: 7, now: NOON, ...over });

describe("the calendar", () => {
  it("the notices' sessions first, then weekdays after them; without them weekdays from the next open", () => {
    expect(sessionsFrom("2026-11-25", CAL).slice(0, 8)).toEqual([...CAL, "2026-12-04", "2026-12-07"]);
    expect(sessionsFrom("2026-11-26", null, "2026-11-27T09:30:00-05:00").slice(0, 2)).toEqual(["2026-11-27", "2026-11-30"]);
    expect(sessionsFrom("2026-11-28", null).slice(0, 2)).toEqual(["2026-11-30", "2026-12-01"]); // a Saturday: weekdays
  });
  it("trend_momentum's next decision fills on the first session of the next week on Wednesday or later", () => {
    const s = sessionsFrom("2026-11-25", CAL);
    expect(nextDecisionFill("trend_momentum", "2026-11-25", s)).toBe("2026-12-02");
    expect(nextDecisionFill("trend_momentum~a", "2026-11-25", s)).toBe("2026-12-02");
    expect(nextDecisionFill("mom_12_1_band", "2026-11-25", s)).toBe("2026-12-01");
    expect(nextDecisionFill("dip_reversal", "2026-11-25", s)).toBeNull();
  });
  it("days read 'Wed 25 Nov'", () => { expect(dayText("2026-11-25")).toBe("Wed 25 Nov"); });
});

describe("the next action, from the standing targets (multi_day.rules.<rule>.standing)", () => {
  it("a decision due: its orders go out on the fill session at 15:50 ET", () => {
    const due = { standing: { state: "due", decided: "2026-11-24", fill_session: "2026-11-25" } };
    expect(next(detail(due))).toBe("Next: orders go out today at 15:50 ET");
    expect(next(detail(due), { now: MORNING })).toBe("Next: orders go out today at 15:50 ET");
    expect(next(detail({ standing: { state: "due", fill_session: "2026-11-27" } }))).toBe("Next: orders go out on Fri 27 Nov at 15:50 ET");
  });
  it("a due decision whose window has passed goes out late, at the next session's window", () => {
    const due = { standing: { state: "due", fill_session: "2026-11-25" } };
    expect(next(detail(due), { now: EVENING })).toBe("Next: orders go out late, on Fri 27 Nov at 15:50 ET");
  });
  it("buys still short after the send: carried at the next window, or on hold while the market filter is off", () => {
    const sent = { standing: { state: "sent", fill_session: "2026-11-24", sent_on: "2026-11-24", short: ["XOM", "TMO"] } };
    expect(next(detail(sent))).toBe("Next: 2 short buys sent again today at 15:50 ET");
    expect(next(detail(sent), { now: EVENING })).toBe("Next: 2 short buys sent again on Fri 27 Nov at 15:50 ET");
    const carry = (risk_off: boolean, session = "2026-11-25") =>
      ({ type: "multi_day_carry", ts: `${session}T20:51:00Z`, payload: { strategy: "trend_momentum", session, of: "2026-11-24", carried: [], skipped: {}, risk_off } });
    expect(next(detail(sent), { events: [carry(true)] })).toBe("Next: carried buys on hold, market filter off");
    expect(next(detail(sent), { events: [carry(true, "2026-11-24")] })).toBe("Next: 2 short buys sent again today at 15:50 ET"); // the fill day itself
    expect(next(detail(sent), { events: [carry(true), { ...carry(false), ts: "2026-11-25T20:55:00Z" }] })).toBe("Next: 2 short buys sent again today at 15:50 ET");
    expect(next(detail({ ...sent, note: "carried buys skipped: market filter off" }))).toBe("Next: carried buys on hold, market filter off");
  });
  it("nothing owed: the stops at the open while the market is closed, the next decision while it is open", () => {
    const met = { standing: { state: "met", fill_session: "2026-11-25", sent_on: "2026-11-25", short: [] } };
    expect(next(detail(met))).toBe("Next: next decision, orders on Wed 2 Dec at 15:50 ET");
    expect(next(detail(met), { now: EVENING })).toBe("Next: stops checked at the open, Fri 27 Nov 09:30 ET");
    expect(next(detail(met), { now: MORNING })).toBe("Next: stops checked at the open, Wed 25 Nov 09:30 ET");
    expect(next(detail(met), { now: EVENING, held: 0 })).toBe("Next: next decision, orders on Wed 2 Dec at 15:50 ET");
  });
  it("waiting for the evening's prices, or a session without its decision yet", () => {
    expect(next(detail({ waiting: "daily store not refreshed yet", standing: { state: "met", fill_session: "2026-11-25" } }), { now: EVENING }))
      .toBe("Next: decision once the daily prices are in");
    expect(next(detail({ standing: { state: "undecided" } }))).toBe("Next: decision once the daily prices are in");
  });
});

describe("what comes first, and when there is nothing to say", () => {
  const due = { standing: { state: "due", fill_session: "2026-11-25" } };
  it("the engine's own entry block and trading state (#446) when the heartbeat carries them", () => {
    expect(next(detail(due, { entry_block: "lease unverified" }))).toBe("Next: orders go out today at 15:50 ET; buys on hold, lease unverified");
    expect(next(detail(due, { entry_block: [{ code: "data_check", text: "data check daily_bar_stale XOM" }] })))
      .toBe("Next: orders go out today at 15:50 ET; buys on hold, data check daily_bar_stale XOM");
    expect(next(detail(due, { trading_state: { state: "halted", why: "daily stop" } }))).toBe("Next: nothing new, trading is halted (daily stop)");
    expect(next(detail(due, { trading_state: "reducing_only" }))).toBe("Next: orders go out today at 15:50 ET; buys on hold, buys blocked");
    expect(next(detail(due, { trading_state: "active", entry_block: null }))).toBe("Next: orders go out today at 15:50 ET");
    expect(entryBlock({ entry_block: [] })).toBeNull();
    expect(engineTrading({ trading_state: "sideways" })).toBeNull();
  });
  it("late daily prices come before the standing targets", () => {
    expect(next(detail(due, {}, { data_late: [{ session: "2026-11-24", deadline: "2026-11-25T03:30:00-05:00", rules: ["trend_momentum"] }] })))
      .toBe("Next: decision once the prices for Tue 24 Nov arrive");
  });
  it("an engine that should run and does not; a paused run", () => {
    expect(next(detail(due), { engine: { live: false, asleep: false } })).toBe("Next: nothing new until the engine runs again");
    expect(next(detail(due), { engine: { live: false, asleep: true } })).toBe("Next: orders go out today at 15:50 ET");
    expect(next(detail(due), { run: { kind: "paper", status: "paused" } })).toBe("Next: no new buys while the run is paused; stops stay at the broker");
  });
  it("nothing: a replay, an ended run, a damaged journal, no multi-day book, its end, every rule retired", () => {
    expect(next(detail(due), { run: { kind: "replay", status: "ended" } })).toBeNull();
    expect(next(detail(due), { run: { kind: "paper", status: "ended" } })).toBeNull();
    expect(next(detail(due), { engine: { live: false, asleep: false, journal: true } })).toBeNull();
    expect(next({ state: "trading" })).toBeNull();
    expect(next(detail(due, {}, { book: "ended" }))).toBeNull();
    expect(next(detail({ retired: true, ...due }))).toBeNull();
  });
  it("an older engine without standing targets: only the stops at the open, when closed", () => {
    expect(next(detail({}))).toBeNull();
    expect(next(detail({}), { now: EVENING })).toBe("Next: stops checked at the open, Fri 27 Nov 09:30 ET");
  });
  it("untrusted fields are read defensively", () => {
    expect(next(detail({ standing: { state: "due", fill_session: "<b>x</b>" } }), { now: EVENING })).toBe("Next: stops checked at the open, Fri 27 Nov 09:30 ET");
    expect(next(detail({ standing: "due" }))).toBeNull();
    expect(next({ multi_day: { rules: [1, 2] } })).toBeNull();
  });
});

describe("late daily prices (#645: heartbeat multi_day.data_late, event multi_day_data_late)", () => {
  const ev = (session: string, strategy = "trend_momentum") =>
    ({ type: "multi_day_data_late", ts: "2026-11-25T08:30:00Z", payload: { session, strategy, deadline: "2026-11-25T03:30:00-05:00", why: "store not refreshed" } });
  it("the heartbeat's list, newest session first", () => {
    expect(dataLate({ data_late: [{ session: "2026-11-23", rules: ["trend_momentum"] }, { session: "2026-11-24", rules: [] }] }))
      .toEqual([{ session: "2026-11-24", rules: [] }, { session: "2026-11-23", rules: ["trend_momentum"] }]);
    expect(dataLate({ data_late: [{ session: "yesterday" }, null] })).toEqual([]);
  });
  it("an event counts until the rule has decided on that session", () => {
    expect(dataLate({ rules: { trend_momentum: { last_decision: "2026-11-23" } } }, [ev("2026-11-24")])).toEqual([{ session: "2026-11-24", rules: ["trend_momentum"] }]);
    expect(dataLate({ rules: { trend_momentum: { last_decision: "2026-11-24" } } }, [ev("2026-11-24")])).toEqual([]);
    expect(dataLate(null, [{ ...ev("2026-11-24"), type: "multi_day_waiting" }])).toEqual([]);
  });
  it("in plain words", () => {
    expect(lateText({ session: "2026-11-24", rules: [] })).toBe("The prices for Tue 24 Nov haven't arrived; kaibo is waiting");
  });
});
