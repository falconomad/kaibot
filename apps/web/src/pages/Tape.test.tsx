import { describe, expect, it, vi } from "vitest";
import { Tape } from "./Tape";
import { boom, ctxFor, glitches, hasAlert, hasStatus, render, replayRun, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

describe("Tape page states", () => {
  it("loading: a skeleton", () => {
    const html = render(<Tape />);
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });

  it("empty: says nothing happened yet and when setups are checked", () => {
    const html = render(<Tape />, { seeds: [{ key: ["tape", 1], data: [], infinite: true }] });
    expect(text(html)).toContain("Nothing yet. Setups are checked every 5 minutes");
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a replay explains that replays have no tape", () => {
    const r = replayRun();
    const html = render(<Tape />, { ctx: ctxFor(r), seeds: [{ key: ["tape", r.id], data: [], infinite: true }] });
    expect(text(html)).toContain("Replays don't record a tape");
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<Tape />, { seeds: [{ key: ["tape", 1], error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("The tape couldn't load.");
    expect(text(html)).toContain("permission denied for function");
    expect(text(html)).toContain("Try again");
  });
});
