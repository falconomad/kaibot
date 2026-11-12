// M5 estimator (S45). Split from derive.ts: only the Strategy pages need it (and it pulls in config.ts).
import { SESSION_OPEN, focusSet, toMin, type ConfigBody, type Themes } from "./config";
import { BAR } from "./model";

// ---------------------------------------------------------------- M5 estimator (S45, contract "Estimator")
export interface EstimatorData { dip_per_month: Record<string, number>; entry_share_by_half_hour: Record<string, number> }
export type Pace = "very slow evidence" | "slow" | "normal";
/** `unmeasured`: tradable symbols with no replay trade rate (custom tickers). While any is in the set, the honest
 * figure is "not measured yet": perMonth only counts the measured ones. */
export interface Estimate { perMonth: number; monthsTo900: number | null; pace: Pace; spyNotEstimated: boolean; unmeasured: string[] }

/** Share of replay entries that fall inside [start, end] (minutes after 09:30), relative to the level's full window,
 * so the level's own window is exactly 1. Each half-hour bucket is assumed uniform inside itself. */
export function windowShare(start: number, end: number, est: EstimatorData, full: [number, number] = [5, 375]): number {
  let num = 0, den = 0;
  for (const [k, share] of Object.entries(est.entry_share_by_half_hour)) {
    const lo = Number(k), hi = lo + 30;
    const fullOv = Math.max(0, Math.min(hi, full[1]) - Math.max(lo, full[0]));
    if (!fullOv) continue;
    const ov = Math.max(0, Math.min(hi, end, full[1]) - Math.max(lo, start, full[0]));
    den += share;
    num += share * (ov / fullOv);
  }
  return den > 0 ? num / den : 0;
}

/** ≈ trades a month for a config: Σ over tradable symbols of dip_per_month × the selected share of the entry window
 * (dip on). Only mode trades the focus set; All and Prefer trade the whole universe. SPY momentum is not estimated. */
export function estimateTrades(body: ConfigBody, est: EstimatorData, themes: Themes,
  entry: { start: string; end: string } = { start: "09:35", end: "15:45" }): Estimate {
  const open = SESSION_OPEN;
  // excluded stocks are never traded, in any mode
  const tradable = body.focus.mode === "only" ? focusSet(body, themes)
    : Object.keys(est.dip_per_month).filter((s) => !body.focus.exclude.includes(s));
  const share = windowShare(toMin(body.timing.entry_start) - open, toMin(body.timing.entry_end) - open, est,
    [toMin(entry.start) - open, toMin(entry.end) - open]);
  const perMonth = body.strategies.dip_reversal ? tradable.reduce((a, s) => a + (est.dip_per_month[s] ?? 0), 0) * share : 0;
  return { perMonth, monthsTo900: perMonth > 0 ? BAR.trades / perMonth : null, pace: perMonth < 15 ? "very slow evidence" : perMonth < 40 ? "slow" : "normal",
    spyNotEstimated: body.strategies.spy_late_momentum, unmeasured: tradable.filter((s) => !(s in est.dip_per_month)) };
}

/** Plain words for the evidence pace. */
export function paceText(e: Estimate): string {
  if (e.unmeasured.length) return "Kaibot has no measured trade rate for tickers new to it. The figure appears after this list's first replay.";
  if (e.monthsTo900 === null) return e.spyNotEstimated ? "The dip rule is off; the SPY late-day rule is not estimated yet." : "No trades expected.";
  const m = e.monthsTo900;
  const when = m >= 24 ? `about ${Math.round(m / 12)} years` : `about ${Math.max(1, Math.round(m))} months`;
  return `The go-live bar needs ${BAR.trades} trades: ${when} at this pace (${e.pace}).`;
}

/** "≈ 12" or "not measured yet" (a custom ticker has no trade rate). */
export const estShort = (e: Estimate) => (e.unmeasured.length ? "not measured yet" : `≈ ${Math.round(e.perMonth)}`);
