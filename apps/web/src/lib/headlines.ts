// The headline line on Home (kb_headlines, migration 0043): the stock headlines the engine reads anyway, one or two
// lines each, held and decided stocks first. They inform only: no order, no setting, never a warning colour.
//
// Licence (Product, 23 Nov 2026). Two sources, each row carries its licence:
//   alpaca_benzinga -> owner_only  Alpaca/Benzinga via our Trading API key: the single owner may see them, nobody else
//   sec_edgar       -> public      SEC 8-K filings, public records
// The database already returns owner_only rows only to the owner (kaibot.headline_list); `visibleTo` is the same rule
// again in the page, so a non-owner view (multi-user, January) can never show an owner_only row even if a future
// read returned one.
//
// Untrusted text: a headline is rendered as a React text node only (never HTML). A link is kept only when it is a
// plain https URL; anything else is dropped.
import { supa } from "./api";

export type Licence = "owner_only" | "public";
export type Viewer = "owner" | "other";
export type Tag = "analyst_up" | "analyst_down" | "management_change" | "listing";
export interface Headline {
  id: number; symbols: string[]; headline: string; source: "alpaca_benzinga" | "sec_edgar"; licence: Licence;
  url: string | null; tags: Tag[]; focus: boolean; created_at: string;
}

export const TAGS: Record<Tag, string> = {
  analyst_up: "Analyst up", analyst_down: "Analyst down", management_change: "Management", listing: "Listing",
};
export const SOURCE_LABEL: Record<Headline["source"], string> = { alpaca_benzinga: "Benzinga", sec_edgar: "SEC filing" };
export const MAX_CHARS = 140;
/** How long one headline stays in the cycling card. */
export const DWELL_MS = 6_000;
/** The static list's length (reduced motion). */
export const STATIC_ROWS = 3;

const SYMBOL = /^[A-Z][A-Z0-9.-]{0,9}$/;
const LICENCE: Record<Headline["source"], Licence> = { alpaca_benzinga: "owner_only", sec_edgar: "public" };

/** An https URL without spaces, quotes or angle brackets, or null. `javascript:`, `http:`, `data:` ... are dropped. */
export function httpsOnly(v: unknown): string | null {
  if (typeof v !== "string" || v.length > 500 || !/^https:\/\/[^\s"'<>]+$/.test(v)) return null;
  try {
    const u = new URL(v);
    return u.protocol === "https:" && !!u.hostname ? u.href : null;
  } catch {
    return null;
  }
}

/** Untrusted text -> one line of at most MAX_CHARS characters (control characters out). */
export function plain(v: unknown): string {
  const s = typeof v === "string" ? v.replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]/g, " ").replace(/\s+/g, " ").trim() : "";
  return s.length > MAX_CHARS ? `${s.slice(0, MAX_CHARS - 1).trimEnd()}…` : s;
}

/** One row of kb_headlines, or null when it cannot be read. The licence is the source's, whatever the row says. */
export function parseHeadline(x: unknown): Headline | null {
  if (!x || typeof x !== "object") return null;
  const r = x as Record<string, unknown>;
  const source = r.source === "sec_edgar" || r.source === "alpaca_benzinga" ? r.source : null;
  const headline = plain(r.headline);
  const symbols = Array.isArray(r.symbols) ? r.symbols.filter((s): s is string => typeof s === "string" && SYMBOL.test(s)).slice(0, 10) : [];
  const t = typeof r.created_at === "string" ? Date.parse(r.created_at) : NaN;
  if (!source || !headline || !symbols.length || !Number.isFinite(t)) return null;
  // fail closed: a row whose stated licence disagrees with its source is treated as owner_only
  const licence: Licence = r.licence === LICENCE[source] ? LICENCE[source] : "owner_only";
  const tags = Array.isArray(r.tags) ? r.tags.filter((g): g is Tag => typeof g === "string" && g in TAGS) : [];
  return { id: typeof r.id === "number" ? r.id : 0, symbols, headline, source, licence, url: httpsOnly(r.url), tags,
    focus: r.focus === true, created_at: new Date(t).toISOString() };
}

/** THE licence guard of the page: owner_only rows only for the owner. */
export const visibleTo = (items: Headline[], viewer: Viewer): Headline[] =>
  viewer === "owner" ? items : items.filter((h) => h.licence === "public");

/** Held and decided stocks first, then newest first. */
export const ordered = (items: Headline[]): Headline[] =>
  [...items].sort((a, b) => Number(b.focus) - Number(a.focus) || b.created_at.localeCompare(a.created_at) || b.id - a.id);

export function parseHeadlines(data: unknown, viewer: Viewer): Headline[] {
  const raw = data && typeof data === "object" ? (data as { items?: unknown }).items : null;
  const items = Array.isArray(raw) ? raw.map(parseHeadline).filter((h): h is Headline => h !== null) : [];
  return ordered(visibleTo(items, viewer));
}

/** kb_headlines; null while migration 0043 is not applied (the function does not exist yet). */
export async function fetchHeadlines(viewer: Viewer): Promise<Headline[] | null> {
  const { data, error } = await supa.rpc("kb_headlines", { p_limit: 20 });
  if (error) {
    if (error.code === "PGRST202" || error.code === "42883" || /could not find the function|does not exist/i.test(error.message ?? "")) return null;
    throw Object.assign(new Error(error.message), { code: error.code });
  }
  return parseHeadlines(data, viewer);
}

/** "now", "12 min", "3 h", "2 d". */
export function age(iso: string, now = Date.now()): string {
  const m = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
  if (m < 1) return "now";
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h` : `${Math.round(h / 24)} d`;
}

/** The next index of the cycle. */
export const nextIndex = (i: number, n: number) => (n > 0 ? (i + 1) % n : 0);
