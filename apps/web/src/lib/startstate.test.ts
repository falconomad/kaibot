import { describe, expect, it } from "vitest";
import type { Heartbeat, Overview, Run } from "./api";
import { engineOnline, startState } from "./startstate";

const NOW = Date.parse("2026-11-16T20:00:00Z");
const run = (over: Partial<Run> = {}): Run => ({ id: 3, created_at: "2026-11-16T19:59:00Z", amount_usd: 5000, status: "scheduled", end_reason: null,
  peak_equity: 5000, kind: "paper", label: "Paper", level: "balanced", started_on: null, ended_on: null, ...over });
const beat = (ageS: number, detail: Heartbeat["detail"] = { state: "market_closed", next_open: "2026-11-17T09:30:00-05:00" }): Heartbeat[] =>
  [{ process: "engine", ts: new Date(NOW - ageS * 1000).toISOString(), detail }];
const ov = (heartbeat: Heartbeat[], pending: Overview["pending_commands"] = []): Overview =>
  ({ run: run(), daily: [], heartbeat, pending_commands: pending, ledger: null, today: null });
const cmd = { kind: "new_run", ts: "2026-11-16T19:59:50Z", payload: { amount_usd: 5000, level: "balanced" } };

describe("startState: what Home says after 'Start paper run'", () => {
  it("applied, engine online: scheduled for the next open", () =>
    expect(startState(run(), ov(beat(12)), NOW)).toEqual({ kind: "scheduled", online: true, nextOpen: "2026-11-17T09:30:00-05:00", marketOpen: false }));
  it("applied, market open: starts within the session", () =>
    expect(startState(run(), ov(beat(5, { state: "trading" })), NOW)).toMatchObject({ kind: "scheduled", online: true, marketOpen: true }));
  it("applied, heartbeat stale: scheduled, not online", () =>
    expect(startState(run(), ov(beat(600)), NOW)).toEqual({ kind: "scheduled", online: false, nextOpen: null, marketOpen: false }));
  it("not applied yet, engine online: queued, picked up shortly", () =>
    expect(startState(run({ status: "running" }), ov(beat(8), [cmd]), NOW)).toEqual({ kind: "queued_online", amount: 5000, level: "balanced" }));
  it("only a stale heartbeat waits for the engine", () => {
    expect(startState(run({ status: "running" }), ov(beat(600), [cmd]), NOW).kind).toBe("queued_waiting");
    expect(startState(run({ status: "running" }), ov([], [cmd]), NOW).kind).toBe("queued_waiting");
    expect(startState(run({ status: "running" }), ov(beat(8, { state: "stopped" }), [cmd]), NOW).kind).toBe("queued_waiting");
  });
  it("a running run with nothing queued, a replay, or no overview: nothing", () => {
    expect(startState(run({ status: "running" }), ov(beat(8)), NOW).kind).toBe("none");
    expect(startState(run({ kind: "replay" }), ov(beat(8), [cmd]), NOW).kind).toBe("none");
    expect(startState(run(), undefined, NOW).kind).toBe("none");
  });
  it("a bad next_open is left out", () =>
    expect(startState(run(), ov(beat(8, { state: "market_closed", next_open: "soon" })), NOW)).toMatchObject({ nextOpen: null }));
  it("engineOnline: 90 seconds", () => {
    expect(engineOnline(ov(beat(90)), NOW)).toBe(true);
    expect(engineOnline(ov(beat(91)), NOW)).toBe(false);
  });
});
