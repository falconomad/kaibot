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
    expect(text(html)).toContain("How much paper money should Kaibot trade?");
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
