import { describe, expect, it, vi } from "vitest";
import { Live, StartBanner } from "./Live";
import { renderToStaticMarkup } from "react-dom/server";
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
    expect(t).toContain("Holding nothing");
    expect(t).toContain("Nothing held right now.");
    expect(t).toContain("$10,000.00");
    expect(t).toContain("Engine offline · no new trades until it is back"); // never connected: always the warning
    expect(glitches(html)).toEqual([]);
  });

  it("empty: a replay with no trading day", () => {
    const r = replayRun();
    const html = render(<Live />, { ctx: ctxFor(r), seeds: [{ key: ["trades-all", r.id], data: { total: 0, rows: [] } }] });
    expect(text(html)).toContain("Replay on past data, not live trading");
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

describe("Home: the new run's start state (no 'waits for the engine' while it is online)", () => {
  const t = (el: Parameters<typeof renderToStaticMarkup>[0]) => text(renderToStaticMarkup(el));
  it("applied with the engine online: accepted and scheduled for the next open, ET (CET)", () => {
    const s = t(<StartBanner state={{ kind: "scheduled", online: true, nextOpen: "2026-11-17T09:30:00-05:00", marketOpen: false }} />);
    expect(s).toContain("The engine has accepted it.");
    expect(s).toContain("Scheduled: starts at the next open, Tue, Nov 17, 09:30 ET (15:30 CET).");
    expect(s).not.toMatch(/waits for the engine|comes online/);
  });
  it("queued with the engine online: picked up shortly", () => {
    const s = t(<StartBanner state={{ kind: "queued_online", amount: 5000, level: "balanced" }} />);
    expect(s).toContain("The engine is online and picks it up within about 10 seconds.");
    expect(s).not.toContain("waits for the engine");
  });
  it("queued with a stale heartbeat: waits for the engine", () =>
    expect(t(<StartBanner state={{ kind: "queued_waiting", amount: 5000, level: "balanced" }} />)).toContain("It waits for the engine"));
  it("on the page: a scheduled run with a fresh heartbeat", () => {
    const r = paperRun({ status: "scheduled" });
    const overview = { run: r, daily: [], pending_commands: [], ledger: null, today: null,
      heartbeat: [{ process: "engine", ts: new Date().toISOString(), detail: { state: "market_closed", next_open: "2026-11-17T09:30:00-05:00" } }] };
    const s = text(render(<Live />, { ctx: ctxFor(r, { overview }), seeds: [{ key: ["trades-all", r.id], data: { total: 0, rows: [] } }] }));
    expect(s).toContain("The engine has accepted it. Scheduled: starts at the next open");
    expect(s).not.toContain("Engine offline");
    expect(s).not.toMatch(/waits for the engine|comes online/);
  });
});
