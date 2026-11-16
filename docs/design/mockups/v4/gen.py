"""Generator for the v4 mobile-first mockups (#226). Writes five self-contained HTML files (390 px wide, no network).

    python docs/design/mockups/v4/gen.py

Static designs only: invented numbers, no app code, original layout and marks (no third-party assets).
"""
from __future__ import annotations

import pathlib

OUT = pathlib.Path(__file__).resolve().parent

CSS = """
:root{color-scheme:dark;
 /* colour roles */
 --bg:#0A0B0D;--surface:#131519;--surface-2:#1B1E24;--line:#262A31;--line-strong:#3A404A;
 --text:#F4F5F7;--text-2:#A1A8B3;--text-3:#8A929E;
 --accent:#4F95F0;--accent-text:#7FB2FF;--accent-tint:#132A4A;
 --up:#3DDC97;--up-tint:#12301F;--down:#FF6B6B;--down-tint:#2A1517;
 --amber:#F5B64C;--amber-tint:#33280F;
 /* spacing 4-pt scale */
 --s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;--s8:32px;
 /* type scale */
 --t-cap:12px;--t-sm:14px;--t-md:16px;--t-lg:20px;--t-xl:28px;--t-hero:44px;
 /* radius, elevation, motion */
 --r-sm:8px;--r-md:12px;--r-lg:16px;--r-xl:24px;--r-pill:999px;
 --e-sheet:0 -12px 40px rgba(0,0,0,.55);
 --ease:cubic-bezier(.2,.7,.2,1);--d-fast:120ms;--d-base:200ms;--d-sheet:320ms}
*{box-sizing:border-box}
html,body{margin:0}
body{width:390px;min-height:844px;margin:0 auto;background:var(--bg);color:var(--text);
 font:400 var(--t-md)/1.4 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased;padding-bottom:84px}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.dim{color:var(--text-2)}
h1,h2,p{margin:0}
/* top bar */
.top{display:flex;align-items:center;gap:var(--s2);height:56px;padding:0 var(--s4)}
.mark{width:28px;height:28px;border-radius:9px;background:var(--text);display:grid;place-items:center}
.top h1{font-size:var(--t-lg);font-weight:700;letter-spacing:-.02em;flex:1}
.pill{height:28px;padding:0 10px;border-radius:var(--r-pill);display:inline-flex;align-items:center;gap:6px;font-size:var(--t-cap);font-weight:600}
.pill.paper{background:var(--amber);color:#0A0B0D}
.pill.live{background:var(--surface);border:1px solid var(--line);color:var(--text)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--up);box-shadow:0 0 0 0 rgba(61,220,151,.5);animation:pulse 2s infinite}
.dot.amber{background:var(--amber);animation:none}.dot.red{background:var(--down);animation:none}.dot.grey{background:var(--text-3);animation:none}
@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(61,220,151,0)}100%{box-shadow:0 0 0 0 rgba(61,220,151,0)}}
/* status rows: one line, whole row is the tap target */
.status{display:flex;align-items:center;gap:var(--s3);min-height:48px;width:calc(100% - 32px);text-align:left;color:var(--text);font-family:inherit;margin:0 var(--s4);padding:var(--s2) var(--s3) var(--s2) var(--s4);
 border-radius:var(--r-lg);font-size:var(--t-sm);font-weight:500;border:1px solid transparent}
.status + .status{margin-top:var(--s2)}
.status .grow{flex:1;min-width:0}
.status small{display:block;font-size:var(--t-cap);font-weight:400;color:var(--text-2)}
.status.breach{background:var(--down-tint);border-color:#5A2A2E}
.status.exp{background:var(--accent-tint);border-color:#1F3F6B}
.status.ended{background:var(--amber-tint);border-color:#4A3A16}
.chev{flex:none;color:var(--text-3)}
/* hero */
.hero{padding:var(--s5) var(--s4) var(--s2)}
.hero .label{font-size:var(--t-sm);color:var(--text-2)}
.hero .value{font-size:var(--t-hero);font-weight:700;letter-spacing:-.04em;line-height:1.1}
.hero .delta{display:flex;gap:var(--s4);font-size:var(--t-sm);font-weight:600;margin-top:2px}
.chart{display:block;width:100%;height:150px;margin-top:var(--s2)}
.ranges{display:flex;gap:var(--s2);padding:var(--s2) var(--s4) var(--s3)}
.ranges button{flex:1;height:44px;border-radius:var(--r-pill);border:0;background:transparent;color:var(--text-2);font:600 var(--t-sm) inherit;font-family:inherit}
.ranges button[aria-pressed=true]{background:var(--surface-2);color:var(--text)}
/* sections and rows */
.sect{display:flex;align-items:baseline;justify-content:space-between;padding:var(--s4) var(--s4) var(--s2)}
.sect h2{font-size:var(--t-md);font-weight:600}
.sect a{font-size:var(--t-sm);color:var(--accent-text);text-decoration:none;min-height:44px;min-width:44px;display:inline-flex;align-items:center;justify-content:flex-end;margin:-12px 0}
.list{margin:0 var(--s4);background:var(--surface);border:1px solid var(--line);border-radius:var(--r-xl);overflow:hidden}
.row{display:flex;align-items:center;gap:var(--s3);min-height:64px;padding:var(--s3) var(--s4);width:100%;border:0;background:transparent;color:inherit;font:inherit;text-align:left}
.row + .row{border-top:1px solid var(--line)}
.row .main{flex:1;min-width:0}
.row b{display:block;font-size:var(--t-md);font-weight:600}
.row .sub{display:block;font-size:var(--t-cap);color:var(--text-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .end{text-align:right;flex:none}
.row .end b{font-size:var(--t-md)}
.row .end span{font-size:var(--t-cap);font-weight:600}
.badge{flex:none;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;font-size:var(--t-cap);font-weight:700;color:var(--text)}
.ico{flex:none;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;background:var(--surface-2);color:var(--text-2)}
.ico.up{background:var(--up-tint);color:var(--up)}.ico.down{background:var(--down-tint);color:var(--down)}.ico.amber{background:var(--amber-tint);color:var(--amber)}
.tag{display:inline-flex;align-items:center;height:20px;padding:0 8px;border-radius:var(--r-pill);font-size:var(--t-cap);font-weight:600;background:var(--surface-2);color:var(--text-2)}
.tag.exp{background:var(--accent-tint);color:var(--accent-text)}
.track{display:block;position:relative;height:4px;border-radius:4px;background:linear-gradient(90deg,#4A2529,var(--surface-2) 35%,var(--surface-2) 65%,#1C3B30);margin-top:8px}
.track i{position:absolute;top:-4px;width:12px;height:12px;margin-left:-6px;border-radius:50%;background:var(--text);border:2px solid var(--surface)}
.chips{display:flex;gap:var(--s2);padding:var(--s2) var(--s4) var(--s3);overflow-x:auto}
.chips button{flex:none;height:44px;padding:0 var(--s4);border-radius:var(--r-pill);border:1px solid var(--line);background:transparent;color:var(--text-2);font:600 var(--t-sm) inherit;font-family:inherit}
.chips button[aria-pressed=true]{background:var(--text);border-color:var(--text);color:#0A0B0D}
.day{padding:var(--s4) var(--s4) var(--s2);font-size:var(--t-cap);font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em}
.page-h{padding:var(--s2) var(--s4) var(--s1)}
.page-h h2{font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em}
.page-h p{font-size:var(--t-sm);color:var(--text-2)}
.note{margin:var(--s3) var(--s4) 0;font-size:var(--t-cap);color:var(--text-3)}
.big-state{display:flex;align-items:center;gap:var(--s4);margin:var(--s2) var(--s4) var(--s4);padding:var(--s5) var(--s4);border-radius:var(--r-xl);background:var(--surface);border:1px solid var(--line)}
.big-state .ring{width:56px;height:56px;border-radius:50%;display:grid;place-items:center;background:var(--up-tint);color:var(--up)}
.big-state b{font-size:var(--t-lg);display:block}
.big-state span{font-size:var(--t-sm);color:var(--text-2)}
/* bottom tab bar: five items, labels always shown */
.tabs{position:fixed;left:50%;transform:translateX(-50%);bottom:0;width:390px;height:76px;padding:8px 8px 18px;display:flex;
 background:rgba(10,11,13,.92);backdrop-filter:blur(14px);border-top:1px solid var(--line)}
.tabs a{flex:1;min-height:48px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;color:var(--text-3);
 font-size:var(--t-cap);font-weight:600;text-decoration:none}
.tabs a[aria-current=page]{color:var(--text)}
/* bottom sheet */
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.6)}
.sheet{position:fixed;left:50%;transform:translateX(-50%);bottom:0;width:390px;max-height:86%;padding:var(--s2) var(--s4) var(--s6);
 background:var(--surface);border-radius:var(--r-xl) var(--r-xl) 0 0;border-top:1px solid var(--line-strong);box-shadow:var(--e-sheet)}
.grab{width:36px;height:4px;border-radius:4px;background:var(--line-strong);margin:0 auto var(--s4)}
.sheet-h{display:flex;align-items:center;gap:var(--s3)}
.sheet-h b{font-size:var(--t-lg)}
.sheet-h .x{margin-left:auto;width:44px;height:44px;border-radius:50%;border:0;background:var(--surface-2);color:var(--text-2);display:grid;place-items:center}
.sheet .value{font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em;margin-top:var(--s3)}
.ladder{margin:var(--s5) 0 var(--s2)}
.ladder .track{height:6px}
.ladder .track i{top:-5px;width:16px;height:16px;margin-left:-8px}
.ladder .ends{display:flex;justify-content:space-between;font-size:var(--t-cap);color:var(--text-2);margin-top:var(--s2)}
.kv{display:grid;grid-template-columns:1fr auto;gap:var(--s3) var(--s4);font-size:var(--t-sm);padding:var(--s4) 0;border-top:1px solid var(--line);margin-top:var(--s4)}
.kv span:nth-child(odd){color:var(--text-2)}.kv span:nth-child(even){font-weight:600;text-align:right}
.callout{padding:var(--s3) var(--s4);border-radius:var(--r-lg);background:var(--accent-tint);border:1px solid #1F3F6B;font-size:var(--t-sm)}
.callout b{display:block;color:var(--accent-text);font-size:var(--t-sm)}
.btn{display:flex;align-items:center;justify-content:center;width:100%;height:52px;border-radius:var(--r-pill);border:0;background:var(--text);color:#0A0B0D;
 font:600 var(--t-md) inherit;font-family:inherit;margin-top:var(--s4)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

I = {  # 24 px line icons, drawn for this project
    "home": "M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z",
    "pos": "M4 19h16M7 16V9m5 7V5m5 11v-5",
    "act": "M4 7h16M4 12h10M4 17h13",
    "eng": "M3 12h4l3-7 4 14 3-7h4",
    "more": "M5 12h.01M12 12h.01M19 12h.01",
    "chev": "m9 6 6 6-6 6",
    "buy": "M12 19V5m-6 6 6-6 6 6",
    "sell": "M12 5v14m-6-6 6 6 6-6",
    "skip": "M6 6l12 12M18 6 6 18",
    "pause": "M9 6v12m6-12v12",
    "check": "m5 12 5 5 9-10",
    "x": "M6 6l12 12M18 6 6 18",
    "flask": "M9 3h6m-5 0v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3",
    "shield": "M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z",
}


def ico(name: str, size: int = 22, w: float = 2) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{w}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{I[name]}"/></svg>')


CHEV = f'<span class="chev">{ico("chev", 18)}</span>'
BADGE = {"NVDA": "#1F5A3A", "AVGO": "#6B2B2B", "LLY": "#6A4A1C", "COST": "#5A2440", "AMD": "#23406B", "JPM": "#3F3A6B", "CRM": "#1E5566"}


def badge(sym: str) -> str:
    return f'<span class="badge" style="background:{BADGE.get(sym, "#2A2F38")}">{sym}</span>'


def top() -> str:
    return ('<header class="top"><span class="mark"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0A0B0D" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 17l5-6 4 4 7-9"/></svg></span><h1>kaibot</h1>'
            '<span class="pill paper">Paper</span><span class="pill live"><span class="dot"></span>Running</span></header>')


def tabs(active: str) -> str:
    items = [("home", "Home"), ("pos", "Positions"), ("act", "Activity"), ("eng", "Engine"), ("more", "More")]
    return '<nav class="tabs" aria-label="Main">' + "".join(
        f'<a href="#"{" aria-current=page" if k == active else ""}>{ico(k)}{label}</a>' for k, label in items) + "</nav>"


def chart() -> str:
    pot = [0, 18, -24, 37, 72, 60, 135, 79, 108, 158, 126, 170, 107]
    spy = [0, 20, -40, 40, 70, 60, 110, 20, 60, 120, 100, 130, 129]
    lo, hi, w, h = -60, 190, 390, 150
    pts = lambda s: " ".join(f"{i * (w - 12) / (len(s) - 1):.1f},{h - 14 - (v - lo) / (hi - lo) * (h - 28):.1f}" for i, v in enumerate(s))
    last = pts(pot).split(" ")[-1].split(",")
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Pot against the S&amp;P 500 since the start">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4F95F0" stop-opacity=".28"/><stop offset="1" stop-color="#4F95F0" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="0,{h} {pts(pot)} {w - 12},{h}" fill="url(#g)"/>'
            f'<polyline points="{pts(spy)}" fill="none" stroke="#8A929E" stroke-width="1.5" stroke-dasharray="4 4"/>'
            f'<polyline points="{pts(pot)}" fill="none" stroke="#4F95F0" stroke-width="2.5" stroke-linejoin="round"/>'
            f'<circle cx="{last[0]}" cy="{last[1]}" r="5" fill="#4F95F0" stroke="#0A0B0D" stroke-width="2"/></svg>')


def pos_row(sym, sub, value, pnl, up, at) -> str:
    sub_html = f'<span class="sub">{sub}</span>' if sub else ""
    return (f'<button class="row">{badge(sym)}<span class="main"><b>{sym}</b>{sub_html}'
            f'<span class="track" aria-hidden="true"><i style="left:{at}%"></i></span></span>'
            f'<span class="end num"><b>{value}</b><span class="{"up" if up else "down"}">{pnl}</span></span></button>')


def simple_row(icon, tone, title, sub, end="", end_cls="", chev=False) -> str:
    return (f'<button class="row"><span class="ico {tone}">{ico(icon, 20)}</span><span class="main"><b>{title}</b><span class="sub">{sub}</span></span>'
            + (f'<span class="end num"><b class="{end_cls}">{end}</b></span>' if end else "") + (CHEV if chev else "") + "</button>")


def page(title: str, body: str, active: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Kaibot v4 mockup · {title}</title><style>{CSS}</style></head><body>{body}{tabs(active)}</body></html>\n')


HALT = (f'<button class="status breach"><span class="dot red"></span><span class="grow">New buys paused until 13:05</span>{CHEV}</button>')
EXP = (f'<button class="status exp"><span class="grow">Experiment: failed the ten-year test</span>{CHEV}</button>')

POSITIONS = [("NVDA", "14 shares", "$2,070.88", "+$80.36", True, 56), ("AVGO", "4 shares", "$1,308.20", "−$17.40", False, 26),
             ("LLY", "2 shares", "$1,658.62", "+$33.30", True, 45), ("COST", "1 share", "$941.77", "+$7.57", True, 38)]


def home() -> str:
    rows = "".join(pos_row(s, "", v, p, up, at) for (s, _, v, p, up, at) in POSITIONS[:3])
    body = (top() + HALT + EXP
            + '<section class="hero"><p class="label">Paper pot</p><p class="value num">$10,107.10</p>'
              '<p class="delta num"><span class="up">+$107.10 all time</span><span class="down">−$63.40 today</span></p></section>'
            + chart()
            + '<div class="ranges" role="group" aria-label="Chart range"><button>1W</button><button>1M</button><button>3M</button><button aria-pressed="true">All</button></div>'
            + '<div class="sect"><h2>Holding 4 of 5</h2><a href="#" aria-label="All positions">' + ico("chev", 20) + '</a></div>'
            + f'<div class="list">{rows}</div>')
    return page("Home", body, "home")


def positions(with_sheet: bool = False) -> str:
    rows = "".join(pos_row(s, f"{sub} · in at {inat}", v, p, up, at) for (s, sub, v, p, up, at), inat in zip(POSITIONS, ["$142.18", "$331.40", "$812.66", "$934.20"]))
    body = (top() + '<div class="page-h"><h2>Positions</h2><p class="num">4 open · $5,979 invested · 59% of pot</p></div>'
            + '<div class="chips" role="group" aria-label="Show"><button aria-pressed="true">Open</button><button>Closed</button></div>'
            + EXP.replace("Experiment: failed the ten-year test", "Trend momentum is an experiment: it failed the ten-year test<small>Paper only · up to 60% invested</small>")
            + f'<div class="sect"><h2>Open now</h2></div><div class="list">{rows}</div>'
            + '<p class="note">Every stop is held at the broker, so it works even when your Mac is off.</p>')
    if with_sheet:
        body += ('<div class="scrim"></div><section class="sheet" role="dialog" aria-modal="true" aria-label="NVDA position">'
                 '<div class="grab"></div>'
                 f'<div class="sheet-h">{badge("NVDA")}<div><b>NVDA</b><div class="dim" style="font-size:var(--t-sm)">NVIDIA · 14 shares</div></div>'
                 f'<button class="x" aria-label="Close">{ico("x", 18)}</button></div>'
                 '<p class="value num">$2,070.88 <span class="up" style="font-size:var(--t-md);letter-spacing:0">+$80.36 · +4.0%</span></p>'
                 '<div class="ladder"><div class="track" aria-hidden="true"><i style="left:56%"></i></div>'
                 '<div class="ends num"><span>Stop $133.65</span><span>Now $147.92</span><span>Target $159.24</span></div></div>'
                 '<div class="callout"><b>Experiment: failed the ten-year test</b>Trend momentum trades on paper only, to see how it behaves live. It never trades real money.</div>'
                 '<div class="kv num"><span>Bought</span><span>Nov 12 at $142.18</span><span>Why</span><span>Steady uptrend, ranked 3 of 40</span>'
                 '<span>If it hits the stop</span><span>−$119.42</span></div>'
                 '<p class="dim" style="font-size:var(--t-cap)">Kaibot manages each trade itself. You can pause or stop the whole run under More.</p>'
                 '<button class="btn">Full trade details</button></section>')
    return page("Positions" + (" · sheet" if with_sheet else ""), body, "pos")


def activity() -> str:
    today = (simple_row("pause", "down", "New buys paused", "3 losses in a row · 12:05", chev=True)
             + simple_row("sell", "down", "Sold CAT", "Stop hit · 12:04", "−$48.10", "down", )
             + simple_row("skip", "", "Skipped AMD", "Company news in the last day · 11:20", chev=True)
             + simple_row("buy", "up", "Bought 14 NVDA", "Trend momentum · 10:52", "$142.18"))
    yday = (simple_row("sell", "up", "Sold JPM", "Target reached · 15:12", "+$38.20", "up")
            + simple_row("buy", "up", "Bought 4 AVGO", "Trend momentum · 10:41", "$331.40")
            + simple_row("skip", "", "Skipped CRM", "Its whole sector fell too · 10:15", chev=True))
    body = (top() + '<div class="page-h"><h2>Activity</h2><p>What Kaibot did, and what it passed on.</p></div>'
            + '<div class="chips" role="group" aria-label="Show"><button aria-pressed="true">All</button><button>Buys &amp; sells</button><button>Skipped</button><button>Alerts</button></div>'
            + f'<div class="day">Today</div><div class="list">{today}</div><div class="day">Friday, Nov 13</div><div class="list">{yday}</div>')
    return page("Activity", body, "act")


def engine() -> str:
    def health(dot, title, sub):
        return f'<button class="row"><span class="ico" style="background:transparent"><span class="dot {dot}"></span></span><span class="main"><b>{title}</b><span class="sub">{sub}</span></span>{CHEV}</button>'
    body = (top() + '<div class="page-h"><h2>Engine</h2></div>'
            + f'<div class="big-state"><span class="ring">{ico("check", 28, 2.5)}</span><div><b>Running</b><span class="num">Checked in 12 seconds ago</span></div></div>'
            + HALT.replace("New buys paused until 13:05", "New buys paused until 13:05<small>3 losses in a row. Open trades keep their stops.</small>")
            + '<div class="sect"><h2>Safety checks</h2></div><div class="list">'
            + health("", "Engine", "Trading · on your Mac") + health("", "Watchdog", "Watching · sees the engine")
            + health("", "Safety net", "Armed · sells all at 15:56 if silent") + health("", "Paper account", "Clean · only Kaibot's orders")
            + '</div><div class="sect"><h2>Today</h2></div><div class="list">'
            + simple_row("act", "", "3 setups found", "2 bought · 1 skipped", chev=True)
            + simple_row("flask", "", "Trend momentum", "Experiment: failed the ten-year test", chev=True).replace('class="ico "', 'class="ico" style="background:var(--accent-tint);color:var(--accent-text)"')
            + simple_row("shield", "", "Go / no-go", "Keep collecting · 37 of 900 trades", chev=True)
            + '</div><div class="list" style="margin-top:var(--s4)">' + simple_row("more", "", "Technical details", "Version, fingerprints, operations log", chev=True) + "</div>")
    return page("Engine", body, "eng")


if __name__ == "__main__":
    for name, html in [("home", home()), ("positions", positions()), ("activity", activity()), ("engine", engine()), ("sheet", positions(True))]:
        (OUT / f"{name}.html").write_text(html)
        print("wrote", name, len(html))
