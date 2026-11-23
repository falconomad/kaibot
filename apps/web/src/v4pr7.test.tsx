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
    expect(renderToStaticMarkup(<Tick value={5} className="gain">+$5.00</Tick>)).toBe('<span class="tk gain">+$5.00</span>');
    expect(TINT_MS).toBe(680); // 120 ms in, 560 ms out (lib/tick.ts)
  });
  it("the wash is opacity only (in, then out, 680 ms) in the up/down tints; the roll is transform, opacity and the colour's return, no scale", () => {
    expect(props(keyframes("kb-wash"))).toEqual(["opacity", "opacity", "opacity"]);
    for (const k of ["kb-roll-up", "kb-roll-down"]) {
      expect(props(keyframes(k)).every((p) => ["transform", "opacity", "color"].includes(p)), k).toBe(true);
      expect(keyframes(k)).not.toMatch(/scale|100%/);           // no 100%: it ends on the figure's own colour
    }
    expect(keyframes("kb-roll-up")).toMatch(/color: var\(--up\)/);
    expect(keyframes("kb-roll-down")).toMatch(/color: var\(--down\)/);
    expect(css).toMatch(/\.tk > \.tk-fx \{[^}]*opacity: 0; animation: kb-wash 680ms/);
    expect(css).toMatch(/\.tint-up > \.tk-fx \{ background: var\(--up-tint\); \} \.tint-down > \.tk-fx \{ background: var\(--down-tint\); \}/);
    expect(css).toMatch(/\.tint-up > \.tk-mid \{ animation: kb-roll-up 680ms/);
    // colour is never the only cue: an arrow, drawn by CSS (so it is not in the figure's text) and hidden from readers
    expect(css).toMatch(/\.tint-up > \.tk-fx::after \{ content: "\\25B2"; color: var\(--up\); \} \.tint-down > \.tk-fx::after \{ content: "\\25BC"; color: var\(--down\); \}/);
    expect(css).toMatch(/\.tk:is\(\.gain, \.loss, \.flat\) > \.tk-fx::after \{ content: none; \}/); // no second arrow
    // reduced motion: all of it is animation, so the global rule below turns it off and the figure just changes
    expect(css).not.toMatch(/\.tint-(up|down)[^{]*\{[^}]*color: var\(--(up|down)\) !important/);
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
