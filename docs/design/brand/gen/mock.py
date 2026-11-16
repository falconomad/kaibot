"""Static mockups in the v4 layout, drawn twice: with today's token values and with brand system v1 (issue #265).

The markup is the same for both; only the token values, the typeface and four named details change (the logo, the
primary button, the experiment label, the live point on the chart and the tab bar). Invented sample numbers.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re

import tokens

HERE = pathlib.Path(__file__).resolve().parent
LOGO = HERE.parent.parent / "logo" / "concepts" / "1-rising-k"

BEFORE = {  # apps/web/src/tokens.css on main, 16 Nov 2026, plus the colours written straight into app.css
    "paper": "#0A0B0D", "surface": "#131519", "surface-2": "#1B1E24", "rule": "#262A31", "rule-strong": "#3A404A", "ink": "#F4F5F7", "slate": "#A1A8B3", "stale": "#8A929E",
    "sea": "#4F95F0", "sea-ink": "#7FB2FF", "sea-tint": "#132A4A", "gain": "#3DDC97", "gain-tint": "#12301F", "loss": "#FF6B6B", "loss-tint": "#2A1517",
    "warn": "#F5B64C", "warn-tint": "#33280F", "ai": "#C9B6FF", "ai-tint": "#1E1A2B", "s-pot": "#4F95F0", "s-spy": "#C4862F",
    "on-sea": "#0A0B0D", "sea-line": "#2D4F7A", "focus": "#4F95F0", "selection": "#174189", "live": "#3DDC97", "control": "#262A31", "loss-line": "#5A2A2E", "warn-line": "#4A3A16",
    "warn-text": "#F5B64C", "on-warn": "#0A0B0D", "paper-edge": "#F5B64C", "ai-line": "#3A3155", "exp-bg": "#16263D", "exp-line": "#2D4F7A", "exp-hatch": "#2D4F7A", "exp-text": "#F4F5F7",
    "s-grid": "#262A31", "s-up": "#3DDC97", "s-down": "#FF6B6B", "scrim": "rgba(0, 0, 0, .6)", "bar-bg": "rgba(10, 11, 13, .86)", "elev-sheet": "0 -12px 40px rgba(0, 0, 0, .55)",
    "btn-bg": "#F4F5F7", "btn-fg": "#0A0B0D", "font": "'Inter', system-ui, sans-serif",
}


def after(mode: str) -> dict:
    t = dict(tokens.theme(mode))
    t.update({"btn-bg": t["sea"], "btn-fg": t["on-sea"], "font": "'Outfit', 'Inter', system-ui, sans-serif"})
    return t


def font_face() -> str:
    """Outfit, embedded for the render only (the font is not in the repository)."""
    fonts = json.loads(pathlib.Path(os.environ.get("KAIBO_BRAND_FONTS") or HERE / "fonts.json").read_text())
    out = []
    for w in (400, 500, 600, 700):
        b = base64.b64encode(pathlib.Path(fonts[f"outfit-{w}"]).read_bytes()).decode()
        out.append(f'@font-face{{font-family:"Outfit";font-weight:{w};src:url(data:font/woff;base64,{b}) format("woff")}}')
    return "".join(out)


CSS = """
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--paper);color:var(--ink);font:400 16px/1.4 var(--font);-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
::selection{background:var(--selection);color:var(--ink)}
h1,h2,p{margin:0}
.phone{width:390px;height:844px;position:relative;overflow:hidden;background:var(--paper)}
.up{color:var(--gain)}.down{color:var(--loss)}.dim{color:var(--slate)}
[data-v=after]{word-spacing:.06em}
/* skeleton, table, meter (components sheet) */
.sk{height:14px;border-radius:8px;background:linear-gradient(90deg,var(--surface-2),var(--rule),var(--surface-2))}
.tbl{width:100%;border-collapse:collapse;font-size:14px}.tbl th{font-size:12px;font-weight:600;color:var(--slate);text-align:left;padding:8px 0;border-bottom:1px solid var(--rule-strong)}.tbl td{padding:10px 0;border-bottom:1px solid var(--rule)}.tbl .n{text-align:right}
.meter{height:8px;border-radius:8px;background:var(--surface-2);overflow:hidden}.meter i{display:block;height:100%;border-radius:8px;background:var(--sea)}
/* header */
.hdr{height:57px;display:flex;align-items:center;gap:8px;padding:6px 16px;border-bottom:1px solid var(--surface-2);background:var(--bar-bg)}
.brand{display:flex;align-items:center;gap:8px;min-height:44px;font-weight:700;font-size:17px;letter-spacing:-.02em;color:var(--ink)}
.sp{flex:1}
.pill{font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--on-warn);background:var(--warn);border:1px solid var(--paper-edge);border-radius:999px;padding:3px 9px;white-space:nowrap}
.chip{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 12px;border:1px solid var(--rule);border-radius:999px;background:var(--surface);color:var(--slate);font-size:13px}
.chip b{color:var(--ink);font-weight:600}.dot{width:8px;height:8px;border-radius:50%;background:var(--live);flex:none}
/* status rows */
.st{display:flex;align-items:center;gap:12px;min-height:56px;margin:8px 16px 0;padding:10px 12px 10px 16px;border-radius:16px;font-size:14px;font-weight:500;border:1px solid transparent}
.st .g{flex:1;min-width:0}.st small{display:block;font-size:12px;font-weight:400;color:var(--slate)}
.st.breach{background:var(--loss-tint);border-color:var(--loss-line)}.st.breach .d{width:10px;height:10px;border-radius:50%;background:var(--loss);flex:none}
.st.exp{background:var(--exp-bg);border-color:var(--exp-line);color:var(--exp-text)}
.st.warn{background:var(--warn-tint);border-color:var(--warn-line)}
.st .ic{flex:none;display:none;color:var(--ink)}
.chev{flex:none;color:var(--stale)}
/* brand v1: the experiment is a lab note, not a brand element: neutral, dashed, with a flask and a hatched edge */
[data-v=after] .st.exp{border-style:dashed;position:relative;padding-left:22px;overflow:hidden}
[data-v=after] .st.exp::before{content:"";position:absolute;left:0;top:0;bottom:0;width:8px;background:repeating-linear-gradient(135deg,var(--exp-line) 0 2px,transparent 2px 6px)}
[data-v=after] .st.exp .ic{display:block}
.tag{display:inline-flex;align-items:center;gap:4px;height:22px;padding:0 8px;border-radius:999px;font-size:12px;font-weight:600;background:var(--surface-2);color:var(--slate);white-space:nowrap}
.tag.exp{background:var(--sea-tint);color:var(--sea-ink)}
[data-v=after] .tag.exp{background:transparent;color:var(--exp-text);border:1px dashed var(--exp-line)}
/* hero and chart */
.hero{padding:18px 16px 6px}.hero .l{font-size:14px;color:var(--slate)}.hero .v{font-size:44px;font-weight:700;letter-spacing:-.035em;line-height:1.1}
.hero .dl{display:flex;gap:16px;font-size:14px;font-weight:600;margin-top:2px}
.chart{display:block;width:100%;height:150px;margin-top:6px}
.rg{display:flex;gap:8px;padding:8px 16px 10px}.rg b{flex:1;height:44px;border-radius:999px;display:grid;place-items:center;color:var(--slate);font-size:14px;font-weight:600}
.rg b.on{background:var(--surface-2);color:var(--ink)}
.sec{display:flex;align-items:center;justify-content:space-between;padding:12px 16px 8px}.sec h2{font-size:16px;font-weight:600}.sec a{color:var(--sea-ink)}
.list{margin:0 16px;background:var(--surface);border:1px solid var(--rule);border-radius:24px;overflow:hidden}
.row{display:flex;align-items:center;gap:12px;min-height:64px;padding:12px 16px}.row+.row{border-top:1px solid var(--rule)}
.row .m{flex:1;min-width:0}.row b{display:block;font-size:16px;font-weight:600}.row .s{display:block;font-size:12px;color:var(--slate);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .e{text-align:right;flex:none}.row .e span{font-size:12px;font-weight:600}
.bdg{flex:none;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;background:var(--surface-2);color:var(--ink)}
.ico{flex:none;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;background:var(--surface-2);color:var(--slate)}
.ico.u{background:var(--gain-tint);color:var(--gain)}.ico.d{background:var(--loss-tint);color:var(--loss)}
.trk{display:block;position:relative;height:4px;border-radius:4px;margin-top:8px;background:linear-gradient(90deg,var(--loss-line),var(--surface-2) 35%,var(--surface-2) 65%,var(--gain-line,var(--gain-tint)))}
.trk i{position:absolute;top:-4px;width:12px;height:12px;margin-left:-6px;border-radius:50%;background:var(--ink);border:2px solid var(--surface)}
[data-v=after] .trk i{background:var(--live)}
.chips{display:flex;gap:8px;padding:8px 16px 4px}.chips b{height:44px;padding:0 16px;border-radius:999px;border:1px solid var(--control);display:grid;place-items:center;color:var(--slate);font-size:14px;font-weight:600;white-space:nowrap}
.chips b.on{background:var(--ink);border-color:var(--ink);color:var(--paper)}
.ph{padding:8px 16px 0}.ph h2{font-size:28px;font-weight:700;letter-spacing:-.03em}.ph p{font-size:14px;color:var(--slate)}
.day{padding:14px 16px 8px;font-size:12px;font-weight:600;color:var(--stale);text-transform:uppercase;letter-spacing:.06em}
.note{margin:10px 16px 0;font-size:12px;color:var(--stale)}
.big{display:flex;align-items:center;gap:16px;margin:8px 16px 0;padding:18px 16px;border-radius:24px;background:var(--surface);border:1px solid var(--rule)}
.big .r{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;background:var(--gain-tint);color:var(--gain)}.big b{font-size:20px;display:block}.big span{font-size:14px;color:var(--slate)}
/* tab bar */
.tabs{position:absolute;left:0;right:0;bottom:0;height:76px;padding:8px 8px 18px;display:flex;background:var(--bar-bg);border-top:1px solid var(--rule)}
.tabs a{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;color:var(--stale);font-size:12px;font-weight:600;position:relative}
.tabs a.on{color:var(--ink)}
[data-v=after] .tabs a.on::before{content:"";position:absolute;top:-2px;width:6px;height:6px;border-radius:50%;background:var(--sea)}
/* sheet */
.scrim{position:absolute;inset:0;background:var(--scrim)}
.sheet{position:absolute;left:0;right:0;bottom:0;padding:8px 16px 24px;background:var(--surface);border-radius:24px 24px 0 0;border-top:1px solid var(--rule-strong);box-shadow:var(--elev-sheet)}
.grab{width:36px;height:4px;border-radius:4px;background:var(--rule-strong);margin:0 auto 16px}
.sh{display:flex;align-items:center;gap:12px}.sh b{font-size:20px}.x{margin-left:auto;width:44px;height:44px;border-radius:50%;background:var(--surface-2);color:var(--slate);display:grid;place-items:center}
.sheet .v{font-size:28px;font-weight:700;letter-spacing:-.03em;margin-top:12px}
.lad{margin:20px 0 8px}.lad .trk{height:6px}.lad .trk i{top:-5px;width:16px;height:16px;margin-left:-8px}.ends{display:flex;justify-content:space-between;font-size:12px;color:var(--slate);margin-top:8px}
.call{display:flex;gap:10px;padding:12px 14px;border-radius:16px;background:var(--exp-bg);border:1px solid var(--exp-line);font-size:14px;color:var(--exp-text)}
.call b{display:block}.call svg{flex:none;display:none;margin-top:2px}
[data-v=after] .call{border-style:dashed}[data-v=after] .call svg{display:block}
.kv{display:grid;grid-template-columns:1fr auto;gap:12px 16px;font-size:14px;padding:16px 0;border-top:1px solid var(--rule);margin-top:16px}
.kv span:nth-child(odd){color:var(--slate)}.kv span:nth-child(even){font-weight:600;text-align:right}
.btn{display:flex;align-items:center;justify-content:center;width:100%;height:52px;border-radius:999px;background:var(--btn-bg);color:var(--btn-fg);font-weight:600;font-size:16px;margin-top:14px}
.btn.ghost{background:var(--surface-2);color:var(--ink);border:1px solid var(--control)}
/* sign in */
.center{height:100%;display:flex;align-items:center;justify-content:center;padding:24px}
.card{width:100%;background:var(--surface);border:1px solid var(--rule);border-radius:24px;padding:28px;display:flex;flex-direction:column;gap:14px}
.card h1{font-size:22px;font-weight:700;letter-spacing:-.02em}.card p{font-size:14px;color:var(--slate)}
.inp{height:48px;border-radius:12px;border:1px solid var(--control);background:var(--paper);display:flex;align-items:center;padding:0 16px;color:var(--ink)}
.inp.f{outline:2px solid var(--focus);outline-offset:2px}
label{font-size:12px;font-weight:600;color:var(--slate)}
/* desktop */
.desk{width:1280px;height:800px;overflow:hidden;background:var(--paper)}
.dtop{display:flex;align-items:center;gap:20px;height:72px;padding:0 32px;border-bottom:1px solid var(--surface-2)}
.dnav{display:flex;gap:0;flex:1;margin-left:8px;font-size:14px;font-weight:500}.dnav a{white-space:nowrap;display:inline-flex;align-items:center;min-height:44px;padding:0 11px;border-radius:999px;color:var(--slate)}
.dnav a.on{background:var(--surface-2);color:var(--ink)}
.dmain{max-width:1240px;margin:0 auto;padding:20px 32px;display:grid;grid-template-columns:1.5fr 1fr;gap:20px}
.dmain .st{margin:0 0 8px}.dcard{background:var(--surface);border:1px solid var(--rule);border-radius:24px;padding:22px}
.dcard h2{font-size:17px;font-weight:600;margin-bottom:10px}.desk .hero{padding:10px 0 4px}.desk .hero .v{font-size:60px}.desk .chart{height:250px}
.desk .list{margin:0}.desk .rg{padding:8px 0}.desk .rg b{flex:none;width:64px}
"""

I = {"home": "M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z", "pos": "M4 19h16M7 16V9m5 7V5m5 11v-5", "act": "M4 7h16M4 12h10M4 17h13", "eng": "M3 12h4l3-7 4 14 3-7h4",
     "more": "M5 12h.01M12 12h.01M19 12h.01", "chev": "m9 6 6 6-6 6", "buy": "M12 19V5m-6 6 6-6 6 6", "sell": "M12 5v14m-6-6 6 6 6-6", "skip": "M6 6l12 12M18 6 6 18", "pause": "M9 6v12m6-12v12",
     "check": "m5 12 5 5 9-10", "flask": "M9 3h6m-5 0v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3", "shield": "M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z"}


def ico(n, size=22, w=2.0, cls=""):
    return (f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{w}" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{I[n]}"/></svg>')


CHEV = f'<span class="chev">{ico("chev", 18)}</span>'


def brand(v: str, t: dict, light=False) -> str:
    if v == "before":   # today's mark: a white tile with a short zigzag, and the name set in the UI face
        return ('<span class="brand"><svg width="26" height="26" viewBox="0 0 28 28"><rect x="1" y="1" width="26" height="26" rx="8" fill="#F4F5F7"/>'
                '<path d="M8 18 L12.5 12.5 L15.5 15.5 L20 9.5" stroke="#0A0B0D" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>kaibo</span>')
    svg = (LOGO / ("wordmark-on-light.svg" if light else "wordmark-on-dark.svg")).read_text()
    vb = [float(x) for x in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    h = 21.0
    svg = re.sub(r' width="[^"]+" height="[^"]+"', f' width="{vb[2] * h / vb[3]:.1f}" height="{h}"', svg, count=1)
    return f'<span class="brand" style="margin-top:-3px">{svg}</span>'


def header(v, t, light=False):
    return f'<div class="hdr">{brand(v, t, light)}<span class="sp"></span><span class="pill">PAPER</span><span class="chip"><i class="dot"></i><b>Running</b></span></div>'


def tabs(on):
    items = [("home", "Home"), ("pos", "Positions"), ("act", "Activity"), ("eng", "Engine"), ("more", "More")]
    return '<div class="tabs">' + "".join(f'<a class="{"on" if k == on else ""}">{ico(k)}{n}</a>' for k, n in items) + "</div>"


def chart(t, w=390, h=150, v="before"):
    pot = [0, 18, -24, 37, 72, 60, 135, 79, 108, 158, 126, 170, 107]
    spy = [0, 20, -40, 40, 70, 60, 110, 20, 60, 120, 100, 130, 129]
    lo, hi = -60, 190
    X = lambda i, n: i * (w - 16) / (n - 1)  # noqa: E731
    Y = lambda val: h - 14 - (val - lo) / (hi - lo) * (h - 28)  # noqa: E731
    pts = lambda s: " ".join(f"{X(i, len(s)):.1f},{Y(val):.1f}" for i, val in enumerate(s))  # noqa: E731
    lx, ly = X(len(pot) - 1, len(pot)), Y(pot[-1])
    dash = "4 4" if v == "before" else "2 5"
    end = (f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="{t["s-pot"]}" stroke="{t["paper"]}" stroke-width="2"/>' if v == "before" else
           f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="10" fill="none" stroke="{t["live"]}" stroke-width="1.5" opacity=".35"/><circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="{t["live"]}"/>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t["s-pot"]}" stop-opacity=".26"/>'
            f'<stop offset="1" stop-color="{t["s-pot"]}" stop-opacity="0"/></linearGradient></defs><polygon points="0,{h} {pts(pot)} {lx:.1f},{h}" fill="url(#g)"/>'
            f'<polyline points="{pts(spy)}" fill="none" stroke="{t["s-spy"]}" stroke-width="1.5" stroke-dasharray="{dash}" stroke-linecap="round"/>'
            f'<polyline points="{pts(pot)}" fill="none" stroke="{t["s-pot"]}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>{end}</svg>')


HALT = f'<div class="st breach"><i class="d"></i><span class="g">New buys paused until 13:05</span>{CHEV}</div>'
EXP = f'<div class="st exp">{ico("flask", 20, 2, "ic")}<span class="g">Experiment: failed the ten-year test</span>{CHEV}</div>'
POS = [("NVDA", "14 shares · in at $142.18", "$2,070.88", "▲ +$80.36", True, 56), ("AVGO", "4 shares · in at $331.40", "$1,308.20", "▼ −$17.40", False, 26),
       ("LLY", "2 shares · in at $812.66", "$1,658.62", "▲ +$33.30", True, 45), ("COST", "1 share · in at $934.20", "$941.77", "▲ +$7.57", True, 38)]


def prow(s, sub, val, pnl, up, at, show_sub=True):
    return (f'<div class="row"><span class="bdg">{s}</span><span class="m"><b>{s}</b>' + (f'<span class="s">{sub}</span>' if show_sub else "")
            + f'<span class="trk"><i style="left:{at}%"></i></span></span><span class="e"><b>{val}</b><span class="{"up" if up else "down"}">{pnl}</span></span></div>')


def arow(icon, tone, title, sub, end="", cls=""):
    return (f'<div class="row"><span class="ico {tone}">{ico(icon, 20)}</span><span class="m"><b>{title}</b><span class="s">{sub}</span></span>'
            + (f'<span class="e"><b class="{cls}">{end}</b></span>' if end else CHEV) + "</div>")


def hero():
    return ('<div class="hero"><p class="l">Paper pot</p><p class="v">$10,107.10</p><p class="dl"><span class="up">▲ +$107.10 all time</span><span class="down">▼ −$63.40 today</span></p></div>')


def home(v, t, light=False):
    rows = "".join(prow(*p, show_sub=False) for p in POS[:3])
    return (header(v, t, light) + HALT + EXP + hero() + chart(t, v=v) + '<div class="rg"><b>1W</b><b>1M</b><b>3M</b><b class="on">All</b></div>'
            + f'<div class="sec"><h2>Holding 4 of 5</h2><a>{ico("chev", 20)}</a></div><div class="list">{rows}</div>' + tabs("home"))


def positions(v, t, light=False):
    rows = "".join(prow(*p) for p in POS)
    exp = EXP.replace("Experiment: failed the ten-year test", "Trend momentum is an experiment: it failed the ten-year test<small>Paper only · up to 60% invested</small>")
    sheet = ('<div class="scrim"></div><div class="sheet"><div class="grab"></div><div class="sh"><span class="bdg">NVDA</span><div><b>NVDA</b>'
             f'<div class="dim" style="font-size:14px">NVIDIA · 14 shares</div></div><span class="x">{ico("skip", 18)}</span></div>'
             '<p class="v">$2,070.88 <span class="up" style="font-size:16px;letter-spacing:0">▲ +$80.36 · +4.0%</span></p>'
             '<div class="lad"><span class="trk"><i style="left:56%"></i></span><div class="ends"><span>Stop $133.65</span><span>Now $147.92</span><span>Target $159.24</span></div></div>'
             f'<div class="call">{ico("flask", 18)}<span><b>Experiment: failed the ten-year test</b>Trend momentum trades on paper only, to see how it behaves live. It never trades real money.</span></div>'
             '<div class="kv"><span>Bought</span><span>Nov 12 at $142.18</span><span>Why</span><span>Steady uptrend, ranked 3 of 40</span><span>If it hits the stop</span><span>−$119.42</span></div>'
             '<div class="btn">Full trade details</div></div>')
    return (header(v, t, light) + '<div class="ph"><h2>Positions</h2><p>4 open · $5,979 invested · 59% of pot</p></div><div class="chips"><b class="on">Open</b><b>Closed</b></div>'
            + exp + f'<div class="sec"><h2>Open now</h2></div><div class="list">{rows}</div>' + tabs("pos") + sheet)


def activity(v, t, light=False):
    today = (arow("pause", "d", "New buys paused", "3 losses in a row · 12:05") + arow("sell", "d", "Sold CAT", "Stop hit · 12:04", "−$48.10", "down")
             + arow("skip", "", "Skipped AMD", "Company news in the last day · 11:20") + arow("buy", "u", "Bought 14 NVDA", "Trend momentum · 10:52", "$142.18"))
    yday = (arow("sell", "u", "Sold JPM", "Target reached · 15:12", "+$38.20", "up") + arow("buy", "u", "Bought 4 AVGO", "Trend momentum · 10:41", "$331.40")
            + arow("skip", "", "Skipped CRM", "Its whole sector fell too · 10:15"))
    return (header(v, t, light) + '<div class="ph"><h2>Activity</h2><p>What kaibo did, and what it passed on.</p></div>'
            '<div class="chips"><b class="on">All</b><b>Buys &amp; sells</b><b>Skipped</b><b>Alerts</b></div>'
            + f'<div class="day">Today</div><div class="list">{today}</div><div class="day">Friday, Nov 13</div><div class="list">{yday}</div>' + tabs("act"))


def engine(v, t, light=False):
    hrow = lambda a, b: f'<div class="row"><span class="ico" style="background:transparent"><i class="dot" style="background:var(--gain)"></i></span><span class="m"><b>{a}</b><span class="s">{b}</span></span>{CHEV}</div>'  # noqa: E731
    halt = HALT.replace("New buys paused until 13:05", "New buys paused until 13:05<small>3 losses in a row. Open trades keep their stops.</small>")
    return (header(v, t, light) + '<div class="ph"><h2>Engine</h2></div>'
            + f'<div class="big"><span class="r">{ico("check", 28, 2.5)}</span><div><b>Running</b><span>Checked in 12 seconds ago</span></div></div>' + halt
            + '<div class="sec"><h2>Safety checks</h2></div><div class="list">' + hrow("Engine", "Trading · on your Mac") + hrow("Watchdog", "Watching · sees the engine")
            + hrow("Safety net", "Armed · sells all at 15:56 if silent") + "</div>"
            + '<div class="sec"><h2>Today</h2></div><div class="list">' + arow("act", "", "3 setups found", "2 bought · 1 skipped")
            + f'<div class="row"><span class="ico">{ico("flask", 20)}</span><span class="m"><b>Trend momentum</b><span class="s">Experiment: failed the ten-year test</span></span>{CHEV}</div></div>' + tabs("eng"))


def signin(v, t, light=False):
    return (f'<div class="center"><div class="card">{brand(v, t, light)}<h1>Sign in</h1><p>kaibo trades on paper only. Enter your email and we send you a sign-in link.</p>'
            '<label>Email</label><div class="inp f">you@example.com</div><div class="btn">Send the link</div><div class="btn ghost" style="margin-top:0">Use a different account</div>'
            '<p style="font-size:12px">Simulated money. Nothing here is advice.</p></div></div>')


def desktop(v, t, light=False):
    nav = "".join(f'<a class="{"on" if n == "Home" else ""}">{n}</a>' for n in ["Home", "Positions", "Activity", "Engine", "Report", "Go / no-go", "Strategy", "Run & settings"])
    rows = "".join(prow(*p) for p in POS)
    return (f'<div class="dtop">{brand(v, t, light)}<div class="dnav">{nav}</div><span class="pill">PAPER · BALANCED</span><span class="chip"><i class="dot"></i><b>Running</b> kai-macbook · 12 s ago</span></div>'
            f'<div class="dmain"><div>{HALT}{EXP}<div class="dcard" style="margin-top:12px">{hero()}{chart(t, 720, 250, v)}<div class="rg"><b>1W</b><b>1M</b><b>3M</b><b class="on">All</b></div></div></div>'
            f'<div><div class="dcard"><h2>Holding 4 of 5</h2><div class="list">{rows}</div></div>'
            '<div class="dcard" style="margin-top:20px"><h2>Controls</h2><p class="dim" style="font-size:14px">Pause new buys, or sell everything and stop the run.</p>'
            '<div style="display:flex;gap:12px"><div class="btn" style="flex:1">Pause new buys</div><div class="btn ghost" style="flex:1;color:var(--loss);border-color:var(--loss-line)">Flatten and stop</div></div></div></div></div>')


SCREENS = {"home": (home, "phone"), "positions-sheet": (positions, "phone"), "activity": (activity, "phone"), "engine": (engine, "phone"), "sign-in": (signin, "phone"),
           "home-desktop": (desktop, "desk")}


def variables(t: dict) -> str:
    return ":root{" + "".join(f"--{k}:{v};" for k, v in t.items()) + "}"


def screen(name: str, v: str, mode="dark") -> str:
    """One screen as a self-contained HTML fragment (a div with its own variables)."""
    fn, frame = SCREENS[name]
    t = BEFORE if v == "before" else after(mode)
    style = "".join(f"--{k}:{val};" for k, val in t.items())
    return f'<div class="{frame}" data-v="{v}" style="{style}background:var(--paper);color:var(--ink);font-family:var(--font)">{fn(v, t, mode == "light")}</div>'
