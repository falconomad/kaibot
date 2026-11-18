// The engine's night (stakeholder, 18 Nov 2026): the Mac that runs the engine shuts down at 22:20 CET on purpose and
// starts on weekdays at 13:45 CET, before the 09:30 ET open. An engine that is off outside market hours is asleep, a
// calm and neutral state; off during market hours (09:30-16:00 ET on a trading day) it is the amber warning.
// Pure: unit-tested in nightfix.test.tsx.
import type { Heartbeat } from "./api";

/** The Mac's schedule (Berlin time, weekdays). */
export const ENGINE_WAKE = { tz: "Europe/Berlin", h: 13, m: 45 } as const;
export const ENGINE_SLEEP = { tz: "Europe/Berlin", h: 22, m: 20 } as const;

const parts = (d: Date, tz: string) => {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", year: "numeric", month: "2-digit",
    day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(d).map((x) => [x.type, x.value]));
  return { wd: p.weekday as string, date: `${p.year}-${p.month}-${p.day}`, min: Number(p.hour) * 60 + Number(p.minute) };
};
const WEEKEND = ["Sat", "Sun"];

/** 09:30-16:00 ET on a trading day. Holidays and early closes: when the engine's last heartbeat said the market was
 *  closed and its next open is still ahead, the market is closed now. An engine that died while trading said nothing
 *  of the sort, so a weekday's 09:30-16:00 counts as market hours. */
export function marketHours(now: Date, beat?: Pick<Heartbeat, "ts" | "detail"> | null): boolean {
  const et = parts(now, "America/New_York");
  if (WEEKEND.includes(et.wd) || et.min < 9 * 60 + 30 || et.min >= 16 * 60) return false;
  const next = typeof beat?.detail?.next_open === "string" ? Date.parse(beat.detail.next_open) : NaN;
  if (beat?.detail?.state === "market_closed" && Number.isFinite(next) && next > now.getTime()) return false;
  return true;
}

/** The next time the Mac starts (13:45 Berlin on a weekday) after `now`. */
export function nextWake(now: Date): Date {
  for (let i = 0; i < 8; i++) {
    // a guess near 13:45 Berlin on day i, corrected for the offset that day (CET or CEST)
    const probe = new Date(now.getTime() + i * 86_400_000);
    const b = parts(probe, ENGINE_WAKE.tz);
    const guess = new Date(Date.parse(`${b.date}T13:45:00Z`));
    const off = parts(guess, ENGINE_WAKE.tz).min - (13 * 60 + 45); // Berlin minus UTC, in minutes
    const at = new Date(guess.getTime() - off * 60_000);
    if (at > now && !WEEKEND.includes(parts(at, ENGINE_WAKE.tz).wd)) return at;
  }
  return new Date(now.getTime() + 86_400_000);
}

/** "13:45" in the viewer's time zone when it is within a day, else with its weekday ("Mon 13:45"). */
export function wakeText(now: Date, at: Date, tz?: string): string {
  const zone = tz ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const hm = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(at);
  return at.getTime() - now.getTime() < 86_400_000 ? hm : `${parts(at, zone).wd} ${hm}`;
}

export interface EngineClock { kind: "asleep" | "off" | "offline"; text: string; wake: string | null }

/** The state of an engine that is not live: offline during market hours (amber); outside them asleep (the Mac's night)
 *  or off (its waking hours), both neutral. */
export function engineClock(now: Date, beat?: Pick<Heartbeat, "ts" | "detail"> | null, tz?: string): EngineClock {
  if (marketHours(now, beat)) return { kind: "offline", text: "Engine offline · no new trades until it is back", wake: null };
  const b = parts(now, ENGINE_WAKE.tz);
  // the Mac's waking hours, outside market hours (before the open, or between the close and 22:20): off, still calm
  if (!WEEKEND.includes(b.wd) && b.min >= ENGINE_WAKE.h * 60 + ENGINE_WAKE.m && b.min < ENGINE_SLEEP.h * 60 + ENGINE_SLEEP.m)
    return { kind: "off", text: "Engine off · market closed", wake: null };
  const wake = wakeText(now, nextWake(now), tz);
  return { kind: "asleep", text: `Engine asleep · starts ${wake}`, wake };
}
