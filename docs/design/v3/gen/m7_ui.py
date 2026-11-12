"""M7 · shared pieces for the multi-day dashboard boards (issue #129): nav, phone tab bar, CSS, icons, chart drawing.

Imported by multiday.py. Uses the v3 shell unchanged (shell.py, kb_motion.css, kb_more.css) and adds only what the
new screens need. Templates are plain strings with {{bindings}}; `@NAME@` marks are filled by `sub()`.
"""
import json
import re
import sys

sys.path.insert(0, '.')
import shell  # noqa: E402
from shell import CARD  # noqa: E402

# ------------------------------------------------------------------ navigation

NAV = [("Home", "Overview.dc.html"), ("Holdings", "Holdings.dc.html"), ("Next close", "NextClose.dc.html"),
       ("Evidence", "Evidence.dc.html"), ("Report", "Report.dc.html"), ("Engine", "Engine.dc.html"),
       ("Strategy", "Configure.dc.html"), ("Run &amp; settings", "Settings.dc.html")]
M7_PAGES = ['Overview', 'Holdings', 'Holding', 'NextClose', 'Evidence']

TAB_ICON = {
    "Home": '<path d="M3 11 L12 4 L21 11 V20 H14 V14 H10 V20 H3 Z" stroke-linejoin="round"></path>',
    "Holdings": '<path d="M8 7 H20 M8 12 H20 M8 17 H20" stroke-linecap="round"></path><path d="M4 7 H4.01 M4 12 H4.01 M4 17 H4.01" stroke-linecap="round" stroke-width="2.6"></path>',
    "Next": '<circle cx="12" cy="12" r="8.5"></circle><path d="M12 7.5 V12 L15 14" stroke-linecap="round" stroke-linejoin="round"></path>',
    "Evidence": '<path d="M12 3.5 L19 6.5 V11.5 C19 15.8 16 19 12 20.5 C8 19 5 15.8 5 11.5 V6.5 Z" stroke-linejoin="round"></path><path d="M9 12 L11.2 14.2 L15 10" stroke-linecap="round" stroke-linejoin="round"></path>',
    "More": '<path d="M5 12 H5.01 M12 12 H12.01 M19 12 H19.01" stroke-linecap="round" stroke-width="3"></path>',
}
TAB_HREF = {"Home": "Overview.dc.html", "Holdings": "Holdings.dc.html", "Next": "NextClose.dc.html", "Evidence": "Evidence.dc.html"}
TAB_STYLE = ('display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 60px; min-height: 44px; '
             'text-decoration: none; font-size: 11px; font-weight: 600')
MORE_LINKS = [("Report", "Report.dc.html", "Each day in numbers"), ("Engine", "Engine.dc.html", "Health, alarms and the event log"),
              ("Strategy", "Configure.dc.html", "What Kaibot may trade and how boldly"), ("Run &amp; settings", "Settings.dc.html", "Pot, level and account")]


def tabbar(active, live=True):
    """Phone tab bar: four pages and More. `live=False` leaves out the handlers (static state sheets)."""
    def svg(k):
        return f'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">{TAB_ICON[k]}</svg>'
    items = "\n".join(
        f'    <a href="{TAB_HREF[k]}" class="press" {"aria-current=\"page\" " if k == active else ""}style="{TAB_STYLE}; color: {"#F4F5F7" if k == active else "#A1A8B3"}">{svg(k)}{k}</a>'
        for k in TAB_HREF)
    on = ' onClick="{{openMore}}" aria-expanded="{{moreS}}"' if live else ''
    more = (f'    <button type="button" class="press"{on} aria-haspopup="dialog" style="{TAB_STYLE}; color: #A1A8B3; background: transparent; '
            f'border: 0; padding: 0; font-family: inherit; cursor: pointer">{svg("More")}More</button>')
    return f'  <nav class="tabbar" aria-label="Main">\n{items}\n{more}\n  </nav>\n' + (MORE_SHEET if live else '')


MORE_SHEET = '''  <sc-if value="{{more}}" hint-placeholder-val="{{false}}">
    <div class="mwrap">
      <div onClick="{{closeMore}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
      <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="more-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 18px 12px 24px; display: flex; flex-direction: column; gap: 2px">
        <div style="display: flex; align-items: center; gap: 8px; padding: 0 8px 8px"><h2 id="more-h" style="margin: 0; flex: 1; font-size: 18px; font-weight: 600">More</h2><button type="button" onClick="{{closeMore}}" aria-label="Close" class="press" style="width: 44px; height: 44px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button></div>
''' + "".join(
    f'        <a href="{href}" class="rowlink" style="display: flex; align-items: center; gap: 12px; min-height: 56px; padding: 6px 12px; border-radius: 14px; color: #F4F5F7; text-decoration: none"><span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><b style="font-size: 15px; font-weight: 600">{n}</b><span style="font-size: 13px; color: #A1A8B3">{sub_}</span></span>@CHEV@</a>\n'
    for n, href, sub_ in MORE_LINKS) + '''      </section>
    </div>
  </sc-if>
'''

# ------------------------------------------------------------------ icons (24 px grid, one path each so a binding can swap them)

IC = {
    'ok': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M8 12.4 l2.7 2.7 L16 9.6',
    'warn': 'M12 4 L21 20 H3 Z M12 10 V14 M12 17 V17.4',
    'crit': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M9 9 l6 6 M15 9 l-6 6',
    'pause': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M10 9 V15 M14 9 V15',
    'info': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M12 11 V16 M12 7.8 V8.2',
    'clock': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M12 7.5 V12 l3 2',
    'off': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M8 12 H16',
    'dot': 'M12 7 a5 5 0 1 0 0 10 a5 5 0 0 0 0 -10',
    'shield': 'M12 3.5 L19 6.5 V11.5 C19 15.8 16 19 12 20.5 C8 19 5 15.8 5 11.5 V6.5 Z M9 12 l2.2 2.2 L15 10',
    'shieldoff': 'M12 3.5 L19 6.5 V11.5 C19 15.8 16 19 12 20.5 C8 19 5 15.8 5 11.5 V6.5 Z M12 8.5 V12.5 M12 15.6 V16',
    'plus': 'M12 5 V19 M5 12 H19',
    'minus': 'M5 12 H19',
    'resize': 'M8 9 l4 -4 l4 4 M8 15 l4 4 l4 -4',
    'skip': 'M12 3 a9 9 0 1 0 0 18 a9 9 0 0 0 0 -18 M6 18 L18 6',
    'check': 'M5 12.5 l4.5 4.5 L19 7',
    'x': 'M6 6 L18 18 M18 6 L6 18',
}
TONE = {   # ink, tint behind an icon, border for a tinted box
    'ok': ('#3DDC97', '#12301F', '#262A31'), 'warn': ('#F5B64C', '#33280F', '#5A4520'), 'crit': ('#FF6B6B', '#2A1517', '#5A2A2E'),
    'info': ('#7FB2FF', '#16263D', '#2D4F7A'), 'off': ('#A1A8B3', '#1B1E24', '#3A404A'),
}


def icon(d, size=18, stroke='currentColor', width=2.2, extra=''):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{stroke}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true" style="flex: none{extra}"><path d="{d}"></path></svg>')


X = icon(IC['x'], 14, width=2.4)
CHEV = '<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#A1A8B3" stroke-width="2" aria-hidden="true" style="flex: none"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg>'
BACK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 5 L8 12 L15 19" stroke-linecap="round" stroke-linejoin="round"></path></svg>'
SAMPLE = ('<span style="flex: none; display: inline-flex; align-items: center; height: 26px; padding: 0 10px; border-radius: 999px; border: 1px dashed #3A404A; '
          'font-size: 12px; font-weight: 600; color: #A1A8B3; white-space: nowrap">Sample data</span>')
FOOT = '<p style="margin: 0; font-size: 12px; color: #A1A8B3; text-align: center; line-height: 1.5">Sample data: every price, rank and result here is invented · paper trading only · times in Berlin (New York is 6 hours behind)</p>'

# ------------------------------------------------------------------ CSS added to the v3 shell

M7_CSS = '''
.show-sm,.show-sm-flex,.hd-phone{display:none!important}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
select.kbs{appearance:none;-webkit-appearance:none;background:#131519 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23A1A8B3' stroke-width='2'%3E%3Cpath d='M6 9l6 6l6-6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 14px center;padding:0 40px 0 16px}
.seg{transition:background-color .25s var(--ease),color .25s var(--ease)}
.chip{transition:background-color .2s var(--ease),border-color .2s var(--ease),color .2s var(--ease),transform .15s var(--ease)}
.chip:active{transform:scale(.96)}
.rowlink{transition:background-color .2s var(--ease)}.rowlink:hover{background:#1B1E24}
.chev{transition:transform .25s var(--ease)}.open-chev{transform:rotate(90deg)}
.expand{animation:kb-rise .3s var(--ease) both}
.mwrap{position:fixed;inset:0;z-index:8;display:flex;align-items:center;justify-content:center;padding:24px}
.mpanel{width:min(440px,100%);max-height:86vh;overflow:auto;border-radius:24px;animation:kb-rise .3s var(--ease) both}
@keyframes kb-sheet{from{transform:translateY(100%)}to{transform:none}}
button:disabled{cursor:not-allowed}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 28px}
.stat{display:flex;flex-direction:column;gap:3px;min-width:0}
.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px 24px}
.side{display:flex;flex-direction:column;gap:16px}
.hrow{display:grid;grid-template-columns:minmax(0,2.1fr) 92px 156px 128px minmax(0,2.5fr) 172px 16px;gap:0 18px;align-items:center}
.prow{display:grid;grid-template-columns:44px minmax(0,1.15fr) minmax(0,1.6fr) 150px;gap:6px 16px;align-items:center}
.plrow{display:grid;grid-template-columns:36px minmax(0,1fr) auto;gap:4px 14px;align-items:start}
.two{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);gap:20px;align-items:start}
.gates{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px 24px}
.gsteps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.checks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px 32px;padding-top:6px}
.orow{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,440px);gap:12px 28px;align-items:center}
.kv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.strip{display:flex;flex-direction:column;gap:14px}
@media (max-width: 1080px){.two{grid-template-columns:minmax(0,1fr)}}
@media (max-width: 1180px){.hd-desk{display:none!important}.hd-phone{display:block!important}}
@media (max-width: 760px){
  .show-sm{display:block!important}.show-sm-flex{display:flex!important}
  .hd-desk{display:none!important}.hd-phone{display:block!important}
  .mwrap{align-items:flex-end;padding:0}.mpanel{width:100%;border-radius:24px 24px 0 0;animation:kb-sheet .35s var(--ease) both}
  .tap{min-height:44px!important}
  .two{grid-template-columns:minmax(0,1fr)!important}
  .stats{grid-template-columns:minmax(0,1fr);gap:8px}
  .stat{flex-direction:row;flex-wrap:wrap;align-items:baseline;gap:0 10px}
  .stat>dt{flex:1 1 auto}.stat>.sub{flex:1 1 100%;text-align:right}
  .facts{grid-template-columns:minmax(0,1fr);gap:12px}
  .side{gap:12px}
  .hrow{grid-template-columns:minmax(0,1fr) auto 16px;gap:0 10px}
  .prow{grid-template-columns:40px minmax(0,1fr);gap:4px 12px}
  .plrow{grid-template-columns:36px minmax(0,1fr);gap:4px 12px}
  .gates{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
  .gsteps{gap:6px}.gstep{padding:10px 8px!important}
  .checks{grid-template-columns:minmax(0,1fr)}
  .orow{grid-template-columns:minmax(0,1fr)}
  .steps{grid-template-columns:minmax(0,1fr)}
  .two.reflow{display:flex;flex-direction:column;gap:16px}.two.reflow>div{display:contents!important}
  .o1{order:1}.o2{order:2}.o3{order:3}.o4{order:4}.o5{order:5}.o6{order:6}
  .kv{grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
  .chartcard{padding:14px 12px 10px!important}
  .statusbar{padding:6px 8px 6px 14px!important;gap:0!important}
  .pad-sm{padding:6px 4px 10px!important}
  .full-row{grid-column:2}
  .full-row-r{grid-column:2;align-items:baseline!important;flex-direction:row!important;gap:8px!important}
}
'''


def phone_scope(css):
    """Copy every rule inside `@media (max-width: 760px)` under `.phone`, so a phone frame drawn on a wide board
    gets the phone layout without a narrow window."""
    out = []
    for m in re.finditer(r'@media \(max-width: 760px\)\{', css):
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += {'{': 1, '}': -1}.get(css[i], 0)
            i += 1
        body = css[m.end():i - 1]
        for rule in re.finditer(r'([^{}]+)\{([^{}]*)\}', body):
            sels = ','.join('.phone ' + s.strip() for s in rule.group(1).split(',') if s.strip())
            out.append(f'{sels}{{{rule.group(2)}}}')
    return '\n'.join(out)


def setup(active_tab):
    """Point the shared shell at the M7 nav. Call before shell.head() for every board."""
    shell.NAV = NAV
    shell.TAB = sub(tabbar(active_tab))
    if M7_CSS not in shell.CSS:
        shell.CSS = shell.CSS + shell.MORE + M7_CSS


def sub(s, **kw):
    """Fill @NAME@ marks. The shared ones first, then the caller's."""
    base = {'CARD': CARD, 'X': X, 'CHEV': CHEV, 'BACK': BACK, 'SAMPLE': SAMPLE, 'FOOT': FOOT, 'LINK': LINK, 'LABEL': LABEL}
    base.update(kw)
    for _ in range(3):                      # marks may sit inside other marks
        for k, v in base.items():
            s = s.replace(f'@{k}@', v)
    left = re.findall(r'@[A-Z][A-Z0-9_]*@', s)
    assert not left, f'unfilled marks: {sorted(set(left))}'
    return s


def static(s):
    """Drop event handlers: for frames that only show a state."""
    return re.sub(r' on[A-Z][A-Za-z]*="\{\{[^}]*\}\}"', '', s)


def page_head(title, active):
    """v3 shell head with this story's level pill and Home link."""
    h = shell.head(title, active, 100)
    logo = 'style="display: flex; align-items: center; gap: 10px; color: #F4F5F7; text-decoration: none"'
    assert logo in h
    h = h.replace(logo, logo[:-1] + '; min-height: 44px"')              # the logo is a link: give it a full-size target
    return h.replace('PAPER · HUNTER', 'PAPER · BALANCED').replace('href="Home.dc.html"', 'href="Overview.dc.html"')


def write(name, title, active, body, script, h, props='', width=1440, height=None):
    """One board file. Phone boards are the same page in a 390 px window, with links to the phone boards."""
    out = page_head(title, active).replace('min-height: 100px', f'min-height: {height or h}px') + body + shell.tail('-', script, height or h)
    out = out.replace('data-props=\'{"$preview":{"width":1440', 'data-props=\'{' + props + '"$preview":{"width":' + str(width))
    if width < 760:
        for p in M7_PAGES:
            out = out.replace(f'href="{p}.dc.html"', f'href="{p}Mobile.dc.html"')
    open(f'../project/v3/{name}.dc.html', 'w').write(out)
    return out


# ------------------------------------------------------------------ the pot and S&P 500 chart

def nice_ticks(lo, hi, most):
    """Round dollar values for the grid: the smallest clean step that gives at most `most` lines."""
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        ticks = [t for t in range(int(lo // step) * step, int(hi) + step, step) if lo <= t <= hi]
        if len(ticks) <= most:
            return ticks
    return []


def chart(days, pot, spy, labels, kind, uid):
    """Static SVG for one range and one size, plus the numbers the board script needs for the hover read-out.

    One y axis in dollars for both lines; both start at the same value. Solid hairline grid. The pot is a solid blue
    line with a soft wash; the S&P 500 is dashed amber. Names sit at the line ends in text colours.
    """
    desk = kind == 'd'
    W, H = (800, 330) if desk else (358, 172)
    L, R, T, B = (56, 122, 14, 30) if desk else (50, 40, 10, 24)
    n = len(days)
    start = pot[0]
    lo, hi = min(pot + spy), max(pot + spy)
    pad = max((hi - lo) * 0.12, start * 0.004)
    lo, hi = lo - pad, hi + pad

    def x(i):
        return L + (i / (n - 1)) * (W - L - R)

    def y(v):
        return T + (1 - (v - lo) / (hi - lo)) * (H - T - B)

    def path(vals):
        return ' '.join(f"{'M' if i == 0 else 'L'}{x(i):.1f} {y(v):.1f}" for i, v in enumerate(vals))

    fs = 11
    grid = []
    for t in nice_ticks(lo, hi, 5 if desk else 4):
        if abs(y(t) - y(start)) < 13:
            continue                                   # the start line carries its own label
        grid.append(f'<line x1="{L}" y1="{y(t):.1f}" x2="{W - R}" y2="{y(t):.1f}" stroke="#262A31" stroke-width="1"></line>'
                    f'<text x="{L - 8}" y="{y(t) + 4:.1f}" text-anchor="end" font-size="{fs}" fill="#A1A8B3" font-family="Inter">${t:,.0f}</text>')
    grid.append(f'<line x1="{L}" y1="{y(start):.1f}" x2="{W - R}" y2="{y(start):.1f}" stroke="#3A404A" stroke-width="1"></line>'
                f'<text x="{L - 8}" y="{y(start) + 4:.1f}" text-anchor="end" font-size="{fs}" font-weight="600" fill="#F4F5F7" font-family="Inter">${start:,.0f}</text>')
    xl = []
    for i, text in labels:
        anchor = 'start' if i == 0 else 'end' if i == n - 1 else 'middle'
        xl.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="{anchor}" font-size="{fs}" fill="#A1A8B3" font-family="Inter">{text}</text>')
    py, sy = y(pot[-1]), y(spy[-1])
    lp, ls = py, sy
    gap = 16 if desk else 14
    if abs(lp - ls) < gap:                             # keep the two end labels apart
        mid, s = (lp + ls) / 2, (-1 if lp <= ls else 1)
        lp, ls = mid + s * gap / 2, mid - s * gap / 2
    ex = W - R
    if desk:
        ends = (f'<text x="{ex + 12}" y="{lp + 4:.1f}" font-size="12" font-weight="600" fill="#F4F5F7" font-family="Inter">Pot ${pot[-1]:,.0f}</text>'
                f'<text x="{ex + 12}" y="{ls + 4:.1f}" font-size="12" fill="#A1A8B3" font-family="Inter">S&amp;P 500 ${spy[-1]:,.0f}</text>')
    else:
        ends = (f'<text x="{ex + 9}" y="{lp + 4:.1f}" font-size="11" font-weight="600" fill="#F4F5F7" font-family="Inter">Pot</text>'
                f'<text x="{ex + 9}" y="{ls + 4:.1f}" font-size="11" fill="#A1A8B3" font-family="Inter">S&amp;P</text>')
    base = H - B
    svg = f'''<svg viewBox="0 0 {W} {H}" aria-hidden="true" style="display: block; width: 100%; height: auto">
            <defs><linearGradient id="pf{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4F95F0" stop-opacity="0.26"></stop><stop offset="1" stop-color="#4F95F0" stop-opacity="0"></stop></linearGradient></defs>
            {''.join(grid)}
            {''.join(xl)}
            <g class="rise" style="transform-origin: 0 {base}px">
              <path d="{path(pot)} L{x(n - 1):.1f} {base} L{x(0):.1f} {base} Z" fill="url(#pf{uid})"></path>
              <path d="{path(spy)}" fill="none" stroke="#C4862F" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"></path>
              <path d="{path(pot)}" fill="none" stroke="#4F95F0" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>
            </g>
            <g class="fade">
              <circle cx="{ex}" cy="{sy:.1f}" r="4" fill="#C4862F" stroke="#131519" stroke-width="2"></circle>
              <circle cx="{ex}" cy="{py:.1f}" r="5" fill="#4F95F0" stroke="#131519" stroke-width="2"></circle>
              {ends}
            </g>
            <g opacity="{{{{hv.o}}}}">
              <line x1="{{{{hv.x{kind}}}}}" y1="{T}" x2="{{{{hv.x{kind}}}}}" y2="{base}" stroke="#A1A8B3" stroke-width="1"></line>
              <circle cx="{{{{hv.x{kind}}}}}" cy="{{{{hv.s{kind}}}}}" r="4" fill="#C4862F" stroke="#131519" stroke-width="2"></circle>
              <circle cx="{{{{hv.x{kind}}}}}" cy="{{{{hv.p{kind}}}}}" r="5" fill="#4F95F0" stroke="#131519" stroke-width="2"></circle>
            </g>
          </svg>'''
    geo = dict(w=W, l=L, r=R, x=[round(x(i), 1) for i in range(n)], p=[round(y(v), 1) for v in pot], s=[round(y(v), 1) for v in spy])
    return svg, geo


# ------------------------------------------------------------------ shared by every board

PROPS = '"showLoading":{"editor":"boolean","default":false,"section":"States"},'
LINK = 'display: inline-flex; align-items: center; gap: 6px; min-height: 44px; font-size: 14px; font-weight: 600; text-decoration: none'
LABEL = 'font-size: 13px; color: #A1A8B3'


def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


# shared by every board script: phone "More" sheet, loading skeleton, small helpers
JS_COMMON = r'''
  componentDidMount() { this._t = setTimeout(() => this.setState({ ready: true }), 1000); }
  componentWillUnmount() { clearTimeout(this._t); }
  common() {
    const st = this.state; const loading = !!this.props.showLoading || !st.ready;
    return { loading, ready: !loading, more: !!st.more, moreS: String(!!st.more), openMore: () => this.setState({ more: true }), closeMore: () => this.setState({ more: false }) };
  }'''


def script(tpl, data):
    return tpl.replace('@COMMON@', JS_COMMON).replace('@DATA@', js(data))
