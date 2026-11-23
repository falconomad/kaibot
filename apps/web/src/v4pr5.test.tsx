// v4 PR 5 (docs/design/mobile_first_v4.md, "Engine", "Go / no-go", "Report", "Trade detail") and the Activity trim.
import { describe, expect, it, vi } from "vitest";
import type { GateResp, Health as HealthResp, Heartbeat, LlmBudget, Report as ReportResp, TapeItem, TradeDetail } from "./lib/api";
import { Health } from "./pages/Health";
import { Gate } from "./pages/Gate";
import { Report } from "./pages/Report";
import { Tape } from "./pages/Tape";
import { TradeDetailPage } from "./pages/TradeDetail";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const run = paperRun();
const now = new Date().toISOString();
const EXP = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
const hb: Heartbeat[] = [{ process: "engine", ts: now, detail: { state: "trading", host: "mac", account_clean: true, experiments: [EXP] } as Heartbeat["detail"] },
  { process: "watchdog", ts: now, detail: { state: "watching" } }];
const ctx = ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: hb }, risk: { severity: "breach", items: ["Engine halted entries for today"] } });
const health: HealthResp = { now, heartbeat: hb, halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } };
const budget: LlmBudget = { day: "2026-11-16", day_usd: 0, month_usd: 0, state: "ok", alert: false,
  caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {} };

describe("Engine: status first", () => {
  it("the status, the halt row, four safety checks, today, and Technical details behind one row", () => {
    const html = render(<Health />, { ctx, seeds: [{ key: ["health"], data: health }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    // N1: the trading-state line sits under the headline (this heartbeat has no `lease`: not "Buying", said honestly)
    expect(t).toMatch(/^Engine Running Checked in \d+ s ago Trading: Holding — lease not reported · reason not reported Engine halted entries for today/);
    expect(t).toContain("Safety checks Engine trading · mac OK Watchdog watching");
    expect(t).toContain("Safety net Armed");
    expect(t).toContain("Paper account Clean");
    expect(t).toContain("Today Today's scan No scan yet today Trend momentum Experiment: failed the ten-year test Go / no-go");
    expect(html).toMatch(/<details class="acc"><summary><span class="acc-title">Technical details<\/span>/);
  });
});

describe("Go / no-go: the verdict first", () => {
  const st = { status: "provisional", kills: [], n_trades: 37, excluded_older_code: 0, pnl_usd: 12, mean_r_pct: null, sessions: 5, random_days: 0,
    random_missing_days: null, n_trials: 1, trades_to_go: 863,
    criteria: { trades: { value: 37, limit: 900, met: false }, t_stat: { value: 0.4, limit: 3, met: false } } };
  const resp = { latest: { id: 1, ts: now, session: "2026-11-13", bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", overall: "keep collecting",
    report: { generated_at: now, bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", frozen_since: null, overall: "keep collecting", bar: {}, kill: {},
      strategies: { dip_reversal: st } } }, history: [], random_days: 0 } as unknown as GateResp;
  it("one sentence, trades against 900, no go-live button; each rule's bar as a checklist in an accordion", () => {
    const html = render(<Gate />, { ctx, seeds: [{ key: ["gate", 1], data: resp }] });
    const t = text(html);
    expect(t).toMatch(/^Go \/ no-go The verdict Keep collecting\. No strategy has cleared the bar yet\. Trades so far 37 \/ 900 There is no go-live button\./);
    expect(html).toMatch(/<details class="acc"><summary><span class="acc-title"><span title="code: dip_reversal">Dip rule/);
    expect(t).toContain("not yet Trades on frozen code now 37 · bar ≥ 900");
    expect(html).toContain("How the bar was fixed");
    expect(html).not.toContain("<table");
  });
});

describe("Report: the day in one line", () => {
  const report: ReportResp = { session: "2026-11-13", sessions: ["2026-11-13"], start_equity: 10000,
    day: { pnl: 25, pnl_venue: 26, pnl_shadow: 25, spy_ret: 0.003, cash_ret: 0.00016, trades: 1 },
    trades: [{ id: 7, strategy: "dip_reversal", symbol: "AMD", entry_ts: "2026-11-13T15:00:00Z", exit_ts: "2026-11-13T19:00:00Z", qty: 2, entry_px: 150,
      exit_px: 162.5, pnl_usd: 25, risk_usd: 10, exit_reason: "target" }] };
  it("P&L against the S&P 500 and cash, the trades as rows, the rest in accordions", () => {
    const html = render(<Report />, { ctx, seeds: [{ key: ["report", 1, undefined], data: report }] });
    const t = text(html);
    expect(t).toContain("+$25.00 · +0.25% , ahead of cash, behind the S&P 500. S&P 500 +0.30% · cash +0.016%");
    expect(t).toContain("AMD Dip rule · target reached");
    for (const a of ["More about this day", "Evidence counter", "What worked, and what did not", "Agent room", "Chapters", "How to read this"])
      expect(html, a).toContain(`<span class="acc-title">${a}</span>`);
  });
});

describe("Trade detail: the result first, detail in accordions", () => {
  const d = { trade: { id: 7, run_id: 1, signal_id: null, strategy: "trend_momentum", symbol: "AAPL", entry_ts: "2026-11-13T15:00:00Z",
    exit_ts: "2026-11-13T19:00:00Z", qty: 2, entry_px: 150, exit_px: 140, stop_px: 140, target_px: 170, risk_usd: 20, exit_reason: "stop", pnl_usd: -20, verdict: null },
    run, gate: null, room: null, fills: [] } as unknown as TradeDetail;
  it("why it closed, the rule with its label, stop and target; accordions for the rest, ?tab= opens one", () => {
    const at = { path: "/positions/7?tab=gate", route: "/positions/:id", seeds: [{ key: ["trade", "7"], data: d }], ctx };
    const html = render(<TradeDetailPage />, at);
    const t = text(html);
    expect(t).toContain("Why it closed stop hit");
    expect(t).toContain("Rule Trend momentum Experiment · failed the ten-year test · paper only");
    expect(t).toContain("Stop · target $140.00 · $170.00");
    expect(html).toContain('<details class="acc" open="" id="trade-gate">');
    expect(html).toMatch(/<details class="acc" id="trade-room">/);
  });
});

describe("Activity: the label once in full, a compact tag on rows", () => {
  const items: TapeItem[] = [{ kind: "fill", ts: now, side: "sell", qty: 5, venue_px: 142.18, shadow_px: 142.1, fees: 0.02, tag: null, symbol: "NVDA",
    strategy: "trend_momentum", trade_id: 7, exit_reason: "target", pnl_usd: 38.2 }];
  it("banner row with the full label; the row's tag says Experiment, named in full for screen readers; chips on one line", () => {
    const html = render(<Tape />, { ctx, seeds: [{ key: ["tape", 1], data: items, infinite: true }] });
    expect(html).toMatch(/class="status-row exp"[^>]*><span class="status-text">Experiment: failed the ten-year test<\/span>/);
    expect(html).toContain('<span class="row-tag" aria-label="Experiment: failed the ten-year test" title="Experiment: failed the ten-year test">Experiment</span>');
    expect(text(html).split("Experiment: failed the ten-year test").length - 1).toBe(1);
    expect(html).toContain('class="chips chips-scroll"');
  });
});
