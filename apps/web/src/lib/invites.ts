// kb_invite_list's answer, made safe to read (pure: no Supabase client, so the page tests import it as it is).
import type { InviteList, InviteRow } from "./api";

/** A count the answer may leave out: null unless it is a finite number. */
const count = (v: unknown) => { const n = v === null || v === undefined || v === "" ? NaN : Number(v); return Number.isFinite(n) ? n : null; };

/** kb_invite_list's answer as the invite card reads it: `invites` always a list and the counts numbers (or null when
 * not sent). 0045/0046 always send all three, but a body of another shape must leave the card empty, not throw and
 * blank the whole Settings page (it did with the design audit's mock, which had no kb_invite_list). null stays null:
 * it means the migration is not applied. */
export function fixInviteList(r: unknown): InviteList | null {
  if (r === null || r === undefined) return null;
  const o = (typeof r === "object" ? r : {}) as Partial<Record<keyof InviteList, unknown>>;
  const rows = Array.isArray(o.invites) ? (o.invites as InviteRow[]).filter((i) => i && typeof i === "object") : [];
  return { cap: count(o.cap), taken: count(o.taken), invites: rows };
}
