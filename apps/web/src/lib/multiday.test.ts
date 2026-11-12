import { describe, expect, it } from "vitest";
import { MOCK_BOOK, bookTotals } from "./multiday";
import src from "./multiday.ts?raw";

describe("multi-day contract draft (#117)", () => {
  it("is marked as a draft to replace", () => expect(src).toMatch(/CONTRACT DRAFT, replace when #117 defines kb_\* RPCs/));
  it("adds up: holdings = invested, invested + cash = pot, shares by rule match", () => {
    const t = bookTotals(MOCK_BOOK);
    expect(t.invested).toBeCloseTo(MOCK_BOOK.mtm.invested, 6);
    expect(t.equity).toBeCloseTo(MOCK_BOOK.mtm.equity, 6);
    expect(t.gross).toBeCloseTo(MOCK_BOOK.exposure.gross, 6);
    expect(t.gross).toBeLessThanOrEqual(MOCK_BOOK.exposure.cap);
    for (const r of MOCK_BOOK.exposure.by_rule) expect(t.byRule.find((x) => x.rule === r.rule)?.value).toBeCloseTo(r.value, 6);
    expect(t.stopsInPlace).toBe(3);
  });
  it("lets two rules hold the same stock", () => {
    const aapl = MOCK_BOOK.positions.filter((p) => p.symbol === "AAPL").map((p) => p.rule);
    expect(new Set(aapl).size).toBe(2);
  });
  it("is not used by any screen yet (the multi-day screens wait for design approval)", () => {
    const files = import.meta.glob(["../pages/*.tsx", "../components/*.tsx", "../main.tsx"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    for (const [f, s] of Object.entries(files)) expect(s, f).not.toMatch(/lib\/multiday/);
  });
});
