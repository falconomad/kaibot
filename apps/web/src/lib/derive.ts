// Pure helpers for the M2 screens. No React, no network: unit-tested in derive.test.ts.
// Home and the shell load this module in the first chunk, so it stays small: the agent room (roomview.ts), the
// go/no-go rows (gateview.ts) and the M5 estimator (estimator.ts) live in their own modules, loaded with their pages.
import type { AttributionGroup, Evidence, Halt, Level, Overview, Report, Run, Scan } from "./api";
import { BAR, LEVELS } from "./model";
import { experimentHaltText, haltTone, retirementUndone } from "./experiment";
import { ruleName } from "./explain";
import { NAME } from "../brand/brand";

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

/** S28: one status for the risk strip. Red when any limit or safety halt is in force; amber when the paper experiment
 *  ended as planned (#210). */
export type Severity = "ok" | "warn" | "breach";
export function riskStatus(run: Run, o: Overview | undefined, dayPct: number, ddPct: number, now = Date.now()):
  { severity: Severity; items: string[]; red: string[] } {
  const items: string[] = [];
  /** v4 (Product, 16 Nov 2026): the red kinds a phone shows on every page: a limit or safety halt in force, the run
   *  ended, the daily stop, the watchdog or dead-man acting. Amber and info states are not in it. */
  const red: string[] = [];
  let severity: Severity = "ok";
  const add = (text: string, s: Severity, isRed = s === "breach") => {
    items.push(text);
    if (isRed) red.push(text);
    if (s === "breach" || (s === "warn" && severity === "ok")) severity = s;
  };
  const lv = run.level ? LEVELS[run.level] : null;
  const beat = o?.heartbeat.find((h) => h.process === "engine");
  const detail = (beat?.detail ?? {}) as { halted?: boolean; account_clean?: boolean };
  if (run.status === "paused") add("Entries paused", "warn");
  if (detail.halted) add("Engine halted entries for today", "breach");
  if (detail.account_clean === false) add(`Paper account has positions or orders ${NAME} didn't place`, "breach");
  // A watchdog halt is over once the engine is back: a fresh heartbeat newer than the halt.
  const fresh = !!beat?.ts && ageS(beat.ts, now) < 120;
  // #210: the experiment's planned end (book stop line, retirement) is amber, never a red breach; a re-enabling is
  // good news and not a halt at all, and it undoes an earlier retirement of the same rule.
  const halts = o?.halts_today ?? [];
  for (const h of halts.filter((x) => !(fresh && haltResolved(x, beat?.ts)) && !retirementUndone(x, halts))) {
    const t = haltTone(h.kind);
    if (t === "info") continue;
    add(haltText(h), t === "breach" ? "breach" : "warn", t === "breach" || t === "note");
  }
  if (lv && dayPct <= -lv.stop) add(`Daily stop reached (−${lv.stop}%)`, "breach");
  else if (lv && dayPct < 0 && -dayPct >= 0.8 * lv.stop) add("Close to the daily stop", "warn");
  if (lv && ddPct >= lv.runDd) add(`Run drawdown cap reached (${lv.runDd}%)`, "breach");
  else if (lv && ddPct >= 0.8 * lv.runDd) add("Close to the run drawdown cap", "warn");
  return { severity, items: [...new Set(items)], red: [...new Set(red)] };
}

/** The run shown when you haven't picked one: the newest, except while the newest paper run is only scheduled. Then
 *  the last paper run that traded stays on screen (Home labels it "Last run (ended)" and shows the new run in a row)
 *  until the new run starts (stakeholder, 16 Nov 2026). `runs` is newest first, as kb_runs returns it. */
export function defaultRun<T extends Pick<Run, "kind" | "status" | "started_on">>(runs: T[]): T | undefined {
  const first = runs[0];
  if (!first || first.kind !== "paper" || first.status !== "scheduled") return first;
  return runs.find((r) => r.kind === "paper" && r.status !== "scheduled" && !!r.started_on) ?? first;
}

/** v4 (Product, 16 Nov 2026): a run that a limit ended is a red kind on the phone bar. A run you stopped, or one
 *  replaced by a new run, is not: nothing went wrong. The engine ends a run with status "ended" and the reason
 *  "drawdown N% from peak" (engine/core.py), or marks it "halted". */
export function runEndedRed(run: Pick<Run, "status" | "end_reason">): string[] {
  const why = (run.end_reason ?? "").trim();
  if (run.status === "halted") return [`Run ended by a limit${why ? `: ${why}` : ""}`];
  if (run.status === "ended" && /^drawdown\b/i.test(why)) return [`Run ended: ${why}`];
  return [];
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
  config: "Strategy settings refused: the level's defaults apply",
  llm_budget: "Monthly LLM budget reached: no new entries",
  broker_rejects: "The broker rejected several orders in a row: entries halted",
  // the replicator's notes for the dashboard: trading is not affected (engine/__main__.report_conflicts)
  replica_conflict: "The dashboard's copy differs from the engine's record: trading goes on",
  replica_poison: "The dashboard's copy lacks a record it refused: trading goes on",
};
export const haltText = (h: Halt) =>
  HALT_TEXT[h.kind] ?? experimentHaltText(h, ruleName) ?? `${h.kind.replace(/_/g, " ")}: ${h.reason}`;

/** A watchdog halt is resolved once the engine is back: a heartbeat newer than the halt, or a later
 *  `watchdog_recovered` event. The banner also asks for the heartbeat to be fresh (riskStatus); history only asks
 *  whether the engine came back after the halt. Other halt kinds are never "resolved" here. */
export function haltResolved(h: Halt, beatTs: string | null | undefined, recoveredTs: string[] = []): boolean {
  if (h.kind !== "watchdog") return false;
  const t = new Date(h.ts).getTime();
  if (Number.isNaN(t)) return false;
  const after = (iso: string | null | undefined) => !!iso && new Date(iso).getTime() > t;
  return after(beatTs) || recoveredTs.some(after);
}

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

/** Seconds since an ISO timestamp. */
export const ageS = (iso: string, now = Date.now()) => Math.max(0, (now - new Date(iso).getTime()) / 1000);

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
