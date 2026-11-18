"""kaibo design system, brand v1 (issue #296; Product's decisions of #307 applied): one reference page built from the
files Design already keeps.

    python docs/design/brand/gen/designsystem.py            # writes ../design_system_v1.html and ../design_system_v1.md
    python docs/design/brand/gen/designsystem.py --shots    # also ../proof/design-system-{phone,desktop}.png (Playwright)

Nothing is typed in here that another file already holds: colours are read from ../tokens_v1.css, ratios from
../contrast_v1.md, the logo from ../../logo/concepts/1-rising-k/. Type, spacing, radii, sizes and motion are the values
of brand_system_v1.md (sections 3, 4, 5, 8) and mobile_first_v4.md ("Tokens", "Motion"); each is named where it is used.
The type steps are read from tokens.TYPE.
Outfit is embedded from apps/web/public/fonts (SIL Open Font License 1.1) when that folder is there; the page falls
back to the system font without it. Nothing here is imported by the app.
"""
from __future__ import annotations

import base64
import html
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import colour  # noqa: E402
import tokens  # noqa: E402

BRAND = HERE.parent
REPO = BRAND.parents[2]
LOGO = BRAND.parent / "logo" / "concepts" / "1-rising-k"
FONTS = REPO / "apps" / "web" / "public" / "fonts"
ISSUE = 296
DECIDED = 307      # Product's decisions on the three points #302 left open


# ---------- read the source files ----------

def read_tokens() -> dict:
    """tokens_v1.css as {"scale": {...}, "dark": {...}, "light": {...}, "shape": {...}}; names without the dashes."""
    text = re.sub(r"/\*.*?\*/", "", (BRAND / "tokens_v1.css").read_text(), flags=re.S)
    out = {"scale": {}, "dark": {}, "light": {}, "shape": {}}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        sel = sel.strip()
        decl = {k: v.strip() for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body)}
        if "light" in sel:
            out["light"].update(decl)
        elif "dark" in sel:
            out["dark"].update(decl)
        elif "blue-400" in decl:
            out["scale"].update(decl)
        else:
            out["shape"].update(decl)
    return out


def read_contrast() -> dict:
    """contrast_v1.md as {"dark": [(what, colour, on, ratio, needs)], "light": [...]}."""
    out, mode = {"dark": [], "light": []}, None
    for line in (BRAND / "contrast_v1.md").read_text().splitlines():
        if line.startswith("## "):
            mode = "dark" if "Dark" in line else "light"
        m = re.match(r"\| (.+?) \| `(#\w+)` \| `(#\w+)` \| ([\d.]+):1 \| ([\d.]+):1 \|", line)
        if m and mode:
            out[mode].append((m[1], m[2], m[3], float(m[4]), float(m[5])))
    return out


T = read_tokens()
C = read_contrast()
PAIR = {what: (fg, bg) for what, fg, bg, _ in tokens.PAIRS}
N_PAIRS = len(C["dark"]) + len(C["light"])
BELOW = [(m, r) for m in C for r in C[m] if r[3] < r[4]]


def drift() -> list:
    """Rows of contrast_v1.md whose colours are no longer the ones in tokens_v1.css, with the ratio measured again."""
    out = []
    for mode, rows in C.items():
        for what, fg_hex, bg_hex, ratio, need in rows:
            if what not in PAIR:
                continue
            fg, bg = PAIR[what]
            now_fg, now_bg = T[mode].get(fg, fg_hex), T[mode].get(bg, bg_hex)
            if now_fg.upper() != fg_hex.upper() or now_bg.upper() != bg_hex.upper():
                out.append((mode, what, fg, fg_hex, now_fg, bg_hex, ratio, round(colour.contrast(now_fg, now_bg), 2), need))
    return out


DRIFT = drift()
STALE_WHAT = {(d[0], d[1]) for d in DRIFT}
# The narrowest passes in the file (within 10% of the line), so nobody makes them smaller by accident.
NARROW = sorted(((m, r) for m in C for r in C[m] if r[3] >= r[4] and r[3] < r[4] * 1.1), key=lambda x: x[1][3] / x[1][4])

# Company tiles: the ten tints of the initials tile, as built (and signed off in #307) in apps/web/src/components/StockBadge.tsx (read-only here).
TILE_TINTS = ["#1E3A5F", "#3A2E1A", "#1F3A2C", "#3B1E2A", "#2A2540", "#1C3540", "#3A2A22", "#26323F", "#2F2440", "#3A2228"]
TILE_SCALE = 1.08
# Pairs this page stands on that contrast_v1.md has no row for. Measured here with gen/colour.py; not part of that file.
EXTRA = [("Text on the glow's brightest point", "ink", "sea-tint", 4.5), ("Second-level text on the glow's brightest point", "slate", "sea-tint", 4.5),
         ("Third-level text on the glow's brightest point", "stale", "sea-tint", 4.5)]
EXTRA_ROWS = [(what, T["dark"][fg], T["dark"][bg], round(colour.contrast(T["dark"][fg], T["dark"][bg]), 2), need) for what, fg, bg, need in EXTRA]
TINT_WORST = min(TILE_TINTS, key=lambda h: colour.contrast(T["dark"]["ink"], h))
EXTRA_ROWS.append(("Initials on the lightest of the ten tile tints", T["dark"]["ink"], TINT_WORST, round(colour.contrast(T["dark"]["ink"], TINT_WORST), 2), 4.5))
EXTRA_BELOW = [r for r in EXTRA_ROWS if r[3] < r[4]] + [d for d in DRIFT if d[7] < d[8]]


# ---------- the system, as the documents state it ----------

ROLES = [  # token, what it is for (brand_system_v1.md, sections 1, 2 and 5)
    ("paper", "Page"), ("surface", "Card, sheet"), ("surface-2", "Raised, pressed, skeleton"), ("rule", "Hairline (decoration)"),
    ("rule-strong", "Sheet edge, grab handle"), ("ink", "Text"), ("slate", "Second-level text"), ("stale", "Third-level text"),
    ("control", "Border of a control"), ("sea", "Primary action, the logo's blue"), ("on-sea", "Label on the primary action"),
    ("sea-ink", "Links, accent text"), ("sea-tint", "The glow"), ("focus", "Focus ring"), ("selection", "Selected text"), ("live", "The live point"),
    ("gain", "Gain, with ▲ and a plus sign"), ("gain-tint", "Gain tag"), ("loss", "Loss, with ▼ and a minus sign"), ("loss-tint", "Halt row, loss tag"),
    ("loss-line", "Halt edge, destructive border"), ("warn", "Paper, caution"), ("warn-tint", "Caution row"), ("warn-line", "Caution edge"),
    ("warn-text", "Amber as text"), ("ai", "Agent"), ("ai-tint", "Agent note"), ("exp-bg", "Experiment: surface"), ("exp-line", "Experiment: dashed edge"),
    ("exp-hatch", "Experiment: hatch"), ("s-pot", "Chart: pot line"), ("s-spy", "Chart: benchmark, dotted"), ("s-up", "Chart: gain mark"), ("s-down", "Chart: loss mark"),
]
_TYPE_DEMO = {  # how each step is drawn on the page, and its sample words
    "--t-hero": ("font-size:var(--t-hero);font-weight:700;letter-spacing:-.035em;line-height:1.02", "$5,021.40"),
    "--t-xl": ("font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em;line-height:1.15", "Positions"),
    "--t-lg": ("font-size:var(--t-lg);font-weight:600;letter-spacing:-.01em", "What the engine holds"),
    "--t-md": ("font-size:var(--t-md)", "The engine checked in a minute ago."),
    "--t-sm": ("font-size:var(--t-sm);color:var(--slate)", "Bought at the close on Monday"),
    "--t-cap": ("font-size:var(--t-cap);font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--stale)", "Today"),
}
# brand_system_v1.md section 3; the sizes come from gen/tokens.py (17 / 15 / 13 since #307)
TYPE = [(n, tok, f"{px}{f' ({desk} desktop)' if desk else ''} / {wt}", trk, use, *_TYPE_DEMO[tok]) for n, tok, px, desk, wt, trk, use in tokens.TYPE]
TYPE_VARS = "".join(f"{tok}:{px}px;" for _, tok, px, *_ in tokens.TYPE)
TYPE_DESK = "".join(f"{tok}:{desk}px;" for _, tok, _px, desk, *_ in tokens.TYPE if desk)
SPACE = [4, 8, 12, 16, 24, 32]                                       # brand_system_v1.md section 4
RADII = [("8", "tag"), ("12", "input"), ("16", "row"), ("24", "card, sheet"), ("pill", "buttons, chips")]   # section 4
SIZES = [("44", "tap target, minimum"), ("48", "tab bar item"), ("64", "list row"), ("52", "primary button"), ("48", "form field"), ("22", "tag")]
MOTION = [  # brand_system_v1.md section 4 and 8, tokens_v1.css, mobile_first_v4.md "Motion", logo_concepts.md
    ("Things rise", "Sheets and rows: 320 ms in, 240 ms out (<code>--d-sheet</code>). Height never animates."),
    ("Lines draw", "<code>--d-draw</code> {d-draw}: the chart line on first load, as the logo's arm does."),
    ("The point pops", "<code>--d-point</code> {d-point} with <code>--ease-pop</code> {ease-pop}; it pulses once when a new value arrives."),
    ("Easing", "<code>cubic-bezier(.2, .7, .2, 1)</code> for everything entering; <code>cubic-bezier(.4, 0, 1, 1)</code> for everything leaving."),
    ("Press and select", "120 ms (<code>--d-fast</code>). Fades, colour changes and the number tick: 200 ms (<code>--d-base</code>)."),
    ("Tab change", "Content cross-fades in 200 ms with a 4 px rise. The top bar and the tab bar do not move."),
    ("The glow", "Drifts with a transform only: 900 ms from sign-in to Home, 600 ms between tabs, the house easing."),
    ("The logo", "Once per app load, under a second: the stem rises, the leg sets, the arm draws, the point appears with one soft pulse."),
    ("What may move", "Only <code>transform</code> and <code>opacity</code>."),
    ("No data motion loops", "Anything that shows data plays once: a chart draws once, a point pops once, a number ticks once. Nothing else loops except the two signals below."),
    ("Loop: skeletons breathe", "Opacity 1 to 0.55 and back every 1.6 s (<code>kb-breathe</code>). No shimmer: it animates <code>background-position</code>, which repaints every frame."),
    ("Loop: the live dot pulses", "A ring grows from the dot and fades every 2 s (<code>kb-pulse</code>), only while the engine is live."),
    ("Reduce motion", "No draw, no pop, no pulse; values just change; the glow jumps; the finished logo shows at once. Both loops stop: skeletons stand still and the live dot shows without its ring."),
]
MOTION = [(a, b.replace("{d-draw}", T["shape"]["d-draw"]).replace("{d-point}", T["shape"]["d-point"]).replace("{ease-pop}", T["shape"]["ease-pop"])) for a, b in MOTION]

# Where each cell of the state table comes from: "rule" is written in a design document, "built" is how the app does it
# today with no written rule, "follows" is this page applying a written rule to a case no document names.
STATES = [
    ("Card", "rule", "Surface, 1 px hairline, 24 px corners, no shadow. Padding 16 on phones, 24 on desktop.",
     "rule", "Skeleton rows with the height of the content they stand for, so nothing jumps.",
     "built", "One plain sentence that says what to expect next. No picture.",
     "built", "Says what could not load, that nothing in the engine changed, the server's message, and a \"Try again\" button."),
    ("Buttons", "rule", "Pill, 52 px. Primary: blue fill, one per screen or sheet. Secondary: raised fill, control border. Destructive: red label, red edge, never blue.",
     "built", "The button is disabled and its label says what is happening (\"Saving…\"). No spinner.",
     "rule", "Disabled: 45% opacity, plus the reason in words nearby.",
     "follows", "The button returns to its resting state; the message stands beside it in words, as a form error does."),
    ("Tabs", "rule", "Resting: third-level colour. Active: text colour plus the 6 px blue point above the icon. Two cues, neither colour alone.",
     "rule", "The bar does not move or change while a page loads.",
     "follows", "The bar does not change when a page is empty.",
     "rule", "The Engine tab carries a red dot while a halt is in force and an amber dot when the engine is offline."),
    ("Chips", "rule", "Pill, 44 px, control border. Selected: text-colour fill, page-colour label. Not blue.",
     "built", "A ticker being checked: dashed edge (dashed means provisional) and the word \"Checking\".",
     "built", "No chips: one plain sentence in their place.",
     "built", "A refused ticker: red tint, red edge, the symbol struck through, and the reason in words."),
    ("Logo", "rule", "The wordmark alone in the header and on sign-in; the k alone where space is short. The files are used unchanged.",
     "rule", "Once per app load the mark draws itself in under a second. With reduce motion it shows finished.",
     "follows", "None. The mark is the same on every screen.",
     "follows", "None. The mark is never recoloured to carry a state."),
    ("Company tile", "rule", "A squircle with the company's logo on a colour sampled from that logo; grey 50 when none was sampled. Drawn 8% larger than the size it is given.",
     "rule", "The tile is laid out at its size first, on its colour (grey 50 until sampled); the logo fades in over 200 ms.",
     "rule", "No logo: the ticker's initials on one of ten dark tints, never under 12 px.",
     "rule", "The logo failed to load: the initials tile. A row with no company (a halt): a neutral tile with a red icon."),
    ("Skeleton", "rule", "Raised-surface bars (<code>--surface-2</code>), row-shaped, 8 px corners, an opacity breathe: 1 to 0.55 and back every 1.6 s. No blue.",
     "rule", "It is the loading state. It may loop, because it carries no data. With reduce motion the bars stand still.",
     "follows", "Never left running: the empty sentence takes its place.",
     "follows", "Never left running: the error message takes its place."),
]

# Findings on contrast. Nothing is changed here (issue #296).
def findings() -> list:
    f = []
    if BELOW:
        f.append("<b>Below WCAG AA in <code>contrast_v1.md</code>:</b> " + "; ".join(f"{r[0]} ({m}): {r[3]:.2f}:1, needs {r[4]:g}:1" for m, r in BELOW) + ".")
    else:
        f.append(f"<b>No measured pair is below WCAG AA.</b> <code>contrast_v1.md</code> holds {N_PAIRS} pairs ({len(C['dark'])} dark, {len(C['light'])} light); every one meets its line.")
    if DRIFT:
        toks = sorted({f"--{d[2]}" for d in DRIFT})
        f.append(f"<b><code>contrast_v1.md</code> is out of date for {len(DRIFT)} rows.</b> " + " and ".join(f"<code>{t}</code>" for t in toks)
                 + " changed in <code>tokens_v1.css</code> on 17 Nov 2026 (#294: "
                 + ", ".join(sorted({f"<code>{d[3]}</code> to <code>{d[4]}</code>" for d in DRIFT}))
                 + ") and the ratios were not measured again. The rows are marked below. Measured for this page with <code>gen/colour.py</code> they are "
                 + ("all above their line" if not any(d[7] < d[8] for d in DRIFT) else "NOT all above their line")
                 + f" ({min(d[7] for d in DRIFT):.2f}:1 at the lowest); those numbers are not in the file yet.")
        f.append("<b><code>gen/tokens.py</code> still builds the old values</b> (<code>slate</code> grey 300, <code>stale</code> grey 400). Running it would put "
                 "<code>tokens_v1.css</code> back. The roles table in <code>brand_system_v1.md</code> shows the old values too. One Design follow-up fixes all three.")
    if NARROW:
        f.append("<b>Narrow passes</b> (within 10% of the line; do not darken or lighten these without measuring): "
                 + "; ".join(f"{r[0].lower()}, {m}, {r[3]:.2f}:1 of {r[4]:g}:1" for m, r in NARROW) + ".")
    f.append("<b>Not in <code>contrast_v1.md</code>:</b> text standing on the glow (<code>brand_system_v1.md</code> section 8 says it passes at the brightest point, "
             "without a number) and the initials on the company tile's ten tints. Measured for this page: "
             + "; ".join(f"{r[0].lower()} {r[3]:.2f}:1" for r in EXTRA_ROWS) + ". "
             + ("None is below its line." if not EXTRA_BELOW else "Some are below their line.") + " They should become rows of the file.")
    f.append("<b>Not measured anywhere:</b> the tile's hairline (white at 8%), a company's own logo on its own sampled colour, and disabled controls at 45% "
             "opacity (WCAG does not set a minimum for inactive controls). The hairline <code>--rule</code> is decoration by rule and nothing relies on it.")
    f.append("<b>The logo's blue on a light page:</b> <code>#4F95F0</code> on <code>#F4F5F7</code> is 2.79:1 (<code>logo_concepts.md</code>), below 3:1. "
             "That is why the light files use <code>#2F6FDE</code> (4.33:1). Never place the dark-background logo on a light surface.")
    return f


# Where the live app differs from this page (apps/web read on main, 17 Nov 2026). For a Frontend follow-up story.
DIFFS = [
    ("Primary button height", "52 px (<code>--btn</code>).", "<code>button</code> has <code>min-height: 44px</code>; only the sheet's action uses <code>--btn</code>.", "Frontend: primary buttons to 52 px."),
    ("Card corners on phones", "24 px at every width.", "<code>.card</code> drops to 20 px under 760 px; rows and sheets stay 24 (<code>--r-card</code>).", "Frontend: use <code>--r-card</code>."),
    ("Card padding on desktop", "24 px.", "22 px.", "Frontend: 24 px (<code>--sp-6</code>)."),
    ("Form fields", "12 px corners, page-colour fill.", "14 px corners, <code>--surface</code> fill; <code>--r-input</code> is defined and unused.", "Frontend: use <code>--r-input</code> and <code>--paper</code>."),
    ("Skeleton corners", "8 px corners on every bar.", "Corners from 10 to 24 px and pills. The opacity breathe itself matches (<code>kb-breathe</code>, 1.6 s).",
     "Frontend: 8 px, or Design widens the rule. Not urgent."),
    ("Tags", "22 px pill.", "<code>.tag</code> is 13 px text with 3 px padding and no set height.", "Frontend: set the height."),
    ("Table headers", "13 px / 600, <code>--slate</code> (the caption step).", "13 px / 500, <code>--stale</code>.", "Frontend: weight 600 and <code>--slate</code>. The size already matches."),
    ("Meters", "8 px track.", "<code>.meter .bar</code> is 4 px and <code>.prog</code> is 6 px.", "Frontend: 8 px."),
    ("Dashed edges", "Dashed means provisional. Section 1 names only the experiment and the benchmark line.",
     "Also the ticker chip while it is checked and unverified evidence (both provisional), and the empty My lists note (not provisional).",
     "Frontend: a solid edge on the empty note. Design: widen the sentence in section 1 to name the other two."),
    ("A colour outside the tokens", "Every colour is a token.", "The caret of <code>select.chip</code> is drawn in <code>#A1A8B3</code>, the old v3 grey.", "Frontend: <code>--slate</code>."),
    ("Header", "56 px, wordmark 21 px tall (section 5).", "64 px on phones, 72 px on desktop; wordmark 26 and 28 px (stakeholder, 16 Nov 2026: the header was too small).",
     "No Frontend change. Design should update section 5."),
    ("Company tile tints", "Signed off as built (#307); the rule is in <code>brand_system_v1.md</code>, section 5.",
     "The tile matches. Its ten initials tints are hex values in <code>StockBadge.tsx</code>, not tokens.", "Design names ten tint tokens in <code>tokens_v1.css</code>; then Frontend points the tile at them."),
]


# ---------- drawing ----------

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def svg_inner(name: str) -> tuple[str, str]:
    """(viewBox, inner markup) of one of Design's logo files, unchanged."""
    s = (LOGO / name).read_text()
    return re.search(r'viewBox="([^"]+)"', s)[1], re.sub(r"<title>.*?</title>", "", re.search(r"<svg[^>]*>(.*)</svg>", s, re.S)[1])


def logo(name: str, height: int, label: str = "kaibo", tight: bool = False) -> str:
    vb, inner = svg_inner(name)
    x, y, w, h = (float(v) for v in vb.split())
    if tight and name.startswith("wordmark"):      # the letters without the file's margin (pad=14 in logo/gen/build.py)
        x, y, w, h = x + 14, y + 14, w - 28, h - 28
        vb = f"{x:g} {y:g} {w:g} {h:g}"
    return f'<svg viewBox="{vb}" height="{height}" width="{height * w / h:.1f}" role="img" aria-label="{label}" style="display:block;overflow:visible">{inner}</svg>'


def squircle(n: float = 5, steps: int = 96) -> str:
    """The tile's curve: a superellipse, n = 5, as the app's mask is (apps/web/src/app.css, "Company tiles")."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        pts.append(f"{50 + 50 * math.copysign(abs(c) ** (2 / n), c):.2f} {50 + 50 * math.copysign(abs(s) ** (2 / n), s):.2f}")
    return "M" + " L".join(pts) + " Z"


def data_svg(body: str) -> str:
    s = f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100' preserveAspectRatio='none'>{body}</svg>"
    return 'url("data:image/svg+xml,' + s.replace("%", "%25").replace("#", "%23").replace("<", "%3C").replace(">", "%3E").replace('"', "'") + '")'


def font_face() -> str:
    if not FONTS.is_dir():
        return ""
    out = ["/* Outfit: Copyright 2021 The Outfit Project Authors. SIL Open Font License 1.1 (apps/web/public/fonts/OFL.txt). Latin subset. */"]
    for w in (400, 500, 600, 700):
        b = base64.b64encode((FONTS / f"outfit-latin-{w}-normal.woff2").read_bytes()).decode()
        out.append(f'@font-face{{font-family:"Outfit";font-weight:{w};font-display:swap;src:url(data:font/woff2;base64,{b}) format("woff2")}}')
    return "\n".join(out)


ICON = {  # 24 px grid, 2 px stroke, round caps and joins, one colour (brand_system_v1.md section 4)
    "home": "M3 11 L12 4 L21 11 V20 H14 V14 H10 V20 H3 Z", "positions": "M4 7 H20 M4 12 H20 M4 17 H14", "activity": "M4 6 H20 M4 10 H16 M4 14 H20 M4 18 H12",
    "engine": "M3 12 H7 L10 5 L14 19 L17 12 H21", "more": "M5 12 H5.01 M12 12 H12.01 M19 12 H19.01",
    "shield": "M12 3 L20 6 V12 C20 16.5 16.5 20 12 21 C7.5 20 4 16.5 4 12 V6 Z", "flask": "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 15h9",
    "stop": "M12 8 V13 M12 16.5 V16.51 M4.5 19 H19.5 L12 5 Z", "x": "M6 6 L18 18 M18 6 L6 18",
}


def icon(name: str, size: int = 24) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><path d="{ICON[name]}"/></svg>')


def tabbar(dot: str = "") -> str:
    tabs = [("home", "Home"), ("positions", "Positions"), ("activity", "Activity"), ("engine", "Engine"), ("more", "More")]
    items = []
    for i, (ic, name) in enumerate(tabs):
        d = f'<i class="tdot {dot}"></i>' if dot and ic == "engine" else ""
        items.append(f'<span class="tab{" on" if i == 0 else ""}"><span class="tico">{icon(ic)}{d}</span>{name}</span>')
    return f'<div class="tabbar" role="img" aria-label="Bottom tab bar, Home active{", " + dot + " dot on Engine" if dot else ""}">{"".join(items)}</div>'


def tile(kind: str, size: int, sym: str = "KBO", tint: int = 0) -> str:
    px = round(size * TILE_SCALE)
    box = f"width:{px}px;height:{px}px"
    if kind == "logo":
        return f'<span class="tile" style="{box};background:var(--grey-50)"><i class="glyph in"></i></span>'
    if kind == "pending":
        return f'<span class="tile" style="{box};background:var(--grey-50)"><i class="glyph" style="opacity:0"></i></span>'
    if kind == "halt":
        return f'<span class="tile sys" style="{box}">{icon("stop", round(px * .5))}</span>'
    n = 4 if min(round(px * .3), int(px * .84 / (4 * .72))) >= 12 else 3
    fs = max(12, min(round(px * .3), int(px * .84 / (n * .72))))
    return f'<span class="tile" style="{box};background:{TILE_TINTS[tint]};font-size:{fs}px">{sym[:n]}</span>'


def sk(w: str = "100%", h: int = 14, r: int = 8) -> str:
    return f'<span class="sk" style="width:{w};height:{h}px;border-radius:{r}px"></span>'


SRC = {"rule": "rule", "built": "as built", "follows": "follows"}


def cell(label: str, src: str, text: str, demo: str) -> str:
    return (f'<div class="st"><div class="st-h"><span class="cap">{label}</span><span class="src {src}">{SRC[src]}</span></div>'
            f'<div class="st-demo">{demo}</div><p class="st-t">{text}</p></div>')


def row(sym: str, name: str, fig: str, cls: str, t: str) -> str:
    return f'<li class="row">{t}<span class="rt"><b>{sym}</b><span class="sm">{name}</span></span><span class="num {cls}">{fig}</span></li>'


def demos() -> dict:
    rows = row("KBO", "Sample", "+$21.40", "gain", tile("logo", 36)) + row("DEMO", "No logo", "−$8.15", "loss", tile("init", 36, "DEMO", 4))
    skrow = f'<li class="row">{sk("39px", 39, 10)}<span class="rt" style="gap:8px">{sk("40%", 14)}{sk("64%", 12)}</span>{sk("56px", 14)}</li>'
    err = ('<div class="state" role="alert"><b class="loss-t">Positions couldn\'t load.</b> <span class="sm">The server didn\'t answer as expected. Nothing in the engine changed.</span>'
           '<span class="sm det">503 Service Unavailable</span><span><button type="button" class="btn sec sm-btn">Try again</button></span></div>')
    chip = lambda t, on=False: f'<button type="button" class="chip" aria-pressed="{str(on).lower()}">{t}</button>'  # noqa: E731
    word = logo("wordmark-on-dark.svg", 28, tight=True)
    return {
        "Card": [f'<div class="card flush"><ul class="rows">{rows}</ul></div>', f'<div class="card flush" role="status" aria-label="Loading"><ul class="rows">{skrow}{skrow}</ul></div>',
                 '<div class="card"><p class="sm" style="margin:0">Nothing is held right now. The next buys are placed at the close.</p></div>', f'<div class="card">{err}</div>'],
        "Buttons": ['<div class="stack"><button type="button" class="btn pri">Review and apply</button><button type="button" class="btn sec">Fine-tune</button>'
                    '<button type="button" class="btn dan">Flatten and stop</button></div>',
                    '<div class="stack"><button type="button" class="btn pri" disabled>Saving…</button></div>',
                    '<div class="stack"><button type="button" class="btn pri" disabled>Save changes</button><p class="sm" style="margin:0">Nothing has changed yet.</p></div>',
                    '<div class="stack"><button type="button" class="btn pri">Save changes</button><p class="sm loss-t" style="margin:0">Not saved: the server didn\'t answer. Nothing in the engine changed.</p></div>'],
        "Tabs": [tabbar(), tabbar(), tabbar(), f'<div class="stack">{tabbar("breach")}{tabbar("offline")}</div>'],
        "Chips": [f'<div class="chips">{chip("All", True)}{chip("Open")}{chip("Closed")}</div>',
                  '<div class="chips"><span class="tchip checking"><b>KBO</b><span class="sm">Checking…</span></span></div>',
                  '<p class="sm" style="margin:0">No tickers yet. Add one and the engine checks it first.</p>',
                  f'<div class="chips"><span class="tchip no"><b class="strike">XKBO</b><span class="sm loss-t">Not traded on a US exchange</span>{icon("x", 16)}</span></div>'],
        "Logo": [f'<div class="stack">{word}{logo("symbol-on-dark.svg", 48)}</div>',
                 f'<div class="stack"><div class="logo-play" data-replay>{logo("wordmark-on-dark.svg", 28, tight=True)}</div><button type="button" class="btn sec sm-btn" data-replay-btn>Play again</button></div>',
                 '<p class="sm" style="margin:0">No empty state.</p>', '<p class="sm" style="margin:0">No error state.</p>'],
        "Company tile": [f'<div class="tiles">{tile("logo", 32)}{tile("logo", 36)}<span class="sm">32 and 36: drawn 35 and 39 px</span></div>',
                         f'<div class="tiles">{tile("pending", 32)}{tile("pending", 36)}<span class="sm">the tile first, then the logo</span></div>',
                         f'<div class="tiles">{tile("init", 32, "KBO", 0)}{tile("init", 36, "DEMO", 2)}<span class="sm">initials</span></div>',
                         f'<div class="tiles">{tile("init", 36, "KBO", 3)}{tile("halt", 36)}<span class="sm">initials; a halt row</span></div>'],
        "Skeleton": [f'<div class="stack" role="status" aria-label="Loading">{sk("46%", 20)}{sk("100%", 52, 8)}{sk("100%", 52, 8)}</div>',
                     f'<div class="stack still">{sk("46%", 20)}{sk("100%", 52, 8)}{sk("100%", 52, 8)}<span class="sm">with reduce motion</span></div>',
                     '<p class="sm" style="margin:0">Nothing is held right now. The next buys are placed at the close.</p>', err],
    }


CSS = """
:root{
  /* Not colours, so not in tokens_v1.css: brand_system_v1.md sections 3 and 4, mobile_first_v4.md "Tokens" and "Motion". */
  --sp-1:4px;--sp-2:8px;--sp-3:12px;--sp-4:16px;--sp-6:24px;--sp-8:32px;
  TYPEVARS
  --r-tag:8px;--r-input:12px;--r-row:16px;--r-card:24px;--r-pill:999px;
  --tap:44px;--tab-item:48px;--row:64px;--btn:52px;
  --d-fast:120ms;--d-base:200ms;--d-sheet:320ms;--d-sheet-out:240ms;
  --ease:cubic-bezier(.2,.7,.2,1);--ease-out:cubic-bezier(.4,0,1,1);
  --glow-from:var(--sea-tint);
}
:root[data-theme="light"]{--glow-from:var(--blue-100)}
@media (min-width:761px){:root{TYPEDESK}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 var(--t-md)/1.5 var(--font-ui);-webkit-font-smoothing:antialiased;
  font-variant-numeric:tabular-nums;word-spacing:.06em;overflow-x:hidden}
/* the page glow (brand_system_v1.md section 8): one fixed layer, 110% by 70% of the screen, anchored top right as on Home */
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:NOISE,radial-gradient(110% 70% at 100% 0%,var(--glow-from) 0%,var(--paper) 60%)}
::selection{background:var(--selection);color:var(--ink)}
a{color:var(--sea-ink);text-decoration:underline;text-underline-offset:2px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
code{font:inherit;font-size:.92em;background:var(--surface-2);border-radius:6px;padding:1px 5px;overflow-wrap:anywhere}
h1{margin:0;font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em;line-height:1.15;text-wrap:balance}
h2{margin:0;font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em;line-height:1.15}
h3{margin:0;font-size:var(--t-lg);font-weight:600;letter-spacing:-.01em}
p{margin:0}
.cap{font-size:var(--t-cap);font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--stale)}
.sm{font-size:var(--t-sm);color:var(--slate)}
.lead{color:var(--slate);max-width:68ch}
.num{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.gain{color:var(--gain)}.loss{color:var(--loss)}.loss-t{color:var(--loss)}
.num.gain::before{content:"\\25B2\\00a0";font-size:.7em;vertical-align:.12em}.num.loss::before{content:"\\25BC\\00a0";font-size:.7em;vertical-align:.12em}
header.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-3) var(--sp-4);background:var(--bar-bg);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--rule)}
header.top .only-light{display:none}:root[data-theme="light"] header.top .only-light{display:block}:root[data-theme="light"] header.top .only-dark{display:none}
.pill{font-size:var(--t-cap);font-weight:600;letter-spacing:.08em;color:var(--on-warn);background:var(--warn);border:1px solid var(--paper-edge);border-radius:var(--r-pill);padding:3px 9px}
.grow{flex:1}
main{max-width:1180px;margin:0 auto;padding:var(--sp-6) var(--sp-4) 72px;display:flex;flex-direction:column;gap:48px}
@media (min-width:761px){main{padding:var(--sp-8) var(--sp-8) 96px}header.top{padding:var(--sp-3) var(--sp-8)}}
section{display:flex;flex-direction:column;gap:var(--sp-4);min-width:0}
.intro{display:flex;flex-direction:column;gap:var(--sp-3)}
nav.toc{display:flex;flex-wrap:wrap;gap:var(--sp-1) var(--sp-4);font-size:var(--t-sm)}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:var(--r-card);padding:var(--sp-4);min-width:0}
@media (min-width:761px){.card{padding:var(--sp-6)}}
.card.flush{padding:0;overflow:hidden}
.note{border:1px solid var(--warn-line);background:var(--warn-tint);border-radius:var(--r-row);padding:var(--sp-3) var(--sp-4);font-size:var(--t-sm)}
/* colour */
.ramp{display:flex;border-radius:var(--r-input);overflow:hidden;border:1px solid var(--rule)}
.ramp span{flex:1;min-width:0;height:56px;display:flex;flex-direction:column;justify-content:flex-end;padding:4px 0;text-align:center;font-size:var(--t-cap);font-weight:600}
.sw{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:var(--sp-3)}
.sw > div{border:1px solid var(--rule);border-radius:var(--r-row);overflow:hidden;background:var(--surface);min-width:0}
.sw i{display:block;height:44px;border-bottom:1px solid var(--rule)}
.sw div div{padding:var(--sp-2) var(--sp-3) var(--sp-3);display:flex;flex-direction:column;gap:2px;font-size:var(--t-cap);color:var(--slate)}
.sw b{font-size:var(--t-sm);color:var(--ink);font-weight:600}
/* type, space, radii */
.trow{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--sp-1);padding:var(--sp-3) 0;border-top:1px solid var(--rule)}
.trow:first-child{border-top:0;padding-top:0}.trow:last-child{padding-bottom:0}
.trow > span:first-child{overflow-wrap:anywhere}
@media (min-width:761px){.trow{grid-template-columns:minmax(0,1fr) 300px;gap:var(--sp-6);align-items:baseline}}
.bars{display:flex;flex-wrap:wrap;align-items:flex-end;gap:var(--sp-4)}
.bars div{display:flex;flex-direction:column;align-items:center;gap:var(--sp-2);font-size:var(--t-cap);color:var(--slate);text-align:center}
.bars i{display:block;background:var(--sea)}
.rad i{width:72px;height:56px;background:var(--surface-2);border:1px solid var(--control)}
.two{display:grid;gap:var(--sp-4);grid-template-columns:minmax(0,1fr)}
@media (min-width:761px){.two{grid-template-columns:repeat(2,minmax(0,1fr))}}
.glow-demo{height:180px;border-radius:var(--r-card);border:1px solid var(--rule);display:flex;align-items:flex-end;padding:var(--sp-4);
  background:radial-gradient(110% 70% at 0% 100%,var(--glow-from) 0%,var(--paper) 60%)}
/* components */
.comp{display:flex;flex-direction:column;gap:var(--sp-3)}
.states{display:grid;gap:var(--sp-3);grid-template-columns:minmax(0,1fr)}
@media (min-width:761px){.states{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:1100px){.states{grid-template-columns:repeat(4,minmax(0,1fr))}.states.wide{grid-template-columns:repeat(2,minmax(0,1fr))}}
.st{display:flex;flex-direction:column;gap:var(--sp-3);padding:var(--sp-4);border:1px solid var(--rule);border-radius:var(--r-card);background:var(--paper);min-width:0}
.st-h{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2)}
.st-demo{display:flex;flex-direction:column;justify-content:center;min-width:0}
.st-t{font-size:var(--t-sm);color:var(--slate)}
@media (min-width:761px){.st-demo{min-height:96px}}
.src{font-size:var(--t-cap);font-weight:600;letter-spacing:.02em;padding:2px 8px;border-radius:var(--r-tag);background:var(--surface-2);color:var(--slate);white-space:nowrap}
.src.follows{background:var(--exp-bg);border:1px dashed var(--exp-line);color:var(--exp-text)}
.stack{display:flex;flex-direction:column;gap:var(--sp-3);align-items:flex-start;min-width:0}.stack > .tabbar,.stack > .sk{align-self:stretch}
.btn{font:inherit;font-weight:600;min-height:var(--btn);padding:0 var(--sp-6);border-radius:var(--r-pill);border:1px solid var(--control);background:var(--surface-2);
  color:var(--ink);cursor:pointer;transition:transform var(--d-fast) var(--ease),filter var(--d-base) var(--ease)}
.btn:active:not(:disabled){transform:scale(.97)}
.btn.pri{background:var(--sea);border-color:var(--sea);color:var(--on-sea)}
.btn.dan{border-color:var(--loss-line);color:var(--loss)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.sm-btn{min-height:var(--tap);padding:0 var(--sp-4);font-size:var(--t-sm)}
.chips{display:flex;flex-wrap:wrap;gap:var(--sp-2)}
.chip{font:inherit;font-size:var(--t-sm);font-weight:500;min-height:var(--tap);padding:0 var(--sp-4);border-radius:var(--r-pill);border:1px solid var(--control);
  background:var(--surface);color:var(--ink);cursor:pointer;transition:background-color var(--d-fast) var(--ease),color var(--d-fast) var(--ease)}
.chip[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.tchip{display:inline-flex;align-items:center;gap:var(--sp-2);min-height:var(--tap);max-width:100%;padding:0 var(--sp-3);border-radius:var(--r-pill);border:1px solid var(--control);background:var(--surface)}
.tchip.checking{border-style:dashed}
.tchip.no{border-color:var(--loss-line);background:var(--loss-tint);color:var(--loss)}
.tchip .sm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.strike{text-decoration:line-through;text-decoration-color:var(--loss);color:var(--ink)}
.tabbar{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));width:100%;max-width:390px;padding:6px 4px 8px;background:var(--bar-bg);border:1px solid var(--rule);border-radius:var(--r-row)}
.tab{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-height:var(--tab-item);padding-top:6px;color:var(--stale);
  font-size:var(--t-cap);font-weight:600;letter-spacing:.02em;min-width:0}
.tab.on{color:var(--ink)}
.tab.on::before{content:"";position:absolute;top:0;left:50%;width:6px;height:6px;margin-left:-3px;border-radius:999px;background:var(--live)}
.tico{position:relative;display:inline-flex}
.tdot{position:absolute;top:-2px;right:-4px;width:9px;height:9px;border-radius:999px;border:2px solid var(--paper)}
.tdot.breach{background:var(--loss)}.tdot.offline{background:var(--warn)}
.rows{list-style:none;margin:0;padding:0}
.row{display:flex;align-items:center;gap:var(--sp-3);min-height:var(--row);padding:var(--sp-3) var(--sp-4);border-top:1px solid var(--rule)}
.row:first-child{border-top:0}
.rt{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.3}
.rt .sm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.state{display:flex;flex-direction:column;gap:var(--sp-2);font-size:var(--t-sm);line-height:1.5}
.det{font-size:var(--t-cap);overflow-wrap:anywhere}
.tiles{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-3)}
.tile{position:relative;flex:none;display:inline-flex;align-items:center;justify-content:center;color:#F4F5F7;font-weight:700;letter-spacing:-.01em;overflow:hidden;
  -webkit-mask:SQUIRCLE center / 100% 100% no-repeat;mask:SQUIRCLE center / 100% 100% no-repeat}
.tile::after{content:"";position:absolute;inset:0;pointer-events:none;background:SQLINE center / 100% 100% no-repeat}
.tile.sys{background:var(--surface-2);color:var(--loss)}
.glyph{display:block;width:76%;height:76%;border-radius:999px;background:conic-gradient(from 210deg,#0A0B0D 0 62%,transparent 0);
  -webkit-mask:radial-gradient(circle,transparent 34%,#000 35%);mask:radial-gradient(circle,transparent 34%,#000 35%)}
.glyph.in{animation:fade var(--d-base) var(--ease) both}
/* the two loops the rule allows (brand_system_v1.md section 4, #307), named as in apps/web/src/app.css; both stop with reduce motion */
.sk{display:block;background:var(--surface-2);flex:none;max-width:100%;animation:kb-breathe 1.6s ease-in-out infinite}
.still .sk{animation:none}
.live-dot{position:relative;display:inline-block;flex:none;width:var(--point);height:var(--point);border-radius:999px;background:var(--live)}
.live-dot::after{content:"";position:absolute;inset:0;border-radius:inherit;background:inherit;animation:kb-pulse 2s var(--ease) infinite}
.still .live-dot::after{display:none}
.srow{display:flex;align-items:center;gap:var(--sp-3);min-height:56px;padding:var(--sp-3) var(--sp-4);border-radius:var(--r-row);border:1px solid var(--rule);background:var(--surface);
  font-size:var(--t-sm);font-weight:500}
.srow svg{flex:none}
.srow.halt{background:var(--loss-tint);border-color:var(--loss-line)}.srow.halt i{flex:none;width:8px;height:8px;border-radius:999px;background:var(--loss)}
.srow.warn{background:var(--warn-tint);border-color:var(--warn-line)}.srow.warn svg{color:var(--warn-text)}
.srow.exp{color:var(--exp-text);border:1px dashed var(--exp-line);padding-left:22px;
  background:repeating-linear-gradient(135deg,var(--exp-hatch) 0 2px,transparent 2px 6px) left / 8px 100% no-repeat,var(--exp-bg)}
.tags{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:center}
.tag{display:inline-flex;align-items:center;gap:4px;height:22px;padding:0 8px;border-radius:var(--r-pill);font-size:var(--t-cap);font-weight:600;letter-spacing:.02em;white-space:nowrap;
  background:var(--surface-2);color:var(--slate)}
.tag.up{background:var(--gain-tint);color:var(--gain)}.tag.down{background:var(--loss-tint);color:var(--loss)}.tag.ai{background:var(--ai-tint);color:var(--ai)}
.tag.exp{background:var(--exp-bg);color:var(--exp-text);border:1px dashed var(--exp-line)}
.field{display:flex;flex-direction:column;gap:6px;font-size:var(--t-sm)}
.field input{font:inherit;font-size:var(--t-md);min-height:48px;padding:0 var(--sp-4);border-radius:var(--r-input);border:1px solid var(--control);background:var(--paper);color:var(--ink);width:100%}
.field.bad input{border-color:var(--loss)}
/* logo */
.clear{position:relative;display:inline-block;padding:CLEARpx;border:1px dashed var(--exp-line);border-radius:4px;line-height:0;max-width:100%}
.clear svg{max-width:100%;height:auto}
.on-light{background:#F4F5F7;border:1px solid var(--rule);border-radius:var(--r-row);padding:var(--sp-4);display:flex;align-items:center;gap:var(--sp-6);flex-wrap:wrap}
.on-dark{background:#0A0B0D;border:1px solid var(--rule);border-radius:var(--r-row);padding:var(--sp-4);display:flex;align-items:center;gap:var(--sp-6);flex-wrap:wrap}
/* motion */
@keyframes kb-breathe{0%,100%{opacity:1}50%{opacity:.55}}
@keyframes kb-pulse{0%{transform:scale(1);opacity:.55}70%,100%{transform:scale(2.4);opacity:0}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes draw{from{transform:scaleX(0)}to{transform:none}}
@keyframes pop{from{transform:scale(0)}to{transform:none}}
@keyframes pulse{from{opacity:.6;transform:none}to{opacity:0;transform:scale(2.4)}}
@keyframes wipe{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
.mo{display:flex;flex-wrap:wrap;gap:var(--sp-6);align-items:center;min-height:72px}
.mo.loops .sk{flex:1 1 120px;max-width:220px}
.mo .m-rise{padding:var(--sp-3) var(--sp-4);border-radius:var(--r-row);background:var(--surface-2);border:1px solid var(--rule);font-size:var(--t-sm)}
.mo .m-line{position:relative;width:140px;height:10px}
.mo .m-line i{position:absolute;left:0;top:4px;width:130px;height:2px;border-radius:2px;background:var(--s-pot);transform-origin:left}
.mo .m-line b{position:absolute;right:0;top:0;width:10px;height:10px;border-radius:999px;background:var(--live)}
.mo .m-line u{position:absolute;right:0;top:0;width:10px;height:10px;border-radius:999px;border:2px solid var(--live);opacity:0}
.play .m-rise{animation:rise var(--d-sheet) var(--ease) both}
.play .m-line i{animation:draw var(--d-draw) var(--ease) both}
.play .m-line b{animation:pop var(--d-point) var(--ease-pop) var(--d-draw) both}
.play .m-line u{animation:pulse .6s var(--ease) calc(var(--d-draw) + var(--d-point)) forwards}
.logo-play.play svg{animation:wipe .96s var(--ease) both}
/* tables */
.tbl{width:100%;border-collapse:collapse;font-size:var(--t-sm)}
.tbl th{text-align:left;font-size:var(--t-cap);font-weight:600;color:var(--slate);padding:var(--sp-2) var(--sp-3) var(--sp-2) 0;border-bottom:1px solid var(--rule);vertical-align:bottom}
.tbl td{padding:var(--sp-2) var(--sp-3) var(--sp-2) 0;border-bottom:1px solid var(--rule);vertical-align:top;overflow-wrap:anywhere}
.tbl tr:last-child td{border-bottom:0}
.tbl .r{text-align:right;white-space:nowrap;padding-right:0}
.tbl i.dot{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px;border:1px solid var(--control)}
.tbl.kv td:first-child{white-space:nowrap;overflow-wrap:normal;color:var(--ink)}
@media (max-width:760px){.tbl.kv,.tbl.kv tbody,.tbl.kv tr,.tbl.kv td{display:block}.tbl.kv td{border:0;padding:0}.tbl.kv tr{padding:var(--sp-3) 0;border-bottom:1px solid var(--rule)}
  .tbl.kv tr:first-child{padding-top:0}.tbl.kv tr:last-child{border:0;padding-bottom:0}}
.scroll{overflow-x:auto}
details > summary{cursor:pointer;min-height:var(--tap);display:flex;align-items:center;font-weight:600}
ul.plain{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:var(--sp-2);font-size:var(--t-sm);color:var(--slate)}
ul.plain b{color:var(--ink);font-weight:600}
.diffs{display:flex;flex-direction:column;gap:var(--sp-3)}
.diff{display:grid;gap:var(--sp-1) var(--sp-6);grid-template-columns:minmax(0,1fr);padding:var(--sp-4);border:1px solid var(--rule);border-radius:var(--r-row);background:var(--surface);font-size:var(--t-sm)}
.diff h3{font-size:var(--t-md)}
.diff .k{font-size:var(--t-cap);font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--stale)}
.diff p{color:var(--slate)}
@media (min-width:761px){.diff{grid-template-columns:180px repeat(3,minmax(0,1fr))}}
footer{font-size:var(--t-sm);color:var(--slate);display:flex;flex-direction:column;gap:var(--sp-2)}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.live-dot::after{display:none}.mo .m-line u{opacity:0}}
"""

JS = """
(function(){
  var root=document.documentElement,btn=document.getElementById('theme');
  function label(){btn.textContent=root.dataset.theme==='light'?'Show the dark theme':'Show the light theme';}
  btn.addEventListener('click',function(){root.dataset.theme=root.dataset.theme==='light'?'dark':'light';label();});
  label();
  function play(el){el.classList.remove('play');void el.offsetWidth;el.classList.add('play');}
  document.querySelectorAll('[data-replay-btn]').forEach(function(b){
    b.addEventListener('click',function(){var box=b.closest('.st,.card');box.querySelectorAll('[data-replay]').forEach(play);});});
  document.querySelectorAll('[data-replay]').forEach(play);
})();
"""


def ratio_of(mode: str, what: str) -> str:
    r = next(r for r in C[mode] if r[0] == what)
    return f"{r[3]:.2f}:1"


def contrast_table(mode: str) -> str:
    rows = []
    for what, fg, bg, ratio, need in C[mode]:
        old = (mode, what) in STALE_WHAT
        rows.append(f'<tr><td>{esc(what)}{" <b>(out of date, see findings)</b>" if old else ""}</td><td><i class="dot" style="background:{fg}"></i>{fg}</td>'
                    f'<td><i class="dot" style="background:{bg}"></i>{bg}</td><td class="r">{ratio:.2f}:1</td><td class="r">{need:g}:1</td></tr>')
    return ('<div class="scroll"><table class="tbl"><thead><tr><th>What</th><th>Colour</th><th>On</th><th class="r">Ratio</th><th class="r">Needs</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def build_html() -> str:
    d, li = T["dark"], T["light"]
    D = demos()
    sq = squircle()
    css = (CSS.replace("SQUIRCLE", data_svg(f"<path d='{sq}'/>"))
           .replace("SQLINE", data_svg(f"<path d='{sq}' fill='none' stroke='white' stroke-opacity='.08' stroke-width='2' vector-effect='non-scaling-stroke'/>"))
           .replace("NOISE", "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' "
                    "baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .035 0'/%3E%3C/filter%3E%3Crect width='100%25' "
                    "height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")")
           .replace("CLEAR", "0").replace("TYPEVARS", TYPE_VARS).replace("TYPEDESK", TYPE_DESK))
    ramp = lambda pre: '<div class="ramp">' + "".join(  # noqa: E731
        f'<span style="background:{v};color:{"#0A0B0D" if colour.lum(v) > .3 else "#F4F5F7"}">{k.split("-")[1]}</span>' for k, v in T["scale"].items() if k.startswith(pre)) + "</div>"
    sw = "".join(f'<div><i style="background:var(--{t})"></i><div><b>--{t}</b><span>{d[t]} dark</span><span>{li[t]} light</span><span>{use}</span></div></div>' for t, use in ROLES)
    typ = "".join(f'<div class="trow"><span style="{st}">{esc(sample)}</span><span class="sm"><b style="color:var(--ink)">{n}</b> <code>{tok}</code><br>{size}, tracking {trk}. For {use}.</span></div>'
                  for n, tok, size, trk, use, st, sample in TYPE)
    space = "".join(f'<div><i style="width:{s}px;height:{s}px"></i>{s}</div>' for s in SPACE)
    radii = "".join(f'<div><i style="border-radius:{"999px" if r == "pill" else r + "px"}"></i>{r}<br>{use}</div>' for r, use in RADII)
    sizes = "".join(f'<div><i style="width:12px;height:{s}px;border-radius:6px"></i>{s}<br>{use}</div>' for s, use in SIZES)
    comps = ""
    for name, *cells in STATES:
        cs = "".join(cell(lab, cells[2 * i], cells[2 * i + 1], D[name][i]) for i, lab in enumerate(["Default", "Loading", "Empty", "Error"]))
        comps += f'<div class="comp" id="c-{name.lower().replace(" ", "-")}"><h3>{name}</h3><div class="states{" wide" if name == "Tabs" else ""}">{cs}</div></div>'
    motion = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td></tr>" for a, b in MOTION)
    finds = "".join(f"<li>{x}</li>" for x in findings())
    diffs = "".join(f'<div class="diff"><h3>{n}</h3><p><span class="k">This page</span><br>{a}</p><p><span class="k">The app today</span><br>{b}</p><p><span class="k">Next step</span><br>{c}</p></div>'
                    for n, a, b, c in DIFFS)
    extra = "".join(f'<tr><td>{esc(w)}</td><td><i class="dot" style="background:{fg}"></i>{fg}</td><td><i class="dot" style="background:{bg}"></i>{bg}</td><td class="r">{r:.2f}:1</td><td class="r">{n:g}:1</td></tr>'
                    for w, fg, bg, r, n in EXTRA_ROWS)
    extra += "".join(f'<tr><td>{esc(w)} ({m}), <code>--{tok}</code> as it is now</td><td><i class="dot" style="background:{new}"></i>{new}</td><td><i class="dot" style="background:{bg}"></i>{bg}</td>'
                     f'<td class="r">{now:.2f}:1</td><td class="r">{need:g}:1</td></tr>' for m, w, tok, old, new, bg, was, now, need in DRIFT)
    word_d, word_l = logo("wordmark-on-dark.svg", 64), logo("wordmark-on-light.svg", 64)
    body = f"""
<header class="top">
  <span class="only-dark">{logo("wordmark-on-dark.svg", 24, tight=True)}</span><span class="only-light">{logo("wordmark-on-light.svg", 24, tight=True)}</span>
  <span class="pill">PAPER</span><span class="grow"></span>
  <button type="button" class="btn sec sm-btn" id="theme">Show the light theme</button>
</header>
<main>
  <div class="intro">
    <span class="cap">Design system, brand v1</span>
    <h1>One page for how kaibo looks and moves</h1>
    <p class="lead">The reference for how the app looks and moves (issue #{ISSUE}, 17 Nov 2026). It replaces the v3 "night" design-system board. Every colour on this page is read from
      <code>tokens_v1.css</code>, every ratio from <code>contrast_v1.md</code>, and the logo from Design's own files. Sample names and numbers are invented.</p>
    <nav class="toc" aria-label="On this page"><a href="#colour">Colour</a><a href="#type">Type</a><a href="#shape">Spacing and shape</a><a href="#glow">The glow</a>
      <a href="#components">Components</a><a href="#logo">Logo</a><a href="#tile">Company tile</a><a href="#motion">Motion</a><a href="#contrast">Contrast</a><a href="#app">The app today</a></nav>
    <p class="note">Decided by Product on 18 Nov 2026 (#{DECIDED}): the type steps are 17 / 15 / 13; the company tile is signed off as built; and the motion rule is
      "no data motion loops", with two loops allowed (skeletons breathe, the live dot pulses). This page shows all three.</p>
  </div>

  <section id="colour">
    <h2>Colour</h2>
    <p class="lead">Three values come straight from the logo: blue <code>{T["scale"]["blue-400"]}</code>, white <code>{T["scale"]["grey-50"]}</code>, black <code>{T["scale"]["grey-950"]}</code>.
      Blue is kept for four things: the logo, the one primary action on a screen, links and focus, and the live point. Dark is the theme the app runs; light is defined and off.</p>
    {ramp("blue")}{ramp("grey")}
    <div class="sw">{sw}</div>
    <p class="sm">A gain or a loss is never colour alone: the figure carries its arrow and its sign, as in <span class="num gain">+$21.40</span> and <span class="num loss">&minus;$8.15</span>.</p>
  </section>

  <section id="type">
    <h2>Type</h2>
    <p class="lead">Outfit for everything, numbers too, with tabular figures on. Weights: 400 text, 500 status rows, 600 headings and buttons, 700 the hero figure and titles.
      Body, small and caption are 17 / 15 / 13 (one step larger since #294, confirmed in #{DECIDED}). Nothing below 12 px. Body text gets <code>word-spacing: 0.06em</code>.</p>
    <div class="card">{typ}</div>
  </section>

  <section id="shape">
    <h2>Spacing and shape</h2>
    <p class="lead">One unit is the logo's stem, 4 px in the app. Line ends are round everywhere; icons and chart lines are <code>{T["shape"]["stroke"]}</code> with round caps and joins.</p>
    <div class="two">
      <div class="card"><div class="stack"><span class="cap">Spacing, px</span><div class="bars">{space}</div></div></div>
      <div class="card"><div class="stack"><span class="cap">Corners, px</span><div class="bars rad">{radii}</div></div></div>
      <div class="card"><div class="stack"><span class="cap">Heights, px</span><div class="bars">{sizes}</div></div></div>
      <div class="card"><div class="stack"><span class="cap">The point</span>
        <div class="tiles"><i style="width:{T["shape"]["point"]};height:{T["shape"]["point"]};border-radius:999px;background:var(--live);display:block"></i>
        <span class="sm"><code>--point</code> {T["shape"]["point"]}, 10 px at the end of the chart line, 6 px above the active tab. The one live indicator. When the engine is stale or stopped it is not shown; a status row says so in words.</span></div>
        <span class="cap">Focus</span><div><button type="button" class="chip" style="outline:2px solid var(--focus);outline-offset:2px">Focused</button></div>
        <span class="sm">2 px <code>--focus</code> ring, 2 px offset, on every control, in both themes.</span></div></div>
    </div>
  </section>

  <section id="glow">
    <h2>The glow</h2>
    <p class="lead">Every page stands on one light: a radial glow of <code>--sea-tint</code> fading into <code>--paper</code>, 110% by 70% of the screen, with a faint noise so the fade does not band.
      It is one layer fixed to the screen, so it never scrolls or repeats. Cards, rows and sheets stay opaque on top of it. This page stands on it, anchored top right as Home is.</p>
    <div class="two">
      <div class="glow-demo"><span class="sm">Sign-in: bottom left</span></div>
      <div class="glow-demo" style="background:radial-gradient(110% 70% at 100% 0%,var(--glow-from) 0%,var(--paper) 60%)"><span class="sm">Home: top right. Positions, Activity, Engine and More sit a little off it.</span></div>
    </div>
  </section>

  <section id="components">
    <h2>Components</h2>
    <p class="lead">Each one in its four states. Narrow the window to phone width (under 761 px) and the same page shows the phone layout. The label on each state says where it comes from:
      <span class="src rule">rule</span> is written in a design document, <span class="src built">as built</span> is how the app does it today with no written rule,
      <span class="src follows">follows</span> is this page applying a written rule to a case no document names. The last two need Design's sign-off.
      The company tile was "as built" until it was signed off in #{DECIDED}; its rule is now in <code>brand_system_v1.md</code>, section 5.</p>
    {comps}
    <div class="comp"><h3>Status rows, tags and a form field</h3>
      <div class="two">
        <div class="stack" style="align-items:stretch">
          <div class="srow halt"><i></i><span>Trading is halted: the daily loss limit was reached.</span></div>
          <div class="srow warn">{icon("shield", 20)}<span>Paper trading. No real money moves.</span></div>
          <div class="srow exp">{icon("flask", 20)}<span>Experiment: failed the ten-year test</span></div>
          <span class="sm">Order on a screen: halt, caution, experiment. Dashed means provisional.</span>
        </div>
        <div class="stack" style="align-items:stretch">
          <div class="tags"><span class="tag up">&#9650; Gain</span><span class="tag down">&#9660; Loss</span><span class="tag">Neutral</span><span class="tag ai">Agent</span><span class="tag exp">{icon("flask", 12)} Experiment</span></div>
          <label class="field">Email<input type="text" value="you@example.com" aria-label="Email, default"></label>
          <label class="field bad">Email<input type="text" value="you@example" aria-label="Email, error" aria-invalid="true"><span class="loss-t">That address is missing its ending, like .com</span></label>
        </div>
      </div>
      <p class="sm">Sheet, meter and the tables that remain keep their rules in <code>brand_system_v1.md</code>, section 5; they are not redrawn here.</p>
    </div>
  </section>

  <section id="logo">
    <h2>Logo</h2>
    <p class="lead">The Rising k. The k's blue arm is the pot's line and the blue point sits on the i. The header, sign-in and New run show the wordmark alone.</p>
    <div class="two">
      <div class="on-dark">{logo("wordmark-on-dark.svg", 40, tight=True)}{logo("symbol-on-dark.svg", 56)}</div>
      <div class="on-light">{logo("wordmark-on-light.svg", 40, tight=True)}{logo("symbol-on-light.svg", 56)}</div>
    </div>
    <h3>Clear space</h3>
    <div class="two">
      <div class="on-dark"><span class="clear">{word_d}</span></div>
      <div class="on-light"><span class="clear" style="border-color:#5D646F">{word_l}</span></div>
    </div>
    <p class="sm">The dashed box is the edge of the wordmark file itself. Design's files carry their own margin: 14 units on every side of the wordmark, where the stem is 15 units wide and the
      letters 88 tall, so the clear space is about one stem (<code>logo/gen/build.py</code>, <code>pad=14</code>; the symbol sits in a 120-unit square). Keep text, edges and other marks outside that box.
      No document states a clear-space rule or a smallest size in words yet: that is a finding for Design, listed under <a href="#app">The app today</a>.</p>
  </section>

  <section id="tile">
    <h2>Company tile</h2>
    <p class="lead">Wherever a stock is named: a squircle (a superellipse, n = 5, the curve of an app icon) with the company's logo on a colour sampled from that logo
      (grey 50 when none was sampled), or the ticker's initials. A hairline, white at 8%, follows the same curve so the tile keeps its edge on the glow.
      Signed off as built (#{DECIDED}); the rule is in <code>brand_system_v1.md</code>, section 5.</p>
    <div class="card"><div class="stack">
      <div class="tiles">{tile("logo", 32)}{tile("logo", 36)}{tile("logo", 40)}{tile("logo", 52)}{tile("init", 32, "KBO", 0)}{tile("init", 36, "DEMO", 5)}{tile("init", 40, "KBO", 7)}{tile("init", 52, "DEMO", 9)}{tile("halt", 36)}</div>
      <span class="sm">Sizes given: 32 (stacked faces on a theme card), 36 (rows, lists, tables), 40 (position and activity rows), 52 (a holding's own page). Each is drawn 8% larger
        (35, 39, 43, 56 px; #293, stakeholder, 17 Nov 2026). The logo keeps the padding sampled for it (12% until sampled). Initials: three or four letters, bold, never under 12 px, on one of ten dark tints
        picked by the ticker. The round mark here stands in for a company's logo; no real logo is used on this page.</span>
    </div></div>
  </section>

  <section id="motion">
    <h2>Motion</h2>
    <p class="lead">The logo's load animation as a family: things rise, lines draw, the point pops. No data motion loops: what shows data plays once.
      Two signals may loop, because they say "loading" or "live" and carry no data; both stop with reduce motion.</p>
    <div class="card"><div class="stack">
      <div class="mo" data-replay><span class="m-rise">A row rises</span><span class="m-line"><i></i><u></u><b></b></span><span class="sm">a line draws, the point pops and pulses once</span></div>
      <button type="button" class="btn sec sm-btn" data-replay-btn>Play again</button>
    </div></div>
    <div class="two">
      <div class="card"><div class="stack" style="align-items:stretch"><span class="cap">The two loops</span>
        <div class="mo loops" role="img" aria-label="A skeleton bar breathing and the live dot pulsing">{sk("100%", 20)}<span class="live-dot"></span><span class="sm">a skeleton breathes, 1.6 s; the live dot pulses, 2 s</span></div></div></div>
      <div class="card"><div class="stack" style="align-items:stretch"><span class="cap">With reduce motion</span>
        <div class="mo loops still" role="img" aria-label="A still skeleton bar and the live dot without its ring">{sk("100%", 20)}<span class="live-dot"></span><span class="sm">both stand still</span></div></div></div>
    </div>
    <div class="card"><table class="tbl kv"><tbody>{motion}</tbody></table></div>
  </section>

  <section id="contrast">
    <h2>Contrast</h2>
    <p class="lead">Ratios are WCAG 2, taken from <code>contrast_v1.md</code>. Text needs 4.5:1; large text and the parts of a control need 3:1. Nothing is changed by this page; what does not hold is listed.</p>
    <div class="card"><h3 style="margin-bottom:12px">Findings</h3><ul class="plain">{finds}</ul></div>
    <div class="card"><details><summary>Dark theme, {len(C["dark"])} pairs from contrast_v1.md</summary>{contrast_table("dark")}</details>
      <details><summary>Light theme, {len(C["light"])} pairs from contrast_v1.md</summary>{contrast_table("light")}</details>
      <details><summary>Measured for this page, not in contrast_v1.md</summary><div class="scroll"><table class="tbl"><thead><tr><th>What</th><th>Colour</th><th>On</th><th class="r">Ratio</th><th class="r">Needs</th></tr></thead>
        <tbody>{extra}</tbody></table></div></details></div>
  </section>

  <section id="app">
    <h2>The app today</h2>
    <p class="lead">Where <code>apps/web</code> on main (read 17 Nov 2026; the type steps, the skeleton and the loops checked again on 18 Nov for #{DECIDED}) differs from this page, for one Frontend follow-up story. Nothing in the app was changed. The colours, the font,
      the focus ring, the tab bar, the filter chips, the experiment's lab note and the glow already match.</p>
    <div class="diffs">{diffs}</div>
    <div class="card"><h3 style="margin-bottom:12px">For Design, not Frontend</h3><ul class="plain">
      <li><b>The logo has no written clear-space rule and no smallest size.</b> This page shows the margin the files carry. A sentence in <code>logo_concepts.md</code> would settle it.</li>
      <li><b>Spacing steps.</b> <code>brand_system_v1.md</code> lists 4, 8, 12, 16, 24, 32 and says they are the v4 steps; <code>mobile_first_v4.md</code> and the app also have 20 and 48. This page shows the brand list.</li>
      <li><b>Card, button, tab, chip, logo and skeleton states marked "follows" or "as built"</b> on this page have no rule in any document yet.</li>
    </ul></div>
  </section>

  <footer>
    <span>Built by <code>docs/design/brand/gen/designsystem.py</code> from <code>tokens_v1.css</code>, <code>contrast_v1.md</code>, <code>brand_system_v1.md</code>, <code>mobile_first_v4.md</code> and
      <code>docs/design/logo/</code>. Outfit is under the SIL Open Font License 1.1, &copy; 2021 The Outfit Project Authors.</span>
    <span>Paper trading only. Nothing on this page is loaded by the app.</span>
  </footer>
</main>
"""
    return ("<!doctype html>\n<html lang=\"en\" data-theme=\"dark\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>kaibo design system, brand v1</title>\n<!-- Written by docs/design/brand/gen/designsystem.py (issue #{ISSUE}). Do not edit by hand. -->\n"
            f"<style>\n{font_face()}\n/* ---- tokens_v1.css, unchanged ---- */\n{(BRAND / 'tokens_v1.css').read_text()}\n/* ---- this page ---- */{css}</style>\n</head>\n<body>{body}<script>{JS}</script>\n</body>\n</html>\n")


def strip(s: str) -> str:
    """Page markup to Markdown text: code tags to backticks, bold to stars, entities back."""
    s = re.sub(r"<code>(.*?)</code>", r"`\1`", s)
    s = re.sub(r"<b>(.*?)</b>", r"**\1**", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).replace("|", "\\|")


def build_md() -> str:
    d, li = T["dark"], T["light"]
    out = [f"""# kaibo design system, brand v1

Issue #{ISSUE}. The one reference for how the app looks and moves. It replaces the v3 "night" design-system board
(`docs/design/v3/project/v3/DesignSystem.dc.html`, issue #79).

- **The page:** [`design_system_v1.html`](design_system_v1.html). Self-contained; open it in a browser. It has a switch
  for the light theme, and it is responsive: under 761 px it shows the phone layout.
- **Proofs:** [phone, 390 px](proof/design-system-phone.png) and [desktop, 1280 px](proof/design-system-desktop.png).
- **Sources, not repeated by hand:** colours from [`tokens_v1.css`](tokens_v1.css), ratios from
  [`contrast_v1.md`](contrast_v1.md), type, shape and component rules from [`brand_system_v1.md`](brand_system_v1.md),
  sizes and motion from [`mobile_first_v4.md`](../mobile_first_v4.md), the logo from [`../logo/`](../logo/).
- Written by `gen/designsystem.py`. Run it again after any of those files change; do not edit this file by hand.

Decided by Product on 18 Nov 2026 (#{DECIDED}): the type steps are 17 / 15 / 13; the company tile is signed off as built;
the motion rule is "no data motion loops", with two loops allowed (skeletons breathe, the live dot pulses).

![The page at desktop width](proof/design-system-desktop.png)

## Colour

Blue is kept for four things: the logo, the one primary action on a screen, links and focus, and the live point.
Dark is the theme the app runs; light is defined and off. A gain or a loss always carries its arrow and its sign.

| Token | Dark | Light | For |
|---|---|---|---|"""]
    out += [f"| `--{t}` | `{d[t]}` | `{li[t]}` | {use} |" for t, use in ROLES]
    out += ["", "## Type", "",
            "Outfit for everything, numbers too, with tabular figures on. Weights 400, 500, 600, 700. Nothing below 12 px.",
            f"Body, small and caption are 17 / 15 / 13: one step larger since #294, confirmed in #{DECIDED}.", "",
            "| Step | Token | Size / weight | Tracking | For |", "|---|---|---|---|---|"]
    out += [f"| {n} | `{tok}` | {size} | {trk} | {use} |" for n, tok, size, trk, use, _, _ in TYPE]
    out += ["", "## Spacing, shape, sizes", "",
            f"- **Spacing:** {', '.join(str(s) for s in SPACE)} px. One unit is the logo's stem, 4 px.",
            "- **Corners:** " + ", ".join(f"{r} ({use})" for r, use in RADII) + ".",
            "- **Heights:** " + ", ".join(f"{s} px ({use})" for s, use in SIZES) + ".",
            f"- **Stroke:** `--stroke` {T['shape']['stroke']}, round caps and joins. **The point:** `--point` {T['shape']['point']}, 10 px at the end of the chart line, 6 px above the active tab.",
            "- **Focus:** 2 px `--focus` ring, 2 px offset, on every control.",
            "", "## The glow", "",
            "One layer fixed to the screen: a radial glow of `--sea-tint` fading into `--paper`, 110% by 70% of the screen, with a faint",
            "noise. Sign-in bottom left, Home top right, small offsets for the other tabs. Cards, rows and sheets stay opaque on it.",
            "", "## Components and their states", "",
            "Shown at phone and desktop width on the page. Where a cell comes from: **rule** is written in a design document;",
            "**as built** is how the app does it today with no written rule; **follows** is this page applying a written rule to a",
            "case no document names. The last two need Design's sign-off. The company tile was \"as built\" until it was signed",
            f"off in #{DECIDED}; its rule is in `brand_system_v1.md`, section 5.", "",
            "| Component | Default | Loading | Empty | Error |", "|---|---|---|---|---|"]
    for name, *c in STATES:
        out.append(f"| **{name}** | " + " | ".join(f"{strip(c[2 * i + 1])} *({SRC[c[2 * i]]})*" for i in range(4)) + " |")
    out += ["", "Status rows (halt, caution, experiment), tags and a form field are on the page too. Sheet, meter and tables keep their",
            "rules in `brand_system_v1.md`, section 5.",
            "", "## Logo", "",
            "The Rising k; the wordmark alone in the header, on sign-in and on New run. The files are used unchanged.", "",
            "**Clear space:** the files carry their own margin: 14 units on every side of the wordmark, where the stem is 15 units",
            "wide and the letters 88 tall, so about one stem (`logo/gen/build.py`, `pad=14`). Keep text, edges and other marks outside",
            "it. No document states a clear-space rule or a smallest size in words yet (a finding for Design).",
            "", "## Company tile", "",
            "A squircle (superellipse, n = 5) with the company's logo on a colour sampled from that logo (grey 50 when none was",
            "sampled), or the ticker's initials; a hairline, white at 8%, on the same curve. Sizes given: 32, 36, 40 and 52; each is",
            "drawn 8% larger (35, 39, 43, 56 px; #293, stakeholder, 17 Nov 2026). Initials: three or four letters, bold, never under",
            f"12 px, on one of ten dark tints. Signed off as built (#{DECIDED}; `apps/web/src/components/StockBadge.tsx`); the rule is in",
            "`brand_system_v1.md`, section 5.",
            "", "## Motion", "", "| | |", "|---|---|"]
    out += [f"| **{a}** | {strip(b)} |" for a, b in MOTION]
    out += ["", "## Contrast: findings", "",
            "Ratios are WCAG 2, from `contrast_v1.md`. Text needs 4.5:1; large text and parts of controls need 3:1. Nothing is",
            "changed here.", ""]
    out += [f"- {strip(x)}" for x in findings()]
    out += ["", "Measured for this page with `gen/colour.py`; **not** in `contrast_v1.md`:", "", "| What | Colour | On | Ratio | Needs |", "|---|---|---|---|---|"]
    out += [f"| {w} | `{fg}` | `{bg}` | {r:.2f}:1 | {n:g}:1 |" for w, fg, bg, r, n in EXTRA_ROWS]
    out += [f"| {w} ({m}), `--{tok}` as it is now | `{new}` | `{bg}` | {now:.2f}:1 | {need:g}:1 |" for m, w, tok, old, new, bg, was, now, need in DRIFT]
    out += ["", "## The app today", "",
            f"Where `apps/web` on main (read 17 Nov 2026; the type steps, the skeleton and the loops checked again on 18 Nov for #{DECIDED})",
            "differs from this page, for one Frontend follow-up story. Nothing in the app was",
            "changed. The colours, the font, the focus ring, the tab bar, the filter chips, the experiment's lab note and the glow",
            "already match.", "", "| What | This page | The app today | Next step |", "|---|---|---|---|"]
    out += [f"| {n} | {strip(a)} | {strip(b)} | {strip(c)} |" for n, a, b, c in DIFFS]
    out += ["", "For Design, not Frontend:", "",
            "- The logo has no written clear-space rule and no smallest size.",
            "- Spacing steps: `brand_system_v1.md` lists 4, 8, 12, 16, 24, 32; `mobile_first_v4.md` and the app also have 20 and 48.",
            "- The card, button, tab, chip, logo and skeleton states marked \"follows\" or \"as built\" have no rule in any document yet.",
            "", "## Limits", "",
            "- Drawn and checked in Chromium at 390 and 1280 px. Not tried on a real phone or in Safari.",
            "- The comparison with the app is from reading `apps/web/src` (tokens.css, v4tokens.css, app.css, the components), not from",
            "  measuring the running app.",
            "- The round mark on the company tiles stands in for a logo; sample names and numbers are invented.", ""]
    return "\n".join(out)


def shots() -> None:
    import io

    from PIL import Image
    from playwright.sync_api import sync_playwright

    url = (BRAND / "design_system_v1.html").as_uri()
    (BRAND / "proof").mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, w, h, scale in (("phone", 390, 844, 1), ("desktop", 1280, 900, 1)):
            page = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale, reduced_motion="reduce")
            page.goto(url)
            page.evaluate("document.fonts.ready")
            # the glow's noise is left out of the proof only: it makes the PNG several times larger and cannot be seen at this size
            page.add_style_tag(content="body::before{background:radial-gradient(110% 70% at 100% 0%,var(--glow-from) 0%,var(--paper) 60%)!important}")
            over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            out = BRAND / "proof" / f"design-system-{name}.png"
            # 256 colours, no dither: a third of the size, and flat UI colours survive it (the m7 previews are stored the same way)
            Image.open(io.BytesIO(page.screenshot(full_page=True))).convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(out, "PNG", optimize=True)
            print(f"{name}: {w} px, sideways overflow {over} px, {out.stat().st_size // 1024} KB")
        b.close()


if __name__ == "__main__":
    (BRAND / "design_system_v1.html").write_text(build_html())
    (BRAND / "design_system_v1.md").write_text(build_md())
    print(f"pairs {N_PAIRS}, below the line {len(BELOW)}, out of date {len(DRIFT)}, narrow {len(NARROW)}, extra below {len(EXTRA_BELOW)}")
    if "--shots" in sys.argv:
        shots()
