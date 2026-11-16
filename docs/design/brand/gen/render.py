"""Renders the brand system proofs with Playwright (issue #265).

    python docs/design/brand/gen/render.py     # writes ../proof/*.png, ../type_specimen.png, ../brand_system_preview.png
"""
from __future__ import annotations

import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import colour  # noqa: E402
import mock  # noqa: E402
import tokens  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = mock.HERE.parent
PAGE = ('<!doctype html><html><head><meta charset="utf-8"><style>{font}{css}'
        'body{{background:#050607;padding:{pad}px;width:max-content}}.pair{{display:flex;gap:{gap}px;align-items:flex-start}}'
        '.col{{display:flex;flex-direction:column;gap:12px}}.cap{{font:600 {cap}px Inter,system-ui,sans-serif;color:#A1A8B3;letter-spacing:.02em}}'
        '.fr{{border:1px solid #262A31;border-radius:{r}px;overflow:hidden}}</style></head><body>{body}</body></html>')


def save(png: bytes, path: pathlib.Path) -> int:
    Image.open(io.BytesIO(png)).convert("RGB").save(path, "PNG", optimize=True)
    return path.stat().st_size


def pair_html(name: str) -> str:
    desk = mock.SCREENS[name][1] == "desk"
    col = lambda cap, html: f'<div class="col"><span class="cap">{cap}</span><div class="fr">{html}</div></div>'  # noqa: E731
    body = '<div class="pair">' + col("BEFORE · today", mock.screen(name, "before")) + col("AFTER · brand system v1", mock.screen(name, "after")) + "</div>"
    if desk:
        body = body.replace('class="pair"', 'class="pair" style="flex-direction:column"')
    return PAGE.format(font=mock.font_face(), css=mock.CSS, pad=24, gap=24, cap=15, r=0 if desk else 28, body=body)


def sw(hexv, name, fg=None):
    fg = fg or ("#0A0B0D" if colour.lum(hexv) > .3 else "#F4F5F7")
    return f'<div class="sw" style="background:{hexv};color:{fg}"><b>{name}</b><span>{hexv}</span></div>'


XCSS = """
.pv{width:430px;background:#0A0B0D;color:#F4F5F7;font-family:'Outfit','Inter',sans-serif;padding:0 0 28px}
.pv h3{margin:28px 20px 4px;font-size:22px;font-weight:700;letter-spacing:-.02em}.pv .lead{margin:0 20px 12px;font-size:15px;color:#B2B8C1;line-height:1.4}
.ramp{display:flex;margin:0 20px 8px;border-radius:12px;overflow:hidden}.ramp .sw{flex:1;height:54px;padding:6px 0 0;text-align:center;font-size:10px}.ramp .sw b{display:block;font-size:11px}.ramp .sw span{display:none}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:0 20px}.grid .sw{border-radius:12px;padding:10px 12px;height:64px;font-size:12px;border:1px solid #262A31}.grid .sw b{display:block;font-size:14px}
.crop{overflow:hidden;margin:0 20px;border:1px solid #262A31;border-radius:24px;width:390px}
.lab{margin:18px 20px 8px;font-size:13px;font-weight:600;letter-spacing:.08em;color:#989FAB;text-transform:uppercase}
.spec{margin:0 20px;padding:20px;border-radius:24px;background:#131519;border:1px solid #262A31}
.spec .big{all:unset;display:block;font-size:44px;font-weight:700;letter-spacing:-.035em;line-height:1.1;font-variant-numeric:tabular-nums}
.spec p{margin:6px 0 0}.spec .nums{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;text-align:right;line-height:1.35}
.scale div{display:flex;align-items:baseline;gap:12px;border-top:1px solid #262A31;padding:8px 0}.scale small{width:92px;flex:none;font-size:12px;color:#989FAB}
"""


def palette_html() -> str:
    d = tokens.theme("dark")
    blues = "".join(sw(v, k) for k, v in tokens.BLUES.items())
    greys = "".join(sw(v, k) for k, v in tokens.GREYS.items() if k not in (0, 750, 850))
    roles = "".join([sw(d["sea"], "Brand blue"), sw(d["paper"], "Page"), sw(d["ink"], "Text"), sw(d["gain"], "Gain ▲"), sw(d["loss"], "Loss ▼"), sw(d["warn"], "Paper, caution"),
                     sw(d["loss-tint"], "Halt", d["ink"]), sw(d["exp-bg"], "Experiment", d["ink"]), sw(d["ai"], "Agent")])
    return f'<div class="lab">Blue, from the logo</div><div class="ramp">{blues}</div><div class="lab">Neutrals, from its black and white</div><div class="ramp">{greys}</div><div class="lab">What each colour means</div><div class="grid">{roles}</div>'


def specimen_html() -> str:
    word = (mock.LOGO / "wordmark-on-dark.svg").read_text()
    scale = "".join(f'<div><small>{n} · {px}/{w}</small><span style="font-size:{min(px, 40)}px;font-weight:{w};letter-spacing:{ls}em;line-height:1.15">{txt}</span></div>' for n, px, w, ls, txt in [
        ("Hero", 44, 700, -.035, "$10,107.10"), ("Title", 28, 700, -.03, "Positions"), ("Heading", 20, 600, -.01, "Holding 4 of 5"), ("Body", 16, 400, 0, "New buys paused until 13:05"),
        ("Small", 14, 400, 0, "Trend momentum · 10:52"), ("Caption", 12, 600, .02, "PAPER ONLY · 12 S AGO")])
    return (f'<div class="spec"><div style="height:40px">{mock.re.sub(r' width="[^"]+" height="[^"]+"', ' height="40"', word, count=1)}</div>'
            '<p style="font-size:15px;color:#B2B8C1">Outfit, for words and for money. Open licence (SIL OFL 1.1).</p>'
            '<p style="font-size:26px;font-weight:600;letter-spacing:-.02em;margin-top:14px">Aa Bb Gg Kk Qq Rr 0123456789</p>'
            '<div style="display:flex;gap:16px;margin-top:14px;align-items:flex-end"><p style="flex:1;font-size:13px;color:#989FAB">Figures line up in columns (tabular numerals), so amounts can be compared down a list.</p>'
            '<div class="nums">$2,070.88<br>$1,308.20<br>$941.77<br><span style="color:#3ADD98">▲ +$111.11</span></div></div>'
            f'<div class="scale" style="margin-top:14px">{scale}</div></div>')


def components_html(mode: str) -> str:
    t = mock.after(mode)
    style = "".join(f"--{k}:{v};" for k, v in t.items())
    I = mock.ico
    body = (mock.header("after", t, mode == "light")
            + '<div class="lab2">Buttons</div><div style="display:flex;gap:8px;padding:0 16px"><div class="btn" style="margin:0">Primary</div><div class="btn ghost" style="margin:0">Secondary</div></div>'
            '<div style="display:flex;gap:8px;padding:8px 16px 0"><div class="btn ghost" style="margin:0;color:var(--loss);border-color:var(--loss-line)">Flatten and stop</div><div class="btn" style="margin:0;opacity:.45">Disabled</div></div>'
            '<div class="lab2">Chips and tags</div><div class="chips"><b class="on">Open</b><b>Closed</b><b style="outline:2px solid var(--focus);outline-offset:2px">Focused</b></div>'
            f'<div style="display:flex;gap:8px;padding:8px 16px 0;flex-wrap:wrap"><span class="tag">Skipped</span><span class="tag" style="background:var(--gain-tint);color:var(--gain)">▲ Gain</span>'
            f'<span class="tag" style="background:var(--loss-tint);color:var(--loss)">▼ Loss</span><span class="tag" style="background:var(--warn-tint);color:var(--warn-text)">Caution</span>'
            f'<span class="tag exp">{I("flask", 13)}Experiment</span><span class="tag" style="background:var(--ai-tint);color:var(--ai)">Agent</span></div>'
            '<div class="lab2">Status rows</div>' + mock.HALT + f'<div class="st warn">{I("shield", 20)}<span class="g">Close to the daily loss limit</span>{mock.CHEV}</div>' + mock.EXP
            + '<div class="lab2">Meter, form, skeleton</div><div style="padding:0 16px"><div style="display:flex;justify-content:space-between;font-size:14px;margin-bottom:6px"><span>Invested</span><b>59% of 60%</b></div>'
            '<div class="meter"><i style="width:59%"></i></div><label style="display:block;margin:14px 0 6px">Email</label><div class="inp f">you@example.com</div>'
            '<div class="sk" style="width:70%;margin-top:14px"></div><div class="sk" style="width:45%;margin-top:8px"></div></div>'
            '<div class="lab2">Table</div><div style="padding:0 16px"><table class="tbl"><tr><th>Stock</th><th class="n">Shares</th><th class="n">Result</th></tr>'
            '<tr><td>NVDA</td><td class="n">14</td><td class="n up">▲ +$80.36</td></tr><tr><td>AVGO</td><td class="n">4</td><td class="n down">▼ −$17.40</td></tr></table></div>' + mock.tabs("home"))
    return (f'<div class="phone" data-v="after" style="{style}background:var(--paper);color:var(--ink);font-family:var(--font);height:1070px">'
            f'<style>.lab2{{padding:16px 16px 8px;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--stale)}}</style>{body}</div>')


def shot(b, html, path, dsf=1.5, sel="body"):
    page = b.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=dsf).new_page()
    page.set_content(html)
    page.wait_for_timeout(250)
    n = save(page.locator(sel).screenshot(), path)
    print(path.name, n // 1024, "KB")
    page.context.close()
    return n


def extras(b) -> int:
    total = 0
    wrap = lambda body, extra="": PAGE.format(font=mock.font_face(), css=mock.CSS + XCSS + extra, pad=0, gap=24, cap=15, r=28, body=body)  # noqa: E731
    total += shot(b, wrap(f'<div class="pv" style="padding:20px 0">{specimen_html()}</div>'), ROOT / "type_specimen.png", 2, ".pv")
    comp = ('<div class="pair" style="padding:24px">' + "".join(f'<div class="col"><span class="cap">{m.upper()} THEME</span><div class="fr">{components_html(m)}</div></div>' for m in ("dark", "light")) + "</div>")
    total += shot(b, wrap(comp), ROOT / "proof" / "components-dark-light.png")
    lightpair = ('<div class="pair" style="padding:24px">' + "".join(f'<div class="col"><span class="cap">{c}</span><div class="fr">{mock.screen(n, "after", "light")}</div></div>'
                                                                      for c, n in (("LIGHT · Home", "home"), ("LIGHT · Position sheet", "positions-sheet"))) + "</div>")
    total += shot(b, wrap(lightpair), ROOT / "proof" / "light-theme.png")
    crop = lambda v, h: f'<div class="crop" style="height:{h}px">{mock.screen("home", v)}</div>'  # noqa: E731
    sheet = f'<div class="crop" style="height:844px">{mock.screen("positions-sheet", "after")}</div>'
    t = mock.after("dark")
    chart = (f'<div class="spec" style="padding:16px 0 12px"><div style="padding:0 16px;font-size:14px;color:#B2B8C1">Paper pot against the market</div>{mock.chart(t, 390, 190, "after")}'
             '<div style="display:flex;gap:18px;padding:8px 16px 0;font-size:13px;color:#B2B8C1"><span><i style="display:inline-block;width:18px;height:3px;border-radius:2px;background:#4F95F0;vertical-align:middle"></i> Your pot</span>'
             '<span><i style="display:inline-block;width:18px;border-top:2px dotted #808692;vertical-align:middle"></i> S&amp;P 500</span><span><i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#4F95F0"></i> Now</span></div></div>')
    pv = ('<div class="pv">'
          + f'<div style="{"".join(f"--{k}:{v};" for k, v in t.items())}" data-v="after">{mock.header("after", t)}</div>'
          + '<h3>kaibo brand system v1</h3><p class="lead">Every colour, letter and shape now comes from the logo you picked: its blue, its black and white, its round ends and its point.</p>'
          + palette_html() + '<h3>Type</h3><p class="lead">A round, geometric typeface that sits with the hand-drawn wordmark.</p>' + specimen_html()
          + '<h3>Home, before</h3><p class="lead">Today: white button, generic mark, the experiment note in the brand blue.</p>' + crop("before", 620)
          + '<h3>Home, after</h3><p class="lead">The wordmark in the header. Red still means halted, amber still means Paper. The experiment note is now a grey lab note with a flask, so it cannot be mistaken for a button.</p>' + crop("after", 620)
          + '<h3>A position sheet</h3><p class="lead">Blue is kept for the one main action and the live point.</p>' + sheet
          + '<h3>The chart</h3><p class="lead">Your pot in brand blue, ending in the logo point. The market is a quiet dotted grey line.</p>' + chart + "</div>")
    total += shot(b, wrap(pv), ROOT / "brand_system_preview.png", 2, ".pv")
    return total


def main() -> None:
    (ROOT / "proof").mkdir(exist_ok=True)
    total = 0
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for name, (_, frame) in mock.SCREENS.items():
            dsf = 1 if frame == "desk" else 1.5
            page = b.new_context(viewport={"width": 1400, "height": 900}, device_scale_factor=dsf).new_page()
            page.set_content(pair_html(name))
            page.wait_for_timeout(250)
            el = page.locator("body")
            n = save(el.screenshot(), ROOT / "proof" / f"before-after-{name}.png")
            total += n
            print(f"before-after-{name}.png", n // 1024, "KB")
            page.context.close()
        total += extras(b)
        b.close()
    print("total", round(total / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
