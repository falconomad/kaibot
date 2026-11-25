// "Next action" on Home (#604, ui_after_engine_v3 N5): one plain sentence that says what kaibo does next and when,
// and the late daily prices notice (#645: `multi_day_data_late`). Pure: unit-tested in nextaction.test.ts.
//
// Read only from what the page already has; every field is untrusted jsonb:
// - the engine heartbeat's `detail`: `state`, `next_open`, and `multi_day` {book, rules.<rule>: {retired, waiting,
//   note, last_decision, standing: {state, decided, fill_session, sent_on, late_of, short, replaced_by}}, data_late:
//   [{session, deadline, rules, why}]} (engine/live_daily.py `status`, engine/standing.py `summary`);
// - when the heartbeat carries them (#446, being added): `entry_block` (why buys are blocked: a text, a list of texts
//   or of {code, text}) and `trading_state` ("active" | "reducing_only" | "halted", or {state, why}). Absent: the
//   line falls back to what the other fields say;
// - kb_health's events when a page already fetched them: the latest `multi_day_carry` (risk_off: the market filter
//   holds the carried buys) and `multi_day_data_late` {session, strategy, deadline, why};
// - the market calendar: kb_market_notices' `sessions` (today when it trades, then the next ones; no early closes)
//   when cached, else the heartbeat's `next_open`, else weekdays.
// Orders go out in the 15:50-15:57 ET window of their session (engine/live_daily.py SEND_FROM / SEND_UNTIL). On an
// early-close day the window is earlier; the page does not know those days and says 15:50 ET.
import { NAME } from "../brand/brand";
import { needsStop } from "./exits";

type Ev = { type: string; ts: string; payload: Record<string, unknown> | null; run_id?: number | null };
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);
const str = (v: unknown, max = 120) => (typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null);
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const date = (v: unknown) => { const s = str(v, 10); return s && DATE.test(s) ? s : null; };

const ET = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
/** The New York date and minute of the day at `t`. */
export function etAt(t: number): { day: string; min: number } {
  const p = Object.fromEntries(ET.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
}
const noon = (d: string) => new Date(`${d}T12:00:00Z`);
const wd = (d: string) => noon(d).getUTCDay(); // 0 Sunday .. 6 Saturday
const addDays = (d: string, n: number) => new Date(noon(d).getTime() + n * 86_400_000).toISOString().slice(0, 10);
/** "Wed 25 Nov" */
export const dayText = (d: string) => new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(noon(d));

const SEND_UNTIL = 15 * 60 + 57, OPEN = 9 * 60 + 30;

/** The trading sessions from `today` on, as well as the page knows them: the calendar's list when it reaches past
 *  today, else weekdays from the heartbeat's next open (a holiday it names is skipped), else weekdays. */
export function sessionsFrom(today: string, calendar?: string[] | null, nextOpen?: unknown): string[] {
  const cal = (calendar ?? []).filter((d) => DATE.test(d) && d >= today).sort();
  const known = cal.some((d) => d > today);
  const t = typeof nextOpen === "string" ? Date.parse(nextOpen) : NaN;
  // past the calendar's last day (it lists six sessions) or without it: weekdays
  const first = known ? addDays(cal.at(-1)!, 1) : Number.isFinite(t) && t > Date.parse(`${today}T00:00:00Z`) ? etAt(t).day : today;
  const out = known ? cal : [];
  for (let i = 0; out.length < 30 && i < 60; i++) {
    const d = addDays(first, i);
    if (wd(d) !== 0 && wd(d) !== 6) out.push(d);
  }
  return out;
}

// ---------------------------------------------------------------- late daily prices (#645)

export interface Late { session: string; rules: string[] }

/** The daily prices that missed their deadline (03:30 ET the night after their session). The heartbeat's
 *  `multi_day.data_late` while the engine is live; otherwise (or as well) a `multi_day_data_late` event of a session
 *  that the rule has not decided on since (its `last_decision` is older). Newest session first, one per session. */
export function dataLate(md: unknown, events?: Ev[] | null): Late[] {
  const m = obj(md);
  const by = new Map<string, Late>();
  for (const x of Array.isArray(m?.data_late) ? (m!.data_late as unknown[]).slice(0, 10) : []) {
    const s = date(obj(x)?.session);
    if (!s) continue;
    const rules = Array.isArray(obj(x)?.rules) ? (obj(x)!.rules as unknown[]).flatMap((r) => (str(r, 80) ? [str(r, 80)!] : [])) : [];
    by.set(s, { session: s, rules });
  }
  const rules = obj(m?.rules) ?? {};
  for (const e of events ?? []) {
    if (e.type !== "multi_day_data_late") continue;
    const s = date(e.payload?.session), rule = str(e.payload?.strategy, 80);
    if (!s || by.has(s)) continue;
    const last = rule ? date(obj(rules[rule])?.last_decision) : null;
    if (last && last >= s) continue; // the rule decided on that session's prices after all
    by.set(s, { session: s, rules: rule ? [rule] : [] });
  }
  return [...by.values()].sort((a, b) => b.session.localeCompare(a.session));
}

/** "The prices for Tue 24 Nov haven't arrived; kaibo is waiting" */
export const lateText = (l: Late) => `The prices for ${dayText(l.session)} haven't arrived; ${NAME} is waiting`;

// ---------------------------------------------------------------- the next action

/** Why buys are blocked, from the heartbeat's `entry_block` (#446) when it says; null when it does not. */
export function entryBlock(detail: unknown): string | null {
  const b = obj(detail)?.entry_block;
  const first = Array.isArray(b) ? b[0] : b;
  return str(typeof first === "string" ? first : obj(first)?.text ?? obj(first)?.code, 80);
}

/** The engine's own trading state (#446) when the heartbeat carries it: {state, why}; null otherwise. */
export function engineTrading(detail: unknown): { state: "active" | "reducing_only" | "halted"; why: string | null } | null {
  const t = obj(detail)?.trading_state;
  const state = typeof t === "string" ? t : str(obj(t)?.state, 20);
  if (state !== "active" && state !== "reducing_only" && state !== "halted") return null;
  return { state, why: str(obj(t)?.why ?? obj(t)?.reason, 80) };
}

export interface NextInput {
  run: { kind: string; status: string };
  /** the engine heartbeat's `detail` */
  detail: unknown;
  /** the engine chip (Shell.engineChip) */
  engine: { live: boolean; asleep: boolean; journal?: boolean };
  events?: Ev[] | null;
  /** kb_market_notices' sessions, when cached */
  calendar?: string[] | null;
  /** how many stocks the book holds */
  held?: number;
  now?: number;
}

const FILTER = /market filter (is )?off/i;

/** The sentence, or null when there is nothing to say (no multi-day book, a replay, an ended run, or a state a status
 *  row above already explains: a damaged journal, the book's end, every rule retired). */
export function nextAction(x: NextInput): string | null {
  const { run, detail, engine } = x;
  if (run.kind !== "paper" || !["scheduled", "running", "paused"].includes(run.status) || engine.journal) return null;
  if (!engine.live && !engine.asleep) return "Next: nothing new until the engine runs again";
  const md = obj(obj(detail)?.multi_day);
  if (!md || md.book === "ended") return null;
  const rules = Object.entries(obj(md.rules) ?? {}).map(([name, v]) => [name, obj(v) ?? {}] as const).filter(([, r]) => r.retired !== true);
  if (!rules.length) return null;

  const now = x.now ?? Date.now(), et = etAt(now), today = et.day;
  const sessions = sessionsFrom(today, x.calendar, obj(detail)?.next_open);
  const isSession = (d: string) => sessions.includes(d);
  // the next session whose send window is still ahead: today's until 15:57 ET, else the next one
  const sendDay = sessions.find((d) => d > today || (d === today && et.min < SEND_UNTIL)) ?? null;
  const when = (d: string) => (d === today ? "today" : `on ${dayText(d)}`);
  const at = (d: string) => `${when(d)} at 15:50 ET`;
  const marketOpen = isSession(today) && et.min >= OPEN && et.min < 16 * 60;
  const openDay = sessions.find((d) => d > today || (d === today && et.min < OPEN)) ?? null;
  // only a rule whose stocks rest a stop at the broker: the momentum rules sell by their list at a close and have none
  // by design (lib/exits.ts; the heartbeat's rules.<rule>.exit decides)
  const stops = rules.some(([name, r]) => needsStop(name, str(r.exit, 40)));
  const stopsLine = () => (x.held && openDay && stops ? `Next: stops checked at the open, ${dayText(openDay)} 09:30 ET` : null);

  const ts = engineTrading(detail);
  if (ts?.state === "halted") return `Next: nothing new, trading is halted${ts.why ? ` (${ts.why})` : ""}`;
  if (run.status === "paused") return "Next: no new buys while the run is paused; stops stay at the broker";
  const late = dataLate(md, engine.live ? null : x.events);
  if (late.length) return `Next: decision once the prices for ${dayText(late[0].session)} arrive`;
  const block = entryBlock(detail) ?? (ts?.state === "reducing_only" ? ts.why ?? "buys blocked" : null);

  for (const [name, r] of rules) {
    const st = obj(r.standing);
    const state = str(st?.state, 20);
    if (str(r.waiting, 200) || state === "undecided") return "Next: decision once the daily prices are in";
    if (!st || !state) continue;
    const fill = date(st.fill_session);
    if (state === "due" && fill) {
      const day = fill > today || (fill === today && et.min < SEND_UNTIL) ? fill : sendDay;
      if (!day) continue;
      const head = `Next: orders go out ${fill === day ? "" : "late, "}${at(day)}`;
      return block ? `${head}; buys on hold, ${block}` : head;
    }
    const short = Array.isArray(st.short) ? st.short.length : 0;
    if (state === "sent" && short > 0) {
      const carry = [...(x.events ?? [])].filter((e) => e.type === "multi_day_carry" && str(e.payload?.strategy, 80) === name
        && (!fill || (date(e.payload?.session) ?? "") > fill)).sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))[0];
      const hold = block ?? (carry?.payload?.risk_off === true || [r.note, r.waiting].some((s) => typeof s === "string" && FILTER.test(s))
        ? "market filter off" : null);
      if (hold) return `Next: carried buys on hold, ${hold}`;
      if (sendDay) return `Next: ${short} short buy${short === 1 ? "" : "s"} sent again ${at(sendDay)}`;
      continue;
    }
    if ((state === "sent" || state === "met") && fill) {
      if (!marketOpen) { const s = stopsLine(); if (s) return s; }
      const nextFill = nextDecisionFill(name, fill, sessions);
      if (nextFill) return `Next: next decision, orders ${at(nextFill)}`;
    }
  }
  return marketOpen ? null : stopsLine();
}

/** The fill session of a rule's next decision after the one that filled on `fill`: trend_momentum decides weekly and
 *  fills on the first session of a week on Wednesday or later (strategies/momentum.py is_weekly_decision);
 *  mom_12_1_band on the first session of a month. Null for another rule, or past the sessions the page knows. */
export function nextDecisionFill(rule: string, fill: string, sessions: string[]): string | null {
  const base = rule.split("~", 1)[0];
  if (base === "trend_momentum") {
    const nextMonday = addDays(fill, ((8 - wd(fill)) % 7) || 7);
    return sessions.find((d) => d >= nextMonday && wd(d) >= 3) ?? null;
  }
  if (base === "mom_12_1_band") return sessions.find((d) => d.slice(0, 7) > fill.slice(0, 7)) ?? null;
  return null;
}
