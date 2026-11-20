"""Dashboard notices, "New on the market" and "Big day" (#378, M8 S104). Static mockups in the brand v1 look.

    python docs/design/mockups/v4/notices/gen.py           # writes the HTML files next to this one
    python docs/design/mockups/v4/notices/gen.py --shot    # and the PNGs (Playwright + Chromium)

Reuses the brand system's mockup kit (docs/design/brand/gen/mock.py: tokens, header, hero, chart, rows, tab bar) and
embeds Outfit from apps/web/public/fonts at render time. Sample days are real calendar days (ANT's first session,
19 Nov 2026; the Fed's statement of 9 Dec 2026); the pot and holdings are the kit's invented numbers. Times are
shown as a viewer in Berlin sees them. Design only: the app's code is apps/web/src/components/Notices.tsx.
"""
from __future__ import annotations

import base64
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT / "docs" / "design" / "brand" / "gen"))

import mock  # noqa: E402

T = mock.after("dark")
FONTS = ROOT / "apps" / "web" / "public" / "fonts"

# Two outline icons for the notices, 24 px grid, 2 px round strokes (brand section 4). Neither is a warning shape
# (no triangle, no shield, no bell) and neither is the experiment's flask.
mock.I.update({
    "new": "M5 21V4m0 0h11l-2 4 2 4H5",                                   # a start flag: something began
    "cal": "M7 3v3m10-3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",  # a day
    "x": "M6 6l12 12M18 6 6 18",
})
ico, CHEV = mock.ico, mock.CHEV

CSS = mock.CSS + """
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
/* the notice row: neutral, quieter than a status row (48 px, card surface, hairline edge), one line */
.nt{display:flex;align-items:center;gap:10px;min-height:48px;margin:8px 16px 0;padding:8px 12px 8px 14px;border-radius:16px;
 background:var(--surface);border:1px solid var(--rule);font-size:15px;font-weight:500;color:var(--ink)}
.nt .ni{flex:none;color:var(--slate)}
.nt .g{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nt .k{color:var(--slate);font-weight:500}
.nt.more{color:var(--slate)}
.err{margin:8px 16px 0;font-size:13px;color:var(--slate);display:flex;gap:8px;align-items:center;min-height:44px}
.err b{color:var(--sea-ink);font-weight:600}
/* the notice sheet */
.sheet h3{margin:4px 0 12px;font-size:20px;font-weight:600;letter-spacing:-.01em}
.sheet .kick{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--slate);letter-spacing:.02em}
.sheet p{font-size:15px;line-height:1.5;margin:0 0 12px;color:var(--ink)}
.sheet p.m{color:var(--slate)}
.when{display:grid;grid-template-columns:1fr auto;gap:10px 16px;font-size:15px;padding:14px 0;border-top:1px solid var(--rule);margin-top:4px}
.when span:nth-child(odd){color:var(--slate)}.when span:nth-child(even){font-weight:600;text-align:right}
.wk{font-size:13px;font-weight:600;color:var(--stale);text-transform:uppercase;letter-spacing:.06em;margin:8px 0 6px}
.wl{list-style:none;margin:0 0 8px;padding:0;font-size:15px}.wl li{display:flex;gap:12px;padding:6px 0}.wl li span:first-child{width:96px;flex:none;color:var(--slate)}
.only{font-size:13px;color:var(--slate);border-top:1px solid var(--rule);padding-top:12px;margin-top:8px}
.closebtn{display:flex;align-items:center;justify-content:center;height:48px;border-radius:999px;background:var(--surface-2);border:1px solid var(--control);font-weight:600;font-size:16px;margin-top:16px}
/* boards */
.board{width:390px;background:var(--paper);padding:0 0 24px}
.state{margin:12px 16px 0;padding:14px 0 14px;border-radius:24px;background:#0E1013;border:1px solid var(--rule)}
.state > small{display:block;margin:0 16px 4px;font-size:13px;font-weight:600;color:var(--stale);text-transform:uppercase;letter-spacing:.06em}
.state > p{margin:8px 16px 0;font-size:13px;color:var(--slate);line-height:1.45}
.state .nt,.state .err{margin-left:4px;margin-right:4px}
.ghost{margin:8px 12px 0;min-height:48px;border-radius:16px;border:1px dashed var(--rule-strong);display:grid;place-items:center;font-size:13px;color:var(--stale)}
@media (min-width:700px){.dmain .nt{margin:0 0 8px}.dmain .nt .k{display:inline}}
"""


def font_face() -> str:
    out = []
    for w in (400, 500, 600, 700):
        f = FONTS / f"outfit-latin-{w}-normal.woff2"
        if f.exists():
            b = base64.b64encode(f.read_bytes()).decode()
            out.append(f'@font-face{{font-family:"Outfit";font-weight:{w};src:url(data:font/woff2;base64,{b}) format("woff2")}}')
    return "".join(out)


def notice(kind: str, text: str, label: str, desk=False) -> str:
    """One notice row. The kind ('New on the market', 'Big day') is in the accessible name always, and visible on
    desktop; on a phone the icon carries it and the line keeps the facts."""
    icon = "new" if kind == "new" else "cal"
    k = f'<span class="k">{label} · </span>' if desk else f'<span class="sr">{label}: </span>'
    return f'<div class="nt">{ico(icon, 20, 2, "ni")}<span class="g">{k}{text}</span>{CHEV}</div>'


ANT = ("new", "Anthropic (ANT) started trading today", "New on the market")
FED = ("cal", "Fed decision at 20:00 your time", "Big day")
CPI = ("cal", "Inflation report at 14:30 your time", "Big day")


def phone(body: str, tab="home", over: str = "") -> str:
    return f'<div class="phone" data-v="after" style="{style()}">{body}{mock.tabs(tab)}{over}</div>'


def style() -> str:
    return "".join(f"--{k}:{v};" for k, v in T.items()) + "background:var(--paper);color:var(--ink);font-family:var(--font);"


def home_rows(rows: str) -> str:
    holdings = "".join(mock.prow(*p, show_sub=False) for p in mock.POS[:3])
    return (mock.header("after", T) + mock.EXP + rows + mock.hero() + mock.chart(T, v="after")
            + '<div class="rg"><b>1W</b><b>1M</b><b>3M</b><b class="on">All</b></div>'
            + f'<div class="sec"><h2>Holding 4 of 5</h2><a>{ico("chev", 20)}</a></div><div class="list">{holdings}</div>')


def home_listing() -> str:
    return phone(home_rows(notice(*ANT)))


def home_bigday() -> str:
    return phone(home_rows(notice(*FED)))


def sheet(body: str, row=ANT) -> str:
    return phone(home_rows(notice(*row)), over='<div class="scrim"></div>'
                 f'<div class="sheet" role="dialog" aria-label="notice"><div class="grab"></div>{body}'
                 '<div class="closebtn">Close</div></div>')


def sheet_listing() -> str:
    return sheet(
        f'<div class="kick">{ico("new", 18, 2)}New on the market</div>'
        '<h3>Anthropic (ANT) started trading today</h3>'
        '<p>kaibo does not buy brand-new listings: they have no price history to judge, and on average they have done '
        'worse than the market in their first years.</p>'
        '<p>Its rules could first look at ANT in about 12 months.</p>'
        '<div class="when"><span>First day of trading</span><span>Wed, Nov 19</span>'
        '<span>Trend momentum, on paper</span><span>Nov 19, 2027 at the earliest</span>'
        '<span>Your own ticker list</span><span>Feb 17, 2027</span></div>'
        '<p class="m">The earliest dates, not a forecast. Trend momentum looks only at S&amp;P 500 companies, and a new '
        'company can join the index after 12 months of trading at the earliest.</p>'
        '<div class="wk">Also this week</div><ul class="wl"><li><span>Tue, Nov 24</span><span>Example Bio (EXBI) is expected to start trading</span></li></ul>'
        '<p class="only">A notice only informs. kaibo changes nothing because of it.</p>')


def sheet_bigday() -> str:
    body = (f'<div class="kick">{ico("cal", 18, 2)}Big day</div>'
            '<h3>The Fed announces its decision at 20:00 your time</h3>'
            '<p>kaibo is not changing anything today; it is recording what happens on days like this.</p>'
            '<p class="m">Days like this: the Fed’s interest-rate decision, about 8 a year. Prices can move more than usual '
            'around 20:00.</p>'
            '<div class="when"><span>When</span><span>Wed, Dec 9 · 20:00 your time</span>'
            '<span>In New York</span><span>14:00 ET</span></div>'
            '<div class="wk">Coming up</div><ul class="wl"><li><span>Thu, Dec 10</span><span>US inflation report at 14:30</span></li>'
            '<li><span>Fri, Dec 11</span><span>Index change at the close (Russell)</span></li></ul>'
            '<p class="only">A notice only informs. kaibo changes nothing because of it.</p>')
    return sheet(body, FED)


def states() -> str:
    def card(title, inner, note):
        return f'<div class="state"><small>{title}</small>{inner}<p>{note}</p></div>'
    body = ('<div class="ph" style="padding-top:20px"><h2>Notice states</h2><p>Home, under the status rows, above the pot.</p></div>'
            + card("1 · New on the market (today)", notice(*ANT),
                   "A very large listing, on its first day. Same row for the next four sessions: “started trading on Wed, Nov 19”.")
            + card("2 · Big day (today)", notice(*FED), "Only on its day. The time is the viewer’s own, from the browser.")
            + card("3 · Tomorrow", notice("cal", "Tomorrow: inflation report, 14:30", "Big day"),
                   "Only a major notice shows the day before. A smaller one waits for its day.")
            + card("4 · More than two", notice(*FED) + notice("cal", "Inflation report at 14:30 your time", "Big day")
                   + f'<div class="nt more">{ico("cal", 20, 2, "ni")}<span class="g">1 more market notice</span>{CHEV}</div>',
                   "Two rows at most, then one quiet row that opens the list.")
            + card("5 · Loading, nothing today, not set up yet", '<div class="ghost">no row, no placeholder</div>',
                   "Most days have no notice, so a placeholder would only flash and vanish. Before migration 0034 is applied the "
                   "dashboard shows nothing, as on a day without notices.")
            + card("6 · Could not load", '<div class="err">Market notices could not load. <b>Try again</b></div>',
                   "One muted line with a text button. No colour of meaning: it is not a warning about the account.")
            )
    return f'<div class="board" data-v="after" style="{style()}">{body}</div>'


def desktop() -> str:
    nav = "".join(f'<a class="{"on" if n == "Home" else ""}">{n}</a>' for n in ["Home", "Positions", "Activity", "Engine", "Report", "Go / no-go", "Strategy", "Run & settings"])
    rows = "".join(mock.prow(*p) for p in mock.POS)
    ant = notice(*ANT, desk=True)
    body = (f'<div class="dtop">{mock.brand("after", T)}<div class="dnav">{nav}</div><span class="pill">PAPER · BALANCED</span>'
            '<span class="chip"><i class="dot"></i><b>Running</b> kai-macbook · 12 s ago</span></div>'
            f'<div class="dmain"><div>{mock.EXP}{ant}<div class="dcard" style="margin-top:12px">{mock.hero()}{mock.chart(T, 720, 250, "after")}'
            '<div class="rg"><b>1W</b><b>1M</b><b>3M</b><b class="on">All</b></div></div></div>'
            f'<div><div class="dcard"><h2>Holding 4 of 5</h2><div class="list">{rows}</div></div></div></div>')
    return f'<div class="desk" data-v="after" style="{style()}">{body}</div>'


PAGES = {"home-listing": (home_listing, 390, 844), "home-bigday": (home_bigday, 390, 844), "sheet-listing": (sheet_listing, 390, 844),
         "sheet-bigday": (sheet_bigday, 390, 844), "states": (states, 390, 0), "desktop": (desktop, 1280, 800)}


def page(fn) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>kaibo mockup · market notices</title><style>{font_face() if SHOT else ""}{CSS}body{{background:#050607;margin:0}}</style></head>'
            f'<body>{fn()}</body></html>\n')


SHOT = "--shot" in sys.argv

if __name__ == "__main__":
    for name, (fn, _, _) in PAGES.items():
        # the committed HTML does not embed the font (it would add 220 kB per file); the PNGs are drawn with it
        (HERE / f"{name}.html").write_text(page(fn).replace(font_face(), "") if SHOT else page(fn))
    if SHOT:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for name, (fn, w, h) in PAGES.items():
                mob = w < 500
                c = b.new_context(viewport={"width": w, "height": h or 844}, device_scale_factor=2 if mob else 1)
                p = c.new_page()
                p.set_content(page(fn))
                p.wait_for_timeout(200)
                over = p.evaluate("document.documentElement.scrollWidth > innerWidth")
                p.screenshot(path=str(HERE / f"{name}.png"), full_page=not h)
                print(name, "overflow" if over else "fits")
                c.close()
            b.close()
