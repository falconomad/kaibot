import { describe, expect, it, vi } from "vitest";
import { TradeDetailPage } from "./TradeDetail";
import { boom, glitches, hasAlert, hasStatus, render, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const at = { path: "/trades/7", route: "/trades/:id" };
const key = ["trade", "7"];

describe("Trade detail page states", () => {
  it("loading: the detail skeleton", () => {
    const html = render(<TradeDetailPage />, at);
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
  });

  it("empty: an unknown trade says so and links back to all trades", () => {
    const html = render(<TradeDetailPage />, { ...at, seeds: [{ key, data: null }] });
    expect(text(html)).toContain("Trade not found.");
    expect(html).toContain('href="/positions"');
    expect(glitches(html)).toEqual([]);
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<TradeDetailPage />, { ...at, seeds: [{ key, error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("This trade couldn't load.");
    expect(text(html)).toContain("permission denied for function");
    expect(text(html)).toContain("Try again");
  });

  it("a loaded trade shows the stock's plain-words summary, or says there is none yet", () => {
    const run = { id: 1, created_at: "2026-11-02T14:00:00Z", amount_usd: 10000, status: "running", end_reason: null, peak_equity: null, kind: "paper", label: "Paper 1", level: "balanced", started_on: "2026-11-02", ended_on: null };
    const trade = { id: 7, run_id: 1, signal_id: null, strategy: "spy_late_momentum", symbol: "AAPL", entry_ts: "2026-11-17T15:00:00Z", exit_ts: null, qty: 3,
      entry_px: 200, exit_px: null, stop_px: 195, target_px: 210, risk_usd: 15, exit_reason: null, pnl_usd: null, verdict: null };
    const d = { trade, run, gate: null, room: null, fills: [] };
    const html = render(<TradeDetailPage />, { ...at, seeds: [{ key, data: d }, { key: ["stockSummary", "AAPL"], data: null }] });
    expect(text(html)).toContain("In plain words");
    expect(text(html)).toContain("No summary for AAPL yet");
    expect(glitches(html)).toEqual([]);
  });
});
