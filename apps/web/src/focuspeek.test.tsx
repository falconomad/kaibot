import { describe, expect, it } from "vitest";
import src from "./pages/Strategy.tsx?raw";
import css from "./app.css?raw";
import tokens from "./v4tokens.css?raw";

describe("focus groups open to show their stocks (17 Nov 2026)", () => {
  it("a tap on a theme opens its stocks in a sheet; the round button alone adds or removes it", () => {
    expect(src).toContain('className="kb-tcard-open" aria-haspopup="dialog"');
    expect(src).toContain('className="kb-tcheck" aria-pressed={c.on}');
    expect(src).toContain("<ThemeSheet card={peekCard}");
    expect(src).toMatch(/<StockCell symbol=\{s\}/);
  });
  it("the stocks rise in one after another, and stand still with reduced motion", () => {
    expect(css).toMatch(/\.kb-peek li \{[^}]*animation: kb-rise/);
    expect(css).toContain("@media (prefers-reduced-motion: reduce) { .kb-peek li { animation: none; } }");
  });
  it("text is one step larger: captions 13 px, small 15 px, body 17 px", () => {
    expect(tokens).toContain("--t-md: 17px; --t-sm: 15px; --t-cap: 13px;");
    expect(css).not.toMatch(/font-size: 1[01]px/);
  });
});
