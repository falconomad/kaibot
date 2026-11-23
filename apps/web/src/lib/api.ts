import { createClient } from "@supabase/supabase-js";
import { coerce } from "./coerce";
import { parseBook, parseSeries } from "./book";
import { parseSummary } from "./summary";
import { type Marks, parseMarks } from "./marks";
import { STREAM_EVENT, STREAM_TOPIC, type Stream, parseStream } from "./stream";
import type { Chapter, ConfigBody, ConfigResp } from "./config";
import { checkerAnswer, type TickerCheck, type TickerLists } from "./tickers";
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
  /** the git sha the engine runs (engine/core.py `code_sha`) */ code_sha?: string; halted?: boolean;
  /** #439: the last ledger/broker check, raw; read by lib/reconcile.reconcileFrom */ reconcile?: unknown } | null }
export interface LedgerPos { qty: number; fill: number | null; stop: number; target: number; status: string; last: number | null; trade_id: number }
/** kb_overview `ledger`: the engine's newest minute row. `positions` is day positions ONLY; `multi_day` (#384, migration
 *  0034, absent before it) is the multi-day book in that minute, raw jsonb read by lib/marks.engineMarks. */
export interface Ledger { ts: string; cash: number; positions: Record<string, LedgerPos>; equity_venue: number; equity_shadow: number; multi_day?: unknown }
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
  caps: { daily_soft: number; monthly_alert: number; monthly_hard: number; /** 0025/0026: the $150 cap on every AI dollar */ lifetime_hard?: number };
  calls_today: number; open_reserved_usd: number; by_purpose: Record<string, number>;
  /** 0026: every AI dollar so far, both ledgers (engine calls and stock summaries), and the summaries' part. */
  lifetime_usd?: number; lifetime_summaries_usd?: number;
}
const numValues = (o: Record<string, unknown> | null | undefined) =>
  Object.fromEntries(Object.entries(o ?? {}).map(([k, v]) => [k, Number(v)]));

/** `accept_reconcile` (M9 1.14, migration 0037): payload {reason, shown}, see lib/reconcile.ts. */
export type CommandKind = "pause" | "resume" | "flatten_and_stop" | "set_level" | "accept_reconcile";
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

/** An RPC that may not be in production yet: a missing function is "not yet" (null), any other error stays an error. */
async function optional<T>(call: () => Promise<T>): Promise<T | null> {
  try { return await call(); } catch (e) {
    const x = e as { code?: string; message?: string };
    if (x?.code === "PGRST202" || x?.code === "42883" || /could not find the function|function .* does not exist/i.test(String(x?.message ?? ""))) return null;
    throw e;
  }
}

// Postgres numerics arrive as strings; coerce the fields we do maths on.
const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));
/** A number the RPC may not send yet (an older migration): null unless it is a finite number. */
const optNum = (v: unknown) => { const n = v === null || v === undefined || v === "" ? NaN : Number(v); return Number.isFinite(n) ? n : null; };
function fixTrade(t: Trade): Trade {
  return { ...t, qty: Number(t.qty), entry_px: Number(t.entry_px), exit_px: num(t.exit_px), stop_px: num(t.stop_px),
    target_px: num(t.target_px), risk_usd: num(t.risk_usd), pnl_usd: num(t.pnl_usd) };
}
function fixRun(r: Run): Run { return { ...r, amount_usd: Number(r.amount_usd), peak_equity: num(r.peak_equity) }; }


/** The one shared subscription behind api.prices. */
const PRICES_LEAVE_MS = 2_000;
const priceStream: {
  listeners: Set<(s: Stream) => void>; ch: ReturnType<typeof supa.channel> | null; joining: boolean;
  leaveTimer: ReturnType<typeof setTimeout> | null;
  /** `joined`: the channel was subscribed once; `down`: it was, and is lost now (lib/stream.pricesPaused). */
  joined: boolean; down: boolean; downs: Set<(down: boolean) => void>;
} = { listeners: new Set(), ch: null, joining: false, leaveTimer: null, joined: false, down: false, downs: new Set() };

function setDown(down: boolean): void {
  const p = priceStream;
  if (p.down === down) return;
  p.down = down;
  for (const f of [...p.downs]) f(down);
}

function leavePrices(): void {
  const p = priceStream;
  if (p.leaveTimer) { clearTimeout(p.leaveTimer); p.leaveTimer = null; }
  if (p.ch) { const ch = p.ch; p.ch = null; void supa.removeChannel(ch); }
  p.joined = false;
  setDown(false);
}

async function joinPrices(): Promise<void> {
  const p = priceStream;
  p.joining = true;
  try {
    await supa.realtime.setAuth().catch(() => {});  // the signed-in session's token, so the owner policy can see who asks
    // A channel of this topic still leaving (an earlier leave) would be handed back by supa.channel and never join again.
    const old = supa.getChannels().find((c) => c.topic === `realtime:${STREAM_TOPIC}`);
    if (old) await supa.removeChannel(old).catch(() => "error");
  } finally {
    p.joining = false;
  }
  if (!p.listeners.size || p.ch) return;
  const ch = supa.channel(STREAM_TOPIC, { config: { private: true } });
  p.ch = ch
    .on("broadcast", { event: STREAM_EVENT }, (m: { payload?: unknown }) => {
      const s = parseStream(m.payload);
      if (s) for (const f of [...p.listeners]) f(s);
    })
    .subscribe((status, err) => {
      if (p.ch !== ch) return;
      // refused by the owner policy: there is no stream here at all, which is not a stream that dropped
      if (status === "CHANNEL_ERROR" && /unauthori[sz]ed|denied|policy/i.test(String(err?.message ?? ""))) return leavePrices();
      if (status === "SUBSCRIBED") { p.joined = true; setDown(false); }
      else if (p.joined) setDown(true);  // CHANNEL_ERROR, TIMED_OUT or CLOSED after a join; supabase-js joins again
    });
}

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
  /** The check answered on Vercel (api/check-ticker.ts; migration 0024): reads Alpaca paper (GET only), writes the
   *  answer with the owner-only kb_ticker_check_answer and returns it. Null when it can't answer here (no keys, local
   *  dev, not deployed): the row stays pending for the Mac worker and the caller keeps polling kb_check_ticker. */
  runTickerCheck: async (symbol: string): Promise<TickerCheck | null> => {
    const token = (await supa.auth.getSession()).data.session?.access_token;
    if (!token) return null;
    const r = await fetch("/api/check-ticker", { method: "POST", signal: AbortSignal.timeout(25_000),
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ symbol }) });
    const type = r.headers.get("content-type");
    return checkerAnswer(r.status, type, (type ?? "").includes("application/json") ? await r.json().catch(() => null) : null);
  },
  tickerLists: async () => {
    const r = await rpc<TickerLists | null>("kb_ticker_lists");
    return { max_tickers: Number(r?.max_tickers ?? 40), max_lists: Number(r?.max_lists ?? 20), min_only: Number(r?.min_only ?? 5),
      lists: Array.isArray(r?.lists) ? r.lists : [] };
  },
  saveTickerList: (name: string, symbols: string[]) =>
    rpc<{ ok: boolean; name: string; symbols: string[] }>("kb_save_ticker_list", { p_name: name, p_symbols: symbols }),
  deleteTickerList: (name: string) => rpc<{ ok: boolean }>("kb_delete_ticker_list", { p_name: name }),
  // #130: the multi-day book (0018, contract docs/ops/ledger.md). Parsed defensively in lib/book.ts.
  book: async (run: number) => parseBook(await rpc<unknown>("kb_book", { p_run: run, p_session: null }), run),
  /** #229 (migration 0020): null while 0020 isn't applied (the function does not exist yet). */
  multidayPlan: async (run: number) => optional(() => rpc<unknown>("kb_multiday_plan", { p_run: run })),
  bookClosed: async (run: number, limit = 50) => optional(() => rpc<unknown>("kb_book_closed", { p_run: run, p_limit: limit })),
  equitySeries: async (run: number) => parseSeries(await rpc<unknown>("kb_equity_series", { p_run: run, p_from: null, p_to: null })),
  /** Live marks (api/marks.ts): what the paper account holds and each stock's price now, read from Alpaca paper (GET
   *  only) for the owner, at most once in 10 s. Null when the function cannot answer here (no keys on a preview, local
   *  dev, not deployed, not the owner): the page then keeps the engine's minute ledger and the close marks
   *  (lib/marks.ts). A request that fails throws, so the last answer stays until it is 30 s old. */
  marks: async (): Promise<Marks | null> => {
    const token = (await supa.auth.getSession()).data.session?.access_token;
    if (!token) return null;
    const r = await fetch("/api/marks", { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(8000) });
    const at = Date.now();
    if (!r.ok || !(r.headers.get("content-type") ?? "").includes("application/json")) return null;
    return parseMarks(await r.json().catch(() => null), at, r.headers.get("date"));
  },
  /** Streamed prices (lib/stream.ts, scripts/price_stream.py): joins the private Realtime channel that only the owner
   *  may read (migration 0032) and calls `on` with each message the relay broadcasts, at most one a second. Returns the
   *  function that stops `on`. Every page shares ONE channel (supabase-js hands out one channel per topic, so a page
   *  leaving its own would take the next page's with it); the channel is left 2 s after the last page stops, so a move
   *  between Live and Positions keeps it. Refused (not the owner, 0032 not applied, the relay off): no message comes
   *  and the page stays on the 10 s poll; a refusal leaves the channel, so nothing retries until a page asks again. */
  prices: (on: (s: Stream) => void, onDown?: (down: boolean) => void): (() => void) => {
    const p = priceStream;
    p.listeners.add(on);
    if (onDown) { p.downs.add(onDown); onDown(p.down); }
    if (p.leaveTimer) { clearTimeout(p.leaveTimer); p.leaveTimer = null; }
    if (!p.ch && !p.joining) void joinPrices();
    let done = false;
    return () => {
      if (done) return;
      done = true;
      p.listeners.delete(on);
      if (onDown) p.downs.delete(onDown);
      if (!p.listeners.size && !p.leaveTimer) p.leaveTimer = setTimeout(leavePrices, PRICES_LEAVE_MS);
    };
  },
  /** Stock summaries (migration 0023): the card from /api/summary, which writes today's on the first view (owner only,
   *  inside its own caps). Where the function is not deployed (local dev) the stored card is read with the RPC.
   *  Null: no card yet, or summaries unavailable here. */
  stockSummary: async (symbol: string) => {
    const token = (await supa.auth.getSession()).data.session?.access_token;
    if (token) {
      const r = await fetch(`/api/summary?symbol=${encodeURIComponent(symbol)}`, { headers: { Authorization: `Bearer ${token}` } });
      const isJson = (r.headers.get("content-type") ?? "").includes("application/json");
      if (r.ok && isJson) return parseSummary(((await r.json()) as { summary?: unknown })?.summary ?? null);
      if (r.status === 401 || r.status === 403) throw Object.assign(new Error("not authorised"), { code: "42501" });
      // no function here (local dev serves index.html; an old deployment 404s): read the stored card instead
      if (isJson && r.status !== 404 && r.status !== 405) throw new Error(`The summary could not be loaded (HTTP ${r.status}).`);
    }
    return parseSummary(await optional(() => rpc<unknown>("kb_stock_summary", { p_symbol: symbol })));
  },
  summarySpend: async () => {
    const r = await optional(() => rpc<Record<string, unknown> | null>("kb_summary_spend"));
    return r ? { day_usd: Number(r.day_usd ?? 0), month_usd: Number(r.month_usd ?? 0), day_cap: Number(r.day_cap ?? 0.3),
      month_cap: Number(r.month_cap ?? 5), cards_today: Number(r.cards_today ?? 0),
      // 0026/0027: the combined lifetime total and its cap, and the calls that settled above their reservation
      lifetime_usd: optNum(r.lifetime_usd), lifetime_cap: optNum(r.lifetime_cap), over_reservation_calls: optNum(r.over_reservation_calls) } : null;
  },
  newRun: (amount: number, level: Level) =>
    rpc<{ queued: boolean }>("kb_new_run", { p_amount: amount, p_level: level, p_idem: crypto.randomUUID() }),
};
