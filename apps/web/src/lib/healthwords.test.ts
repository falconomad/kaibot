import { describe, expect, it } from "vitest";
import { haltText } from "./derive";
import { clearedAt, clearedFrom, clockSkewText, commandEventText, haltClearedText } from "./healthwords";

describe("why a halt ended (#483)", () => {
  it("the session start, which sends only the kind and the day", () => {
    expect(haltClearedText({ kind: "broker_rejects", day: "2026-11-23" })).toBe("Halt cleared at the new session");
    expect(haltClearedText({ kind: "engine_errors", day: "2026-11-23" })).toBe("Halt cleared at the new session");
  });
  it("five clean checks in the same session never read as a new session", () => {
    const t = haltClearedText({ kind: "engine_errors", clean_ticks: 5, day: "2026-11-23", why: "5 clean ticks in a row" });
    expect(t).toBe("Halt cleared after 5 checks in a row without an error: buys are allowed again");
    expect(t).not.toContain("new session");
    expect(t).not.toContain("tick");
  });
  it("a drift halt lifted because the books agree again", () => {
    expect(haltClearedText({ kind: "drift", day: "2026-11-23", why: "a late corporate action was adopted" }))
      .toBe("Halt cleared: the books agree with the broker again");
  });
  it("a reason this page does not know claims nothing", () => {
    expect(haltClearedText({ kind: "llm_budget", why: "something new" })).toBe("Halt cleared");
  });
  it("a payload that is missing or not what the engine sends", () => {
    expect(haltClearedText(null)).toBe("Halt cleared at the new session");
    expect(haltClearedText({ kind: "engine_errors", clean_ticks: "5" })).toBe("Halt cleared at the new session");
    expect(haltClearedText({ kind: 7, why: { a: 1 } })).toBe("Halt cleared at the new session");
  });
});

describe("the engine's clock against the broker's (#435)", () => {
  it("says which way and by how much", () => {
    expect(clockSkewText({ state: "skewed", seconds: -42.4 })).toBe("The engine's clock is 42 seconds behind the broker's: new orders wait until they agree");
    expect(clockSkewText({ state: "skewed", seconds: 300 })).toBe("The engine's clock is 5 minutes ahead of the broker's: new orders wait until they agree");
  });
  it("the end reads as the end", () => {
    expect(clockSkewText({ state: "ok", limit_s: 5 })).toBe("The engine's clock agrees with the broker's again: new orders go out");
  });
  it("an unreadable broker clock, and a payload without a state", () => {
    expect(clockSkewText({ state: "unreadable", broker: "None" })).toBe("The broker's clock can't be read: new orders wait until it can");
    expect(clockSkewText({})).toBe("The engine's clock differs from the broker's: new orders wait until they agree");
    expect(clockSkewText({ state: "skewed", seconds: "x" })).toBe("The engine's clock differs from the broker's: new orders wait until they agree");
  });
});

describe("a command that waits or was refused (#427, #440)", () => {
  it("waiting", () => {
    expect(commandEventText("command_waiting", { kind: "flatten_and_stop", why: "flatten and stop pending" }))
      .toBe("Flatten and stop is waiting: the sale is not complete yet, buys are blocked");
    expect(commandEventText("command_waiting", { kind: "new_run" }))
      .toBe("The new run is waiting: the old run's sale is not complete yet, buys are blocked");
  });
  it("refused: a known kind is named, an unknown one is never echoed", () => {
    expect(commandEventText("command_rejected", { kind: "new_run", reason: "amount 5 is below 100" })).toBe("The engine refused your request to start a new run");
    expect(commandEventText("command_rejected", { kind: "drop_tables" })).toBe("The engine refused a command it does not know");
    expect(commandEventText("command_rejected", { kind: "constructor" })).toBe("The engine refused a command it does not know");
    expect(commandEventText("command_rejected", {})).toBe("The engine refused a command it does not know");
  });
  it("a command the engine applied is left to the caller", () => {
    expect(commandEventText("command_pause", {})).toBeNull();
    expect(commandEventText("halt", { kind: "new_run" })).toBeNull();
  });
});

describe("a halt row with a later clear of its kind is over", () => {
  const ev = (type: string, ts: string, payload: Record<string, unknown>) => ({ type, ts, run_id: 1, payload });
  const events = [ev("halt_cleared", "2026-11-23T15:06:00Z", { kind: "engine_errors", clean_ticks: 5 }),
    ev("halt", "2026-11-23T15:00:00Z", { kind: "engine_errors" }), ev("halt_cleared", "2026-11-23T14:30:00Z", { kind: "drift" }),
    ev("halt_cleared", "2026-11-23T16:00:00Z", {})];
  const cleared = clearedFrom(events);

  it("reads only halt_cleared events that name a kind", () => {
    expect(cleared).toEqual([{ kind: "engine_errors", ts: "2026-11-23T15:06:00Z" }, { kind: "drift", ts: "2026-11-23T14:30:00Z" }]);
    expect(clearedFrom(undefined)).toEqual([]);
  });
  it("same kind, later: cleared at that time", () => {
    expect(clearedAt({ kind: "engine_errors", ts: "2026-11-23T15:00:00Z" }, cleared)).toBe("2026-11-23T15:06:00Z");
  });
  it("an earlier clear, another kind, or a second halt after the clear: still in force", () => {
    expect(clearedAt({ kind: "drift", ts: "2026-11-23T15:00:00Z" }, cleared)).toBeNull();
    expect(clearedAt({ kind: "broker_rejects", ts: "2026-11-23T15:00:00Z" }, cleared)).toBeNull();
    expect(clearedAt({ kind: "engine_errors", ts: "2026-11-23T15:30:00Z" }, cleared)).toBeNull();
    expect(clearedAt({ kind: "engine_errors", ts: "not a time" }, cleared)).toBeNull();
  });
});

describe("the replicator's two halt rows read in plain words", () => {
  it.each(["replica_conflict", "replica_poison"])("%s", (kind) => {
    const t = haltText({ kind, reason: "Supabase refuses fill rows (23514): skipped, the journal keeps them (docs/ops/ledger.md)", ts: "2026-11-23T15:00:00Z" });
    expect(t).not.toMatch(/replica|Supabase|docs\//);
    expect(t).toContain("trading goes on");
  });
});
