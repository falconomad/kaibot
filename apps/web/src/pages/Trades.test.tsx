import { describe, expect, it, vi } from "vitest";
import { Trades } from "./Trades";
import { boom, glitches, hasAlert, hasStatus, render, text, zeros } from "./testkit";

vi.mock("../lib/api", () => import("./testkit-api"));

const key = ["trades", 1, 0];

describe("Trades page states", () => {
  it("loading: a skeleton, and no trade count yet", () => {
    const html = render(<Trades />);
    expect(hasStatus(html)).toBe(true);
    expect(zeros(html)).toEqual([]);
    expect(text(html)).not.toMatch(/total/);
  });

  it("empty: says there are no trades and what will appear", () => {
    const html = render(<Trades />, { seeds: [{ key, data: { total: 0, rows: [] } }] });
    expect(text(html)).toContain("No trades in this run yet.");
    expect(text(html)).toContain("0 total");
    expect(hasStatus(html)).toBe(false);
    expect(glitches(html)).toEqual([]);
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<Trades />, { seeds: [{ key, error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("Trades couldn't load.");
    expect(text(html)).toContain("permission denied for function");
    expect(text(html)).toContain("Try again");
    expect(text(html)).not.toMatch(/total/);
  });
});
