// @vitest-environment jsdom
// The logo's idle motion (stakeholder, 24-25 Nov 2026), mounted in jsdom: the point's hop and the arm's redraw are
// added only when the intro has ended, both pause while the tab is hidden, and with reduced motion neither comes. The
// keyframes and the 4 s cadence are checked against app.css in brand.test.tsx; that they run on the compositor was
// checked in headless Chromium (PR).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Logo, ROLES, mark, resetLogoMotion } from "./components/Logo";
import { drawnFrom } from "./brand/mark";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
const motion = (reduce: boolean) => {
  window.matchMedia = ((q: string) => ({ matches: reduce && /prefers-reduced-motion/.test(q), media: q, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false })) as typeof window.matchMedia;
};
async function mount(ui: React.ReactElement) {
  document.body.innerHTML = '<div id="r"></div>';
  root = createRoot(document.getElementById("r")!);
  await act(async () => root!.render(ui));
}
const hidden = async (h: boolean) => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => h });
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
};
afterEach(async () => { await act(async () => root?.unmount()); root = null; await hidden(false); });

describe("the logo's idle motion", () => {
  it("does not start before the intro has finished: the hop and the arm come only once the pulse has ended", async () => {
    motion(false); resetLogoMotion();
    await mount(<Logo animate idle />);
    expect(document.querySelector(".logo-moving")).not.toBeNull();
    expect(document.querySelector(".logo-hop, .logo-arm, .logo-box")).toBeNull();
    await act(async () => { document.querySelector(".logo-pulse")!.dispatchEvent(new Event("animationend", { bubbles: true })); });
    expect(document.querySelector(".logo-moving")).toBeNull();
    const hop = document.querySelector<SVGElement>("svg.logo-hop")!;
    expect(hop).not.toBeNull();
    expect(hop.getAttribute("class")).toBe("logo-hop");
    expect(hop.getAttribute("aria-hidden")).toBe("true");
    // the letters keep every shape but the point, the arm and the leg; each of those is drawn once, in its own <svg>
    const m = mark("word", "dark");
    expect(document.querySelectorAll("svg.logo path")).toHaveLength(m.parts.length - 3);
    expect(hop.querySelectorAll("path")).toHaveLength(1);
    const arm = document.querySelector<SVGElement>("svg.logo-arm")!;
    expect(arm.querySelector("path")!.getAttribute("d")).toBe(m.parts[ROLES.word.arm].d);
    expect(arm.getAttribute("aria-hidden")).toBe("true");
    // back along its own line by its whole length, as in the intro's draw (in % of the logo's box)
    const [fx, fy] = drawnFrom(m.parts[ROLES.word.arm].d, "left"), [, , w, h] = m.box;
    expect(arm.style.getPropertyValue("--fx")).toBe(`${Math.round((fx / w) * 10000) / 100}%`);
    expect(arm.style.getPropertyValue("--fy")).toBe(`${Math.round((fy / h) * 10000) / 100}%`);
    expect(document.querySelector("svg.logo-leg path")!.getAttribute("d")).toBe(m.parts[ROLES.word.leg].d);
    expect(document.querySelector<HTMLElement>(".logo-clip")!.style.clipPath).toMatch(/^polygon\(/);
    expect(hop.style.left).toBe("46.97%");
    expect(hop.style.width).toBe("5.97%");
  });

  it("a logo shown after the intro has played (the header after sign-in) gets the same motion without one", async () => {
    motion(false);
    await mount(<Logo animate idle />);
    expect(document.querySelector(".logo-moving")).toBeNull();
    expect(document.querySelector("svg.logo-hop")!.getAttribute("class")).toBe("logo-hop");
    expect(document.querySelector("svg.logo-arm")!.getAttribute("class")).toBe("logo-arm");
  });

  it("pauses both while the tab is hidden and goes on when it is back", async () => {
    motion(false);
    await mount(<Logo idle />);
    const states = () => ["svg.logo-hop", "svg.logo-arm"].map((q) => document.querySelector<SVGElement>(q)!.style.animationPlayState);
    expect(states()).toEqual(["", ""]);
    await hidden(true);
    expect(states()).toEqual(["paused", "paused"]);
    await hidden(false);
    expect(states()).toEqual(["", ""]);
  });

  it("removes its one listener when the logo goes", async () => {
    motion(false);
    const off = vi.spyOn(document, "removeEventListener");
    await mount(<Logo idle />);
    await act(async () => root!.unmount()); root = null;
    expect(off).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
    off.mockRestore();
  });

  it("with reduced motion: no intro, no hop, no arm, and Design's mark as it is", async () => {
    motion(true); resetLogoMotion();
    await mount(<Logo animate idle large />);
    expect(document.querySelector(".logo-moving, .logo-hop, .logo-arm, .logo-leg, .logo-box")).toBeNull();
    expect(document.querySelectorAll("svg.logo path")).toHaveLength(mark("word", "dark").parts.length);
    expect(document.querySelector("svg.logo")!.getAttribute("class")).toBe("logo logo-word logo-lg");
  });
});
