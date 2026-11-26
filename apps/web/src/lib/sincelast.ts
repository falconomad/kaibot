// N10 (docs/design/ui_after_engine_v3.md, #724): "Since you last looked", up to five lines of what kaibo did by itself
// since the owner's last visit: alerts (halts, cleared halts, adopted orders, refused answers and the other warnings),
// buys and sells, and decisions (buys decided, setups passed on, unfilled buys carried). Computed from kaibo's own
// records (kb_health's events, kb_tape's items), never generated. Each line opens its Activity row, or the Engine log
// for an event Activity does not list. Pure: unit-tested in sincelast.test.ts. Loaded with the card's chunk only.
//
// The last visit lives in localStorage (`kb.lastLook.<run>`), read and written inside try/catch: private browsing,
// a full store or a blocked one only means no card. A visit lasts VISIT_MS: opening Home again within it keeps the same
// "since", so a reload or a tab change does not wipe the digest the owner is reading.
import type { Halt, TapeItem } from "./api";
import { haltText } from "./derive";
import { EVENT_LABELS, EVENT_SEVERITY } from "./eventNames";
import { NAME } from "../brand/brand";

export const KEY = "kb.lastLook";
export const VISIT_MS = 15 * 60_000;
export const MAX_LINES = 5;

export interface Look { at: number; prev: number | null }
type Store = Pick<Storage, "getItem" | "setItem">;

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The stored look -> the time to digest from (null: no earlier visit) and what to store now. */
export function nextLook(stored: unknown, now: number): { since: number | null; store: Look } {
  const s = stored && typeof stored === "object" ? stored as Record<string, unknown> : null;
  const at = s && finite(s.at) && s.at <= now ? s.at : null;
  if (at === null) return { since: null, store: { at: now, prev: null } };
  const prev = finite(s!.prev) && s!.prev <= at ? s!.prev : null;
  if (now - at < VISIT_MS) return { since: prev, store: { at, prev } }; // the same visit: keep its "since"
  return { since: at, store: { at: now, prev: at } };
}

/** Read this run's last look and record this one. Never throws: no storage means no earlier visit. */
export function takeLook(run: number, now = Date.now(), store: Store | null = safeStorage()): number | null {
  let stored: unknown = null;
  try { stored = JSON.parse(store?.getItem(`${KEY}.${run}`) ?? "null"); } catch { stored = null; }
  const n = nextLook(stored, now);
  try { store?.setItem(`${KEY}.${run}`, JSON.stringify(n.store)); } catch { /* full or blocked: the next visit has no card */ }
  return n.since;
}

function safeStorage(): Store | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}

export type LineTone = "red" | "warn" | "calm";
export interface SinceLine { key: string; text: string; to: string; tone: LineTone; ts: string }
type Ev = { type: string; ts: string; run_id?: number | null; payload?: Record<string, unknown> | null };

// Events that are the owner's to know even though they are routine or good news.
const ALSO = new Set(["halt_cleared", "order_adopted", "multi_day_exit_recovered", "watchdog_recovered"]);
// Events that repeat while one cause lasts, and the ones another line already says (a fill, the daily note, carries).
const SKIP = new Set(["engine_error", "shadow_error", "multi_day_carry", "multi_day_fill", "daily_note", "replica_backlog"]);
const t = (iso: string) => Date.parse(iso);
const list = (syms: string[]) => {
  const u = [...new Set(syms)];
  return u.length <= 3 ? u.join(", ") : `${u.slice(0, 3).join(", ")} and ${u.length - 3} more`;
};
const n = (k: number, one: string, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;
const q = (show: string, ts: string) => `/activity?show=${show}&at=${encodeURIComponent(ts)}`;

/** The digest since `since` (ms): at most MAX_LINES lines, reds first, then warnings, buys and sells, decisions, each
 *  newest first; `more` counts the lines left out. Only rows of this run (or of no run). */
export function digest(since: number, events: Ev[] | undefined, tape: TapeItem[] | undefined, run: number): { lines: SinceLine[]; more: number } {
  const mine = (e: { run_id?: number | null }) => e.run_id === null || e.run_id === undefined || e.run_id === run;
  const ev = (events ?? []).filter((e) => mine(e) && t(e.ts) > since);
  const items = (tape ?? []).filter((i) => t(i.ts) > since);
  const out: SinceLine[] = [];

  // alerts: one line per kind (one per cause), with how many times
  const byType = new Map<string, Ev[]>();
  for (const e of ev) {
    const sev = EVENT_SEVERITY[e.type];
    if (SKIP.has(e.type) || !(ALSO.has(e.type) || sev === "critical" || sev === "warn")) continue;
    if (e.type === "clock_skew" && e.payload?.state === "ok") continue;
    byType.set(e.type, [...(byType.get(e.type) ?? []), e]);
  }
  for (const [type, es] of byType) {
    const last = es.reduce((a, b) => (t(b.ts) > t(a.ts) ? b : a));
    const times = es.length > 1 ? ` (${es.length} times)` : "";
    if (type === "halt") {
      const h: Halt = { kind: String(last.payload?.kind ?? "halt"), reason: String(last.payload?.reason ?? ""), ts: last.ts };
      out.push({ key: "halt", text: `Halt: ${haltText(h)}${times}`, to: q("alerts", last.ts), tone: "red", ts: last.ts });
    } else {
      const label = EVENT_LABELS[type] ?? type.replace(/_/g, " ");
      out.push({ key: type, text: `${label}${times}`, to: "/health", tone: EVENT_SEVERITY[type] === "critical" ? "red" : ALSO.has(type) ? "calm" : "warn", ts: last.ts });
    }
  }

  // buys and sells, one line each side
  const fills = items.filter((i): i is Extract<TapeItem, { kind: "fill" }> => i.kind === "fill");
  for (const side of ["sell", "buy"] as const) {
    const f = fills.filter((x) => x.side === side);
    if (!f.length) continue;
    const newest = f.reduce((a, b) => (t(b.ts) > t(a.ts) ? b : a));
    out.push({ key: `fill-${side}`, text: `${side === "sell" ? "Sold" : "Bought"} ${list(f.map((x) => x.symbol))}${f.length > 1 ? ` (${n(f.length, "fill")})` : ""}`,
      to: q("fills", newest.ts), tone: "calm", ts: newest.ts });
  }

  // decisions: live buys decided, setups passed on, unfilled buys carried
  const live = items.filter((i): i is Extract<TapeItem, { kind: "signal" }> => i.kind === "signal" && i.mode === "live");
  const took = live.filter((s) => s.approved === true), passed = live.filter((s) => s.approved !== true);
  if (took.length) {
    const newest = took.reduce((a, b) => (t(b.ts) > t(a.ts) ? b : a));
    out.push({ key: "took", text: `Decided to buy ${list(took.map((s) => s.symbol))}`, to: q("all", newest.ts), tone: "calm", ts: newest.ts });
  }
  if (passed.length) {
    const newest = passed.reduce((a, b) => (t(b.ts) > t(a.ts) ? b : a));
    out.push({ key: "passed", text: `Passed on ${n(passed.length, "setup")} (${list(passed.map((s) => s.symbol))})`, to: q("skipped", newest.ts), tone: "calm", ts: newest.ts });
  }
  const carries = ev.filter((e) => e.type === "multi_day_carry");
  if (carries.length) {
    const last = carries.reduce((a, b) => (t(b.ts) > t(a.ts) ? b : a));
    const c = last.payload?.carried, k = Array.isArray(c) ? c.length : 0;
    out.push({ key: "carry", text: k ? `${NAME} carried ${n(k, "unfilled buy")} to the next session` : `${NAME} checked its unfilled buys for the next session`,
      to: "/health", tone: "calm", ts: last.ts });
  }

  const rank: Record<LineTone, number> = { red: 0, warn: 1, calm: 2 };
  const order = (l: SinceLine) => (l.tone === "calm" ? (l.key.startsWith("fill") ? 2 : l.key === "took" || l.key === "passed" || l.key === "carry" ? 3 : 1.5) : rank[l.tone]);
  out.sort((a, b) => order(a) - order(b) || t(b.ts) - t(a.ts));
  return { lines: out.slice(0, MAX_LINES), more: Math.max(0, out.length - MAX_LINES) };
}
