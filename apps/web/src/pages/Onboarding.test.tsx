import { beforeEach, describe, expect, it, vi } from "vitest";
import { Onboarding } from "./Onboarding";
import { glitches, hasAlert, render, text } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

// Onboarding has no query: its only server call is the "start run" mutation on the last step. Without a DOM library
// the test can't click through the steps, so it seeds the page's first useState calls (step, amount, level, agreed)
// and stubs the mutation's state. Everything else is the real page.
const seed = vi.hoisted(() => ({ states: [] as unknown[], mutation: {} as Record<string, unknown> }));
vi.mock("react", async (orig) => {
  const r = await orig<typeof import("react")>();
  return { ...r, useState: (init: unknown) => r.useState(seed.states.length ? seed.states.shift() : init) };
});
vi.mock("@tanstack/react-query", async (orig) => {
  const q = await orig<typeof import("@tanstack/react-query")>();
  return { ...q, useMutation: () => ({ isPending: false, isError: false, error: null, mutate: () => {}, ...seed.mutation }) };
});

const lastStep = () => { seed.states = [4, 5000, "balanced", true]; };
beforeEach(() => { seed.states = []; seed.mutation = {}; });

describe("Onboarding (new run) page states", () => {
  it("empty: the first step asks for an amount, with the default filled in", () => {
    const html = render(<Onboarding />, { path: "/start", route: "/start" });
    expect(text(html)).toContain("How much paper money should kaibo trade?");
    expect(html).toContain('aria-current="step"');
    expect(hasAlert(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("loading: while the run is being queued the button says so and can't be pressed twice", () => {
    lastStep();
    seed.mutation = { isPending: true };
    const html = render(<Onboarding />, { path: "/start", route: "/start" });
    expect(text(html)).toContain("Queuing…");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Queuing…<\/button>/);
  });

  it("error: the server's refusal is shown as an alert in words, and the run can be retried", () => {
    lastStep();
    seed.mutation = { isError: true, error: new Error("a run is already queued") };
    const html = render(<Onboarding />, { path: "/start", route: "/start" });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("a run is already queued");
    expect(html).toMatch(/<button class="primary">Start paper run<\/button>/);
  });
});

describe("Onboarding contract: what the engine runs now", () => {
  const at = { path: "/start", route: "/start" };
  const exp = { strategy: "trend_momentum", evidence: "failed_gate_b", experiment: true, label: "Experiment: failed the ten-year test",
    real_money: false, level: "balanced", max_exposure_pct: 60, retire_drawdown_pct: 21 };
  const health = (detail: Record<string, unknown>) => ({ key: ["health"], data: { now: "", halts: [], events: [], pending_commands: [],
    deadman: { armed: true, jobs: [], runs: [] }, heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail }] } });
  it("multi-day on: the day rule ends flat, the experiment holds stocks overnight within the cap and stop line", () => {
    seed.states = [3, 5000, "balanced", true];
    const t = text(render(<Onboarding />, { ...at, seeds: [health({ state: "market_closed", experiments: [exp] })] }));
    expect(t).toContain("The intraday rule ends each day flat.");
    expect(t).toContain("The Trend momentum paper experiment, which failed the ten-year test, holds stocks for weeks, overnight included, within the level's cap and stop line (at Balanced: up to 60% invested, retires if it falls 21% from its peak).");
    expect(t).toContain("No shorts or leverage, ever.");
    expect(t).not.toContain("No overnight positions");
  });
  it("another level than the engine's row: no numbers, only the rule", () => {
    seed.states = [3, 5000, "aggressive", true];
    const t = text(render(<Onboarding />, { ...at, seeds: [health({ experiments: [exp] })] }));
    expect(t).toContain("within the level's cap and stop line.");
    expect(t).not.toContain("60%");
  });
  it("no experiments (multi-day off, older engine, no heartbeat): the day-only contract", () => {
    seed.states = [3, 5000, "balanced", true];
    expect(text(render(<Onboarding />, { ...at, seeds: [health({ state: "idle" })] }))).toContain("ending every day flat. No overnight positions, shorts or leverage.");
    seed.states = [3, 5000, "balanced", true];
    expect(text(render(<Onboarding />, at))).toContain("No overnight positions");
  });
});

