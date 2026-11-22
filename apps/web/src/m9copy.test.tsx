// What M9 changed that the owner sees (22 Nov 2026, main e949400):
// - "Flatten and stop" stays unapplied until every position is sold (M9 1.2, #525); so does a new run that sells the
//   old one first. Controls and Home say so instead of "waiting for the engine".
// - A decision taken late (M9 1.3, #517) and a decision sent late (M9 1.4, #518) on the plan panel.
// Only what kb_overview, kb_multiday_plan and kb_health return today is read (lib/startstate.ts, lib/planlate.ts).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Health as HealthResp, Heartbeat, Overview } from "./lib/api";
import { parseBook } from "./lib/book";
import { carriesFrom, skipText } from "./lib/carry";
import { parsePlan } from "./lib/multiday";
import { lateDecision, lateSend, planNotes } from "./lib/planlate";
import { SEEN_AFTER_S, commandSeen, flattenState, startState } from "./lib/startstate";
import { Controls } from "./pages/Controls";
import { StartBanner } from "./pages/Live";
import { Positions } from "./pages/Positions";
import { ctxFor, glitches, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const NOW = Date.parse("2026-11-23T15:00:00Z");
const run = paperRun({ id: 7, amount_usd: 95000, status: "running" });
const beat = (ageS: number, detail: Heartbeat["detail"] = { state: "trading" }): Heartbeat[] =>
  [{ process: "engine", ts: new Date(NOW - ageS * 1000).toISOString(), detail }];
const cmd = (kind: string, agoS: number, payload: Record<string, unknown> = {}) => ({ kind, ts: new Date(NOW - agoS * 1000).toISOString(), payload });
const ov = (heartbeat: Heartbeat[], pending: Overview["pending_commands"] = []): Overview =>
  ({ run, daily: [], heartbeat, pending_commands: pending, ledger: null, today: null });

describe("Flatten and stop from the click to the run's end (#525)", () => {
  it("no command: nothing", () => expect(flattenState(ov(beat(5)), NOW)).toBe("none"));
  it("just sent, engine online: sent, not yet waiting", () => {
    expect(flattenState(ov(beat(5), [cmd("flatten_and_stop", 20)]), NOW)).toBe("sent");
    expect(flattenState(ov(beat(5), [cmd("flatten_and_stop", 5 + SEEN_AFTER_S - 1)]), NOW)).toBe("sent");
  });
  it("still unapplied after the engine checked in 30 s or more later: waiting for the sale", () => {
    expect(flattenState(ov(beat(5), [cmd("flatten_and_stop", 5 + SEEN_AFTER_S)]), NOW)).toBe("waiting");
    expect(flattenState(ov(beat(5), [cmd("flatten_and_stop", 3 * 86400)]), NOW)).toBe("waiting"); // over a weekend
  });
  it("a stale or stopped engine is never 'waiting for the sale'", () => {
    expect(flattenState(ov(beat(600), [cmd("flatten_and_stop", 3600)]), NOW)).toBe("engine_off");
    expect(flattenState(ov([], [cmd("flatten_and_stop", 3600)]), NOW)).toBe("engine_off");
    expect(flattenState(ov(beat(5, { state: "stopped" }), [cmd("flatten_and_stop", 3600)]), NOW)).toBe("engine_off");
  });
  it("a heartbeat older than the command says nothing about it, and a bad time is not 'seen'", () => {
    expect(commandSeen(ov(beat(60)), new Date(NOW - 10_000).toISOString(), NOW)).toBe(false);
    expect(commandSeen(ov(beat(5)), "soon", NOW)).toBe(false);
  });

  const html = (o: Overview) => renderWith(o);
  function renderWith(o: Overview) {
    return render(<Controls run={run} overview={o} />, { ctx: ctxFor(run, { overview: o }) });
  }
  it("Controls: waiting says the sale is not complete and buys are blocked; the button is gone", () => {
    const now = Date.now(), o = { ...ov([{ process: "engine", ts: new Date(now - 4000).toISOString(), detail: { state: "trading" } }]),
      pending_commands: [{ kind: "flatten_and_stop", ts: new Date(now - 120_000).toISOString(), payload: {} }] };
    const h = html(o), t = text(h);
    expect(t).toContain("Flatten and stop is waiting: the sale is not complete yet, buys are blocked.");
    expect(t).toContain("The run ends when everything is sold.");
    expect(h).not.toMatch(/<button class="danger full-sm">Flatten and stop<\/button>/);
    expect(t).not.toContain("Waiting for the engine");
    expect(h).toContain('role="status"');
    expect(glitches(h)).toEqual([]);
  });
  it("Controls: just sent, and an engine that is off, read differently; other commands are still listed", () => {
    const now = Date.now(), fresh: Heartbeat[] = [{ process: "engine", ts: new Date(now - 4000).toISOString(), detail: { state: "trading" } }];
    const sent = text(html({ ...ov(fresh), pending_commands: [{ kind: "flatten_and_stop", ts: new Date(now - 6000).toISOString(), payload: {} },
      { kind: "set_level", ts: new Date(now - 6000).toISOString(), payload: {} }] }));
    expect(sent).toContain("Flatten and stop is sent. The engine picks it up within about 10 seconds.");
    expect(sent).toContain("Waiting for the engine: change the level");
    expect(sent).not.toContain("the sale is not complete");
    const off = text(html({ ...ov([]), pending_commands: [{ kind: "flatten_and_stop", ts: new Date(now - 600_000).toISOString(), payload: {} }] }));
    expect(off).toContain("Flatten and stop waits for the engine: its last heartbeat is more than 90 seconds old.");
  });
  it("Controls: nothing pending keeps the button", () => {
    expect(html(ov(beat(5)))).toMatch(/<button class="danger full-sm">Flatten and stop<\/button>/);
  });
});

describe("a new run that sells the old one first (#525)", () => {
  const nr = (agoS: number) => cmd("new_run", agoS, { amount_usd: 5000, level: "balanced" });
  it("seen by the engine and still unapplied: the old run is being sold", () =>
    expect(startState(run, ov(beat(5), [nr(120)]), NOW)).toEqual({ kind: "queued_selling", amount: 5000, level: "balanced" }));
  it("just queued, or the engine off: as before", () => {
    expect(startState(run, ov(beat(5), [nr(10)]), NOW).kind).toBe("queued_online");
    expect(startState(run, ov(beat(600), [nr(3600)]), NOW).kind).toBe("queued_waiting");
  });
  it("Home says what it waits for", () => {
    const s = text(renderToStaticMarkup(<StartBanner state={{ kind: "queued_selling", amount: 5000, level: "balanced" }} />));
    expect(s).toContain("A new run of $5,000.00 at Balanced is waiting: the old run's sale is not complete yet, buys are blocked.");
    expect(s).not.toContain("within about 10 seconds");
  });
});

const FRI = "2026-11-20", MON = "2026-11-23", TUE = "2026-11-24";
const rawPlan = (over: Record<string, unknown> = {}, orders: unknown[] = []) => ({ rules: [{ rule: "trend_momentum", decided_after_close_of: FRI,
  fills_at_close_of: MON, decided_at: "2026-11-20T21:20:00Z", decides: true, sell: [], buy_candidates: [], skipped: 0, note: null, orders, ...over }] });
const ev = (type: string, payload: Record<string, unknown>, ts: string) => ({ type, ts, run_id: 7, payload });
const lateCarry = (skipped: Record<string, string> = {}) =>
  ev("multi_day_carry", { strategy: "trend_momentum", session: TUE, late_of: MON, of: MON, carried: [], skipped, risk_off: false }, `${TUE}T20:50:07Z`);

describe("a decision taken late (#517): from kb_multiday_plan's decided_at and fill session", () => {
  const at = (decided_at: unknown) => lateDecision(parsePlan(rawPlan({ decided_at }))[0]);
  it("after the close, in the evening or before the next open: on time", () => {
    expect(at("2026-11-20T21:20:00Z")).toBeNull();          // Friday 16:20 ET
    expect(at("2026-11-23T13:00:00Z")).toBeNull();          // Monday 08:00 ET, before the open
    expect(at("2026-11-23T14:29:00Z")).toBeNull();          // 09:29 ET
  });
  it("during its own fill session: late, with the ET time", () => {
    expect(at("2026-11-23T14:30:00Z")).toBe("09:30");
    expect(at("2026-11-23T15:01:07.123456+00:00")).toBe("10:01");
    expect(at("2026-11-23T10:01:07-05:00")).toBe("10:01");
  });
  it("no time, or one that cannot be read: nothing is claimed", () => {
    expect(at(null)).toBeNull();
    expect(at("yesterday")).toBeNull();
    expect(lateDecision({ decidedAt: "2026-11-23T15:01:00Z", fillsAt: null })).toBeNull();
  });
  it("the note shows only while the fill session is not over", () => {
    const plans = parsePlan(rawPlan({ decided_at: "2026-11-23T15:01:00Z" }));
    expect(planNotes(plans, [], MON)).toMatchObject([{ title: "Decided late, at 10:01 ET" }]);
    expect(planNotes(plans, [], MON)[0].text).toBe("This decision is normally taken after the close of Fri, Nov 20. It was taken during the session instead. Its orders go out at this close as usual.");
    expect(planNotes(plans, [], TUE)).toEqual([]);
    expect(planNotes(parsePlan(rawPlan()), [], MON)).toEqual([]);
  });
});

describe("a decision sent late (#518): from the multi_day_carry event's late_of", () => {
  const plans = parsePlan(rawPlan());
  it("reads late_of; an ordinary carry-over has none", () => {
    const cs = carriesFrom([lateCarry(), ev("multi_day_carry", { strategy: "trend_momentum", session: TUE, of: MON, carried: [], skipped: {} }, `${TUE}T20:55:00Z`)], 7);
    expect(cs.map((c) => c.lateOf)).toEqual([null, MON]);
    expect(lateSend(plans[0], cs)?.session).toBe(TUE);
    expect(lateSend(plans[0], cs.slice(0, 1))).toBeNull();
  });
  it("another rule's or another decision's late send is not this plan's", () => {
    const other = carriesFrom([ev("multi_day_carry", { strategy: "mom_12_1_band", session: TUE, late_of: MON, carried: [], skipped: {} }, `${TUE}T20:50:00Z`),
      ev("multi_day_carry", { strategy: "trend_momentum", session: TUE, late_of: FRI, carried: [], skipped: {} }, `${TUE}T20:50:00Z`)], 7);
    expect(lateSend(plans[0], other)).toBeNull();
  });
  it("the note: when it was due, when it went out, and what was left out in plain words", () => {
    const [n] = planNotes(plans, carriesFrom([lateCarry({ NVDA: "market filter off", APA: "no bar at the last close" })], 7), TUE);
    expect(n.title).toBe("Sent late, at the close of Tue, Nov 24");
    expect(n.text).toBe("The orders were due at the close of Mon, Nov 23 and were not sent then. They go out once, at the close of Tue, Nov 24, sized at that day's prices.");
    expect(n.skipped.map((x) => x.symbol)).toEqual(["NVDA", "APA"]);
    expect(n.skipped[1].text).toContain("it had no price at the last close");
    expect(planNotes(plans, carriesFrom([lateCarry()], 7), "2026-11-25")[0].text).toContain("They went out once");
    expect(skipText("no bar at the last close", "trend_momentum", MON).again).toBe(true);
  });

  it("on the page: the plan panel says it, above the orders", () => {
    const health: HealthResp = { now: `${TUE}T21:00:00Z`, heartbeat: [], halts: [], pending_commands: [], deadman: { armed: false, jobs: [], runs: [] },
      events: [lateCarry({ NVDA: "market filter off" })] };
    const book = parseBook({ run_id: 7, session: MON, mtm: { equity: 95000, equity_reported: 95000 }, positions: [], exposure: { by_rule: [] },
      reconciled: { ok: true, ts: null, positions: {}, cash_diff: 0 } }, 7);
    const html = render(<Positions />, { ctx: ctxFor(run), seeds: [{ key: ["health"], data: health }, { key: ["md-plan", 7], data: rawPlan() },
      { key: ["book", 7], data: book }] });
    const t = text(html);
    expect(t).toContain("Sent late, at the close of Tue, Nov 24.");
    expect(t).toContain("NVDA was left out: the market check is off");
    expect(t).not.toContain("late_of");
    expect(glitches(html)).toEqual([]);
  });
});
