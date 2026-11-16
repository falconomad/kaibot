"""Builds the six kaibo logo concepts as SVG files (issue #255).

    python docs/design/logo/gen/build.py            # writes docs/design/logo/concepts/<n>-<slug>/*.svg

Symbols are drawn here from circles, straight strokes and one sine wave (geo.py). Wordmarks are outlined paths:
concept 1 is drawn by hand from one stem and one circle; the others are set in openly licensed fonts and turned
into outlines (fontpaths.py), so no file depends on a font being installed. The font files are not in the repo:
copy fonts.example.json to fonts.json and point it at your copies (see logo_concepts.md for names and licences).

Original work for kaibo. No third-party logo, glyph or asset is used, traced or imitated.
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys
from dataclasses import dataclass, field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # run with `python -I`: the script folder is not on the path

from fontpaths import Outliner  # noqa: E402
from geo import Shape, arc_band, bbox, capsule, disc, f, ring, rrect_d, segment_below, stroke_outline  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "concepts"

# Colours. Ink and night are the v4 text and background; the accent is the v4 token --sea.
COL = {
    "dark": {"ink": "#F4F5F7", "accent": "#4F95F0", "bg": "#0A0B0D", "tile": "#14171C"},    # on dark backgrounds
    "light": {"ink": "#0A0B0D", "accent": "#2F6FDE", "bg": "#F4F5F7", "tile": "#F4F5F7"},   # on light backgrounds
}
XH = 60.0     # every wordmark is scaled so its x-height is 60 units; the baseline is y = 0


def fonts() -> dict:
    p = os.environ.get("KAIBO_LOGO_FONTS") or str(HERE / "fonts.json")
    return json.loads(pathlib.Path(p).read_text())


# ------------------------------------------------------------------ symbols (drawn in a 120 x 120 box)

# The k of concept 1: stem 15 wide and 88 tall; arm and leg are one chevron that stands 4.5 off the stem.
K_W, K_ASC, K_GAP, K_VERTEX, K_ARM, K_LEG = 15.0, 88.0, 4.5, 0.42, 33.0, 36.0


def kit_k(x=0.0, base=0.0, point=False, arm_role="accent") -> tuple[list[Shape], float]:
    """Returns the k's shapes and its width. With point=True the arm's line carries on to a dot."""
    w = K_W
    v = (x + w + K_GAP + w / 2, base - XH * K_VERTEX)
    tip = (x + w + K_GAP + K_ARM, base - XH + w / 2)
    foot = (x + w + K_GAP + K_LEG, base - w / 2)
    out = [capsule(*v, *tip, w, arm_role)]
    if point:
        ux, uy = tip[0] - v[0], tip[1] - v[1]
        n = math.hypot(ux, uy)
        out.append(disc(tip[0] + ux / n * 22.5, tip[1] + uy / n * 22.5, 9.0, "accent"))   # on the arm's own line
    out += [capsule(*v, *foot, w), capsule(x + w / 2, base - K_ASC + w / 2, x + w / 2, base - w / 2, w)]
    return out, w + K_GAP + K_LEG + w / 2


def sym_rising_k() -> list[Shape]:
    """A lowercase k. Its arm is a rising line that ends in a point: the pot's line and where it is now."""
    return kit_k(point=True)[0]


def weighted_o(cx, cy, ro, thin, thick) -> list[Shape]:
    """A ring that is thin at the top and thick at the bottom; the thick part below the hole is the ballast."""
    ri, up = ro - (thin + thick) / 2, (thick - thin) / 2
    return [ring(cx, cy, ro, ri, 0, -up), segment_below(cx, cy, ro, cy - up + ri, "accent")]


def sym_weighted_o() -> list[Shape]:
    return weighted_o(60, 60, 42, 9, 27)


def sym_shallow_dip() -> list[Shape]:
    """Two rises. The second starts a little below where the first ended: a dip of about a quarter of a rise."""
    return [capsule(25, 97, 51, 55, 15), capsule(67, 65, 93, 23, 15, "accent")]


def sym_anchor_i() -> list[Shape]:
    """The i of kaibo as an anchor: dot, stem and one shallow arc."""
    return [disc(60, 20, 10, "accent"), capsule(60, 43, 60, 86, 14), arc_band(60, 34, 56, 12, 52, 128)]


def sym_sea_level() -> list[Shape]:
    """One calm wave above one level line."""
    length, amp, w, n = 84, 11, 15, 72
    pts = [(18 + length * i / n, 44 - amp * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return [stroke_outline(pts, w, "accent"), capsule(18, 84, 102, 84, w)]


# ------------------------------------------------------------------ wordmarks (baseline y = 0, x-height 60)

@dataclass
class Word:
    shapes: list
    width: float
    top: float                  # y of the highest point (negative)
    credit: str                 # how the letters were made
    marks: dict = field(default_factory=dict)   # named parts, e.g. the distinctive letter


def word_kit() -> Word:
    """kaibo drawn by hand from one stem (width 15) and one circle (diameter 60). No font."""
    w, A, g = 15.0, 88.0, 13.0
    out, x = [], 0.0

    def stem(sx, top):
        out.append(capsule(sx, -top + w / 2, sx, -w / 2, w))

    # k: stem, then arm and leg as one chevron standing a little off the stem; the arm is the rising line
    k, kw = kit_k()
    out += k
    x += kw + g * 0.55
    # a: circle, stem on the right
    out.append(ring(x + XH / 2, -XH / 2, XH / 2, XH / 2 - w)); stem(x + XH - w / 2, XH); x += XH + g
    # i: stem and dot
    stem(x + w / 2, XH); r = w * 0.6
    out.append(disc(x + w / 2, -XH - w * 0.5 - r, r, "accent")); x += w + g
    # b: tall stem on the left, circle
    stem(x + w / 2, A); out.append(ring(x + XH / 2, -XH / 2, XH / 2, XH / 2 - w)); x += XH + g * 0.8
    # o: circle
    out.append(ring(x + XH / 2, -XH / 2, XH / 2, XH / 2 - w)); x += XH
    return Word(out, x, -A, "Drawn for kaibo from one stem and one circle (no font).")


def word_font(key: str, credit: str, tracking=0.0, special=None) -> Word:
    """kaibo set in an open font and outlined. tracking in 1/1000 em. special = "weighted-o" or "live-dot"."""
    o = Outliner(fonts()[key])
    sc = XH / o.xheight
    out, x, marks = [], 0.0, {}
    names = [o.name(c) for c in "kaibo"]
    for i, (ch, g) in enumerate(zip("kaibo", names)):
        if ch == "o" and special == "weighted-o":
            x0, y0, x1, y1 = o.bounds(g)
            lb = o.bounds(o.name("l"))
            t = (lb[2] - lb[0]) * sc                                   # the font's stem width
            ro = (y1 - y0) / 2 * sc
            parts = weighted_o(x + (x0 + x1) / 2 * sc, -(y0 + y1) / 2 * sc, ro, 0.62 * t, 1.75 * t)
            out += parts; marks["o"] = parts
        elif ch == "i" and special == "live-dot":
            cs = sorted(o.contours(g), key=lambda c: c[1][1])           # by lowest point: stem first, dot last
            stem_ops, (dot_ops, db) = [op for c in cs[:-1] for op in c[0]], cs[-1]
            st = o.shape(g, sc, x, 0, ops=stem_ops)
            r = max(db[2] - db[0], db[3] - db[1]) / 2 * sc * 1.04
            dot = disc(x + (db[0] + db[2]) / 2 * sc, -(db[1] + db[3]) / 2 * sc, r, "accent")
            out += [st, dot]; marks["i"] = [st, dot]
        else:
            out.append(o.shape(g, sc, x, 0))
        adv = o.gs[g].width + tracking * o.upm / 1000
        if i + 1 < len(names):
            adv += o.kern(g, names[i + 1])
        x += adv * sc
    x -= tracking * o.upm / 1000 * sc
    bb = bbox(out)
    return Word(out, bb[2], bb[1], credit, marks)


# ------------------------------------------------------------------ the six concepts

@dataclass
class Concept:
    n: int
    slug: str
    name: str
    idea: str
    symbol: list                # shapes in the 120 box
    word: Word
    lockup: tuple | None        # (symbol height, symbol centre y, gap) in wordmark units; None = the wordmark is the lockup
    icon: float = 0.56          # symbol size as a share of the icon tile
    fav: float = 0.70           # the same for the favicon


def concepts() -> list[Concept]:
    ofl = "SIL Open Font License 1.1"
    w2 = word_font("outfit-600", f"Outfit SemiBold ({ofl}), outlined; the o is redrawn.", 0, "weighted-o")
    w6 = word_font("fraunces-600", f"Fraunces SemiBold ({ofl}), outlined; the dot of the i is redrawn.", -4, "live-dot")
    i6 = _fit(w6.marks["i"], 92)
    return [
        Concept(1, "rising-k", "Rising k", "The arm of the k is the pot's line; the dot is where it is now.",
                sym_rising_k(), word_kit(), None, 0.58, 0.74),
        Concept(2, "weighted-o", "Weighted o", "The o is heavy at the bottom, like a keel: it tips, then rights itself.",
                sym_weighted_o(), w2, None, 0.56, 0.72),
        Concept(3, "shallow-dip", "Shallow dip", "Two rises, and the step back between them is small.",
                sym_shallow_dip(), word_font("manrope-700", f"Manrope Bold ({ofl}), outlined.", -12), (104, -40, 26), 0.56, 0.74),
        Concept(4, "anchor-i", "Anchor i", "The i of kaibo is an anchor: it holds its place first.",
                sym_anchor_i(), word_font("jost-500", f"Jost Medium ({ofl}), outlined.", 6), (112, -43, 34), 0.58, 0.76),
        Concept(5, "sea-level", "Sea level", "Kai means sea: one calm wave above one firm line.",
                sym_sea_level(), word_font("sora-600", f"Sora SemiBold ({ofl}), outlined.", -14), (74, -37, 30), 0.62, 0.78),
        Concept(6, "live-dot", "Live dot", "A plain wordmark; the dot of the i is the engine's live light.",
                i6, w6, None, 0.62, 0.78),
    ]


@dataclass(frozen=True)
class Group:
    """Shapes under one scale-and-move transform. Has a box, so it lays out like a single shape."""
    inner: list
    s: float
    tx: float
    ty: float

    @property
    def box(self) -> tuple:
        x0, y0, x1, y1 = bbox(self.inner)
        return x0 * self.s + self.tx, y0 * self.s + self.ty, x1 * self.s + self.tx, y1 * self.s + self.ty


def _fit(shapes, height, box=120) -> list:
    """Scale shapes to the given height and centre them in the symbol box."""
    x0, y0, x1, y1 = bbox(shapes)
    s = height / (y1 - y0)
    return [Group(shapes, s, box / 2 - (x0 + x1) / 2 * s, box / 2 - (y0 + y1) / 2 * s)]


# ------------------------------------------------------------------ SVG output

def paint(shapes, theme: str | None) -> str:
    """theme: "dark" / "light" (two colours) or None (one colour: currentColor)."""
    out = []
    for s in shapes:
        if isinstance(s, Group):
            out.append(f'<g transform="translate({f(s.tx)} {f(s.ty)}) scale({f(s.s)})">{paint(s.inner, theme)}</g>')
            continue
        fill = "currentColor" if theme is None else COL[theme][s.role]
        out.append(f'<path fill="{fill}"' + (' fill-rule="evenodd"' if s.evenodd else "") + f' d="{s.d}"/>')
    return "".join(out)


def placed(shapes, theme, s, tx, ty) -> str:
    if abs(s - 1) < 1e-9 and abs(tx) < 1e-9 and abs(ty) < 1e-9:
        return paint(shapes, theme)
    scale = "" if abs(s - 1) < 1e-9 else f" scale({f(s)})"
    return f'<g transform="translate({f(tx)} {f(ty)}){scale}">{paint(shapes, theme)}</g>'


def svg(w, h, body, title, x0=0.0, y0=0.0) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(w)} {f(h)}" width="{f(w)}" height="{f(h)}" role="img">'
            f"<title>{title}</title>{body}</svg>\n")


def symbol_svg(c: Concept, theme) -> str:
    x0, y0, x1, y1 = bbox(c.symbol)
    return svg(120, 120, placed(c.symbol, theme, 1, 60 - (x0 + x1) / 2, 60 - (y0 + y1) / 2), f"kaibo symbol: {c.name}")


def wordmark_svg(c: Concept, theme, pad=14) -> str:
    x0, y0, x1, y1 = bbox(c.word.shapes)
    return svg(x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, paint(c.word.shapes, theme), "kaibo wordmark: " + c.name, x0 - pad, y0 - pad)


def lockup_parts(c: Concept):
    """Returns (symbol transform or None, wordmark x offset, bounding box) in wordmark units."""
    wb = bbox(c.word.shapes)
    if c.lockup is None:
        return None, 0.0, wb
    hs, cy, gap = c.lockup
    x0, y0, x1, y1 = bbox(c.symbol)
    s = hs / (y1 - y0)
    tx, ty = -x0 * s, cy - (y0 + y1) / 2 * s
    wx = (x1 - x0) * s + gap - wb[0]
    box = (0, min(cy - hs / 2, wb[1]), wx + wb[2], max(cy + hs / 2, wb[3]))
    return (s, tx, ty), wx, box


def lockup_svg(c: Concept, theme, pad=16) -> str:
    sym, wx, (x0, y0, x1, y1) = lockup_parts(c)
    body = (placed(c.symbol, theme, *sym) if sym else "") + placed(c.word.shapes, theme, 1, wx, 0)
    return svg(x1 - x0 + 2 * pad, y1 - y0 + 2 * pad, body, "kaibo logo: " + c.name, x0 - pad, y0 - pad)


def tile_svg(c: Concept, theme, size=512, share=None, radius=0.225) -> str:
    """App icon: a rounded square with the symbol centred by its own outline."""
    share = share or c.icon
    x0, y0, x1, y1 = bbox(c.symbol)
    s = size * share / max(x1 - x0, y1 - y0)
    body = (f'<path fill="{COL[theme]["tile"]}" d="{rrect_d(0, 0, size, size, size * radius)}"/>'
            + placed(c.symbol, theme, s, size / 2 - (x0 + x1) / 2 * s, size / 2 - (y0 + y1) / 2 * s))
    return svg(size, size, body, f"kaibo app icon ({theme}): {c.name}")


def write_all() -> list[Concept]:
    cs = concepts()
    for c in cs:
        d = OUT / f"{c.n}-{c.slug}"
        d.mkdir(parents=True, exist_ok=True)
        files = {
            "symbol-on-dark.svg": symbol_svg(c, "dark"), "symbol-on-light.svg": symbol_svg(c, "light"), "symbol-mono.svg": symbol_svg(c, None),
            "wordmark-on-dark.svg": wordmark_svg(c, "dark"), "wordmark-on-light.svg": wordmark_svg(c, "light"),
            "lockup-on-dark.svg": lockup_svg(c, "dark"), "lockup-on-light.svg": lockup_svg(c, "light"), "lockup-mono.svg": lockup_svg(c, None),
            "icon-dark.svg": tile_svg(c, "dark"), "icon-light.svg": tile_svg(c, "light"),
            "favicon.svg": tile_svg(c, "dark", 64, c.fav, 0.22),
        }
        for name, text in files.items():
            (d / name).write_text(text)
        print(f"{c.n} {c.slug}: {len(files)} files, {sum(len(t) for t in files.values()) // 1024} KB")
    return cs


if __name__ == "__main__":
    write_all()
