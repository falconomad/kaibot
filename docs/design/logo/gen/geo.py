"""Geometry for the kaibo logo exploration (#255).

Every shape comes out as a filled SVG path (no strokes, no live text), with its bounding box, so the logos are
plain outlines that any tool can open. y grows downwards, as in SVG.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def f(v: float) -> str:
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass(frozen=True)
class Shape:
    d: str                      # path data
    box: tuple                  # (xmin, ymin, xmax, ymax)
    role: str = "ink"           # "ink" or "accent"
    evenodd: bool = False

    def as_role(self, role: str) -> "Shape":
        return Shape(self.d, self.box, role, self.evenodd)


def bbox(shapes) -> tuple:
    xs0, ys0, xs1, ys1 = zip(*(s.box for s in shapes))
    return min(xs0), min(ys0), max(xs1), max(ys1)


def disc(cx, cy, r, role="ink") -> Shape:
    d = f"M{f(cx - r)} {f(cy)}a{f(r)} {f(r)} 0 1 0 {f(2 * r)} 0a{f(r)} {f(r)} 0 1 0 {f(-2 * r)} 0z"
    return Shape(d, (cx - r, cy - r, cx + r, cy + r), role)


def ring(cx, cy, ro, ri, dx=0.0, dy=0.0, role="ink") -> Shape:
    """A circle with a round hole. The hole may sit off-centre (dx, dy)."""
    return Shape(disc(cx, cy, ro).d + disc(cx + dx, cy + dy, ri).d, (cx - ro, cy - ro, cx + ro, cy + ro), role, True)


def capsule(x1, y1, x2, y2, w, role="ink") -> Shape:
    """A straight stroke of width w with round ends, as an outline."""
    r = w / 2
    a = math.atan2(y2 - y1, x2 - x1)
    nx, ny = -math.sin(a) * r, math.cos(a) * r
    d = (f"M{f(x1 + nx)} {f(y1 + ny)}L{f(x2 + nx)} {f(y2 + ny)}A{f(r)} {f(r)} 0 0 0 {f(x2 - nx)} {f(y2 - ny)}"
         f"L{f(x1 - nx)} {f(y1 - ny)}A{f(r)} {f(r)} 0 0 0 {f(x1 + nx)} {f(y1 + ny)}z")
    return Shape(d, (min(x1, x2) - r, min(y1, y2) - r, max(x1, x2) + r, max(y1, y2) + r), role)


def _pt(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def arc_band(cx, cy, rc, w, a0, a1, role="ink") -> Shape:
    """Part of a ring with round ends: centre-line radius rc, thickness w, from angle a0 to a1
    (degrees; 0 = east, 90 = south on screen; a1 > a0)."""
    ro, ri, h = rc + w / 2, rc - w / 2, w / 2
    big = 1 if (a1 - a0) > 180 else 0
    o0, o1, i0, i1 = _pt(cx, cy, ro, a0), _pt(cx, cy, ro, a1), _pt(cx, cy, ri, a0), _pt(cx, cy, ri, a1)
    d = (f"M{f(o0[0])} {f(o0[1])}A{f(ro)} {f(ro)} 0 {big} 1 {f(o1[0])} {f(o1[1])}A{f(h)} {f(h)} 0 0 1 {f(i1[0])} {f(i1[1])}"
         f"A{f(ri)} {f(ri)} 0 {big} 0 {f(i0[0])} {f(i0[1])}A{f(h)} {f(h)} 0 0 1 {f(o0[0])} {f(o0[1])}z")
    pts = [_pt(cx, cy, rc, a0 + (a1 - a0) * i / 48) for i in range(49)]
    return Shape(d, (min(p[0] for p in pts) - h, min(p[1] for p in pts) - h, max(p[0] for p in pts) + h, max(p[1] for p in pts) + h), role)


def segment_below(cx, cy, r, y, role="ink") -> Shape:
    """The part of a circle below the horizontal line y (a bowl shape)."""
    hw = math.sqrt(max(r * r - (y - cy) ** 2, 0))
    d = f"M{f(cx - hw)} {f(y)}A{f(r)} {f(r)} 0 0 0 {f(cx + hw)} {f(y)}z"
    return Shape(d, (cx - hw, y, cx + hw, cy + r), role)


def stroke_outline(pts, w, role="ink") -> Shape:
    """Outline of a smooth open curve given as closely spaced centre-line points, with round ends."""
    r, n = w / 2, len(pts)
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        x0, y0 = pts[max(i - 1, 0)]
        x1, y1 = pts[min(i + 1, n - 1)]
        a = math.atan2(y1 - y0, x1 - x0)
        nx, ny = -math.sin(a) * r, math.cos(a) * r
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    d = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in left)
    d += f"A{f(r)} {f(r)} 0 0 0 {f(right[-1][0])} {f(right[-1][1])}"
    d += "L" + "L".join(f"{f(x)} {f(y)}" for x, y in reversed(right[:-1]))
    d += f"A{f(r)} {f(r)} 0 0 0 {f(left[0][0])} {f(left[0][1])}z"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return Shape(d, (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r), role)


def rrect_d(x, y, w, h, r) -> str:
    r = min(r, w / 2, h / 2)
    return (f"M{f(x + r)} {f(y)}h{f(w - 2 * r)}a{f(r)} {f(r)} 0 0 1 {f(r)} {f(r)}v{f(h - 2 * r)}a{f(r)} {f(r)} 0 0 1 {f(-r)} {f(r)}"
            f"h{f(-(w - 2 * r))}a{f(r)} {f(r)} 0 0 1 {f(-r)} {f(-r)}v{f(-(h - 2 * r))}a{f(r)} {f(r)} 0 0 1 {f(r)} {f(-r)}z")
