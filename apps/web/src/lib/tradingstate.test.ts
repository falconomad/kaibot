// N1 / N2 (docs/design/ui_after_engine_v3.md): every state of the trading-state line, and "reason not reported".
import { describe, expect, it } from "vitest";
import type { Heartbeat, Overview, Run } from "./api";
import { JOURNAL_REASON, UNKNOWN, journalDamaged, tradingLine, tradingState, type HealthEvent } from "./tradingstate";

const run = (over: Partial<Run> = {}): Run => ({ id: 1, created_at: "2026-11-02T14:00:00Z", amount_usd: 10_000, status: "running",
  end_reason: null, peak_equity: null, kind: "paper", label: "Paper 1", level: "balanced", started_on: "2026-11-02", ended_on: null, ...over });
// Mon 23 Nov 2026 11:00 ET (17:00 CET): market hours
const OPEN = Date.parse("2026-11-23T16:00:00Z");
// Mon 23 Nov 2026 23:30 CET: the Mac's night
const NIGHT = Date.parse("2026-11-23T22:30:00Z");
const ago = (now: number, s: number) => new Date(now - s * 1000).toISOString();
const beat = (detail: Record<string, unknown>, now = OPEN, ageS = 5): Heartbeat =>
  ({ process: "engine", ts: ago(now, ageS), detail: { lease: "ok", account_clean: true, ...detail } as Heartbeat["detail"] });
type O = Pick<Overview, "heartbeat" | "pending_commands" | "halts_today">;
const ov = (b: Heartbeat | null, over: Partial<O> = {}): O => ({ heartbeat: b ? [b] : [], pending_commands: [], halts_today: [], ...over });
const ev = (type: string, ts: string, payload: Record<string, unknown> = {}): HealthEvent => ({ type, ts, payload });
const s = (r: Run, o: O, e?: HealthEvent[], now = OPEN) => tradingState(r, o, e, now);

describe("Buying", () => {
  it("state trading, lease ok, nothing blocks", () => {
    const x = s(run(), ov(beat({ state: "trading" })));
    expect([x.kind, x.label, x.tone, x.needsYou]).toEqual(["buying", "Buying", "ok", false]);
    expect(tradingLine(x)).toBe("Buying — nothing blocks new buys");
  });
});

describe("Holding", () => {
  it("managing with nothing blocking: the reason is not reported, never guessed", () => {
    const x = s(run(), ov(beat({ state: "managing" })));
    expect(tradingLine(x)).toBe(`Holding — ${UNKNOWN}`);
    expect(x.tone).toBe("calm");
  });
  it("managing with a rule note saying the market filter is off", () => {
    const md = { rules: { trend_momentum: { note: "carried buys dropped: market filter off" } } };
    expect(tradingLine(s(run(), ov(beat({ state: "managing", multi_day: md }))))).toBe("Holding — market filter off");
  });
  it("market closed, starting, error, idle, unknown state", () => {
    expect(s(run(), ov(beat({ state: "market_closed" }))).reason).toBe("market closed");
    expect(s(run(), ov(beat({ state: "preparing" }))).reason).toBe("starting up");
    const err = s(run(), ov(beat({ state: "error" })));
    expect([err.reason, err.tone]).toEqual(["an engine error, retrying", "warn"]);
    expect(s(run(), ov(beat({ state: "idle" }))).reason).toBe("no run trading");
    expect(s(run(), ov(beat({ state: "zzz" }))).reason).toBe("state not reported");
  });
  it("a heartbeat without the lease never earns Buying", () => {
    const b = beat({ state: "trading" });
    delete (b.detail as Record<string, unknown>).lease;
    const x = s(run(), ov(b));
    expect(x.kind).toBe("holding");
    expect(x.reason).toBe(`lease not reported · ${UNKNOWN}`);
  });
  it("a scheduled run waits for its first session", () => {
    expect(tradingLine(s(run({ status: "scheduled" }), ov(beat({ state: "market_closed" }))))).toBe("Holding — the run starts at the next session");
  });
});

describe("Selling only", () => {
  const line = (o: O, r = run(), e?: HealthEvent[]) => tradingLine(s(r, o, e));
  it("database unreachable at start (lease degraded)", () => {
    expect(line(ov(beat({ state: "managing", lease: "degraded" })))).toBe("Selling only — database unreachable at start");
  });
  it("lease unverified or lost", () => {
    expect(line(ov(beat({ state: "managing", lease: "unverified" })))).toBe("Selling only — engine lease not verified");
    expect(line(ov(beat({ state: "managing", lease: "lost" })))).toBe("Selling only — engine lease not verified");
  });
  it("paused by you, calm; paused after three daily stops, needs you", () => {
    const mine = s(run({ status: "paused" }), ov(beat({ state: "managing" })));
    expect([tradingLine(mine), mine.tone, mine.needsYou]).toEqual(["Selling only — paused by you", "calm", false]);
    const auto = s(run({ status: "paused" }), ov(beat({ state: "managing" }), { halts_today: [{ kind: "three_stops", reason: "3 stops", ts: ago(OPEN, 60) }] }));
    expect(auto.reason).toMatch(/three daily stops/);
    expect(auto.needsYou).toBe(true);
  });
  it("a pending flatten or new run", () => {
    expect(line(ov(beat({ state: "managing" }), { pending_commands: [{ kind: "flatten_and_stop", ts: ago(OPEN, 60), payload: {} }] })))
      .toBe("Selling only — selling everything, as you asked");
    expect(line(ov(beat({ state: "managing" }), { pending_commands: [{ kind: "new_run", ts: ago(OPEN, 60), payload: {} }] })))
      .toBe("Selling only — selling the old run for the new one");
  });
  // #607 QA: a calm reason (paused by you, a pending flatten or new run) must never hide a red one.
  describe("a red state wins over a calm reason", () => {
    const bad = { state: "managing", account_clean: false, reconcile: { ok: false } };
    const flatten = { pending_commands: [{ kind: "flatten_and_stop", ts: ago(OPEN, 60), payload: {} }] };
    it("paused by you, with account_clean false and reconcile not ok: the account problem, red", () => {
      const x = s(run({ status: "paused" }), ov(beat(bad)));
      expect([x.reason, x.tone, x.needsYou]).toEqual(["account holds positions or orders kaibo didn't place", "red", true]);
      const y = s(run({ status: "paused" }), ov(beat({ state: "managing", reconcile: { ok: false } })));
      expect([tradingLine(y), y.tone]).toEqual(["Selling only — books and broker disagree", "red"]);
    });
    it("a pending flatten_and_stop with the same flags: red, not 'selling everything, as you asked'", () => {
      const x = s(run(), ov(beat(bad), flatten));
      expect([x.reason, x.tone]).toEqual(["account holds positions or orders kaibo didn't place", "red"]);
      const y = s(run(), ov(beat({ state: "managing", reconcile: { ok: false } }), { pending_commands: [{ kind: "new_run", ts: ago(OPEN, 60), payload: {} }] }));
      expect([y.reason, y.tone]).toEqual(["books and broker disagree", "red"]);
    });
    it("an open halt or `halted` on a paused run, or under a pending flatten: the halt, red", () => {
      const halt = { halts_today: [{ kind: "daily_stop", reason: "day -2%", ts: ago(OPEN, 60) }] };
      expect(s(run({ status: "paused" }), ov(beat({ state: "managing" }), halt)).tone).toBe("red");
      expect(s(run(), ov(beat({ state: "managing" }), { ...halt, ...flatten })).tone).toBe("red");
      expect(s(run({ status: "paused" }), ov(beat({ state: "managing", halted: true }))).reason).toBe("halt: daily stop reached");
    });
    it("clock skew (amber) also wins over a calm pause or flatten", () => {
      const skew = [ev("clock_skew", ago(OPEN, 30), { state: "skewed" })];
      const x = s(run({ status: "paused" }), ov(beat({ state: "managing" })), skew);
      expect([x.reason, x.tone]).toEqual(["the engine's clock and the broker's disagree", "warn"]);
      expect(s(run(), ov(beat({ state: "managing" }), flatten), skew).tone).toBe("warn");
    });
    it("the three-stops halt that paused the run keeps its own words, but a red flag still wins over it", () => {
      const three = { halts_today: [{ kind: "three_stops", reason: "3 stops", ts: ago(OPEN, 60) }] };
      expect(s(run({ status: "paused" }), ov(beat({ state: "managing", halted: true }), three)).reason).toMatch(/three daily stops/);
      expect(s(run({ status: "paused" }), ov(beat({ state: "managing", account_clean: false }), three)).tone).toBe("red");
    });
  });
  it("a halt today, red; the same halt cleared by a later halt_cleared is not", () => {
    const halt = { kind: "drift", reason: "x", ts: ago(OPEN, 600) };
    const x = s(run(), ov(beat({ state: "managing" }), { halts_today: [halt] }));
    expect([x.kind, x.tone]).toEqual(["selling_only", "red"]);
    expect(x.reason).toMatch(/^halt: /);
    expect(s(run(), ov(beat({ state: "trading" }), { halts_today: [halt] }), [ev("halt_cleared", ago(OPEN, 60), { kind: "drift", why: "ok" })]).kind)
      .toBe("buying");
  });
  it("the daily stop flag, a dirty account, books that differ, clocks that disagree", () => {
    expect(line(ov(beat({ state: "managing", halted: true })))).toBe("Selling only — halt: daily stop reached");
    expect(line(ov(beat({ state: "managing", account_clean: false })))).toBe("Selling only — account holds positions or orders kaibo didn't place");
    expect(line(ov(beat({ state: "managing", reconcile: { ok: false, diffs: [] } })))).toBe("Selling only — books and broker disagree");
    expect(line(ov(beat({ state: "managing" })), run(), [ev("clock_skew", ago(OPEN, 60), { state: "skewed", seconds: 30 })]))
      .toBe("Selling only — the engine's clock and the broker's disagree");
    expect(line(ov(beat({ state: "trading" })), run(), [ev("clock_skew", ago(OPEN, 120), { state: "skewed", seconds: 30 }),
      ev("clock_skew", ago(OPEN, 60), { state: "ok" })])).toBe("Buying — nothing blocks new buys");
  });
});

describe("Stopped", () => {
  it("journal damaged: red, needs you, in market hours and at night", () => {
    const e = [ev("engine_start", ago(OPEN, 3600)), ev("engine_error", ago(OPEN, 600), { error: "JournalCorrupt: journal entry 4711 fails its checksum" }),
      ev("engine_stop", ago(OPEN, 590))];
    const stopped = beat({ state: "stopped" }, OPEN, 590);
    for (const now of [OPEN, NIGHT]) {
      const x = s(run(), ov(stopped), e, now);
      expect(tradingLine(x)).toBe(`Stopped — ${JOURNAL_REASON}`);
      expect([x.tone, x.needsYou, x.journal]).toEqual(["red", true, true]);
    }
  });
  it("a start after the damage ends it; another engine error after it too", () => {
    const bad = ev("engine_error", ago(OPEN, 600), { error: "JournalCorrupt: x" });
    expect(journalDamaged([bad, ev("engine_start", ago(OPEN, 60))])).toBe(false);
    expect(journalDamaged([bad, ev("engine_error", ago(OPEN, 60), { error: "TimeoutError" })])).toBe(false);
    expect(journalDamaged([ev("engine_error", ago(OPEN, 60), { error: "TimeoutError" })])).toBe(false);
    expect(journalDamaged([bad], beat({ state: "trading" }, OPEN, 5))).toBe(false); // live again
    expect(journalDamaged([bad], beat({ state: "stopped" }, OPEN, 5))).toBe(true);
    expect(journalDamaged(undefined)).toBe(false);
  });
  it("the run ended: by you, or with its own reason", () => {
    expect(tradingLine(s(run({ status: "stopped", end_reason: "stopped by user" }), ov(null)))).toBe("Stopped — stopped by you");
    expect(tradingLine(s(run({ status: "ended", end_reason: "book stop line reached" }), ov(null)))).toBe("Stopped — book stop line reached");
    expect(tradingLine(s(run({ status: "ended" }), ov(null)))).toBe("Stopped — the run has ended");
  });
});

describe("Not running", () => {
  it("in market hours: amber, needs you, the reason is not reported", () => {
    const x = s(run(), ov(beat({ state: "trading" }, OPEN, 600)));
    expect(tradingLine(x)).toBe(`Not running — ${UNKNOWN} · market open`);
    expect([x.tone, x.needsYou]).toEqual(["warn", true]);
    expect(s(run(), ov(null)).reason).toBe("never connected · market open");
  });
  it("at night: asleep, calm", () => {
    const x = s(run(), ov(beat({ state: "stopped" }, NIGHT, 3600)), [], NIGHT);
    expect(x.kind).toBe("not_running");
    expect(x.reason).toMatch(/^asleep · starts /);
    expect([x.tone, x.needsYou]).toEqual(["calm", false]);
  });
});
