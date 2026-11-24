// Stakeholder, 24 Nov 2026: on 24 Nov the dashboard still listed "planned buys for the close of 18 Nov". Product: Home
// and Positions show only holdings and today's (or the next session's) plan; a past plan is one quiet line on
// Activity; with the market filter off, Home says so once (the trading-state line) and shows no plan.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { Route } from "react-router-dom";
import { Shell, loadTradingState } from "./components/Shell";
import { Live } from "./pages/Live";
import { Positions } from "./pages/Positions";
import { Tape } from "./pages/Tape";
import type { Heartbeat, Health as HealthResp } from "./lib/api";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
afterEach(() => { vi.useRealTimers(); });
beforeAll(async () => { await loadTradingState(); });

const NOW = new Date("2026-11-24T16:00:00Z"); // Tue 11:00 ET, 17:00 Berlin
const run = paperRun({ id: 7 });
const SYMS = Array.from({ length: 37 }, (_, i) => `Q${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))}`);
SYMS[7] = "APA"; SYMS[8] = "XOM"; // two of the 30 that expired on 18 Nov
const order = (symbol: string, state: string) => ({ symbol, shares_now: 0, shares_target: 10, action: "buy", why: "rank", state });
const PLAN = (fills: string) => ({ rules: [{ rule: "trend_momentum", decided_after_close_of: "2026-11-17", fills_at_close_of: fills, sell: [],
  buy_candidates: [], skipped: 0, note: null, orders: SYMS.map((s, i) => order(s, i < 7 ? "filled" : "canceled")) }] });
const unf = (symbol: string) => ({ type: "multi_day_unfilled", ts: "2026-11-18T21:01:00Z", run_id: 7,
  payload: { strategy: "trend_momentum", symbol, side: "buy", qty: 10, filled: 0, left: 10, session: "2026-11-18", status: "expired" } });
const beat: Heartbeat = { process: "engine", ts: new Date(NOW.getTime() - 5000).toISOString(), detail: { state: "managing", lease: "ok", account_clean: true,
  multi_day: { rules: { trend_momentum: { note: "carried buys dropped: market filter off", waiting: "18 Nov plan: 30 buys carried, market filter off" } } } } as Heartbeat["detail"] };
const overview = { ...emptyOverview(run), heartbeat: [beat] };
const health: HealthResp = { now: NOW.toISOString(), heartbeat: [beat], halts: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] },
  events: SYMS.slice(7).map(unf) };
// QA on #620: the engine carries APA and XOM into today's close (#366 multi_day_carry, session = today)
const CARRY_TODAY = { type: "multi_day_carry", ts: "2026-11-24T20:50:07Z", run_id: 7, payload: { strategy: "trend_momentum", session: "2026-11-24",
  of: "2026-11-18", carried: [["APA", 10, 0], ["XOM", 10, 0]], skipped: {}, risk_off: false } };
const withCarry = { ...health, events: [...health.events, CARRY_TODAY] };
const seeds = (fills: string, h: HealthResp = health) => [{ key: ["runs"], data: [run] }, { key: ["overview", 7], data: { ...overview, daily: [], segments: [], halts_today: [] } },
  { key: ["health"], data: h }, { key: ["md-plan", 7], data: PLAN(fills) }, { key: ["trades-all", 7], data: { total: 0, rows: [] } }];

describe("24 Nov: the 18 Nov plan is over", () => {
  it("Home: no plan, and 'market filter off' once (the trading-state line)", () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const t = text(render(<Shell />, { seeds: seeds("2026-11-18"), children: <Route index element={<Live />} /> }));
    expect(t).toContain("Paper pot"); // Home itself rendered, inside the shell
    expect(t).toContain("Holding — market filter off");
    expect(t.match(/market filter off/g)).toHaveLength(1);
    expect(t).not.toMatch(/Planned buys|planned|close of|Last plan|18 Nov|Nov 18/);
    for (const s of SYMS) expect(t).not.toContain(s);
  });
  it("Positions: no planned or unfilled order of 18 Nov", () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const t = text(render(<Positions />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-18") }));
    expect(t).not.toMatch(/Planned buys|At the close of|not filled|Last plan/);
    for (const s of SYMS) expect(t).not.toContain(s);
  });
  it("Activity: one quiet line, how the last plan ended", () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const t = text(render(<Tape />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-18") }));
    expect(t.match(/Last plan/g)).toHaveLength(1);
    expect(t).toContain("Last plan (18 Nov): 7 of 37 buys filled, 30 expired at the close");
  });
  it("today's plan is still shown on Positions (and is not a 'last plan' on Activity)", () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const t = text(render(<Positions />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-24") }));
    expect(t).toContain("At the close of");
    expect(t).toContain(SYMS[10]);
    expect(text(render(<Tape />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-24") }))).not.toContain("Last plan");
  });
  it("buys carried into today's close (APA, XOM) are current orders: listed as carried, kept out of the expired count", () => {
    vi.useFakeTimers({ now: new Date("2026-11-24T21:00:00Z"), toFake: ["Date"] }); // 16:00 ET, after the carry-over sent
    const t = text(render(<Positions />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-18", withCarry) }));
    expect(t).toContain("At the close of");
    expect(t).toContain("APA Buy 10 shares · in the top ranks · carried from 18 Nov, sent again at today's close carried");
    expect(t).toContain("XOM Buy 10 shares · in the top ranks · carried from 18 Nov, sent again at today's close carried");
    expect(t).not.toMatch(/Planned buys|not filled|Last plan/);
    for (const s of SYMS.filter((x) => x !== "APA" && x !== "XOM")) expect(t).not.toContain(s);
    const a = text(render(<Tape />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-18", withCarry) }));
    expect(a).toContain("Last plan (18 Nov): 7 of 37 buys filled, 2 carried into today's close, 28 expired at the close");
  });
  it("#621: with the market filter off the carried buys are on hold, in one line; nothing says 'sent again'", () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const off = { type: "multi_day_carry", ts: "2026-11-23T20:50:19Z", run_id: 7, payload: { strategy: "trend_momentum", session: "2026-11-23",
      of: "2026-11-18", carried: [], skipped: Object.fromEntries(SYMS.slice(7).map((s) => [s, "market filter off"])), risk_off: true } };
    const t = text(render(<Positions />, { ctx: ctxFor(run, { overview }), seeds: seeds("2026-11-18", { ...health, events: [...health.events, off] }) }));
    expect(t.match(/Carried buys on hold — market filter off/g)).toHaveLength(1);
    expect(t).not.toMatch(/sent again|At the close of|Last plan/);
    for (const s of SYMS) expect(t).not.toContain(s);
  });
});
