import { describe, expect, it } from "vitest";
import { RAW_BOOK, RAW_EMPTY_BOOK } from "../pages/testkit-book";
import { bookChecks, bookTotals, byRule, daysHeld, holdingView, mtmCurve, nextDecision, parseBook, parseSeries } from "./book";

describe("parseBook (kb_book, 0018)", () => {
  const b = parseBook(RAW_BOOK);
  it("reads the RPC's shape and adds up: holdings = invested, invested + cash = pot", () => {
    expect(b.positions).toHaveLength(4);
    const t = bookTotals(b);
    expect(t.invested).toBeCloseTo(RAW_BOOK.mtm.invested, 6);
    expect(t.equity).toBeCloseTo(RAW_BOOK.mtm.equity, 6);
    expect(t).toMatchObject({ up: 2, down: 2 });
  });
  it("an unpriced position keeps its mark and says so; a missing stop is null", () => {
    const z = b.positions.find((p) => p.symbol === "ZZZQ")!;
    expect(z).toMatchObject({ mark: 98, priced: false, stop: null });
  });
  it("no ledger row yet: empty, never zeros", () => {
    const e = parseBook(RAW_EMPTY_BOOK);
    expect(e).toMatchObject({ session: null, equity: null, gross: null, positions: [], reconciled: null });
  });
  it("junk in, nothing out", () => {
    expect(parseBook(null).positions).toEqual([]);
    expect(parseBook({ positions: [{ symbol: "X" }, 3, null], exposure: { by_rule: "x" } })).toMatchObject({ positions: [], byRule: [] });
  });
  it("a broker difference is listed by symbol", () => {
    const r = parseBook({ ...RAW_BOOK, reconciled: { ok: false, ts: "t", positions: { GM: { ledger: 3, broker: 2 }, AAPL: { ledger: 4, broker: 4 } }, cash_diff: 0 } });
    expect(r.reconciled).toEqual({ ok: false, ts: "t", cashDiff: 0, diffs: [{ symbol: "GM", ledger: 3, broker: 2 }] });
  });
});

describe("one holding and the book's checks", () => {
  const b = parseBook(RAW_BOOK);
  it("value, gain with dividends, share of the pot, days held, stop distance", () => {
    const v = holdingView(b.positions[0], b);
    expect(v.value).toBeCloseTo(952.4);
    expect(v.gain).toBeCloseTo(4 * (238.1 - 231.4) + 1);
    expect(v.share).toBeCloseTo(952.4 / 4501.7);
    expect(v.days).toBe(51);
    expect(v.stopBelowPct).toBeCloseTo(((238.1 - 196.7) / 238.1) * 100);
  });
  it("days held needs both dates", () => {
    expect(daysHeld(null, "2027-01-01")).toBeNull();
    expect(daysHeld("2027-01-02", "2027-01-01")).toBeNull();
  });
  it("stops: in place, missing, being placed again", () =>
    expect(bookChecks(b)).toMatchObject({ n: 4, resting: 2, missing: ["ZZZQ"], rearming: ["COST"] }));
  it("groups by rule, biggest first", () => {
    const g = byRule(b);
    expect(g.map((x) => x.rule)).toEqual(["trend_momentum", "mom_12_1_band"]);
    expect(g[0].positions.map((p) => p.symbol)).toEqual(["AAPL", "MSFT", "ZZZQ"]);
  });
});

describe("the pot line and the next decision", () => {
  it("marked pot next to SPY from the same start", () => {
    const s = parseSeries([{ session: "2027-01-04", close_equity: 5010, marked: true }, { session: "2027-01-05", close_equity: "4990.5", marked: true }, { bad: 1 }]);
    expect(s).toHaveLength(2);
    const c = mtmCurve(5000, s, [{ session: "2027-01-04", spy_ret: 0.01 }, { session: "2027-01-05", spy_ret: null }]);
    expect(c).toEqual([{ session: "2027-01-04", pot: 5010, spy: 5050 }, { session: "2027-01-05", pot: 4990.5, spy: 5050 }]);
  });
  it("decides after today's close in a session, after the next session's close when closed, tonight if still to come", () => {
    expect(nextDecision("trading", "2026-11-18", null, "2026-11-17", "2026-11-17")).toBe("2026-11-18");
    expect(nextDecision("market_closed", "2026-11-18", "2026-11-19T09:30:00-05:00", "2026-11-18", "2026-11-18")).toBe("2026-11-19");
    expect(nextDecision("market_closed", "2026-11-18", "2026-11-19T09:30:00-05:00", "2026-11-17", "2026-11-18")).toBe("2026-11-18");
    expect(nextDecision("market_closed", "2026-11-18", null, null, null)).toBeNull();
  });
});
