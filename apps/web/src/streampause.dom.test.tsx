// @vitest-environment jsdom
// The price stream's channel is held only while it can be seen (frontend performance pass, 23 Nov 2026):
// - the app goes to the background (another app, the screen off): the page lets go of the channel, so no message is
//   parsed or drawn while nobody looks; shown again: it asks for the channel again and the next message lands;
// - the page goes (a move to another screen, sign-out): the channel is let go, once, and nothing else holds on to it.
// Home is mounted in jsdom with api.prices replaced by a recorder (no network, no keys).
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, unmount } from "./a11y/dom";
import type { Overview } from "./lib/api";
import { parseBook, parseSeries } from "./lib/book";
import type { Marks } from "./lib/marks";
import { Live } from "./pages/Live";
import { ctxFor, emptyOverview, paperRun } from "./pages/testkit";

const asked = vi.hoisted(() => ({ marks: vi.fn<() => Promise<unknown>>(), prices: vi.fn<(on: (s: unknown) => void, down?: (d: boolean) => void) => () => void>() }));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: (_t, k) => (k === "marks" ? asked.marks : k === "prices" ? asked.prices : never) }) };
});
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const SESSION = "2026-11-18";
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 91784.86, invested: 3212.4, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [{ rule: "trend_momentum", symbol: "XOM", qty: 20, avg_cost: 160.59, mark: 160.62, last: 160.62, opened_session: SESSION, dividends: 0,
    stop: null, stop_state: "none_by_design", exit: "target_list" }],
  exposure: { gross: 0.03, overnight: 0.03, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 } }, 7);
const SERIES = parseSeries([{ session: "2026-11-17", close_equity: 95000, marked: true }, { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });
const overview: Overview = { ...emptyOverview(run),
  daily: [{ session: "2026-11-17", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.012294, trades: 0 },
    { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.009749, trades: 0 }],
  heartbeat: [{ process: "engine", ts: "2026-11-19T17:39:55Z", detail: { state: "trading", next_open: "2026-11-20T09:30:00-05:00" } }],
  ledger: { ts: "2026-11-19T17:39:21Z", cash: 94960, positions: {}, equity_venue: 94960, equity_shadow: 94960 }, today: { trades: 0, pnl: 0 } };
const seeds = [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: BOOK }, { key: ["equity_series", 7], data: SERIES }];
const OPEN = "2026-11-19T17:40:00Z"; // Thursday 12:40 in New York

const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const page = () => (document.body.textContent ?? "").replace(/\s+/g, " ");
const tab = async (state: "hidden" | "visible") => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  await act(async () => { document.dispatchEvent(new Event("visibilitychange", { bubbles: true })); });
};
const paused = () => document.querySelector(".paused")?.textContent ?? null;
/** The channel as api.prices hands it out: who holds it now, how often it was let go, one relay broadcast, and a drop
 *  of the joined channel (api.prices' second argument, #601). */
const channel = () => {
  const c = { holders: new Set<(s: unknown) => void>(), joins: 0, left: 0, down: (_d: boolean) => {},
    send: async (prices: Record<string, number>) => {
      await act(async () => { for (const on of c.holders) on({ at: Date.now(), asOf: new Date().toISOString(), prices }); });
    },
    drop: async () => { await act(async () => { c.down(true); }); } };
  asked.prices.mockImplementation((on, down?: (d: boolean) => void) => {
    c.joins++; c.holders.add(on); if (down) c.down = down;
    let done = false;
    return () => { if (!done) { done = true; c.left++; c.holders.delete(on); } };
  });
  return c;
};

beforeEach(async () => {
  asked.marks.mockReset(); asked.prices.mockReset();
  asked.marks.mockImplementation(async (): Promise<Marks> => ({ at: Date.now() - 2_000, by: { XOM: { qty: 20, price: 158.68, prevClose: 160.58 } } }));
  vi.useFakeTimers({ now: new Date(OPEN), toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
});
afterEach(async () => { await unmount(); await tab("visible"); vi.useRealTimers(); });

describe("the price stream while the app is hidden", () => {
  it("lets go of the channel when hidden and takes it again when shown; a message after that lands", async () => {
    const c = channel();
    await mount(<Live />, { ctx: ctxFor(run, { overview }), seeds });
    await pass(50);
    expect(c.joins).toBe(1);
    await c.send({ XOM: 159.18 });
    expect(page()).toContain("Paper pot$94,968.46");
    await tab("hidden");
    expect(c.left).toBe(1);
    expect(c.holders.size).toBe(0);                            // nothing is parsed or drawn in the background
    await pass(60_000);
    expect(c.joins).toBe(1);                                   // and nothing joins again while hidden
    await tab("visible");
    await pass(50);
    expect(c.joins).toBe(2);
    expect(c.holders.size).toBe(1);
    await c.send({ XOM: 159.68 });
    expect(page()).toContain("Paper pot$94,978.46");
  });

  it("a page that goes lets go of the channel exactly once, hidden or not", async () => {
    const c = channel();
    await mount(<Live />, { ctx: ctxFor(run, { overview }), seeds });
    await pass(50);
    await tab("hidden");
    await tab("visible");
    await pass(50);
    expect([c.joins, c.left]).toEqual([2, 1]);
    await unmount();
    expect([c.joins, c.left, c.holders.size]).toEqual([2, 2, 0]);
  });

  it("no 'Prices paused' after a hidden then shown cycle (#601's cue keeps its rules)", async () => {
    const c = channel();
    await mount(<Live />, { ctx: ctxFor(run, { overview }), seeds });
    await pass(50);
    await c.send({ XOM: 159.18 });
    expect(paused()).toBeNull();
    await tab("hidden");
    await pass(90_000);                                        // every price on the page is now 90 s old
    expect(paused()).toBeNull();                               // nothing is counted while hidden
    await tab("visible");
    await pass(50);                                            // the poll answers, the channel is joined again
    expect(c.joins).toBe(2);
    expect(paused()).toBeNull();
    await c.send({ XOM: 159.68 });
    await pass(3_000);
    expect(paused()).toBeNull();
    expect(page()).not.toContain("Prices paused");
  });

  it("a dropped channel shows 'Prices paused'; hidden then shown, the rejoined channel is not down", async () => {
    const c = channel();
    await mount(<Live />, { ctx: ctxFor(run, { overview }), seeds });
    await pass(50);
    await c.drop();
    expect(paused()).toBe("Prices paused");                    // #601: a joined channel that is lost is a pause
    await tab("hidden");
    expect(paused()).toBeNull();
    await pass(30_000);
    await tab("visible");
    await pass(50);
    expect(c.joins).toBe(2);
    expect(paused()).toBeNull();
  });
});
