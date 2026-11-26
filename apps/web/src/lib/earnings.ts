// "Upcoming earnings" on Home (kb_upcoming_earnings, migration 0054): the next earnings reports of the stocks the paper
// run holds, within 30 days. DISPLAY ONLY: the engine never reads this calendar (decision 20: a pre-earnings filter
// needs its pre-registered test to pass gate v3.1 and the stakeholder's approval first).
//
// Source: Alpha Vantage's EARNINGS_CALENDAR, fetched once a day by scripts/fetch_av_earnings.py. Its free key is for
// personal, non-commercial use, so the database answers the owner only, and the card says where the data comes from.
// Untrusted values: every field is checked here and rendered as text only.
import { supa } from "./api";

export interface UpcomingReport {
  symbol: string; reportDate: string; daysUntil: number;
  /** "before market" / "after market" when the calendar says; null otherwise (the card omits it) */
  time: "pre-market" | "post-market" | null;
  estimate: number | null; currency: string | null;
}
export interface Upcoming { today: string; fetchedAt: string | null; items: UpcomingReport[] }

export const WINDOW_DAYS = 30;
/** Data older than this gets a muted "last updated" note. */
export const STALE_MS = 2 * 24 * 3_600_000;
export const EMPTY_TEXT = `No earnings reports for your holdings in the next ${WINDOW_DAYS} days.`;
export const SOURCE_TEXT = "Data: Alpha Vantage";
export const TIME_LABEL = { "pre-market": "before market", "post-market": "after market" } as const;

const SYMBOL = /^[A-Z][A-Z0-9.-]{0,9}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;

const finite = (v: unknown): number | null => {
  const n = v === null || v === undefined || v === "" ? NaN : Number(v);
  return Number.isFinite(n) ? n : null;
};

export function parseReport(x: unknown): UpcomingReport | null {
  if (!x || typeof x !== "object") return null;
  const r = x as Record<string, unknown>;
  const symbol = typeof r.symbol === "string" && SYMBOL.test(r.symbol) ? r.symbol : null;
  const reportDate = typeof r.report_date === "string" && DAY.test(r.report_date) ? r.report_date : null;
  const days = finite(r.days_until);
  if (!symbol || !reportDate || days === null || days < 0 || !Number.isInteger(days)) return null;
  const time = r.report_time === "pre-market" || r.report_time === "post-market" ? r.report_time : null;
  const currency = typeof r.currency === "string" && /^[A-Z]{3}$/.test(r.currency) ? r.currency : null;
  return { symbol, reportDate, daysUntil: days, time, estimate: finite(r.estimate), currency };
}

/** kb_upcoming_earnings' answer -> the card's data, soonest first; null when it cannot be read at all. */
export function parseUpcoming(data: unknown): Upcoming | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  const items = Array.isArray(d.items) ? d.items.map(parseReport).filter((r): r is UpcomingReport => r !== null) : [];
  items.sort((a, b) => a.daysUntil - b.daysUntil || a.symbol.localeCompare(b.symbol));
  const t = typeof d.fetched_at === "string" ? Date.parse(d.fetched_at) : NaN;
  return { today: typeof d.today === "string" ? d.today : "", fetchedAt: Number.isFinite(t) ? new Date(t).toISOString() : null, items };
}

/** "today", "tomorrow", "in 5 days". */
export const untilText = (n: number) => (n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`);

/** "Thu 28 Jan" (the report date is a calendar day: no time zone shift). */
export const dateText = (iso: string) =>
  new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));

/** "Est. EPS $2.35" (other currencies by code); null without an estimate. */
export function estimateText(r: UpcomingReport): string | null {
  if (r.estimate === null) return null;
  const v = r.estimate.toFixed(2);
  return `Est. EPS ${!r.currency || r.currency === "USD" ? `$${v}` : `${v} ${r.currency}`}`;
}

/** The muted "last updated" note when the calendar is older than STALE_MS (or has never been fetched). */
export function staleText(fetchedAt: string | null, now: number): string | null {
  if (!fetchedAt) return null;
  const age = now - Date.parse(fetchedAt);
  if (age <= STALE_MS) return null;
  return `Last updated ${new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(fetchedAt))}`;
}

/** null before migration 0054 (the card shows nothing); any other error is thrown. */
export async function fetchUpcoming(): Promise<Upcoming | null> {
  const { data, error } = await supa.rpc("kb_upcoming_earnings", { p_days: WINDOW_DAYS });
  if (error) {
    if (error.code === "PGRST202" || error.code === "42883" || /could not find the function|does not exist/i.test(error.message ?? "")) return null;
    throw Object.assign(new Error(error.message), { code: error.code });
  }
  return parseUpcoming(data);
}
