// #359: the Positions page's states (loading, empty, error) and, with data, a held position with its exit kind, a
// planned at-the-close order and an order not filled at the close. Testkit only (no network, no DOM library).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Health as HealthResp, Heartbeat } from "../lib/api";
import { parseBook } from "../lib/book";
import { BookSheet, Positions } from "./Positions";
import { boom, ctxFor, emptyOverview, glitches, hasAlert, hasStatus, paperRun, render, replayRun, text, zeros } from "./testkit";
import { RAW_EMPTY_BOOK } from "./testkit-book";

vi.mock("../lib/api", () => import("./testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun({ id: 7, amount_usd: 95000 });
const SESSION = "2026-11-19";
const pos = (symbol: string, over: Record<string, unknown> = {}) =>
  ({ rule: "trend_momentum", symbol, qty: 4, avg_cost: 100, mark: 101, last: 101, opened_session: "2026-11-10", dividends: 0, stop: null, ...over });
const RAW = (positions: unknown[]) => ({ run_id: 7, session: SESSION, mtm: { equity: 95000, equity_reported: 95000 }, positions,
  exposure: { gross: 0.1, overnight: 0.1, by_rule: [] }, reconciled: { ok: true, ts: "2026-11-19T21:06:00Z", positions: {}, cash_diff: 0 } });
const health = (events: unknown[]): HealthResp => ({ now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], events, pending_commands: [],
  deadman: { armed: true, jobs: [], runs: [] } } as unknown as HealthResp);
const beat = (multi_day: unknown): Heartbeat => ({ process: "engine", ts: "2026-11-19T22:10:00Z", detail: { state: "market_closed", multi_day } as never });

// a momentum holding the engine marks as having no stop by design, and a holding whose rule rests a stop at the broker
const BOOK = parseBook(RAW([pos("ABBV", { stop_state: "none_by_design", exit: "target_list" }),
  pos("NVDA", { rule: "orb", qty: 2, stop: { price: 90, state: "resting" }, stop_state: "resting", exit: "broker_stop" })]), 7);
const PLAN = { rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-18", fills_at_close_of: SESSION, sell: [], buy_candidates: ["AAPL"],
  skipped: [], note: null, orders: [
    { symbol: "AAPL", shares_now: 0, shares_target: 5, action: "buy", why: "rank", coid: "c1", state: "planned" },
    { symbol: "MPC", shares_now: 0, shares_target: 10, action: "buy", why: "top_rank", coid: "c2", state: "canceled" }] }] };
const UNFILLED = { type: "multi_day_unfilled", ts: "2026-11-19T21:01:00Z", run_id: 7, payload: { strategy: "trend_momentum", symbol: "MPC", side: "buy",
  qty: 10, filled: 6, left: 4, coid: "c2", session: SESSION, status: "expired", purpose: "entry", type: "market", tif: "day" } };
const ov = { ...emptyOverview(run), heartbeat: [beat({ rules: {}, exits: { trend_momentum: "target_list", orb: "broker_stop" } })] };

describe("Positions page states", () => {
  beforeEach(() => { vi.useFakeTimers({ now: new Date("2026-11-19T22:00:00Z"), toFake: ["Date"] }); });
  afterEach(() => { vi.useRealTimers(); });

  it("loading: a skeleton, and no count that reads as data", () => {
    const html = render(<Positions />, { ctx: ctxFor(run) });
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
    expect(text(html)).not.toContain("open ·");
    expect(text(html)).not.toContain("Nothing held");
  });

  it("empty: no positions and no planned orders say so, with what to expect", () => {
    const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["book", 7], data: parseBook(RAW_EMPTY_BOOK, 7) },
      { key: ["md-plan", 7], data: null }, { key: ["health"], data: health([]) }] });
    const t = text(html);
    expect(t).toContain("Positions 0 open");
    expect(t).toContain("Nothing held right now. Day trades are sold by 15:55");
    expect(html).not.toMatch(/next-close|book-strip/);
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("error: the book's error with a retry, and never 'nothing held' or '0 open' under it", () => {
    const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["book", 7], error: boom() }, { key: ["md-plan", 7], error: boom() },
      { key: ["health"], error: boom() }] });
    const t = text(html);
    expect(hasAlert(html)).toBe(true);
    expect(t).toContain("The multi-day book couldn't load.");
    expect(t).toContain("permission denied for function");
    expect(t).toContain("Try again");
    expect(t).not.toContain("Nothing held");
    expect(zeros(html)).toEqual([]);
    expect(glitches(html)).toEqual([]);
  });

  it("with data: held positions with their exit kind, a planned order and an unfilled order at the close", () => {
    const html = render(<Positions />, { ctx: ctxFor(run, { overview: ov }), seeds: [{ key: ["book", 7], data: BOOK },
      { key: ["md-plan", 7], data: PLAN }, { key: ["health"], data: health([UNFILLED]) }] });
    const t = text(html);
    expect(t).toContain("Positions 2 open");
    expect(html).toMatch(/<b>ABBV<\/b>/);
    expect(html).toMatch(/<b>NVDA<\/b>/);
    // the exit kind: one stop needed (NVDA's, in place), ABBV sold by its rule's list
    expect(t).toContain("Safety stops 1 of 1 in place · 1 sold by the rule's list instead");
    expect(t).toContain("At the close of Nov 19, 2026");
    expect(t).toMatch(/AAPL Buy 5 shares .*planned/);
    expect(t).toContain("MPC 4 shares not filled · expired at the close not filled");
    expect(glitches(html)).toEqual([]);
  });

  it("the holding's sheet: no stop by design is not a missing stop", () => {
    const abbv = BOOK.positions.find((p) => p.symbol === "ABBV")!;
    const o = { kind: "book" as const, symbol: "ABBV", p: abbv, value: 404, gain: 4 };
    const t = text(render(<BookSheet o={o} b={BOOK} exp={null} exits={{ trend_momentum: "target_list" }} />));
    expect(t).toContain("Safety stop at the broker none by design: the rule sells by its list at a close");
    expect(t).not.toContain("places it again");
    // a rule that needs a stop and has none still says it is placed again
    const lost = { ...abbv, rule: "orb", stopState: "missing", exit: "broker_stop" };
    const t2 = text(render(<BookSheet o={{ ...o, p: lost }} b={BOOK} exp={null} exits={{}} />));
    expect(t2).toContain("none: kaibo places it again");
  });

  it("a replay: its trades load, are empty, or fail with a retry", () => {
    const r = replayRun();
    expect(hasStatus(render(<Positions />, { ctx: ctxFor(r) }))).toBe(true);
    const empty = text(render(<Positions />, { ctx: ctxFor(r), seeds: [{ key: ["trades", r.id, 0], data: { total: 0, rows: [] } }] }));
    expect(empty).toContain("No trades in this run yet.");
    const err = render(<Positions />, { ctx: ctxFor(r), seeds: [{ key: ["trades", r.id, 0], error: boom() }] });
    expect(hasAlert(err)).toBe(true);
    expect(text(err)).toContain("Trades couldn't load.");
    expect(text(err)).toContain("Try again");
  });
});
