import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignIn } from "./SignIn";
import { glitches, hasAlert, render, text } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

// Sign-in has no query: it sends a magic link and keeps the result in local state. Without a DOM library the test
// can't submit the form, so it seeds the page's useState calls (email, state, error text). The rest is the real page.
const seed = vi.hoisted(() => ({ states: [] as unknown[] }));
vi.mock("react", async (orig) => {
  const r = await orig<typeof import("react")>();
  return { ...r, useState: (init: unknown) => r.useState(seed.states.length ? seed.states.shift() : init) };
});
beforeEach(() => { seed.states = []; });

describe("Sign-in page states", () => {
  it("empty: an email field and a disabled send button until an address is typed", () => {
    const html = render(<SignIn />);
    expect(text(html)).toContain("Sign in");
    expect(html).toMatch(/<button class="primary" disabled="">Email me a link<\/button>/);
    expect(hasAlert(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("loading: while the link is sent the button says so and is disabled", () => {
    seed.states = ["owner@example.com", "sending"];
    const html = render(<SignIn />);
    expect(html).toMatch(/<button class="primary" disabled="">Sending…<\/button>/);
  });

  it("error: the server's message is an alert in words, and the form stays usable", () => {
    seed.states = ["owner@example.com", "error", "Email rate limit exceeded"];
    const html = render(<SignIn />);
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("Email rate limit exceeded");
    expect(html).toMatch(/<button class="primary">Email me a link<\/button>/);
  });

  it("sent: says where the link went", () => {
    seed.states = ["owner@example.com", "sent"];
    expect(text(render(<SignIn />))).toContain("We sent a sign-in link to owner@example.com.");
  });
});
