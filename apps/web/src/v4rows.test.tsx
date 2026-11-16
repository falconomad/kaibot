// v4 PR 4 (docs/design/mobile_first_v4.md, "Positions", "Activity"): rows instead of tables, Holdings folded into
// Positions, test-only rows off by default; and two Home fixes (Product, 16 Nov 2026).
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { Heartbeat, Overview, TapeItem } from "./lib/api";
import { parseBook } from "./lib/book";
import { HomeStatus } from "./components/Home";
import { Positions } from "./pages/Positions";
import { Tape } from "./pages/Tape";
import { Live } from "./pages/Live";
import mainSrc from "./main.tsx?raw";
import { RAW_BOOK, RAW_EMPTY_BOOK } from "./pages/testkit-book";
import { ctxFor, emptyOverview, glitches, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const run = paperRun();
const now = new Date().toISOString();
const EXP = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
  real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
const beat = (detail: Record<string, unknown>): Heartbeat[] => [{ process: "engine", ts: now, detail: { state: "trading", ...detail } as Heartbeat["detail"] }];
const ledger = { ts: now, cash: 5000, equity_venue: 10080, equity_shadow: 10070,
  positions: { NVDA: { qty: 10, fill: 200, stop: 190, target: 220, status: "open", last: 207, trade_id: 7 } } };
const ov = (o: Partial<Overview> = {}): Overview => ({ ...emptyOverview(run), heartbeat: beat({ experiments: [EXP] }), ledger, ...o });
const book = (raw: unknown) => ({ key: ["book", 1], data: parseBook(raw, 1) });

describe("Positions (v4, Holdings folded in)", () => {
  it("open: the day's positions and the multi-day book as rows, biggest first, with invested and share of the pot", () => {
    const html = render(<Positions />, { ctx: ctxFor(run, { overview: ov() }), seeds: [book(RAW_BOOK)] });
    const t = text(html);
    expect(t).toContain("Positions 5 open · $5,121.70 invested · 51% of the pot");
    expect(t).toContain("NVDA 10 shares · in at $200.00 $2,070.00 +$70.00");
    expect(t).toContain("AAPL Trend momentum · 4 shares · 51 days");
    expect(t).toContain("Trend momentum is an experiment: it failed the ten-year test");
    expect(t).toContain("2 of 4 in place · ZZZQ missing"); // the book's safety strip
    expect(html).not.toContain("<table");
    expect((html.match(/class="row"/g) ?? []).length).toBe(5);
    expect(glitches(html)).toEqual([]);
  });
  it("empty: nothing held, in words", () => {
    const t = text(render(<Positions />, { ctx: ctxFor(run, { overview: ov({ ledger: null }) }), seeds: [book(RAW_EMPTY_BOOK)] }));
    expect(t).toContain("Positions 0 open");
    expect(t).toContain("Nothing held right now.");
  });
  it("/holdings and /trades lead to Positions", () => {
    expect(mainSrc).toContain('<Route path="holdings" element={<Navigate to="/positions" replace />} />');
    expect(mainSrc).toContain('<Route path="trades" element={<Navigate to="/positions" replace />} />');
  });
});

const items: TapeItem[] = [
  { kind: "fill", ts: "2026-11-16T15:06:00Z", side: "sell", qty: 5, venue_px: 142.18, shadow_px: 142.1, fees: 0.02, tag: null, symbol: "NVDA",
    strategy: "trend_momentum", trade_id: 7, exit_reason: "target", pnl_usd: 38.2 },
  { kind: "signal", ts: "2026-11-16T15:00:00Z", mode: "live", strategy: "dip_reversal", symbol: "AMD", entry: 150, stop: 145, target: 160,
    reasons: [], veto: "company news in 24 h", approved: null, qty: null, binding: null, trade_id: null, counterfactual: null },
  { kind: "signal", ts: "2026-11-16T14:00:00Z", mode: "shadow", strategy: "orb_stocks_in_play", symbol: "TSLA", entry: 300, stop: 290, target: 320,
    reasons: [], veto: null, approved: null, qty: null, binding: null, trade_id: null, counterfactual: { pnl_usd: 12 } },
];

describe("Activity (v4)", () => {
  const ctx = ctxFor(run, { overview: ov({ halts_today: [{ kind: "losses", reason: "3 in a row", ts: "2026-11-16T15:03:00Z" }] }) });
  it("rows with one sentence and one number, today's halts as alerts, test-only rows off by default", () => {
    const html = render(<Tape />, { ctx, seeds: [{ key: ["tape", 1], data: items, infinite: true }] });
    const t = text(html);
    expect(t).toContain("Sold NVDA target reached · 5 at $142.18");
    expect(t).toContain("Experiment: failed the ten-year test");
    expect(t).toContain("+$38.20");
    expect(t).toContain("Skipped AMD the company had news in the last 24 hours");
    expect(t).toContain("3 losses in a row: 60-minute pause");
    expect(t).not.toContain("TSLA");
    expect(t).toContain("Test only · 1");
    expect(html).toMatch(/aria-pressed="false"[^>]*>Test only/);
    expect(html).not.toContain("<table");
  });
});

describe("Home fixes (Product, 16 Nov 2026)", () => {
  it("a skipped decision is its own amber row on the first screen", () => {
    const o = ov({ heartbeat: beat({ experiments: [EXP], multi_day: { book: "open", rules: { trend_momentum: {
      note: "no decision today: daily store not refreshed (see logs/fetch_daily.log)" } } } }) });
    const html = renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={o} engineLive book={undefined} risk={undefined} /></MemoryRouter>);
    expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">No decision today: Trend momentum skipped its decision/);
    expect(html.indexOf("No decision today")).toBeLessThan(html.indexOf("Experiment: failed the ten-year test"));
    const yet = ov({ heartbeat: beat({ multi_day: { book: "open", rules: { trend_momentum: { note: "no decision yet: the first one is taken after a close" } } } }) });
    expect(renderToStaticMarkup(<MemoryRouter><HomeStatus run={run} overview={yet} engineLive book={undefined} risk={undefined} /></MemoryRouter>))
      .not.toContain("skipped its decision");
  });
  it("the chart's last point is the hero figure, marked live", () => {
    const o = ov({ daily: [{ session: "2026-11-02", pnl: 50, pnl_venue: 50, pnl_shadow: 50, spy_ret: 0.001, trades: 1 }] });
    const html = render(<Live />, { ctx: ctxFor(run, { overview: o }), seeds: [{ key: ["trades-all", 1], data: { total: 0, rows: [] } }] });
    const hero = text(html).match(/Paper pot (\$[\d,]+\.\d\d)/)![1];
    expect(hero).toBe("$10,070.00");
    expect(html).toContain(`aria-label="Pot ${hero} versus SPY`);
    expect(html).toContain(">Pot $10,070 now<");
  });
});
