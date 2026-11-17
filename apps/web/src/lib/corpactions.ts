// Announced corporate actions (#266, PR #271: the multi-day rule's buy guard). Two sources, both optional, so the
// dashboard shows nothing new until the engine sends them:
// - the heartbeat's multi_day.corporate_actions: string[]. A held stock: "AAPL: split on 2026-11-20 — the engine does
//   not handle this yet, check before then". A failed read: "trend_momentum: no new buys this session: <why>".
// - events (kb_health lists them once migration 0021 of PR #274 is applied; before that they simply don't arrive):
//   multi_day_ca_skip {strategy, session, symbol, action, on, text}: a buy left out (#271);
//   multi_day_ca_unread {strategy, session, why, not_bought, text}: the list couldn't be read, no buys that session;
//   spin_off_adopted {parent, symbol, qty, cost_basis, ex_date, ref, action}: shares of a spun-off company taken into
//     the book, sold at the next closing auction (#275);
//   symbol_change_adopted {symbol, new_symbol, qty, ex_date, ref}: a held stock renamed (#275);
//   corporate_action_not_adopted {kind, symbol, new_symbol, why, action}: the ledger couldn't match the broker (#275).
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

// ---------------------------------------------------------------- the events in plain words (Activity and Engine)

export type CaTone = "note" | "warn" | "alert";
export interface CaEvent { ts: string; type: string; symbol: string | null; head: string; line: string; detail: string; tone: CaTone; icon: "skipped" | "bought" | "took" | "halt" }
type Ev = { type: string; ts: string; payload: Record<string, unknown> };
const shares = (q: number | null) => (q === null ? "" : `${Number.isInteger(q) ? q : Number(q.toFixed(4))} share${q === 1 ? "" : "s"} of `);
const qtyOf = (v: unknown) => { const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN; return Number.isFinite(n) ? n : null; };
const KIND: Record<string, string> = { spin_off: "spin-off", symbol_change: "ticker change", forward_split: "split", reverse_split: "reverse split", cash_merger: "merger" };

/** One engine event about a corporate action, in plain words; null for any other event or a payload that doesn't fit. */
export function caEvent(e: Ev): CaEvent | null {
  const p = e.payload ?? {};
  if (e.type === "multi_day_ca_skip") {
    const symbol = words(p.symbol, 10), action = words(p.action), on = words(p.on, 30);
    return symbol && action && on ? { ts: e.ts, type: e.type, symbol, head: `Skipped ${symbol}: ${action} on ${onDay(on)}`, tone: "note", icon: "skipped",
      line: "Multi-day rule", detail: `${CA_REASON} ${symbol} has a ${action} announced for ${onDay(on)}, so this buy was left out.` } : null;
  }
  if (e.type === "multi_day_ca_unread") {
    const why = words(p.why, 200);
    return { ts: e.ts, type: e.type, symbol: null, head: "Couldn't read upcoming splits and spin-offs: no new buys today", tone: "warn", icon: "halt",
      line: "Multi-day rule", detail: `The list of announced corporate actions couldn't be read, so the multi-day rule bought nothing new this session. Sales and stops went on.${why ? ` (${why})` : ""}` };
  }
  if (e.type === "spin_off_adopted") {
    const parent = words(p.parent, 10), symbol = words(p.symbol, 10), q = qtyOf(p.qty);
    return parent && symbol ? { ts: e.ts, type: e.type, symbol, head: `Added ${shares(q)}${symbol} from the ${parent} spin-off: sells at tonight's close`, tone: "note", icon: "bought",
      line: "Multi-day book", detail: `${parent} spun off ${symbol}, and the shares arrived in the paper account. ${NAME} took them into its books and sells them at the next closing auction; it doesn't keep stocks it didn't choose.` } : null;
  }
  if (e.type === "symbol_change_adopted") {
    const from = words(p.symbol, 10), to = words(p.new_symbol, 10);
    return from && to ? { ts: e.ts, type: e.type, symbol: to, head: `Renamed: ${from} is now ${to}`, tone: "note", icon: "took",
      line: "Multi-day book", detail: `${from} trades as ${to} from now on. Same shares, same cost; the books follow the new ticker.` } : null;
  }
  if (e.type === "corporate_action_not_adopted") {
    const symbol = words(p.symbol, 10), kind = KIND[String(p.kind)] ?? words(p.kind, 30)?.replace(/_/g, " ") ?? "corporate action", why = words(p.why, 200);
    return symbol ? { ts: e.ts, type: e.type, symbol, head: `Couldn't take in the ${symbol} ${kind}: no new buys until it's checked`, tone: "alert", icon: "halt",
      line: "Multi-day book", detail: `The books and the paper account disagree after the ${symbol} ${kind}${why ? `: ${why}` : ""}. New buys stay blocked until the paper account is checked.` } : null;
  }
  return null;
}
export const caEvents = (events: Ev[] | undefined): CaEvent[] => (events ?? []).flatMap((e) => { const c = caEvent(e); return c ? [c] : []; });
