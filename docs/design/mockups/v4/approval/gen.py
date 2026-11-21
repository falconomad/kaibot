"""Large transaction approval (#487, M13 ticket 4.8). Static mockups in the brand v1 look. Design only.

    python docs/design/mockups/v4/approval/gen.py           # writes the HTML files next to this one
    python docs/design/mockups/v4/approval/gen.py --shot    # and the PNGs (Playwright + Chromium)

Reuses the brand system's mockup kit (docs/design/brand/gen/mock.py: tokens, header, hero, chart, rows, tab bar,
sheet) and embeds Outfit from apps/web/public/fonts at render time. The pot and holdings are the kit's invented
numbers; the order (buy 22 NVDA, about $3,254, 32% of a $10,107 pot) is invented to sit above a 25% line. Times are
shown as a viewer in Berlin sees them (Fri 4 Dec 2026: 15:45 in New York is 21:45 in Berlin). Nothing here is built;
the note is docs/design/m13_large_transaction_approval.md.
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

# One outline icon for "waits for you", 24 px grid, 2 px round strokes (brand section 4): a clock. Not a warning
# shape (no triangle, no bell) and not the experiment's flask: an approval is a question, not an alarm.
mock.I.update({"clock": "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "x": "M6 6l12 12M18 6 6 18",
               "lock": "M7 11V8a5 5 0 0 1 10 0v3M6 11h12a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"})
ico, CHEV = mock.ico, mock.CHEV

CSS = mock.CSS + """
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
/* the "waits for you" row: the brand's blue, because it asks for an action; never red or amber (nothing is wrong) */
.st.ask{background:var(--sea-tint);border-color:var(--sea-line);color:var(--ink)}
.st.ask .ai{flex:none;color:var(--sea-ink)}
/* the approval sheet */
.sheet.ap{padding-bottom:20px}
.ap .kick{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--sea-ink);letter-spacing:.02em}
.ap h3{margin:6px 0 4px;font-size:22px;font-weight:700;letter-spacing:-.02em}
.ap .sub{font-size:15px;color:var(--slate);margin:0 0 12px}
.ap p{font-size:15px;line-height:1.5;margin:0 0 10px}
.ap p.m{color:var(--slate);font-size:14px}
.share{margin:6px 0 14px}.share .bar{height:10px;border-radius:10px;background:var(--surface-2);position:relative;overflow:hidden}
.share .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:10px;background:var(--sea)}
.share .bar u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink)}
.share .cap{display:flex;justify-content:space-between;font-size:13px;color:var(--slate);margin-top:6px}
.share .cap b{color:var(--ink);font-weight:600}
.ap .kv{margin-top:4px;padding:12px 0;gap:10px 16px}
.due{display:flex;gap:10px;align-items:flex-start;padding:12px 14px;border-radius:16px;background:var(--surface-2);font-size:14px;line-height:1.45}
.due svg{flex:none;margin-top:1px;color:var(--slate)}.due b{display:block;font-size:15px}
.two{display:flex;gap:12px;margin-top:14px}.two .btn{margin-top:0;flex:1}
.fine{font-size:13px;color:var(--slate);margin:12px 0 0;line-height:1.45}
/* lock screen */
.lockscr{background:linear-gradient(180deg,#10131A,#07080A);display:flex;flex-direction:column;align-items:center;padding-top:76px}
.lockscr .lk{color:var(--slate)}.lockscr .tm{font-size:84px;font-weight:600;letter-spacing:-.04em;line-height:1;margin-top:8px}
.lockscr .dt{font-size:18px;color:var(--slate);margin-top:6px}
.push{position:absolute;left:12px;right:12px;bottom:150px;padding:14px 16px;border-radius:22px;background:rgba(38,42,49,.92);border:1px solid var(--rule-strong)}
.push .top{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--slate)}.push .top .app{width:22px;height:22px;border-radius:6px;background:#14171C;display:grid;place-items:center}
.push .top .ago{margin-left:auto}
.push b{display:block;font-size:16px;font-weight:600;margin-top:8px}.push span.t{display:block;font-size:15px;line-height:1.4;margin-top:2px}
.lockscr .hint{position:absolute;bottom:60px;left:0;right:0;text-align:center;font-size:13px;color:var(--stale)}
/* boards */
.board{width:390px;background:var(--paper);padding:0 0 24px}
.state{margin:12px 16px 0;padding:14px 0 14px;border-radius:24px;background:#0E1013;border:1px solid var(--rule)}
.state > small{display:block;margin:0 16px 4px;font-size:13px;font-weight:600;color:var(--stale);text-transform:uppercase;letter-spacing:.06em}
.state > p{margin:8px 16px 0;font-size:13px;color:var(--slate);line-height:1.45}
.state .st{margin-left:8px;margin-right:8px}.state .list{margin:6px 8px 0;border-radius:18px}
.state .push{position:static;margin:6px 8px 0}
.done{display:flex;gap:12px;align-items:flex-start;margin:6px 8px 0;padding:14px;border-radius:18px;background:var(--surface);border:1px solid var(--rule);font-size:15px;line-height:1.45}
.done .r{flex:none;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;background:var(--surface-2);color:var(--slate)}
.done .r.ok{background:var(--gain-tint);color:var(--gain)}.done b{display:block}.done span{color:var(--slate);font-size:14px}
"""

ORDER = {"sym": "NVDA", "name": "NVIDIA", "side": "Buy", "qty": 22, "value": "$3,254", "pct": 32, "line": 25, "rule": "Trend momentum",
         "due": "21:45 your time", "due_ny": "15:45 in New York", "left": "in 3 h 12 min"}


def font_face() -> str:
    out = []
    for w in (400, 500, 600, 700):
        f = FONTS / f"outfit-latin-{w}-normal.woff2"
        if f.exists():
            b = base64.b64encode(f.read_bytes()).decode()
            out.append(f'@font-face{{font-family:"Outfit";font-weight:{w};src:url(data:font/woff2;base64,{b}) format("woff2")}}')
    return "".join(out)


def style() -> str:
    return "".join(f"--{k}:{v};" for k, v in T.items()) + "background:var(--paper);color:var(--ink);font-family:var(--font);"


def phone(body: str, tab="home", over: str = "", cls="") -> str:
    return f'<div class="phone {cls}" data-v="after" style="{style()}">{body}{mock.tabs(tab) if tab else ""}{over}</div>'


def ask_row(text="A large order waits for your approval", small=f'Answer by {ORDER["due"]}, or it is not sent') -> str:
    return f'<div class="st ask">{ico("clock", 20, 2, "ai")}<span class="g">{text}<small>{small}</small></span>{CHEV}</div>'


def home_rows(rows: str) -> str:
    holdings = "".join(mock.prow(*p, show_sub=False) for p in mock.POS[:3])
    return (mock.header("after", T) + rows + mock.EXP + mock.hero() + mock.chart(T, v="after")
            + '<div class="rg"><b>1W</b><b>1M</b><b>3M</b><b class="on">All</b></div>'
            + f'<div class="sec"><h2>Holding 4 of 5</h2><a>{ico("chev", 20)}</a></div><div class="list">{holdings}</div>')


def push(title: str, text: str, ago="now") -> str:
    mark = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4F95F0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4v16M7 13l9-8M10 11l7 9"/></svg>'
    return (f'<div class="push"><div class="top"><span class="app">{mark}</span>kaibo<span class="ago">{ago}</span></div>'
            f'<b>{title}</b><span class="t">{text}</span></div>')


def lock() -> str:
    body = (f'{ico("lock", 22, 2, "lk")}<div class="tm">18:33</div><div class="dt">Friday, December 4</div>'
            + push("kaibo needs your approval", f'A large order is waiting. Open kaibo to approve or decline by {ORDER["due"].replace(" your time", "")}.')
            + '<div class="hint">No amount and no stock name on the lock screen</div>')
    return phone(body, tab="", cls="lockscr")


def home_waiting() -> str:
    return phone(home_rows(ask_row()))


def share_bar() -> str:
    o = ORDER
    return (f'<div class="share"><div class="bar"><i style="width:{o["pct"]}%"></i><u style="left:{o["line"]}%"></u></div>'
            f'<div class="cap"><span><b>{o["pct"]}% of your pot</b> in one order</span><span>your line: {o["line"]}%</span></div></div>')


def approve_body(confirm=False) -> str:
    o = ORDER
    head = (f'<div class="kick">{ico("clock", 18, 2)}Waits for your approval</div>'
            f'<h3>{o["side"]} {o["qty"]} {o["sym"]}, about {o["value"]}</h3>'
            f'<p class="sub">{o["name"]} · {o["rule"]} · on paper</p>')
    if confirm:
        return (head + '<p><b>Approve this order?</b></p>'
                f'<p>kaibo will send it at today’s close (22:00 your time): buy about {o["qty"]} shares of {o["sym"]} for about {o["value"]}, '
                f'{o["pct"]}% of your pot. The exact number of shares is set from the price at that moment.</p>'
                '<p class="m">You can still take the approval back until 21:45.</p>'
                '<div class="two"><div class="btn ghost">Back</div><div class="btn">Yes, approve</div></div>')
    return (head + share_bar()
            + '<p>This one order is larger than the share of the pot you let kaibo move without asking, so it waits for you.</p>'
            '<div class="kv"><span>Why kaibo wants it</span><span>Steady uptrend, ranked 2 of 40</span>'
            '<span>Paid for with</span><span>Cash, $4,128 free</span>'
            '<span>After it</span><span>NVDA would be 52% of the pot</span></div>'
            f'<div class="due">{ico("clock", 20, 2)}<span><b>Answer by {o["due"]} ({o["left"]})</b>{o["due_ny"]}. With no answer the order is not sent, and nothing else changes.</span></div>'
            '<div class="two"><div class="btn ghost">Decline</div><div class="btn">Approve</div></div>'
            '<p class="fine">Decline: kaibo does not send it today. Its rules decide again after the close and may ask again. '
            'Sales that protect the pot never wait for you.</p>')


def sheet(body: str) -> str:
    return phone(home_rows(ask_row()), over='<div class="scrim"></div>'
                 f'<div class="sheet ap" role="dialog" aria-label="approval"><div class="grab"></div>{body}</div>')


def approve() -> str:
    return sheet(approve_body())


def confirm() -> str:
    return sheet(approve_body(confirm=True))


def states() -> str:
    def card(title, inner, note):
        return f'<div class="state"><small>{title}</small>{inner}<p>{note}</p></div>'

    def done(icon, ok, title, text):
        return f'<div class="done"><span class="r {"ok" if ok else ""}">{ico(icon, 20)}</span><div><b>{title}</b><span>{text}</span></div></div>'
    act = lambda *rows: '<div class="list">' + "".join(rows) + "</div>"  # noqa: E731
    body = ('<div class="ph" style="padding-top:20px"><h2>Approval states</h2><p>What the owner sees after each outcome, and in Activity.</p></div>'
            + card("1 · Approved", done("check", True, "Approved", "kaibo sends it at today’s close, 22:00 your time. You can take this back until 21:45.")
                   + act(mock.arow("check", "u", "You approved: buy 22 NVDA", "About $3,254 · 18:41")),
                   "The row on Home goes. The order is sent once, in the normal window; the fill appears as any other.")
            + card("2 · Declined", done("x", False, "Declined", "Not sent today. kaibo’s rules decide again after the close.")
                   + act(mock.arow("skip", "", "You declined: buy 22 NVDA", "Not sent · 18:41")),
                   "Declining is not an error: no red. An optional one-line reason is kept with the record.")
            + card("3 · No answer in time", done("clock", False, "Not sent: no answer by 21:45", "Nothing was bought. kaibo’s rules decide again after the close.")
                   + act(mock.arow("skip", "", "Expired: buy 22 NVDA", "No answer by 21:45 · not sent")),
                   "Fail closed. One notification says it expired; no reminder after that.")
            + card("4 · The order changed while it waited", ask_row("The order changed: look again", "Now buy 19 NVDA, about $2,810 · answer by 21:45"),
                   "An approval is for the numbers on the screen. If the plan changes by more than a small band, the old approval is void and kaibo asks again.")
            + card("5 · More than one waits", ask_row("2 large orders wait for your approval", "The first is due by 21:45 your time"),
                   "One row, one list; each order is approved or declined on its own.")
            + card("6 · A sale that protects the pot", push("kaibo sold a stock to protect the pot", "It was a large sale. No approval was needed. Open kaibo to see it.", "2 min ago"),
                   "A protective exit required by a risk rule is sent at once and the owner is told. It never waits.")
            + card("7 · Too late, or already answered", done("clock", False, "This order no longer waits", "It was answered on another device at 18:41, or its time passed. Nothing to do."),
                   "Opening an old notification lands here, never on live buttons.")
            + card("8 · Offline, or the engine is not running", done("clock", False, "kaibo can’t take your answer now", "You seem to be offline. The order still waits until 21:45; try again."),
                   "The buttons are off, with the reason next to them. An answer is recorded only when the server confirms it."))
    return f'<div class="board" data-v="after" style="{style()}">{body}</div>'


PAGES = {"lock": (lock, 390, 844), "home-waiting": (home_waiting, 390, 844), "approve": (approve, 390, 844), "confirm": (confirm, 390, 844),
         "states": (states, 390, 0)}


def page(fn) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>kaibo mockup · large transaction approval</title><style>{font_face() if SHOT else ""}{CSS}body{{background:#050607;margin:0}}</style></head>'
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
                c = b.new_context(viewport={"width": w, "height": h or 844}, device_scale_factor=2)
                p = c.new_page()
                p.set_content(page(fn))
                p.wait_for_timeout(200)
                over = p.evaluate("document.documentElement.scrollWidth > innerWidth")
                tall = p.evaluate("(() => { const s = document.querySelector('.sheet'); return s ? s.getBoundingClientRect().top < 40 : false; })()")
                p.screenshot(path=str(HERE / f"{name}.png"), full_page=not h)
                print(name, "overflow" if over else "fits", "SHEET TOO TALL" if tall else "")
                c.close()
            b.close()
