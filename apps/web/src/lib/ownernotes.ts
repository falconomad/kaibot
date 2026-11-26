// The owner's small notices of #604 (docs/design/ui_after_engine_v3.md) that ship with ticket 8.6 (#724). Pure and in
// the first load: the top bar and Home read them before any lazy chunk arrives (section 4, "the first-load budget":
// N6 and N7 are first-load items). Unit-tested in ownernotes.test.ts.
//
//  N6  "Last seen on <host>": the heartbeat's `detail.host`, so the owner knows which machine to check. The owner's
//      screens only: the host name must never reach another user (section 5).
//  N7  the engine's record is ahead of this screen: the heartbeat's `replication.backlog_alarm` (engine/journal.py
//      Replicator.status: rows waiting ten minutes and not getting fewer). A poll 5 s old can show figures an hour old.
//  N9  "Can't reach kaibo's database": when the page's own RPCs fail at the connection (a network error, a gateway
//      or pooler error, a timeout), it is the database and not the engine, and stops stay at the broker.
//  N12 where prices come from: live ticks are from one exchange; the stop line and P&L use confirmed closing marks.
import { NAME } from "../brand/brand";
import type { Freshness } from "./freshness";

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null);

/** N6: the machine the engine last reported from, as one short printable word; null when it never said. */
export function hostOf(detail: unknown): string | null {
  const h = obj(detail)?.host;
  if (typeof h !== "string") return null;
  const s = h.replace(/[^\x21-\x7e]/g, "").slice(0, 40);
  return s || null;
}
export const lastSeenText = (host: string | null) => (host ? `last seen on ${host}` : "");

/** N7: the dashboard's copy is behind the engine's record (the heartbeat's replication block). `pending`: the rows
 *  waiting, when the engine says. Null while it keeps up, and for an engine that does not report it. */
export function replicationLag(detail: unknown): { pending: number | null } | null {
  const r = obj(obj(detail)?.replication);
  if (!r || r.backlog_alarm !== true) return null;
  return { pending: typeof r.pending === "number" && Number.isFinite(r.pending) && r.pending > 0 ? Math.round(r.pending) : null };
}
export const LAG_TEXT = `${NAME}'s record is ahead of this screen · figures may be behind`;
/** N7 in the one freshness cue (components/Fresh.tsx): amber, said once to a screen reader. */
export function lagFreshness(detail: unknown): Freshness | null {
  return replicationLag(detail) ? { state: "stale", text: LAG_TEXT } : null;
}

/** N9: an RPC error that is the connection to the database, not an answer from it. supabase-js reports a failed fetch
 *  as a message ("TypeError: Failed to fetch", "Load failed", "fetch failed", "NetworkError ..."); a gateway, pooler
 *  or timeout comes as a 502-504 page or PostgREST's PGRST000-PGRST003 (the database cannot be reached), or Postgres
 *  SQLSTATE 08xxx / 57P0x (connection lost, shutting down). A refusal (42501), a missing function or a bad argument is
 *  an answer: false. */
export function dbUnreachable(e: unknown): boolean {
  const x = obj(e) ?? (e instanceof Error ? { message: e.message } : null);
  const code = typeof x?.code === "string" ? x.code : "";
  if (/^PGRST00[0-3]$|^08|^57P0/.test(code)) return true;
  const msg = typeof x?.message === "string" ? x.message : typeof e === "string" ? e : "";
  return /failed to fetch|load failed|fetch failed|networkerror|network request failed|timed? ?out|timeout|\b50[234]\b|bad gateway|service unavailable|gateway time/i.test(msg);
}
export const DB_DOWN = `Can't reach ${NAME}'s database`;
export const DB_DOWN_WHY = "This is the connection to the database, not the engine. The engine runs on its own, and stops stay at the broker.";

/** N12: one line in the position sheet and Home's details. */
export const PRICE_SOURCE = "Live prices are from one exchange (IEX). The stop line and P&L use confirmed closing marks.";
