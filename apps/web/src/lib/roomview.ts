// M3 agent room helpers (S35 transcripts, S36 A/B, S32 budget). Split from derive.ts so the first load stays small.
import type { BudgetState, LlmBudget, PackItem, RoomAB, RoomSession, RoomStatus, RoomVerdict } from "./api";
import { fmtR } from "./derive";

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
