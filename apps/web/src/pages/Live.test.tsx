import { describe, expect, it, vi } from "vitest";
import { Live } from "./Live";
import { boom, ctxFor, glitches, hasAlert, hasStatus, paperRun, render, replayRun, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const run = paperRun();
const noTrades = { key: ["trades-all", 1], data: { total: 0, rows: [] } };

describe("Live (Home) page states", () => {
  it("loading: the dashboard skeleton while the overview is on its way, never a zero pot", () => {
    const html = render(<Live />, { ctx: ctxFor(run, { overview: undefined }), seeds: [noTrades] });
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
    expect(text(html)).not.toContain("$");
  });

  it("loading: the skeleton while trades are on their way", () => {
    const html = render(<Live />, { ctx: ctxFor(run) });
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });

  it("empty: a new paper run says no trades and no positions, in words", () => {
    const html = render(<Live />, { ctx: ctxFor(run), seeds: [noTrades] });
    const t = text(html);
    expect(t).toContain("No trades yet.");
    expect(t).toContain("No open positions.");
    expect(t).toContain("$10,000.00");
    expect(t).toContain("The engine isn't running.");
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a replay with no trading day", () => {
    const r = replayRun();
    const html = render(<Live />, { ctx: ctxFor(r), seeds: [{ key: ["trades-all", r.id], data: { total: 0, rows: [] } }] });
    expect(text(html)).toContain("Replay, not live trading.");
    expect(text(html)).toContain("No trades yet.");
    expect(glitches(html)).toEqual([]);
  });

  it("error: the overview failed, so it says so with a retry instead of a skeleton", () => {
    const html = render(<Live />, { ctx: ctxFor(run, { overview: undefined, overviewError: boom() }), seeds: [noTrades] });
    expect(hasAlert(html)).toBe(true);
    expect(hasStatus(html)).toBe(false);
    expect(text(html)).toContain("The run overview couldn't load.");
    expect(text(html)).toContain("Try again");
  });

  it("error: trades failed", () => {
    const html = render(<Live />, { ctx: ctxFor(run), seeds: [{ key: ["trades-all", 1], error: boom("timeout") }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("Trades couldn't load.");
    expect(text(html)).toContain("timeout");
  });
});
