// One trading-state line (docs/design/ui_after_engine_v3.md, N1 and N2): Buying / Holding / Selling only / Stopped /
// Not running, with the reason in plain words. Pure: unit-tested in tradingstate.test.ts.
//
// Only data the dashboard already gets: the engine's heartbeat row from kb_overview (`state`, `lease`, `halted`,
// `account_clean`, `reconcile`, `multi_day`), today's halt rows, the unapplied commands, the run's status and, when a
// page has them, kb_health's events (`engine_error` with "JournalCorrupt: ...", `engine_start`, `halt_cleared`,
// `clock_skew`). The heartbeat does not carry the engine's own `entry_block()` reason (ticket E1 / #446): where a
// reason cannot be read from these, the line says "reason not reported" instead of guessing.
//
// Rules, first match wins:
//  1. journal damaged: the latest `engine_error` says JournalCorrupt and no `engine_start` came after it (and no
//     fresh live heartbeat after it) -> Stopped, red, "needs you at the Mac", in or out of market hours
//  2. the run ended -> Stopped (by you / the run's end reason)
//  3. no engine heartbeat, a stale one, or `state: stopped` -> Not running: in market hours amber with "reason not
//     reported", outside them calm ("asleep" / "off")
//  4. lease `degraded` -> Selling only, database unreachable at start; lease `unverified` / `lost` / missing on a
//     live engine -> Selling only, lease not verified
//  5. a halt today that is not cleared (or `halted`) -> Selling only, the halt in words (a `three_stops` halt on a
//     paused run is rule 9's own reason, not this one)
//  6. `account_clean: false` -> Selling only, account holds positions or orders kaibo didn't place
//  7. `reconcile.ok: false` -> Selling only, books and broker disagree
//  8. the latest `clock_skew` event is not `ok` -> Selling only, clocks disagree
//  9. run paused -> Selling only: paused by you, or by kaibo after three daily stops (a `three_stops` halt today)
// 10. a pending "Flatten and stop" / new run -> Selling only, selling the book
// Rules 5-8 (red, and the amber clock skew) come before the calm reasons 9-10: a pause or a pending flatten must
// never hide a halt or a broker problem behind a calm line (#607 QA; false reassurance, ui_after_engine_v3 section 4).
// 11. `state: trading` -> Buying
// 12. `state: managing` -> Holding: "market filter off" when a rule's note says so, else "reason not reported";
//     `market_closed` -> Holding, market closed; `starting` / `preparing` -> Holding, starting up; `error` -> Holding,
//     amber, an error the engine is retrying; `idle` -> Holding, no run trading; anything else -> Holding, state not
//     reported
import type { Halt, Overview, Run } from "./api";
import { engineClock } from "./engineclock";
import { CLOSE_MIN, HOLIDAYS, OPEN_MIN, closeMin } from "./nyse";
import { haltText } from "./derive";
import { NAME } from "../brand/brand";
import { type HealthEvent, JOURNAL_REASON, journalDamaged } from "./journalstop";

export { type HealthEvent, JOURNAL_REASON, journalDamaged };

export type TradingKind = "buying" | "holding" | "selling_only" | "stopped" | "not_running";
export type TradingTone = "ok" | "calm" | "warn" | "red";

export interface TradingState {
  kind: TradingKind;
  /** "Buying", "Holding", "Selling only", "Stopped", "Not running" */
  label: string;
  /** the reason in plain words; "reason not reported" when the data does not say */
  reason: string;
  tone: TradingTone;
  /** the owner has to act (red states, an engine down in market hours) */
  needsYou: boolean;
  /** rule 1: the stop that needs the owner at the Mac */
  journal?: boolean;
  /** rule 5: the halt in force, so a page can open its reason with a tap (components/HaltChip, #724) */
  halt?: Halt;
}

export const LABEL: Record<TradingKind, string> = {
  buying: "Buying", holding: "Holding", selling_only: "Selling only", stopped: "Stopped", not_running: "Not running",
};
export const UNKNOWN = "reason not reported";

const FRESH_S = 90; // the engine chip's limit (Shell.engineChip, startstate.ENGINE_FRESH_S)
const t = (iso: unknown) => (typeof iso === "string" ? Date.parse(iso) : NaN);
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const make = (kind: TradingKind, reason: string, tone: TradingTone, needsYou = false): TradingState =>
  ({ kind, label: LABEL[kind], reason, tone, needsYou });

export function tradingState(run: Run, o: Pick<Overview, "heartbeat" | "pending_commands" | "halts_today"> | undefined,
  events?: HealthEvent[], now = Date.now()): TradingState {
  const beat = o?.heartbeat.find((h) => h.process === "engine") ?? null;
  const d = (beat?.detail ?? {}) as Record<string, unknown>;
  if (journalDamaged(events, beat)) return { ...make("stopped", JOURNAL_REASON, "red", true), journal: true };

  if (!["scheduled", "running", "paused"].includes(run.status)) {
    const why = run.end_reason?.trim();
    return make("stopped", !why ? "the run has ended" : /by user/i.test(why) ? "stopped by you" : why.slice(0, 80), "calm");
  }

  const age = (now - t(beat?.ts)) / 1000;
  if (!beat || !Number.isFinite(age) || age > FRESH_S || d.state === "stopped") {
    const c = engineClock(new Date(now), beat);
    if (c.kind === "offline") return make("not_running", beat ? `${UNKNOWN} · market open` : "never connected · market open", "warn", true);
    return make("not_running", c.kind === "asleep" ? `asleep · starts ${c.wake}` : "engine off · market closed", "calm");
  }

  const lease = d.lease;
  if (lease === "degraded") return make("selling_only", "database unreachable at start", "warn", true);
  if (lease === "unverified" || lease === "lost" || lease === "none") return make("selling_only", "engine lease not verified", "warn", true);

  const halts = (o?.halts_today ?? []).filter((h: Halt) => h.run_id == null || h.run_id === run.id);
  const paused = run.status === "paused";
  // Red first (rules 5-8), then the calm reasons: a calm line must never hide a red one.
  // a `halt_cleared` of the halt's kind after it ends it (lib/healthwords.clearedAt; not imported: it pulls in
  // lib/explain, which is not in the first load); a watchdog halt ends with a newer heartbeat (derive.haltResolved).
  // A `three_stops` halt is what paused the run: on a paused run rule 9 says it ("paused after three daily stops").
  const cleared = (h: Halt) => (events ?? []).some((e) => e.type === "halt_cleared" && obj(e.payload)?.kind === h.kind && t(e.ts) > t(h.ts));
  const open = halts.filter((h) => !cleared(h) && !(h.kind === "watchdog" && t(beat.ts) > t(h.ts)) && !(paused && h.kind === "three_stops"));
  if (open.length) return { ...make("selling_only", `halt: ${haltText(open[0]).slice(0, 80)}`, "red", true), halt: open[0] };
  if (d.halted === true && !(paused && halts.some((h) => h.kind === "three_stops")))
    return make("selling_only", "halt: daily stop reached", "red", true);
  if (d.account_clean === false) return make("selling_only", `account holds positions or orders ${NAME} didn't place`, "red", true);
  if (obj(d.reconcile)?.ok === false) return make("selling_only", "books and broker disagree", "red", true);
  const skew = [...(events ?? [])].filter((e) => e.type === "clock_skew").sort((a, b) => t(b.ts) - t(a.ts))[0];
  if (skew && obj(skew.payload)?.state !== "ok") return make("selling_only", "the engine's clock and the broker's disagree", "warn", true);

  if (paused)
    return halts.some((h) => h.kind === "three_stops") ? make("selling_only", "paused after three daily stops · resume when ready", "warn", true)
      : make("selling_only", "paused by you", "calm");
  if (run.status === "scheduled") return make("holding", "the run starts at the next session", "calm");

  const cmd = o?.pending_commands.find((c) => c.kind === "flatten_and_stop" || c.kind === "new_run");
  if (cmd) return make("selling_only", cmd.kind === "new_run" ? "selling the old run for the new one" : "selling everything, as you asked", "calm");

  const state = d.state;
  if (lease === undefined && (state === "trading" || state === "managing"))
    return make("holding", `lease not reported · ${UNKNOWN}`, "warn");
  if (state === "trading") return make("buying", "nothing blocks new buys", "ok");
  if (state === "managing") return make("holding", marketFilterOff(d.multi_day) ? "market filter off" : UNKNOWN, "calm");
  if (state === "market_closed") return make("holding", "market closed", "calm");
  if (state === "starting" || state === "preparing") return make("holding", "starting up", "calm");
  if (state === "error") return make("holding", "an engine error, retrying", "warn");
  if (state === "idle") return make("holding", "no run trading", "calm");
  return make("holding", "state not reported", "warn");
}

/** A rule's note in the heartbeat's `multi_day` block says the market filter is off (the carry-over writes it). */
function marketFilterOff(md: unknown): boolean {
  const rules = obj(obj(md)?.rules);
  return !!rules && Object.values(rules).some((r) => {
    const x = obj(r);
    return [x?.note, x?.waiting].some((s) => typeof s === "string" && /market filter (is )?off/i.test(s));
  });
}

/** "Selling only — paused by you" */
export const tradingLine = (s: TradingState) => `${s.label} — ${s.reason}`;

/** The market's session, for the phone top bar's compact line (stakeholder, 27 Nov 2026: "Holding · Closed", never
 *  "Holding — market clo…"). NYSE hours in New York: pre-market 04:00-09:30, open 09:30-16:00, after hours to 20:00;
 *  on an early close (lib/nyse.ts) the session ends at 13:00 and after hours at 17:00. A full-day holiday is "Holiday",
 *  a weekend or the night "Closed". A heartbeat that says the market is closed with its next open still ahead wins over
 *  the table (a closure the table does not know). */
export type MarketSession = "pre_market" | "open" | "early_close" | "after_hours" | "closed" | "holiday";
export const MARKET_SHORT: Record<MarketSession, string> = {
  pre_market: "Pre-market", open: "Open", early_close: "Early close", after_hours: "After hours", closed: "Closed", holiday: "Holiday",
};
/** The smallest phones (380 px and under, app.css): the session in one short word. */
export const MARKET_TINY: Record<MarketSession, string> = {
  pre_market: "Pre", open: "Open", early_close: "Early", after_hours: "After", closed: "Closed", holiday: "Holiday",
};
/** Phones 420 px and under (app.css): the state in at most two short words, so the line fits beside the logo. */
export const LABEL_TINY: Record<TradingKind, string> = {
  buying: "Buying", holding: "Holding", selling_only: "Sell only", stopped: "Stopped", not_running: "Off",
};
const PRE_MIN = 4 * 60;
const AFTER_MIN = 4 * 60; // after hours run four hours past the close (20:00, or 17:00 on an early close)

export function marketSession(now: number, beat?: { detail?: Record<string, unknown> | null } | null): MarketSession {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", year: "numeric",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(now)).map((x) => [x.type, x.value]));
  const date = `${p.year}-${p.month}-${p.day}`;
  const min = Number(p.hour) * 60 + Number(p.minute);
  if (p.weekday === "Sat" || p.weekday === "Sun") return "closed";
  if (HOLIDAYS.has(date)) return "holiday";
  const close = closeMin(date);
  const next = typeof beat?.detail?.next_open === "string" ? Date.parse(beat.detail.next_open) : NaN;
  const toldClosed = beat?.detail?.state === "market_closed" && Number.isFinite(next) && next > now;
  if (min >= OPEN_MIN && min < close) return toldClosed ? "closed" : close < CLOSE_MIN ? "early_close" : "open";
  if (min >= PRE_MIN && min < OPEN_MIN) return "pre_market";
  if (min >= close && min < close + AFTER_MIN) return "after_hours";
  return "closed";
}

/** The compact line for phones: the state and the market's session ("Holding · Closed"); `tiny` for 420 px and under
 *  (the state's short word, "Sell only · After hours") and `tinier` for 380 px and under ("Sell only · After"). The full
 *  line stays the chip's accessible name and title. */
export function shortLine(s: TradingState, m: MarketSession, size: "phone" | "tiny" | "tinier" = "phone"): string {
  const label = size === "phone" ? s.label : LABEL_TINY[s.kind];
  return `${label} · ${(size === "tinier" ? MARKET_TINY : MARKET_SHORT)[m]}`;
}
