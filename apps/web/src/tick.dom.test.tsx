// @vitest-environment jsdom
// components/Tick.tsx in a browser DOM: the figure changes in place (same element), only the changed characters are
// marked, a burst is coalesced to one tick per 250 ms with the latest value, an equal value does not tick, and the
// arrow is drawn by CSS (not in the text) and hidden from screen readers. Memoised rows: a streamed price re-renders
// only the row that moved.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Tick } from "./components/Tick";
import { money } from "./lib/fmt";
import homeSrc from "./components/Home.tsx?raw";
import posSrc from "./pages/Positions.tsx?raw";
import css from "./app.css?raw";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
const show = (v: number | null, from?: string) => act(() => root.render(<Tick value={v} from={from} className="gain">{v === null ? "—" : money(v)}</Tick>));
const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const el = () => document.querySelector("#app > span")!;
const mid = () => document.querySelector(".tk-mid")?.textContent ?? null;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-23T15:00:00Z"));
  document.body.innerHTML = '<div id="app"></div>';
  root = createRoot(document.getElementById("app")!);
});
afterEach(() => { act(() => root.unmount()); vi.useRealTimers(); });

describe("the tick", () => {
  it("the first render does not tick; a move ticks in place on the changed digits only, then is plain again", async () => {
    await show(3173.6);
    const first = el();
    expect(first.className).toBe("tk gain");
    expect(document.querySelector(".tk-fx")).toBeNull();
    await show(3173.65);
    expect(el()).toBe(first);                                  // the same element: nothing remounts or shifts
    expect(el().className).toBe("tk gain tint-up");
    expect(el().textContent).toBe("$3,173.65");                // the arrow is CSS, not text
    expect(mid()).toBe("5");
    expect(document.querySelector(".tk-fx")?.getAttribute("aria-hidden")).toBe("true");
    expect(el().closest("[aria-live]")).toBeNull();
    await pass(679);
    expect(el().className).toBe("tk gain tint-up");
    await pass(1);
    expect(el().className).toBe("tk gain");
    expect(document.querySelector(".tk-fx")).toBeNull();
    expect(el().textContent).toBe("$3,173.65");
  });

  it("down is marked down", async () => {
    await show(100);
    await show(99.5);
    expect(el().className).toContain("tint-down");
    expect(mid()).toBe("99.50");
  });

  it("a burst: one tick at once, the rest wait for the window's end and the latest value wins", async () => {
    await show(100);
    await show(101);
    expect(el().textContent).toBe("$101.00");
    await pass(50);
    await show(102);
    await show(98);                                            // inside the window: held, the shown figure stays
    expect(el().textContent).toBe("$101.00");
    expect(el().className).toContain("tint-up");
    await pass(200);                                           // 250 ms after the first tick
    expect(el().textContent).toBe("$98.00");
    expect(el().className).toContain("tint-down");             // against what was shown, not against 102
  });

  it("an equal value, or one that formats the same, does not tick", async () => {
    await show(100);
    await show(100);
    expect(el().className).toBe("tk gain");
    await show(100.001);                                       // "$100.00" again
    expect(el().className).toBe("tk gain");
    expect(document.querySelector(".tk-fx")).toBeNull();
  });

  it("a change of source replaces the figure at once and never ticks", async () => {
    await show(100, "engine");
    await show(120, "live");
    expect(el().textContent).toBe("$120.00");
    expect(el().className).toBe("tk gain");
  });
});

describe("the tick never moves the layout", () => {
  // Read from the stylesheet as the browser cascades it (jsdom), mid-tick: the wash and its arrow are taken out of the
  // flow, the figure keeps tabular figures, and nothing that sets a box's size is touched by a tick rule or keyframe.
  const tickRules = [...css.matchAll(/(?:^|\n)([^{}@\n]*\btk(?:-fx|-mid)?\b[^{}]*)\{([^}]*)\}/g)].map((m) => [m[1].trim(), m[2]] as const);
  const keyframes = (name: string) => new RegExp(`@keyframes ${name} \\{([^@]*?)\\}\\s*\\}`).exec(css)?.[1] ?? "";
  const SIZE = /(?:^|;)\s*(?:width|min-width|max-width|height|padding[a-z-]*|margin(?!-right: 1px)[a-z-]*|font-size(?!: 12px)|font-weight|letter-spacing|border(?!-radius)[a-z-]*|display(?!: inline-block))\s*:/;

  it("mid-tick, the wash and the arrow are absolutely positioned and the figure keeps tabular figures", async () => {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    try {
      await show(3173.6);
      await show(3173.65);
      const fx = document.querySelector(".tk-fx")!;
      expect(getComputedStyle(fx).position).toBe("absolute");
      expect(getComputedStyle(fx).pointerEvents).toBe("none");
      expect(getComputedStyle(el()).position).toBe("relative");
      expect(getComputedStyle(el()).fontVariantNumeric).toBe("tabular-nums");
      expect(getComputedStyle(document.querySelector(".tk-mid")!).display).toBe("inline-block");
      expect((document.querySelector(".tk-mid") as HTMLElement).getAttribute("style")).toBeNull();
    } finally { style.remove(); }
  });
  it("in the stylesheet: the wash and the arrow are out of the flow, no tick rule sizes a box, the keyframes only paint", () => {
    expect(tickRules.length).toBeGreaterThanOrEqual(8);
    const rule = (sel: string) => tickRules.find(([s]) => s === sel)?.[1] ?? "";
    expect(rule(".tk")).toMatch(/position: relative;[^}]*font-variant-numeric: tabular-nums/);
    expect(rule(".tk > .tk-fx")).toMatch(/position: absolute; inset: /);
    expect(rule(".tk > .tk-fx::after")).toMatch(/position: absolute;/);
    for (const [sel, body] of tickRules) {
      expect(body, sel).not.toMatch(sel === ".tk" ? /position: (static|sticky)/ : /position: (static|relative|sticky)/);
      expect(body, sel).not.toMatch(SIZE);
    }
    for (const k of ["kb-wash", "kb-roll-up", "kb-roll-down"]) {
      const props = [...keyframes(k).matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
      expect(props.length, k).toBeGreaterThan(0);
      expect(props.every((p) => ["opacity", "transform", "color"].includes(p)), k).toBe(true);
    }
  });
});

describe("rows are memoised", () => {
  it("Home's holding rows and Positions' open rows are memo components fed with primitives", () => {
    expect(homeSrc).toMatch(/const HoldRow = memo\(function HoldRow/);
    expect(homeSrc).toMatch(/<HoldRow key=\{r\.key\} r=\{r\}/);
    expect(homeSrc).toMatch(/\}, \(a, b\) => JSON\.stringify\(a\) === JSON\.stringify\(b\)\);/);
    expect(posSrc).toMatch(/const OpenRow = memo\(function OpenRow/);
    expect(posSrc).toMatch(/const openSheet = useCallback\(/);
  });
});
