import { describe, expect, it } from "vitest";
import type { Halt } from "./api";
import { ruleName } from "./explain";
import { experimentHaltText, experimentView, experimentsFrom, findExperiment, haltRule, haltTone, multiDayFrom, noDecisionText,
  parseRetired, retirementUndone } from "./experiment";

// The heartbeat detail as #205 (risk/profiles.experiments) and #207 (LiveDaily.status) send it.
const EXP_ROW = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, decided: "2026-11-13", decision: "docs/decisions/15_engine_v3.md section 11", level: "balanced",
  max_exposure_pct: 60, retire_drawdown_pct: 21 };

describe("experimentsFrom / experimentView (#210)", () => {
  it("reads the heartbeat's label rows and says them plainly", () => {
    const [e] = experimentsFrom({ experiments: [EXP_ROW] });
    const v = experimentView(e);
    expect(v.text).toBe("Experiment — failed the ten-year test · paper only");
    expect(v.cap).toBe("up to 60% invested");
    expect(v.stop).toBe("retires if it falls 21% from its peak");
    expect(v.tip).toMatch(/did not pass the ten-year test/);
    expect(v.tip).toMatch(/never trades real money/);
  });
  it("other levels: 30% / 13% and 100% / 27%", () => {
    const [e] = experimentsFrom({ experiments: [{ ...EXP_ROW, level: "conservative", max_exposure_pct: 30, retire_drawdown_pct: 13 }] });
    expect(experimentView(e)).toMatchObject({ cap: "up to 30% invested", stop: "retires if it falls 13% from its peak" });
  });
  it("finds a neighbour setting by its rule", () => {
    const list = experimentsFrom({ experiments: [EXP_ROW] });
    expect(findExperiment("trend_momentum~a", list)?.strategy).toBe("trend_momentum");
    expect(findExperiment("dip_reversal", list)).toBeNull();
  });
  it("absent or malformed fields: nothing (old engines, flag off)", () => {
    expect(experimentsFrom(null)).toEqual([]);
    expect(experimentsFrom({ state: "trading" })).toEqual([]);
    expect(experimentsFrom({ experiments: "x" })).toEqual([]);
    expect(experimentsFrom({ experiments: [{ strategy: "trend_momentum" }, 3, null] })).toEqual([]);
    expect(multiDayFrom({ state: "trading" })).toBeNull();
  });
  it("a missing cap or trigger leaves that line out", () => {
    const [e] = experimentsFrom({ experiments: [{ ...EXP_ROW, max_exposure_pct: null, retire_drawdown_pct: undefined }] });
    expect(experimentView(e)).toMatchObject({ cap: null, stop: null });
  });
});

describe("the daily path's notes (#207 heartbeat multi_day)", () => {
  it("parses the block", () => {
    const md = multiDayFrom({ multi_day: { book: "open", rules: { trend_momentum: { last_decision: "2026-11-12",
      note: "no decision today: daily store not refreshed (see logs/fetch_daily.log) (store ends 2026-11-11)" } } } });
    expect(md?.bookEnded).toBe(false);
    expect(md?.rules[0]).toMatchObject({ rule: "trend_momentum", retired: false, lastDecision: "2026-11-12" });
    expect(noDecisionText(md!.rules[0].note!)).toBe("No decision today: the daily prices weren't refreshed after the last close, so the rule made no new choice.");
  });
  it("the other no-decision notes", () => {
    expect(noDecisionText("no decision yet: the first one is taken after a close")).toMatch(/first choice after a market close/);
    expect(noDecisionText("no decision today: none was taken after the last close (engine off then?)")).toMatch(/engine off then/);
    expect(noDecisionText("something new")).toBe("Something new");
  });
  it("reads a retirement note", () => {
    expect(parseRetired("trend_momentum retired: book fell 21.3% from its peak on 2026-11-20")).toEqual({ fellPct: 21.3, session: "2026-11-20" });
    expect(parseRetired("trend_momentum retired: book reached its drawdown end on None")).toEqual({ fellPct: null, session: null });
  });
});

const halt = (kind: string, reason: string, ts = "2026-11-20T15:00:00Z"): Halt => ({ kind, reason, ts, session: "2026-11-20" });
const RETIRED = halt("multi_day_retired", "rule=trend_momentum; trend_momentum retired: book fell 21.3% from its peak on 2026-11-20");
const BOOK = halt("book_drawdown", "multi-day book drawdown 21.3% reached its end of 21% (peak 10,412.00, now 8,194.24): no new multi-day trades in this run, the book is sold at market");
const BACK = halt("multi_day_reenabled", "rule=trend_momentum; re-enabled by the stakeholder (KAIBOT_REENABLE)", "2026-11-21T13:00:00Z");

describe("#207 halt rows", () => {
  it("tones: the experiment's end is not a breach", () => {
    expect(haltTone("multi_day_retired")).toBe("ended");
    expect(haltTone("book_drawdown")).toBe("ended");
    expect(haltTone("multi_day_reenabled")).toBe("info");
    expect(haltTone("drift")).toBe("breach");
    expect(haltTone("daily_stop")).toBe("breach");
    expect(haltTone("watchdog")).toBe("note");
  });
  it("plain text with reason and date", () => {
    expect(haltRule(RETIRED)).toBe("trend_momentum");
    expect(experimentHaltText(RETIRED, ruleName)).toBe(
      "Experiment ended: Trend momentum retired: its book fell 21.3% from its peak on Nov 20, 2026. It trades no more until you switch it back on.");
    expect(experimentHaltText(BOOK, ruleName)).toBe(
      "Experiment ended: the multi-day book fell 21.3% from its peak (stop line 21%); its shares are sold, the day rules go on");
    expect(experimentHaltText(BACK, ruleName)).toBe("Trend momentum switched back on by you");
    expect(experimentHaltText(halt("drift", "x"), ruleName)).toBeNull();
  });
  it("a later re-enabling undoes the retirement", () => {
    expect(retirementUndone(RETIRED, [RETIRED, BACK])).toBe(true);
    expect(retirementUndone(RETIRED, [RETIRED])).toBe(false);
    expect(retirementUndone(RETIRED, [RETIRED, { ...BACK, ts: "2026-11-19T00:00:00Z" }])).toBe(false);
  });
});
