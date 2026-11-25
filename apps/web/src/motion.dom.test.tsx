// @vitest-environment jsdom
// "Pause animations" (audit, WCAG 2.2.2; lib/motion.ts): Settings' switch stops the logo's idle hop and arm, at once and
// for the next visit; reduced motion stops them too; the default stays on; a browser without storage keeps working.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Logo } from "./components/Logo";
import { MotionCard } from "./components/MotionCard";
import { PAUSE_KEY, animationsPaused, resetMotionMemo, setAnimationsPaused } from "./lib/motion";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
let reduce = false;
const changes = new Set<() => void>();
beforeEach(() => {
  reduce = false; changes.clear(); localStorage.clear(); resetMotionMemo();
  window.matchMedia = ((q: string) => ({ get matches() { return reduce && /prefers-reduced-motion/.test(q); }, media: q, onchange: null,
    addEventListener: (_: string, f: () => void) => changes.add(f), removeEventListener: (_: string, f: () => void) => changes.delete(f),
    addListener() {}, removeListener() {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
afterEach(async () => { await act(async () => root?.unmount()); root = null; vi.restoreAllMocks(); });
async function mount(ui: React.ReactElement) {
  document.body.innerHTML = '<div id="r"></div>';
  root = createRoot(document.getElementById("r")!);
  await act(async () => root!.render(ui));
}
const hops = () => !!document.querySelector("svg.logo-hop") && !!document.querySelector("svg.logo-arm");

describe("Pause animations", () => {
  it("by default the idle motion plays and the switch is off", async () => {
    await mount(<><Logo idle /><MotionCard /></>);
    expect(hops()).toBe(true);
    expect(document.querySelector('[role="switch"]')!.getAttribute("aria-checked")).toBe("false");
  });
  it("the switch stops the hop and the arm at once, is kept in localStorage, and turns them back on", async () => {
    await mount(<><Logo idle /><MotionCard /></>);
    const sw = document.querySelector<HTMLButtonElement>('[role="switch"]')!;
    expect(sw.getAttribute("aria-labelledby")).toBe("motion-h");
    await act(async () => sw.click());
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(hops()).toBe(false);
    expect(document.querySelector("svg.logo")).not.toBeNull(); // the logo itself stays, still
    expect(localStorage.getItem(PAUSE_KEY)).toBe("1");
    await act(async () => sw.click());
    expect(hops()).toBe(true);
    expect(localStorage.getItem(PAUSE_KEY)).toBeNull();
  });
  it("a saved choice holds on the next visit", async () => {
    localStorage.setItem(PAUSE_KEY, "1");
    await mount(<Logo idle />);
    expect(hops()).toBe(false);
  });
  it("reduced motion stops it too, also when it is turned on while the page is open", async () => {
    await mount(<Logo idle />);
    expect(hops()).toBe(true);
    reduce = true;
    await act(async () => { for (const f of [...changes]) f(); });
    expect(hops()).toBe(false);
  });
  it("storage that throws (private mode, blocked): the default, no error, and the switch still works for this page", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
    expect(animationsPaused()).toBe(false);
    expect(() => setAnimationsPaused(true)).not.toThrow();
    await mount(<Logo idle />);
    expect(hops()).toBe(false);
  });
});
