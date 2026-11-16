// The sign-in glow behind every page (stakeholder, 16 Nov 2026). A fixed layer, opaque cards on top, and text that
// stands directly on the glow keeps its contrast at the glow's brightest point.
import { describe, expect, it } from "vitest";
import css from "./app.css?raw";
import tokens from "./tokens.css?raw";
import { ANCHORS, anchorFor, setGlow } from "./lib/glow";

const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const lum = (h: string) => { const [r, g, b] = hex(h).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
const theme = (head: string) => Object.fromEntries([...tokens.slice(tokens.indexOf(head)).split("}")[0].matchAll(/(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})/g)].map((m) => [m[1], m[2]]));

describe("the glow behind every page", () => {
  it("one layer fixed to the viewport, not a scrolling or fixed-attachment background", () => {
    expect(css).toMatch(/body::before \{ content: ""; position: fixed; z-index: -1; pointer-events: none; top: -100vh; left: -100vw; width: 300vw; height: 300vh;/);
    expect(css).toMatch(/background: var\(--glow-noise\), var\(--glow\); will-change: transform;\s*transform: translate\(var\(--glow-x, -50vw\), var\(--glow-y, 50vh\)\); transition: transform 600ms var\(--ease\);/);
    expect(css).not.toMatch(/background-attachment:\s*fixed/);
    // sign-in now uses the same layer instead of its own
    expect(css).toMatch(/\.center \{[^}]*padding: 24px; \}/);
    expect(tokens).toContain("--glow: radial-gradient(36.667% 23.333% at 50% 50%, var(--sea-tint) 0%, var(--paper) 60%);");
    expect(tokens).toMatch(/:root\[data-theme="light"\] \{[^}]*--glow: radial-gradient\(36\.667% 23\.333% at 50% 50%, var\(--blue-100\) 0%, var\(--paper\) 60%\);/);
  });
  it("cards, rows, accordions and sheets are opaque, so text on them keeps the contrast it had", () => {
    for (const sel of [".card {", ".rows {", ".acc {"]) expect(css.slice(css.indexOf(sel)).split("}")[0], sel).toContain("background: var(--surface)");
    expect(css.slice(css.indexOf(".sheet {")).split("}")[0]).toContain("background: var(--surface)");
    expect(css).toMatch(/header\.top \{[^}]*background: color-mix\(in srgb, var\(--surface\) 92%, transparent\)/);
  });
  it("text standing on the glow passes AA at its brightest point, dark and light", () => {
    for (const [head, glow] of [[':root, :root[data-theme="dark"] {', "--sea-tint"], [':root[data-theme="light"] {', null]] as const) {
      const t = theme(head);
      const bright = glow ? t[glow] : "#D9E9FF"; // light: --blue-100
      for (const role of ["--ink", "--slate", "--stale", "--sea-ink", "--gain", "--loss"]) expect(ratio(t[role], bright), `${head} ${role}`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(t["--control"], bright), `${head} --control`).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("the glow's place, and its drift", () => {
  const doc = () => { const style = new Map<string, string>(); const body = { dataset: {} as Record<string, string>, style: { setProperty: (k: string, v: string) => style.set(k, v) } };
    return { d: { body } as unknown as Document, body, style }; };
  it("each place has its anchor: sign-in bottom-left, Home top-right, small offsets for the tabs", () => {
    expect(ANCHORS.signin).toEqual([-50, 50]);
    expect(ANCHORS.home).toEqual([50, -50]);
    const tabs = ["/positions", "/activity", "/health", "/more"].map(anchorFor);
    expect(tabs).toEqual(["positions", "activity", "engine", "more"]);
    expect(new Set(tabs.map((a) => ANCHORS[a].join())).size).toBe(4);
    for (const a of tabs) { const [x, y] = ANCHORS[a]; expect(Math.hypot(x - 50, y + 50)).toBeLessThanOrEqual(30); }
    expect(anchorFor("/")).toBe("home");
    expect(anchorFor("/positions/12")).toBe("positions");
    expect(anchorFor("/report")).toBe("more");
  });
  it("the first place is set without motion; leaving sign-in is the long drift; a tab change the short one; the same place moves nothing", () => {
    const { d, body, style } = doc();
    setGlow("home", d);
    expect([body.dataset.glowMove, style.get("--glow-x"), style.get("--glow-y")]).toEqual(["none", "50vw", "-50vh"]);
    const s2 = doc(); setGlow("signin", s2.d); setGlow("home", s2.d);
    expect(s2.body.dataset.glowMove).toBe("long");
    setGlow("activity", s2.d);
    expect(s2.body.dataset.glowMove).toBe("short");
    s2.body.dataset.glowMove = "x"; setGlow("activity", s2.d);
    expect(s2.body.dataset.glowMove).toBe("x");
    expect(css).toMatch(/body\[data-glow-move="long"\]::before \{ transition-duration: 900ms; \}/);
    expect(css).toMatch(/body\[data-glow-move="none"\]::before \{ transition: none; \}/);
  });
});
