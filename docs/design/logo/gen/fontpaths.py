"""Outline text from an openly licensed font file into filled SVG paths (fontTools), with the font's own pair kerning.

The font files are not in the repository. docs/design/logo/logo_concepts.md names each font, its licence and where to
get it; build.py takes their paths from fonts.json or the KAIBO_LOGO_FONTS environment variable.
"""
from __future__ import annotations

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from geo import Shape, f


class Outliner:
    def __init__(self, path: str):
        self.font = TTFont(path, lazy=True)
        self.gs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.xheight = self.font["OS/2"].sxHeight

    def name(self, ch: str) -> str:
        return self.cmap[ord(ch)]

    def bounds(self, name: str):
        bp = BoundsPen(self.gs)
        self.gs[name].draw(bp)
        return bp.bounds

    def contours(self, name: str):
        """The glyph's closed contours as separate recordings, each with its bounds (font units, y up)."""
        rec = RecordingPen()
        self.gs[name].draw(rec)
        out, cur = [], []
        for op, args in rec.value:
            cur.append((op, args))
            if op in ("closePath", "endPath"):
                bp = BoundsPen(None)
                for o, a in cur:
                    getattr(bp, o)(*a)
                out.append((cur, bp.bounds))
                cur = []
        return out

    def _d(self, ops, scale, dx, dy) -> str:
        pen = SVGPathPen(None, ntos=f)
        tp = TransformPen(pen, (scale, 0, 0, -scale, dx, dy))
        for o, a in ops:
            getattr(tp, o)(*a)
        return pen.getCommands()

    def shape(self, name: str, scale: float, dx: float, dy: float, role="ink", ops=None) -> Shape:
        """One glyph (or some of its contours) scaled, flipped to y-down and moved so its origin sits at (dx, dy)."""
        if ops is None:
            rec = RecordingPen()
            self.gs[name].draw(rec)
            ops = rec.value
        bp = BoundsPen(None)
        for o, a in ops:
            getattr(bp, o)(*a)
        x0, y0, x1, y1 = bp.bounds
        return Shape(self._d(ops, scale, dx, dy), (dx + x0 * scale, dy - y1 * scale, dx + x1 * scale, dy - y0 * scale), role)

    def kern(self, left: str, right: str) -> float:
        """Pair kerning from the font's GPOS 'kern' feature, in font units (0 when there is none)."""
        if "GPOS" not in self.font:
            return 0
        t = self.font["GPOS"].table
        if not t.FeatureList or not t.LookupList:
            return 0
        idx = sorted({i for fr in t.FeatureList.FeatureRecord if fr.FeatureTag == "kern" for i in fr.Feature.LookupListIndex})
        total = 0
        for i in idx:
            for st in t.LookupList.Lookup[i].SubTable:
                st = getattr(st, "ExtSubTable", st)
                if type(st).__name__ != "PairPos" or left not in st.Coverage.glyphs:
                    continue
                v = None
                if st.Format == 1:
                    ps = st.PairSet[st.Coverage.glyphs.index(left)]
                    v = next((r.Value1 for r in ps.PairValueRecord if r.SecondGlyph == right), None)
                    if v is None:
                        continue
                else:
                    c1, c2 = st.ClassDef1.classDefs.get(left, 0), st.ClassDef2.classDefs.get(right, 0)
                    v = st.Class1Record[c1].Class2Record[c2].Value1
                total += getattr(v, "XAdvance", 0) or 0 if v is not None else 0
                break
        return total
