// @vitest-environment jsdom
// The logo's idle hop (stakeholder, 24 Nov 2026), mounted in jsdom: the hop is added only when the intro has ended,
// it pauses while the tab is hidden, and with reduced motion it never comes. The keyframes and timing are checked
// against app.css in brand.test.tsx; that it runs on the compositor was checked in headless Chromium (PR).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Logo, mark, resetLogoMotion } from "./components/Logo";

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

describe("the logo's idle hop", () => {
  it("does not start before the intro has finished: the point hops only once the pulse has ended", async () => {
    motion(false); resetLogoMotion();
    await mount(<Logo animate idle="often" />);
    expect(document.querySelector(".logo-moving")).not.toBeNull();
    expect(document.querySelector(".logo-hop")).toBeNull();
    await act(async () => { document.querySelector(".logo-pulse")!.dispatchEvent(new Event("animationend", { bubbles: true })); });
    expect(document.querySelector(".logo-moving")).toBeNull();
    const hop = document.querySelector<SVGElement>("svg.logo-hop")!;
    expect(hop).not.toBeNull();
    expect(hop.getAttribute("class")).toBe("logo-hop");
    expect(hop.getAttribute("aria-hidden")).toBe("true");
    // the letters keep every shape but the point, which is drawn once, in the hop's own <svg>, over the same spot
    expect(document.querySelectorAll("svg.logo path")).toHaveLength(mark("word", "dark").parts.length - 1);
    expect(hop.querySelectorAll("path")).toHaveLength(1);
    expect(hop.style.left).toBe("46.97%");
    expect(hop.style.width).toBe("5.97%");
  });

  it("a logo shown after the intro has played (the header after sign-in) gets the hop without one", async () => {
    motion(false);
    await mount(<Logo animate idle="rare" />);
    expect(document.querySelector(".logo-moving")).toBeNull();
    expect(document.querySelector("svg.logo-hop")!.getAttribute("class")).toBe("logo-hop rare");
  });

  it("pauses while the tab is hidden and goes on when it is back", async () => {
    motion(false);
    await mount(<Logo idle="often" />);
    const hop = () => document.querySelector<SVGElement>("svg.logo-hop")!;
    expect(hop().style.animationPlayState).toBe("");
    await hidden(true);
    expect(hop().style.animationPlayState).toBe("paused");
    await hidden(false);
    expect(hop().style.animationPlayState).toBe("");
  });

  it("removes its one listener when the logo goes", async () => {
    motion(false);
    const off = vi.spyOn(document, "removeEventListener");
    await mount(<Logo idle="often" />);
    await act(async () => root!.unmount()); root = null;
    expect(off).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
    off.mockRestore();
  });

  it("with reduced motion: no intro, no hop, and Design's mark as it is", async () => {
    motion(true); resetLogoMotion();
    await mount(<Logo animate idle="often" large />);
    expect(document.querySelector(".logo-moving, .logo-hop, .logo-box")).toBeNull();
    expect(document.querySelectorAll("svg.logo path")).toHaveLength(mark("word", "dark").parts.length);
    expect(document.querySelector("svg.logo")!.getAttribute("class")).toBe("logo logo-word logo-lg");
  });
});
