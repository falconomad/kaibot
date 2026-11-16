// The corporate-action events of #271 and #275 in plain words, in Activity and on Engine. They reach the dashboard
// once migration 0021 (PR #274) lets kb_health return them; until then nothing new shows.
import { describe, expect, it, vi } from "vitest";
import { caEvent, caEvents } from "./lib/corpactions";
import { Tape } from "./pages/Tape";
import { Health } from "./pages/Health";
import { ctxFor, paperRun, render, text } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const ev = (type: string, payload: Record<string, unknown>, ts = "2026-11-17T14:31:00Z", run_id: number | null = 1) => ({ type, ts, run_id, payload });
const EVENTS = [
  ev("multi_day_ca_unread", { strategy: "trend_momentum", session: "2026-11-17", why: "HTTP 503", not_bought: ["AAPL"], text: "no new buys this session: HTTP 503" }, "2026-11-17T14:01:00Z"),
  ev("spin_off_adopted", { parent: "HON", symbol: "NEWCO", qty: 120, cost_basis: 3100.5, ex_date: "2026-11-17", ref: "x", action: "sold at the next closing auction" }, "2026-11-17T14:32:00Z"),
  ev("symbol_change_adopted", { symbol: "OLD", new_symbol: "NEW", qty: 4, ex_date: "2026-11-17", ref: "y" }, "2026-11-17T14:33:00Z"),
  ev("corporate_action_not_adopted", { kind: "spin_off", symbol: "GE", new_symbol: "GEV", why: "the venue holds 3 shares of GEV, the ledger 0", action: "entries stay blocked" }, "2026-11-17T14:34:00Z"),
  ev("multi_day_ca_skip", { strategy: "trend_momentum", symbol: "NVDA", action: "split", on: "2026-11-20", text: "skipped: corporate action on 2026-11-20" }, "2026-11-17T14:35:00Z"),
];

describe("the words", () => {
  it("each event reads as Product asked", () => {
    expect(caEvents(EVENTS).map((c) => [c.head, c.tone])).toEqual([
      ["Couldn't read upcoming splits and spin-offs: no new buys today", "warn"],
      ["Added 120 shares of NEWCO from the HON spin-off: sells at tonight's close", "note"],
      ["Renamed: OLD is now NEW", "note"],
      ["Couldn't take in the GE spin-off: no new buys until it's checked", "alert"],
      ["Skipped NVDA: split on Nov 20, 2026", "note"]]);
    expect(caEvent(EVENTS[3])!.detail).toContain("the venue holds 3 shares of GEV, the ledger 0");
  });
  it("other events and payloads that don't fit are left alone", () => {
    expect(caEvent(ev("session_start", {}))).toBeNull();
    expect(caEvent(ev("spin_off_adopted", { parent: "HON" }))).toBeNull();
    expect(caEvent(ev("symbol_change_adopted", { symbol: "OLD" }))).toBeNull();
    expect(caEvent(ev("spin_off_adopted", { parent: "HON", symbol: "NEWCO" }))!.head).toBe("Added NEWCO from the HON spin-off: sells at tonight's close");
  });
});

describe("Activity", () => {
  const health = (events: unknown[]) => ({ key: ["health"], data: { now: "", heartbeat: [], halts: [], events, deadman: { armed: true, jobs: [], runs: [] }, pending_commands: [] } });
  const tape = { key: ["tape", 1], data: [], infinite: true };
  it("shows the run's corporate-action events as rows; the one that blocks buys reads as an alert", () => {
    const html = render(<Tape />, { ctx: ctxFor(paperRun()), seeds: [tape, health([...EVENTS, ev("spin_off_adopted", { parent: "X", symbol: "OTHER", qty: 1 }, "2026-11-17T15:00:00Z", 2)])] });
    const t = text(html);
    for (const h of caEvents(EVENTS).map((c) => c.head)) expect(t).toContain(h);
    expect(t).not.toContain("OTHER");
    expect(html).toMatch(/<button type="button" class="row alert"[^>]*>.*?Couldn&#x27;t take in the GE spin-off/);
  });
  it("before 0021: kb_health doesn't send them, and nothing new shows", () => {
    expect(text(render(<Tape />, { ctx: ctxFor(paperRun()), seeds: [tape, health([])] }))).not.toMatch(/spin-off|Renamed|splits/);
  });
});

describe("Engine's log", () => {
  it("labels the same events in the same words", () => {
    const data = { now: "", heartbeat: [], halts: [], events: EVENTS, deadman: { armed: true, jobs: [], runs: [] }, pending_commands: [] };
    const t = text(render(<Health />, { ctx: ctxFor(paperRun()), seeds: [{ key: ["health"], data }] }));
    for (const h of ["Couldn't read upcoming splits and spin-offs: no new buys today", "Added 120 shares of NEWCO from the HON spin-off: sells at tonight's close",
      "Renamed: OLD is now NEW"]) expect(t).toContain(h);
    expect(t).not.toMatch(/spin off adopted|symbol change adopted/);
  });
});
