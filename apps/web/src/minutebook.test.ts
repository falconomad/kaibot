// #384: the engine's minute row (kb_overview `ledger`) carries the multi-day book (`multi_day`): real cash and each
// holding's shares and mark. Contract (issue #384, lib/marks.ts "the engine's minute row"):
// - the pot stays the row's equity; the book's shares stay kb_book's; the row's marks only price the book's rows;
// - day positions come only from `ledger.positions`: a row that carries both never shows a multi-day stock twice;
// - the row's marks are used only when the account's live prices are not, the row is today's, nothing is settling and
//   it lists exactly the book. Pure maths: no network, no clock but the one passed in.
import { describe, expect, it, vi } from "vitest";
import type { Overview } from "./lib/api";
import { holdingView, parseBook } from "./lib/book";
import { engineMarks, markOf, parseMinuteBook, potLive } from "./lib/marks";
import { holdingRows } from "./components/Home";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const SESSION = "2026-11-18";
const pos = (symbol: string, qty: number, avg: number, mark: number, rule = "trend_momentum") =>
  ({ rule, symbol, qty, avg_cost: avg, mark, last: mark, opened_session: SESSION, dividends: 0, stop: null });
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", cash: 79671.79, invested: 15325.48, equity: 94997.26, equity_reported: 94997.26, start_equity: 95000, prev_close_equity: 95000 },
  positions: [pos("ABBV", 5, 286.16, 285.92), pos("XOM", 20, 160.59, 160.62)], exposure: { by_rule: [] } }, 7);
const run = { amount_usd: 95000 };
const NOW = Date.parse("2026-11-19T19:56:00Z"); // Thursday 19 Nov, 14:56 ET
const md = (over: Record<string, unknown> = {}, positions?: unknown[]) => ({ v: 1, cash_ledger: 91368.89, invested: 4588.40, settling: [],
  positions: positions ?? [
    { rule: "trend_momentum", symbol: "ABBV", qty: 5, avg_cost: 286.16, mark: 282.0, mark_ts: "2026-11-19T19:55:00Z", mark_from: "minute_bar", value: 1410.0 },
    { rule: "trend_momentum", symbol: "XOM", qty: 20, avg_cost: 160.59, mark: 158.92, mark_ts: "2026-11-19T19:55:00Z", mark_from: "minute_bar", value: 3178.4 }], ...over });
const EQ = 91368.89 + 1410.0 + 3178.4; // cash + invested: the row's equity
const ledger = (multi_day: unknown, positions: Record<string, unknown> = {}, ts = "2026-11-19T19:55:07Z") =>
  ({ ts, cash: 91368.89, positions, equity_venue: EQ, equity_shadow: EQ, multi_day });
const ov = (l: unknown) => ({ daily: [], ledger: l, today: { trades: 0, pnl: 0 } }) as unknown as Pick<Overview, "daily" | "ledger" | "today">;

describe("the minute row's multi-day block", () => {
  it("prices the book's rows at the row's marks; the pot is the row's equity; one source for both", () => {
    const p = potLive(run, ov(ledger(md())), BOOK, undefined, null, new Date(NOW));
    expect(p.live).toBeNull();
    expect(p.engine).not.toBeNull();
    expect(p.pot).toBeCloseTo(EQ, 6);
    expect(p.engine!.pot).toBe(p.pot);
    expect(p.fresh).toEqual({ kind: "engine", at: Date.parse("2026-11-19T19:55:07Z") });
    const abbv = holdingView(BOOK.positions[0], BOOK, markOf(p.engine, "ABBV"));
    expect(abbv.price).toBe(282.0);
    expect(abbv.value).toBeCloseTo(1410.0, 6);
    expect(abbv.dayPct).toBeCloseTo((282.0 / 285.92 - 1) * 100, 9); // against the close mark, as the pot's today
    // cash + the row's holdings = the pot, to the cent
    const rows = BOOK.positions.map((x) => holdingView(x, BOOK, markOf(p.engine, x.symbol)).value);
    expect(Math.round(100 * (91368.89 + rows.reduce((a, v) => a + v, 0)))).toBe(Math.round(100 * p.pot));
  });

  it("a row that carries day positions and the multi-day block: each stock shows once", () => {
    const day = { NVDA: { qty: 10, fill: 200, stop: 190, target: 220, status: "open", last: 207, trade_id: 9 } };
    const p = potLive(run, ov(ledger(md(), day)), BOOK, undefined, null, new Date(NOW));
    expect(Object.keys(p.positions)).toEqual(["NVDA"]); // day positions are `ledger.positions` only
    const rows = holdingRows(p.positions, BOOK, p.engine);
    expect(rows.map((r) => r.symbol).sort()).toEqual(["ABBV", "NVDA", "XOM"]);
    expect(rows.filter((r) => r.book).map((r) => r.symbol).sort()).toEqual(["ABBV", "XOM"]);
  });

  it("the account's live prices come first; the row's marks are only the fallback", () => {
    const marks = { at: NOW - 3000, by: { ABBV: { qty: 5, price: 283.1, prevClose: 286 }, XOM: { qty: 20, price: 159, prevClose: 160.6 } } };
    const p = potLive(run, ov(ledger(md())), BOOK, undefined, marks, new Date(NOW));
    expect(p.live).not.toBeNull();
    expect(p.engine).toBeNull();
  });

  it("falls back to the close marks: orders settling, other shares, another stock, a row from yesterday, no block", () => {
    const at = (l: unknown) => potLive(run, ov(l), BOOK, undefined, null, new Date(NOW)).engine;
    expect(at(ledger(md({ settling: ["XOM"] })))).toBeNull(); // 15:50-15:57 ET: fills landing
    expect(at(ledger(md({}, [{ rule: "trend_momentum", symbol: "ABBV", qty: 5, mark: 282 }, { rule: "trend_momentum", symbol: "XOM", qty: 21, mark: 158.9 }])))).toBeNull();
    expect(at(ledger(md({}, [{ rule: "trend_momentum", symbol: "ABBV", qty: 5, mark: 282 }, { rule: "trend_momentum", symbol: "PFE", qty: 20, mark: 28 }])))).toBeNull();
    expect(at(ledger(md({}, [{ rule: "trend_momentum", symbol: "ABBV", qty: 5, mark: 282 }])))).toBeNull();
    expect(at(ledger(md(), {}, "2026-11-18T20:59:00Z"))).toBeNull();
    expect(at(ledger(undefined))).toBeNull(); // before migration 0034, and old rows
    expect(at(ledger(md({ v: 2 })))).toBeNull();
    expect(at(ledger(md({}, [{ rule: "trend_momentum", symbol: "ABBV", qty: 5, mark: 0 }, { rule: "trend_momentum", symbol: "XOM", qty: 20, mark: 158.9 }])))).toBeNull();
    // and then the rows are at the close marks, never at the row's
    const p = potLive(run, ov(ledger(md({ settling: ["XOM"] }))), BOOK, undefined, null, new Date(NOW));
    expect(holdingView(BOOK.positions[1], BOOK, markOf(p.engine, "XOM")).price).toBe(160.62);
  });

  it("after today's close is booked the booked close is the truth", () => {
    const today = parseBook({ run_id: 7, session: "2026-11-19", mtm: { equity: 95000, equity_reported: 95000 }, positions: [pos("ABBV", 5, 286.16, 285.92), pos("XOM", 20, 160.59, 160.62)] }, 7);
    expect(engineMarks(today, ov(ledger(md())), NOW)).toBeNull();
  });

  it("reads only what the engine writes", () => {
    expect(parseMinuteBook(null)).toBeNull();
    expect(parseMinuteBook({ v: 1, positions: [] })).toBeNull(); // no settling list
    expect(parseMinuteBook(md())!.positions.map((p) => p.symbol)).toEqual(["ABBV", "XOM"]);
  });
});
