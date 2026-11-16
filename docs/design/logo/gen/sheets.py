"""Proof sheets for the kaibo logo concepts (issue #255): each concept in context, rendered to PNG with Playwright.

    python docs/design/logo/gen/sheets.py            # writes ../logo_concepts.png and ../sheets/concept-<n>-<slug>.png

Needs the fonts of build.py (for the wordmarks), Playwright with Chromium, Pillow, and Inter installed for the captions.
The favicons are first drawn at their real size (16 and 32 px, one device pixel per CSS pixel) and then shown both at
that size and enlarged without smoothing, so the sheets show the pixels a browser tab would show.
"""
from __future__ import annotations

import base64
import io
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = build.HERE.parent
W = 1080
NOTES = {  # one line each for the sheets; the full reasoning is in logo_concepts.md
    1: ("One stem, one circle, one chevron. In the word the line starts in the k and the point sits on the i; alone, the k carries its point.",
        "Other finance brands lead with a K; Klarna's app icon is one. This k is lowercase, with a loose chevron and a point."),
    2: ("A set wordmark with one redrawn letter: the hole of the o sits high, so the bottom is heavy. The blue is the ballast.",
        "Ring and O marks are common. The off-centre hole and the blue base set it apart; in one colour only the hole does."),
    3: ("Two equal strokes at the same angle. The second starts a quarter of a rise lower than the first ended.",
        "A slash in a square is Deutsche Bank's mark. Two loose round strokes are not that, but it is the same family."),
    4: ("The i of the name, with one shallow arc at its foot. Three parts: dot, stem, arc.",
        "Anchors are a common picture, and Anchor was a crypto savings product that failed in 2022."),
    5: ("A single sine wave above a straight bar of the same weight. It is also the maths sign read as 'about equal'.",
        "Wave marks exist in finance (Current, Calibra's 2019 tilde, Solitone) and Equals uses two bars. None of those pairs a wave with a bar."),
    6: ("A serif wordmark, unchanged but for the dot of the i, which is a round blue point like the app's live light.",
        "A coloured dot on an i is a common device. As an icon the lone i reads as 'information'. Weak in one colour."),
}


def inline(svg_text: str, h=None, w=None, style="") -> str:
    """Inline an SVG at a given CSS size (the files carry their natural size)."""
    s = re.sub(r' width="[^"]+" height="[^"]+"', "", svg_text.strip(), count=1)
    css = (f"height:{h}px;" if h else "") + (f"width:{w}px;" if w else "width:auto;") + "display:block;" + style
    return s.replace("<svg ", f'<svg style="{css}" ', 1)


def fit(svg_text: str, max_w: float, max_h: float, style="") -> str:
    """Inline an SVG as large as fits in max_w x max_h."""
    vb = [float(v) for v in re.search(r'viewBox="([^"]+)"', svg_text).group(1).split()]
    k = min(max_w / vb[2], max_h / vb[3])
    return inline(svg_text, h=round(vb[3] * k, 2), w=round(vb[2] * k, 2), style=style)


def uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


CSS = """
*{box-sizing:border-box}html,body{margin:0}
body{width:1080px;background:#0A0B0D;color:#F4F5F7;font:400 30px/1.35 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased;padding:56px 48px 64px}
h1{margin:0;font-size:68px;font-weight:700;letter-spacing:-.03em;line-height:1.05}
h2{margin:64px 0 8px;font-size:34px;font-weight:600;letter-spacing:-.01em}
.cap{font-size:26px;color:#A1A8B3;margin:0 0 20px}
.top{display:flex;align-items:center;gap:26px}
.num{flex:none;width:96px;height:96px;border-radius:50%;background:#4F95F0;color:#0A0B0D;display:grid;place-items:center;font-size:56px;font-weight:700}
.idea{margin:26px 0 0;font-size:36px;line-height:1.3;color:#F4F5F7}
.meta{margin-left:auto;font-size:24px;color:#8A929E;text-align:right}
.hero{margin-top:44px;height:330px;border:1px solid #262A31;border-radius:32px;display:grid;place-items:center;background:#0A0B0D}
.row{display:flex;gap:20px;margin-top:20px}
.card{flex:1;min-width:0;height:230px;border-radius:28px;border:1px solid #262A31;background:#131519;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;position:relative}
.card.light{background:#F4F5F7;border-color:#F4F5F7;color:#0A0B0D}.card.blue{background:#2F6FDE;border-color:#2F6FDE;color:#fff}
.card small{position:absolute;left:20px;bottom:14px;font-size:22px;color:#8A929E}.card.light small{color:#5A6170}.card.blue small{color:#DCE7FB}
.phone{width:390px;border:1px solid #262A31;border-radius:0 0 0 0;background:#0A0B0D;overflow:hidden;font-size:15px;line-height:1.5}
.hdr{height:57px;display:flex;align-items:center;gap:8px;padding:6px 16px;border-bottom:1px solid #1B1E24;background:#0A0B0D}
.brand{display:flex;align-items:center;min-height:44px}
.sp{flex:1}
.pill{font-size:12px;font-weight:700;letter-spacing:.08em;color:#0A0B0D;background:#F5B64C;border-radius:999px;padding:4px 9px;white-space:nowrap;line-height:1.5}
.chip{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 12px;border:1px solid #262A31;border-radius:999px;background:#131519;color:#A1A8B3;font-size:13px}
.chip b{color:#F4F5F7;font-weight:600}.chip i{width:8px;height:8px;border-radius:50%;background:#3DDC97;display:block}
.pot{padding:18px 16px 20px}.pot p{margin:0;color:#A1A8B3;font-size:14px}.pot b{display:block;font-size:44px;font-weight:700;letter-spacing:-.04em;line-height:1.1}
.pot span{font-size:14px;font-weight:600;color:#3DDC97}
.zoom{width:780px;height:114px;overflow:hidden;border:1px solid #262A31}.zoom .hdr{transform:scale(2);transform-origin:0 0;width:390px}
.wall{flex:1;height:330px;border-radius:28px;display:flex;align-items:center;justify-content:center;gap:44px;padding:0 30px}
.wall.d{background:#1A2233}.wall.l{background:#DDE3EC;color:#0A0B0D}
.grid4{display:grid;grid-template-columns:60px 60px;gap:26px 28px;font-size:11px;text-align:center;line-height:1.2}
.grid4 div{display:flex;flex-direction:column;gap:6px;align-items:center}.ph{width:60px;height:60px;border-radius:13.5px}
.tabs{display:flex;gap:20px;margin-top:8px}
.tab{flex:1;height:120px;border-radius:20px;padding:18px 20px 0;display:flex;align-items:flex-end;gap:14px}
.tab.d{background:#0A0B0D;border:1px solid #262A31}.tab.l{background:#DEE1E6}
.tb{height:36px;width:230px;border-radius:10px 10px 0 0;display:flex;align-items:center;gap:8px;padding:0 12px;font-size:12px}
.tab.d .tb{background:#1F2228;color:#E6E8EB}.tab.l .tb{background:#fff;color:#1F2328}
.tb .x{margin-left:auto;opacity:.6}
.px{image-rendering:pixelated;display:block}
.zrow{display:flex;gap:20px;margin-top:20px}
.z{flex:1;height:236px;padding-bottom:40px;border-radius:20px;background:#131519;border:1px solid #262A31;display:flex;align-items:center;justify-content:center;gap:30px;position:relative}
.z.l{background:#F4F5F7;border-color:#F4F5F7}.z small{position:absolute;left:18px;bottom:12px;font-size:22px;color:#8A929E}
.foot{margin-top:56px;padding-top:28px;border-top:1px solid #262A31;font-size:27px;color:#A1A8B3}.foot b{color:#F4F5F7;font-weight:600}.foot p{margin:0 0 14px}
"""


def header_html(c) -> str:
    """The v4 phone header (apps/web: header.top on phones, 56 px + 1 px rule), with this concept as the brand."""
    sym, wx, (x0, y0, x1, y1) = build.lockup_parts(c)
    k = (10.5 if sym else 12.0) / build.XH                # CSS px per wordmark unit: x-height 10.5 px after a symbol, 12 px alone
    lock = inline(build.lockup_svg(c, "dark", pad=0), h=round((y1 - y0) * k, 2))
    nudge = "" if sym else "margin-top:-3px"              # a bare wordmark sits on its x-height, not its ascenders
    return (f'<div class="hdr"><span class="brand" style="{nudge}">{lock}</span><span class="sp"></span>'
            '<span class="pill">PAPER</span><span class="chip"><i></i><b>Running</b></span></div>')


def sheet_html(c, fav) -> str:
    d = build.OUT / f"{c.n}-{c.slug}"
    rd = lambda n: (d / n).read_text()  # noqa: E731
    part = lambda name, h, cls="", label="", style="": (  # noqa: E731
        f'<div class="card {cls}" style="{style}">{fit(rd(name), 270, h)}<small>{label}</small></div>')
    ph = lambda col: f'<div><span class="ph" style="background:{col}"></span>&nbsp;</div>'  # noqa: E731

    def wall(theme):
        icon = rd(f"icon-{theme}.svg")
        cols = ["#3A4353", "#55607A", "#2B3344"] if theme == "dark" else ["#B9C2D0", "#9FAABD", "#C9D1DC"]
        cls = "d" if theme == "dark" else "l"
        return (f'<div class="wall {cls}">{inline(icon, h=180)}<div class="grid4">{ph(cols[0])}'
                f'<div>{inline(icon, h=60)}kaibo</div>{ph(cols[1])}{ph(cols[2])}</div></div>')

    f16, f32 = fav[c.n]
    tab = lambda cls: (f'<div class="tab {cls}"><div class="tb"><img src="{f16}" width="16" height="16">kaibo · paper<span class="x">×</span></div>'  # noqa: E731
                       f'<img src="{f32}" width="32" height="32" style="margin-bottom:14px"></div>')
    built, watch = NOTES[c.n]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="top"><span class="num">{c.n}</span><h1>{c.name}</h1><span class="meta">kaibo logo<br>concept {c.n} of 6</span></div>
<p class="idea">{c.idea}</p>
<div class="hero">{fit(rd("lockup-on-dark.svg"), 760, 190 if c.lockup else 160)}</div>
<div class="row">{part("symbol-on-dark.svg", 140, label="Symbol")}{part("wordmark-on-dark.svg", 84, label="Wordmark")}
<div class="card" style="color:#F4F5F7">{fit(rd("lockup-mono.svg"), 270, 84)}<small>One colour</small></div></div>
<div class="row">{part("lockup-on-light.svg", 84, "light", "On a light background")}
<div class="card light">{fit(rd("lockup-mono.svg"), 270, 84)}<small>One colour, black</small></div>
<div class="card blue">{fit(rd("lockup-mono.svg"), 270, 84)}<small>One colour, on blue</small></div></div>
<h2>In the app header</h2><p class="cap">The v4 phone header, 390 × 57 px, at real size and at twice the size.</p>
<div style="display:flex;gap:20px;align-items:flex-start"><div class="phone">{header_html(c)}<div class="pot"><p>Paper pot</p><b>$10,107.10</b><span>+$107.10 all time</span></div></div></div>
<div class="zoom" style="margin-top:20px">{header_html(c)}</div>
<h2>Home screen</h2><p class="cap">App icon at 180 px and at 60 px, dark and light.</p>
<div class="row" style="margin-top:0">{wall("dark")}{wall("light")}</div>
<h2>Browser tab</h2><p class="cap">Favicon at 16 px and 32 px, then the same pixels enlarged.</p>
<div class="tabs">{tab("d")}{tab("l")}</div>
<div class="zrow"><div class="z"><img class="px" src="{f16}" width="128" height="128"><img class="px" src="{f32}" width="128" height="128"><small>16 px × 8 · 32 px × 4</small></div>
<div class="z l"><img class="px" src="{f16}" width="128" height="128"><img class="px" src="{f32}" width="128" height="128"><small>the same, on light</small></div></div>
<div class="foot"><p><b>Built from.</b> {built}</p><p><b>Could be confused with.</b> {watch}</p><p><b>Wordmark.</b> {c.word.credit}</p></div>
</body></html>"""


CONTACT_CSS = """
*{box-sizing:border-box}html,body{margin:0}
body{width:1080px;background:#0A0B0D;color:#F4F5F7;font:400 28px/1.3 Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased;padding:48px 36px 48px}
h1{margin:0 12px;font-size:60px;font-weight:700;letter-spacing:-.03em}
.sub{margin:10px 12px 30px;font-size:29px;color:#A1A8B3}
.g{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.c{border:1px solid #262A31;border-radius:32px;background:#0A0B0D;padding:26px 26px 24px;display:flex;flex-direction:column}
.t{display:flex;align-items:center;gap:16px}
.n{flex:none;width:68px;height:68px;border-radius:50%;background:#4F95F0;color:#0A0B0D;display:grid;place-items:center;font-size:42px;font-weight:700}
.t b{font-size:42px;font-weight:700;letter-spacing:-.02em;white-space:nowrap}
.pick{margin-left:auto;font-size:21px;font-weight:600;color:#7FB2FF;border:1.5px solid #2B4F86;border-radius:999px;padding:5px 14px;white-space:nowrap}
.i{margin:14px 0 0;font-size:27px;line-height:1.3;color:#A1A8B3;min-height:72px}
.l{height:190px;display:grid;place-items:center}
.k{display:flex;gap:16px;align-items:flex-end;justify-content:center}
.k img{margin-bottom:4px}
.foot{margin:30px 12px 0;font-size:25px;color:#8A929E}
"""


def contact_html(cs, fav) -> str:
    cards = []
    for c in cs:
        d = build.OUT / f"{c.n}-{c.slug}"
        rd = lambda n: (d / n).read_text()  # noqa: E731,B023
        pick = {1: "First pick", 5: "Second pick"}.get(c.n)             # Design's recommendation
        tag = f'<span class="pick">{pick}</span>' if pick else ""
        cards.append(f'<div class="c"><div class="t"><span class="n">{c.n}</span><b>{c.name}</b>{tag}</div><p class="i">{c.idea}</p>'
                     f'<div class="l">{fit(rd("lockup-on-dark.svg"), 400, 104 if c.lockup else 92)}</div>'
                     f'<div class="k">{inline(rd("icon-dark.svg"), h=132)}{inline(rd("icon-light.svg"), h=132)}'
                     f'<img src="{fav[c.n][1]}" width="32" height="32"><img src="{fav[c.n][0]}" width="16" height="16"></div></div>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{CONTACT_CSS}</style></head><body>'
            '<h1>kaibo: six logo concepts</h1><p class="sub">Logo, app icon dark and light, favicon at 32 and 16 px. Original work; nothing is final. Picks: the design team.</p>'
            f'<div class="g">{"".join(cards)}</div><p class="foot">Design exploration for issue #255 · 16 Nov 2026 · one sheet per concept in sheets/</p></body></html>')


def save_png(png: bytes, path: pathlib.Path) -> int:
    """Lossless and full colour (the brand colours must not shift), re-packed by Pillow to cut the size."""
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "PNG", optimize=True)
    return path.stat().st_size


def main() -> None:
    cs = build.write_all()
    (ROOT / "sheets").mkdir(exist_ok=True)
    total = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": 1200}, device_scale_factor=1)
        page = ctx.new_page()
        fav = {}
        for c in cs:                      # real-size favicons first
            svg_text = (build.OUT / f"{c.n}-{c.slug}" / "favicon.svg").read_text()
            page.set_content(f'<body style="margin:0;background:transparent">{inline(svg_text, h=16, style="position:absolute;left:0;top:0")}'
                             f'{inline(svg_text, h=32, style="position:absolute;left:40px;top:0")}</body>')
            a = page.screenshot(clip={"x": 0, "y": 0, "width": 16, "height": 16}, omit_background=True)
            b = page.screenshot(clip={"x": 40, "y": 0, "width": 32, "height": 32}, omit_background=True)
            fav[c.n] = (uri(a), uri(b))
        for c in cs:
            page.set_content(sheet_html(c, fav))
            page.wait_for_timeout(150)
            out = ROOT / "sheets" / f"concept-{c.n}-{c.slug}.png"
            n = save_png(page.screenshot(full_page=True), out)
            total += n
            print(out.name, n // 1024, "KB")
        page.set_content(contact_html(cs, fav))
        page.wait_for_timeout(150)
        n = save_png(page.screenshot(full_page=True), ROOT / "logo_concepts.png")
        total += n
        print("logo_concepts.png", n // 1024, "KB")
        browser.close()
    print("total", round(total / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
