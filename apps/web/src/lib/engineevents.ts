// The engine's newer records for the dashboard (19 Nov 2026, main 3c66c53). Pure: unit-tested in engineevents.test.tsx.
// Every field is untrusted jsonb, read defensively; a field the engine does not send yet means nothing is shown.
//
// Sources (engine/live_daily.py LiveDaily.status and its events; agents/daily_note.py; agents/preopen_shadow.py):
// - heartbeat detail.multi_day.exits {rule: "target_list" | "broker_stop"} and rules[<rule>].exit (#339);
// - event `multi_day_unfilled` {strategy, symbol, side, qty, filled, left, coid, session, status, purpose, type, tif}:
//   an order of the daily path the venue let expire at the close with shares left (#333);
// - event `daily_note` {session, text, source: "model" | "template", why, cost_usd}, and heartbeat
//   multi_day.daily_note {session, text, source} (#311, KAIBOT_DAILY_NOTE=on);
// - event `multi_day_review_shadow` {strategy, session, status, review_status, error, cost_usd, latency_ms, mode,
//   acted: false, would_veto, would_downgrade, allowed, reasons {symbol: "<verdict>: <flag>: <reason>"}}, and heartbeat
//   multi_day.rules[<rule>].review_shadow {mode, session, status, would_veto, would_downgrade, reasons, cost_usd, note}
//   (#310, KAIBOT_PREOPEN=shadow). An observer only: nothing in the engine reads it back.
// Events arrive through kb_health's `events`, which lists a fixed set of types: until Backend adds these three types
// to it, only the heartbeat's copies show.
import type { MultiDay } from "./experiment";
import { marketHours } from "./engineclock";

type Ev = { type: string; ts: string; run_id?: number | null; payload: Record<string, unknown> };
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const str = (v: unknown, max = 80) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
const syms = (v: unknown) => (Array.isArray(v) ? v.slice(0, 60).flatMap((s) => { const x = str(s, 12); return x ? [x] : []; }) : []);
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ofRun = (e: Ev, runId: number) => e.run_id === null || e.run_id === undefined || e.run_id === runId;

// ---------------------------------------------------------------- 1. exit kinds

/** rule -> exit kind, from the heartbeat (rules[*].exit, then the exits map, which wins). {} when not sent. */
export function exitKinds(md: MultiDay | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [rule, v] of Object.entries(obj(md?.raw?.rules) ?? {}).slice(0, 40)) {
    const k = str(obj(v)?.exit, 20);
    if (k) out[rule] = k;
  }
  for (const [rule, v] of Object.entries(obj(md?.raw?.exits) ?? {}).slice(0, 40)) {
    const k = str(v, 20);
    if (k) out[rule] = k;
  }
  return out;
}

// ---------------------------------------------------------------- 2. orders not filled at the close

export interface Unfilled { ts: string; rule: string; symbol: string; side: "buy" | "sell"; qty: number; filled: number; left: number; session: string | null }

/** The run's `multi_day_unfilled` events, newest first. */
export function unfilledFrom(events: Ev[] | undefined, runId: number): Unfilled[] {
  return (events ?? []).filter((e) => e.type === "multi_day_unfilled" && ofRun(e, runId)).flatMap((e): Unfilled[] => {
    const p = e.payload ?? {}, rule = str(p.strategy), symbol = str(p.symbol, 12), qty = num(p.qty), left = num(p.left);
    const side: Unfilled["side"] | null = p.side === "sell" ? "sell" : p.side === "buy" ? "buy" : null;
    if (!rule || !symbol || !side || qty === null || left === null || left <= 0) return [];
    const session = str(p.session, 10);
    return [{ ts: e.ts, rule, symbol, side, qty, filled: num(p.filled) ?? Math.max(0, qty - left), left,
      session: session && DATE.test(session) ? session : null }];
  }).sort((a, b) => b.ts.localeCompare(a.ts));
}

const shares = (n: number) => `${Number.isInteger(n) ? n : Number(n.toFixed(4))} share${n === 1 ? "" : "s"}`;
/** "MPC · 4 shares not filled · expired at the close" */
export const unfilledText = (u: Unfilled) => `${u.symbol} · ${shares(u.left)} not filled · expired at the close`;
/** The line under the symbol where the row already names it. */
export const unfilledLine = (u: Unfilled) => `${shares(u.left)} not filled · expired at the close`;
/** What happens next, from engine/live_daily.py's module notes. */
export const unfilledNext = (u: Unfilled) => u.side === "buy"
  ? "The buy is not sent again: it waits for the rule's next decision after a close."
  : "The sale is sent again at the next session's close.";
export const unfilledDetail = (u: Unfilled) =>
  `${u.side === "buy" ? "Buy" : "Sale"} of ${shares(u.qty)}: ${u.filled ? `${shares(u.filled)} filled, ` : "none filled, "}${shares(u.left)} expired at the close.`;

// ---------------------------------------------------------------- 3. the daily note

export interface DailyNote { session: string; text: string; source: "model" | "template"; why: string | null }

const noteOf = (v: unknown): DailyNote | null => {
  const o = obj(v), session = str(o?.session, 10), t = str(o?.text, 2000);
  if (!o || !session || !DATE.test(session) || !t) return null;
  // anything but an explicit "model" is labelled as the template: the AI label is never shown by default
  return { session, text: t, source: o.source === "model" ? "model" : "template", why: str(o.why, 200) };
};

/** The latest note: the heartbeat's multi_day.daily_note or a `daily_note` event, whichever has the later session. */
export function dailyNote(md: MultiDay | null | undefined, events: Ev[] | undefined): DailyNote | null {
  const all = [noteOf(md?.raw?.daily_note), ...(events ?? []).filter((e) => e.type === "daily_note").map((e) => noteOf(e.payload))]
    .filter((n): n is DailyNote => n !== null);
  // the event carries `why`; for the same session prefer it (stable sort keeps the heartbeat first otherwise)
  return all.reduce<DailyNote | null>((best, n) => (!best || n.session > best.session || (n.session === best.session && n.why && !best.why) ? n : best), null);
}

const etDay = (d: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
export const isToday = (n: DailyNote, now = new Date()) => n.session === etDay(now);

/** Who wrote it, in plain words. */
export const noteLabel = (n: DailyNote, now?: Date) => n.source === "model"
  ? `Written by AI from ${!now || isToday(n, now) ? "today's" : "that day's"} records` : "Written from a fixed template, not by AI";

/** After the close and until the next open: never during market hours, and not for a note older than four days. */
export function showNote(n: DailyNote, now = new Date()): boolean {
  if (marketHours(now)) return false;
  const age = (Date.parse(etDay(now) + "T00:00:00Z") - Date.parse(n.session + "T00:00:00Z")) / 86_400_000;
  return age >= 0 && age <= 4;
}

// ---------------------------------------------------------------- 4. the shadow pre-open review

export interface ShadowReview {
  rule: string; session: string | null; status: string; veto: string[]; downgrade: string[]; allowed: string[];
  reasons: { symbol: string; verdict: "veto" | "downgrade"; why: string }[]; costUsd: number | null; error: string | null;
}

const reasonsOf = (v: unknown) => Object.entries(obj(v) ?? {}).slice(0, 40).flatMap(([symbol, r]) => {
  const t = str(r, 200);
  const m = t ? /^(veto|downgrade):\s*(.*)$/i.exec(t) : null;
  return m ? [{ symbol: symbol.slice(0, 12), verdict: m[1].toLowerCase() as "veto" | "downgrade", why: m[2].trim() }] : [];
});

const shadowOf = (rule: string, v: unknown): ShadowReview | null => {
  const o = obj(v), status = str(o?.status, 30);
  if (!o || !status) return null;
  const session = str(o.session, 10);
  return { rule, session: session && DATE.test(session) ? session : null, status, veto: syms(o.would_veto), downgrade: syms(o.would_downgrade),
    allowed: syms(o.allowed), reasons: reasonsOf(o.reasons), costUsd: num(o.cost_usd), error: str(o.error, 120) };
};

/** One review per rule, the newest session first: the heartbeat's block (today) or the latest event. */
export function shadowReviews(md: MultiDay | null | undefined, events: Ev[] | undefined): ShadowReview[] {
  const by = new Map<string, ShadowReview>();
  const put = (r: ShadowReview | null) => {
    if (!r) return;
    const had = by.get(r.rule);
    if (!had || (r.session ?? "") > (had.session ?? "")) by.set(r.rule, r);
  };
  for (const [rule, v] of Object.entries(obj(md?.raw?.rules) ?? {}).slice(0, 40)) put(shadowOf(rule, obj(v)?.review_shadow));
  for (const e of (events ?? []).filter((x) => x.type === "multi_day_review_shadow")) {
    const rule = str(e.payload?.strategy);
    if (rule) put(shadowOf(rule, e.payload));
  }
  return [...by.values()].sort((a, b) => (b.session ?? "").localeCompare(a.session ?? "") || a.rule.localeCompare(b.rule));
}

/** What the observer would have done, in a few words. */
export function shadowLine(r: ShadowReview): string {
  if (r.status === "not_yet") return "Not run yet today";
  if (r.status === "skipped_budget") return "Skipped: the AI budget said no";
  if (r.status === "skipped_no_key") return "Skipped: no AI key on the engine";
  if (r.status !== "done") return `Did not finish${r.error ? ` (${r.error})` : ""}`;
  const parts = [...(r.veto.length ? [`Would veto ${r.veto.join(", ")}`] : []), ...(r.downgrade.length ? [`would halve ${r.downgrade.join(", ")}`] : [])];
  const line = parts.join(" · ");
  return line ? line[0].toUpperCase() + line.slice(1) : "Would allow every buy";
}
