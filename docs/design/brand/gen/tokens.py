"""kaibo brand system v1: scales, roles, contrast table and the proposed token file (issue #265).

    python docs/design/brand/gen/tokens.py     # writes ../tokens_v1.css and ../contrast_v1.md, prints the checks

Everything starts from three colours read from the Rising k logo files (docs/design/logo/concepts/1-rising-k):
the blue of the arm and the point, the white of the stem, and the black the logo uses on light backgrounds.
Scales are built in OKLCH (even steps of lightness, one hue), then named by role for a dark and a light theme.

Two dark roles are set by hand and are not steps of the grey scale: second- and third-level text (`--slate`, `--stale`)
were made brighter on 17 Nov 2026 (#294, stakeholder: larger, clearer text). The type steps follow the same request
(#307: 17 / 15 / 13). They are kept here as TYPE so the design-system page reads them from one place; they are not
written to tokens_v1.css, which holds colour and the logo's shape only.
"""
from __future__ import annotations

import pathlib
import re

from colour import contrast, from_oklch, mix, oklch

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
LOGO = ROOT.parent / "logo" / "concepts" / "1-rising-k"


def logo_colours() -> dict:
    """The exact values in the logo's own files."""
    dark = set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', (LOGO / "symbol-on-dark.svg").read_text()))
    light = set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', (LOGO / "symbol-on-light.svg").read_text()))
    blue = next(c for c in dark if oklch(c)[1] > 0.05)
    return {"blue": blue, "white": next(c for c in dark if c != blue), "blue_on_light": next(c for c in light if oklch(c)[1] > 0.05),
            "black": next(c for c in light if oklch(c)[1] <= 0.05)}


LC = logo_colours()
B_L, B_C, B_H = oklch(LC["blue"])            # 0.667, 0.153, 255.9
D_L, D_C, D_H = oklch(LC["blue_on_light"])   # 0.563, 0.182, 260.6
W_L, W_C, N_H = oklch(LC["white"])           # 0.970, 0.003, 264.5
K_L, K_C, _ = oklch(LC["black"])             # 0.149, 0.005


def _interp(x, pts):
    pts = sorted(pts)
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def blue(L: float) -> str:
    """The brand blue at another lightness. Hue and chroma pass through both logo blues."""
    if abs(L - B_L) < 1e-4:
        return LC["blue"]
    if abs(L - D_L) < 1e-4:
        return LC["blue_on_light"]
    h = _interp(L, [(D_L, D_H), (B_L, B_H)])
    c = _interp(L, [(0.16, 0.035), (0.24, 0.055), (0.33, 0.095), (0.45, 0.165), (D_L, D_C), (B_L, B_C), (0.76, 0.115), (0.86, 0.070), (0.93, 0.035), (0.97, 0.016)])
    return from_oklch(L, c, h)


def neutral(L: float) -> str:
    """A grey with the logo's own faint blue cast. The two ends are the logo's white and black."""
    if abs(L - W_L) < 1e-4:
        return LC["white"]
    if abs(L - K_L) < 1e-4:
        return LC["black"]
    c = _interp(L, [(K_L, K_C), (0.20, 0.009), (0.30, 0.014), (0.45, 0.020), (0.70, 0.019), (0.85, 0.012), (W_L, W_C)])
    return from_oklch(L, c, N_H - 3)


BLUE = {50: 0.965, 100: 0.93, 200: 0.86, 300: 0.76, 400: B_L, 500: D_L, 600: 0.47, 700: 0.39, 800: 0.30, 900: 0.24, 950: 0.19}
NEUTRAL = {0: 1.0, 50: W_L, 100: 0.925, 200: 0.86, 300: 0.78, 400: 0.70, 500: 0.62, 600: 0.50, 700: 0.39, 750: 0.33, 800: 0.285, 850: 0.235, 900: 0.195, 950: K_L}
BLUES = {k: blue(v) for k, v in BLUE.items()}
GREYS = {k: ("#FFFFFF" if k == 0 else neutral(v)) for k, v in NEUTRAL.items()}

# Dark-theme text below the first level, as the app has had it since #294. They sit between grey steps (200/300 and
# 300/400), so they are fixed values, not scale steps: the grey scale itself is unchanged.
SLATE_DARK = "#C0C5CD"
STALE_DARK = "#A7ADB8"

# Type steps (brand_system_v1.md section 3). Body, small and caption are one step larger since #294; Product confirmed
# 17 / 15 / 13 as the intent in #307. The app's own values are in apps/web/src/v4tokens.css (--t-md, --t-sm, --t-cap).
# (step, token, px, px on desktop or None, weights, tracking, used for)
TYPE = [
    ("Hero", "--t-hero", 44, 60, "700", "-0.035em", "the pot"),
    ("Title", "--t-xl", 28, None, "700", "-0.03em", "page title, sheet figure"),
    ("Heading", "--t-lg", 20, None, "600", "-0.01em", "sheet title"),
    ("Body", "--t-md", 17, None, "400 or 600", "0", "rows, buttons"),
    ("Small", "--t-sm", 15, None, "400 or 500", "0", "second lines, status rows"),
    ("Caption", "--t-cap", 13, None, "600", "+0.02em (caps +0.08em)", "labels, PAPER pill"),
]
TYPE_PX = {tok: px for _, tok, px, *_ in TYPE}

# Meaning colours: one hue each, set at the same lightness steps as the blue so they sit beside it.
HUES = {"gain": (160, 0.165), "loss": (24, 0.185), "amber": (78, 0.145), "agent": (296, 0.105)}


def tone(name: str, L: float, k=1.0) -> str:
    h, c = HUES[name]
    return from_oklch(L, c * k, h)


def theme(mode: str) -> dict:
    g, b = GREYS, BLUES
    if mode == "dark":
        t = {
            "paper": g[950], "surface": g[900], "surface-2": g[850], "rule": g[800], "rule-strong": g[700],
            "ink": g[50], "slate": SLATE_DARK, "stale": STALE_DARK,
            "sea": b[400], "sea-ink": b[300], "sea-tint": b[900], "sea-line": b[700], "on-sea": g[950],
            "gain": tone("gain", 0.80), "gain-tint": tone("gain", 0.26, 0.30), "gain-line": tone("gain", 0.42, 0.55),
            "loss": tone("loss", 0.72), "loss-tint": tone("loss", 0.25, 0.28), "loss-line": tone("loss", 0.54, 0.70),
            "warn": tone("amber", 0.82), "warn-tint": tone("amber", 0.27, 0.30), "warn-line": tone("amber", 0.56, 0.60), "on-warn": g[950], "paper-edge": tone("amber", 0.82),
            "ai": tone("agent", 0.82), "ai-tint": tone("agent", 0.25, 0.32), "ai-line": tone("agent", 0.42, 0.60),
            "exp-bg": g[850], "exp-line": g[500], "exp-hatch": g[700], "exp-text": g[50], "control": g[500],
            "focus": b[300], "selection": b[700], "live": b[400],
            "s-pot": b[400], "s-spy": g[500], "s-grid": g[800], "s-up": tone("gain", 0.64), "s-down": tone("loss", 0.52),
            "scrim": "rgba(0, 0, 0, .6)", "bar-bg": "rgba(10, 11, 13, .86)", "elev-sheet": "0 -12px 40px rgba(0, 0, 0, .55)",
        }
    else:
        t = {
            "paper": g[50], "surface": g[0], "surface-2": g[100], "rule": g[200], "rule-strong": g[400],
            "ink": g[950], "slate": g[700], "stale": g[600],
            "sea": b[500], "sea-ink": b[600], "sea-tint": b[50], "sea-line": b[200], "on-sea": g[0],
            "gain": tone("gain", 0.50, 0.80), "gain-tint": tone("gain", 0.95, 0.25), "gain-line": tone("gain", 0.80, 0.50),
            "loss": tone("loss", 0.52, 1.0), "loss-tint": tone("loss", 0.95, 0.16), "loss-line": tone("loss", 0.60, 0.90),
            "warn": tone("amber", 0.82), "warn-tint": tone("amber", 0.95, 0.30), "warn-line": tone("amber", 0.60, 0.85), "on-warn": g[950], "paper-edge": tone("amber", 0.60, 0.85),
            "ai": tone("agent", 0.48, 1.5), "ai-tint": tone("agent", 0.95, 0.25), "ai-line": tone("agent", 0.80, 0.55),
            "exp-bg": g[100], "exp-line": g[600], "exp-hatch": g[300], "exp-text": g[950], "control": g[600],
            "focus": b[500], "selection": b[200], "live": b[500],
            "s-pot": b[500], "s-spy": g[600], "s-grid": g[200], "s-up": tone("gain", 0.62, 0.9), "s-down": tone("loss", 0.46),
            "scrim": "rgba(10, 11, 13, .45)", "bar-bg": "rgba(244, 245, 247, .88)", "elev-sheet": "0 -12px 40px rgba(10, 11, 13, .18)",
        }
    t["pass"], t["fail"] = t["gain"], t["loss"]
    t["warn-text"] = t["warn"] if mode == "dark" else tone("amber", 0.50, 0.75)     # amber as text on light needs a darker step
    return t


# (what, foreground role, background role, minimum) -- text needs 4.5, large text and UI parts need 3
PAIRS = [
    ("Text on the page", "ink", "paper", 4.5), ("Text on a card", "ink", "surface", 4.5), ("Text on a raised surface", "ink", "surface-2", 4.5),
    ("Second-level text on the page", "slate", "paper", 4.5), ("Second-level text on a card", "slate", "surface", 4.5), ("Second-level text on a raised surface", "slate", "surface-2", 4.5),
    ("Third-level text on the page", "stale", "paper", 4.5), ("Third-level text on a card", "stale", "surface", 4.5), ("Third-level text on a raised surface", "stale", "surface-2", 4.5),
    ("Link and accent text on the page", "sea-ink", "paper", 4.5), ("Link and accent text on a card", "sea-ink", "surface", 4.5), ("Accent text on its tint", "sea-ink", "sea-tint", 4.5),
    ("Primary button: label on blue", "on-sea", "sea", 4.5), ("Primary button against the page", "sea", "paper", 3), ("Primary button against a card", "sea", "surface", 3),
    ("Focus ring on the page", "focus", "paper", 3), ("Focus ring on a card", "focus", "surface", 3), ("Selected text on the selection colour", "ink", "selection", 4.5),
    ("The live point on the page", "live", "paper", 3), ("The live point on a card", "live", "surface", 3),
    ("Gain figure on the page", "gain", "paper", 4.5), ("Gain figure on a card", "gain", "surface", 4.5), ("Gain text on its tint", "gain", "gain-tint", 4.5),
    ("Loss figure on the page", "loss", "paper", 4.5), ("Loss figure on a card", "loss", "surface", 4.5), ("Loss text on its tint", "loss", "loss-tint", 4.5),
    ("Halt row: text on the red tint", "ink", "loss-tint", 4.5), ("Halt row: red dot on the red tint", "loss", "loss-tint", 3), ("Halt row: edge against the page", "loss-line", "paper", 3),
    ("Warning row: text on the amber tint", "ink", "warn-tint", 4.5), ("Warning text on its tint", "warn-text", "warn-tint", 4.5), ("Warning row: edge against the page", "warn-line", "paper", 3),
    ("PAPER pill: label on amber", "on-warn", "warn", 4.5), ("PAPER pill: its edge against the page", "paper-edge", "paper", 3),
    ("Agent text on its tint", "ai", "ai-tint", 4.5), ("Agent text on a card", "ai", "surface", 4.5),
    ("Experiment label: text on its row", "exp-text", "exp-bg", 4.5), ("Experiment label: dashed edge on its row", "exp-line", "exp-bg", 3), ("Experiment label: dashed edge against the page", "exp-line", "paper", 3),
    ("Input and control border on a card", "control", "surface", 3), ("Input and control border on the page", "control", "paper", 3),
    ("Chart: pot line on a card", "s-pot", "surface", 3), ("Chart: benchmark line on a card", "s-spy", "surface", 3),
    ("Chart: gain mark on a card", "s-up", "surface", 3), ("Chart: loss mark on a card", "s-down", "surface", 3),
    ("Tab bar: active label", "ink", "paper", 4.5), ("Tab bar: resting label", "stale", "paper", 4.5), ("Tab bar: active mark", "sea", "paper", 3),
]


def table(mode: str):
    t = theme(mode)
    return [(what, t[fg], t[bg], round(contrast(t[fg], t[bg]), 2), need) for what, fg, bg, need in PAIRS]


def css() -> str:
    d, l = theme("dark"), theme("light")
    order = ["paper", "surface", "surface-2", "rule", "rule-strong", "ink", "slate", "stale", "sea", "sea-ink", "sea-tint", "gain", "gain-tint", "loss", "loss-tint",
             "pass", "warn", "warn-tint", "fail", "ai", "ai-tint", "s-pot", "s-spy"]
    new = ["on-sea", "sea-line", "focus", "selection", "live", "control", "gain-line", "loss-line", "warn-line", "warn-text", "on-warn", "paper-edge", "ai-line",
           "exp-bg", "exp-line", "exp-hatch", "exp-text", "s-grid", "s-up", "s-down", "scrim", "bar-bg", "elev-sheet"]

    def block(t, names, indent="  "):
        return "\n".join(f"{indent}--{n}: {t[n]};" for n in names)

    scale = "\n".join(f"  --blue-{k}: {v};" for k, v in BLUES.items()) + "\n" + "\n".join(f"  --grey-{k}: {v};" for k, v in GREYS.items())
    return f"""/* kaibo brand system v1: proposed token values (issue #265). NOT loaded by the app. Written by gen/tokens.py.
   Every colour is derived from the Rising k logo: blue {LC["blue"]}, white {LC["white"]}, black {LC["black"]}
   (and the logo's blue for light backgrounds, {LC["blue_on_light"]}). See brand_system_v1.md.

   How to apply: the first block uses the names of apps/web/src/tokens.css with new values, so it replaces that
   file's :root colours. The v4 names in v4tokens.css (--bg, --text, --accent, --up, --amber ...) are aliases of
   these and need no change. The second block is NEW names: each is listed in brand_system_v1.md under
   "What needs more than a value". The light theme is opt-in until those are done. */

:root {{
  /* the two scales (reference; components use the role names below) */
{scale}
}}

:root, :root[data-theme="dark"] {{
  color-scheme: dark;
  /* 1. existing names, new values */
{block(d, order)}
  --font-ui: "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-num: "Outfit", system-ui, sans-serif;      /* figures use the same face with tabular numerals */
  --font-ai: "Outfit", system-ui, sans-serif;
  /* 2. new names */
{block(d, new)}
}}

:root[data-theme="light"] {{
  color-scheme: light;
{block(l, order)}
{block(l, new)}
}}

/* Shape, taken from the logo: one unit is the stem (4 px in the app); ends are round. Unchanged v4 values are not repeated. */
:root {{
  --stroke: 2px;            /* icon and chart lines: round caps, round joins */
  --point: 8px;             /* the live point; 10 px at the end of the chart line */
  --d-draw: 300ms;          /* a line drawing itself, as the logo's arm does */
  --d-point: 180ms;         /* the point arriving */
  --ease-pop: cubic-bezier(.3, 1.4, .5, 1);
}}
"""


if __name__ == "__main__":
    print("logo:", LC)
    print("blue", {k: v for k, v in BLUES.items()})
    print("grey", {k: v for k, v in GREYS.items()})
    bad = 0
    for mode in ("dark", "light"):
        print("---", mode)
        for what, fg, bg, r, need in table(mode):
            flag = "" if r >= need else "   <-- FAIL"
            bad += r < need
            print(f"{what:44s} {fg} on {bg}  {r:5.2f}  (needs {need}){flag}")
    (ROOT / "tokens_v1.css").write_text(css())
    lines = ["# Contrast, measured", "", "Written by `gen/tokens.py`. WCAG 2 contrast ratios. Text needs 4.5:1; large text and parts of controls need 3:1.", ""]
    for mode in ("dark", "light"):
        lines += [f"## {mode.capitalize()} theme", "", "| What | Colour | On | Ratio | Needs |", "|---|---|---|---|---|"]
        lines += [f"| {what} | `{fg}` | `{bg}` | {r:.2f}:1 | {need}:1 |" for what, fg, bg, r, need in table(mode)] + [""]
    (ROOT / "contrast_v1.md").write_text("\n".join(lines))
    print("failures:", bad)
