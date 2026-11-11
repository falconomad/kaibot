// Pure helpers for the M2 screens. No React, no network: unit-tested in derive.test.ts.
import type { AttributionGroup, Evidence, Halt, Level, Overview, Report, Run } from "./api";
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
