import { createClient } from "@supabase/supabase-js";
import { coerce } from "./coerce";
import type { Chapter, ConfigBody, ConfigResp } from "./config";
import type { TickerCheck, TickerLists } from "./tickers";
export { coerce };

const url = import.meta.env.VITE_SUPABASE_URL as string;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string;
export const supa = createClient(url, key, { auth: { persistSession: true, detectSessionInUrl: true, flowType: "pkce" } });

export type Level = "conservative" | "balanced" | "aggressive";

export interface Run {
  id: number; created_at: string; amount_usd: number; status: string; end_reason: string | null;
  peak_equity: number | null; kind: "paper" | "replay"; label: string | null; level: Level | null;
  started_on: string | null; ended_on: string | null;
}
export interface Daily { session: string; pnl: number; pnl_venue: number; pnl_shadow: number; spy_ret: number | null; cash_ret?: number | null; trades: number }
export interface GateCriterion { value: number | null; limit: number | null; met: boolean | null }
export type GateStatus = "pass" | "fail" | "kill" | "provisional";
export interface GateStrategy {
  status: GateStatus; kills: string[]; criteria: Record<string, GateCriterion>; n_trades: number; excluded_older_code: number;
  pnl_usd: number; mean_r_pct: number | null; sessions: number; random_days: number; random_missing_days: string[] | null;
  n_trials: number; trades_to_go: number;
}
export interface GateReportBody {
  generated_at: string; bar_sha: string; fingerprint: string; frozen_since: string | null; overall: GateOverall;
  bar: Record<string, number | boolean>; kill: Record<string, number | boolean>; strategies: Record<string, GateStrategy>;
}
export type GateOverall = "discuss" | "keep collecting" | "kill";
export interface GateHistoryRow { id: number; ts: string; session: string; overall: GateOverall; fingerprint: string; status: Record<string, GateStatus>; n_trades: Record<string, number> }
export interface GateResp { latest: { id: number; ts: string; session: string; bar_sha: string; fingerprint: string; overall: GateOverall; report: GateReportBody } | null; history: GateHistoryRow[]; random_days: number }
export interface NearMiss { symbol: string; m: number; z: number; why: string }
export interface Scan { bars: number; checks: number; regime_off: number; setups: number; near: number; vetoed: Record<string, number>; to_room: number; recent_near: NearMiss[] }
export interface Heartbeat { process: string; ts: string; detail: { state?: string; minute?: number; host?: string; next_open?: string; account_clean?: boolean; entries_today?: number; scan?: Scan | null;
  /** the git sha the engine runs (engine/core.py `code_sha`) */ code_sha?: string; halted?: boolean } | null }
export interface LedgerPos { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number }
export interface Ledger { ts: string; cash: number; positions: Record<string, LedgerPos>; equity_venue: number; equity_shadow: number }
export interface Overview {
  run: Run; daily: Daily[]; heartbeat: Heartbeat[];
  pending_commands: { kind: string; ts: string; payload: Record<string, unknown> }[];
  ledger: Ledger | null; today: { trades: number; pnl: number } | null;
  segments?: { level: Level; from_session: string }[];
  halts_today?: Halt[];
}
export interface Halt { kind: string; reason: string; ts: string; run_id?: number | null; session?: string | null }
export interface Evidence {
  n: number; pnl: number; mean_r: number | null; sd_r: number | null; t: number | null; ci_lo: number | null; ci_hi: number | null;
  sessions: number; first_session: string | null; by_strategy: { strategy: string; n: number; pnl: number; mean_r: number | null }[];
}
export interface ReportTrade { id: number; strategy: string; symbol: string; entry_ts: string; exit_ts: string | null; qty: number;
  entry_px: number; exit_px: number | null; pnl_usd: number | null; risk_usd: number | null; exit_reason: string | null }
export interface Report {
  session: string | null; sessions: string[];
  day?: { pnl: number; pnl_venue: number; pnl_shadow: number; spy_ret: number | null; cash_ret: number | null; trades: number } | null;
  start_equity?: number | null; trades?: ReportTrade[];
  signals?: { live: number; approved: number; shadow: number };
  vetoes?: { reason: string; n: number }[];
  shadow?: { strategy: string; symbol: string; ts: string; veto: string | null; pnl_usd: number | null; r: number | null }[];
  halts?: Halt[];
}
export interface AttributionGroup { dim: string; key: string; n: number; pnl: number; mean_r: number | null; win: number;
  t: number | null; flag: "kill" | "watch" | null }
export interface Attribution { since: string; weeks: number; n: number; groups: AttributionGroup[];
  weekly: { week: string; strategy: string; n: number; pnl: number }[] }
export type TapeItem =
  | { kind: "signal"; ts: string; signal_id?: number; mode: "live" | "shadow"; strategy: string; symbol: string; entry: number | null; stop: number | null;
      target: number | null; reasons: string[]; veto: string | null; approved: boolean | null; qty: number | null; binding: string | null;
      trade_id: number | null; counterfactual: Counterfactual | null;
      room?: { status: RoomStatus; verdict: RoomVerdict | null; cause: string | null; confidence: string | null; counterfactual: Counterfactual | null } | null }
  | { kind: "fill"; ts: string; side: "buy" | "sell"; qty: number; venue_px: number; shadow_px: number; fees: number; tag: string | null;
      symbol: string; strategy: string; trade_id: number | null; exit_reason: string | null; pnl_usd: number | null };
export interface Health {
  now: string; heartbeat: Heartbeat[]; halts: Halt[];
  events: { type: string; ts: string; run_id: number | null; payload: Record<string, unknown> }[];
  deadman: { armed: boolean; jobs: { job: string; schedule: string; active: boolean }[];
    runs: { status: string; message: string; start: string }[] };
  pending_commands: { kind: string; ts: string }[];
}
// M3 agent room (S35/S36). Model fields are untrusted jsonb: read them through derive.roomView, render as plain text.
export type RoomVerdict = "allow" | "downgrade" | "veto";
export type RoomStatus = "pending" | "done" | "timeout" | "invalid" | "skipped" | "error";
export interface Counterfactual { pnl_usd?: number; r_multiple?: number; exit_reason?: string }
export interface EvidenceRef { id: string; source?: string; published_at?: string }
export interface PackItem { id: string; kind?: string; source?: string; published_at?: string; headline?: string; url?: string;
  n_outlets?: number; triage?: { cause?: string; materiality?: string; direction?: string; summary?: string } | null }
export interface RoomSession {
  id?: number; signal_id: number; ts: string; status: RoomStatus; verdict: RoomVerdict | null; rules_only: "allow" | "veto" | null;
  proposer: unknown; critic: unknown; judge: unknown; evidence: { items?: unknown } | null;
  latency_ms: number | null; cost_usd: number | null; error: string | null; counterfactual: Counterfactual | null;
}
export interface Room {
  session: RoomSession;
  signal: { id: number; run_id: number; ts: string; mode: string; strategy: string; symbol: string; entry: number | null;
    stop: number | null; target: number | null; reasons: string[]; veto: string | null } | null;
  trade: { id: number; qty: number; pnl_usd: number | null; risk_usd: number | null; exit_reason: string | null; exit_ts: string | null } | null;
}
export interface RoomAB {
  since: string; weeks: number; n: number;
  by_verdict: Partial<Record<RoomVerdict | "none", number>>; by_status: Partial<Record<RoomStatus, number>>;
  veto: { n: number; n_cf: number; mean_r: number | null; sd_r: number | null; cf_pnl: number; n_loss_avoided: number;
    loss_avoided: number; n_win_missed: number; win_missed: number };
  allow: { n: number; n_closed: number; mean_r: number | null; sd_r: number | null; pnl: number };
  downgrade: { n: number; n_paired: number; pnl: number; cf_pnl: number; mean_r: number | null };
  no_answer: { n: number; n_cf: number; cf_pnl: number };
  arms: { n: number; rules_pnl: number; room_pnl: number };
  lift: { n_veto: number; n_allow: number; diff: number | null; ci_lo: number | null; ci_hi: number | null };
  spend: { llm_usd: number; room_usd: number; by_purpose: Record<string, number>; per_loss_avoided: number | null };
}
export type BudgetState = "ok" | "rules_only" | "no_entries";
export interface LlmBudget {
  day: string; day_usd: number; month_usd: number; state: BudgetState; alert: boolean;
  caps: { daily_soft: number; monthly_alert: number; monthly_hard: number };
  calls_today: number; open_reserved_usd: number; by_purpose: Record<string, number>;
}
const numValues = (o: Record<string, unknown> | null | undefined) =>
  Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k, Number(v)]));

export type CommandKind = "pause" | "resume" | "flatten_and_stop" | "set_level";
export interface Trade {
  id: number; run_id: number; signal_id: number | null; strategy: string; symbol: string;
  entry_ts: string; exit_ts: string | null; qty: number; entry_px: number; exit_px: number | null;
  stop_px: number | null; target_px: number | null; risk_usd: number | null; exit_reason: string | null;
  pnl_usd: number | null; verdict: "allow" | "downgrade" | "veto" | null; features?: Record<string, unknown>;
}
export interface TradeDetail {
  trade: Trade; run: Run;
  gate: { checks: Record<string, unknown>; approved: boolean; qty: number; binding: string | null; policy_hash: string; code_sha: string } | null;
  room: RoomSession | null;
  fills: { ts: string; side: string; qty: number; venue_px: number; shadow_px: number; fees: number }[];
}

async function rpc<T>(fn: string, args?: Record<string, unknown>): Promise<T> {
  const { data, error } = await supa.rpc(fn, args);
  if (error) throw Object.assign(new Error(error.message), { code: error.code });
  return data as T;
}

// Postgres numerics arrive as strings; coerce the fields we do maths on.
const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));
function fixTrade(t: Trade): Trade {
  return { ...t, qty: Number(t.qty), entry_px: Number(t.entry_px), exit_px: num(t.exit_px), stop_px: num(t.stop_px),
    target_px: num(t.target_px), risk_usd: num(t.risk_usd), pnl_usd: num(t.pnl_usd) };
}
function fixRun(r: Run): Run { return { ...r, amount_usd: Number(r.amount_usd), peak_equity: num(r.peak_equity) }; }


export const api = {
  claimOwner: () => rpc<boolean>("kb_claim_owner"),
  runs: async () => (await rpc<Run[]>("kb_runs")).map(fixRun),
  overview: async (run: number) => {
    const o = await rpc<Overview>("kb_overview", { p_run: run });
    const ledger = o.ledger ? { ...o.ledger, cash: Number(o.ledger.cash), equity_venue: Number(o.ledger.equity_venue),
      equity_shadow: Number(o.ledger.equity_shadow) } : null;
    return { ...o, ledger, run: fixRun(o.run), daily: o.daily.map((d) => ({ ...d, pnl: Number(d.pnl), pnl_venue: Number(d.pnl_venue),
      pnl_shadow: Number(d.pnl_shadow), spy_ret: num(d.spy_ret) })) };
  },
  trades: async (run: number, limit = 50, offset = 0) => {
    const r = await rpc<{ total: number; rows: Trade[] }>("kb_trades", { p_run: run, p_limit: limit, p_offset: offset });
    return { total: r.total, rows: r.rows.map(fixTrade) };
  },
  trade: async (id: number) => {
    const r = await rpc<TradeDetail | null>("kb_trade", { p_id: id });
    return r ? { ...r, trade: fixTrade(r.trade), run: fixRun(r.run), room: r.room ? coerce(r.room) : null } : null;
  },
  command: (kind: CommandKind, payload: Record<string, unknown> = {}) =>
    rpc<{ queued: boolean }>("kb_command", { p_kind: kind, p_idem: crypto.randomUUID(), p_payload: payload }),
  evidence: async (run: number) => coerce(await rpc<Evidence>("kb_evidence", { p_run: run })),
  report: async (run: number, session?: string) =>
    coerce(await rpc<Report>("kb_report", { p_run: run, p_session: session ?? null })),
  attribution: async (run: number, weeks = 4) => coerce(await rpc<Attribution>("kb_attribution", { p_run: run, p_weeks: weeks })),
  tape: async (run: number, limit = 100, before?: string) =>
    coerce(await rpc<TapeItem[]>("kb_tape", { p_run: run, p_limit: limit, p_before: before ?? null })),
  health: async () => coerce(await rpc<Health>("kb_health")),
  room: async (signal: number) => {
    const r = await rpc<Room | null>("kb_room", { p_signal: signal });
    return r ? coerce(r) : null;
  },
  roomAb: async (run: number, weeks = 13) => {
    const r = coerce(await rpc<RoomAB>("kb_room_ab", { p_run: run, p_weeks: weeks }));
    return { ...r, spend: { ...r.spend, by_purpose: numValues(r.spend?.by_purpose) } };
  },
  llmBudget: async () => {
    const b = coerce(await rpc<LlmBudget>("kb_llm_budget"));
    return { ...b, by_purpose: numValues(b.by_purpose) };
  },
  gate: async (run: number) => rpc<GateResp>("kb_gate_report", { p_run: run }),
  // M5 config (docs/m5/contract.md). The server validates every save and apply; the UI is bounded so it never trips that.
  getConfig: async (run: number) => rpc<ConfigResp>("kb_get_config", { p_run: run }),
  saveConfig: (run: number, body: ConfigBody) =>
    rpc<{ ok: boolean; draft: { body: ConfigBody; updated_at: string } }>("kb_save_config", { p_run: run, p_body: body }),
  applyConfig: (run: number) => rpc<{ version: number; applies_from: string }>("kb_apply_config", { p_run: run, p_idem: crypto.randomUUID() }),
  resetConfig: (run: number) => rpc<unknown>("kb_reset_config", { p_run: run }),
  chapters: async (run: number) => {
    const r = coerce(await rpc<Chapter[] | null>("kb_chapters", { p_run: run })) ?? [];
    return r.map((c) => ({ ...c, version: Number(c.version), n_trades: Number(c.n_trades), pnl_usd: Number(c.pnl_usd ?? 0) }));
  },
  // S68/S69 custom ticker lists (contract "Custom ticker lists"). kb_delete_ticker_list may not exist in the database
  // yet (migrations_pending step 1): callers read that with tickers.isMissingFunction and say so in plain words.
  checkTicker: (symbol: string) => rpc<TickerCheck>("kb_check_ticker", { p_symbol: symbol }),
  tickerLists: async () => {
    const r = await rpc<TickerLists | null>("kb_ticker_lists");
    return { max_tickers: Number(r?.max_tickers ?? 40), max_lists: Number(r?.max_lists ?? 20), min_only: Number(r?.min_only ?? 5),
      lists: Array.isArray(r?.lists) ? r.lists : [] };
  },
  saveTickerList: (name: string, symbols: string[]) =>
    rpc<{ ok: boolean; name: string; symbols: string[] }>("kb_save_ticker_list", { p_name: name, p_symbols: symbols }),
  deleteTickerList: (name: string) => rpc<{ ok: boolean }>("kb_delete_ticker_list", { p_name: name }),
  newRun: (amount: number, level: Level) =>
    rpc<{ queued: boolean }>("kb_new_run", { p_amount: amount, p_level: level, p_idem: crypto.randomUUID() }),
};
