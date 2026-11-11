import { describe, expect, it } from "vitest";
import type { Evidence, Overview, Report, Run } from "./api";
import { coerce } from "./coerce";
import { dayVsBenchmarks, evidenceVerdict, flagged, fmtR, levelPlan, riskStatus } from "./derive";

const run = (over: Partial<Run> = {}): Run => ({
  id: 2, created_at: "2026-11-10T16:29:16Z", amount_usd: 5000, status: "running", end_reason: null, peak_equity: 5000,
  kind: "paper", label: "Paper · Hunter · $5,000", level: "aggressive", started_on: "2026-11-10", ended_on: null, ...over,
});
const ov = (over: Partial<Overview> = {}): Overview => ({
  run: run(), daily: [], heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail: { state: "trading" } }],
  pending_commands: [], ledger: null, today: null, segments: [], halts_today: [], ...over,
});
const ev = (over: Partial<Evidence>): Evidence => ({
  n: 0, pnl: 0, mean_r: null, sd_r: null, t: null, ci_lo: null, ci_hi: null, sessions: 0, first_session: null, by_strategy: [], ...over,
});

describe("evidenceVerdict (S29)", () => {
  it("says nothing with no trades", () => expect(evidenceVerdict(ev({})).text).toMatch(/No closed trades/));
  it("refuses an interval under 30 trades", () => expect(evidenceVerdict(ev({ n: 12, ci_lo: -1, ci_hi: 1 })).text).toMatch(/too few/));
  it("an interval spanning zero is not evidence", () => {
    const v = evidenceVerdict(ev({ n: 187, ci_lo: -0.17, ci_hi: 0.13 }));
    expect(v.text).toMatch(/includes zero/);
    expect(v.tone).toBe("flat");
    expect(v.frac).toBeCloseTo(187 / 900);
  });
  it("above zero is still not the go-live bar", () => expect(evidenceVerdict(ev({ n: 400, ci_lo: 0.01, ci_hi: 0.2 })).text).toMatch(/t > 3/));
  it("below zero says it loses", () => expect(evidenceVerdict(ev({ n: 100, ci_lo: -0.3, ci_hi: -0.05 })).tone).toBe("loss"));
  it("caps progress at the bar", () => expect(evidenceVerdict(ev({ n: 2000, ci_lo: -1, ci_hi: 1 })).frac).toBe(1));
});

describe("riskStatus (S28: red on breach)", () => {
  it("is ok on a normal day", () => expect(riskStatus(run(), ov(), -0.5, 1).severity).toBe("ok"));
  it("warns near the daily stop", () => expect(riskStatus(run(), ov(), -2.5, 1).severity).toBe("warn"));
  it("is red at the daily stop", () => {
    const s = riskStatus(run(), ov(), -3.0, 1);
    expect(s.severity).toBe("breach");
    expect(s.items.join()).toMatch(/Daily stop/);
  });
  it("is red at the drawdown cap", () => expect(riskStatus(run(), ov(), 0, 12).severity).toBe("breach"));
  it("is red on a safety halt", () =>
    expect(riskStatus(run(), ov({ halts_today: [{ kind: "drift", reason: "x", ts: "2026-11-11T15:00:00Z" }] }), 0, 0).severity).toBe("breach"));
  it("is red when the engine says the account isn't clean", () =>
    expect(riskStatus(run(), ov({ heartbeat: [{ process: "engine", ts: "", detail: { account_clean: false } }] }), 0, 0).severity).toBe("breach"));
  it("only warns for a paused run or a watchdog note", () => {
    expect(riskStatus(run({ status: "paused" }), ov(), 0, 0).severity).toBe("warn");
    expect(riskStatus(run(), ov({ halts_today: [{ kind: "watchdog", reason: "x", ts: "" }] }), 0, 0).severity).toBe("warn");
  });
});

describe("levelPlan (S23: next session)", () => {
  const segs = [{ level: "aggressive" as const, from_session: "2026-11-10" }, { level: "balanced" as const, from_session: "2026-11-12" }];
  it("keeps today's level and shows the scheduled one", () => {
    const p = levelPlan(run(), segs, "2026-11-11");
    expect(p.current).toBe("aggressive");
    expect(p.next?.level).toBe("balanced");
  });
  it("applies it from its session on", () => expect(levelPlan(run(), segs, "2026-11-12")).toEqual({ current: "balanced", next: null }));
});

describe("dayVsBenchmarks (S30)", () => {
  it("compares the day with SPY and cash in % of the starting pot", () => {
    const r: Report = { session: "2026-11-11", sessions: [], start_equity: 5000,
      day: { pnl: 25, pnl_venue: 26, pnl_shadow: 25, spy_ret: 0.003, cash_ret: 0.00016, trades: 2 } };
    const d = dayVsBenchmarks(r);
    expect(d.potPct).toBeCloseTo(0.5);
    expect(d.spyPct).toBeCloseTo(0.3);
    expect(d.beatSpy && d.beatCash).toBe(true);
  });
});

describe("flagged (S31)", () => {
  it("puts kill flags first, then the biggest losers", () => {
    const g = flagged([
      { dim: "hour_et", key: "14:00", n: 12, pnl: -50, mean_r: -0.1, win: 0.4, t: -0.8, flag: "watch" },
      { dim: "exit_reason", key: "stop", n: 90, pnl: -900, mean_r: -1, win: 0, t: -9, flag: "kill" },
      { dim: "hour_et", key: "10:00", n: 15, pnl: -120, mean_r: -0.2, win: 0.3, t: -1, flag: "watch" },
      { dim: "strategy", key: "dip_reversal", n: 187, pnl: 700, mean_r: 0.01, win: 0.5, t: 0.2, flag: null },
    ]);
    expect(g.map((x) => x.key)).toEqual(["stop", "10:00", "14:00"]);
  });
});

describe("coerce / fmtR", () => {
  it("turns Postgres numeric strings into numbers, deeply", () => {
    const x = coerce({ n: 3, pnl: "12.50", rows: [{ qty: "4.0000", symbol: "AAPL" }], counterfactual: { r_multiple: "-1.2" } });
    expect(x).toEqual({ n: 3, pnl: 12.5, rows: [{ qty: 4, symbol: "AAPL" }], counterfactual: { r_multiple: -1.2 } });
  });
  it("formats R", () => {
    expect(fmtR(0.1234)).toBe("+0.12R");
    expect(fmtR(-1)).toBe("−1.00R");
    expect(fmtR(null)).toBe("—");
  });
});
