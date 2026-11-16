"""Motion sketches for the two recommended concepts (issue #255).

    python docs/design/logo/gen/motion.py     # writes ../motion/motion_demo.html and ../sheets/motion-storyboard.png

The demo is a self-contained page: the marks are redrawn as strokes so they can be drawn on, and every animation is
switched off under "reduce motion" (the finished mark simply shows). A sketch for discussion, not production code.
"""
from __future__ import annotations

import io
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import build  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = build.HERE.parent
INK, SEA, NIGHT = "#F4F5F7", "#4F95F0", "#0A0B0D"

# Concept 1 as centre lines (same numbers as build.kit_k, moved into a 120 box)
W1 = build.K_W
OX, OY = 21.57, 104.0
V = (OX + W1 + build.K_GAP + W1 / 2, OY - build.XH * build.K_VERTEX)
TIP = (OX + W1 + build.K_GAP + build.K_ARM, OY - build.XH + W1 / 2)
FOOT = (OX + W1 + build.K_GAP + build.K_LEG, OY - W1 / 2)
_n = math.hypot(TIP[0] - V[0], TIP[1] - V[1])
DOT = (TIP[0] + (TIP[0] - V[0]) / _n * 22.5, TIP[1] + (TIP[1] - V[1]) / _n * 22.5)
STEM = ((OX + W1 / 2, OY - W1 / 2), (OX + W1 / 2, OY - build.K_ASC + W1 / 2))


def wave_d(amp=11.0, n=72) -> str:
    return "M" + " L".join(f"{18 + 84 * i / n:.2f} {44 - amp * math.sin(2 * math.pi * i / n):.2f}" for i in range(n + 1))


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def line(a, b, col, w=15.0) -> str:
    return f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}" stroke="{col}" stroke-width="{w}" stroke-linecap="round"/>'


def k_frame(stem=1.0, leg=1.0, arm=1.0, dot=1.0, ring=0.0) -> str:
    out = []
    if arm > 0:
        out.append(line(V, lerp(V, TIP, arm), SEA))
    if dot > 0:
        out.append(f'<circle cx="{DOT[0]:.2f}" cy="{DOT[1]:.2f}" r="{9 * dot:.2f}" fill="{SEA}"/>')
    if ring > 0:
        out.append(f'<circle cx="{DOT[0]:.2f}" cy="{DOT[1]:.2f}" r="{9 + 9 * ring:.2f}" fill="none" stroke="{SEA}" stroke-width="2.5" opacity="{0.55 * (1 - ring):.2f}"/>')
    if leg > 0:
        out.append(line(V, lerp(V, FOOT, leg), INK))
    if stem > 0:
        out.append(line(STEM[0], lerp(STEM[0], STEM[1], stem), INK))
    return "".join(out)


def sea_frame(bar=1.0, amp=1.0, wave=True) -> str:
    out = []
    if wave:
        out.append(f'<path d="{wave_d(11 * amp)}" fill="none" stroke="{SEA}" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>')
    if bar > 0:
        out.append(line((18, 84), (18 + 84 * bar, 84), INK))
    return "".join(out)


FRAMES = {
    1: [("0.15 s", "The stem rises from the baseline.", k_frame(0.7, 0, 0, 0)),
        ("0.35 s", "The leg sets; the arm starts to draw.", k_frame(1, 1, 0.45, 0)),
        ("0.60 s", "The line reaches its length; the point appears.", k_frame(1, 1, 1, 0.55)),
        ("0.90 s", "One soft pulse, then still.", k_frame(1, 1, 1, 1, 0.6))],
    5: [("0.15 s", "The line is drawn, left to right.", sea_frame(0.5, 0, False)),
        ("0.30 s", "The water lies flat on it.", sea_frame(1, 0.04)),
        ("0.55 s", "One swell rises.", sea_frame(1, 0.62)),
        ("0.80 s", "It settles at its height, then still.", sea_frame(1, 1))],
}

DEMO = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>kaibo logo: motion sketches (concepts 1 and 5)</title>
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0A0B0D;color:#F4F5F7;font:16px/1.5 Inter,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}
  main{width:min(720px,100%);padding:32px 20px}
  h1{font-size:22px;margin:0 0 4px} p{color:#A1A8B3;margin:0 0 20px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  figure{margin:0;padding:20px;border:1px solid #262A31;border-radius:24px;background:#131519;text-align:center}
  figcaption{color:#A1A8B3;font-size:14px;margin-top:8px}
  svg{width:160px;height:160px}
  button{margin-top:20px;min-height:44px;padding:0 20px;border-radius:999px;border:1px solid #3A404A;background:#1B1E24;color:#F4F5F7;font:600 15px Inter,system-ui,sans-serif}
  /* every stroke has pathLength 1, so "drawing" is one number going from 1 to 0 */
  .draw{stroke-dasharray:1;stroke-dashoffset:1;animation:draw var(--d,.24s) cubic-bezier(.2,.7,.2,1) var(--t,0s) forwards}
  @keyframes draw{to{stroke-dashoffset:0}}
  .pop{transform-box:fill-box;transform-origin:center;transform:scale(0);animation:pop .18s cubic-bezier(.3,1.4,.5,1) .56s forwards}
  @keyframes pop{to{transform:scale(1)}}
  .pulse{transform-box:fill-box;transform-origin:center;opacity:0;animation:pulse .7s cubic-bezier(.2,.7,.2,1) .74s 1}
  @keyframes pulse{0%{opacity:.55;transform:scale(1)}100%{opacity:0;transform:scale(2.1)}}
  .swell{transform-box:fill-box;transform-origin:center;transform:scaleY(.04);animation:swell .5s cubic-bezier(.3,1.25,.5,1) .28s forwards}
  @keyframes swell{to{transform:scaleY(1)}}
  /* Reduced motion: nothing moves; the finished mark is simply there. */
  @media (prefers-reduced-motion:reduce){
    .draw{animation:none;stroke-dashoffset:0}.pop{animation:none;transform:none}.pulse{animation:none;opacity:0}.swell{animation:none;transform:none}
  }
</style></head>
<body><main>
<h1>kaibo logo: motion sketches</h1>
<p>How concepts 1 and 5 could arrive on load. Under one second, once, then still. With "reduce motion" on, nothing moves.</p>
<div class="row" id="stage">
<figure><svg viewBox="0 0 120 120" role="img" aria-label="Concept 1, Rising k">
  <line class="draw" pathLength="1" style="--t:.26s;--d:.3s" x1="__VX__" y1="__VY__" x2="__TX__" y2="__TY__" stroke="#4F95F0" stroke-width="15" stroke-linecap="round"/>
  <circle class="pulse" cx="__DX__" cy="__DY__" r="9" fill="none" stroke="#4F95F0" stroke-width="2.5"/>
  <circle class="pop" cx="__DX__" cy="__DY__" r="9" fill="#4F95F0"/>
  <line class="draw" pathLength="1" style="--t:.12s;--d:.24s" x1="__VX__" y1="__VY__" x2="__FX__" y2="__FY__" stroke="#F4F5F7" stroke-width="15" stroke-linecap="round"/>
  <line class="draw" pathLength="1" style="--t:0s;--d:.2s" x1="__SX__" y1="__SY0__" x2="__SX__" y2="__SY1__" stroke="#F4F5F7" stroke-width="15" stroke-linecap="round"/>
</svg><figcaption>1 · Rising k: stem, leg, the line, then its point with one pulse</figcaption></figure>
<figure><svg viewBox="0 0 120 120" role="img" aria-label="Concept 5, Sea level">
  <path class="swell" d="__WAVE__" fill="none" stroke="#4F95F0" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>
  <line class="draw" pathLength="1" style="--t:0s;--d:.26s" x1="18" y1="84" x2="102" y2="84" stroke="#F4F5F7" stroke-width="15" stroke-linecap="round"/>
</svg><figcaption>5 · Sea level: the line is drawn, then one swell rises and settles</figcaption></figure>
</div>
<button type="button" onclick="const s=document.getElementById('stage');const h=s.innerHTML;s.innerHTML='';requestAnimationFrame(()=>s.innerHTML=h)">Play again</button>
</main></body></html>
"""


def demo_html() -> str:
    rep = {"__VX__": V[0], "__VY__": V[1], "__TX__": TIP[0], "__TY__": TIP[1], "__FX__": FOOT[0], "__FY__": FOOT[1], "__DX__": DOT[0], "__DY__": DOT[1],
           "__SX__": STEM[0][0], "__SY0__": STEM[0][1], "__SY1__": STEM[1][1]}
    out = DEMO.replace("__WAVE__", wave_d())
    for k, v in rep.items():
        out = out.replace(k, f"{v:.2f}")
    return out


def storyboard_html(cs) -> str:
    names = {c.n: c.name for c in cs}
    rows = []
    for n, frames in FRAMES.items():
        cells = "".join(f'<div class="f"><svg viewBox="0 0 120 120">{svg}</svg><b>{t}</b><span>{cap}</span></div>' for t, cap, svg in frames)
        rows.append(f'<h2><i>{n}</i>{names[n]}</h2><div class="r">{cells}</div>')
    return ('<!doctype html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;width:1080px;background:#0A0B0D;color:#F4F5F7;'
            'font:400 26px/1.3 Inter,system-ui,sans-serif;padding:48px 40px}h1{margin:0;font-size:52px;letter-spacing:-.03em}p{margin:10px 0 0;color:#A1A8B3;font-size:28px}'
            'h2{display:flex;align-items:center;gap:16px;font-size:38px;margin:44px 0 16px}h2 i{font-style:normal;width:60px;height:60px;border-radius:50%;background:#4F95F0;color:#0A0B0D;'
            'display:grid;place-items:center;font-weight:700}.r{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}'
            '.f{border:1px solid #262A31;border-radius:24px;background:#131519;padding:16px 16px 18px;display:flex;flex-direction:column;gap:6px}'
            '.f svg{width:100%;height:auto}.f b{font-size:26px}.f span{font-size:23px;color:#A1A8B3;min-height:90px}</style></head><body>'
            '<h1>Motion on load</h1><p>Under one second, once, then still. With "reduce motion" on, the finished mark simply shows.</p>'
            + "".join(rows) + "</body></html>")


def main() -> None:
    cs = build.concepts()
    (ROOT / "motion").mkdir(exist_ok=True)
    (ROOT / "motion" / "motion_demo.html").write_text(demo_html())
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_context(viewport={"width": 1080, "height": 900}, device_scale_factor=1).new_page()
        page.set_content(storyboard_html(cs))
        page.wait_for_timeout(150)
        out = ROOT / "sheets" / "motion-storyboard.png"
        Image.open(io.BytesIO(page.screenshot(full_page=True))).convert("RGB").save(out, "PNG", optimize=True)
        print(out.name, out.stat().st_size // 1024, "KB")
        # check the demo: it must end in the finished state, and stand still under reduced motion
        for rm in ("no-preference", "reduce"):
            ctx = b.new_context(viewport={"width": 800, "height": 600}, reduced_motion=rm)
            p2 = ctx.new_page()
            p2.set_content(demo_html())
            p2.wait_for_timeout(120 if rm == "reduce" else 1800)
            running = p2.evaluate("document.getAnimations().filter(a => a.playState === 'running').length")
            off = p2.evaluate("[...document.querySelectorAll('.draw')].map(e => getComputedStyle(e).strokeDashoffset).join(',')")
            print(f"demo, {rm}: running animations {running}, dash offsets {off}")
            ctx.close()
        b.close()


if __name__ == "__main__":
    main()
