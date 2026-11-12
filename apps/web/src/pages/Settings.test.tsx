import { describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import { boom, ctxFor, glitches, hasAlert, hasStatus, paperRun, render, replayRun, text } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

// Settings has no query of its own: the run list comes from the shell, and the run controls need the shell's overview.
const run = paperRun();

describe("Run & settings page states", () => {
  it("loading: the run is shown, and the controls have a placeholder until the overview arrives", () => {
    const html = render(<Settings />, { ctx: ctxFor(run, { overview: undefined }) });
    expect(hasStatus(html)).toBe(true);
    expect(text(html)).toContain("This run");
    expect(text(html)).not.toContain("Pause entries");
  });

  it("empty: a fresh run with no level change and no other runs", () => {
    const html = render(<Settings />, { ctx: ctxFor(paperRun({ level: null, started_on: null })) });
    const t = text(html);
    expect(t).toContain("Scheduled change none");
    expect(t).toContain("Started / ended — / —");
    expect(t).toContain("Pause entries");
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a replay has no controls and says it is read-only", () => {
    const r = replayRun();
    const html = render(<Settings />, { ctx: ctxFor(r) });
    expect(text(html)).toContain("read-only");
    expect(text(html)).not.toContain("Pause entries");
    expect(hasStatus(html)).toBe(false);
  });

  it("error: the overview failed, so the controls say they couldn't load instead of disappearing", () => {
    const html = render(<Settings />, { ctx: ctxFor(run, { overview: undefined, overviewError: boom() }) });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("The run controls couldn't load.");
    expect(text(html)).toContain("Try again");
    expect(text(html)).toContain("All runs");
  });
});
