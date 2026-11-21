import { describe, expect, it, vi } from "vitest";
import type { Health as HealthResp, LlmBudget } from "../lib/api";
import { Health } from "./Health";
import { boom, glitches, hasAlert, hasStatus, render, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const quiet: HealthResp = {
  now: "2026-11-12T22:00:00Z", heartbeat: [], halts: [], events: [], pending_commands: [],
  deadman: { armed: false, jobs: [], runs: [] },
};
const budget: LlmBudget = {
  day: "2026-11-12", day_usd: 0, month_usd: 0, state: "ok", alert: false,
  caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {},
};

describe("Engine health page states", () => {
  it("loading: a skeleton", () => {
    const html = render(<Health />);
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });

  it("loading: the LLM budget card has its own placeholder while health is shown", () => {
    const html = render(<Health />, { seeds: [{ key: ["health"], data: quiet }] });
    expect(hasStatus(html)).toBe(true);
    expect(text(html)).toContain("LLM budget Loading…");
  });

  it("empty: no process, halt or log entry yet, each said in words", () => {
    const html = render(<Health />, { seeds: [{ key: ["health"], data: quiet }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    expect(t).toContain("No process has reported yet.");
    expect(t).toContain("No halts recorded.");
    expect(t).toContain("Nothing logged yet.");
    expect(t).toContain("No scan yet today");
    expect(t).toContain("Safety net Not armed");
    expect(t).toContain("Code version running now not reported");
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<Health />, { seeds: [{ key: ["health"], error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("Engine health couldn't load.");
    expect(text(html)).toContain("Try again");
  });

  it("error: only the LLM budget failed; the rest of the page still shows", () => {
    const html = render(<Health />, { seeds: [{ key: ["health"], data: quiet }, { key: ["llm_budget"], error: boom("budget view missing") }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("The LLM budget couldn't load.");
    expect(text(html)).toContain("budget view missing");
    expect(text(html)).toContain("Processes");
  });

  it("every engine health event of 0033 and 0036 reads in plain words, never by its code name", () => {
    const types = ["multi_day_carry", "corporate_actions_error", "ledger_mismatch", "reconcile_blocked", "dividend_difference",
      "venue_fee_unread", "replica_conflict", "stop_state_fallback", "engine_lease_takeover", "journal_adopted", "order_adopted",
      "session_finalized_late", "watchdog_protect", "multi_day_exit_recovered", "multi_day_shorting_on",
      "multi_day_column_missing", "multi_day_review", "command_accept_reconcile"];
    const events = types.map((type, i) => ({ type, ts: `2026-11-21T0${i % 10}:00:00Z`, run_id: 7, payload: {} }));
    const html = render(<Health />, { seeds: [{ key: ["health"], data: { ...quiet, events } }, { key: ["llm_budget"], data: budget }] });
    const t = text(html);
    for (const type of types) expect(t).not.toContain(type.replace(/_/g, " "));
    expect(t).toContain("Books differ from the broker: new buys stopped");
    expect(t).toContain("Live prices of held stocks wait for a database update");
    expect(t).toContain("Pre-open reviewer (can veto or halve buys)");
    expect(t).toContain("You asked to accept the differences between the books and the broker");
    expect(glitches(html)).toEqual([]);
  });
});
