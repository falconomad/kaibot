// #327: text contrast and touch targets on the screens added since brand v1, read from the tokens and the stylesheet.
// WCAG 2 AA: 4.5:1 for text, 3:1 for large text (24 px, or 18.66 px bold) and for the focus ring against what it is
// drawn on. This is arithmetic on the declared colours; the colours as drawn, on every page and width, are measured in
// Chromium by scripts/mobile_check.py and scripts/a11y_check.py (axe's color-contrast rule).
import { describe, expect, it } from "vitest";
import css from "../app.css?raw";
import tokens from "../tokens.css?raw";
import v4 from "../v4tokens.css?raw";
import badge from "../components/StockBadge.tsx?raw";
import { TINTS } from "../components/StockBadge";

type Rgb = [number, number, number];
const rgb = (hex: string): Rgb => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;
const lum = (c: Rgb) => { const [r, g, b] = c.map((v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
export const ratio = (a: Rgb, b: Rgb) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
/** `fg` drawn at `alpha` over `bg`. */
const over = (fg: Rgb, bg: Rgb, alpha: number): Rgb => fg.map((v, i) => v * alpha + bg[i] * (1 - alpha)) as Rgb;

// The dark theme's tokens (the only theme switched on), as declared in tokens.css.
const dark = tokens.slice(tokens.indexOf(':root, :root[data-theme="dark"] {'), tokens.indexOf(':root[data-theme="light"]'));
const T = Object.fromEntries([...dark.matchAll(/--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b/g)].map((m) => [m[1], rgb(m[2])])) as Record<string, Rgb>;
const r = (fg: string, bg: string) => ratio(T[fg], T[bg]);
const rule = (sel: string) => { const m = new RegExp(`(?:^|\\n)${sel.replace(/[.[\]=*+]/g, "\\$&")} \\{([^}]*)\\}`).exec(css); if (!m) throw new Error(`no rule ${sel}`); return m[1]; };

describe("the check reads the tokens", () => {
  it("the dark theme's colours, and the ratio of black on white", () => {
    expect(Object.keys(T).length).toBeGreaterThan(30);
    expect([T.slate, T.stale]).toEqual([rgb("#C0C5CD"), rgb("#A7ADB8")]);
    expect(ratio(rgb("#000000"), rgb("#FFFFFF"))).toBeCloseTo(21, 5);
  });
});

describe("the two grey text colours (#294) meet 4.5:1 wherever text is set in them", () => {
  // Every background a muted line sits on in the new screens: the page, a card, a raised card or sheet row, a picked
  // theme or list, and the refused, waiting and accepted chips.
  const grounds = ["paper", "surface", "surface-2", "sea-tint", "loss-tint", "warn-tint", "gain-tint"];
  for (const grey of ["slate", "stale"]) it(`--${grey}`, () => {
    for (const g of grounds) expect(r(grey, g), `--${grey} on --${g}`).toBeGreaterThanOrEqual(4.5);
  });
  it("the stylesheet's muted text uses those tokens", () => {
    expect(rule(".muted")).toContain("color: var(--slate)");
    expect(v4).toContain("--text-2: var(--slate); --text-3: var(--stale);");
  });
});

describe("text on the new screens meets 4.5:1", () => {
  const pairs: [string, string, string][] = [
    ["ink", "surface-2", "the stock summary card's text"],
    ["slate", "surface-2", "the summary's dates and credit line"],
    ["ink", "surface", "a chip's ticker; a sheet's text"],
    ["loss", "loss-tint", "a refused chip's reason and its remove button"],
    ["warn", "warn-tint", "a waiting chip's note; the Only-these warning"],
    ["sea", "surface", "Retry and the other text buttons"],
    ["sea", "loss-tint", "Retry on a chip that was not checked"],
    ["sea", "warn-tint", "Retry on a waiting chip"],
    ["sea-ink", "sea-tint", "a picked theme's or list's count"],
    ["ink", "sea-tint", "a picked theme's or list's name"],
    ["on-sea", "sea", "Add to focus, Save list"],
    ["slate", "surface", "the round add button's plus sign"],
  ];
  for (const [fg, bg, what] of pairs) it(`${what}: --${fg} on --${bg}`, () => {
    expect(r(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
  it("those are the colours the stylesheet gives them", () => {
    expect(rule(".ss")).toContain("background: var(--surface-2)");
    expect(rule(".ss-body")).toContain("color: var(--ink)");
    expect(rule(".ss-meta")).toContain("color: var(--slate)");
    expect(rule(".kb-chip.no")).toContain("background: var(--loss-tint)");
    expect(rule(".kb-chip-why")).toContain("color: var(--loss)");
    expect(rule(".kb-chip.slow")).toContain("background: var(--warn-tint)");
    expect(rule(".kb-chip.slow .kb-chip-why")).toContain("color: var(--warn)");
    expect(rule(".linkbtn")).toContain("color: var(--sea)");
    expect(rule(".kb-tcard.on")).toContain("background: var(--sea-tint)");
    expect(rule(".kb-tcard.on .kb-tmeta")).toContain("color: var(--sea-ink)");
  });
});

describe("a stock you never trade, in an opened focus group", () => {
  it("is dimmed by its tile and a grey ticker, never by fading its words", () => {
    // Fading the whole row to 50% put the 13 px "never traded" line at 3.5:1 on the sheet.
    expect(ratio(over(T.slate, T.surface, 0.5), T.surface)).toBeLessThan(4.5);
    expect(css).not.toMatch(/\.kb-peek li\.off \{[^}]*opacity/);
    expect(rule(".kb-peek li.off .badge")).toContain("opacity: .5");
    expect(rule(".kb-peek li.off b")).toContain("color: var(--slate)");
    expect(r("slate", "surface")).toBeGreaterThanOrEqual(4.5);
  });
});

describe("tile colours", () => {
  it("the initials are the only text on a tile, in --ink on a dark tint: 4.5:1 on every tint", () => {
    expect(TINTS.length).toBe(10);
    for (const t of TINTS) expect(ratio(T.ink, rgb(t)), t).toBeGreaterThanOrEqual(4.5);
    expect(rule(".badge")).toContain("color: var(--ink)");
  });
  it("a sampled tile colour carries the company's logo and no text", () => {
    // In StockBadge the tile with a sampled colour (--tile-bg) holds one <img> and nothing else; a logo is exempt from
    // the contrast minimum (WCAG 1.4.3, logotypes), and its tile colour is sampled from the logo's own background.
    const logo = badge.slice(badge.indexOf("if (src)"), badge.indexOf("// The initials fit"));
    expect(logo).toContain('"--tile-bg": tile.bg');
    expect(logo.match(/<img /g)).toHaveLength(1);
    expect(logo).not.toMatch(/\{symbol\}|\{letters/);
  });
});

describe("the focus ring is 3:1 against what it is drawn on", () => {
  for (const g of ["paper", "surface", "surface-2", "sea-tint", "loss-tint", "warn-tint"]) it(`--focus on --${g}`, () => {
    expect(r("focus", g)).toBeGreaterThanOrEqual(3);
  });
});

describe("touch targets on the new controls are 44 px (as declared; measured in the browser by the scripts above)", () => {
  const px = (decl: string, prop: string) => Number(new RegExp(`(?:^|[;\\s])${prop}: (\\d+)px`).exec(decl)?.[1] ?? NaN);
  it("--tap is 44 px and every button starts at 44 px high", () => {
    expect(v4).toContain("--tap: 44px;");
    expect(px(rule("button, .btn"), "min-height")).toBe(44);
  });
  it("a focus group's card and its round add button", () => {
    expect(px(rule(".kb-tcard-open"), "min-height")).toBeGreaterThanOrEqual(44);
    expect(rule(".kb-tcard-open")).toContain("width: 100%");
    const round = rule(".kb-tcheck");
    expect([px(round, "width"), px(round, "height"), px(round, "min-height")]).toEqual([44, 44, 44]);
  });
  it("a ticker chip: its summary button, Remove and Retry", () => {
    expect(px(rule(".kb-chip"), "min-height")).toBe(44);
    expect(px(rule(".kb-chip-info"), "min-height")).toBe(44);
    expect(px(rule(".kb-chip-info"), "min-width")).toBe(44);
    const x = rule(".kb-chip-x");
    expect([px(x, "width"), px(x, "height")]).toEqual([44, 44]);
    // Retry is a text button: 44 px high from the button rule, and at least 44 px wide.
    expect(px(rule(".kb-chip .linkbtn"), "min-width")).toBe(44);
    expect(rule(".linkbtn")).not.toMatch(/min-height/);
  });
  it("a sheet's close button and the button at its foot", () => {
    expect(rule(".sheet-x")).toMatch(/width: var\(--tap\); min-height: var\(--tap\); height: var\(--tap\)/);
    expect(rule(".kb-peek-btn")).toContain("width: 100%");
  });
});
