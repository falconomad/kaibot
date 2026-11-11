import { describe, expect, it } from "vitest";
import type { Evidence, Overview, Report, RoomAB, RoomSession, Run } from "./api";
import { coerce } from "./coerce";
import { budgetText, dayVsBenchmarks, evidenceVerdict, flagged, fmtR, levelPlan, liftInterval, packIndex, resolveEvidence, riskStatus,
  roomAbVerdict, roomOutcome, roomView, safeUrl, scanSummary } from "./derive";

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

const pack = { items: [
  { id: "n1", kind: "news", source: "Reuters", published_at: "2026-11-11T14:02:00Z", headline: "Chipmakers slide on export curbs",
    url: "https://example.com/a", n_outlets: "4", triage: { cause: "sector", materiality: "high", direction: "down", summary: "Sector-wide." } },
  { id: "n2", kind: "news", source: "PRN", published_at: "2026-11-11T13:00:00Z", headline: "<b>x</b>", url: "javascript:alert(1)" },
] };
const session = (over: Partial<RoomSession> = {}): RoomSession => ({
  signal_id: 7, ts: "2026-11-11T15:00:00Z", status: "done", verdict: "veto", rules_only: "allow", latency_ms: 21000, cost_usd: 0.05,
  error: null, counterfactual: null, evidence: pack,
  proposer: { thesis: "Sector dip", cause: "sector", evidence: [{ id: "n1", source: "Reuters", published_at: "x" }] },
  critic: { strongest_counter: "Guidance cut", cause: "company_news", red_flags: ["8-K", 3, null], evidence: [{ id: "n9", source: "Blog", published_at: "2026-11-11T12:00:00Z" }] },
  judge: { verdict: "veto", cause: "company_news", confidence: "high", invalidation: "reclaims VWAP", horizon_minutes: 45, evidence_ids: ["n1", "n9", "n1"] },
  ...over,
});

describe("evidence links (S35: linked, sourced)", () => {
  it("resolves a citation to its pack item", () => {
    const r = resolveEvidence([{ id: "n1", source: "ignored", published_at: "x" }], packIndex(pack));
    expect(r[0].item?.headline).toBe("Chipmakers slide on export curbs");
    expect(r[0].source).toBe("Reuters");
    expect(r[0].item?.n_outlets).toBe(4);
  });
  it("marks ids not in the pack as unverified, keeping the model's own source", () => {
    const r = resolveEvidence([{ id: "n9", source: "Blog", published_at: "t" }], packIndex(pack));
    expect(r[0].item).toBeNull();
    expect(r[0].source).toBe("Blog");
  });
  it("accepts judge evidence_ids as strings, dedupes, ignores junk", () => {
    const r = resolveEvidence(["n1", "n1", 5, null, { nope: 1 }, "n2"], packIndex(pack));
    expect(r.map((x) => x.id)).toEqual(["n1", "n2"]);
  });
  it("never links anything but http(s)", () => {
    expect(packIndex(pack).get("n2")?.url).toBeUndefined();
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("data:text/html,x")).toBeNull();
    expect(safeUrl("/relative")).toBeNull();
    expect(safeUrl("https://example.com/a")).toBe("https://example.com/a");
  });
  it("survives a missing or malformed pack", () => {
    expect(packIndex(null).size).toBe(0);
    expect(packIndex({ items: "nope" }).size).toBe(0);
  });
  it("builds a safe view and counts unverified citations", () => {
    const v = roomView(session());
    expect(v.proposer?.thesis).toBe("Sector dip");
    expect(v.critic?.redFlags).toEqual(["8-K", "3"]);
    expect(v.judge?.horizon).toBe(45);
    expect(v.unverified).toBe(2); // critic n9 and judge n9
    expect(v.cited.has("n1")).toBe(true);
  });
  it("handles a session with no transcript (timeout)", () => {
    const v = roomView(session({ status: "timeout", verdict: null, proposer: null, critic: null, judge: null }));
    expect(v.proposer).toBeNull();
    expect(v.unverified).toBe(0);
  });
});

describe("roomOutcome (S35 wording)", () => {
  it("explains a timeout", () => expect(roomOutcome("timeout", null).text).toBe("No trade: the room did not answer within 45 s."));
  it("explains a budget skip as rules-only", () =>
    expect(roomOutcome("skipped", null, "budget refused", "allow").text).toMatch(/^Rules-only: daily LLM budget reached\./));
  it("explains the room being off", () => expect(roomOutcome("skipped", null, "room disabled").text).toMatch(/switched off/));
  it("invalid and error fail closed", () => {
    expect(roomOutcome("invalid", null).text).toMatch(/^No trade/);
    expect(roomOutcome("error", null).tag).toBe("veto");
  });
  it("names the verdicts", () => {
    expect(roomOutcome("done", "downgrade").text).toMatch(/half size/);
    expect(roomOutcome("done", "veto").label).toBe("veto");
    expect(roomOutcome("done", "allow").tag).toBe("allow");
  });
});

const ab = (over: Partial<RoomAB> = {}): RoomAB => ({
  since: "2026-08-12T00:00:00Z", weeks: 13, n: 77, by_verdict: { veto: 40, allow: 30, downgrade: 4 }, by_status: { done: 74, timeout: 2, skipped: 1 },
  veto: { n: 40, n_cf: 40, mean_r: -0.2175, sd_r: 0.9961, cf_pnl: -217.5, n_loss_avoided: 27, loss_avoided: 607.5, n_win_missed: 13, win_missed: 390 },
  allow: { n: 30, n_closed: 30, mean_r: 0.15, sd_r: 0.9662, pnl: 112.5 },
  downgrade: { n: 4, n_paired: 4, pnl: -18.75, cf_pnl: -37.5, mean_r: -0.375 },
  no_answer: { n: 2, n_cf: 2, cf_pnl: 7.5 }, arms: { n: 77, rules_pnl: -420, room_pnl: 73.75 },
  lift: { n_veto: 40, n_allow: 30, diff: 0.3675, ci_lo: -0.096, ci_hi: 0.831 },
  spend: { llm_usd: 0.052, room_usd: 3.85, by_purpose: {}, per_loss_avoided: 0.0019 }, ...over,
});

describe("veto lift (S36)", () => {
  it("matches the SQL interval in kb_room_ab", () => {
    const l = liftInterval({ n: 30, mean: 0.15, sd: 0.9662 }, { n: 40, mean: -0.2175, sd: 0.9961 })!;
    expect(l.diff).toBeCloseTo(0.3675, 4);
    expect(l.lo).toBeCloseTo(-0.096, 3);
    expect(l.hi).toBeCloseTo(0.831, 3);
  });
  it("needs two on each side", () => expect(liftInterval({ n: 1, mean: 1, sd: 0 }, { n: 5, mean: 0, sd: 1 })).toBeNull());
  it("is too early to tell under 30 vetoes, even with a big lift", () => {
    const v = roomAbVerdict(ab({ veto: { ...ab().veto, n_cf: 12, mean_r: -2, sd_r: 0.1 } }));
    expect(v.text).toMatch(/^Too early to tell: 12 vetoed/);
    expect(v.tone).toBe("flat");
  });
  it("an interval spanning zero is not evidence", () => {
    const v = roomAbVerdict(ab());
    expect(v.text).toMatch(/includes zero/);
    expect(v.tone).toBe("flat");
  });
  it("says the vetoes help only when the whole interval is above zero", () =>
    expect(roomAbVerdict(ab({ veto: { ...ab().veto, mean_r: -0.9, sd_r: 0.5 } })).tone).toBe("gain"));
  it("says rules-only would have done better when vetoes beat allows", () =>
    expect(roomAbVerdict(ab({ veto: { ...ab().veto, mean_r: 1.2, sd_r: 0.5 } })).tone).toBe("loss"));
  it("handles an empty period", () => expect(roomAbVerdict(ab({ n: 0 })).text).toMatch(/No room sessions/));
});

describe("budgetText (S32)", () => {
  const caps = { daily_soft: 3, monthly_alert: 150, monthly_hard: 190 };
  it("ok", () => expect(budgetText({ state: "ok", alert: false, caps }).tone).toBe("ok"));
  it("rules-only past the daily cap", () => expect(budgetText({ state: "rules_only", alert: false, caps }).text).toMatch(/Rules-only.*\$3/));
  it("no entries at the hard cap", () => expect(budgetText({ state: "no_entries", alert: true, caps }).tone).toBe("breach"));
  it("warns past the monthly alert", () => expect(budgetText({ state: "ok", alert: true, caps }).text).toMatch(/\$150 alert/));
});

describe("scanSummary", () => {
  const base = { bars: 0, checks: 0, regime_off: 0, setups: 0, near: 0, vetoed: {}, to_room: 0, recent_near: [] };
  it("waits before the first bar", () => expect(scanSummary(base).headline).toMatch(/09:40 ET/));
  it("says no qualifying dip on an empty day", () => {
    const s = scanSummary({ ...base, bars: 12, checks: 708, near: 2 });
    expect(s.headline).toBe("No qualifying dip yet today");
    expect(s.lines).toContain("2 near-misses (a rebound formed but the dip didn't qualify)");
  });
  it("counts setups, vetoes and entries", () => {
    const s = scanSummary({ ...base, bars: 30, checks: 1770, setups: 3, vetoed: { "company news in 24 h": 2 } }, 1);
    expect(s.headline).toBe("3 setups found · 2 vetoed · 1 entered");
    expect(s.lines).toContain("2 vetoed: company news in 24 h");
  });
  it("handles a missing scan", () => expect(scanSummary(null).headline).toBe("No scan yet today"));
});
