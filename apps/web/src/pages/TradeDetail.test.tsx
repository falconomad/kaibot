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
    expect(html).toContain('href="/trades"');
    expect(glitches(html)).toEqual([]);
  });

  it("error: a plain sentence with the server's message and a retry", () => {
    const html = render(<TradeDetailPage />, { ...at, seeds: [{ key, error: boom() }] });
    expect(hasAlert(html)).toBe(true);
    expect(text(html)).toContain("This trade couldn't load.");
    expect(text(html)).toContain("permission denied for function");
    expect(text(html)).toContain("Try again");
  });
});
