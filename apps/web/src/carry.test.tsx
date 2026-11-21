// #366 on the plan panel: after its fill session a decision's buys that are still short are carried at each next
// session's close until the next decision (event `multi_day_carry` {strategy, session, of, carried: [[symbol, target,
// held]], skipped: {symbol: reason}, risk_off}, engine/live_daily.py `_plan`). The panel says, per planned buy, where it
// stands: filled / carried and filled on <day> / waiting to be carried / skipped today: <reason in plain words>.
// 20 Nov 2026: every carried buy was skipped with "market filter off"; the panel showed Wednesday's buys as "not filled".
import { describe, expect, it, vi } from "vitest";
import { parseBook } from "./lib/book";
import { buyState, buyStateText, carriesFrom, nextDecisionText, pastBuys, skipText } from "./lib/carry";
import { parsePlan } from "./lib/multiday";
import type { Health as HealthResp } from "./lib/api";
import { Positions } from "./pages/Positions";
import { ctxFor, glitches, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun({ id: 7, amount_usd: 95000 });
const WED = "2026-11-18", THU = "2026-11-19", FRI = "2026-11-20";
const ev = (type: string, payload: Record<string, unknown>, ts: string, run_id: number | null = 7) => ({ type, ts, run_id, payload });
const carry = (session: string, carried: [string, number, number][], skipped: Record<string, string>, over: Record<string, unknown> = {}) =>
  ev("multi_day_carry", { strategy: "trend_momentum", session, of: WED, carried, skipped, risk_off: false, ...over }, `${session}T20:50:07Z`);
const buy = (symbol: string, target: number, state = "canceled") => ({ symbol, shares_now: 0, shares_target: target, action: "buy", why: "rank", state });
const RAW_PLAN = (orders: unknown[]) => ({ rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-17", fills_at_close_of: WED, sell: [],
  buy_candidates: [], skipped: 0, note: null, orders }] });
const pos = (symbol: string, qty: number, opened: string) =>
  ({ rule: "trend_momentum", symbol, qty, avg_cost: 100, mark: 101, last: 101, opened_session: opened, dividends: 0, stop: null });
const book = (session: string, positions: unknown[]) => parseBook({ run_id: 7, session, mtm: { equity: 95000, equity_reported: 95000 }, positions,
  exposure: { by_rule: [] }, reconciled: { ok: true, ts: null, positions: {}, cash_diff: 0 } }, 7);

describe("the carry-over events", () => {
  it("parses the engine's payload; another run's events and broken ones are dropped; newest session first", () => {
    const cs = carriesFrom([carry(THU, [["APA", 26, 0]], { BBY: "market filter off" }), carry(FRI, [], { APA: "market filter off" }, { risk_off: true }),
      { ...carry(FRI, [], {}), run_id: 8 }, ev("multi_day_carry", { strategy: "trend_momentum" }, "2026-11-20T21:00:00Z")], 7);
    expect(cs.map((c) => c.session)).toEqual([FRI, THU]);
    expect(cs[1]).toMatchObject({ rule: "trend_momentum", of: WED, carried: [{ symbol: "APA", target: 26, held: 0 }], skipped: { BBY: "market filter off" } });
    expect(cs[0].riskOff).toBe(true);
  });
});

describe("each reason in plain words", () => {
  it("market filter off: the market check, and it tries again until the next decision", () => {
    expect(skipText("market filter off", "trend_momentum", WED)).toEqual({ again: true,
      text: "the market check is off: the rule doesn't buy while the market trend is down; it will try again next session until Wednesday's decision" });
    expect(nextDecisionText("mom_12_1_band", WED)).toBe("next month's decision");
  });
  it("a buy that is not sent again says so", () => {
    expect(skipText("order canceled", "trend_momentum", WED)).toEqual({ again: false, text: "its order was canceled; it is not bought before Wednesday's decision" });
    expect(skipText("sold since the decision", "trend_momentum", WED).again).toBe(false);
    expect(skipText("not buyable: universe", "trend_momentum", WED).text).toMatch(/^it is not in the list of stocks the rule may buy today/);
    expect(skipText("no cash room at the last close (the cash reserve is kept)", "trend_momentum", WED).text).toContain("not enough cash");
  });
  it("an unknown reason is shown as the engine wrote it, never with a capitalised brand", () => {
    expect(skipText("Kaibo said no", "trend_momentum", WED).text).toMatch(/^kaibo said no; it will try again/);
  });
});

describe("each planned buy's state", () => {
  const [plan] = parsePlan(RAW_PLAN([buy("ABBV", 5, "filled"), buy("APA", 26), buy("BBY", 23), buy("BIIB", 11), buy("PFE", 59)]));
  const o = (s: string) => plan.orders.find((x) => x.symbol === s)!;
  // Thursday's carry-over bought APA; Friday's skipped BBY for the market check; PFE was not tried yet today
  const cs = carriesFrom([carry(THU, [["APA", 26, 0]], { BBY: "market filter off" }), carry(FRI, [], { BBY: "market filter off", BIIB: "order canceled" })], 7);
  const b = book(THU, [pos("ABBV", 5, WED), pos("APA", 26, THU)]);

  it("filled / carried and filled on <day> / skipped today / waiting to be carried", () => {
    expect(buyState(plan, o("ABBV"), cs, b, FRI)).toEqual({ kind: "filled", day: WED });
    expect(buyState(plan, o("APA"), cs, b, FRI)).toEqual({ kind: "carried_filled", day: THU });
    expect(buyState(plan, o("BBY"), cs, b, FRI)).toMatchObject({ kind: "skipped", day: FRI, today: true, again: true });
    expect(buyState(plan, o("BIIB"), cs, b, FRI)).toMatchObject({ kind: "skipped", today: true, again: false });
    expect(buyState(plan, o("PFE"), cs, b, FRI)).toEqual({ kind: "waiting", held: 0, target: 59 });
    expect(buyStateText({ kind: "carried_filled", day: THU }).tag).toBe("carried and filled on Thu, Nov 19");
    expect(buyStateText(buyState(plan, o("BBY"), cs, b, FRI)).tag).toBe("skipped today");
    expect(buyStateText(buyState(plan, o("PFE"), cs, b, FRI)).tag).toBe("waiting to be carried");
  });
  it("a buy sent again at today's close, before that close is booked, is carried", () => {
    const today = carriesFrom([carry(FRI, [["PFE", 59, 0]], {})], 7);
    expect(buyState(plan, o("PFE"), today, b, FRI)).toEqual({ kind: "carried", day: FRI, today: true });
    expect(buyStateText({ kind: "carried", day: FRI, today: true }).line).toBe("sent again at today's close");
  });
  it("a skip of an earlier session says its day (the weekend after Friday's)", () => {
    const s = buyState(plan, o("BBY"), cs, book(FRI, [pos("ABBV", 5, WED), pos("APA", 26, THU)]), "2026-11-21");
    expect(buyStateText(s).tag).toBe("skipped on Fri, Nov 20");
  });
  it("on the fill session itself the order's own state shows", () => {
    expect(buyState(plan, o("PFE"), [], book("2026-11-17", []), WED)).toEqual({ kind: "order" });
  });
  it("once every buy is in, the earlier plan stops showing (after the session it came in)", () => {
    const done = parsePlan(RAW_PLAN([buy("ABBV", 5, "filled")]))[0];
    expect(pastBuys(done, [], book(WED, [pos("ABBV", 5, WED)]), THU)).not.toBeNull();
    expect(pastBuys(done, [], book(THU, [pos("ABBV", 5, WED)]), FRI)).toBeNull();
  });
});

describe("Positions after the 20 Nov session", () => {
  const SEVEN = ["ABBV", "APA", "BBY", "BIIB", "PFE", "TMO", "XOM"];
  it("every carried buy skipped with the market filter off: each says so in plain words, none says just 'not filled'", () => {
    const health: HealthResp = { now: "2026-11-20T22:00:00Z", heartbeat: [], halts: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] },
      events: [carry(FRI, [], Object.fromEntries(SEVEN.map((s) => [s, "market filter off"])), { risk_off: true }),
        ev("multi_day_unfilled", { strategy: "trend_momentum", symbol: "APA", side: "buy", qty: 26, filled: 0, left: 26, session: WED, status: "expired" }, `${WED}T21:01:00Z`)] };
    vi.useFakeTimers({ now: new Date("2026-11-20T22:00:00Z"), toFake: ["Date"] });
    try {
      const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health },
        { key: ["md-plan", 7], data: RAW_PLAN(SEVEN.map((s) => buy(s, 10))) }, { key: ["book", 7], data: book(THU, []) }] });
      const t = text(html);
      expect(t).toContain("Planned buys for the close of Nov 18, 2026");
      for (const s of SEVEN) expect(t).toContain(`${s} Buy 10 shares · in the top ranks · the market check is off`);
      expect(t.match(/skipped today/g)?.length).toBe(7);
      expect(t).toContain("it will try again next session until Wednesday's decision");
      expect(t).not.toContain("not filled");
      expect(glitches(html)).toEqual([]);
    } finally { vi.useRealTimers(); }
  });
  it("before 0033 is applied (no carry events): a short buy after its fill session waits to be carried", () => {
    const health: HealthResp = { now: "2026-11-19T22:00:00Z", heartbeat: [], halts: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] }, events: [] };
    vi.useFakeTimers({ now: new Date("2026-11-19T22:00:00Z"), toFake: ["Date"] });
    try {
      const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health },
        { key: ["md-plan", 7], data: RAW_PLAN([buy("ABBV", 5, "filled"), buy("APA", 26)]) }, { key: ["book", 7], data: book(WED, [pos("ABBV", 5, WED)]) }] });
      const t = text(html);
      expect(t).toContain("ABBV Buy 5 shares · in the top ranks · bought at the close of Wed, Nov 18 filled");
      expect(t).toContain("APA Buy 26 shares · in the top ranks · not filled; the next session's close tries again waiting to be carried");
    } finally { vi.useRealTimers(); }
  });
});
