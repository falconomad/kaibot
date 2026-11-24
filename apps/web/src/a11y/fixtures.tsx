// #327: every page of main.tsx with its query keys and its data for the empty and the loaded state. Invented numbers.
// Not a test file itself.
import type { ReactElement, ReactNode } from "react";
import { Route } from "react-router-dom";
import type { RunCtx } from "../components/Shell";
import type { Evidence, GateResp, Health as HealthResp, Heartbeat, LlmBudget, Overview, Report as ReportResp, TapeItem, Trade } from "../lib/api";
import { parseBook } from "../lib/book";
import { levelDefaults, normalizeBounds, type ConfigResp } from "../lib/config";
import { parseSummary } from "../lib/summary";
import { FineTune } from "../pages/FineTune";
import { Gate } from "../pages/Gate";
import { Health } from "../pages/Health";
import { Live } from "../pages/Live";
import { More } from "../pages/More";
import { Onboarding } from "../pages/Onboarding";
import { Positions } from "../pages/Positions";
import { Report } from "../pages/Report";
import { Settings } from "../pages/Settings";
import { NotOwner, SignIn } from "../pages/SignIn";
import { Register } from "../pages/Register";
import { Member } from "../pages/Member";
import { Strategy, StrategyHome } from "../pages/Strategy";
import { Tape } from "../pages/Tape";
import { TradeDetailPage } from "../pages/TradeDetail";
import { ctxFor, emptyOverview, paperRun, type Seed } from "../pages/testkit";
import { RAW_BOOK, RAW_EMPTY_BOOK } from "../pages/testkit-book";

export const run = paperRun();
const now = () => new Date().toISOString();

const EXP = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
const beat = (): Heartbeat[] => [{ process: "engine", ts: now(), detail: { state: "trading", code_sha: "abc1234", entries_today: 1, experiments: [EXP],
  scan: { bars: 59, checks: 118, regime_off: 0, setups: 2, near: 1, vetoed: { spread: 1 }, to_room: 0, recent_near: [{ symbol: "AMD", m: 0.8, z: 1.7, why: "dip too shallow" }] },
} as Heartbeat["detail"] }];
const ledger = { ts: now(), cash: 5000, equity_venue: 10080, equity_shadow: 10070,
  positions: { NVDA: { qty: 10, fill: 200, stop: 190, target: 220, status: "open", last: 207, trade_id: 7 } } };
const loadedOverview = (): Overview => ({ ...emptyOverview(run), heartbeat: beat(), ledger, today: { trades: 1, pnl: 25 },
  daily: [{ session: "2026-11-16", pnl: 40, pnl_venue: 41, pnl_shadow: 40, spy_ret: 0.002, trades: 2 }, { session: "2026-11-17", pnl: 25, pnl_venue: 26, pnl_shadow: 25, spy_ret: 0.003, trades: 1 }],
  halts_today: [{ kind: "losses", reason: "3 in a row", ts: now() }] });
export const loadedCtx = (): RunCtx => ctxFor(run, { overview: loadedOverview() });

const trade: Trade = { id: 7, run_id: 1, signal_id: null, strategy: "trend_momentum", symbol: "AAPL", entry_ts: "2026-11-13T15:00:00Z",
  exit_ts: "2026-11-13T19:00:00Z", qty: 2, entry_px: 150, exit_px: 140, stop_px: 140, target_px: 170, risk_usd: 20, exit_reason: "stop", pnl_usd: -20, verdict: null };
const evidence0: Evidence = { n: 0, pnl: 0, mean_r: null, sd_r: null, t: null, ci_lo: null, ci_hi: null, sessions: 0, first_session: null, by_strategy: [] };
const evidence1: Evidence = { n: 12, pnl: 84, mean_r: 0.21, sd_r: 1.1, t: 0.66, ci_lo: -0.4, ci_hi: 0.8, sessions: 9, first_session: "2026-11-02",
  by_strategy: [{ strategy: "trend_momentum", n: 12, pnl: 84, mean_r: 0.21 }] };
const chapters = [{ version: 1, from: "2026-11-02", to: null, n_trades: 12, pnl_usd: 84, mean_r: 0.21, t_stat: 0.66, too_early: true }];
const book = (raw: unknown): Seed => ({ key: ["book", 1], data: parseBook(raw, 1) });

const planRaw = { rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-17", fills_at_close_of: "2099-01-01", decides: true,
  sell: [{ symbol: "XOM", why: "rank" }], buy_candidates: ["AAPL"],
  orders: [{ symbol: "XOM", shares_now: 2, shares_target: 0, action: "sell", why: "rank", coid: "c1", state: "working" },
    { symbol: "AAPL", shares_now: 1, shares_target: 4, action: "buy", why: "rank", coid: "c2", state: "working" }], skipped: 0, note: null }] };
const soldRaw = [{ rule: "trend_momentum", symbol: "XOM", opened_session: "2026-11-18", closed_session: "2026-12-09", qty: 2, avg_cost: 116.2,
  exit_px: 113.1, pnl_usd: -6.2, dividends: 0, days_held: 21, reason: "rank_below_cutoff" }];

const tape: TapeItem[] = [
  { kind: "fill", ts: "2026-11-16T15:06:00Z", side: "sell", qty: 5, venue_px: 142.18, shadow_px: 142.1, fees: 0.02, tag: null, symbol: "NVDA",
    strategy: "trend_momentum", trade_id: 7, exit_reason: "target", pnl_usd: 38.2 },
  { kind: "signal", ts: "2026-11-16T15:00:00Z", mode: "live", strategy: "dip_reversal", symbol: "AMD", entry: 150, stop: 145, target: 160,
    reasons: [], veto: "company news in 24 h", approved: null, qty: null, binding: null, trade_id: null, counterfactual: null },
];

const report0: ReportResp = { session: null, sessions: [] };
const report1: ReportResp = { session: "2026-11-13", sessions: ["2026-11-12", "2026-11-13"], start_equity: 10000,
  day: { pnl: 25, pnl_venue: 26, pnl_shadow: 25, spy_ret: 0.003, cash_ret: 0.00016, trades: 1 },
  trades: [{ id: 7, strategy: "dip_reversal", symbol: "AMD", entry_ts: "2026-11-13T15:00:00Z", exit_ts: "2026-11-13T19:00:00Z", qty: 2, entry_px: 150,
    exit_px: 162.5, pnl_usd: 25, risk_usd: 10, exit_reason: "target" }],
  signals: { live: 3, approved: 1, shadow: 2 }, vetoes: [{ reason: "spread", n: 2 }],
  shadow: [{ strategy: "orb_stocks_in_play", symbol: "TSLA", ts: "2026-11-13T14:00:00Z", veto: null, pnl_usd: 12, r: 0.4 }],
  halts: [{ kind: "losses", reason: "3 in a row", ts: "2026-11-13T15:03:00Z" }] };
const attribution0 = { since: "2026-10-15T00:00:00Z", weeks: 4, n: 0, groups: [], weekly: [] };
const attribution1 = { since: "2026-10-15T00:00:00Z", weeks: 4, n: 12,
  groups: [{ dim: "strategy", key: "trend_momentum", n: 12, pnl: 84, mean_r: 0.21, win: 0.5, t: 0.66, flag: null }],
  weekly: [{ week: "2026-11-09", strategy: "trend_momentum", n: 6, pnl: 40 }] };
const roomAb0 = { since: "2026-08-13T00:00:00Z", weeks: 13, n: 0, by_verdict: {}, by_status: {} };

const health0: HealthResp = { now: "2026-11-12T22:00:00Z", heartbeat: [], halts: [], events: [], pending_commands: [], deadman: { armed: false, jobs: [], runs: [] } };
const health1 = (): HealthResp => ({ now: now(), heartbeat: beat(), halts: [{ kind: "losses", reason: "3 in a row", ts: now(), run_id: 1, session: "2026-11-17" }],
  events: [{ type: "engine_start", ts: now(), run_id: 1, payload: { host: "mac" } }], pending_commands: [{ kind: "pause", ts: now() }],
  deadman: { armed: true, jobs: [{ job: "kb_deadman", schedule: "* * * * *", active: true }], runs: [{ status: "succeeded", message: "ok", start: now() }] } });
const budget0: LlmBudget = { day: "2026-11-12", day_usd: 0, month_usd: 0, state: "ok", alert: false,
  caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {} };
const budget1: LlmBudget = { ...budget0, day_usd: 0.42, month_usd: 6.1, calls_today: 14, by_purpose: { triage: 0.3, room: 0.12 } };

const gate0: GateResp = { latest: null, history: [], random_days: 0 };
const crit = (value: number, limit: number, met: boolean) => ({ value, limit, met });
const gate1: GateResp = {
  latest: { id: 2, ts: "2026-11-17T21:05:00Z", session: "2026-11-17", bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", overall: "keep collecting",
    report: { generated_at: "2026-11-17T21:05:00Z", bar_sha: "abcdef0123456789", fingerprint: "f00dfeed12345678", frozen_since: "2026-11-01", overall: "keep collecting",
      bar: { min_trades: 100, min_t: 2 }, kill: { max_drawdown_pct: 21 },
      strategies: { trend_momentum: { status: "provisional", kills: [], criteria: { trades: crit(12, 100, false), t: crit(0.66, 2, false) }, n_trades: 12,
        excluded_older_code: 0, pnl_usd: 84, mean_r_pct: 0.21, sessions: 9, random_days: 3, random_missing_days: null, n_trials: 4, trades_to_go: 88 } } } },
  history: [{ id: 1, ts: "2026-11-16T21:05:00Z", session: "2026-11-16", overall: "keep collecting", fingerprint: "f00dfeed12345678",
    status: { trend_momentum: "provisional" }, n_trades: { trend_momentum: 10 } }],
  random_days: 3,
};

const B = normalizeBounds(null, "balanced");
const UNIVERSE = ["AAPL", "MSFT", "NVDA", "AMD", "AVGO", "XOM", "CVX", "JPM"];
const THEMES = { chips: { label: "Chips", symbols: ["NVDA", "AMD", "AVGO"] }, energy: { label: "Energy", symbols: ["XOM", "CVX"] } };
const config0 = { level: "balanced", bounds: {}, themes: {}, universe: [], applied: null, pending: null, draft: null, versions: [] } as unknown as ConfigResp;
/** A draft on themes, with one theme picked and one stock the owner never trades. */
export function configThemes(): ConfigResp {
  const d = levelDefaults(B);
  const body = { ...d, focus: { ...d.focus, mode: "prefer" as const, themes: ["chips"], exclude: ["AMD"] } };
  return { level: "balanced", bounds: {}, themes: THEMES, universe: UNIVERSE, applied: null, pending: null,
    draft: { body, updated_at: "2026-11-12T20:00:00Z" }, versions: [] } as unknown as ConfigResp;
}
/** A draft that carries a custom list, so the Focus card opens on My lists. */
export function configLists(): ConfigResp {
  const d = levelDefaults(B);
  const custom = { name: "AI infrastructure", tickers: ["NBIS", "CRWV", "FSLY", "PLTR", "SNOW"].map((symbol) => ({ symbol, etf: "XLK" })) };
  return { ...configThemes(), draft: { body: { ...d, focus: { ...d.focus, mode: "only" as const, custom } }, updated_at: "2026-11-12T20:00:00Z" } } as unknown as ConfigResp;
}
const savedLists = { lists: [{ name: "AI infrastructure", updated_at: "2026-11-12T20:00:00Z", symbols: ["NBIS", "CRWV", "FSLY", "PLTR", "SNOW"] }] };

export const summary = parseSummary({
  symbol: "AAPL", day: "2026-11-18", ai: true, text: "x",
  sources: [{ name: "Finnhub profile", as_of: "2026-11-18" }, { name: "Alpaca news", as_of: "2026-11-17" }],
  body: { about: "Apple Inc is a US technology company listed on NASDAQ.", news: [{ headline: "Apple raises dividend", gist: null, date: "2026-11-17", source: "benzinga" }],
    news_status: "ok", earnings: { date: "2027-01-29", hour: "amc" }, earnings_status: "ok", actions: [], actions_status: "ok" },
});
const tradeDetail = { trade, run, gate: null, room: null, fills: [{ ts: "2026-11-13T15:00:00Z", side: "buy", qty: 2, venue_px: 150, shadow_px: 150.02, fees: 0.01 }] };

export interface Page {
  name: string; el: ReactElement; path?: string; route?: string; children?: ReactNode;
  /** every query the page reads: all seeded with an error for the error state */
  keys: unknown[][];
  /** the page with nothing to show yet */
  empty: Seed[];
  /** the page with data in every part */
  loaded: Seed[];
}
const strategyKids = <><Route index element={<StrategyHome />} /><Route path="fine-tune" element={<FineTune />} /></>;
const tapeSeed = (data: TapeItem[]): Seed => ({ key: ["tape", 1], data, infinite: true });

/** Every page of main.tsx. A page with no query of its own (More, Settings, sign-in) has no seeds: its states come
 *  from the shell's context (the overview), which the tests pass as loading, empty and loaded. */
export const PAGES: Page[] = [
  { name: "Home", el: <Live />, keys: [["trades-all", 1], ["evidence", 1], ["chapters", 1], ["book", 1], ["equity_series", 1]],
    empty: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }, { key: ["evidence", 1], data: evidence0 }, { key: ["chapters", 1], data: [] }, book(RAW_EMPTY_BOOK)],
    loaded: [{ key: ["trades-all", 1], data: { total: 1, rows: [trade] } }, { key: ["evidence", 1], data: evidence1 }, { key: ["chapters", 1], data: chapters }, book(RAW_BOOK)] },
  { name: "Positions", el: <Positions />, keys: [["trades", 1, 0], ["md-plan", 1], ["md-sold", 1], ["book", 1], ["equity_series", 1]],
    empty: [book(RAW_EMPTY_BOOK), { key: ["md-plan", 1], data: null }, { key: ["trades", 1, 0], data: { total: 0, rows: [] } }, { key: ["md-sold", 1], data: [] }],
    loaded: [book(RAW_BOOK), { key: ["md-plan", 1], data: planRaw }, { key: ["trades", 1, 0], data: { total: 1, rows: [trade] } }, { key: ["md-sold", 1], data: soldRaw }] },
  { name: "Trade", el: <TradeDetailPage />, path: "/positions/7", route: "/positions/:id", keys: [["trade", "7"], ["stockSummary", "AAPL"]],
    empty: [{ key: ["trade", "7"], data: null }],
    loaded: [{ key: ["trade", "7"], data: tradeDetail }, { key: ["stockSummary", "AAPL"], data: summary }] },
  { name: "Activity", el: <Tape />, keys: [["tape", 1], ["health"]],
    empty: [tapeSeed([]), { key: ["health"], data: health0 }],
    loaded: [tapeSeed(tape), { key: ["health"], data: health1() }] },
  { name: "More", el: <More />, keys: [], empty: [], loaded: [] },
  { name: "Report", el: <Report />, keys: [["evidence", 1], ["chapters", 1], ["report", 1, undefined], ["room_ab", 1, 13], ["attribution", 1, 4]],
    empty: [{ key: ["evidence", 1], data: evidence0 }, { key: ["chapters", 1], data: [] }, { key: ["report", 1, undefined], data: report0 },
      { key: ["room_ab", 1, 13], data: roomAb0 }, { key: ["attribution", 1, 4], data: attribution0 }],
    loaded: [{ key: ["evidence", 1], data: evidence1 }, { key: ["chapters", 1], data: chapters }, { key: ["report", 1, undefined], data: report1 },
      { key: ["room_ab", 1, 13], data: roomAb0 }, { key: ["attribution", 1, 4], data: attribution1 }] },
  { name: "Engine", el: <Health />, keys: [["health"], ["llm_budget"], ["summary_spend"]],
    empty: [{ key: ["health"], data: health0 }, { key: ["llm_budget"], data: budget0 }, { key: ["summary_spend"], data: null }],
    loaded: [{ key: ["health"], data: health1() }, { key: ["llm_budget"], data: budget1 }, { key: ["summary_spend"], data: null }] },
  { name: "Go / no-go", el: <Gate />, keys: [["gate", 1]], empty: [{ key: ["gate", 1], data: gate0 }], loaded: [{ key: ["gate", 1], data: gate1 }] },
  { name: "Strategy (themes)", el: <Strategy />, path: "/strategy", route: "/strategy", children: strategyKids, keys: [["config", 1], ["chapters", 1], ["tickerLists"]],
    empty: [{ key: ["config", 1], data: config0 }, { key: ["chapters", 1], data: [] }],
    loaded: [{ key: ["config", 1], data: configThemes() }, { key: ["chapters", 1], data: chapters }] },
  { name: "Strategy (My lists)", el: <Strategy />, path: "/strategy", route: "/strategy", children: strategyKids, keys: [["config", 1], ["chapters", 1], ["tickerLists"]],
    empty: [{ key: ["config", 1], data: configLists() }, { key: ["tickerLists"], data: { lists: [] } }],
    loaded: [{ key: ["config", 1], data: configLists() }, { key: ["chapters", 1], data: chapters }, { key: ["tickerLists"], data: savedLists }] },
  { name: "Fine-tune", el: <Strategy />, path: "/strategy/fine-tune", route: "/strategy", children: strategyKids, keys: [["config", 1], ["chapters", 1]],
    empty: [{ key: ["config", 1], data: config0 }, { key: ["chapters", 1], data: [] }],
    loaded: [{ key: ["config", 1], data: configThemes() }, { key: ["chapters", 1], data: chapters }] },
  { name: "Settings", el: <Settings />, keys: [], empty: [], loaded: [] },
  { name: "New run", el: <Onboarding />, path: "/start", route: "/start", keys: [["health"]], empty: [{ key: ["health"], data: health0 }], loaded: [{ key: ["health"], data: health1() }] },
  { name: "Sign in", el: <SignIn />, keys: [], empty: [], loaded: [] },
  { name: "Not the owner", el: <NotOwner failed={false} retry={() => {}} />, keys: [], empty: [], loaded: [] },
  { name: "Not the owner (the check failed)", el: <NotOwner failed retry={() => {}} />, keys: [], empty: [], loaded: [] },
  { name: "Register (invite)", el: <Register search="?code=ABCD-1234-ABCD-1234" />, keys: [], empty: [], loaded: [] },
  { name: "Invited member", el: <Member name="Fran" />, keys: [["invites"]], empty: [{ key: ["invites"], data: { cap: 5, taken: 2, invites: [] } }],
    loaded: [{ key: ["invites"], data: { cap: 5, taken: 3, invites: [{ id: 1, email: "friend@example.com", created_at: "2026-11-24T10:00:00Z",
      expires_at: "2026-12-01T10:00:00Z", used_at: null, status: "open", code: "ABCD-1234-ABCD-1234", mine: true }] } }] },
];
