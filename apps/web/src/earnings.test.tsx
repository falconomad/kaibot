// #797: "Upcoming earnings" on Home (lib/earnings.ts, components/UpcomingEarnings.tsx, migration 0054). Display only.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EarningsCard } from "./components/UpcomingEarnings";
import linesSrc from "./components/Lines.tsx?raw";
import { EMPTY_TEXT, SOURCE_TEXT, STALE_MS, dateText, estimateText, parseUpcoming, staleText, untilText } from "./lib/earnings";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const NOW = Date.parse("2026-11-27T12:00:00Z");
const RAW = {
  today: "2026-11-27", source: "alphavantage", fetched_at: "2026-11-27T01:00:05Z",
  items: [
    { symbol: "MSFT", report_date: "2026-12-02", days_until: 5, report_time: "post-market", estimate: "3.1", currency: "USD" },
    { symbol: "AAPL", report_date: "2026-11-28", days_until: 1, report_time: null, estimate: null, currency: "USD" },
    { symbol: "<b>x</b>", report_date: "2026-11-29", days_until: 2 },          // not a symbol: dropped
    { symbol: "NVDA", report_date: "soon", days_until: 3 },                     // not a date: dropped
    { symbol: "SAP", report_date: "2026-12-10", days_until: 13, report_time: "pre-market", estimate: 1.5, currency: "EUR" },
  ],
};

describe("parsing kb_upcoming_earnings", () => {
  it("keeps valid rows only, soonest first, numbers from strings, unknown time as null", () => {
    const d = parseUpcoming(RAW)!;
    expect(d.items.map((r) => r.symbol)).toEqual(["AAPL", "MSFT", "SAP"]);
    expect(d.items[1]).toEqual({ symbol: "MSFT", reportDate: "2026-12-02", daysUntil: 5, time: "post-market", estimate: 3.1, currency: "USD" });
    expect(d.items[0].time).toBeNull();
    expect(d.fetchedAt).toBe("2026-11-27T01:00:05.000Z");
    expect(parseUpcoming(null)).toBeNull();
    expect(parseUpcoming({ items: "x" })!.items).toEqual([]);
  });
  it("words", () => {
    expect([untilText(0), untilText(1), untilText(5)]).toEqual(["today", "tomorrow", "in 5 days"]);
    expect(dateText("2026-12-02")).toBe("Wed 2 Dec");
    const [aapl, msft, sap] = parseUpcoming(RAW)!.items;
    expect([estimateText(aapl), estimateText(msft), estimateText(sap)]).toEqual([null, "Est. EPS $3.10", "Est. EPS 1.50 EUR"]);
  });
  it("a 'last updated' note only when the data is older than two days", () => {
    expect(staleText("2026-11-27T01:00:00Z", NOW)).toBeNull();
    expect(staleText(new Date(NOW - STALE_MS + 1000).toISOString(), NOW)).toBeNull();
    expect(staleText("2026-11-24T01:00:00Z", NOW)).toBe("Last updated 24 Nov");
    expect(staleText(null, NOW)).toBeNull();
  });
});

describe("the card", () => {
  it("lists symbol, date, days until, the time only when known, the estimate, and the source", () => {
    const html = renderToStaticMarkup(<EarningsCard data={parseUpcoming(RAW)!} now={NOW} />);
    expect(html).toContain("Upcoming earnings");
    expect(html).toContain("AAPL");
    expect(html).toContain("tomorrow</span>");                // AAPL: no time, so nothing after "tomorrow"
    expect(html).toContain("in 5 days · after market");
    expect(html).toContain("in 13 days · before market");
    expect(html).toContain("Est. EPS $3.10");
    expect(html).toContain(SOURCE_TEXT);
    expect(html).not.toContain("Last updated");
    expect(html).not.toContain("<b>x</b>");
  });
  it("says the empty state, and the muted note when the data is old", () => {
    const html = renderToStaticMarkup(<EarningsCard data={{ today: "2026-11-27", fetchedAt: "2026-11-20T01:00:00Z", items: [] }} now={NOW} />);
    expect(html).toContain(EMPTY_TEXT);
    expect(EMPTY_TEXT).toBe("No earnings reports for your holdings in the next 30 days.");
    expect(html).toContain("Last updated 20 Nov");
  });
  it("is in the lazy Lines chunk, for the owner only, behind its own error boundary", () => {
    expect(linesSrc).toContain('viewer === "owner" && headlines && <ErrorBoundary name="Upcoming earnings"><UpcomingEarnings />');
  });
});
