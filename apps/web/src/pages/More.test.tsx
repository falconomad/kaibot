// #359: the More page renders, and every link on it points at a route the app has.
import { describe, expect, it, vi } from "vitest";
import { ROUTE_LOADERS } from "../routes";
import { More } from "./More";
import { ctxFor, glitches, paperRun, render, replayRun, text } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const links = (html: string) => [...html.matchAll(/<a\b[^>]*\shref="([^"]+)"/g)].map((m) => m[1]);

describe("More page", () => {
  it("renders its pages, the paper note and sign out", () => {
    const html = render(<More />);
    const t = text(html);
    for (const label of ["Report", "Go / no-go", "Strategy", "Run & settings", "New run"]) expect(t).toContain(label);
    expect(t).toContain("Paper trading only");
    expect(t).toContain("Sign out");
    expect(glitches(html)).toEqual([]);
  });

  it("every link points at an existing route", () => {
    const hrefs = links(render(<More />));
    expect(hrefs.length).toBeGreaterThanOrEqual(5);
    for (const to of hrefs) expect(Object.keys(ROUTE_LOADERS), to).toContain(to);
  });

  it("with meters and more than one run: the limits and the run picker", () => {
    const a = paperRun(), b = replayRun();
    const html = render(<More />, { ctx: ctxFor(a, { runs: [a, b], meters: { dayPct: -0.4, dd: 1.2, replay: false }, pickRun: () => {} }) });
    const t = text(html);
    expect(t).toContain("Day vs stop");
    expect(t).toContain("Run drawdown");
    expect(html).toMatch(/<select[^>]*aria-label="Run"/);
    expect(t).toContain("Replay A");
    expect(glitches(html)).toEqual([]);
  });

  it("a single run without meters: no picker and no limits card", () => {
    const html = render(<More />);
    expect(html).not.toMatch(/<select|more-meters/);
  });
});
