// @vitest-environment jsdom
// M13 4.7 (#481): the install offer and the Settings card, the installed app's sign-in by code, and the manifest's
// link to the tags in index.html.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { all, activate, doing, mount, q, settle, unmount } from "../a11y/dom";
import { auditNode, lines } from "../a11y/axe";
import { InstallApp } from "../components/InstallApp";
import CodeEntry from "./CodeEntry";
import { type InstallPrompt, installOffer, installSteps, isStandalone, platform, resetInstall, startPwa } from "./install";

const auth = vi.hoisted(() => ({ verifyOtp: vi.fn<(a: unknown) => Promise<{ error: { message: string } | null }>>() }));
vi.mock("../lib/api", async () => ({ ...(await import("../pages/testkit-api")), supa: { auth } }));

const text = (el: Element | null) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
const standalone = (on: boolean) => { window.matchMedia = ((query: string) => ({ matches: on && /display-mode: standalone/.test(query), media: query,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as typeof window.matchMedia; };
const type = (el: HTMLInputElement, value: string) => doing(() => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
/** A window that records what startPwa listens to and registers. */
function fakeWindow(sw = true) {
  const on: Record<string, (e: Event) => void> = {};
  const register = vi.fn(async () => ({}));
  return { on, register, w: { addEventListener: ((t: string, f: (e: Event) => void) => { on[t] = f; }) as Window["addEventListener"], navigator: sw ? { serviceWorker: { register } } : {} } };
}
const offer = (outcome: "accepted" | "dismissed" = "accepted") => {
  const e = new Event("beforeinstallprompt", { cancelable: true }) as InstallPrompt;
  e.prompt = vi.fn(async () => {});
  (e as { userChoice: unknown }).userChoice = Promise.resolve({ outcome });
  return e;
};

beforeEach(() => { resetInstall(); standalone(false); auth.verifyOtp.mockReset(); });
afterEach(async () => { await unmount(); });

describe("starting the installable app", () => {
  it("registers the worker at the site's root and keeps the browser's install offer (its own banner is held back)", () => {
    const { w, on, register } = fakeWindow();
    startPwa(w);
    expect(register).toHaveBeenCalledWith("/sw.js");
    expect(installOffer()).toBeNull();
    const e = offer();
    on.beforeinstallprompt(e);
    expect(e.defaultPrevented).toBe(true);
    expect(installOffer()).toBe(e);
    on.appinstalled(new Event("appinstalled"));
    expect(installOffer()).toBeNull();
  });

  it("a browser without service workers, or a registration that fails, changes nothing", async () => {
    expect(() => startPwa(fakeWindow(false).w)).not.toThrow();
    const f = fakeWindow();
    f.register.mockRejectedValue(new Error("blocked"));
    expect(() => startPwa(f.w)).not.toThrow();
    await Promise.resolve();
  });

  it("knows the home screen from a tab, and an iPhone or iPad from an Android phone", () => {
    expect(isStandalone({ navigator: {}, matchMedia: () => ({ matches: false }) })).toBe(false);
    expect(isStandalone({ navigator: {}, matchMedia: (query) => ({ matches: query === "(display-mode: standalone)" }) })).toBe(true);
    expect(isStandalone({ navigator: { standalone: true } })).toBe(true);
    expect(platform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1")).toBe("ios");
    expect(platform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15", 5)).toBe("ios");
    expect(platform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15", 0)).toBe("other");
    expect(platform("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36")).toBe("android");
    expect(installSteps("ios").join(" ")).toContain("Add to Home Screen");
    expect(installSteps("android").join(" ")).toContain("Install app");
  });
});

describe("Settings: On your phone", () => {
  it("no offer from the browser (Safari): the steps in words", async () => {
    await mount(<InstallApp />);
    expect(text(q("section"))).toContain("Put kaibo on your home screen.");
    expect(all("ol li").length).toBe(3);
    expect(q("button")).toBeNull();
    expect(lines(await auditNode(document.body))).toEqual([]);
  });

  it("the browser offers an install (Chrome on Android): one button that asks it, then says where the icon is", async () => {
    await mount(<InstallApp />);
    const { w, on } = fakeWindow();
    startPwa(w);
    const e = offer();
    await doing(() => { on.beforeinstallprompt(e); });
    const b = q<HTMLButtonElement>("button")!;
    expect(text(b)).toBe("Add kaibo to this device");
    expect(q("ol")).toBeNull();
    await activate(b);
    await settle();
    expect(e.prompt).toHaveBeenCalledTimes(1);
    expect(text(q('[role="status"]'))).toBe("Added. Look for the kaibo icon on your home screen.");
  });

  it("opened from the home screen: it says so and offers nothing", async () => {
    standalone(true);
    await mount(<InstallApp />);
    expect(text(q("section"))).toBe("On your phonekaibo is on your home screen and you are using it from there.");
  });
});

describe("sign-in inside the installed app", () => {
  it("in a browser tab the code field is not there: the emailed link works", async () => {
    await mount(<CodeEntry email="owner@example.com" />);
    expect(q("input")).toBeNull();
  });

  it("from the home screen: six digits from the email sign in; anything else cannot be sent", async () => {
    standalone(true);
    auth.verifyOtp.mockResolvedValue({ error: null });
    await mount(<CodeEntry email="owner@example.com" />);
    const input = q<HTMLInputElement>("input")!, b = q<HTMLButtonElement>("button")!;
    expect(input.getAttribute("autocomplete")).toBe("one-time-code");
    expect(input.getAttribute("inputmode")).toBe("numeric");
    expect(b.disabled).toBe(true);
    await type(input, "12345");
    expect(b.disabled).toBe(true);
    await type(input, "123 456");
    expect(b.disabled).toBe(false);
    await activate(b);
    await settle();
    expect(auth.verifyOtp).toHaveBeenCalledWith({ email: "owner@example.com", token: "123456", type: "email" });
    expect(q('[role="alert"]')).toBeNull();
    expect(lines(await auditNode(document.body))).toEqual([]);
  });

  it("a wrong or used code is said in words, and typing again clears it", async () => {
    standalone(true);
    auth.verifyOtp.mockResolvedValue({ error: { message: "Token has expired or is invalid" } });
    await mount(<CodeEntry email="owner@example.com" />);
    await type(q<HTMLInputElement>("input")!, "000000");
    await activate(q<HTMLButtonElement>("button")!);
    await settle();
    expect(text(q('[role="alert"]'))).toMatch(/^That code didn't work\./);
    await type(q<HTMLInputElement>("input")!, "00000");
    expect(q('[role="alert"]')).toBeNull();
  });
});
