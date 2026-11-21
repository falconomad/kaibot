// #378 (M8 S104): the market notices on Home, "New on the market" and "Big day" (docs/design/m8_market_notices.md).
// Read-only: kb_market_notices (migration 0035). They inform only; nothing here leads to an order or a setting.
// Loaded with the Notices chunk only (components/Notices.tsx is lazy), so none of this is in the first load.
import { supa } from "./api";
import { ruleName } from "./explain";
import { NAME } from "../brand/brand";

export type NoticeKind = "new_listing" | "big_day";
export type NoticeFlag = "ipo" | "direct_listing" | "fed_day" | "cpi_day" | "megacap_report" | "index_reshuffle" | "mega_listing";
export interface NoticeRule { rule: string; state: string; first_eligible: string; needs_sp500: boolean }
export interface Notice {
  kind: NoticeKind; flag: NoticeFlag; tier: "major" | "notice"; session: string; symbol: string | null; title: string;
  starts: string | null; first_eligible: string | null; source: string;
  detail: { name?: string; rules?: NoticeRule[]; index?: string[]; megacaps?: string[]; what?: string[] };
  /** A very large listing is also a big day for the market: the two are shown as one row, the listing. */
  alsoBigDay?: boolean;
}
export interface Notices { today: string; from: string; to: string; sessions: string[]; notices: Notice[] }

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const KINDS = new Set(["new_listing", "big_day"]);
const FLAGS = new Set(["ipo", "direct_listing", "fed_day", "cpi_day", "megacap_report", "index_reshuffle", "mega_listing"]);
const strs = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : undefined);

/** The RPC's answer, checked field by field; a malformed notice is left out, never guessed. */
export function parseNotices(raw: unknown): Notices | null {
  const r = raw as Record<string, unknown> | null;
  if (!r || typeof r.today !== "string" || !DAY.test(r.today)) return null;
  const sessions = (strs(r.sessions) ?? []).filter((d) => DAY.test(d));
  const notices: Notice[] = [];
  for (const x of Array.isArray(r.notices) ? r.notices : []) {
    const n = x as Record<string, unknown>;
    if (!n || !KINDS.has(n.kind as string) || !FLAGS.has(n.flag as string) || typeof n.session !== "string" || !DAY.test(n.session)) continue;
    const d = (n.detail && typeof n.detail === "object" ? n.detail : {}) as Record<string, unknown>;
    const rules = Array.isArray(d.rules) ? (d.rules as NoticeRule[]).filter((q) => q && typeof q.rule === "string" && typeof q.first_eligible === "string") : undefined;
    notices.push({
      kind: n.kind as NoticeKind, flag: n.flag as NoticeFlag, tier: n.tier === "major" ? "major" : "notice", session: n.session,
      symbol: typeof n.symbol === "string" && n.symbol ? n.symbol : null, title: typeof n.title === "string" ? n.title : "",
      starts: typeof n.starts === "string" && Number.isFinite(Date.parse(n.starts)) ? n.starts : null,
      first_eligible: typeof n.first_eligible === "string" && DAY.test(n.first_eligible) ? n.first_eligible : null,
      source: typeof n.source === "string" ? n.source : "",
      detail: { name: typeof d.name === "string" ? d.name : undefined, rules, index: strs(d.index), megacaps: strs(d.megacaps), what: strs(d.what) },
    });
  }
  return { today: r.today, from: String(r.from ?? r.today), to: String(r.to ?? r.today), sessions, notices };
}

/** kb_market_notices; null while migration 0035 is not applied (the function does not exist yet). */
export async function fetchNotices(): Promise<Notices | null> {
  const { data, error } = await supa.rpc("kb_market_notices", {});
  if (error) {
    if (error.code === "PGRST202" || error.code === "42883" || /could not find the function|does not exist/i.test(error.message ?? "")) return null;
    throw Object.assign(new Error(error.message), { code: error.code });
  }
  return parseNotices(data);
}

// ---------------------------------------------------------------- which notices, and their words

/** The next session after today (today itself is not counted). */
export const nextSession = (n: Notices) => n.sessions.find((d) => d > n.today) ?? null;

/** One notice per thing: a very large listing's big day is folded into the listing of the same symbol and day. */
export function merged(n: Notices): Notice[] {
  const all = n.notices.map((x) => ({ ...x }));
  const listings = new Map(all.filter((x) => x.kind === "new_listing").map((x) => [`${x.symbol}|${x.session}`, x]));
  const out: Notice[] = [];
  for (const x of all) {
    if (x.kind === "big_day" && x.flag === "mega_listing") {
      const l = listings.get(`${x.symbol}|${x.session}`);
      if (l) { l.alsoBigDay = true; continue; }
    }
    out.push(x);
  }
  return out;
}

/** What Home shows (design, "Which notices are on Home"): big days on their day, and the session before when major;
 *  major listings from the session before through their fifth session (the window starts four sessions back). */
export function homeNotices(n: Notices): Notice[] {
  const next = nextSession(n);
  const rank = (x: Notice) => (x.session === n.today ? 0 : x.session < n.today ? 1 : 2) * 2 + (x.tier === "major" ? 0 : 1);
  return merged(n).filter((x) => {
    if (x.kind === "big_day") return x.session === n.today || (x.tier === "major" && x.session === next);
    return x.tier === "major" && x.session >= n.from && (x.session <= n.today || x.session === next);
  }).sort((a, b) => rank(a) - rank(b) || a.session.localeCompare(b.session));
}

const asDate = (d: string) => new Date(`${d}T12:00:00Z`);
export const dayName = (d: string) => new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" }).format(asDate(d));
export const longDay = (d: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(asDate(d));
/** A time in the viewer's own zone (the browser's, unless a test names one). */
export const clock = (iso: string, tz?: string) =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(new Date(iso));

const INDEX: Record<string, string> = { sp500_quarterly: "S&P 500", russell_recon: "Russell", nasdaq100_annual: "Nasdaq-100" };
const indexNames = (ids?: string[]) => [...new Set((ids ?? []).map((i) => INDEX[i] ?? i.replace(/_/g, " ")))];
const andList = (xs: string[]) => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
const names = (xs: string[]) => (xs.length > 3 ? `${xs.slice(0, 3).join(", ")} and ${xs.length - 3} more` : andList(xs));

export const KIND_LABEL: Record<NoticeKind, string> = { new_listing: "New on the market", big_day: "Big day" };

/** When a notice is, against today: "today", "tomorrow" (or the weekday of the next session), past or later. */
export function when(x: Notice, n: Notices): "today" | "next" | "past" | "later" {
  if (x.session === n.today) return "today";
  if (x.session < n.today) return "past";
  return x.session === nextSession(n) ? "next" : "later";
}
const nextWord = (x: Notice, n: Notices) => {
  const t = asDate(n.today); t.setUTCDate(t.getUTCDate() + 1);
  return t.toISOString().slice(0, 10) === x.session ? "Tomorrow" : dayName(x.session).split(",")[0];
};

/** The short line of a row (phone) or of a list item. `full`: the sheet's title, as a sentence. */
export function noticeLine(x: Notice, n: Notices, opts: { tz?: string; full?: boolean; list?: boolean } = {}): string {
  const w0 = when(x, n), tz = opts.tz, full = !!opts.full;
  const w = opts.list && w0 === "next" ? "later" : w0;  // a dated list says the day itself, not "Tomorrow:"
  const t = x.starts ? clock(x.starts, tz) : null;
  const pre = w === "next" ? `${nextWord(x, n)}: ` : "";
  const sym = x.symbol ?? "";
  if (x.kind === "new_listing") {
    const who = `${x.detail.name || sym} (${sym})`;
    if (w === "today") return `${who} started trading today`;
    if (w === "past") return `${who} started trading on ${dayName(x.session)}`;
    if (w === "next") return `${pre}${who} starts trading`;
    return `${who} is expected to start trading`;
  }
  switch (x.flag) {
    case "fed_day":
      if (full) return `The Fed announces its decision${t ? ` at ${t} your time` : ""}`;
      return pre ? `${pre}Fed decision${t ? `, ${t}` : ""}` : `Fed decision${t ? ` at ${t} your time` : ""}`;
    case "cpi_day":
      if (full) return `The US inflation report comes out${t ? ` at ${t} your time` : ""}`;
      return pre ? `${pre}inflation report${t ? `, ${t}` : ""}` : `Inflation report${t ? ` at ${t} your time` : ""}`;
    case "megacap_report": {
      const who = x.detail.megacaps?.length ? names(x.detail.megacaps) : "Some of the largest companies";
      return `${pre}${who} report${full ? " their" : ""} results`;
    }
    case "index_reshuffle": {
      const ix = indexNames(x.detail.index);
      const at = w === "today" ? "today's close" : "the close";
      return full ? `Index funds rebalance at ${at}${ix.length ? ` (${andList(ix)})` : ""}`
        : `${pre}Index change at the close${ix.length ? ` (${andList(ix)})` : ""}`;
    }
    default: {  // a very large listing without its listing row, or one of its dated follow-ups
      const follow = x.detail.what?.find((s) => s.includes(" "))?.split(" ")[1];
      if (follow === "index_entry") return `${pre}${sym} joins a stock index`;
      if (follow) return `${pre}A follow-up of ${sym}'s listing (${follow.replace(/_/g, " ")})`;
      return `${pre}A very large company starts trading${sym ? ` (${sym})` : ""}`;
    }
  }
}

/** "in about 12 months" / "in about 6 weeks" from the listing's first session to the paper rule's earliest date. */
export function untilText(from: string, to: string): string {
  const days = Math.round((asDate(to).getTime() - asDate(from).getTime()) / 86_400_000);
  if (days <= 0) return "now";
  if (days < 60) return `in about ${Math.max(1, Math.round(days / 7))} week${Math.round(days / 7) === 1 ? "" : "s"}`;
  const m = Math.round(days / 30.44);
  return `in about ${m} months`;
}

const RULE_LABEL: Record<string, string> = { owner_tickers: "Your own ticker list" };
/** The two dates the sheet names: the rule on paper and the owner's own list. */
export function eligibleRows(x: Notice): [string, string][] {
  const rules = x.detail.rules ?? [];
  const out: [string, string][] = [];
  const paper = rules.find((r) => r.rule === "trend_momentum");
  if (paper) out.push([`${ruleName(paper.rule)}, on paper`, `${longDay(paper.first_eligible)}${paper.needs_sp500 ? " at the earliest" : ""}`]);
  const own = rules.find((r) => r.rule === "owner_tickers");
  if (own) out.push([RULE_LABEL.owner_tickers, longDay(own.first_eligible)]);
  return out;
}

/** The sheet's sentences, per kind (design, "The two notices"). */
export const BIG_DAY_WHY = `${NAME} is not changing anything today; it is recording what happens on days like this.`;
export const LISTING_WHY = `${NAME} does not buy brand-new listings: they have no price history to judge, and on average they have done worse than the market in their first years.`;
export const ONLY_INFORMS = `A notice only informs. ${NAME} changes nothing because of it.`;
export const HOW_OFTEN: Partial<Record<NoticeFlag, string>> = {
  fed_day: "Days like this: the Fed's interest-rate decision, about 8 a year. Prices can move more than usual around it.",
  cpi_day: "Days like this: the monthly US inflation report, 12 a year, out before the market opens.",
  megacap_report: "Days like this: some of the ten largest US companies report their results, a few dozen days a year.",
  index_reshuffle: "Days like this: funds that follow an index trade at the close to match its new list, 5 or 6 days a year.",
  mega_listing: "Days like this: a very large company's first days of trading, two or three in the last ten years.",
};
