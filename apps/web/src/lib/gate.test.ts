import { describe, expect, it } from "vitest";
import type { GateStrategy } from "./api";
import src from "../pages/Gate.tsx?raw";
import { gateOverallText, gateRows, gateStatusText } from "./derive";

const crit = (met: boolean | null, value: number | null = 1, limit: number | null = 0) => ({ value, limit, met });
const st = (over: Partial<GateStrategy> = {}): GateStrategy => ({
  status: "provisional", kills: [], n_trades: 120, excluded_older_code: 0, pnl_usd: 42, mean_r_pct: 0.1, sessions: 30,
  random_days: 30, random_missing_days: [], n_trials: 4, trades_to_go: 780,
  criteria: { trades: crit(false, 120, 900), months: crit(false, 1.2, 3), t_stat: crit(true, 3.4, 3), deflated_sharpe: crit(true, 0.7, 0.5),
    random_pct: crit(null, null, 95), sharpe_vs_spy: crit(true, 1.4, 0.9), drawdown: crit(true, 0.02, 0.12) },
  ...over,
});

describe("gate rows", () => {
  it("never shows a pass before the sample size is reached", () => {
    const rows = gateRows(st());
    expect(rows.find((r) => r.key === "t_stat")!.state).toBe("provisional");
    expect(rows.find((r) => r.key === "drawdown")!.state).toBe("pass"); // a drawdown inside the cap is a fact, not a claim
    expect(rows.find((r) => r.key === "random_pct")!.state).toBe("unknown");
    expect(rows.some((r) => r.state === "pass" && r.key !== "drawdown")).toBe(false);
    expect(rows.find((r) => r.key === "trades")!.state).toBe("pending"); // slate "not yet", never red while provisional
  });
  it("shows red only for a kill or a real fail", () => {
    expect(gateRows(st({ status: "kill" })).find((r) => r.key === "trades")!.state).toBe("fail");
  });
  it("shows passes once the strategy has cleared the bar", () => {
    const rows = gateRows(st({ status: "pass" }));
    expect(rows.find((r) => r.key === "t_stat")!.state).toBe("pass");
  });
  it("formats values and limits", () => {
    const r = gateRows(st()).find((x) => x.key === "drawdown")!;
    expect([r.value, r.limit]).toEqual(["2.0%", "≤ 12%"]);
  });
  it("labels", () => {
    expect(gateStatusText("provisional").label).toBe("Provisional: can only kill");
    expect(gateStatusText("kill").tone).toBe("fail");
    expect(gateOverallText("discuss")).toMatch(/Nothing switches automatically/);
  });
});

describe("S41: no live button", () => {
  it("the go/no-go page has no buttons, forms or commands at all", () => {
    expect(src).not.toMatch(/<button|<form|onClick|api\.command|newRun/);
  });
  it("says so", () => expect(src).toMatch(/There is no go-live button/));
});
