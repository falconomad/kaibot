// @vitest-environment jsdom
// Live marks, the polling itself (stakeholder, 19 Nov 2026). Home is mounted in jsdom with the clock in the test's
// hands and /api/marks replaced by a counter, so each rule can be seen happening:
// - the market is open and the tab is visible: one request at once, then one every 10 seconds, and the figures follow;
// - the tab is hidden: no request; the prices on screen go stale after 30 s and the page falls back by itself;
// - the market is closed: no request at all, however long the page stays up; the open arrives: the first request;
// - a request that fails is not tried again before the next poll, and the last answer is dropped at 30 s.
// And axe on Home and Positions in the live state and in the fallback (the other states are in a11y/pages.test.tsx).
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { audit, lines } from "./a11y/axe";
import { mount, unmount } from "./a11y/dom";
import type { Overview } from "./lib/api";
import { parseBook, parseSeries } from "./lib/book";
import type { Marks } from "./lib/marks";
import { Live } from "./pages/Live";
import { Positions } from "./pages/Positions";
import { ctxFor, emptyOverview, paperRun, render } from "./pages/testkit";

const asked = vi.hoisted(() => ({ marks: vi.fn<() => Promise<unknown>>(), prices: vi.fn<(on: (s: unknown) => void) => () => void>() }));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: (_t, k) => (k === "marks" ? asked.marks : k === "prices" ? asked.prices : never) }) };
});
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const SESSION = "2026-11-18";
const pos = (symbol: string, qty: number, avg: number, mark: number) =>
  ({ rule: "trend_momentum", symbol, qty, avg_cost: avg, mark, last: mark, opened_session: SESSION, dividends: 0, stop: null, stop_state: "none_by_design", exit: "target_list" });
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 91784.86, invested: 3212.4, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [pos("XOM", 20, 160.59, 160.62)], exposure: { gross: 0.03, overnight: 0.03, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 } }, 7);
const SERIES = parseSeries([{ session: "2026-11-17", close_equity: 95000, marked: true }, { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });
const overview = (o: Partial<Overview> = {}): Overview => ({ ...emptyOverview(run),
  daily: [{ session: "2026-11-17", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.012294, trades: 0 },
    { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.009749, trades: 0 }],
  heartbeat: [{ process: "engine", ts: "2026-11-19T17:39:55Z", detail: { state: "trading", next_open: "2026-11-20T09:30:00-05:00" } }],
  ledger: { ts: "2026-11-19T17:39:21Z", cash: 94960, positions: {}, equity_venue: 94960, equity_shadow: 94960 }, today: { trades: 0, pnl: 0 }, ...o });
const seeds = [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: BOOK }, { key: ["equity_series", 7], data: SERIES }];

/** What /api/marks answers: XOM at `price`, read `age` milliseconds ago. */
const xom = (price: number, age = 2_000) => async (): Promise<Marks> => ({ at: Date.now() - age, by: { XOM: { qty: 20, price, prevClose: 160.58 } } });
const OPEN = "2026-11-19T17:40:00Z"; // Thursday 12:40 in New York
const start = async (now: string, o = overview()) => {
  vi.useFakeTimers({ now: new Date(now), toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  await mount(<Live />, { ctx: ctxFor(run, { overview: o }), seeds });
  await pass(50);
};
/** Let `ms` of the page's time pass: timers, polls and their answers. */
const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const page = () => (document.body.textContent ?? "").replace(/\s+/g, " ");
const tab = async (state: "hidden" | "visible") => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  await act(async () => { window.dispatchEvent(new Event("visibilitychange")); });
};

beforeEach(() => { asked.marks.mockReset(); asked.prices.mockReset(); asked.prices.mockImplementation(() => () => {}); });
afterEach(async () => { await unmount(); await tab("visible"); vi.useRealTimers(); });

describe("the market is open and the tab is visible", () => {
  it("asks at once, then every 10 seconds; the pot, today and the holding follow, and the line counts the seconds", async () => {
    asked.marks.mockImplementation(xom(158.68));
    await start(OPEN);
    expect(asked.marks).toHaveBeenCalledTimes(1);
    expect(page()).toContain("Paper pot$94,958.46");           // 94,997.26 + 20 x (158.68 - 160.62)
    expect(page()).toContain("−$38.80 today");
    expect(page()).toContain("Live · prices 2 s ago");
    expect(page()).toContain("$3,173.60−$38.20−1.18% today");
    await pass(3_000);
    expect(page()).toContain("Live · prices 5 s ago");         // the same prices, three seconds older
    expect(asked.marks).toHaveBeenCalledTimes(1);
    asked.marks.mockImplementation(xom(159.68, 1_000));
    await pass(7_000);
    expect(asked.marks).toHaveBeenCalledTimes(2);
    expect(page()).toContain("Paper pot$94,978.46");
    expect(page()).toContain("−$18.80 today");
    expect(page()).toContain("Live · prices 1 s ago");
    expect(page()).toContain("$3,193.60−$18.20−0.56% today");
    await pass(60_000);
    expect(asked.marks).toHaveBeenCalledTimes(8);              // one a poll: 6 a minute, never more
  });

  it("a number that moved is tinted for a moment by Tick, and nothing on the page loops", async () => {
    asked.marks.mockImplementation(xom(158.68));
    await start(OPEN);
    // the engine's figure was on screen first (94,960.00): replacing it with the live one is not a move, so no tint
    expect(document.querySelectorAll(".tint-up, .tint-down")).toHaveLength(0);
    asked.marks.mockImplementation(xom(159.68, 1_000));
    await pass(10_000);
    expect(document.querySelector(".hero .tint-up")?.textContent).toBe("$94,978.46");
    await pass(700);
    expect(document.querySelectorAll(".tint-up, .tint-down")).toHaveLength(0);
    // the chart's point follows the live pot without its pop: a pop every 10 seconds would be a loop
    expect(document.querySelectorAll(".point-pop, .point-pulse")).toHaveLength(0);
  });

  it("the function cannot answer here (a preview without keys): the engine's figures, and the page says so", async () => {
    asked.marks.mockResolvedValue(null);
    await start(OPEN);
    expect(asked.marks).toHaveBeenCalledTimes(1);
    expect(page()).toContain("Paper pot$94,960.00");
    expect(page()).toContain("Updated a minute ago by the engine");
    expect(page()).toContain("$3,212.40+$0.60");
    expect(page()).not.toMatch(/Live ·|% today/);
    expect(document.querySelector(".hold-end .day")).toBeNull(); // the kept place is given back
  });

  it("a request that fails is not tried again before the next poll; the last prices are dropped when 30 s old", async () => {
    asked.marks.mockImplementationOnce(xom(158.68)).mockRejectedValue(new TypeError("Failed to fetch"));
    await start(OPEN);
    expect(page()).toContain("Live · prices 2 s ago");
    await pass(10_000);
    expect(asked.marks).toHaveBeenCalledTimes(2);              // the poll, once: no retry
    expect(page()).toContain("Live · prices 12 s ago");        // one missed poll does not flip the page
    expect(page()).toContain("Paper pot$94,958.46");
    await pass(17_000);
    expect(page()).toContain("Live · prices 29 s ago");
    await pass(1_500);
    expect(page()).toContain("Updated a minute ago by the engine");
    expect(page()).toContain("Paper pot$94,960.00");
    expect(page()).toContain("$3,212.40+$0.60");
    expect(page()).not.toMatch(/Live ·|% today/);
    expect(document.querySelectorAll(".tint-up, .tint-down")).toHaveLength(0); // a change of source is not a move: no tint
    expect(document.querySelector(".hold-end .day")).toBeNull(); // nothing is coming: no place is kept
    expect(asked.marks).toHaveBeenCalledTimes(3);              // 0, 10 and 20 s
    asked.marks.mockImplementation(xom(158.68, 500));
    await pass(2_000);
    expect(asked.marks).toHaveBeenCalledTimes(4);
    expect(page()).toContain("Live · prices");                 // and back, when the function answers again
  });
});

describe("the tab is hidden", () => {
  it("makes no request; what is on screen stops being called live after 30 s; coming back asks at once", async () => {
    asked.marks.mockImplementation(xom(158.68));
    await start(OPEN);
    await tab("hidden");
    await pass(45_000);
    expect(asked.marks).toHaveBeenCalledTimes(1);
    expect(page()).toContain("Updated a minute ago by the engine");
    expect(page()).not.toContain("Live ·");
    expect(document.querySelector(".hold-end .day")).toBeNull();
    let answer: (m: Marks) => void = () => {};
    asked.marks.mockImplementation(() => new Promise<Marks>((done) => { answer = done; }));
    await tab("visible");
    await pass(50);
    expect(asked.marks).toHaveBeenCalledTimes(2);
    expect(document.querySelector(".hold-end .day")?.textContent).toBe("\u00a0"); // the row keeps the line's place while it asks
    answer(await xom(158.68)());
    await pass(50);
    expect(page()).toContain("Live · prices 2 s ago");
    expect(document.querySelector(".hold-end .day")?.textContent).toBe("−1.18% today");
  });
});

describe("the market is closed", () => {
  it("no request at all, however long the page stays up; the booked close is shown and named", async () => {
    asked.marks.mockImplementation(xom(158.68));
    await start("2026-11-19T12:00:00Z", overview({ ledger: null, heartbeat: [] })); // 07:00 in New York
    await pass(5 * 60_000);
    expect(asked.marks).not.toHaveBeenCalled();
    expect(page()).toContain("Paper pot$94,997.26");
    expect(page()).toContain("At Wednesday's close");
    expect(page()).toContain("$3,212.40+$0.60");
  });

  it("evening, weekend and a holiday: no request", async () => {
    const thanksgiving = overview({ ledger: null, heartbeat: [{ process: "engine", ts: "2026-11-25T21:20:00Z", detail: { state: "market_closed", next_open: "2026-11-27T09:30:00-05:00" } }] });
    for (const [when, o] of [["2026-11-19T21:00:00Z", overview()], ["2026-11-21T17:40:00Z", overview({ ledger: null })], ["2026-11-26T17:40:00Z", thanksgiving]] as const) {
      asked.marks.mockImplementation(xom(158.68));
      await start(when, o);
      await pass(60_000);
      expect(asked.marks, when).not.toHaveBeenCalled();
      expect(page(), when).not.toContain("Live ·");
      await unmount();
    }
  });

  it("the open arrives while the page is up: the first request follows within half a minute", async () => {
    asked.marks.mockImplementation(xom(160.9));
    const before = overview({ ledger: null, heartbeat: [{ process: "engine", ts: "2026-11-19T14:29:30Z", detail: { state: "market_closed", next_open: "2026-11-19T09:30:00-05:00" } }] });
    await start("2026-11-19T14:29:45Z", before); // 09:29:45 in New York
    expect(asked.marks).not.toHaveBeenCalled();
    expect(page()).toContain("At Wednesday's close");
    await pass(30_100); // the page looks at the clock again: the market is open, so it asks
    await pass(100);    // and the answer is drawn
    expect(asked.marks).toHaveBeenCalledTimes(1);
    expect(page()).toContain("Live · prices 2 s ago");
    expect(page()).toContain("Paper pot$95,002.86");
  });

  it("the close arrives while the page is up: the requests stop", async () => {
    asked.marks.mockImplementation(xom(158.68));
    await start("2026-11-19T20:59:30Z"); // 15:59:30 in New York
    expect(asked.marks).toHaveBeenCalledTimes(1);
    await pass(45_000);
    const n = asked.marks.mock.calls.length;
    expect(n).toBeLessThanOrEqual(4);
    await pass(10 * 60_000);
    expect(asked.marks).toHaveBeenCalledTimes(n);
    expect(page()).not.toContain("Live ·");
  });
});

describe("streamed prices (scripts/price_stream.py over Supabase Realtime)", () => {
  /** The channel as api.prices hands it to the page: `send` plays one broadcast from the relay. */
  const channel = () => {
    const c: { on: ((s: unknown) => void) | null; left: number; send: (prices: Record<string, number>) => Promise<void> } = {
      on: null, left: 0,
      send: async (prices) => { await act(async () => { c.on?.({ at: Date.now(), asOf: new Date().toISOString(), prices }); }); },
    };
    asked.prices.mockImplementation((on) => { c.on = on; return () => { c.left++; c.on = null; }; });
    return c;
  };

  it("a message replaces the polled price within the second and the tag says live; 5 s of silence: back on the 10 s poll", async () => {
    const c = channel();
    asked.marks.mockImplementation(xom(158.68));
    await start(OPEN);
    expect(asked.prices).toHaveBeenCalledTimes(1);
    expect(page()).toContain("Paper pot$94,958.46");
    expect(document.querySelector(".fresh .feed")?.textContent).toBe("10 s");
    await c.send({ XOM: 159.18 });
    expect(page()).toContain("Paper pot$94,968.46");           // 94,997.26 + 20 x (159.18 - 160.62)
    expect(page()).toContain("Live · prices 1 s ago");
    expect(document.querySelector(".fresh .feed")?.textContent).toBe("live");
    await pass(1_000); await c.send({ XOM: 159.68 });
    expect(page()).toContain("Paper pot$94,978.46");
    expect(asked.marks).toHaveBeenCalledTimes(1);              // the poll keeps its own pace
    await pass(5_100);                                         // silent for more than 5 s
    expect(page()).toContain("Paper pot$94,958.46");           // the polled price again, without a word
    expect(document.querySelector(".fresh .feed")?.textContent).toBe("10 s");
    expect(page()).not.toMatch(/error|offline|lost/i);
  });

  it("a stream never makes stale polled figures live, and the channel is left when the market closes", async () => {
    const c = channel();
    asked.marks.mockImplementationOnce(xom(158.68)).mockRejectedValue(new TypeError("Failed to fetch"));
    await start("2026-11-19T20:59:00Z");                       // 15:59 in New York
    for (let i = 0; i < 29; i++) { await c.send({ XOM: 159 }); await pass(1_000); }
    expect(page()).not.toContain("Live ·");                   // the poll is 30 s old: not live, stream or not
    expect(page()).toContain("Paper pot$94,960.00");           // the engine's figure, not the streamed price
    expect(c.left).toBe(0);
    await pass(60_000);                                        // 16:00 has passed
    expect(c.left).toBe(1);
  });
});

describe("axe: the live state and its fallback", () => {
  it("Home and Positions: no serious or critical finding, and the line that counts seconds is plain text", async () => {
    for (const marks of [await xom(158.68)(), null]) {
      vi.useFakeTimers({ now: new Date(OPEN), toFake: ["Date"] });
      const m = marks && { ...marks, at: Date.now() - 2_000 };
      const all = [...seeds, { key: ["marks"], data: m }, { key: ["md-plan", 7], data: null }, { key: ["health"], data: null }];
      const home = render(<Live />, { ctx: ctxFor(run, { overview: overview() }), seeds: all });
      const pos = render(<Positions />, { ctx: ctxFor(run, { overview: overview() }), seeds: all, path: "/positions", route: "/positions" });
      vi.useRealTimers();
      expect(home).toContain(m ? '<p class="fresh live">Live · prices 2 s ago<span class="feed" title="Prices update every 10 s">10 s</span></p>' : '<p class="fresh">Updated a minute ago by the engine</p>');
      expect(pos).toContain(m ? '<p class="fresh live">Live · prices 2 s ago<span class="feed" title="Prices update every 10 s">10 s</span></p>' : "<p class=\"fresh\">At Wednesday&#x27;s close</p>");
      expect(lines(await audit(home))).toEqual([]);
      expect(lines(await audit(pos))).toEqual([]);
    }
  });
});
