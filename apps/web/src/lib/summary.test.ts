// Stock summaries (display-only AI; kb_stock_summary, migration 0023): the parser never trusts the server's shape,
// and the credit line names who wrote the card, from which sources, on which day.
import { describe, expect, it } from "vitest";
import { actionsLine, creditLine, earningsLine, newsNote, parseSummary } from "./summary";

const row = {
  symbol: "AAPL", day: "2026-11-18", text: "Apple Inc is a technology company.", ai: true, model: "claude-haiku-5-5",
  sources: [{ name: "Finnhub profile", as_of: "2026-11-18" }, { name: "Alpaca news", as_of: "2026-11-17" }],
  body: {
    about: "Apple Inc is a US technology company listed on NASDAQ.", about_ai: true,
    news: [{ headline: "Apple raises dividend to $0.27 a share", gist: "Apple raised its dividend 4% to $0.27 a share.", date: "2026-11-17", source: "benzinga" },
      { headline: "Apple to open three stores in India", gist: null, date: "2026-11-15", source: "benzinga" }],
    news_status: "ok", earnings: { date: "2027-01-29", hour: "amc", as_of: "2026-11-18" }, earnings_status: "ok",
    actions: [{ action: "split", on: "2026-12-01" }], actions_status: "ok", profile_status: "ok",
  },
};

describe("parseSummary", () => {
  it("reads a card", () => {
    const s = parseSummary(row)!;
    expect(s.symbol).toBe("AAPL");
    expect(s.ai).toBe(true);
    expect(s.news.map((n) => n.text)).toEqual(["Apple raised its dividend 4% to $0.27 a share.", "Apple to open three stores in India"]);
    expect(s.earnings).toEqual({ date: "2027-01-29", hour: "amc" });
  });
  it("is null for nothing, and for a shape it does not know", () => {
    expect(parseSummary(null)).toBeNull();
    expect(parseSummary("x")).toBeNull();
    expect(parseSummary({ symbol: "AAPL" })).toBeNull();
    expect(parseSummary({ ...row, day: "yesterday" })).toBeNull();
  });
  it("drops malformed parts instead of failing", () => {
    const s = parseSummary({ ...row, body: { about: "Apple.", news: [{ headline: 3 }, "x", { headline: "Ok", date: "2026-11-18" }], earnings: "soon", actions: [{}] }, sources: "x" })!;
    expect(s.news.map((n) => n.text)).toEqual(["Ok"]);
    expect(s.earnings).toBeNull();
    expect(s.actions).toEqual([]);
    expect(s.sources).toEqual([]);
    expect(s.newsStatus).toBe("ok");
  });
  it("caps lengths so a bad row cannot flood the sheet", () => {
    const s = parseSummary({ ...row, body: { ...row.body, about: "a".repeat(5000), news: Array(9).fill(row.body.news[0]) } })!;
    expect(s.about.length).toBeLessThanOrEqual(400);
    expect(s.news.length).toBe(3);
  });
});

describe("lines", () => {
  const s = parseSummary(row)!;
  it("credits the model, the sources and the day", () => {
    expect(creditLine(s)).toBe("Written by AI from Finnhub profile and Alpaca news, Nov 18, 2026.");
    expect(creditLine({ ...s, ai: false })).toBe("From Finnhub profile and Alpaca news, Nov 18, 2026. Not reworded by AI.");
    expect(creditLine({ ...s, sources: [] })).toBe("Written by AI from the facts read on Nov 18, 2026.");
  });
  it("says earnings, actions and no news plainly", () => {
    expect(earningsLine(s)).toBe("Next earnings: Jan 29, 2027, after the close");
    expect(earningsLine({ ...s, earnings: null })).toBe("No earnings date announced yet");
    expect(earningsLine({ ...s, earnings: null, earningsStatus: "unread" })).toBe("Earnings date: could not be read");
    expect(actionsLine(s)).toBe("Announced: split on Dec 1, 2026");
    expect(actionsLine({ ...s, actions: [] })).toBe("No corporate action announced");
    expect(newsNote({ ...s, news: [], newsStatus: "none" })).toBe("No news about AAPL in the last 7 days.");
    expect(newsNote({ ...s, news: [], newsStatus: "unread" })).toBe("News could not be read that day.");
    expect(newsNote(s)).toBeNull();
  });
});
