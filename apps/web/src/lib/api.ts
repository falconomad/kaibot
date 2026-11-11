import { createClient } from "@supabase/supabase-js";
import { coerce } from "./coerce";
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
export interface Heartbeat { process: string; ts: string; detail: { state?: string; minute?: number; host?: string; next_open?: string; account_clean?: boolean } | null }
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
  | { kind: "signal"; ts: string; mode: "live" | "shadow"; strategy: string; symbol: string; entry: number | null; stop: number | null;
      target: number | null; reasons: string[]; veto: string | null; approved: boolean | null; qty: number | null; binding: string | null;
      trade_id: number | null; counterfactual: { pnl_usd?: number; r_multiple?: number } | null }
  | { kind: "fill"; ts: string; side: "buy" | "sell"; qty: number; venue_px: number; shadow_px: number; fees: number; tag: string | null;
      symbol: string; strategy: string; trade_id: number | null; exit_reason: string | null; pnl_usd: number | null };
export interface Health {
  now: string; heartbeat: Heartbeat[]; halts: Halt[];
  events: { type: string; ts: string; run_id: number | null; payload: Record<string, unknown> }[];
  deadman: { armed: boolean; jobs: { job: string; schedule: string; active: boolean }[];
    runs: { status: string; message: string; start: string }[] };
  pending_commands: { kind: string; ts: string }[];
}
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
  room: { proposer: unknown; critic: unknown; judge: unknown; verdict: string; latency_ms: number; cost_usd: number } | null;
  fills: { ts: string; side: string; qty: number; venue_px: number; shadow_px: number; fees: number }[];
}

async function rpc<T>(fn: string, args?: Record<string, unknown>): Promise<T> {
  const { data, error } = await supa.rpc(fn, args);
  if (error) throw new Error(error.message);
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
    return r ? { ...r, trade: fixTrade(r.trade), run: fixRun(r.run) } : null;
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
  newRun: (amount: number, level: Level) =>
    rpc<{ queued: boolean }>("kb_new_run", { p_amount: amount, p_level: level, p_idem: crypto.randomUUID() }),
};
