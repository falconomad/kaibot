// Stock summaries (display-only AI; api/summary.ts, migration 0023). The card is written on the first view of a stock
// on a day from facts Kaibot holds or reads on free tiers: the Finnhub profile, the news, the earnings calendar and
// Alpaca's corporate actions. The model only rewords the profile and the headlines; the dates,
// earnings and actions here are the facts themselves. Server text is untrusted: parsed defensively, rendered as text.
import { day } from "./fmt";

export type Status = "ok" | "none" | "unread";
export interface SummaryNews { text: string; date: string; source: string }
export interface Summary {
  symbol: string; day: string; ai: boolean; about: string;
  news: SummaryNews[]; newsStatus: Status;
  earnings: { date: string; hour: string } | null; earningsStatus: Status;
  actions: { action: string; on: string }[]; actionsStatus: Status;
  sources: { name: string; asOf: string }[];
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const str = (v: unknown, max: number) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const status = (v: unknown, fallback: Status = "ok"): Status => (v === "ok" || v === "none" || v === "unread" ? v : fallback);

/** kb_stock_summary's answer -> a Summary, or null (no card yet, or a shape this page does not know). */
export function parseSummary(raw: unknown): Summary | null {
  const r = obj(raw);
  if (!r) return null;
  const symbol = str(r.symbol, 12), d = str(r.day, 10), b = obj(r.body);
  if (!symbol || !ISO.test(d) || !b) return null;
  const news = arr(b.news).map(obj).flatMap((n) => {
    const text = str(n?.gist, 300) || str(n?.headline, 300), date = str(n?.date, 10);
    return n && text && ISO.test(date) ? [{ text, date, source: str(n.source, 40) }] : [];
  }).slice(0, 3);
  const e = obj(b.earnings);
  const earnings = e && ISO.test(str(e.date, 10)) ? { date: str(e.date, 10), hour: str(e.hour, 3) } : null;
  const actions = arr(b.actions).map(obj).flatMap((a) => (a && str(a.action, 40) ? [{ action: str(a.action, 40), on: ISO.test(str(a.on, 10)) ? str(a.on, 10) : "" }] : [])).slice(0, 3);
  const sources = arr(r.sources).map(obj).flatMap((s) => (s && str(s.name, 40) ? [{ name: str(s.name, 40), asOf: str(s.as_of, 10) }] : [])).slice(0, 6);
  return {
    symbol, day: d, ai: r.ai === true, about: str(b.about, 400),
    news, newsStatus: status(b.news_status, news.length ? "ok" : "none"),
    earnings, earningsStatus: status(b.earnings_status, earnings ? "ok" : "none"),
    actions, actionsStatus: status(b.actions_status, "ok"), sources,
  };
}

const HOURS: Record<string, string> = { bmo: "before the open", amc: "after the close", dmh: "during market hours" };
const words = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

export function earningsLine(s: Summary): string {
  if (s.earningsStatus === "unread") return "Earnings date: could not be read";
  if (!s.earnings) return "No earnings date announced yet";
  const h = HOURS[s.earnings.hour];
  return `Next earnings: ${day(s.earnings.date)}${h ? `, ${h}` : ""}`;
}

export function actionsLine(s: Summary): string {
  if (s.actionsStatus === "unread") return "Corporate actions: could not be read";
  if (!s.actions.length) return "No corporate action announced";
  return `Announced: ${s.actions.map((a) => `${a.action} on ${a.on ? day(a.on) : "a date not given yet"}`).join("; ")}`;
}

/** What to say instead of headlines, or null when there are headlines. */
export function newsNote(s: Summary): string | null {
  if (s.news.length) return null;
  return s.newsStatus === "unread" ? "News could not be read that day." : `No news about ${s.symbol} in the last 7 days.`;
}

/** The small credit line: who wrote it, from which sources, for which day. */
export function creditLine(s: Summary): string {
  const from = s.sources.length ? words(s.sources.map((x) => x.name)) : "";
  if (!s.ai) return `From ${from || "the facts read"}, ${day(s.day)}. Not reworded by AI.`;
  return from ? `Written by AI from ${from}, ${day(s.day)}.` : `Written by AI from the facts read on ${day(s.day)}.`;
}
