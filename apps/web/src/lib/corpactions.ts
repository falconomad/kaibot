// Announced corporate actions (#266, PR #271: the multi-day rule's buy guard). Two sources, both optional, so the
// dashboard shows nothing new until the engine sends them:
// - the heartbeat's multi_day.corporate_actions: string[]. A held stock: "AAPL: split on 2026-11-20 — the engine does
//   not handle this yet, check before then". A failed read: "trend_momentum: no new buys this session: <why>".
// - the event `multi_day_ca_skip` {strategy, session, symbol, action, on, text: "skipped: corporate action on <date>"}:
//   a buy left out. It reaches the dashboard once kb_health lists that event type (a Backend-quant migration).
import { day } from "./fmt";
import { NAME } from "../brand/brand";

export const CA_REASON = `${NAME} doesn't buy a stock with a split, spin-off, merger or ticker change coming up.`;

export interface HeldAction { symbol: string; action: string; on: string; line: string }
export interface CaSkip { ts: string; symbol: string; action: string; on: string; strategy: string | null }

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const words = (s: unknown, max = 60) => (typeof s === "string" && s.trim() ? s.trim().slice(0, max) : null);
/** "2026-11-20" -> "Nov 20, 2026"; anything else as it came, cut short. */
export const onDay = (on: string) => (DATE.test(on) ? day(on) : on.slice(0, 30));

/** The heartbeat's notes, split into held stocks with an action coming and rules that could not read the list. */
export function caNotes(multiDay: unknown): { held: HeldAction[]; unread: string[] } {
  const raw = (multiDay && typeof multiDay === "object" ? (multiDay as Record<string, unknown>).corporate_actions : null);
  const lines = Array.isArray(raw) ? raw.filter((l): l is string => typeof l === "string").slice(0, 30) : [];
  const held: HeldAction[] = [], unread: string[] = [];
  for (const line of lines) {
    const m = /^([A-Z][A-Z0-9.]{0,9}): (.+?) on (\d{4}-\d{2}-\d{2})\b/.exec(line);
    if (m) held.push({ symbol: m[1], action: m[2], on: m[3], line });
    else if (/no new buys this session/i.test(line)) unread.push(line.replace(/^[a-z0-9_~]+:\s*/i, ""));
  }
  return { held, unread };
}

/** The skip events among kb_health's events, newest first; nothing when the engine or the RPC doesn't send them. */
export function caSkips(events: { type: string; ts: string; payload: Record<string, unknown> }[] | undefined): CaSkip[] {
  return (events ?? []).filter((e) => e.type === "multi_day_ca_skip").flatMap((e) => {
    const p = e.payload ?? {}, symbol = words(p.symbol, 10), action = words(p.action), on = words(p.on, 30);
    return symbol && action && on ? [{ ts: e.ts, symbol, action, on, strategy: words(p.strategy, 40) }] : [];
  });
}
