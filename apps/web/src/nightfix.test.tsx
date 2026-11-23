// Stakeholder, 18 Nov 2026 23:35 CET, after the first real trading day of the multi-day book:
// 1. the pot read $95,000.00 · $0.00 all time · $0.00 today while 7 stocks were held (it summed closed trades);
// 2. a red "7 stocks have no safety stop" for trend_momentum, a rule that has no per-stock stop by design;
// 3. "Engine offline: nothing new trades" in amber at night, while the Mac is off on purpose.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { HomeStatus } from "./components/Home";
import { BookChecks } from "./components/Book";
import { engineChip } from "./components/Shell";
import { Live } from "./pages/Live";
import { bookChecks, parseBook, parseSeries, potNow } from "./lib/book";
import { needsStop } from "./lib/exits";
import { engineClock, marketHours } from "./lib/engineclock";
import type { Heartbeat, Overview } from "./lib/api";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const inRouter = (el: ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);

// The real answer of 18 Nov 2026 (run 1283812969414991), in kb_book's and kb_equity_series' shapes.
const SESSION = "2026-11-18";
const pos = (symbol: string, qty: number, avg: number, mark: number, rule = "trend_momentum", stop: unknown = null) =>
  ({ rule, symbol, qty, avg_cost: avg, mark, last: mark, opened_session: SESSION, dividends: 0, stop });
const RAW = {
  run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 79671.79, invested: 15325.48, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [pos("ABBV", 5, 286.16, 285.92), pos("APA", 26, 45.54, 45.54), pos("BBY", 23, 100.32, 100.32), pos("BIIB", 11, 251.48, 251.48),
    pos("PFE", 59, 28.07, 28.065), pos("TMO", 4, 692.95, 692.49), pos("XOM", 20, 160.59, 160.62)],
  exposure: { gross: 0.161325, overnight: 0.161325, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 },
};
const SERIES = parseSeries([
  { session: "2026-11-16", close_equity: 95000, marked: true }, { session: "2026-11-17", close_equity: 95000, marked: true },
  { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });
// Wed 18 Nov 2026, 23:35 CET = 17:35 ET: after the close.
const NIGHT = new Date("2026-11-18T22:35:00Z");
const overview = (o: Partial<Overview> = {}): Overview => ({
  ...emptyOverview(run),
  daily: [{ session: "2026-11-16", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.013967, trades: 0 },
    { session: "2026-11-17", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.012294, trades: 0 },
    { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.009749, trades: 0 }],
  // the intraday ledger knows nothing of the multi-day book: 95,000 in cash, no positions
  ledger: { ts: "2026-11-18T20:59:21Z", cash: 95000, positions: {}, equity_venue: 95000, equity_shadow: 95000 },
  ...o,
});

describe("1. the pot, all time and today come from the marked book", () => {
  const book = parseBook(RAW, 7);
  it("reads close_equity (equity_reported) and the previous close", () => {
    expect(book.equityReported).toBe(94997.26);
    const p = potNow(run, overview(), book, SERIES, NIGHT);
    expect(p.pot).toBeCloseTo(94997.26, 2);
    expect(p.dayPnl).toBeCloseTo(-2.74, 2);
    expect(p.dayLabel).toBe("today");
    expect(p.today).toBe(true);
  });
  it("day one: minus the session's start equity when there is no earlier close", () => {
    const p = potNow(run, overview(), book, SERIES.slice(2), NIGHT);
    expect(p.dayPnl).toBeCloseTo(94997.26 - 95000, 2);
  });
  it("the next morning, before the open: the last session's change, named by its day", () => {
    const p = potNow(run, overview(), book, SERIES, new Date("2026-11-19T12:00:00Z"));
    expect(p.pot).toBeCloseTo(94997.26, 2);
    expect(p.dayLabel).toBe("on Wed, Nov 18");
    expect(p.today).toBe(false);
  });
  it("during the next session: the live mark (the minute ledger, the whole run) less the last close", () => {
    const o = overview({ ledger: { ts: "2026-11-19T16:00:00Z", cash: 79671.79, positions: {}, equity_venue: 95040, equity_shadow: 95030 } });
    const p = potNow(run, o, book, SERIES, new Date("2026-11-19T16:01:00Z"));
    expect(p.pot).toBe(95030);
    expect(p.dayPnl).toBeCloseTo(95030 - 94997.26, 2);
    expect(p.dayLabel).toBe("today");
  });
  it("no ledger row yet: the realised figures, as before", () => {
    expect(potNow(run, overview({ ledger: null }), parseBook({ run_id: 7, session: null, mtm: null, positions: [] }, 7), [], NIGHT).pot).toBe(95000);
  });
  it("Home shows $94,997.26 · −$2.74 all time · −$2.74 today, and the chart ends at the same figure", () => {
    const html = render(<Live />, { ctx: ctxFor(run, { overview: overview() }),
      seeds: [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: book }, { key: ["equity_series", 7], data: SERIES }] });
    const t = text(html);
    expect(t).toContain("Paper pot $94,997.26");
    expect(t).toContain("−$2.74 all time");
    expect(t).not.toContain("$95,000.00");
  });
});

describe("2. the safety-stop alarm counts only positions meant to carry a broker stop", () => {
  it("the momentum rules and their neighbours sell by their target list: no stop by design", () => {
    for (const r of ["trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter", "trend_momentum~reg60"]) expect(needsStop(r)).toBe(false);
    for (const r of ["orb", "noise_band", "some_new_multi_day_rule", "trend_momentum_v2"]) expect(needsStop(r)).toBe(true);
  });
  it("the source wins when it says so (a position's `exit`)", () => {
    expect(needsStop("trend_momentum", "stop")).toBe(true);
    expect(needsStop("orb", "target_list")).toBe(false);
  });
  it("18 Nov: 7 trend_momentum stocks without a stop raise no alarm", () => {
    const b = parseBook(RAW, 7);
    expect(bookChecks(b)).toMatchObject({ n: 7, needed: 0, missing: [], byDesign: ["ABBV", "APA", "BBY", "BIIB", "PFE", "TMO", "XOM"] });
    const html = inRouter(<HomeStatus run={run} overview={overview()} engineLive book={b} risk={{ severity: "ok", items: [] }} />);
    expect(text(html)).not.toMatch(/no safety stop/);
    expect(html).not.toContain("status-row breach");
    expect(text(inRouter(<BookChecks b={b} />))).toContain("None by design");
  });
  it("still red for a rule that should carry one: an intraday position, or a multi-day sleeve with a stop", () => {
    const b = parseBook({ ...RAW, positions: [...RAW.positions, pos("NVDA", 3, 190, 191, "orb"), pos("GM", 2, 60, 61, "trend_momentum", null)] }, 7);
    const stopRule = parseBook({ ...RAW, positions: [{ ...pos("KO", 4, 70, 71, "trend_momentum"), exit: "stop" }] }, 7);
    expect(bookChecks(b).missing).toEqual(["NVDA"]);
    expect(bookChecks(stopRule).missing).toEqual(["KO"]);
    const html = inRouter(<HomeStatus run={run} overview={overview()} engineLive book={b} risk={{ severity: "ok", items: [] }} />);
    expect(html).toMatch(/class="status-row breach"[^>]*>.*1 stock has no safety stop/);
    expect(text(inRouter(<BookChecks b={b} />))).toContain("0 of 1 in place · NVDA missing");
  });
});

describe("3. the engine at night is asleep, not offline", () => {
  const beat = (ts: string, state = "market_closed", next_open = "2026-11-19T09:30:00-05:00"): Heartbeat =>
    ({ process: "engine", ts, detail: { state, next_open } });
  const o = (b: Heartbeat) => overview({ heartbeat: [b] });
  it("market hours are 09:30-16:00 ET on trading days", () => {
    expect(marketHours(new Date("2026-11-18T15:00:00Z"))).toBe(true);  // Wed 10:00 ET
    expect(marketHours(new Date("2026-11-18T22:35:00Z"))).toBe(false); // Wed 17:35 ET
    expect(marketHours(new Date("2026-11-21T15:00:00Z"))).toBe(false); // Saturday
    // Thanksgiving: the last heartbeat (market closed) said the next open is Friday
    expect(marketHours(new Date("2026-11-26T15:00:00Z"), beat("2026-11-25T21:20:00Z", "market_closed", "2026-11-27T09:30:00-05:00"))).toBe(false);
    // an engine that died while trading: still market hours
    expect(marketHours(new Date("2026-11-19T15:00:00Z"), beat("2026-11-19T14:50:00Z", "trading", "2026-11-20T09:30:00-05:00"))).toBe(true);
  });
  it("23:35 CET: asleep, starting at 13:45 Berlin time, in the viewer's time", () => {
    const c = engineClock(NIGHT, beat("2026-11-18T21:20:00Z"), "Europe/Berlin");
    expect(c).toMatchObject({ kind: "asleep", text: "Engine asleep · starts 13:45" });
    expect(engineClock(NIGHT, beat("2026-11-18T21:20:00Z"), "America/New_York").text).toBe("Engine asleep · starts 07:45");
    // Friday night: Monday
    expect(engineClock(new Date("2026-11-20T22:35:00Z"), beat("2026-11-20T21:20:00Z"), "Europe/Berlin").text).toBe("Engine asleep · starts Mon 13:45");
  });
  it("offline during market hours is still the amber warning", () => {
    expect(engineClock(new Date("2026-11-19T15:00:00Z"), beat("2026-11-19T14:50:00Z", "trading"), "Europe/Berlin").kind).toBe("offline");
  });
  it("the header chip and the Home row: calm and neutral at night, in plain English", () => {
    const b = beat("2026-11-18T21:20:00Z");
    vi.useFakeTimers({ now: NIGHT });
    try {
      const chip = engineChip(o(b), run);
      expect(chip).toMatchObject({ live: false, asleep: true, text: "Engine asleep" });
      const html = inRouter(<HomeStatus run={run} overview={o(b)} engineLive={false} engine={chip} book={undefined} risk={{ severity: "ok", items: [] }} />);
      expect(html).toMatch(/class="status-row neutral"[^>]*><span class="status-text">Engine asleep · starts /);
      expect(html).not.toContain("status-row warn");
      expect(text(html)).not.toMatch(/nothing new trades/);
    } finally { vi.useRealTimers(); }
  });
  it("the chip's small line: the host alone while it runs (no ticking age, 23 Nov 2026); 'last seen …' when offline", () => {
    vi.useFakeTimers({ now: new Date("2026-11-19T15:00:00Z") });
    try {
      const up = { ...beat("2026-11-19T14:59:48Z", "trading"), detail: { state: "trading", host: "mac-mini", next_open: "2026-11-19T09:30:00-05:00" } };
      expect(engineChip(o(up), run)).toMatchObject({ live: true, sub: "mac-mini" });
      expect(engineChip(o({ ...up, detail: { state: "trading" } }), run).sub).toBe("");
      expect(engineChip(o(up), run).sub).not.toMatch(/ago|\d+ s/);
      expect(engineChip(o(beat("2026-11-19T14:50:00Z", "trading")), run)).toMatchObject({ live: false, text: "Engine offline", sub: "last seen 10 min ago" });
      expect(engineChip(o(beat("2026-11-19T14:58:00Z", "trading")), run).sub).toBe("last seen 2 min ago");
    } finally { vi.useRealTimers(); }
  });
  it("during market hours the row warns, with fixed copy", () => {
    const b = beat("2026-11-19T14:50:00Z", "trading");
    vi.useFakeTimers({ now: new Date("2026-11-19T15:00:00Z") });
    try {
      const chip = engineChip(o(b), run);
      expect(chip).toMatchObject({ live: false, asleep: false, text: "Engine offline" });
      const html = inRouter(<HomeStatus run={run} overview={o(b)} engineLive={false} engine={chip} book={undefined} risk={{ severity: "ok", items: [] }} />);
      expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">Engine offline · no new trades until it is back/);
    } finally { vi.useRealTimers(); }
  });
});
