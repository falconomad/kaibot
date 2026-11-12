import { describe, expect, it } from "vitest";
import { explainSignal } from "./explain";

const base = {
  kind: "signal" as const, ts: "2026-11-12T14:45:26Z", mode: "live" as const, strategy: "dip_reversal", symbol: "ABBV",
  entry: 297.43, stop: 294.94, target: 302.41, reasons: ["no-news dip", "5-min higher low", "sector-adjusted"],
  veto: null as string | null, approved: null as boolean | null, qty: null as number | null, binding: null as string | null,
  trade_id: null, counterfactual: null,
};

describe("explainSignal", () => {
  it("explains a news veto in plain words", () => {
    const e = explainSignal({ ...base, veto: "company news in 24 h" }, { taken: false });
    expect(e.outcome).toBe("Not bought: the company had news in the last 24 hours, and this rule only buys drops without news.");
    expect(e.saw).toBe("Dip rule: the stock fell sharply with no news, stopped falling in the last 5 minutes, fell more than its sector.");
    expect(e.detail).toContain("veto: company news in 24 h");
  });
  it("marks shadow rules as test only", () => {
    const e = explainSignal({ ...base, mode: "shadow", strategy: "orb_stocks_in_play", symbol: "NVDA",
      reasons: ["stock_in_play", "5m_range_up", "close_above_or_high"], binding: "position_cap" }, { taken: false });
    expect(e.outcome).toMatch(/^Test only/);
    expect(e.saw).toContain("broke above its first-5-minute high");
  });
  it("translates a failed risk check", () => {
    const e = explainSignal({ ...base, approved: false, binding: "buying_power" }, { taken: false });
    expect(e.outcome).toBe("Not bought: a risk check failed (there was not enough cash left).");
  });
  it("reports a buy", () => {
    expect(explainSignal({ ...base, approved: true, qty: 3 }, { taken: true }).outcome).toBe("Bought 3 shares.");
  });
  it("keeps unknown codes readable", () => {
    expect(explainSignal({ ...base, veto: "something new" }, { taken: false }).outcome).toBe("Not bought: something new.");
  });
});

import { CHECK_TEXT, EXIT_TEXT, RETIRED_TEXT, TEST_PHASE, bindingText, causeText, checkText, commandText, exitText, ruleLabel,
  ruleName, ruleStatus, vetoText } from "./explain";

describe("plain words for engine codes", () => {
  it("names the rules and marks the dip and SPY late-day rules as retired (decision 15)", () => {
    expect(ruleName("dip_reversal")).toBe("Dip rule");
    expect(ruleName("spy_late_momentum")).toBe("SPY late-day rule");
    expect(ruleStatus("dip_reversal")).toBe("retired");
    expect(ruleStatus("spy_late_momentum")).toBe("retired");
    expect(ruleLabel("dip_reversal")).toBe(`Dip rule (${RETIRED_TEXT})`);
    expect(RETIRED_TEXT).toBe("retired, systems test only");
    expect(ruleLabel("orb_stocks_in_play")).toBe("Opening-breakout rule (test only, never traded)");
    expect(ruleName("brand_new_rule")).toBe("brand new rule");
    expect(ruleStatus("brand_new_rule")).toBeNull();
  });
  it("covers every gate check and size limit in risk/gate.py", () => {
    for (const k of ["kill_switch", "reconciled", "market_open", "entry_window", "event_blackout", "data_ok", "pump_risk", "symbol_halted",
      "duplicate_symbol", "entries_today", "run_drawdown_pct", "quote_age_s", "no_short", "stop_side", "daily_loss_pct", "max_positions",
      "qty_positive", "position_pct", "open_risk_pct"]) expect(CHECK_TEXT[k], k).toBeTruthy();
    expect(checkText("stop_side")).toBe("Stop is below the buy price");
    expect(bindingText("position_cap")).toBe("the largest-position cap");
    expect(bindingText(null)).toBe("—");
  });
  it("covers every exit reason the engine writes", () => {
    for (const k of ["stop", "target", "trail", "time", "moc", "manual", "daily_stop", "run_drawdown", "carried_over"]) expect(EXIT_TEXT[k], k).toBeTruthy();
    expect(exitText(null)).toBe("open");
    expect(exitText("new_reason")).toBe("new reason");
  });
  it("translates commands, causes and vetoes", () => {
    expect(commandText("flatten_and_stop")).toBe("sell everything and stop");
    expect(causeText("company_news")).toBe("company news");
    expect(causeText(null)).toBe("—");
    expect(vetoText("company news in 24 h")).toMatch(/had news in the last 24 hours/);
  });
  it("links the test phase to the Engine v3 plan in one sentence", () => {
    expect(TEST_PHASE.url).toBe("https://claude.ai/artifact/VSQymBFJQznrFhw5W3Gu7Q");
    expect(TEST_PHASE.text.split(/[.!?](\s|$)/).filter((x) => x && x.trim()).length).toBe(1);
  });
});
