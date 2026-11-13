// #210: the trend_momentum paper experiment (decision 15 section 11), in plain words. Pure: unit-tested in
// experiment.test.ts. Loaded with the shell (riskStatus uses the halt helpers), so it stays small.
//
// Sources, all optional (an older engine, or KAIBOT_MULTIDAY off, sends none of them, and then nothing new shows):
// - heartbeat `detail.experiments[]` (#205, risk/profiles.experiments): the label row of each multi-day rule with a
//   Gate B verdict: strategy, label, evidence, experiment, real_money, level, max_exposure_pct, retire_drawdown_pct.
// - heartbeat `detail.multi_day` (#207, engine/live_daily.LiveDaily.status): the book (`open` | `ended`), per rule
//   `{ retired, note, last_decision, waiting }`, and one joined `note`.
// - halt rows (#207): `book_drawdown` (the book reached its stop line), `multi_day_retired` (reason
//   "rule=<rule>; <rule> retired: book fell 21.3% from its peak on <date>"), `multi_day_reenabled`.
// Every field is untrusted jsonb: read through the parsers below, rendered as plain text.
import type { Halt } from "./api";

export interface Experiment {
  strategy: string;
  label: string;            // "Experiment: failed the ten-year test"
  evidence: string | null;  // "failed_gate_b"
  experiment: boolean;
  realMoney: boolean;
  level: string | null;
  capPct: number | null;    // max_exposure_pct at the run's level
  retirePct: number | null; // retire_drawdown_pct at the run's level
}

export interface DailyRuleState { rule: string; retired: boolean; note: string | null; lastDecision: string | null; waiting: string | null }
export interface MultiDay { bookEnded: boolean; rules: DailyRuleState[] }

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const str = (v: unknown, max = 300) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const numOrNull = (v: unknown) => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
/** "trend_momentum~a" (a neighbour setting) has its rule's label. */
export const baseRule = (code: string) => code.split("~", 1)[0];

/** The experiment rows of an engine heartbeat's `detail`; [] when there are none or they don't parse. */
export function experimentsFrom(detail: unknown): Experiment[] {
  const list = obj(detail)?.experiments;
  if (!Array.isArray(list)) return [];
  const out: Experiment[] = [];
  for (const raw of list.slice(0, 20)) {
    const r = obj(raw);
    const strategy = str(r?.strategy, 80);
    const label = str(r?.label, 120);
    if (!r || !strategy || !label) continue;
    out.push({
      strategy, label, evidence: str(r.evidence, 40), experiment: r.experiment === true,
      // Anything but an explicit `true` reads as paper only: the engine never sends true (registry.EVIDENCE asserts it).
      realMoney: r.real_money === true, level: str(r.level, 20),
      capPct: numOrNull(r.max_exposure_pct), retirePct: numOrNull(r.retire_drawdown_pct),
    });
  }
  return out;
}

/** The heartbeat's `multi_day` block, or null when the daily path is not on. */
export function multiDayFrom(detail: unknown): MultiDay | null {
  const md = obj(obj(detail)?.multi_day);
  if (!md) return null;
  const rules: DailyRuleState[] = Object.entries(obj(md.rules) ?? {}).slice(0, 20).map(([rule, v]) => {
    const r = obj(v) ?? {};
    return { rule, retired: r.retired === true, note: str(r.note), lastDecision: str(r.last_decision, 20), waiting: str(r.waiting, 200) };
  });
  return { bookEnded: md.book === "ended", rules };
}

export const findExperiment = (code: string, list: Experiment[]) => list.find((e) => e.strategy === baseRule(code)) ?? null;

const fmtPct = (v: number) => `${Number.isInteger(v) ? v : Number(v.toFixed(1))}%`;

/** What the badge says, its one-line explanation (hover or tap), the cap and the stop line, in plain words. */
export function experimentView(e: Experiment) {
  // The engine's label is "Experiment: failed the ten-year test"; shown as "Experiment — failed the ten-year test".
  const head = e.label.replace(/^\s*experiment\s*:\s*/i, "Experiment — ");
  const failed = e.evidence === "failed_gate_b";
  const text = e.realMoney ? head : `${head} · paper only`;
  const tip = (failed ? "This rule did not pass the ten-year test of past prices. " : "")
    + (e.experiment ? "It trades on paper to see how it behaves live; the result can't count as a pass. " : "")
    + (e.realMoney ? "" : "It never trades real money.");
  return {
    text, tip: tip.trim(),
    cap: e.capPct !== null ? `up to ${fmtPct(e.capPct)} invested` : null,
    stop: e.retirePct !== null ? `retires if it falls ${fmtPct(e.retirePct)} from its peak` : null,
  };
}

// ---------------------------------------------------------------- the daily path's notes (heartbeat)

const DATES = /\d{4}-\d{2}-\d{2}/g;
const dayText = (iso: string) =>
  new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(iso + "T00:00:00Z"));

/** One "no decision" note of the daily path (LiveDaily._no_decision) in plain words. */
export function noDecisionText(note: string): string {
  const n = note.toLowerCase();
  if (n.startsWith("no decision today: daily store not refreshed"))
    return "No decision today: the daily prices weren't refreshed after the last close, so the rule made no new choice.";
  if (n.startsWith("no decision yet")) return "No decision yet: the rule makes its first choice after a market close.";
  if (n.startsWith("no decision today: none was taken"))
    return "No decision today: none was made after the last close (was the engine off then?).";
  return note.charAt(0).toUpperCase() + note.slice(1);
}

/** "trend_momentum retired: book fell 21.3% from its peak on 2026-11-20" -> drawdown and date (either may be missing). */
export function parseRetired(note: string): { fellPct: number | null; session: string | null } {
  const fell = /fell\s+(\d+(?:\.\d+)?)%/i.exec(note);
  const dates = note.match(DATES);
  return { fellPct: fell ? Number(fell[1]) : null, session: dates ? dates[dates.length - 1] : null };
}

export function retiredText(name: string, fellPct: number | null, session: string | null): string {
  const fell = fellPct !== null ? `its book fell ${fmtPct(fellPct)} from its peak` : "its book reached its stop line";
  return `${name} retired: ${fell}${session ? ` on ${dayText(session)}` : ""}. It trades no more until you switch it back on.`;
}

// ---------------------------------------------------------------- halt rows (#207)

/** How a halt reads. `breach`: a limit or safety halt (red). `ended`: the experiment ended as planned (amber).
 *  `info`: nothing to act on (the stakeholder switched a rule back on). `note`: an operations note (amber). */
export type HaltTone = "breach" | "ended" | "info" | "note";
export const EXPERIMENT_HALTS = ["book_drawdown", "multi_day_retired", "multi_day_reenabled"] as const;

export function haltTone(kind: string): HaltTone {
  if (kind === "multi_day_retired" || kind === "book_drawdown") return "ended";
  if (kind === "multi_day_reenabled") return "info";
  if (kind === "deadman" || kind === "watchdog") return "note";
  return "breach";
}

/** The rule a #207 halt names: "rule=trend_momentum; ..." */
export const haltRule = (h: Pick<Halt, "reason">) => /(?:^|;\s*)rule=([A-Za-z0-9_~]+)/.exec(h.reason ?? "")?.[1] ?? null;

/** Plain text of the three #207 halt kinds, or null for any other kind. `name` turns a rule code into its name. */
export function experimentHaltText(h: Halt, name: (code: string) => string): string | null {
  const reason = h.reason ?? "";
  if (h.kind === "multi_day_retired") {
    const rule = haltRule(h);
    const r = parseRetired(reason);
    return `Experiment ended: ${retiredText(rule ? name(rule) : "the multi-day rule", r.fellPct, r.session ?? h.session ?? null)}`;
  }
  if (h.kind === "book_drawdown") {
    const dd = /drawdown\s+(\d+(?:\.\d+)?)%/i.exec(reason), end = /end of\s+(\d+(?:\.\d+)?)%/i.exec(reason);
    const fell = dd ? ` fell ${fmtPct(Number(dd[1]))} from its peak` : " reached its stop line";
    return `Experiment ended: the multi-day book${fell}${end ? ` (stop line ${fmtPct(Number(end[1]))})` : ""}; its shares are sold, the day rules go on`;
  }
  if (h.kind === "multi_day_reenabled") {
    const rule = haltRule(h);
    return `${rule ? name(rule) : "A multi-day rule"} switched back on by you`;
  }
  return null;
}

/** A retirement that a later re-enabling of the same rule has undone. */
export function retirementUndone(h: Halt, all: Halt[]): boolean {
  if (h.kind !== "multi_day_retired") return false;
  const rule = haltRule(h), t = Date.parse(h.ts);
  return all.some((x) => x.kind === "multi_day_reenabled" && haltRule(x) === rule && Date.parse(x.ts) > t);
}
