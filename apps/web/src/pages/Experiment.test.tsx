// #210: the trend_momentum paper experiment on every screen that shows the rule, and nothing new without the fields.
import { describe, expect, it, vi } from "vitest";
import type { Health as HealthResp, Heartbeat, LlmBudget, TapeItem, Trade, TradeDetail } from "../lib/api";
import { Gate } from "./Gate";
import { Health } from "./Health";
import { ExperimentCard } from "../components/Experiment";
import { Live } from "./Live";
import { Tape } from "./Tape";
import { TradeDetailPage } from "./TradeDetail";
import { Positions } from "./Positions";
import { ctxFor, emptyOverview, glitches, paperRun, render, text } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const BADGE = "Experiment · failed the ten-year test · paper only";
const EXP = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, decided: "2026-11-13", level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
const NOTE = "no decision today: daily store not refreshed (see logs/fetch_daily.log)";
const beat = (detail: Record<string, unknown>): Heartbeat[] =>
  [{ process: "engine", ts: new Date().toISOString(), detail: { state: "market_closed", ...detail } as Heartbeat["detail"] }];
const withExp = beat({ experiments: [EXP], multi_day: { book: "open", rules: { trend_momentum: { last_decision: "2026-11-12", note: NOTE } }, note: `trend_momentum: ${NOTE}` } });
const retired = beat({ experiments: [EXP], multi_day: { book: "ended", retired: ["trend_momentum"],
  rules: { trend_momentum: { retired: true, note: "trend_momentum retired: book fell 21.3% from its peak on 2026-11-20" } } } });
const old = beat({ code_sha: "abc1234" }); // an engine from before #205, or KAIBOT_MULTIDAY off

const run = paperRun();
const ctx = (hb: Heartbeat[]) => ctxFor(run, { overview: { ...emptyOverview(run), heartbeat: hb } });
const trade: Trade = { id: 7, run_id: 1, signal_id: null, strategy: "trend_momentum", symbol: "AAPL", entry_ts: "2026-11-13T21:00:00Z",
  exit_ts: null, qty: 4, entry_px: 231.4, exit_px: null, stop_px: 196.7, target_px: null, risk_usd: 138.8, exit_reason: null, pnl_usd: null, verdict: null };
const trades = (k: string[] | (string | number)[]) => ({ key: k, data: { total: 1, rows: [trade] } });
const budget: LlmBudget = { day: "2026-11-13", day_usd: 0, month_usd: 0, state: "ok", alert: false,
  caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {} };
const health = (hb: Heartbeat[], halts: HealthResp["halts"] = []): HealthResp =>
  ({ now: "2026-11-13T22:00:00Z", heartbeat: hb, halts, events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } });

describe("Home (#210, v4 status rows)", () => {
  it("the experiment row states the label in full; its sheet holds the badge, cap, stop line and the no-decision note", () => {
    const html = render(<Live />, { ctx: ctx(withExp), seeds: [trades(["trades-all", 1])] });
    expect(html).toMatch(/<button type="button" class="status-row info"[^>]*><span class="status-text">Experiment: failed the ten-year test<\/span>/);
    const sheet = text(render(<ExperimentCard beats={withExp} />));
    expect(sheet).toContain(BADGE);
    expect(sheet).toContain("up to 60% invested");
    expect(sheet).toContain("retires if it falls 21% from its peak");
    expect(sheet).toContain("No decision today: the daily prices weren't refreshed after the last close");
  });
  it("a retired experiment reads as ended, in amber, never red", () => {
    const html = render(<Live />, { ctx: ctx(retired), seeds: [trades(["trades-all", 1])] });
    expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">Experiment ended: Trend momentum retired/);
    expect(html).not.toContain("status-row breach");
    expect(text(render(<ExperimentCard beats={retired} />))).toContain("Trend momentum retired: its book fell 21.3% from its peak on Nov 20, 2026");
  });
  it("an old engine: no experiment, the test-phase row instead", () => {
    const html = render(<Live />, { ctx: ctx(old), seeds: [trades(["trades-all", 1])] });
    expect(text(html)).not.toContain("Experiment");
    expect(text(html)).toContain("Test phase: paper only, testing the systems");
    expect(glitches(html)).toEqual([]);
  });
});

describe("Engine (#210)", () => {
  const halts = [
    { kind: "multi_day_reenabled", ts: "2026-11-21T13:00:00Z", reason: "rule=trend_momentum; re-enabled by the stakeholder (KAIBOT_REENABLE)" },
    { kind: "multi_day_retired", ts: "2026-11-20T15:00:00Z", session: "2026-11-20", reason: "rule=trend_momentum; trend_momentum retired: book fell 21.3% from its peak on 2026-11-20" },
    { kind: "drift", ts: "2026-11-19T15:00:00Z", reason: "AAPL 4 vs 3" },
  ];
  it("the card, the note, and halt rows toned: ended amber, re-enable info, breach red", () => {
    const html = render(<Health />, { seeds: [{ key: ["health"], data: health(withExp, halts) }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    expect(t).toContain("Trend momentum Experiment: failed the ten-year test");
    expect(t).toContain("No decision today: Trend momentum skipped its decision");
    expect(t).toContain("Trend momentum switched back on by you info");
    expect(t).toContain("Experiment ended: Trend momentum retired");
    expect(t).toContain("experiment ended · switched back on later");
    expect(t).toMatch(/Positions differ from the broker: entries halted halt/);
    expect(html).toContain('class="tag info"');
    expect(html).toContain('class="tag downgrade"');
    expect(html).toContain('class="tag veto"');
  });
  it("an old engine: no experiment card", () => {
    const t = text(render(<Health />, { seeds: [{ key: ["health"], data: health(old) }, { key: ["llm_budget"], data: budget }] }));
    expect(t).not.toContain("Paper experiment");
    expect(t).not.toContain("Experiment —");
  });
});

describe("Trades, Tape, Trade detail (#210)", () => {
  it("Positions: closed trades of the rule carry the experiment label in full, only when the engine names it", () => {
    const rp = { ...run, kind: "replay" as const };
    const c = (hb: Heartbeat[]) => ctxFor(rp, { overview: { ...emptyOverview(rp), heartbeat: hb } });
    const seeds = [{ key: ["trades", 1, 0], data: { total: 1, rows: [trade] } }];
    expect(text(render(<Positions />, { ctx: c(withExp), seeds }))).toContain("Experiment: failed the ten-year test");
    expect(text(render(<Positions />, { ctx: c(old), seeds }))).not.toContain("Experiment");
  });
  it("Tape: on a fill and on a setup", () => {
    const items: TapeItem[] = [
      { kind: "fill", ts: "2026-11-13T21:00:00Z", side: "buy", qty: 4, venue_px: 231.4, shadow_px: 231.5, fees: 0, tag: null, symbol: "AAPL",
        strategy: "trend_momentum", trade_id: 7, exit_reason: null, pnl_usd: null },
      { kind: "signal", ts: "2026-11-13T20:00:00Z", mode: "live", strategy: "dip_reversal", symbol: "MSFT", entry: 400, stop: 390, target: 420,
        reasons: [], veto: null, approved: true, qty: 1, binding: null, trade_id: null, counterfactual: null },
    ];
    const t = text(render(<Tape />, { ctx: ctx(withExp), seeds: [{ key: ["tape", 1], data: items, infinite: true }] }));
    expect(t.split("Experiment: failed the ten-year test").length - 1).toBe(1); // the fill only: the dip rule is no experiment
  });
  it("Trade detail: badge in the header, cap and stop line next to the rule", () => {
    const d: TradeDetail = { trade, run, gate: null, room: null, fills: [] };
    const at = { path: "/trades/7", route: "/trades/:id", seeds: [{ key: ["trade", "7"], data: d }] };
    const t = text(render(<TradeDetailPage />, { ...at, ctx: ctx(withExp) }));
    expect(t).toContain(BADGE);
    expect(t).toContain("up to 60% invested · retires if it falls 21% from its peak");
    expect(t).not.toContain("research only");
    // Without the heartbeat's label row the rule keeps its recorded verdict (the badge), only without the limits.
    const t0 = text(render(<TradeDetailPage />, { ...at, ctx: ctx(old) }));
    expect(t0.split(BADGE).length - 1).toBe(1);
    expect(t0).not.toContain("invested");
    expect(t0).not.toContain("research only");
  });
});

describe("Gate page verdicts (#210 follow-up)", () => {
  const none = { key: ["gate", 1], data: { latest: null, history: [], random_days: 0 } };
  it("each Engine v3 rule with its honest verdict, never 'research only'", () => {
    const html = render(<Gate />, { ctx: ctx(withExp), seeds: [none] });
    const t = text(html);
    expect(t).toContain("Engine v3 rules: the ten-year test");
    expect(t).toContain(`Trend momentum ${BADGE}`);
    expect(t).toContain("12-month momentum failed the ten-year test");
    expect(t).toContain("SPY trend filter failed the ten-year test");
    expect(t).toContain("SPY noise band ten-year test: waiting for the engine run");
    expect(t).toContain("SPY noise band, held overnight waiting: tested after the noise band");
    expect(t).not.toContain("research only");
    expect(html).toContain('class="tag gate-fail"');
    expect(glitches(html)).toEqual([]);
  });
  it("the experiment badge stays without the heartbeat, without limits", () => {
    const t = text(render(<Gate />, { ctx: ctx(old), seeds: [none] }));
    expect(t).toContain(`Trend momentum ${BADGE}`);
    expect(t).not.toContain("invested");
  });
});
