// Pure helpers for the M2 screens. No React, no network: unit-tested in derive.test.ts.
import type { AttributionGroup, BudgetState, Evidence, Halt, Level, LlmBudget, Overview, PackItem, Report, RoomAB, RoomSession,
  RoomStatus, RoomVerdict, Run, Scan, GateStrategy, GateOverall } from "./api";
import { BAR, LEVELS } from "./model";

export const etToday = (d = new Date()) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);

/** S29: how far along the evidence is, and what the confidence interval says. Never overstates. */
export function evidenceVerdict(e: Evidence | undefined): { frac: number; text: string; tone: "flat" | "gain" | "loss" } {
  if (!e || e.n === 0) return { frac: 0, text: "No closed trades yet.", tone: "flat" };
  const frac = Math.min(1, e.n / BAR.trades);
  if (e.n < 30 || e.ci_lo === null || e.ci_hi === null)
    return { frac, text: `${e.n} trades: too few to say anything yet (need ≥ 30 for an interval).`, tone: "flat" };
  const ci = `95% interval for the average trade: ${fmtR(e.ci_lo)} to ${fmtR(e.ci_hi)}`;
  if (e.ci_lo > 0) return { frac, text: `${ci}. Above zero so far, but the go-live bar needs t > ${BAR.t} on ≥ ${BAR.trades} trades.`, tone: "gain" };
  if (e.ci_hi < 0) return { frac, text: `${ci}. Below zero: the strategy is losing money after costs.`, tone: "loss" };
  return { frac, text: `${ci}. It includes zero: no evidence of an edge yet.`, tone: "flat" };
}

export const fmtR = (r: number | null | undefined) =>
  r === null || r === undefined || Number.isNaN(r) ? "—" : (r >= 0 ? "+" : "−") + Math.abs(r).toFixed(2) + "R";

/** S28: one status for the risk strip. Red when any limit or safety halt is in force. */
export type Severity = "ok" | "warn" | "breach";
export function riskStatus(run: Run, o: Overview | undefined, dayPct: number, ddPct: number):
  { severity: Severity; items: string[] } {
  const items: string[] = [];
  let severity: Severity = "ok";
  const bump = (s: Severity) => { if (s === "breach" || (s === "warn" && severity === "ok")) severity = s; };
  const lv = run.level ? LEVELS[run.level] : null;
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  const detail = (beat?.detail ?? {}) as { halted?: boolean; account_clean?: boolean };
  if (run.status === "paused") { items.push("Entries paused"); bump("warn"); }
  if (detail.halted) { items.push("Engine halted entries for today"); bump("breach"); }
  if (detail.account_clean === false) { items.push("Paper account has positions or orders Kaibot didn't place"); bump("breach"); }
  for (const h of o?.halts_today ?? []) { items.push(haltText(h)); bump(h.kind === "deadman" || h.kind === "watchdog" ? "warn" : "breach"); }
  if (lv && dayPct <= -lv.stop) { items.push(`Daily stop reached (−${lv.stop}%)`); bump("breach"); }
  else if (lv && dayPct < 0 && -dayPct >= 0.8 * lv.stop) { items.push("Close to the daily stop"); bump("warn"); }
  if (lv && ddPct >= lv.runDd) { items.push(`Run drawdown cap reached (${lv.runDd}%)`); bump("breach"); }
  else if (lv && ddPct >= 0.8 * lv.runDd) { items.push("Close to the run drawdown cap"); bump("warn"); }
  return { severity, items: [...new Set(items)] };
}

export const HALT_TEXT: Record<string, string> = {
  daily_stop: "Daily stop: flattened and halted for today",
  run_drawdown: "Run drawdown cap: run ended",
  drift: "Positions differ from the broker: entries halted",
  engine_errors: "Repeated engine errors: entries halted",
  calendar_stale: "Event calendar out of date: entries halted",
  three_stops: "3 daily stops in ~10 sessions: run paused",
  watchdog: "Engine went quiet: watchdog cancelled unfilled entries",
  deadman: "Dead-man check ran at 15:56 ET",
  losses: "3 losses in a row: 60-minute pause",
};
export const haltText = (h: Halt) => HALT_TEXT[h.kind] ?? `${h.kind.replace(/_/g, " ")}: ${h.reason}`;

/** S23: the level that applies today and any change already scheduled for a later session. */
export function levelPlan(run: Run, segments: { level: Level; from_session: string }[] | undefined, today = etToday()) {
  const segs = [...(segments ?? [])].sort((a, b) => a.from_session.localeCompare(b.from_session));
  const current = [...segs].reverse().find((s) => s.from_session <= today)?.level ?? run.level;
  const next = segs.filter((s) => s.from_session > today).at(-1) ?? null;
  return { current, next: next && next.level !== current ? next : null };
}

/** S30: the day against SPY and cash, in percent of the day's starting pot. */
export function dayVsBenchmarks(r: Report) {
  const start = r.start_equity ?? null;
  const pnl = r.day?.pnl ?? 0;
  const potPct = start ? (pnl / start) * 100 : 0;
  const spyPct = (r.day?.spy_ret ?? 0) * 100;
  const cashPct = (r.day?.cash_ret ?? 0) * 100;
  return { potPct, spyPct, cashPct, beatSpy: potPct > spyPct, beatCash: potPct > cashPct };
}

/** S31: losers first; flags explained in plain words. */
export const FLAG_TEXT = { kill: "Losing with ≥ 20 trades and t ≤ −2: consider switching it off",
  watch: "Losing on average over ≥ 10 trades: watch" } as const;
export function flagged(groups: AttributionGroup[]) {
  return groups.filter((g) => g.flag).sort((a, b) => (a.flag === b.flag ? a.pnl - b.pnl : a.flag === "kill" ? -1 : 1));
}
export const DIM_LABEL: Record<string, string> = {
  strategy: "Strategy", exit_reason: "How it closed", hour_et: "Entry hour (ET)", weekday: "Weekday", symbol: "Stock",
};

/** Seconds since an ISO timestamp, and a short human form. */
export const ageS = (iso: string, now = Date.now()) => Math.max(0, (now - new Date(iso).getTime()) / 1000);
export const ageText = (s: number) =>
  s < 120 ? `${Math.round(s)} s ago` : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;

// ---------- M3 agent room (S35 transcripts, S36 A/B) ----------
// Everything the models wrote is untrusted: these helpers only ever return plain strings/numbers, never markup.

const str = (v: unknown, max = 2000) => (typeof v === "string" ? v.slice(0, max) : typeof v === "number" ? String(v) : "");
const obj = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const arr = (v: unknown) => (Array.isArray(v) ? v : []);

/** Only http(s) links are ever rendered as links (no javascript:, data:, relative paths). */
export function safeUrl(u: unknown): string | null {
  if (typeof u !== "string") return null;
  try {
    const x = new URL(u);
    return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
  } catch { return null; }
}

/** The evidence pack, defensively parsed and keyed by id. */
export function packIndex(evidence: RoomSession["evidence"]): Map<string, PackItem> {
  const m = new Map<string, PackItem>();
  for (const raw of arr(obj(evidence).items)) {
    const o = obj(raw), id = str(o.id, 80);
    if (!id || m.has(id)) continue;
    const t = obj(o.triage);
    m.set(id, { id, kind: str(o.kind, 40), source: str(o.source, 120), published_at: str(o.published_at, 40),
      headline: str(o.headline, 400), url: safeUrl(o.url) ?? undefined,
      n_outlets: typeof o.n_outlets === "number" ? o.n_outlets : Number(o.n_outlets) || undefined,
      triage: o.triage ? { cause: str(t.cause, 40), materiality: str(t.materiality, 40), direction: str(t.direction, 40),
        summary: str(t.summary, 600) } : null });
  }
  return m;
}

export interface ResolvedRef { id: string; item: PackItem | null; source: string; published_at: string }
/** S35 "linked, sourced": every citation resolves to its pack item, or is marked unverified (item null). Deduplicated, order kept. */
export function resolveEvidence(refs: unknown, pack: Map<string, PackItem>): ResolvedRef[] {
  const out: ResolvedRef[] = [], seen = new Set<string>();
  for (const r of arr(refs)) {
    const o = typeof r === "string" ? { id: r } : obj(r);
    const id = str(o.id, 80);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const item = pack.get(id) ?? null;
    out.push({ id, item, source: item?.source || str(o.source, 120), published_at: item?.published_at || str(o.published_at, 40) });
  }
  return out;
}

export interface RoomView {
  proposer: { thesis: string; cause: string; evidence: ResolvedRef[] } | null;
  critic: { counter: string; cause: string; redFlags: string[]; evidence: ResolvedRef[] } | null;
  judge: { verdict: string; cause: string; confidence: string; invalidation: string; horizon: number | null; evidence: ResolvedRef[] } | null;
  pack: PackItem[]; unverified: number; cited: Set<string>;
}
/** A safe, typed view of a room session for the transcript. */
export function roomView(s: RoomSession): RoomView {
  const pack = packIndex(s.evidence);
  const p = s.proposer ? obj(s.proposer) : null, c = s.critic ? obj(s.critic) : null, j = s.judge ? obj(s.judge) : null;
  const view: RoomView = {
    proposer: p ? { thesis: str(p.thesis, 400), cause: str(p.cause, 40), evidence: resolveEvidence(p.evidence, pack) } : null,
    critic: c ? { counter: str(c.strongest_counter, 400), cause: str(c.cause, 40),
      redFlags: arr(c.red_flags).map((x) => str(x, 200)).filter(Boolean).slice(0, 6), evidence: resolveEvidence(c.evidence, pack) } : null,
    judge: j ? { verdict: str(j.verdict, 20), cause: str(j.cause, 40), confidence: str(j.confidence, 20), invalidation: str(j.invalidation, 200),
      horizon: typeof j.horizon_minutes === "number" ? j.horizon_minutes : null, evidence: resolveEvidence(j.evidence_ids, pack) } : null,
    pack: [...pack.values()], unverified: 0, cited: new Set(),
  };
  for (const refs of [view.proposer?.evidence, view.critic?.evidence, view.judge?.evidence])
    for (const r of refs ?? []) { view.cited.add(r.id); if (!r.item) view.unverified++; }
  return view;
}

export const CAUSE_TEXT: Record<string, string> = {
  company_news: "company news", sector: "sector move", macro: "macro", noise: "no identifiable cause (noise)", unknown: "unknown",
};

/** S35: what the room decided and what happened, in plain words. tag is a CSS tag class. */
export function roomOutcome(status: RoomStatus, verdict: RoomVerdict | null, error?: string | null, rulesOnly?: string | null):
  { label: string; text: string; tag: "allow" | "downgrade" | "veto" | "" } {
  switch (status) {
    case "pending": return { label: "deliberating", text: "The room is still deliberating (it has at most 45 s).", tag: "" };
    case "timeout": return { label: "no answer", text: "No trade: the room did not answer within 45 s.", tag: "veto" };
    case "invalid": return { label: "invalid", text: "No trade: the room's answer failed validation (for example it cited evidence that isn't in the pack).", tag: "veto" };
    case "error": return { label: "error", text: "No trade: the room call failed, and Kaibot fails closed.", tag: "veto" };
    case "skipped": {
      const why = /budget|cap|spend/i.test(error ?? "") ? "daily LLM budget reached"
        : /off|disabled/i.test(error ?? "") ? "the agent room is switched off" : "daily LLM budget reached or the room is off";
      const rules = rulesOnly === "veto" ? " The rules said no trade." : " The deterministic rules decided: full size.";
      return { label: "rules-only", text: `Rules-only: ${why}.${rules}`, tag: "" };
    }
    default:
      if (verdict === "allow") return { label: "allow", text: "Allowed: traded at full size.", tag: "allow" };
      if (verdict === "downgrade") return { label: "downgrade", text: "Downgraded: traded at half size.", tag: "downgrade" };
      if (verdict === "veto") return { label: "veto", text: "Vetoed: no trade.", tag: "veto" };
      return { label: "no verdict", text: "No verdict recorded: no trade.", tag: "veto" };
  }
}

/** Difference of two means with a simple (Welch, normal) 95% interval. null when either side has fewer than 2. */
export function liftInterval(a: { n: number; mean: number | null; sd: number | null }, b: { n: number; mean: number | null; sd: number | null }) {
  if (a.n < 2 || b.n < 2 || a.mean === null || b.mean === null || a.sd === null || b.sd === null) return null;
  const diff = a.mean - b.mean, se = Math.sqrt((a.sd ** 2) / a.n + (b.sd ** 2) / b.n);
  return { diff, lo: diff - 1.96 * se, hi: diff + 1.96 * se };
}

export const MIN_VETOES = 30;
/** S36: does the room earn its place? Never overstates: < 30 vetoes with a known outcome is "too early to tell". */
export function roomAbVerdict(ab: RoomAB | undefined): { text: string; tone: "flat" | "gain" | "loss"; lift: ReturnType<typeof liftInterval> } {
  if (!ab || ab.n === 0) return { text: "No room sessions in this period yet.", tone: "flat", lift: null };
  const lift = liftInterval({ n: ab.allow.n_closed, mean: ab.allow.mean_r, sd: ab.allow.sd_r },
    { n: ab.veto.n_cf, mean: ab.veto.mean_r, sd: ab.veto.sd_r });
  if (ab.veto.n_cf < MIN_VETOES || !lift)
    return { text: `Too early to tell: ${ab.veto.n_cf} vetoed candidate${ab.veto.n_cf === 1 ? "" : "s"} with a known outcome (need ≥ ${MIN_VETOES}).`,
      tone: "flat", lift };
  const ci = `veto lift ${fmtR(lift.diff)}, 95% interval ${fmtR(lift.lo)} to ${fmtR(lift.hi)}`;
  if (lift.lo > 0) return { text: `Vetoed candidates did worse than the trades the room allowed (${ci}). The vetoes look useful so far; weigh that against the LLM spend.`, tone: "gain", lift };
  if (lift.hi < 0) return { text: `The room is vetoing better candidates than it allows (${ci}). Rules-only would have done better.`, tone: "loss", lift };
  return { text: `No evidence yet that the vetoes help: the interval includes zero (${ci}).`, tone: "flat", lift };
}

/** S32/S36: today's budget state in plain words. */
export function budgetText(b: Pick<LlmBudget, "state" | "alert" | "caps">): { text: string; tone: "ok" | "warn" | "breach" } {
  const s: BudgetState = b.state;
  if (s === "no_entries") return { text: `No new entries: the monthly LLM hard cap ($${b.caps.monthly_hard}) is reached.`, tone: "breach" };
  if (s === "rules_only") return { text: `Rules-only for the rest of today: the daily LLM cap ($${b.caps.daily_soft}) is reached, so the room is skipped.`, tone: "warn" };
  if (b.alert) return { text: `Room on, but this month's spend passed the $${b.caps.monthly_alert} alert.`, tone: "warn" };
  return { text: "Room on: spend is within the daily and monthly caps.", tone: "ok" };
}

/** A timestamp from model/pack data: formatted if it parses, else shown as-is. */
export const safeDate = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

/** Plain-language summary of today's dip scan (engine heartbeat). Never claims a cause the counters don't show. */
export function scanSummary(sc: Scan | null | undefined, entries = 0): { headline: string; lines: string[] } {
  if (!sc) return { headline: "No scan yet today", lines: ["The engine reports its scan once the session has started."] };
  const vetoed = Object.values(sc.vetoed ?? {}).reduce((a, b) => a + b, 0);
  const lines = [`${sc.bars} five-minute bars scanned, ${sc.checks.toLocaleString("en-US")} stock checks`];
  if (sc.regime_off) lines.push(`${sc.regime_off} bars skipped: market regime off (SPY weak or VIX > 35)`);
  for (const [why, n] of Object.entries(sc.vetoed ?? {}).sort((a, b) => b[1] - a[1])) lines.push(`${n} vetoed: ${why}`);
  if (sc.to_room) lines.push(`${sc.to_room} sent to the agent room`);
  if (sc.near) lines.push(`${sc.near} near-misses (a rebound formed but the dip didn't qualify)`);
  const headline = sc.setups === 0
    ? (sc.bars === 0 ? "Waiting for the first 5-minute bar (09:40 ET)" : "No qualifying dip yet today")
    : `${sc.setups} setup${sc.setups === 1 ? "" : "s"} found · ${vetoed} vetoed · ${entries} entered`;
  return { headline, lines };
}

/** Clock time (ET) at which the 5-minute bar ending at session minute m completed: 09:30 + m + 1. */
export function sessionTime(m: number): string {
  const t = 9 * 60 + 30 + m + 1;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

// ---------- S41 go/no-go ----------
export type GateRowState = "pass" | "fail" | "unknown" | "provisional" | "pending";
const GATE_ROWS: { key: string; label: string; fmt: (v: number) => string; lim: (l: number) => string }[] = [
  { key: "trades", label: "Trades on frozen code", fmt: (v) => v.toLocaleString("en-US"), lim: (l) => `≥ ${l}` },
  { key: "months", label: "Months of forward paper", fmt: (v) => v.toFixed(1), lim: (l) => `≥ ${l}` },
  { key: "t_stat", label: "Expectancy after costs (t-stat)", fmt: (v) => v.toFixed(2), lim: (l) => `> ${l}` },
  { key: "deflated_sharpe", label: "Deflated Sharpe (probability)", fmt: (v) => v.toFixed(2), lim: (l) => `> ${l}` },
  { key: "random_pct", label: "Beats random entries (percentile)", fmt: (v) => `${v.toFixed(1)}%`, lim: (l) => `≥ ${l}%` },
  { key: "sharpe_vs_spy", label: "Sharpe vs SPY (annualised)", fmt: (v) => v.toFixed(2), lim: (l) => `> SPY ${l.toFixed(2)}` },
  { key: "drawdown", label: "Max drawdown", fmt: (v) => `${(v * 100).toFixed(1)}%`, lim: (l) => `≤ ${(l * 100).toFixed(0)}%` },
];

/** Rows for one strategy. Below the bar's sample size a met row is never shown as a pass ("can only kill"). */
export function gateRows(st: GateStrategy): { key: string; label: string; value: string; limit: string; state: GateRowState }[] {
  const provisional = st.status === "provisional" || st.status === "kill";
  return GATE_ROWS.map((r) => {
    const c = st.criteria[r.key];
    const value = c?.value == null ? "—" : r.fmt(Number(c.value));
    const limit = c?.limit == null ? "—" : r.lim(Number(c.limit));
    let state: GateRowState = c?.met == null ? "unknown" : c.met ? "pass" : "fail";
    if (provisional && state === "pass" && r.key !== "drawdown") state = "provisional";
    if (st.status === "provisional" && state === "fail") state = "pending"; // not enough data yet: slate, not red
    return { key: r.key, label: r.label, value, limit, state };
  });
}

export function gateStatusText(s: GateStrategy["status"]): { label: string; tone: "pass" | "fail" | "warn" | "muted" } {
  switch (s) {
    case "pass": return { label: "Cleared the bar", tone: "pass" };
    case "kill": return { label: "Kill", tone: "fail" };
    case "fail": return { label: "Not yet", tone: "warn" };
    default: return { label: "Provisional: can only kill", tone: "muted" };
  }
}

export function gateOverallText(o: GateOverall | undefined): string {
  if (o === "discuss") return "A strategy cleared the pre-registered bar. That opens a written go-live discussion. Nothing switches automatically.";
  if (o === "kill") return "Every live strategy met a kill criterion. Stop or redesign before collecting more data.";
  return "Keep collecting. No strategy has cleared the bar yet.";
}
