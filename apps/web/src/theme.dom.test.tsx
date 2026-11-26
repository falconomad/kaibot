// @vitest-environment jsdom
// The theme setting (#720; lib/theme.ts, components/ThemeCard.tsx): System (the default) follows the device and changes
// with it; Dark and Light are kept in localStorage; storage that throws keeps System; index.html sets the same theme
// before the first paint.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import html from "../index.html?raw";
import { ThemeCard } from "./components/ThemeCard";
import { PREPAINT, THEME_KEY, applyTheme, resetThemeForTests, savedTheme, setTheme, startTheme } from "./lib/theme";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
let light = false;
const changes = new Set<() => void>();
beforeEach(() => {
  light = false; changes.clear(); localStorage.clear(); resetThemeForTests();
  delete document.documentElement.dataset.theme;
  document.head.innerHTML = '<meta name="theme-color" content="#0A0B0D" />';
  window.matchMedia = ((q: string) => ({ get matches() { return light && /prefers-color-scheme: light/.test(q); }, media: q, onchange: null,
    addEventListener: (_: string, f: () => void) => changes.add(f), removeEventListener: (_: string, f: () => void) => changes.delete(f),
    addListener() {}, removeListener() {}, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
afterEach(async () => { await act(async () => root?.unmount()); root = null; vi.restoreAllMocks(); });
async function mount() {
  document.body.innerHTML = '<div id="r"></div>';
  root = createRoot(document.getElementById("r")!);
  await act(async () => root!.render(<ThemeCard />));
}
const theme = () => document.documentElement.dataset.theme;
const radios = () => [...document.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
const checked = () => radios().find((r) => r.getAttribute("aria-checked") === "true")?.textContent;
const deviceTurns = async (toLight: boolean) => { light = toLight; await act(async () => { for (const f of [...changes]) f(); }); };

describe("the theme setting", () => {
  it("System by default: it draws the device's scheme, dark when the device says nothing", async () => {
    startTheme();
    expect([savedTheme(), theme()]).toEqual(["system", "dark"]);
    resetThemeForTests(); light = true; startTheme();
    expect(theme()).toBe("light");
    await mount();
    expect(radios().map((r) => r.textContent)).toEqual(["System", "Dark", "Light"]);
    expect(checked()).toBe("System");
    expect(document.querySelector('[role="radiogroup"]')!.getAttribute("aria-labelledby")).toBe("theme-h");
  });
  it("System follows the device while the page is open; Dark and Light do not", async () => {
    startTheme(); await mount();
    await deviceTurns(true);
    expect(theme()).toBe("light");
    await deviceTurns(false);
    expect(theme()).toBe("dark");
    await act(async () => radios()[2].click());
    await deviceTurns(false);
    expect(theme()).toBe("light");
    await act(async () => radios()[1].click());
    await deviceTurns(true);
    expect(theme()).toBe("dark");
  });
  it("a choice applies at once, is kept in localStorage, and System removes it", async () => {
    startTheme(); await mount();
    await act(async () => radios()[2].click());
    expect([theme(), checked(), localStorage.getItem(THEME_KEY)]).toEqual(["light", "Light", "light"]);
    await act(async () => radios()[0].click());
    expect([theme(), checked(), localStorage.getItem(THEME_KEY)]).toEqual(["dark", "System", null]);
  });
  it("arrow keys move and choose, as in any radio group; Tab enters at the chosen one", async () => {
    startTheme(); await mount();
    expect(radios().map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    radios()[0].focus();
    await act(async () => { radios()[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })); });
    expect([checked(), theme(), document.activeElement?.textContent]).toEqual(["Dark", "dark", "Dark"]);
    await act(async () => { radios()[1].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })); });
    expect([checked(), theme()]).toEqual(["Light", "light"]);
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, -1, 0]);
  });
  it("a saved choice holds on the next visit, whatever the device says; an unknown value is System", () => {
    localStorage.setItem(THEME_KEY, "light");
    startTheme();
    expect(theme()).toBe("light");
    resetThemeForTests(); localStorage.setItem(THEME_KEY, "sepia"); light = true; startTheme();
    expect([savedTheme(), theme()]).toEqual(["system", "light"]);
  });
  it("another tab changing the choice changes this one", async () => {
    startTheme(); await mount();
    localStorage.setItem(THEME_KEY, "light");
    await act(async () => { window.dispatchEvent(new StorageEvent("storage", { key: THEME_KEY })); });
    expect([theme(), checked()]).toEqual(["light", "Light"]);
  });
  it("storage that throws (private mode, blocked): System, no error, and the choice still works for this page", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
    light = true;
    expect(() => startTheme()).not.toThrow();
    expect([savedTheme(), theme()]).toEqual(["system", "light"]);
    await mount();
    await act(async () => radios()[1].click());
    expect([theme(), checked()]).toEqual(["dark", "Dark"]);
  });
  it("the browser bar's colour (meta theme-color) follows the page colour", () => {
    document.documentElement.style.setProperty("--paper", "#F4F5F7");
    setTheme("light");
    expect(document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')!.content).toBe("#F4F5F7");
    document.documentElement.style.removeProperty("--paper");
  });
});

describe("before the first paint (index.html)", () => {
  // the inline script, run as the browser runs it
  const run = () => new Function(PREPAINT)();
  it("index.html carries PREPAINT word for word, in <head> before any stylesheet or module script", () => {
    const at = html.indexOf(`<script>${PREPAINT}</script>`);
    expect(at).toBeGreaterThan(0);
    expect(at).toBeLessThan(html.indexOf("</head>"));
    expect(at).toBeLessThan(html.indexOf('rel="stylesheet"'));
    expect(at).toBeLessThan(html.indexOf('type="module"'));
  });
  it("draws what the app will: the saved choice, else the device's scheme; it never throws", () => {
    for (const [saved, dev, want] of [[null, false, "dark"], [null, true, "light"], ["dark", true, "dark"], ["light", false, "light"], ["x", true, "light"]] as const) {
      localStorage.clear(); if (saved) localStorage.setItem(THEME_KEY, saved);
      light = dev; delete document.documentElement.dataset.theme;
      run();
      expect(theme(), `${saved} ${dev}`).toBe(want);
      resetThemeForTests();
      expect(applyTheme(), `${saved} ${dev}`).toBe(want);
    }
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
    expect(run).not.toThrow();
  });
});
