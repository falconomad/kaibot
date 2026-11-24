// @vitest-environment jsdom
// Sign-in's entrance (stakeholder, 24 Nov 2026): the mirror of the sign-out drift (lib/glow.ts primeGlowArrival,
// app.css kb-glow-arrive). Sign-out moves the glow from Home's anchor down-left into sign-in's corner (setGlow's
// "short" move, 600 ms on --ease); a first load of sign-in or /register sweeps it up-right into the same corner, once,
// on the same duration and curve, with transform only. Reduced motion stands it still.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import css from "./app.css?raw";
import { doing, mount, q, settle, unmount } from "./a11y/dom";
import { ANCHORS, primeGlowArrival, setGlow } from "./lib/glow";
import { SignIn } from "./pages/SignIn";
import { Register } from "./pages/Register";

const rec = vi.hoisted(() => ({ otp: vi.fn<() => Promise<{ error: { code?: string; message: string } | null }>>() }));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  return { ...kit, supa: { ...kit.supa, auth: { ...kit.supa.auth, signInWithOtp: rec.otp } } };
});
const mem = new Map<string, string>();
vi.stubGlobal("localStorage", { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v); },
  get length() { return mem.size; }, key: (i: number) => [...mem.keys()][i] ?? null });

const loc = (pathname = "/", search = "", hash = "") => ({ pathname, search, hash });
const body = () => document.body.dataset;
const resetGlow = () => { delete body().glow; delete body().glowMove; document.body.removeAttribute("style"); };
const type = (sel: string, value: string) => doing(() => {
  const el = q<HTMLInputElement>(sel)!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
const rule = (sel: string) => new RegExp(`${sel.replace(/[[\]().*"]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
const keyframes = /@keyframes kb-glow-arrive \{([^@]*?)\}\s*\}/.exec(css)?.[1] ?? "";
const translate = (part: string) => /translate\((-?\d+)vw, (-?\d+)vh\)/.exec(keyframes.split(part)[1] ?? "")!.slice(1).map(Number);

beforeEach(() => { mem.clear(); rec.otp.mockReset(); resetGlow(); });
afterEach(async () => { await unmount(); resetGlow(); });

describe("sign-in's entrance plays once per page load", () => {
  it("a signed-out first load arrives; a second prime or the page's own setGlow does nothing", () => {
    expect(primeGlowArrival(loc(), document)).toBe(true);
    expect([body().glow, body().glowMove]).toEqual(["signin", "arrive"]);
    expect(primeGlowArrival(loc(), document)).toBe(false);
    setGlow("signin", document);
    expect(body().glowMove).toBe("arrive");
  });

  it("not when a session is stored, on the return from the emailed link, or on an auth error; /register's invite code is fine", () => {
    mem.set("sb-abcd-auth-token", "{}");
    expect(primeGlowArrival(loc(), document)).toBe(false);
    mem.clear();
    for (const l of [loc("/", "?from=signin&code=x"), loc("/", "?code=x"), loc("/", "?error=access_denied"), loc("/", "", "#access_token=x")])
      expect(primeGlowArrival(l, document), JSON.stringify(l)).toBe(false);
    expect(body().glowMove).toBeUndefined();
    mem.set("sb-abcd-auth-token-code-verifier", "x"); // a link was sent, nobody signed in yet
    expect(primeGlowArrival(loc("/register", "?code=INV123"), document)).toBe(true);
  });

  it("a failed attempt and its message never replay it, and the form takes focus at once", async () => {
    primeGlowArrival(loc(), document);
    await mount(<SignIn />);
    const email = q<HTMLInputElement>("#email")!;
    email.focus();
    expect(document.activeElement).toBe(email);
    const before = [body().glow, body().glowMove, document.body.getAttribute("style")];
    rec.otp.mockResolvedValue({ error: { message: "Email rate limit exceeded" } });
    await type("#email", "owner@example.com");
    await doing(() => { q<HTMLButtonElement>("button.primary")!.click(); }); await settle();
    expect(q('[role="alert"]')?.textContent).toContain("Email rate limit exceeded");
    rec.otp.mockResolvedValue({ error: { message: "Another message" } });
    await doing(() => { q<HTMLButtonElement>("button.primary")!.click(); }); await settle();
    expect(q('[role="alert"]')?.textContent).toContain("Another message");
    expect([body().glow, body().glowMove, document.body.getAttribute("style")]).toEqual(before);
    expect(q("form.card")!.getAttribute("style")).toBeNull();
  });

  it("/register gets the same treatment, and takes sign-in's place without motion when the entrance wasn't primed", async () => {
    primeGlowArrival(loc("/register"), document);
    await mount(<Register search="" />);
    expect(body().glowMove).toBe("arrive");
    await unmount(); resetGlow();
    await mount(<Register search="" />);
    expect([body().glow, body().glowMove]).toEqual(["signin", "none"]);
  });

  it("a sign-out later in the same load is the ordinary drift, not the entrance", () => {
    primeGlowArrival(loc(), document);
    setGlow("home", document);
    setGlow("signin", document);
    expect(body().glowMove).toBe("short");
  });
});

describe("the motion mirrors sign-out's", () => {
  it("same 600 ms and --ease as the sign-out drift; ends in sign-in's corner; travels up-right, against sign-out's down-left", () => {
    expect(rule("body::before")).toContain("transition: transform 600ms var(--ease)");
    expect(rule('body[data-glow-move="arrive"]::before')).toBe(" transition: none; animation: kb-glow-arrive 600ms var(--ease) both; ");
    const [fx, fy] = translate("from"); const [tx, ty] = translate("to");
    expect([tx, ty]).toEqual([...ANCHORS.signin]);
    const out = [ANCHORS.signin[0] - ANCHORS.home[0], ANCHORS.signin[1] - ANCHORS.home[1]]; // sign-out: (-100, +100) vw/vh
    const arrive = [tx - fx, ty - fy];
    expect(Math.sign(arrive[0])).toBe(-Math.sign(out[0]));
    expect(Math.sign(arrive[1])).toBe(-Math.sign(out[1]));
    // the 300vw x 300vh layer (top/left -100) still covers the screen at the first frame
    expect([-100 + fx <= 0, 200 + fx >= 100, -100 + fy <= 0, 200 + fy >= 100]).toEqual([true, true, true, true]);
  });

  it("GPU-friendly: the keyframes move transform only", () => {
    const props = [...keyframes.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
    expect(props).toEqual(["transform", "transform"]);
  });

  it("reduced motion: the global rule turns the entrance off, so the glow stands still in sign-in's corner", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation: none !important; transition: none !important; \}/);
    // with the animation off, the layer's resting transform is sign-in's anchor (the default of --glow-x/-y)
    expect(rule("body::before")).toContain("transform: translate(var(--glow-x, -50vw), var(--glow-y, 50vh))");
    // and nothing re-enables motion for the entrance under reduced motion
    const reduced = css.split("@media (prefers-reduced-motion: reduce)").slice(1).map((b) => b.split("\n}")[0]).join(" ");
    expect(reduced).not.toContain("kb-glow-arrive");
  });
});
