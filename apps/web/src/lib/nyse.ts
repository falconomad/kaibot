// The NYSE calendar the page knows by itself (audit, 25 Nov 2026): the full-day holidays and the 13:00 ET early closes of
// 2026 and 2027, as NYSE publishes them (checked against Alpaca's /v2/calendar on 25 Nov 2026). The market notices'
// `sessions` (kb_market_notices) and the heartbeat's `next_open` are used where a page has them; this table is what is
// left when it does not, and the only source of the early closes (neither of those says them).
// Past 2027 the table knows nothing and every weekday counts as a session: extend it with the next year's dates
// (nyse.test.ts fails from 1 Dec 2027 to say so).
// Pure: unit-tested in nyse.test.ts.

/** The last day the table covers. */
export const COVERED_UNTIL = "2027-12-31";

/** NYSE full-day holidays (weekdays only; a holiday on a weekend is observed on the day listed). */
export const HOLIDAYS: ReadonlySet<string> = new Set([
  "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
  "2026-11-26", "2026-12-25",
  "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
  "2027-11-25", "2027-12-24",
]);

/** Early closes: the close in minutes after midnight ET (13:00). */
export const EARLY_CLOSES: Readonly<Record<string, number>> = { "2026-11-27": 780, "2026-12-24": 780, "2027-11-26": 780 };

export const OPEN_MIN = 9 * 60 + 30;
export const CLOSE_MIN = 16 * 60;

const noon = (d: string) => new Date(`${d}T12:00:00Z`);
export const addDays = (d: string, n: number) => new Date(noon(d).getTime() + n * 86_400_000).toISOString().slice(0, 10);

/** A weekday that is not an NYSE holiday. */
export function isTradingDay(d: string): boolean {
  const w = noon(d).getUTCDay();
  return w !== 0 && w !== 6 && !HOLIDAYS.has(d);
}

/** The day's close in minutes ET: 13:00 on an early close, else 16:00. */
export const closeMin = (d: string) => EARLY_CLOSES[d] ?? CLOSE_MIN;
/** The engine's send window (engine/live_daily.py send_from / send_until): the close minus 10 minutes to the close minus 3. */
export const sendFromMin = (d: string) => closeMin(d) - 10;
export const sendUntilMin = (d: string) => closeMin(d) - 3;

/** 770 -> "12:50" */
export const hm = (min: number) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/** The first trading day on or after `d`. */
export function tradingDayFrom(d: string): string {
  let x = d;
  for (let i = 0; i < 15 && !isTradingDay(x); i++) x = addDays(x, 1);
  return x;
}
