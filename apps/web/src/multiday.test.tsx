// #229 follow-ups on PR #247's shapes (migration 0020, heartbeat multi_day.book_line). Every field optional.
import { describe, expect, it, vi } from "vitest";
import css from "./app.css?raw";
import { Positions } from "./pages/Positions";
import { bookLine, lineText, parsePlan, parseSold, reasonText, rowTag, sectorText, sellWhen } from "./lib/multiday";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";
import { etToday } from "./lib/derive";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun();
const today = etToday();
const planRaw = { rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-17", fills_at_close_of: today, decides: true,
  sell: [{ symbol: "XOM", why: "rank" }], buy_candidates: ["AAPL"],
  orders: [{ symbol: "XOM", shares_now: 2, shares_target: 0, action: "sell", why: "rank", coid: "c1", state: "working" },
    { symbol: "NVDA", shares_now: 3, shares_target: 5, action: "resize", why: null, coid: null, state: "planned" },
    { symbol: "AAPL", shares_now: 1, shares_target: 4, action: "buy", why: "rank", coid: "c2", state: "working" },
    { symbol: "BAD", action: "launch" }], skipped: 1, note: null }] };

describe("the shapes", () => {
  it("the stop line: '2.2% below its peak · retires at 21%', only for this run, nothing when absent", () => {
    const md = { book_line: { run_id: 1, equity: 10412.5, peak: 10650, peak_ts: "x", drawdown_pct: 2.23, end_pct: 21, ended: false } };
    expect(lineText(bookLine(md, 1)!)).toBe("2.2% below its peak · retires at 21%");
    expect(bookLine(md, 2)).toBeNull();
    expect(lineText(bookLine({ book_line: { ...md.book_line, drawdown_pct: 0 } }, 1)!)).toBe("At its peak · retires at 21%");
    expect(lineText(bookLine({ book_line: { ...md.book_line, ended: true } }, 1)!)).toMatch(/^Stop line reached/);
    for (const bad of [undefined, {}, { book_line: { equity: 1 } }]) expect(bookLine(bad, 1)).toBeNull();
  });
  it("sell readings in words; a met one is flagged", () => {
    const r = sellWhen([{ code: "rank", reading: 41, threshold: 100, of: 503, met: false },
      { code: "trend", reading_pct: -5.5, threshold_pct: 0, window_days: 100, met: true },
      { code: "gap", reading_pct: 7.2, threshold_pct: 15, window_days: 90, met: false }, { code: "?" }, null]);
    expect(r.map((x) => [x.met, x.text])).toEqual([
      [false, "Ranked 41 of 503: sells when outside the top 100"],
      [true, "5.5% below its 100-day average: sells when below it"],
      [false, "Largest daily move 7.2% in 90 days: sells after one over 15%"]]);
    expect(sellWhen(undefined)).toEqual([]);
  });
  it("the plan: orders and their state; row tags; a bad order is dropped", () => {
    const plans = parsePlan(planRaw);
    expect(plans[0].orders.map((o) => [o.symbol, o.state])).toEqual([["XOM", "working"], ["NVDA", "planned"], ["AAPL", "working"]]);
    expect(rowTag(plans, "trend_momentum", "XOM")).toBe("Selling tonight");
    expect(rowTag(plans, "trend_momentum", "NVDA")).toBe("Adding 2 tonight");
    expect(rowTag(plans, "trend_momentum", "MSFT")).toBeNull();
    expect(parsePlan(null)).toEqual([]);
  });
  it("sold holdings and their reason in words; sector 'unknown' reads as not known", () => {
    const s = parseSold([{ rule: "trend_momentum", symbol: "XOM", opened_session: "2026-11-18", closed_session: "2026-12-09", qty: 2, avg_cost: 116.2,
      exit_px: 113.1, pnl_usd: -6.2, dividends: 0, days_held: 21, reason: "rank_below_cutoff" }, { symbol: "X" }]);
    expect(s.map((x) => [x.symbol, x.pnl, x.days, reasonText(x.reason)])).toEqual([["XOM", -6.2, 21, "fell out of the top ranks"]]);
    expect([sectorText("unknown"), sectorText(""), sectorText(null), sectorText("Energy")]).toEqual([null, null, null, "Energy"]);
  });
});

describe("Positions with #247's data", () => {
  const book = { runId: 1, session: "2026-11-17", ts: null, cash: 1000, invested: 900, equity: 1900, startEquity: 2000, prevClose: 1890, dividends: 0, gross: 900,
    overnight: 900, byRule: [], reconciled: null, positions: [
      { rule: "trend_momentum", symbol: "XOM", qty: 2, avgCost: 116.2, mark: 115, priced: true, opened: "2026-11-10", dividends: 0, stop: { price: 100, state: "resting" } },
      { rule: "trend_momentum", symbol: "NVDA", qty: 3, avgCost: 140, mark: 150, priced: true, opened: "2026-11-10", dividends: 0, stop: { price: 130, state: "resting" } }] };
  const ov = { ...emptyOverview(run), heartbeat: [{ process: "engine", ts: new Date().toISOString(),
    detail: { multi_day: { book: "open", rules: {}, book_line: { run_id: 1, equity: 10412.5, peak: 10650, drawdown_pct: 2.23, end_pct: 21, ended: false } } } }] };
  it("shows the stop line, tonight's orders and the row tags", () => {
    const html = render(<Positions />, { ctx: ctxFor(run, { overview: ov as never }), seeds: [{ key: ["book", 1], data: book }, { key: ["md-plan", 1], data: planRaw }] });
    const t = text(html);
    expect(t).toContain("Multi-day book: 2.2% below its peak · retires at 21%");
    expect(t).toMatch(/At the close of .* XOM Sell all 2 shares · fell out of the top ranks at the broker/);
    expect(t).toContain("AAPL Add 3 shares · in the top ranks at the broker · 1 of 4 held");
    expect(html).toMatch(/<b>XOM<\/b>.*?<span class="row-plan">Selling tonight<\/span>/);
    expect(html).toMatch(/<b>NVDA<\/b>.*?<span class="row-plan">Adding 2 tonight<\/span>/);
  });
  it("before 0020 and the engine code: nothing new", () => {
    const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["book", 1], data: book }, { key: ["md-plan", 1], data: null }] });
    expect(html).not.toMatch(/row-plan|next-close|Multi-day book:/);
  });
  it("the styles keep a row's tag and the plan's lines inside a 375 px screen", () => {
    expect(css).toMatch(/\.nc-list li > \* \{ min-width: 0; overflow-wrap: anywhere; \}/);
    expect(css).toMatch(/\.row-plan \{[^}]*color: var\(--warn-text\)/);
  });
});
