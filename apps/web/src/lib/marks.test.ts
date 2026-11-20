// Live marks (stakeholder, 19 Nov 2026: "why the amounts on the dashboard are not updating in realtime"). Between two
// closes the page marks the booked book with the paper account's prices (api/marks.ts), when and only when the account
// holds exactly what the book says. Otherwise every number falls back to one source: the pot to the engine's minute
// ledger (potNow), the rows to their close marks. Pure maths: no network, no clock but the one passed in.
import { describe, expect, it } from "vitest";
import type { Overview } from "./api";
import { type Book, holdingView, parseBook, parseSeries } from "./book";
import { STALE_MS, freshText, liveBook, markOf, parseMarks, potLive, type Marks } from "./marks";
import { MARKS_MS, marksPollMs, poll } from "./poll";

// The book at Wednesday 18 Nov 2026's close (run 7, as kb_book answers), and the same seven holdings at the broker.
const SESSION = "2026-11-18";
const pos = (symbol: string, qty: number, avg: number, mark: number, rule = "trend_momentum") =>
  ({ rule, symbol, qty, avg_cost: avg, mark, last: mark, opened_session: SESSION, dividends: 0, stop: null });
const POSITIONS = [pos("ABBV", 5, 286.16, 285.92), pos("APA", 26, 45.54, 45.54), pos("BBY", 23, 100.32, 100.32), pos("BIIB", 11, 251.48, 251.48),
  pos("PFE", 59, 28.07, 28.065), pos("TMO", 4, 692.95, 692.49), pos("XOM", 20, 160.59, 160.62)];
const raw = (positions: unknown[] = POSITIONS, over: Record<string, unknown> = {}) => ({
  run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 79671.79, invested: 15325.48, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions, exposure: { gross: 0.161325, overnight: 0.161325, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 }, ...over });
const BOOK = parseBook(raw(), 7);
const SERIES = parseSeries([{ session: "2026-11-16", close_equity: 95000, marked: true }, { session: "2026-11-17", close_equity: 95000, marked: true },
  { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = { amount_usd: 95000 };

// Thursday 19 Nov 2026, 12:40 in New York; the prices were read 4 seconds before.
const NOW = Date.parse("2026-11-19T17:40:04Z"), AT = NOW - 4_000;
const PRICES: Record<string, [number, number, number]> = { // qty, price now, previous close at the broker
  ABBV: [5, 282.37, 286.01], APA: [26, 44.65, 45.52], BBY: [23, 98.72, 100.31], BIIB: [11, 246.62, 251.41],
  PFE: [59, 27.565, 28.06], TMO: [4, 686.36, 692.74], XOM: [20, 158.68, 160.58] };
const marks = (over: Record<string, [number, number, number | null] | null> = {}, at = AT): Marks => ({ at,
  by: Object.fromEntries(Object.entries({ ...PRICES, ...over }).filter(([, v]) => v).map(([s, v]) => [s, { qty: v![0], price: v![1], prevClose: v![2] }])) });
// What the seven moves add up to: 5(−3.55) + 26(−0.89) + 23(−1.60) + 11(−4.86) + 59(−0.50) + 4(−6.13) + 20(−1.94)
const MOVE = -223.97;
const ledger = (ts: string, equity = 94796.94) => ({ ts, cash: equity, positions: {}, equity_venue: equity, equity_shadow: equity });
const ov = (o: Partial<Overview> = {}) => ({ daily: [], ledger: ledger("2026-11-19T17:39:21Z"), today: { trades: 0, pnl: 0 }, ...o }) as Pick<Overview, "daily" | "ledger" | "today">;

describe("the live pot", () => {
  it("is the last close plus each holding's shares times (price now less the close mark); today is it less the last close", () => {
    const l = liveBook(BOOK, marks(), ov(), NOW)!;
    expect(l.pot).toBeCloseTo(94997.26 + MOVE, 6);
    expect(l.pot).toBeCloseTo(94773.29, 6);
    expect(l.dayPnl).toBeCloseTo(MOVE, 6);
    expect(l.dayPct).toBeCloseTo((MOVE / 94997.26) * 100, 9);
    expect(l.at).toBe(AT);
  });

  it("the pot on the page: live figures, labelled today, from one source", () => {
    const p = potLive(run, ov(), BOOK, SERIES, marks(), new Date(NOW));
    expect(p.pot).toBeCloseTo(94773.29, 6);
    expect(p.dayPnl).toBeCloseTo(MOVE, 6);
    expect(p).toMatchObject({ dayLabel: "today", today: true, source: "book", positions: {}, fresh: { kind: "live", at: AT } });
    expect(p.live).not.toBeNull();
    // never the engine's minute figure mixed in: the ledger says 94,796.94 and none of it is in the live pot
    expect(p.pot - p.dayPnl).toBeCloseTo(94997.26, 6);
  });

  it("a stock held by two rules is one holding at the broker: the shares are added before they are compared", () => {
    const two = parseBook(raw([pos("XOM", 12, 160.59, 160.62), pos("XOM", 8, 150, 160.62, "mom_12_1_band"), pos("ABBV", 5, 286.16, 285.92)]), 7);
    const l = liveBook(two, marks({ APA: null, BBY: null, BIIB: null, PFE: null, TMO: null }), ov(), NOW)!;
    expect(l.dayPnl).toBeCloseTo(20 * (158.68 - 160.62) + 5 * (282.37 - 285.92), 6);
    expect(liveBook(two, marks({ APA: null, BBY: null, BIIB: null, PFE: null, TMO: null, XOM: [12, 158.68, 160.58] }), ov(), NOW)).toBeNull();
  });

  it("fractional shares compare exactly enough", () => {
    const frac = parseBook(raw([pos("XOM", 0.1 + 0.2, 160.59, 160.62)]), 7);
    expect(liveBook(frac, { at: AT, by: { XOM: { qty: 0.3, price: 158.68, prevClose: 160.58 } } }, ov(), NOW)).not.toBeNull();
    expect(liveBook(frac, { at: AT, by: { XOM: { qty: 0.31, price: 158.68, prevClose: 160.58 } } }, ov(), NOW)).toBeNull();
  });
});

describe("each fallback: the engine's minute ledger for the pot, the close marks for the rows", () => {
  const fallback = (m: Marks | null, o = ov(), book: Book = BOOK, now = NOW) => {
    expect(liveBook(book, m, o, now)).toBeNull();
    return potLive(run, o, book, SERIES, m, new Date(now));
  };
  const engine = { pot: 94796.94, dayLabel: "today", today: true, live: null, fresh: { kind: "engine", at: Date.parse("2026-11-19T17:39:21Z") } };

  it("the function is unavailable (no keys, an error, not the owner)", () => {
    const p = fallback(null);
    expect(p).toMatchObject(engine);
    expect(p.dayPnl).toBeCloseTo(94796.94 - 94997.26, 6); // what potNow does today
  });

  it("the prices are stale: older than 30 seconds", () => {
    expect(STALE_MS).toBe(30_000);
    expect(liveBook(BOOK, marks({}, NOW - 30_000), ov(), NOW)).not.toBeNull();
    expect(fallback(marks({}, NOW - 30_001))).toMatchObject(engine);
    expect(fallback(marks({}, NaN))).toMatchObject(engine);
  });

  it("the share counts differ: a fill landed today (15:50 to 15:57 ET), or anything unexplained", () => {
    expect(fallback(marks({ XOM: [21, 158.68, 160.58] }))).toMatchObject(engine);        // bought one more
    expect(fallback(marks({ XOM: null }))).toMatchObject(engine);                        // sold out of a holding
    expect(fallback(marks({ NVDA: [3, 190, 189] }))).toMatchObject(engine);              // a new holding
    expect(fallback(marks({ XOM: null, XON: [20, 158.68, 160.58] }))).toMatchObject(engine); // another stock, same count
    expect(fallback(marks({ XOM: [-20, 158.68, 160.58] }))).toMatchObject(engine);       // a short position
  });

  it("a trade today moved cash the close does not know: the same shares can hide a round trip", () => {
    expect(fallback(marks(), ov({ today: { trades: 2, pnl: -14.2 } }))).toMatchObject(engine);
  });

  it("a day position that filled is a stock the book does not have; what the minute row lists does not decide", () => {
    const nvda = { qty: 3, fill: 190, stop: 185, target: 200, status: "open", last: 191, trade_id: 9 };
    const day = { ...ledger("2026-11-19T17:39:21Z"), positions: { NVDA: nvda } };
    // filled: the account holds NVDA too, and the engine counted a trade today
    expect(fallback(marks({ NVDA: [3, 191, 189] }), ov({ ledger: day, today: { trades: 1, pnl: 0 } }))).toMatchObject({ pot: 94796.94, live: null, positions: day.positions });
    // issue #384: the engine will list the multi-day holdings in its minute row. The account still holds exactly the
    // book, so the page stays live, and the row's list is not shown a second time next to the book's.
    const xom = { qty: 20, fill: 160.59, stop: 0, target: 0, status: "open", last: 158.7, trade_id: 0 };
    const later = potLive(run, ov({ ledger: { ...ledger("2026-11-19T17:39:21Z"), positions: { XOM: xom } } }), BOOK, SERIES, marks(), new Date(NOW));
    expect(later.live).not.toBeNull();
    expect(later).toMatchObject({ positions: {}, fresh: { kind: "live" } });
  });

  it("a price that is not a price", () => {
    expect(fallback(marks({ XOM: [20, 0, 160.58] }))).toMatchObject(engine);
    expect(fallback(marks({ XOM: [20, NaN, 160.58] }))).toMatchObject(engine);
  });

  it("nothing is held: there is no price to be live about", () => {
    const empty = parseBook(raw([]), 7);
    expect(liveBook(empty, { at: AT, by: {} }, ov(), NOW)).toBeNull();
  });

  it("no close is booked yet, or today's close already is: the booked figures are the truth", () => {
    expect(liveBook(parseBook({ run_id: 7, session: null, mtm: null, positions: [] }, 7), marks(), ov(), NOW)).toBeNull();
    expect(liveBook(null, marks(), ov(), NOW)).toBeNull();
    const tonight = parseBook(raw(POSITIONS, { session: "2026-11-19" }), 7);
    expect(liveBook(tonight, marks(), ov(), NOW)).toBeNull();
  });

  it("with no minute row from today either, the pot is the last close and says so", () => {
    const p = fallback(null, ov({ ledger: ledger("2026-11-18T20:59:21Z", 95000) }));
    expect(p.pot).toBeCloseTo(94997.26, 6);
    expect(p).toMatchObject({ dayLabel: "on Wed, Nov 18", today: false, fresh: { kind: "close", session: SESSION } });
  });

  it("the rows never mix sources: close marks in a fallback, the broker's price and the live pot when live", () => {
    const xom = BOOK.positions.find((p) => p.symbol === "XOM")!;
    const atClose = holdingView(xom, BOOK, markOf(null, "XOM"));
    expect(atClose).toMatchObject({ price: 160.62, day: null, dayPct: null });
    expect(atClose.value).toBeCloseTo(3212.4, 6);
    expect(atClose.gain).toBeCloseTo(0.6, 6);
    expect(atClose.share).toBeCloseTo(3212.4 / 94997.26, 9);
    const live = liveBook(BOOK, marks(), ov(), NOW);
    const now = holdingView(xom, BOOK, markOf(live, "XOM"));
    expect(now.price).toBe(158.68);
    expect(now.value).toBeCloseTo(20 * 158.68, 6);                  // shares times the price now
    expect(now.gain).toBeCloseTo(20 * (158.68 - 160.59), 6);        // against the average cost
    expect(now.gainPct).toBeCloseTo(((158.68 - 160.59) / 160.59) * 100, 9);
    expect(now.day).toBeCloseTo(20 * (158.68 - 160.58), 6);         // today, against the previous close
    expect(now.dayPct).toBeCloseTo(((158.68 - 160.58) / 160.58) * 100, 9);
    expect(now.share).toBeCloseTo((20 * 158.68) / 94773.29, 9);     // of the live pot, not of the close
    expect(markOf(live, "NVDA")).toBeNull();
  });

  it("a holding with no previous close at the broker has a value and a gain, and no change today", () => {
    const live = liveBook(BOOK, marks({ XOM: [20, 158.68, null] }), ov(), NOW);
    const v = holdingView(BOOK.positions.find((p) => p.symbol === "XOM")!, BOOK, markOf(live, "XOM"));
    expect(v.value).toBeCloseTo(3173.6, 6);
    expect(v).toMatchObject({ day: null, dayPct: null });
  });
});

describe("how fresh the pot is: three states", () => {
  it("live: seconds since the prices were read", () => {
    expect(freshText({ kind: "live", at: AT }, NOW)).toBe("Live · prices 4 s ago");
    expect(freshText({ kind: "live", at: NOW }, NOW)).toBe("Live · prices 1 s ago");
    expect(freshText({ kind: "live", at: NOW + 900 }, NOW)).toBe("Live · prices 1 s ago"); // never a negative age
    expect(freshText({ kind: "live", at: NOW - 29_600 }, NOW)).toBe("Live · prices 30 s ago");
  });
  it("the fallback: when the engine wrote its minute row", () => {
    const t = (s: number) => freshText({ kind: "engine", at: NOW - s * 1000 }, NOW);
    expect(t(5)).toBe("Updated a minute ago by the engine");
    expect(t(62)).toBe("Updated a minute ago by the engine");
    expect(t(170)).toBe("Updated 3 minutes ago by the engine");
    expect(t(59 * 60)).toBe("Updated 59 minutes ago by the engine");
    expect(t(61 * 60)).toBe("Updated an hour ago by the engine");
    expect(t(3 * 3600)).toBe("Updated 3 hours ago by the engine");
  });
  it("outside market hours: the day of the booked close", () => {
    expect(freshText({ kind: "close", session: "2026-11-18" }, Date.parse("2026-11-19T12:00:00Z"))).toBe("At Wednesday's close");
    expect(freshText({ kind: "close", session: "2026-11-20" }, Date.parse("2026-11-23T12:00:00Z"))).toBe("At Friday's close"); // over the weekend
    expect(freshText({ kind: "close", session: "2026-11-19" }, Date.parse("2026-11-19T22:30:00Z"))).toBe("At today's close");
    expect(freshText({ kind: "close", session: "2026-11-10" }, Date.parse("2026-11-19T22:30:00Z"))).toBe("At the close of Nov 10"); // a weekday alone would mislead
  });
  it("the pot's state follows what is shown: live, the engine's minute row, or the close", () => {
    expect(potLive(run, ov(), BOOK, SERIES, marks(), new Date(NOW)).fresh).toEqual({ kind: "live", at: AT, feed: "poll" });
    expect(potLive(run, ov(), BOOK, SERIES, null, new Date(NOW)).fresh).toEqual({ kind: "engine", at: Date.parse("2026-11-19T17:39:21Z") });
    // Wednesday night, the close booked: the minute row of that day is not used (potNow), and no price is asked for
    const night = new Date("2026-11-18T22:35:00Z");
    expect(potLive(run, ov({ ledger: ledger("2026-11-18T20:59:21Z", 95000) }), BOOK, SERIES, null, night).fresh).toEqual({ kind: "close", session: SESSION });
    // a new run with no close and no minute row: nothing to say
    expect(potLive(run, ov({ ledger: null }), parseBook({ run_id: 7, session: null, mtm: null, positions: [] }, 7), [], null, night).fresh).toBeNull();
  });
});

describe("the function's answer, as the page reads it", () => {
  const body = { available: true, as_of: "2026-11-19T17:40:00.000Z", positions: [{ symbol: "XOM", qty: 20, price: 158.68, prev_close: 160.58 },
    { symbol: "NBIS", qty: 3, price: 101.5, prev_close: null }] };
  it("keeps the prices and when they were read", () => {
    expect(parseMarks(body, NOW)).toEqual({ at: AT, by: { XOM: { qty: 20, price: 158.68, prevClose: 160.58 }, NBIS: { qty: 3, price: 101.5, prevClose: null } } });
  });
  it("measures their age on the server's clock, so a device whose clock is wrong still tells live from stale", () => {
    // the server sent the answer at 17:40:04 (its Date header): 4 seconds old. This device believes it is 5 minutes later.
    const here = NOW + 300_000;
    expect(parseMarks(body, here, "Thu, 19 Nov 2026 17:40:04 GMT")!.at).toBe(here - 4_000);
    expect(parseMarks(body, here, "Thu, 19 Nov 2026 17:39:59 GMT")!.at).toBe(here); // never from the future
    expect(parseMarks(body, here, "not a date")!.at).toBe(AT);                      // no usable header: as_of as it is
  });
  it("unavailable, or anything it does not understand, is no marks at all", () => {
    for (const bad of [null, undefined, "x", [], { available: false, as_of: null, positions: [] }, { available: true, as_of: "soon", positions: [] },
      { available: true, as_of: body.as_of }, { available: true, as_of: body.as_of, positions: [{ symbol: "XOM", qty: "20", price: 158.68 }] },
      { available: true, as_of: body.as_of, positions: [{ symbol: "XOM", qty: 20, price: 158.68 }, { symbol: "XOM", qty: 1, price: 158.68 }] },
      { error: "not authorised" }])
      expect(parseMarks(bad, NOW), JSON.stringify(bad)).toBeNull();
  });
});

describe("when the page asks for marks", () => {
  const paper = { kind: "paper" as const, status: "running" };
  const beat = (state: string, next_open: string, ts: string) => ({ heartbeat: [{ process: "engine", ts, detail: { state, next_open } }] }) as never;
  it("every 10 seconds while the market is open, through poll(): never in a hidden tab", () => {
    expect(MARKS_MS).toBe(10_000);
    expect(marksPollMs(paper, undefined, new Date("2026-11-19T17:40:00Z"))).toBe(10_000);
    expect(marksPollMs({ ...paper, status: "paused" }, undefined, new Date("2026-11-19T14:30:00Z"))).toBe(10_000); // 09:30 ET
    expect(poll(MARKS_MS)).toEqual({ refetchInterval: 10_000, refetchIntervalInBackground: false });
  });
  it("market closed: no call. The booked close is the truth then", () => {
    for (const closed of ["2026-11-19T14:29:59Z", "2026-11-19T21:00:00Z", "2026-11-19T22:30:00Z", "2026-11-21T17:40:00Z", "2026-11-22T17:40:00Z"])
      expect(marksPollMs(paper, undefined, new Date(closed)), closed).toBe(false);
    // Thanksgiving: the engine's last heartbeat said the market is closed until Friday
    expect(marksPollMs(paper, beat("market_closed", "2026-11-27T09:30:00-05:00", "2026-11-25T21:20:00Z"), new Date("2026-11-26T15:00:00Z"))).toBe(false);
  });
  it("only for a paper run that is trading: not a replay, not a run that ended or has not started", () => {
    const open = new Date("2026-11-19T17:40:00Z");
    expect(marksPollMs({ kind: "replay", status: "ended" }, undefined, open)).toBe(false);
    for (const status of ["scheduled", "ended", "halted", "stopped"]) expect(marksPollMs({ kind: "paper", status }, undefined, open), status).toBe(false);
    expect(marksPollMs(undefined, undefined, open)).toBe(false);
  });
});
