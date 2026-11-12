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
