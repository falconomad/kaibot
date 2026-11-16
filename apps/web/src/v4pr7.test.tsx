// v4 PR 7: motion polish (docs/design/mobile_first_v4.md, "Motion"): number tick, sheet spring, live pulse, route fades,
// all off with reduced motion.
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Tick, TINT_MS, tickDir } from "./components/Tick";
import { navKind } from "./components/Shell";
import css from "./app.css?raw";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

const keyframes = (name: string) => new RegExp(`@keyframes ${name} \\{([^@]*?)\\}\\s*\\}`).exec(css)?.[1] ?? "";
const props = (body: string) => [...body.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);

describe("number tick", () => {
  it("only a changed figure ticks: up, down, never on the first render or on unknown values", () => {
    expect(tickDir(100, 101)).toBe("up");
    expect(tickDir(100, 99.5)).toBe("down");
    expect(tickDir(100, 100)).toBeNull();
    expect(tickDir(undefined, 100)).toBeNull();
    expect(tickDir(100, null)).toBeNull();
    expect(tickDir(NaN, 1)).toBeNull();
    expect(renderToStaticMarkup(<Tick value={5} className="gain">+$5.00</Tick>)).toBe('<span class="gain">+$5.00</span>');
    expect(TINT_MS).toBe(600);
  });
  it("the fade is opacity only in 200 ms; the tint is a class (colour), so it stays with reduced motion", () => {
    expect(props(keyframes("kb-tick"))).toEqual(["opacity", "opacity"]);
    expect(css).toMatch(/\.tick \{ animation: kb-tick var\(--d-base\)/);
    expect(css).toMatch(/\.tint-up, \.tint-up \* \{ color: var\(--up\) !important; \}/);
    expect(css).toMatch(/\.tint-down, \.tint-down \* \{ color: var\(--down\) !important; \}/);
  });
});

describe("sheet spring, route fades, live pulse", () => {
  it("the sheet springs with transform only, and a strip under it covers the overshoot on phones", () => {
    const k = keyframes("kb-sheet-in");
    expect(props(k).every((p) => p === "transform")).toBe(true);
    expect(k).toMatch(/70% \{ transform: translateY\(-1\.5%\); \}/);
    expect(css).toMatch(/\.sheet::after \{[^}]*top: 100%; height: 24px; background: var\(--surface\)/);
  });
  it("a tab fades; a detail page is pushed in from the right, transform and opacity only", () => {
    expect(navKind("/")).toBe("tab");
    expect(navKind("/positions")).toBe("tab");
    expect(navKind("/positions/12")).toBe("push");
    expect(navKind("/strategy/fine-tune")).toBe("push");
    expect(props(keyframes("kb-push")).every((p) => p === "transform" || p === "opacity")).toBe(true);
    expect(css).toMatch(/main\[data-nav="push"\] > \* \{ animation: kb-push 240ms/);
  });
  it("the live pulse is transform and opacity on a pseudo-element, and stops with reduced motion", () => {
    expect(props(keyframes("kb-pulse")).every((p) => p === "transform" || p === "opacity")).toBe(true);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{ \.live-dot::after \{ display: none; \} \}/);
  });
  it("reduced motion turns every animation and transition off", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important; transition: none !important; \}/);
  });
});
