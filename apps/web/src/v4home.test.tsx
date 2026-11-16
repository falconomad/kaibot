// v4 PR 3 (docs/design/mobile_first_v4.md, "Home"): status rows, the pot, top holdings, Details.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { HomeStatus, TopHoldings, holdingRows } from "./components/Home";
import { Live } from "./pages/Live";
import { Shell } from "./components/Shell";
import { parseBook } from "./lib/book";
import type { Overview } from "./lib/api";
import { RAW_BOOK } from "./pages/testkit-book";
import { ctxFor, emptyOverview, paperRun, render, replayRun, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun();
const fresh = () => new Date().toISOString();
const ov = (o: Partial<Overview> = {}): Overview => ({ ...emptyOverview(run), heartbeat: [{ process: "engine", ts: fresh(), detail: { state: "trading" } }], ...o });
const inRouter = (el: ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);

describe("Home status rows", () => {
  it("a halt is a red row announced as an alert, with how many more", () => {
    const html = inRouter(<HomeStatus run={run} overview={ov()} engineLive book={undefined}
      risk={{ severity: "breach", items: ["Engine halted entries for today", "3 losses in a row: 60-minute pause"] }} />);
    expect(html).toMatch(/<div role="alert"><button type="button" class="status-row breach"/);
    expect(text(html)).toContain("Engine halted entries for today · 1 more");
  });
  it("the engine offline is an amber row; a missing safety stop of the book is a red row", () => {
    const b = parseBook({ ...RAW_BOOK }, 1);
    const html = inRouter(<HomeStatus run={run} overview={ov()} engineLive={false} book={b} risk={{ severity: "ok", items: [] }} />);
    expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">Engine offline: nothing new trades/);
    expect(html).toMatch(/class="status-row breach"[^>]*>.*1 stock has no safety stop/);
  });
  it("a replay says so; nothing else when all is well", () => {
    expect(text(inRouter(<HomeStatus run={replayRun()} overview={ov()} engineLive book={undefined} risk={undefined} />)))
      .toBe("Replay on past data, not live trading");
  });
});

describe("Home holdings and page", () => {
  it("intraday positions and the multi-day book, biggest first, with the stop-to-target bar where there is one", () => {
    const rows = holdingRows({ NVDA: { qty: 10, fill: 200, stop: 190, target: 220, status: "open", last: 207, trade_id: 7 } }, parseBook(RAW_BOOK, 1));
    expect(rows.map((r) => r.symbol)).toEqual(["NVDA", "AAPL", "COST", "MSFT", "ZZZQ"]);
    expect(rows.map((r) => r.value)).toEqual([...rows.map((r) => r.value)].sort((a, b) => b - a));
    const n = rows.find((r) => r.symbol === "NVDA")!;
    expect(n).toMatchObject({ value: 2070, gain: 70, to: "/positions/7", bar: { stop: 190, now: 207, target: 220 } });
    expect(rows.find((r) => r.symbol === "AAPL")!.to).toBe("/holdings");
    const html = inRouter(<TopHoldings rows={rows} max={null} />);
    expect(text(html)).toMatch(/^Holding 5 NVDA/);
    expect(html.match(/class="hold-row"/g)).toHaveLength(3);
    expect(html).toContain('role="img" aria-label="Price $207.00 between stop $190.00 and target $220.00"');
  });
  it("Home: the pot, all-time and today, the chart, Details; no latest-trades table and no shell strip on Home", () => {
    const html = render(<Live />, { ctx: { ...ctxFor(run, { overview: ov() }), meters: { dayPct: -0.4, dd: 1, replay: false } },
      seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
    const t = text(html);
    expect(t).toContain("Paper pot $10,000.00");
    expect(t).toContain("$0.00 all time"); // a new run starts flat
    expect(t).toContain("Details vs S&P 500, limits, evidence, rules");
    expect(html).toContain('class="desk-only meters home-meters"');
    expect(t).not.toContain("Latest trades");
    const shell = render(<Shell />, { seeds: [{ key: ["runs"], data: [run] },
      { key: ["overview", 1], data: ov({ halts_today: [{ kind: "drift", reason: "x", ts: fresh() }] }) }] });
    expect(shell).not.toContain('class="riskstrip');
  });
});
