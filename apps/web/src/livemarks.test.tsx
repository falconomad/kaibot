// Stakeholder, 19 Nov 2026, during market hours: "why the amounts on the dashboard are not updating in realtime".
// Each holding stood at the last booked close all day, and the pot moved once a minute at best. On the page now:
// 1. live: the pot, today, the chart's last point and every holding at the paper account's prices;
// 2. each fallback (the function unavailable, prices stale, share counts that differ): the pot from the engine's minute
//    ledger, the rows at their close marks, and no figure that mixes the two;
// 3. outside market hours: the booked close, whatever is in the cache.
// Stakeholder, 23 Nov 2026: no line says how old the figures are ("Live · prices 4 s ago", "Updated a minute ago by
// the engine", "At Wednesday's close" are gone). One quiet cue is left: "Prices paused", while the market is open and a
// held stock has had no price for more than a minute (or the stream dropped: livemarks.dom.test.tsx).
// The maths is in lib/marks.test.ts, the function in apiMarks.test.ts, the polling itself in livemarks.dom.test.tsx.
import { afterEach, describe, expect, it, vi } from "vitest";
import css from "./app.css?raw";
import type { Overview } from "./lib/api";
import { parseBook, parseSeries } from "./lib/book";
import { type Marks, liveBook, markOf } from "./lib/marks";
import { Live } from "./pages/Live";
import { BookSheet, Positions } from "./pages/Positions";
import { type Seed, ctxFor, emptyOverview, glitches, paperRun, render, text } from "./pages/testkit";
import liveSrc from "./pages/Live.tsx?raw";
import marksSrc from "./components/Marks.tsx?raw";
import bookHookSrc from "./components/useBook.ts?raw";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
afterEach(() => { vi.useRealTimers(); });

// The book at Wednesday 18 Nov 2026's close (run 7), and the same seven holdings at the broker on Thursday at 12:40 ET.
const SESSION = "2026-11-18";
const pos = (symbol: string, qty: number, avg: number, mark: number) =>
  ({ rule: "trend_momentum", symbol, qty, avg_cost: avg, mark, last: mark, opened_session: SESSION, dividends: 0, stop: null, stop_state: "none_by_design", exit: "target_list" });
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 79671.79, invested: 15325.48, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [pos("ABBV", 5, 286.16, 285.92), pos("APA", 26, 45.54, 45.54), pos("BBY", 23, 100.32, 100.32), pos("BIIB", 11, 251.48, 251.48),
    pos("PFE", 59, 28.07, 28.065), pos("TMO", 4, 692.95, 692.49), pos("XOM", 20, 160.59, 160.62)],
  exposure: { gross: 0.161325, overnight: 0.161325, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 } }, 7);
const SERIES = parseSeries([{ session: "2026-11-16", close_equity: 95000, marked: true }, { session: "2026-11-17", close_equity: 95000, marked: true },
  { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });

const NOW = Date.parse("2026-11-19T17:40:04Z");
const PRICES: Record<string, [number, number, number]> = { // shares, price now, previous close at the broker
  ABBV: [5, 282.37, 286.01], APA: [26, 44.65, 45.52], BBY: [23, 98.72, 100.31], BIIB: [11, 246.62, 251.41],
  PFE: [59, 27.565, 28.06], TMO: [4, 686.36, 692.74], XOM: [20, 158.68, 160.58] };
const marks = (over: Record<string, [number, number, number]> = {}, at = NOW - 4_000): Marks =>
  ({ at, by: Object.fromEntries(Object.entries({ ...PRICES, ...over }).map(([s, v]) => [s, { qty: v[0], price: v[1], prevClose: v[2] }])) });

const overview = (o: Partial<Overview> = {}): Overview => ({ ...emptyOverview(run),
  daily: [{ session: "2026-11-16", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.013967, trades: 0 },
    { session: "2026-11-17", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.012294, trades: 0 },
    { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.009749, trades: 0 }],
  heartbeat: [{ process: "engine", ts: "2026-11-19T17:39:55Z", detail: { state: "trading", next_open: "2026-11-20T09:30:00-05:00" } }],
  // the engine's minute row: one figure for the whole run, no position of the multi-day book, cash equal to the equity
  ledger: { ts: "2026-11-19T17:39:21Z", cash: 94796.94, positions: {}, equity_venue: 94796.94, equity_shadow: 94796.94 },
  today: { trades: 0, pnl: 0 }, ...o });
const seeds = (m: Marks | null | undefined): Seed[] => [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: BOOK },
  { key: ["equity_series", 7], data: SERIES }, { key: ["md-plan", 7], data: null }, { key: ["health"], data: null },
  ...(m === undefined ? [] : [{ key: ["marks"], data: m }])];
const at = (iso: number | string) => vi.useFakeTimers({ now: new Date(iso), toFake: ["Date"] });
const home = (m: Marks | null | undefined, o = overview()) => render(<Live />, { ctx: ctxFor(run, { overview: o }), seeds: seeds(m) });
const positions = (m: Marks | null | undefined, o = overview()) => render(<Positions />, { ctx: ctxFor(run, { overview: o }), seeds: seeds(m), path: "/positions", route: "/positions" });
/** One row's text, by its ticker. */
/** Any line about the figures' age, as the page had before 23 Nov 2026. */
const AGE = /s ago|ago by the engine|Live ·|At [A-Za-z']+ close|At the close of|Updated/;
const rowOf = (html: string, symbol: string, cls = "hold-row") =>
  text(new RegExp(`<button[^>]*class="${cls}"[^>]*>(?:(?!</button>).)*?<b>${symbol}</b>.*?</button>`, "s").exec(html)?.[0] ?? "");

describe("Home while the market is open and the account holds exactly the book", () => {
  it("the pot is the last close plus the day's move, today is that move, and no line says how old the prices are", () => {
    at(NOW);
    const html = home(marks()), t = text(html);
    expect(t).toContain("Paper pot $94,773.29");
    expect(t).toContain("−$226.71 all time");
    expect(t).toContain("−$223.97 today");
    expect(t).not.toMatch(AGE);
    expect(html).not.toMatch(/class="(fresh|paused)/);
    // one source: the engine's minute figure is nowhere on the page
    expect(t).not.toContain("94,796.94");
    expect(glitches(html)).toEqual([]);
  });

  it("a held stock with no price for more than a minute: a quiet 'Prices paused', and the figures fall back", () => {
    at(NOW);
    for (const age of [59_000, 60_000]) expect(text(home(marks({}, NOW - age)))).not.toContain("Prices paused");
    const html = home(marks({}, NOW - 60_001)), t = text(html);
    expect(html).toContain('<p class="paused" role="status">Prices paused</p>');
    expect(t).toContain("Paper pot $94,796.94");               // the engine's minute row, as with any stale prices
    expect(t).not.toMatch(AGE);
  });

  it("each holding: shares times the price now, its gain against the average cost, today against the previous close", () => {
    at(NOW);
    const html = home(marks());
    // biggest first by the booked close (XOM 3,212.40, TMO 2,769.96, BIIB 2,766.28), so rows do not swap as prices move
    expect([...html.matchAll(/class="hold-row".*?<b>([A-Z]+)<\/b>/gs)].map((m) => m[1])).toEqual(["XOM", "TMO", "BIIB"]);
    expect(rowOf(html, "XOM")).toBe("XOM Trend momentum · 0 d $3,173.60 −$38.20 −1.18% today");
    expect(rowOf(html, "TMO")).toMatch(/\$2,745\.44 −\$26\.36 −0\.92% today$/);
    // gains and losses keep their colours (and so their arrows, which the stylesheet adds to .gain and .loss in .num)
    expect(html).toMatch(/<span class="hold-end num">.*?<span class="tk loss">−\$38\.20<\/span><span class="day"><span class="tk loss">−1\.18%<\/span> today<\/span>/s);
  });

  it("the chart's last point follows the live pot; the earlier points stay the booked closes", () => {
    at(NOW);
    const html = home(marks());
    expect(html).toContain('aria-label="Pot $94,773.29 versus SPY');
    expect(text(html)).toContain("Pot $94,773 now");
    const d = /<path class="morph" d="(M[^"]+)" fill="none" stroke="var\(--s-pot\)"/.exec(html)![1];
    expect(d.split("L")).toHaveLength(4); // three closes and now
  });

  it("a holding that went up is green, with its sign", () => {
    at(NOW);
    const html = home(marks({ XOM: [20, 162.5, 160.58] }));
    expect(rowOf(html, "XOM")).toBe("XOM Trend momentum · 0 d $3,250.00 +$38.20 +1.20% today");
    expect(html).toMatch(/<span class="tk gain">\+\$38\.20<\/span><span class="day"><span class="tk gain">\+1\.20%<\/span> today<\/span>/);
  });
});

describe("Home's fallbacks: the engine's minute ledger for the pot, the close marks for the rows", () => {
  const engine = (html: string) => {
    const t = text(html);
    expect(t).toContain("Paper pot $94,796.94");
    expect(t).toContain("−$200.32 today");
    expect(t).not.toMatch(AGE);                                 // no line says where the figures come from or how old
    expect(html).not.toMatch(/class="(fresh|paused)/);         // not paused: the prices are not a minute old
    // the rows: every figure at the close mark, and no change today
    expect(rowOf(html, "XOM")).toBe("XOM Trend momentum · 0 d $3,212.40 +$0.60");
    expect(t).not.toMatch(/% today/);
    // nothing of the live pot (94,773.29) or of a live row is mixed in
    expect(t).not.toMatch(/94,773|3,173\.60/);
    expect(glitches(html)).toEqual([]);
  };
  it("the function is unavailable (a preview without keys, an error, not deployed)", () => { at(NOW); engine(home(null)); });
  it("the prices are stale: older than 30 seconds", () => { at(NOW); engine(home(marks({}, NOW - 30_001))); });
  it("the share counts differ: a fill landed today", () => { at(NOW); engine(home(marks({ XOM: [21, 158.68, 160.58] }))); });
  it("the account holds a stock the book does not", () => { at(NOW); engine(home(marks({ NVDA: [3, 190, 189] }))); });
  it("a trade today moved cash the close does not know", () => {
    at(NOW);
    engine(home(marks(), overview({ today: { trades: 1, pnl: -3.1 } })));
  });
  it("while the first prices are on their way the rows keep the place of their 'today' line, and show close marks", () => {
    at(NOW);
    const html = home(undefined);
    expect(text(html)).not.toMatch(AGE);
    expect(text(html)).not.toContain("Prices paused");           // nothing has arrived yet: nothing has paused
    expect(rowOf(html, "XOM")).toBe("XOM Trend momentum · 0 d $3,212.40 +$0.60");
    expect(html.match(/<span class="day" aria-hidden="true"> <\/span>/g)).toHaveLength(3);
  });
  it("the engine wrote nothing today either: the pot is the last close, and no line says so", () => {
    at(NOW);
    const html = home(null, overview({ ledger: null }));
    expect(text(html)).toContain("Paper pot $94,997.26");
    expect(text(html)).toContain("−$2.74 on Wed, Nov 18");
    expect(text(html)).not.toMatch(AGE);
  });
});

describe("outside market hours the booked close is the truth", () => {
  it("before the open: the close, named by its day, even with prices still in the cache", () => {
    at("2026-11-19T12:00:00Z"); // Thursday 07:00 in New York
    const fresh = marks({}, Date.now() - 2_000);
    const html = home(fresh, overview({ ledger: null, heartbeat: [] })), t = text(html);
    expect(t).toContain("Paper pot $94,997.26");
    expect(t).not.toMatch(AGE);
    expect(t).not.toMatch(/% today|Prices paused/);
    expect(rowOf(html, "XOM")).toBe("XOM Trend momentum · 0 d $3,212.40 +$0.60");
    expect(html).not.toContain('class="day"'); // nothing is on its way either: no place is kept
  });
  it("on the night of a close that is booked: today's close", () => {
    at("2026-11-18T22:35:00Z"); // Wednesday 17:35 in New York
    const t = text(home(marks({}, Date.now() - 2_000), overview({ ledger: { ts: "2026-11-18T20:59:21Z", cash: 95000, positions: {}, equity_venue: 95000, equity_shadow: 95000 } })));
    expect(t).toContain("Paper pot $94,997.26");
    expect(t).toContain("−$2.74 today");
    expect(t).not.toMatch(AGE);
  });
  it("a weekend: Friday's close", () => {
    at("2026-11-21T15:00:00Z");
    const friday = { ...BOOK, session: "2026-11-20" };
    const t = text(render(<Live />, { ctx: ctxFor(run, { overview: overview({ ledger: null, heartbeat: [] }) }),
      seeds: [...seeds(marks({}, Date.now() - 1_000)).filter((s) => String(s.key[0]) !== "book"), { key: ["book", 7], data: friday }] }));
    expect(t).not.toMatch(AGE);
  });
});

describe("Positions", () => {
  it("live: every open row at the price now, with today's change, and no line about the prices' age", () => {
    at(NOW);
    const html = positions(marks()), t = text(html);
    expect(t).toMatch(/Positions 7 open · \$15,101\.5\d invested · 16% of the pot/);
    expect(t).not.toMatch(AGE);
    expect(rowOf(html, "XOM", "row")).toBe("XOM Trend momentum · 20 shares · 0 days $3,173.60 −$38.20 −1.18% today");
    expect(rowOf(html, "ABBV", "row")).toMatch(/\$1,411\.85 −\$18\.95 −1\.27% today$/);
    expect([...html.matchAll(/class="row".*?<b>([A-Z]+)<\/b>/gs)].map((m) => m[1])).toEqual(["XOM", "TMO", "BIIB", "BBY", "PFE", "ABBV", "APA"]);
    expect(glitches(html)).toEqual([]);
  });
  it("fallback: the rows at their close marks, with no line about it; prices over a minute old are paused", () => {
    for (const m of [null, marks({}, NOW - 31_000), marks({ XOM: [19, 158.68, 160.58] })]) {
      at(NOW);
      const html = positions(m), t = text(html);
      expect(t).toMatch(/Positions 7 open · \$15,325\.4\d invested · 16% of the pot/);
      expect(t).not.toMatch(AGE);
      expect(t).not.toContain("Prices paused");
      expect(rowOf(html, "XOM", "row")).toBe("XOM Trend momentum · 20 shares · 0 days $3,212.40 +$0.60");
      expect(t).not.toMatch(/% today|3,173\.60/);
    }
    at(NOW);
    expect(positions(marks({}, NOW - 61_000))).toContain('<p class="paused" role="status">Prices paused</p>');
  });
  it("the detail sheet, live: the price now and today's change in dollars and percent", () => {
    at(NOW);
    const xom = BOOK.positions.find((p) => p.symbol === "XOM")!;
    const live = liveBook(BOOK, marks(), overview(), NOW);
    const html = render(<BookSheet o={{ symbol: "XOM", p: xom }} b={BOOK} exp={null} exits={{ trend_momentum: "target_list" }} live={markOf(live, "XOM")} />, { seeds: [{ key: ["stockSummary", "XOM"], data: null }] });
    const t = text(html);
    expect(t).toContain("$3,173.60 −$38.20 · −1.2%");
    expect(t).toContain("Price now $158.68");
    expect(t).toContain("Today −$38.00 · −1.18%");
    expect(t).toContain("Size, of the pot 3.3%"); // 3,173.60 of the live pot
    expect(t).toContain("Prices are live, from the paper account.");
    expect(t).not.toMatch(/Price at the close|at the last close/);
    expect(html).toMatch(/<span class="v num loss">/);
  });
  it("the detail sheet, not live: the close mark, as before", () => {
    at(NOW);
    const xom = BOOK.positions.find((p) => p.symbol === "XOM")!;
    const t = text(render(<BookSheet o={{ symbol: "XOM", p: xom }} b={BOOK} exp={null} exits={{ trend_momentum: "target_list" }} live={markOf(null, "XOM")} />,
      { seeds: [{ key: ["stockSummary", "XOM"], data: null }] }));
    expect(t).toContain("$3,212.40 +$0.60 · +0.0%");
    expect(t).toContain("Price at the close $160.62");
    expect(t).toContain("Prices are at the last close.");
    expect(t).not.toMatch(/Price now|Today/);
  });
});

describe("how it is drawn and asked for", () => {
  const rule = (sel: string) => new RegExp(`(?:^|\\n)${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
  it("'Prices paused' is small and quiet, in text tokens, with a pause sign (not colour alone); nothing animates", () => {
    expect(rule(".paused")).toMatch(/font-size: var\(--t-cap\); color: var\(--text-2\)/);
    expect(rule(".paused::before")).toMatch(/border-left: 2px solid var\(--text-3\); border-right: 2px solid var\(--text-3\)/);
    expect(rule(".day")).toMatch(/color: var\(--text-2\)/);
    for (const sel of [".paused", ".paused::before", ".day"]) expect(rule(sel), sel).not.toMatch(/animation|transition|infinite/);
    expect(css).not.toMatch(/(^|\n)\.fresh\b/);                // the old age line's rules are gone
  });
  it("numbers change through Tick; nothing counts seconds; the pause is said once (role=status), nothing else is live", () => {
    at(NOW);
    expect(home(marks())).not.toMatch(/role="status"|aria-live/);
    expect(marksSrc).toMatch(/<Tick value=\{r\} className=\{tone\(r\)\}>/);
    expect(marksSrc).not.toMatch(/aria-live|setInterval\([^)]*1000/);
    expect(marksSrc).toMatch(/<p className="paused" role="status">Prices paused<\/p>/);
    expect(liveSrc).not.toMatch(/FreshLine|ageText|Updated \{/);
  });
  it("the poll goes through lib/poll.ts, one try at a time, and the book keeps its 5-minute cache", () => {
    expect(marksSrc).toMatch(/marksPollMs\(run, overview\) !== false/);
    expect(marksSrc).toMatch(/enabled: open, retry: false, [^}]*\.\.\.poll\(open \? MARKS_MS : false\)/);
    expect(marksSrc).not.toMatch(/refetchInterval:/);
    expect(liveSrc).toMatch(/useMarks\(run, overview\)/);
    expect(bookHookSrc).toMatch(/queryKey: \["book", run\.id\][^\n]*staleTime: STALE\.daily, \.\.\.poll\(on \? 300_000 : false\)/);
  });
});
