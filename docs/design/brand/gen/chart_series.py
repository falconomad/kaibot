"""The two lines of the Home pot chart, before and after #350, as each kind of colour vision sees them.

    python docs/design/brand/gen/chart_series.py            # prints the measured table
    python docs/design/brand/gen/chart_series.py --shot     # also writes ../proof/chart-series-350.png (Playwright)

The colours come from tokens.py. The measures are those of the dataviz palette validator (`validate_palette.js`):
OKLCH lightness band and chroma floor, the distance between the two lines in OKLab x 100 with full colour vision and
under the Machado 2009 simulations (severity 1.0), and WCAG contrast against each surface. The validator itself is the
reference; this file repeats its arithmetic so the proof sheet can be drawn without Node. Line width and dash are the
app's (pot 2.5 px solid, benchmark 1.5 px dotted with round caps) and are not changed by #350.
"""
from __future__ import annotations

import base64
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import colour  # noqa: E402
import tokens  # noqa: E402

BRAND = HERE.parent
FONT = BRAND.parents[2] / "apps" / "web" / "public" / "fonts" / "outfit-latin-500-normal.woff2"

# The benchmark before #350: grey 500 (dark) and grey 600 (light).
BEFORE = {"dark": tokens.GREYS[500], "light": tokens.GREYS[600]}
BAND = {"dark": (0.48, 0.67), "light": (0.43, 0.77)}
CHROMA_FLOOR, CVD_TARGET, NORMAL_FLOOR, CONTRAST_MIN = 0.10, 8.0, 15.0, 3.0
SURFACES = ("surface", "paper", "surface-2")          # card, page, raised
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
SEEN_BY = (("normal", "Full colour vision"), ("protan", "Protanopia (no red cones)"), ("deutan", "Deuteranopia (no green cones)"), ("tritan", "Tritanopia (no blue cones)"))


def _lin(h):
    return [colour._lin(c) for c in colour.hex_rgb(h)]


def _sim(h, kind):
    if kind == "normal":
        return _lin(h)
    r, g, b = _lin(h)
    return [min(1.0, max(0.0, m[0] * r + m[1] * g + m[2] * b)) for m in MACHADO[kind]]


def seen(h, kind):
    """The colour as that kind of vision sees it, as a hex."""
    return colour.rgb_hex(tuple(colour._gam(c) for c in _sim(h, kind)))


def _lab(rgb):
    r, g, b = rgb
    l, m, s = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b, 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
               0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta(a, b, kind="normal"):
    return 100 * math.dist(_lab(_sim(a, kind)), _lab(_sim(b, kind)))


def measure(mode, spy):
    t = tokens.theme(mode)
    pot = t["s-pot"]
    L, C, _ = colour.oklch(spy)
    d = {k: delta(pot, spy, k) for k, _ in SEEN_BY}
    con = {s: colour.contrast(spy, t[s]) for s in SURFACES}
    lo, hi = BAND[mode]
    ok = (lo <= L <= hi and C >= CHROMA_FLOOR and min(d["protan"], d["deutan"]) >= CVD_TARGET and d["normal"] >= NORMAL_FLOOR
          and min(con.values()) >= CONTRAST_MIN)
    return {"pot": pot, "spy": spy, "L": L, "C": C, "d": d, "con": con, "ok": ok}


def rows():
    out = []
    for mode in ("dark", "light"):
        out.append((mode, "before", measure(mode, BEFORE[mode])))
        out.append((mode, "after", measure(mode, tokens.theme(mode)["s-spy"])))
    return out


def chart(pot, spy, w=300, h=110):
    a = [0, 18, -24, 37, 72, 60, 135, 79, 108, 158, 126, 170, 107]
    b = [0, 20, -40, 40, 70, 60, 110, 20, 60, 120, 100, 130, 129]
    X = lambda i: 6 + i * (w - 12) / (len(a) - 1)  # noqa: E731
    Y = lambda v: h - 10 - (v + 60) / 250 * (h - 20)  # noqa: E731
    pts = lambda s: " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s))  # noqa: E731
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<polyline points="{pts(b)}" fill="none" stroke="{spy}" stroke-width="1.5" stroke-dasharray="0.1 5" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<polyline points="{pts(a)}" fill="none" stroke="{pot}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def page() -> str:
    face = ""
    if FONT.exists():
        face = f'@font-face{{font-family:"Outfit";src:url(data:font/woff2;base64,{base64.b64encode(FONT.read_bytes()).decode()}) format("woff2")}}'
    cols = rows()
    head = "".join(f'<th>{mode.capitalize()} theme, {when}<br><code>{m["pot"]}</code> and <code>{m["spy"]}</code></th>' for mode, when, m in cols)
    body = ""
    for kind, label in SEEN_BY:
        cells = ""
        for mode, _, m in cols:
            t = tokens.theme(mode)
            pot, spy, bg, ink, sub = (seen(m["pot"], kind), seen(m["spy"], kind), seen(t["surface"], kind), seen(t["ink"], kind), seen(t["slate"], kind))
            need = NORMAL_FLOOR if kind == "normal" else CVD_TARGET
            note = "reported only" if kind == "tritan" else f"needs {need:.0f}"
            cells += (f'<td><div class="card" style="background:{bg};color:{ink}">{chart(pot, spy)}'
                      f'<div class="lg" style="color:{sub}"><span><i style="border-top:2.5px solid {pot}"></i>kaibo</span>'
                      f'<span><i style="border-top:3px dotted {spy}"></i>S&amp;P 500</span></div></div>'
                      f'<div class="n">distance {m["d"][kind]:.1f} <em>({note})</em></div></td>')
        body += f"<tr><th class='r'>{label}</th>{cells}</tr>"
    foot = "".join(f'<td class="f">lightness {m["L"]:.2f}, chroma {m["C"]:.3f} (floor {CHROMA_FLOOR:.2f})<br>contrast {" / ".join(f"{v:.2f}" for v in m["con"].values())}'
                   f'<br><b class="{"ok" if m["ok"] else "no"}">{"passes" if m["ok"] else "fails"}</b></td>' for _, _, m in cols)
    return f"""<!doctype html><meta charset="utf-8"><style>{face}
body{{margin:0;padding:28px;background:#0A0B0D;color:#F4F5F7;font:500 14px/1.4 "Outfit",system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}}p{{margin:0 0 18px;color:#C0C5CD;max-width:900px}}
table{{border-collapse:separate;border-spacing:10px 8px}}th{{font-weight:500;color:#C0C5CD;text-align:left;font-size:13px}}th.r{{width:150px;vertical-align:middle}}
code{{font:500 12px ui-monospace,monospace;color:#F4F5F7}}.card{{border-radius:16px;padding:12px 10px 10px}}
.lg{{display:flex;gap:16px;padding:6px 6px 0;font-size:13px}}.lg i{{display:inline-block;width:18px;vertical-align:middle;margin-right:6px}}
.n{{font-size:12px;color:#C0C5CD;padding:4px 6px 0}}.n em{{font-style:normal;color:#A7ADB8}}.f{{font-size:12px;color:#C0C5CD;vertical-align:top;padding-left:6px}}
.ok{{color:#3ADD98}}.no{{color:#FF716D}}</style>
<h1>Home chart: the kaibo line and the S&amp;P 500 line (#350)</h1>
<p>The kaibo line keeps the brand blue. The benchmark was a grey; it is now a quiet mauve, so the two lines differ by colour as well as by width and dots.
Each row shows the same chart as one kind of colour vision sees it. Sample data.</p>
<table><tr><th></th>{head}</tr>{body}<tr><th class="r">Measured<br>(contrast: card / page / raised)</th>{foot}</tr></table>"""


def shot() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(viewport={"width": 1560, "height": 900}, device_scale_factor=1.5).new_page()
        pg.set_content(page())
        pg.wait_for_timeout(250)
        out = BRAND / "proof" / "chart-series-350.png"
        pg.screenshot(path=str(out), full_page=True)
        b.close()
        print(out.name, out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    for mode, when, m in rows():
        print(f'{mode:5s} {when:6s} {m["pot"]} {m["spy"]}  L {m["L"]:.3f}  C {m["C"]:.3f}  ' + "  ".join(f'{k} {v:.1f}' for k, v in m["d"].items())
              + "  contrast " + " / ".join(f"{v:.2f}" for v in m["con"].values()) + ("  PASS" if m["ok"] else "  FAIL"))
    if "--shot" in sys.argv:
        shot()
