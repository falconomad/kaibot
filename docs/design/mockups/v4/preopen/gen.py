"""Pre-open agent review in the v4 language (#215). Writes static, self-contained HTML mockups next to this file.

    python docs/design/mockups/v4/preopen/gen.py

Reuses the v4 tokens, icons and rows from ../gen.py. Invented sample data: the plan for Tue 17 Nov 2026 of the
Trend momentum rule on paper. Share counts only: the contract carries shares, not prices (see m7_preopen_review.md).
"""
from __future__ import annotations

import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v4gen", HERE.parent / "gen.py")
v4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v4)
ico, badge, CHEV, top, tabs = v4.ico, v4.badge, v4.CHEV, v4.top, v4.tabs
v4.BADGE.update({"WMT": "#23406B", "KO": "#3F3A6B", "COST": "#1E5566", "NVDA": "#1F5A3A", "AVGO": "#2A2F38"})
v4.I.update({
    "agent": "M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8zM18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z",
    "half": "M12 4a8 8 0 1 0 0 16zM12 4a8 8 0 1 1 0 16",
    "keep": "m5 12 5 5 9-10",
    "clock": "M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18",
    "plus": "M12 5v14M5 12h14",
    "minus": "M5 12h14",
    "off": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8 12h8",
    "coin": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M9 9.5c0-1.4 1.3-2 3-2s3 .6 3 2-1.3 1.8-3 2.3-3 .9-3 2.4 1.3 2 3 2 3-.6 3-2M12 5.5v2m0 9v2",
})

CSS = v4.CSS + """
:root{--agent:#C9B6FF;--agent-tint:#1E1A2B;--agent-line:#3A3155}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.ico.agent{background:var(--agent-tint);color:var(--agent)}
.tag.agent{background:var(--agent-tint);color:var(--agent)}
.tag.keep{background:var(--surface-2);color:var(--text-2)}
.status.agent{background:var(--agent-tint);border-color:var(--agent-line)}
.status.ended small,.status.agent small,.status.breach small{color:var(--text-2)}
.status.ended .dot{background:var(--amber);animation:none}
.plan-h{display:flex;align-items:baseline;justify-content:space-between;gap:var(--s3);padding:var(--s4) var(--s4) var(--s1)}
.plan-h h2{font-size:var(--t-md);font-weight:600}
.plan-h span{font-size:var(--t-cap);color:var(--text-2)}
.caption{margin:var(--s2) var(--s4) 0;font-size:var(--t-cap);color:var(--text-2);line-height:1.45}
.row .end .tag{margin-top:4px}
.was{color:var(--text-3);text-decoration:line-through;font-weight:400;margin-right:4px}
.label-box{display:flex;gap:var(--s3);align-items:flex-start;padding:var(--s3) var(--s4);border-radius:var(--r-lg);
 background:var(--agent-tint);border:1px solid var(--agent-line);font-size:var(--t-sm);margin-top:var(--s4)}
.label-box svg{flex:none;color:var(--agent);margin-top:2px}
.quote{margin-top:var(--s4);padding:var(--s3) var(--s4);border-left:3px solid var(--agent);background:var(--surface-2);border-radius:0 var(--r-md) var(--r-md) 0;font-size:var(--t-sm)}
.quote small{display:block;color:var(--text-2);font-size:var(--t-cap);margin-bottom:4px}
.arms{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:var(--s2);margin-top:var(--s4);padding:var(--s3) var(--s4);border-radius:var(--r-lg);background:var(--surface-2)}
.arms div{display:flex;flex-direction:column}.arms div:last-child{text-align:right}
.arms small{font-size:var(--t-cap);color:var(--text-2)}.arms b{font-size:var(--t-lg)}
.arms svg{color:var(--text-3)}
.sheet h3{margin:var(--s3) 0 0;font-size:var(--t-lg);font-weight:600}
.state-card{margin:var(--s3) var(--s4) 0;padding:var(--s4);border-radius:var(--r-xl);background:var(--surface);border:1px solid var(--line)}
.state-card > small{display:block;font-size:var(--t-cap);font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:var(--s3)}
.state-card .status{margin:0;width:100%}
.state-card .list{margin:var(--s3) 0 0}
.state-card p{font-size:var(--t-sm);color:var(--text-2);margin-top:var(--s3)}
/* desktop */
body.desk{width:1440px;min-height:900px;padding-bottom:0}
.desk .dtop{display:flex;align-items:center;gap:var(--s5);height:64px;padding:0 var(--s8);border-bottom:1px solid var(--line)}
.desk .dtop h1{font-size:var(--t-lg);font-weight:700;letter-spacing:-.02em}
.desk .dnav{display:flex;gap:var(--s1);flex:1;margin-left:var(--s4)}
.desk .dnav a{min-height:44px;display:inline-flex;align-items:center;padding:0 var(--s4);border-radius:var(--r-pill);color:var(--text-2);text-decoration:none;font-size:var(--t-md);font-weight:500}
.desk .dnav a[aria-current=page]{background:var(--surface-2);color:var(--text)}
.desk main{max-width:1320px;margin:0 auto;padding:var(--s6) var(--s8);display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.35fr);gap:var(--s6);align-items:start}
.desk .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-xl);padding:var(--s6)}
.desk .card h2{font-size:var(--t-lg);font-weight:600}
.desk .card .sub{font-size:var(--t-sm);color:var(--text-2);margin-top:2px}
.desk .list{margin:var(--s4) 0 0}
.desk table{width:100%;border-collapse:collapse;margin-top:var(--s4);font-size:var(--t-sm)}
.desk th{text-align:left;font-size:var(--t-cap);font-weight:600;color:var(--text-3);padding:var(--s2) var(--s3);border-bottom:1px solid var(--line)}
.desk td{padding:var(--s3);border-bottom:1px solid var(--line);vertical-align:middle}
.desk tr:last-child td{border-bottom:0}
.desk tr.sel td{background:var(--surface-2)}
.desk td.r,.desk th.r{text-align:right}
.desk .stock{display:flex;align-items:center;gap:var(--s3)}
.desk .stock .badge{width:40px;height:40px}
.tag{white-space:nowrap}
.desk .stock div b{display:block}.desk .stock div span{font-size:var(--t-cap);color:var(--text-2)}
.desk .why{color:var(--text-2);max-width:340px}
.desk .side{position:sticky;top:var(--s6)}
.desk .page-title{grid-column:1/-1;display:flex;align-items:flex-end;justify-content:space-between}
.desk .page-title h2{font-size:var(--t-xl);font-weight:700;letter-spacing:-.03em}
.desk .open-btn{min-height:44px;min-width:44px;padding:0 var(--s3);border:0;border-radius:var(--r-md);background:transparent;color:var(--accent-text);font:600 var(--t-sm) inherit;font-family:inherit}
"""

AGENT_LINE = ("The agent review can only cancel or halve a buy. It never sells, and it never sets a price, a size, "
              "a stop or an exit.")
SESSION, SESSION_SHORT = "Tue, Nov 17", "Tue 17 Nov"

# (symbol, company, kind, current, rules target, reviewed, verdict, flag, confidence, reason)
PLAN = [
    ("WMT", "Walmart", "new", 0, 12, 12, "allow", "none", "high", "Nothing found since the close."),
    ("NVDA", "NVIDIA", "add", 14, 16, 16, "allow", "none", "medium", "No new filings or company news since the close."),
    ("COST", "Costco", "new", 0, 6, 3, "downgrade", "earnings_soon", "high", "Reports earnings in 2 days, inside the first holding days."),
    ("KO", "Coca-Cola", "new", 0, 5, 0, "veto", "company_news", "medium", "A share offering was announced after the close; its size and price are not known yet."),
]
SALES = [("AVGO", "Broadcom", 4, 0)]
FLAG = {"none": "Nothing found", "company_news": "Company news", "earnings_soon": "Earnings soon",
        "corporate_action": "Corporate action", "data_issue": "Data looks wrong", "unknown": "Not sure"}
WORD = {"allow": "Kept", "downgrade": "Halved", "veto": "Cancelled"}


def page(title: str, body: str, active: str = "pos", cls: str = "") -> str:
    tb = tabs(active) if cls not in ("desk", "board") else ""  # the states board is a sheet of states, not a screen
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Kaibot v4 mockup · pre-open review · {title}</title><style>{CSS}</style></head>'
            f'<body class="{cls}">{body}{tb}</body></html>\n')


def order_text(kind, cur, tgt) -> str:
    n = tgt - cur
    return f"Buy {n}" if kind == "new" else f"Add {n}"


def plan_row(sym, company, kind, cur, tgt, rev, verdict, *_rest, review_on=True) -> str:
    if not review_on or verdict == "allow":
        head = f"{order_text(kind, cur, tgt)} {sym}"
        icon, tone = "plus", ""
    elif verdict == "downgrade":
        head = f'<span class="was">{order_text(kind, cur, tgt)}</span>{order_text(kind, cur, rev)} {sym}'
        icon, tone = "half", "agent"
    else:
        head = f'<span class="was">{order_text(kind, cur, tgt)}</span>No buy · {sym}'
        icon, tone = "skip", "agent"
    sub = company + (f" · holding {cur}" if kind == "add" else "")
    tag = "" if not review_on else f'<span class="tag {"keep" if verdict == "allow" else "agent"}">{WORD[verdict]}</span>'
    return (f'<button class="row"><span class="ico {tone}">{ico(icon, 20)}</span><span class="main"><b>{head}</b>'
            f'<span class="sub">{sub}</span></span><span class="end">{tag}</span>{CHEV}</button>')


def sale_row(sym, company, cur, tgt) -> str:
    return (f'<button class="row"><span class="ico">{ico("minus", 20)}</span><span class="main"><b>Sell {cur - tgt} {sym}</b>'
            f'<span class="sub">{company} · left the top fifth</span></span><span class="end"><span class="tag keep">Not reviewed</span></span>{CHEV}</button>')


def plan_list(review_on=True, blocked=False) -> str:
    rows = []
    for p in PLAN:
        if blocked:
            sym, company, kind, cur, tgt = p[:5]
            rows.append(f'<button class="row"><span class="ico ended" style="background:var(--amber-tint);color:var(--amber)">{ico("skip", 20)}</span>'
                        f'<span class="main"><b><span class="was">{order_text(kind, cur, tgt)}</span>No buy · {sym}</b>'
                        f'<span class="sub">{company} · held back: no review</span></span>{CHEV}</button>')
        else:
            rows.append(plan_row(*p, review_on=review_on))
    rows += [sale_row(*s) for s in SALES]
    return '<div class="list">' + "".join(rows) + "</div>"


def review_status(state: str) -> str:
    """The one row above the plan that says what the review did. Off is a quiet caption, not a row."""
    if state == "done":
        return (f'<button class="status agent"><span class="ico agent" style="width:32px;height:32px">{ico("agent", 18)}</span>'
                f'<span class="grow">Review: 1 cancelled, 1 halved, 2 kept<small>Agent checked news and earnings, 08:41 ET</small></span>{CHEV}</button>')
    if state == "pending":
        return (f'<button class="status" style="background:var(--surface);border-color:var(--line)"><span class="ico" style="width:32px;height:32px">{ico("clock", 18)}</span>'
                f'<span class="grow">Agent review runs before 09:00 ET<small>4 buys wait for it. Sales go ahead.</small></span>{CHEV}</button>')
    if state == "failed":
        return (f'<button class="status ended"><span class="dot"></span>'
                f'<span class="grow">No new buys on {SESSION}<small>The agent review didn\'t answer in time</small></span>{CHEV}</button>')
    if state == "budget":
        return (f'<button class="status ended"><span class="dot"></span>'
                f'<span class="grow">No new buys on {SESSION}<small>Today\'s AI budget is used up</small></span>{CHEV}</button>')
    if state == "monthly":
        return (f'<button class="status breach"><span class="dot red"></span>'
                f'<span class="grow">No new buys: monthly AI budget reached<small>Holds and sales go ahead</small></span>{CHEV}</button>')
    return ""


def plan_header(state: str) -> str:
    return (f'<div class="plan-h"><h2>Plan for {SESSION}</h2><span>Trend momentum · orders at 09:00 ET</span></div>'
            + (f'<div style="margin-top:var(--s2)">{review_status(state)}</div>' if state != "off" else ""))


def positions_plan() -> str:
    body = (top() + '<div class="page-h"><h2>Positions</h2><p class="num">9 held · next orders at 09:00 ET</p></div>'
            + '<div class="chips" role="group" aria-label="Show"><button>Open</button><button aria-pressed="true">Plan</button><button>Closed</button></div>'
            + plan_header("done") + f'<div class="sect" style="padding-top:var(--s3)"></div>' + plan_list()
            + f'<p class="caption">{AGENT_LINE}</p>')
    return page("Positions plan", body)


def activity() -> str:
    rows = (f'<button class="row"><span class="ico agent">{ico("skip", 20)}</span><span class="main"><b>Review cancelled KO</b>'
            f'<span class="sub">Company news since the close · 08:41</span></span><span class="end num"><b class="dim" style="font-weight:600">5 sh</b></span>{CHEV}</button>'
            f'<button class="row"><span class="ico agent">{ico("half", 20)}</span><span class="main"><b>Review halved COST</b>'
            f'<span class="sub">Earnings in 2 days · 08:41</span></span><span class="end num"><b class="dim" style="font-weight:600">6 → 3 sh</b></span>{CHEV}</button>'
            + v4.simple_row("sell", "down", "Sold AVGO", "Fell out of the top fifth · 09:30", "−$17.40", "down")
            + v4.simple_row("buy", "up", "Bought 12 WMT", "Trend momentum · 09:30", "$1,189.20"))
    yday = (v4.simple_row("sell", "up", "Sold JPM", "Target reached · 15:12", "+$38.20", "up")
            + v4.simple_row("buy", "up", "Bought 4 AVGO", "Trend momentum · 10:41", "$331.40"))
    body = (top() + '<div class="page-h"><h2>Activity</h2><p>What Kaibot did, and what it passed on.</p></div>'
            + '<div class="chips" role="group" aria-label="Show"><button aria-pressed="true">All</button><button>Buys &amp; sells</button><button>Skipped</button><button>Agent room</button></div>'
            + f'<div class="day">Today</div><div class="list">{rows}</div><div class="day">Monday, Nov 16</div><div class="list">{yday}</div>')
    return page("Activity", body, "act")


def sheet() -> str:
    base = activity().split("<body")[1].split(">", 1)[1].rsplit("<nav", 1)[0]
    s = (f'<div class="scrim"></div><section class="sheet" role="dialog" aria-modal="true" aria-label="KO: buy cancelled by the agent review">'
         '<div class="grab"></div>'
         f'<div class="sheet-h">{badge("KO")}<div><b>KO</b><div class="dim" style="font-size:var(--t-sm)">Coca-Cola · Trend momentum</div></div>'
         f'<button class="x" aria-label="Close">{ico("x", 18)}</button></div>'
         '<h3>Buy cancelled by the agent review</h3>'
         f'<div class="arms num"><div><small>The rule planned</small><b>Buy 5</b></div>{ico("chev", 20)}<div><small>After the review</small><b>No buy</b></div></div>'
         '<div class="quote"><small>Company news · medium confidence · 2 sources</small>A share offering was announced after the close; its size and price are not known yet.</div>'
         '<div class="kv num"><span>For</span><span>Tue, Nov 17 · checked 08:41 ET</span>'
         '<span>What the buy would have done</span><span>Measured after the close</span>'
         '<span>Today\'s review</span><span>4 buys · $0.04 · 31 s</span></div>'
         f'<div class="label-box">{ico("agent", 18)}<span>{AGENT_LINE}</span></div>'
         '<button class="btn">See the whole plan</button></section>')
    return (page("Activity sheet", base + s, "act"))


def states() -> str:
    def card(title, state, rows_html, note):
        return f'<div class="state-card"><small>{title}</small>{review_status(state) if state != "off" else ""}{rows_html}<p>{note}</p></div>'
    off_rows = '<div class="list">' + "".join(plan_row(*p, review_on=False) for p in PLAN[2:]) + "</div>"
    done_rows = '<div class="list">' + "".join(plan_row(*p) for p in PLAN[2:]) + "</div>"
    blocked = '<div class="list">' + plan_list(blocked=True).split('<div class="list">')[1].split("</button>")[0] + "</button></div>"
    body = (top() + '<div class="page-h"><h2>Review states</h2><p>How the plan reads in each state. Positions › Plan.</p></div>'
            + card("1 · Off (the default)", "off", off_rows + '<p class="caption" style="margin:var(--s3) 0 0">Agent review is off. The rule\u2019s plan goes ahead as it is.</p>',
                   "No row, no colour, no tags: only that caption under the plan.")
            + card("2 · Waiting", "pending", "", "Before 09:00 ET on a day with buys. Neutral. The buys show as planned.")
            + card("3 · Done", "done", done_rows, "Violet = the agent. Kept rows carry a grey tag; cancelled and halved show the old order struck through.")
            + card("4 · No answer: failed closed", "failed", blocked,
                   "Timeout, error or an answer that breaks the contract. Every buy that session is held back; sales go ahead. Amber.")
            + card("5 · Daily AI budget used up", "budget", "", "Same effect as 4: no new buys that session. Amber. The sheet names the cap that was hit.")
            + card("6 · Monthly AI budget reached", "monthly", "", "Red: the existing monthly-budget halt. It also stops intraday entries.")
            + '<div class="state-card"><small>7 · Nothing to review</small><p style="margin:0">Only sales or holds that session. No review row; the caption reads “No buys to review.”</p></div>'
            + '<div style="height:var(--s4)"></div>')
    return page("States", body, cls="board")


def home_failed() -> str:
    rows = "".join(v4.pos_row(s, "", v, p, up, at) for (s, _, v, p, up, at) in v4.POSITIONS[:2])
    body = (top()
            + f'<button class="status ended"><span class="dot"></span><span class="grow">No new buys on {SESSION}<small>The agent review didn\'t answer in time</small></span>{CHEV}</button>'
            + '<div style="height:var(--s2)"></div>' + v4.EXP
            + '<section class="hero"><p class="label">Paper pot</p><p class="value num">$10,107.10</p>'
              '<p class="delta num"><span class="up">+$107.10 all time</span><span class="down">−$63.40 today</span></p></section>'
            + v4.chart()
            + '<div class="ranges" role="group" aria-label="Chart range"><button>1W</button><button>1M</button><button>3M</button><button aria-pressed="true">All</button></div>'
            + f'<div class="sect"><h2>Holding 9</h2><a href="#" aria-label="All positions">{ico("chev", 20)}</a></div><div class="list">{rows}</div>')
    return page("Home, review failed", body, "home")


def desktop() -> str:
    nav = "".join(f'<a href="#"{" aria-current=page" if n == "Positions" else ""}>{n}</a>' for n in ["Home", "Positions", "Activity", "Engine", "Report", "Go / no-go", "Strategy", "Run & settings"])
    rows = ""
    for i, (sym, company, kind, cur, tgt, rev, verdict, flag, conf, reason) in enumerate(PLAN):
        order = order_text(kind, cur, tgt)
        after = order if verdict == "allow" else (order_text(kind, cur, rev) if verdict == "downgrade" else "No buy")
        tagc = "keep" if verdict == "allow" else "agent"
        rows += (f'<tr{" class=sel" if sym == "KO" else ""}><td><div class="stock">{badge(sym)}<div><b>{sym}</b><span>{company}</span></div></div></td>'
                 f'<td class="num">{order}</td><td class="num"><b>{after}</b></td><td><span class="tag {tagc}">{WORD[verdict]}</span></td>'
                 f'<td class="why">{reason if flag == "none" else f"{FLAG[flag]}: {reason}"}</td><td class="r"><button class="open-btn" aria-label="Open {sym}">{ico("chev", 18)}</button></td></tr>')
    for sym, company, cur, tgt in SALES:
        rows += (f'<tr><td><div class="stock">{badge(sym)}<div><b>{sym}</b><span>{company}</span></div></div></td><td class="num">Sell {cur - tgt}</td>'
                 f'<td class="num"><b>Sell {cur - tgt}</b></td><td><span class="tag keep">Not reviewed</span></td><td class="why">Sales are never reviewed.</td>'
                 f'<td class="r"><button class="open-btn" aria-label="Open {sym}">{ico("chev", 18)}</button></td></tr>')
    held = "".join(v4.pos_row(s, sub, v, p, up, at) for (s, sub, v, p, up, at) in v4.POSITIONS)
    side = (f'<section class="card side" aria-label="KO detail"><div class="sheet-h">{badge("KO")}<div><b>KO · buy cancelled</b>'
            '<div class="dim" style="font-size:var(--t-sm)">Coca-Cola · Trend momentum</div></div></div>'
            f'<div class="arms num"><div><small>The rule planned</small><b>Buy 5</b></div>{ico("chev", 20)}<div><small>After the review</small><b>No buy</b></div></div>'
            '<div class="quote"><small>Company news · medium confidence · 2 sources</small>A share offering was announced after the close; its size and price are not known yet.</div>'
            '<div class="kv num"><span>What the buy would have done</span><span>Measured after the close</span><span>Today\'s review</span><span>4 buys · $0.04 · 31 s</span></div></section>')
    body = (f'<header class="dtop"><span class="mark"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0A0B0D" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 17l5-6 4 4 7-9"/></svg></span>'
            f'<h1>kaibot</h1><nav class="dnav" aria-label="Main">{nav}</nav><span class="pill paper">Paper</span><span class="pill live"><span class="dot"></span>Running</span></header>'
            '<main><div class="page-title"><div><h2>Positions</h2><p class="dim">9 held · next orders at 09:00 ET</p></div></div>'
            f'<div style="display:flex;flex-direction:column;gap:var(--s6)"><section class="card"><h2>Held now</h2><p class="sub">Largest four of 9</p><div class="list">{held}</div></section>{side}</div>'
            f'<section class="card"><div style="display:flex;justify-content:space-between;align-items:baseline"><h2>Plan for {SESSION}</h2><span class="dim" style="font-size:var(--t-sm)">Orders go in at 09:00 ET</span></div>'
            f'<div style="margin-top:var(--s4)">{review_status("done").replace("width:calc(100% - 32px)", "")}</div>'
            '<table><thead><tr><th>Stock</th><th>Rule planned</th><th>After review</th><th>Review</th><th>Why</th><th class="r"><span class="sr-only">Open</span></th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<div class="label-box">{ico("agent", 18)}<span>{AGENT_LINE}</span></div></section></main>')
    return page("Desktop", body, cls="desk")


if __name__ == "__main__":
    for name, html in [("plan", positions_plan()), ("activity", activity()), ("sheet", sheet()), ("states", states()),
                       ("home-failed", home_failed()), ("desktop", desktop())]:
        (HERE / f"{name}.html").write_text(html)
        print("wrote", name, len(html))
