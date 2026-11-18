// Stock summaries on the stock sheets, the trade page and a My lists chip: every state renders, the credit line is
// there, server text stays text, and nothing loops but the skeleton's breathe.
import { describe, expect, it, vi } from "vitest";
import css from "../app.css?raw";
import { StockSummary } from "./StockSummary";
import { render, text } from "../pages/testkit";
import { parseSummary } from "../lib/summary";

vi.mock("../lib/api", () => import("../pages/testkit-api"));

const card = parseSummary({
  symbol: "AAPL", day: "2026-11-18", ai: true, text: "x",
  sources: [{ name: "Finnhub profile", as_of: "2026-11-18" }, { name: "Alpaca news", as_of: "2026-11-17" }],
  body: { about: "Apple Inc is a US technology company listed on NASDAQ.", news: [{ headline: "<b>Apple</b> raises dividend", gist: null, date: "2026-11-17", source: "benzinga" }],
    news_status: "ok", earnings: { date: "2027-01-29", hour: "amc" }, earnings_status: "ok", actions: [], actions_status: "ok" },
});
const at = (data: unknown, error?: unknown) => render(<StockSummary symbol="AAPL" />, { seeds: [error ? { key: ["stockSummary", "AAPL"], error } : data === undefined ? { key: ["x"] } : { key: ["stockSummary", "AAPL"], data }] });

describe("StockSummary", () => {
  it("shows the card with dates and the credit line", () => {
    const t = text(at(card));
    expect(t).toContain("Apple Inc is a US technology company listed on NASDAQ.");
    expect(t).toContain("<b>Apple</b> raises dividend"); // server text as text, never markup
    expect(t).toContain("benzinga · Nov 17, 2026");
    expect(t).toContain("Next earnings: Jan 29, 2027, after the close");
    expect(t).toContain("No corporate action announced");
    expect(t).toContain("Written by AI from Finnhub profile and Alpaca news, Nov 18, 2026.");
  });
  it("works before there is any summary", () => {
    const t = text(at(null));
    expect(t).toContain("No summary for AAPL yet");
    expect(t).not.toContain("Written by AI");
  });
  it("loads with the skeleton and says so", () => {
    const h = at(undefined);
    expect(h).toContain('aria-busy="true"');
    expect(h).toContain('class="sk"');
  });
  it("an error is a quiet line, not a broken sheet", () => {
    expect(text(at(undefined, new Error("boom")))).toContain("Couldn't load the summary");
  });
  it("says when there was no news", () => {
    const t = text(at({ ...card!, news: [], newsStatus: "none" }));
    expect(t).toContain("No news about AAPL in the last 7 days.");
  });
  it("uses the brand type steps and no looping animation", () => {
    const block = css.slice(css.indexOf("/* stock summaries"), css.indexOf("/* end stock summaries"));
    expect(block.length).toBeGreaterThan(50);
    expect(block).not.toMatch(/animation|infinite/);
    for (const px of block.match(/font-size:\s*[^;]+/g) ?? []) expect(px).toMatch(/var\(--t-(md|sm|cap)\)/);
  });
});
