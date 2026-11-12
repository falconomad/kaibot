"""M7 · Holdings: what Kaibot holds, by rule, and one holding in detail. Built by multiday.py.

Each stock shows the days held, its share of the pot, gain or loss, the rule that owns it, how far it is from that
rule's sell conditions and the safety stop resting at the broker. Desktop shows every column; on a phone a row opens
in place.
"""
import datetime as dt
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_ui as ui  # noqa: E402
from m7_ui import IC, PROPS, TONE, icon, script, sub  # noqa: E402

TAGS = {'sell': ('Selling tonight', 'warn'), 'add': ('Adding {n} tonight', 'info'), 'trim': ('Selling {n} tonight', 'info')}


# ================================================================== view-models

def holding_row(r):
    out = r['rank'] > d.CUT
    below = r['above_avg'] is not None and r['above_avg'] < 0
    e2 = '' if r['above_avg'] is None else f"{abs(r['above_avg']):.1f}% {'below' if below else 'above'} its 100-day average"
    tag, tone = '', 'info'
    if r['plan']:
        tag, tone = TAGS[r['plan'][0]]
        tag = tag.format(n=r['plan'][1])
    return dict(
        sym=r['sym'], i=r['sym'][:2], c=d.tint(r['sym']), name=r['name'], rule=r['rule'], ruleName=d.RULES[r['rule']]['short'],
        days=f"{r['days']} days", since=f"since {d.short(r['bought'])}", daysN=r['days'],
        share=f"{r['share']:.1f}%", shareW=f"{min(100, r['share'] / d.LIMIT_STOCK * 100):.0f}%", shareN=r['share'],
        value=d.usd(r['value']), sh=f"{r['sh']} share{'s' if r['sh'] != 1 else ''}",
        gain=f"{d.arrow(r['gain'])} {d.signed(r['gain'])}", gainPct=d.pct(r['gain_pct']), gainInk=d.ink(r['gain']), gainN=r['gain_pct'],
        exit1=f"Rank {r['rank']} of {d.UNIVERSE}", exit1b='outside the top 100' if out else 'sells below 100',
        exit1Ink='#F5B64C' if out else '#F4F5F7', exit1Icon=out, exit2=e2, exit2Ink='#F5B64C' if below else '#A1A8B3', exit2Icon=below,
        hasExit2=bool(e2), stop=d.usd(r['stop']), stopSub=f"{r['stop_below']:.1f}% below", stopWord='in place',
        tag=tag, tagShow=bool(tag), tagInk=TONE[tone][0], tagBg=TONE[tone][1], tagBd=TONE[tone][2])


def holdings_vm(rows=None):
    rows = d.ROWS if rows is None else rows
    groups = []
    for k in ('r1', 'r2'):
        mine = [r for r in rows if r['rule'] == k]
        if not mine:
            continue
        gain = sum(r['gain'] for r in mine)
        groups.append(dict(key=k, name=d.RULES[k]['name'], n=len(mine),
                           meta=f"{len(mine)} stocks · {sum(r['share'] for r in mine):.0f}% of the pot",
                           gain=f"{d.arrow(gain)} {d.signed(gain)} since bought", ink=d.ink(gain), what=d.RULES[k]['what'],
                           next=f"Next check {d.day(d.RULES[k]['next_check'])}", rows=[holding_row(r) for r in mine]))
    sold = []
    for sym, name, rule, _sh, (bm, bd), (sm, sd), res, pc, why in d.SOLD:
        b, e = dt.date(2027, bm, bd), dt.date(2027, sm, sd)
        sold.append(dict(sym=sym, i=sym[:2], c=d.tint(sym), name=name, ruleName=d.RULES[rule]['short'], when=f"Sold {d.day(e)}",
                         held=f"{(e - b).days} days", res=f"{d.arrow(res)} {d.signed(res)}", pct=d.pct(pc, 1), ink=d.ink(res), why=why))
    return dict(
        sub=f"{len(rows)} stocks · {d.usd(d.INVESTED)} in stocks, {d.INVESTED / d.POT * 100:.0f}% of the pot · {d.usd(d.CASH)} in cash",
        subShort=f"{len(rows)} stocks · {d.INVESTED / d.POT * 100:.0f}% of the pot",
        nHeld=len(rows), nSold=len(d.SOLD), groups=groups, shown=groups, sold=sold, hasRows=bool(rows), isEmpty=not rows,
        emptyTitle='No stocks yet',
        emptyText='Kaibot buys only at a close, after a rule has checked its list. Steady risers checks every Wednesday after the close; '
                  'what it picks is bought at Thursday’s close and shows up here with its safety stop.',
        soldSum=f"{len(d.SOLD)} sold · result {d.signed(d.REALISED)} · dividends {d.signed(d.DIVIDENDS)}",
        barNote=f"Each bar shows the stock’s share of the pot against the limit for one stock ({d.LIMIT_STOCK}%).")


# ================================================================== templates

BADGE = ('<span aria-hidden="true" style="flex: none; width: 40px; height: 40px; border-radius: 999px; background: {{h.c}}; color: #0A0B0D; display: flex; '
         'align-items: center; justify-content: center; font-size: 12px; font-weight: 700">{{h.i}}</span>')
TAG = ('<sc-if value="{{h.tagShow}}" hint-placeholder-val="{{false}}"><span style="align-self: flex-start; font-size: 12px; font-weight: 600; color: {{h.tagInk}}; '
       'background: {{h.tagBg}}; border: 1px solid {{h.tagBd}}; border-radius: 999px; padding: 2px 9px; white-space: nowrap">{{h.tag}}</span></sc-if>')
WARN14 = icon(IC['warn'], 14, '#F5B64C', 2.4, '; margin-right: 5px; vertical-align: -2px')
SHIELD14 = icon(IC['shield'], 14, '#3DDC97', 2.2, '; margin-right: 5px; vertical-align: -2px')
NAME = ('<span style="display: flex; align-items: baseline; gap: 8px; min-width: 0"><b style="font-size: 15px; font-weight: 600">{{h.sym}}</b>'
        '<span style="min-width: 0; font-size: 13px; color: #A1A8B3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{h.name}}</span></span>')
EXIT = ('<span style="color: {{h.exit1Ink}}"><sc-if value="{{h.exit1Icon}}" hint-placeholder-val="{{false}}">@WARN14@</sc-if><span class="sr">Sells when: </span>'
        '{{h.exit1}} <span style="color: #A1A8B3">· {{h.exit1b}}</span></span>'
        '<sc-if value="{{h.hasExit2}}" hint-placeholder-val="{{true}}"><span style="color: {{h.exit2Ink}}">'
        '<sc-if value="{{h.exit2Icon}}" hint-placeholder-val="{{false}}">@WARN14@</sc-if>{{h.exit2}}</span></sc-if>')

DESK_ROW = '''              <li>
                <a href="Holding.dc.html" class="rowlink hrow" style="padding: 12px 14px; border-radius: 16px; color: #F4F5F7; text-decoration: none">
                  <span style="display: flex; align-items: center; gap: 12px; min-width: 0">@BADGE@<span style="min-width: 0; display: flex; flex-direction: column; gap: 4px">@NAME@@TAG@</span></span>
                  <span class="num" style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px"><span class="sr">Held </span>{{h.days}}</span><span style="font-size: 12px; color: #A1A8B3">{{h.since}}</span></span>
                  <span class="num" style="display: flex; flex-direction: column; gap: 5px"><span style="display: flex; align-items: baseline; gap: 8px; white-space: nowrap"><b style="font-size: 14px; font-weight: 600">{{h.share}}<span class="sr"> of the pot</span></b><span style="font-size: 12px; color: #A1A8B3">{{h.value}} · {{h.sh}}</span></span><span aria-hidden="true" style="position: relative; height: 4px; border-radius: 999px; background: #243A5C"><span style="position: absolute; left: 0; top: 0; bottom: 0; width: {{h.shareW}}; border-radius: 999px; background: #4F95F0"></span></span></span>
                  <span class="num" style="display: flex; flex-direction: column; gap: 2px"><b style="font-size: 15px; font-weight: 600; color: {{h.gainInk}}; white-space: nowrap">{{h.gain}}</b><span style="font-size: 13px; color: #A1A8B3">{{h.gainPct}}</span></span>
                  <span class="num" style="min-width: 0; display: flex; flex-direction: column; gap: 2px; font-size: 13px; line-height: 1.4">@EXIT@</span>
                  <span class="num" style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px"><span class="sr">Safety stop </span>{{h.stop}}</span><span style="font-size: 12px; color: #A1A8B3; white-space: nowrap">@SHIELD14@{{h.stopWord}} · {{h.stopSub}}</span></span>
                  @CHEV@
                </a>
              </li>'''

PHONE_ROW = '''              <li style="border-top: 1px solid #1B1E24">
                <button type="button" class="rowlink" aria-expanded="{{h.openS}}" onClick="{{h.tap}}" style="width: 100%; display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 10px 4px; border: 0; border-radius: 12px; background: transparent; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer">
                  @BADGE@
                  <span style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px">@NAME@<span class="num" style="font-size: 13px; color: #A1A8B3">{{h.days}} · {{h.share}}<span class="sr"> of the pot</span></span>@TAG@</span>
                  <span class="num" style="flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 3px"><b style="font-size: 15px; font-weight: 600; color: {{h.gainInk}}; white-space: nowrap">{{h.gain}}</b><span style="font-size: 13px; color: #A1A8B3">{{h.gainPct}}</span></span>
                  <svg class="chev {{h.chev}}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#A1A8B3" stroke-width="2" aria-hidden="true" style="flex: none"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg>
                </button>
                <sc-if value="{{h.open}}" hint-placeholder-val="{{false}}">
                  <div class="expand num" style="padding: 2px 4px 14px 56px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; line-height: 1.45">
                    <div style="display: flex; flex-direction: column; gap: 1px"><span style="font-size: 12px; color: #A1A8B3">Sells when</span>@EXIT@</div>
                    <div style="display: flex; flex-direction: column; gap: 1px"><span style="font-size: 12px; color: #A1A8B3">Safety stop at the broker</span><span>@SHIELD14@{{h.stop}} · {{h.stopWord}} · {{h.stopSub}}</span></div>
                    <div style="display: flex; flex-direction: column; gap: 1px"><span style="font-size: 12px; color: #A1A8B3">Size</span><span>{{h.value}} · {{h.sh}} · held {{h.since}}</span></div>
                    <a href="Holding.dc.html" style="@LINK@; align-self: flex-start">Why it was bought <span aria-hidden="true">→</span></a>
                  </div>
                </sc-if>
              </li>'''

GROUP_HEAD = '''            <div style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 12px; padding: 18px 14px 10px">
              <h2 style="margin: 0; font-size: 16px; font-weight: 600">{{g.name}}</h2>
              <span class="num" style="font-size: 13px; color: #A1A8B3">{{g.meta}}</span>
              <span style="flex: 1"></span>
              <span class="num" style="font-size: 14px; font-weight: 600; color: {{g.ink}}; white-space: nowrap">{{g.gain}}</span>
              <span style="flex: 1 1 100%; font-size: 13px; color: #A1A8B3; line-height: 1.45">{{g.what}} {{g.next}}.</span>
            </div>'''

EMPTY = '''        <div class="swap" style="display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 28px 22px">
          <b style="font-size: 17px; font-weight: 600">{{@P@.emptyTitle}}</b>
          <span style="font-size: 14px; color: #A1A8B3; line-height: 1.55; max-width: 640px">{{@P@.emptyText}}</span>
          <a href="NextClose.dc.html" style="@LINK@">See what is planned <span aria-hidden="true">→</span></a>
        </div>'''

LIST = '''      <section class="card pad-sm enter d2" aria-label="Stocks held now" style="@CARD@; padding: 10px 8px 12px">
        <sc-if value="{{@P@.isEmpty}}" hint-placeholder-val="{{false}}">
@EMPTY@
        </sc-if>
        <sc-if value="{{@P@.hasRows}}" hint-placeholder-val="{{true}}">
        <div class="hrow hd-desk" aria-hidden="true" style="padding: 12px 14px 10px; font-size: 12px; color: #A1A8B3; border-bottom: 1px solid #1B1E24"><span>Stock</span><span>Held</span><span>Size, of the pot</span><span>Gain or loss</span><span>Sells when</span><span>Safety stop at the broker</span><span></span></div>
        <sc-for list="{{@P@.shown}}" as="g" hint-placeholder-count="2">
          <div>
@GROUP_HEAD@
            <ul class="hd-desk" style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column">
              <sc-for list="{{g.rows}}" as="h" hint-placeholder-count="4">
@DESK_ROW@
              </sc-for>
            </ul>
            <ul class="hd-phone" style="list-style: none; margin: 0; padding: 0 10px">
              <sc-for list="{{g.rows}}" as="h" hint-placeholder-count="4">
@PHONE_ROW@
              </sc-for>
            </ul>
          </div>
        </sc-for>
        <p class="hd-desk" style="margin: 0; padding: 12px 14px 4px; font-size: 12px; color: #A1A8B3">{{@P@.barNote}}</p>
        </sc-if>
      </section>'''


def holdings_list(P):
    """The card with the held stocks: desktop rows with every column, phone rows that open in place."""
    return sub(LIST, EMPTY=EMPTY, GROUP_HEAD=GROUP_HEAD, DESK_ROW=DESK_ROW, PHONE_ROW=PHONE_ROW, BADGE=BADGE, TAG=TAG, NAME=NAME, EXIT=EXIT,
               WARN14=WARN14, SHIELD14=SHIELD14, P=P)


SOLD_LIST = '''      <section class="card pad-sm swap" aria-label="Stocks sold" style="@CARD@; padding: 10px 8px 12px">
        <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column">
          <sc-for list="{{ho.sold}}" as="h" hint-placeholder-count="6">
            <li class="prow" style="padding: 12px 14px; border-bottom: 1px solid #1B1E24">
              @BADGE@
              <span style="min-width: 0; display: flex; flex-direction: column; gap: 2px">@NAME@<span class="num" style="font-size: 13px; color: #A1A8B3">{{h.when}} · held {{h.held}} · {{h.ruleName}}</span></span>
              <span class="full-row" style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{h.why}}</span>
              <span class="num full-row-r" style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px"><b style="font-size: 15px; font-weight: 600; color: {{h.ink}}; white-space: nowrap">{{h.res}}</b><span style="font-size: 13px; color: #A1A8B3">{{h.pct}}</span></span>
            </li>
          </sc-for>
        </ul>
        <p class="num" style="margin: 0; padding: 12px 14px 4px; font-size: 13px; color: #A1A8B3">{{ho.soldSum}}</p>
      </section>'''

SKEL = '''    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">
      <div aria-busy="true" aria-label="Loading" style="display: flex; flex-direction: column; gap: 16px">
        <span class="sk" style="width: 220px; height: 40px; border-radius: 12px"></span>
        <span class="sk" style="width: 60%; height: 16px"></span>
        <span class="sk" style="height: 44px; width: 320px; max-width: 100%; border-radius: 999px"></span>
        <span class="sk" style="height: 520px; border-radius: 24px"></span>
      </div>
    </sc-if>'''

PAGE = '''  <main class="page" style="max-width: 1320px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 20px">
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px 20px">
      <div style="flex: 1 1 360px; min-width: 0; display: flex; flex-direction: column; gap: 6px">
        <div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Holdings</h1><span class="show-sm">@SAMPLE@</span></div>
        <span class="num hide-sm" style="font-size: 15px; color: #A1A8B3">{{ho.sub}}</span><span class="num show-sm" style="font-size: 15px; color: #A1A8B3">{{ho.subShort}}</span>
      </div>
      <span class="hide-sm" style="align-self: center">@SAMPLE@</span>
      <div role="tablist" aria-label="Held or sold" class="full-sm" style="display: flex; gap: 2px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 3px">
        <sc-for list="{{tabs}}" as="t" hint-placeholder-count="2">
          <button type="button" role="tab" aria-selected="{{t.on}}" onClick="{{t.pick}}" class="seg" style="flex: 1 1 auto; height: 44px; padding: 0 18px; border-radius: 999px; border: 0; background: {{t.bg}}; color: {{t.ink}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; white-space: nowrap">{{t.name}}</button>
        </sc-for>
      </div>
    </div>
    <sc-if value="{{isHeld}}" hint-placeholder-val="{{true}}">
      <div class="enter d1" style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px">
        <div role="group" aria-label="Show the stocks of one rule" style="flex: 1 1 auto; display: flex; flex-wrap: wrap; gap: 8px">
          <sc-for list="{{chips}}" as="c" hint-placeholder-count="3">
            <button type="button" aria-pressed="{{c.on}}" onClick="{{c.pick}}" class="chip" style="height: 44px; padding: 0 16px; border-radius: 999px; border: 1px solid {{c.bd}}; background: {{c.bg}}; color: {{c.ink}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; white-space: nowrap">{{c.name}}<span class="num hide-sm" style="font-weight: 500"> · {{c.n}}</span></button>
          </sc-for>
        </div>
        <label style="display: flex; align-items: center; gap: 10px; font-size: 13px; color: #A1A8B3">Sort
          <select class="kbs" onChange="{{sortPick}}" style="height: 44px; border-radius: 999px; border: 1px solid #262A31; color: #F4F5F7; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">
            <option value="size">Biggest first</option><option value="gain">Best gain first</option><option value="days">Longest held first</option>
          </select>
        </label>
      </div>
@LIST@
    </sc-if>
    <sc-if value="{{isSold}}" hint-placeholder-val="{{false}}">
@SOLD_LIST@
    </sc-if>
    @FOOT@
    </sc-if>
  </main>
'''

JS = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; this.state = { tab: 'held', rule: 'all', sort: 'size', open: 'WMT', more: false, ready: false }; }
@COMMON@
  renderVals() {
    const D = this.D, st = this.state, ho = D.ho;
    const key = { size: 'shareN', gain: 'gainN', days: 'daysN' }[st.sort];
    const shown = ho.groups.filter((g) => st.rule === 'all' || g.key === st.rule).map((g) => ({ ...g,
      rows: [...g.rows].sort((a, b) => b[key] - a[key]).map((h) => ({ ...h, open: st.open === h.sym, openS: String(st.open === h.sym), chev: st.open === h.sym ? 'open-chev' : '',
        tap: () => this.setState({ open: st.open === h.sym ? null : h.sym }) })) }));
    const on = (x) => ({ on: String(x), bd: x ? '#4F95F0' : '#262A31', bg: x ? '#16263D' : '#131519', ink: x ? '#F4F5F7' : '#A1A8B3' });
    return { ...this.common(), ho: { ...ho, shown },
      tabs: [['held', 'Held now · ' + ho.nHeld], ['sold', 'Sold · ' + ho.nSold]].map(([k, n]) => ({ name: n, on: String(st.tab === k), bg: st.tab === k ? '#F4F5F7' : 'transparent', ink: st.tab === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ tab: k }) })),
      isHeld: st.tab === 'held', isSold: st.tab === 'sold',
      chips: [{ key: 'all', name: 'All', n: ho.nHeld }, ...ho.groups.map((g) => ({ key: g.key, name: g.name, n: g.n }))].map((c) => ({ ...c, ...on(st.rule === c.key), pick: () => this.setState({ rule: c.key }) })),
      sortPick: (e) => this.setState({ sort: e.target.value }),
    };
  }
}'''


def build():
    data = dict(ho=holdings_vm())
    ui.setup('Holdings')
    body = sub(PAGE, SKEL=SKEL, LIST=holdings_list('ho'), SOLD_LIST=sub(SOLD_LIST, BADGE=BADGE, NAME=NAME))
    sc = script(JS, data)
    ui.write('Holdings', 'Holdings', 'Holdings', body, sc, 1390, PROPS)
    ui.write('HoldingsMobile', 'Holdings on a phone', 'Holdings', body, sc, 844, PROPS, width=390)
    return data


# ================================================================== one holding (sample: Walmart)

def level_bar(r, kind):
    """Where the price is: safety stop, the price Kaibot paid, the 100-day average and now, on one dollar scale."""
    desk = kind == 'd'
    W, H = (630, 124) if desk else (326, 128)
    marks = sorted([(r['stop'], 'Safety stop', '#FF6B6B'), (r['px_in'], 'Bought', '#A1A8B3'), (r['avg'], '100-day average', '#F5B64C'),
                    (r['now'], 'Now', '#F4F5F7')])
    lo, hi = marks[0][0], marks[-1][0]
    pad_l, pad_r = (36, 36) if desk else (22, 22)

    def x(v):
        return pad_l + (v - lo) / (hi - lo) * (W - pad_l - pad_r)

    y = H / 2
    out = [f'<line x1="{pad_l - 14}" y1="{y}" x2="{W - pad_r + 14}" y2="{y}" stroke="#262A31" stroke-width="6" stroke-linecap="round"></line>',
           f'<line x1="{x(r["avg"]):.1f}" y1="{y}" x2="{x(r["now"]):.1f}" y2="{y}" stroke="#4F95F0" stroke-width="6"></line>']
    for i, (v, name, col) in enumerate(marks):
        up = i % 2 == 1                                    # alternate above and below so neighbours never collide
        xv = x(v)
        anchor = 'start' if xv < 70 else 'end' if xv > W - 70 else 'middle'
        tx = pad_l - 14 if anchor == 'start' else W - pad_r + 14 if anchor == 'end' else xv
        y1, y2 = (y - 30, y - 16) if up else (y + 30, y + 44)
        if name == 'Now':
            out.append(f'<circle cx="{xv:.1f}" cy="{y}" r="7" fill="{col}" stroke="#131519" stroke-width="3"></circle>')
        else:
            out.append(f'<line x1="{xv:.1f}" y1="{y - 10}" x2="{xv:.1f}" y2="{y + 10}" stroke="{col}" stroke-width="3" stroke-linecap="round"></line>')
        out.append(f'<text x="{tx:.1f}" y="{y1:.1f}" text-anchor="{anchor}" font-size="12" fill="#A1A8B3" font-family="Inter">{name}</text>'
                   f'<text x="{tx:.1f}" y="{y2:.1f}" text-anchor="{anchor}" font-size="13" font-weight="600" fill="#F4F5F7" font-family="Inter">{d.usd(v)}</text>')
    label = (f"Price scale. Safety stop {d.usd(r['stop'])}, bought at {d.usd(r['px_in'])}, 100-day average {d.usd(r['avg'])}, now {d.usd(r['now'])}.")
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{label}" style="display: block; width: 100%; height: auto">{"".join(out)}</svg>'


def holding_vm(sym='WMT'):
    r = next(x for x in d.ROWS if x['sym'] == sym)
    rule = d.RULES[r['rule']]
    today = r['gap'] + r['intraday']
    scale = 130                                              # the rank bar runs a little past the cut-off
    stop_until = r['bought'] + dt.timedelta(days=90)
    ok, shield = IC['ok'], IC['shield']
    return dict(
        sym=r['sym'], name=r['name'], i=r['sym'][:2], c=d.tint(r['sym']),
        sub=f"{r['sym']} · {rule['name']} · held {r['days']} days, since {d.day(r['bought'])}",
        gain=f"{d.arrow(r['gain'])} {d.signed(r['gain'])}", gainInk=d.ink(r['gain']),
        gainSub=f"{d.pct(r['gain_pct'])} · {r['sh']} shares · {d.usd(r['value'])} · {r['share']:.1f}% of the pot",
        tag='Adding 1 share tonight',
        kv=[dict(k='Bought at', v=d.usd(r['px_in']), s=f"{d.day(r['bought'])}, closing price"),
            dict(k='Price now', v=d.usd(r['now']), s=f'as of {d.PRICES_AT} Berlin'),
            dict(k='Size', v=f"{r['sh']} shares", s=f"{d.usd(r['value'])} · {r['share']:.1f}% of the pot"),
            dict(k='Most for one stock', v=f'{d.LIMIT_STOCK}%', s='of the pot'),
            dict(k='Today', v=f'{d.arrow(today)} {d.signed(today)}', s=f"overnight\u00a0{d.signed(r['gap'])} · since the open\u00a0{d.signed(r['intraday'])}"),
            dict(k='Dividends', v='+$0.71', s='received Tue 2 Mar')],
        why=(f"On Wed 6 Jan, after the close, {rule['name']} ranked Walmart {d.ordinal(r['rank_in'])} of {d.UNIVERSE} stocks for how steadily it had "
             f"climbed over the previous four months. It was 4.2% above its 100-day average, and the S&P 500 was above its 200-day average, so buying "
             f"was allowed. Kaibot bought {r['sh']} shares at the next close, {d.day(r['bought'])}, for {d.usd(r['px_in'])} each."),
        size=(f"Why {r['sh']} shares: every stock gets the same risk. Walmart moved about {r['move']}% on a normal day, so {r['sh']} shares meant a "
              "normal day moves the pot by about 0.1%."),
        chips=[f"Rank when bought: {d.ordinal(r['rank_in'])} of {d.UNIVERSE}", 'Above its 100-day average: yes', 'Market check: buying allowed'],
        sells=[
            dict(icon=ok, ink='#3DDC97', title='It leaves the top fifth of the ranking',
                 text=f"Now {d.ordinal(r['rank'])} of {d.UNIVERSE}. {rule['name']} sells below {d.ordinal(d.CUT)}.", rank=True),
            dict(icon=ok, ink='#3DDC97', title='It closes below its 100-day average',
                 text=f"Now {d.usd(r['now'])}; the average is {d.usd(r['avg'])}, {r['above_avg']:.1f}% lower.", rank=False),
            dict(icon=ok, ink='#3DDC97', title='It jumps or falls more than 15% in one day',
                 text='Biggest day since it was bought: 3.1%.', rank=False),
            dict(icon=shield, ink='#3DDC97', title=f"The safety stop at the broker is hit: {d.usd(r['stop'])}",
                 text=(f"{r['stop_below']:.1f}% below today’s price. The order rests at the broker, so it works even when Kaibot is off. "
                       f"Kaibot renews it before {d.day(stop_until)}."), rank=False),
        ],
        sellsFoot='The first three are checked every Wednesday after the close. Flatten and stop sells everything at once.',
        rankLeft=f"{r['rank'] / scale * 100:.1f}%", cutLeft=f'{d.CUT / scale * 100:.1f}%',
        rankAria=f"Ranked {d.ordinal(r['rank'])} of {d.UNIVERSE}. Sells below {d.ordinal(d.CUT)}.",
        nextTitle='Tonight’s close: Kaibot adds 1 share',
        nextText='3 shares become 4. This is the two-week size check: Walmart moves less in a day than when it was bought, so the same risk allows one more share.',
        nextAfter=f"Next list check: {d.day(rule['next_check'])}, after the close.",
        history=[dict(when='Wed 10 Mar', what='Size check: add 1 share at the next close'),
                 dict(when='Tue 2 Mar', what='Dividend received: +$0.71'),
                 dict(when='Wed 24 Feb', what='Size check: no change'),
                 dict(when='Thu 7 Jan', what=f"Bought {r['sh']} shares at {d.usd(r['px_in'])}, the closing price. Safety stop placed at {d.usd(r['stop'])}"),
                 dict(when='Wed 6 Jan', what=f"Picked by {rule['name']}: ranked {d.ordinal(r['rank_in'])} of {d.UNIVERSE}")],
        priceNote=(f"The blue stretch is the room above the 100-day average: {r['above_avg']:.1f}%. The safety stop is "
                   f"{r['stop_below']:.1f}% below today’s price."),
    ), r


ONE = '''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 20px 40px 64px; display: flex; flex-direction: column; gap: 20px">
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
    <div style="display: flex; align-items: center; gap: 12px">
      <a href="Holdings.dc.html" class="press" style="flex: 1 1 auto; display: inline-flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; color: #A1A8B3; text-decoration: none; min-height: 44px">@BACK@Holdings</a>@SAMPLE@
    </div>
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: center; gap: 14px 18px">
      <span aria-hidden="true" style="flex: none; width: 56px; height: 56px; border-radius: 999px; background: {{one.c}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 700">{{one.i}}</span>
      <div style="flex: 1 1 260px; min-width: 0; display: flex; flex-direction: column; gap: 4px">
        <h1 class="h1" style="margin: 0; font-size: 32px; font-weight: 700; letter-spacing: -0.02em">{{one.name}}</h1>
        <span class="num" style="font-size: 14px; color: #A1A8B3; line-height: 1.45">{{one.sub}}</span>
      </div>
      <div class="full-sm" style="display: flex; flex-direction: column; align-items: flex-end; gap: 6px">
        <span style="font-size: 12px; font-weight: 600; color: #7FB2FF; background: #16263D; border: 1px solid #2D4F7A; border-radius: 999px; padding: 4px 10px">{{one.tag}}</span>
        <span class="num hero" style="font-size: 40px; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1; color: {{one.gainInk}}">{{one.gain}}</span>
        <span class="num" style="font-size: 14px; color: #A1A8B3">{{one.gainSub}}</span>
      </div>
    </div>
    <div class="two reflow">
      <div style="display: flex; flex-direction: column; gap: 20px; min-width: 0">
        <section class="card o4 enter d1" aria-labelledby="pr-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 12px">
          <h2 id="pr-h" style="margin: 0; font-size: 18px; font-weight: 600">Where the price is</h2>
          <div class="hide-sm">@BAR_D@</div><div class="show-sm">@BAR_M@</div>
          <span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{one.priceNote}}</span>
        </section>
        <section class="card o2 enter d2" aria-labelledby="sl-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 6px">
          <h2 id="sl-h" style="margin: 0 0 6px; font-size: 18px; font-weight: 600">What would make Kaibot sell it</h2>
          <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column">
            <sc-for list="{{one.sells}}" as="s" hint-placeholder-count="4">
              <li style="display: flex; gap: 12px; padding: 14px 0; border-top: 1px solid #1B1E24">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="{{s.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 1px"><path d="{{s.icon}}"></path></svg>
                <div style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px">
                  <b style="font-size: 15px; font-weight: 600">{{s.title}}</b>
                  <span class="num" style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{s.text}}</span>
                  <sc-if value="{{s.rank}}" hint-placeholder-val="{{false}}">
                    <div role="img" aria-label="{{one.rankAria}}" style="position: relative; margin-top: 8px; padding-bottom: 20px; max-width: 420px">
                      <div style="display: flex; height: 8px; border-radius: 999px; overflow: hidden; background: #1B1E24"><span style="flex: 0 0 {{one.cutLeft}}; background: #243A5C"></span></div>
                      <span style="position: absolute; left: {{one.cutLeft}}; top: -4px; width: 2px; height: 16px; margin-left: -1px; background: #F5B64C; border-radius: 1px"></span>
                      <span style="position: absolute; left: {{one.rankLeft}}; top: -3px; width: 14px; height: 14px; margin-left: -7px; border-radius: 999px; background: #F4F5F7; border: 3px solid #131519; box-sizing: border-box"></span>
                      <span class="num" style="position: absolute; left: 0; bottom: 0; font-size: 12px; color: #A1A8B3">1st</span>
                      <span class="num" style="position: absolute; left: {{one.cutLeft}}; bottom: 0; transform: translateX(-50%); font-size: 12px; color: #A1A8B3">100th</span>
                      <span style="position: absolute; right: 0; bottom: 0; font-size: 12px; color: #A1A8B3">sells</span>
                    </div>
                  </sc-if>
                </div>
              </li>
            </sc-for>
          </ul>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5; padding-top: 10px; border-top: 1px solid #1B1E24">{{one.sellsFoot}}</span>
        </section>
        <section class="card o5 enter d3" aria-label="Key numbers" style="@CARD@; padding: 22px">
          <dl class="kv" style="margin: 0">
            <sc-for list="{{one.kv}}" as="k" hint-placeholder-count="6"><div style="display: flex; flex-direction: column; gap: 3px; min-width: 0"><dt style="font-size: 12px; color: #A1A8B3">{{k.k}}</dt><dd class="num" style="margin: 0; font-size: 18px; font-weight: 700">{{k.v}}</dd><dd class="num" style="margin: 0; font-size: 12px; color: #A1A8B3; line-height: 1.4">{{k.s}}</dd></div></sc-for>
          </dl>
        </section>
      </div>
      <div style="display: flex; flex-direction: column; gap: 20px; min-width: 0">
        <section class="card o3 enter d2" aria-labelledby="nx-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 8px; background: #16263D; border-color: #2D4F7A">
          <h2 id="nx-h" style="margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">What happens next</h2>
          <b style="font-size: 17px; font-weight: 600">{{one.nextTitle}}</b>
          <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{one.nextText}}</span>
          <span class="num" style="font-size: 13px; color: #A1A8B3">{{one.nextAfter}}</span>
          <a href="NextClose.dc.html" style="@LINK@; align-self: flex-start">See tonight’s plan <span aria-hidden="true">→</span></a>
        </section>
        <section class="card o1 enter d3" aria-labelledby="wy-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 12px">
          <h2 id="wy-h" style="margin: 0; font-size: 18px; font-weight: 600">Why Kaibot bought it</h2>
          <p style="margin: 0; font-size: 15px; line-height: 1.55">{{one.why}}</p>
          <p style="margin: 0; font-size: 14px; color: #A1A8B3; line-height: 1.5">{{one.size}}</p>
          <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px">
            <sc-for list="{{one.chips}}" as="c" hint-placeholder-count="3"><li class="num" style="background: #1B1E24; border-radius: 999px; padding: 7px 12px; font-size: 13px">{{c}}</li></sc-for>
          </ul>
        </section>
        <section class="card o6 enter d4" aria-labelledby="hi-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 4px">
          <h2 id="hi-h" style="margin: 0 0 6px; font-size: 18px; font-weight: 600">History</h2>
          <ol style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column">
            <sc-for list="{{one.history}}" as="e" hint-placeholder-count="5"><li style="display: flex; gap: 14px; padding: 10px 0; border-top: 1px solid #1B1E24"><span class="num" style="flex: none; width: 84px; font-size: 13px; color: #A1A8B3">{{e.when}}</span><span class="num" style="font-size: 14px; line-height: 1.45">{{e.what}}</span></li></sc-for>
          </ol>
        </section>
      </div>
    </div>
    @FOOT@
    </sc-if>
  </main>
'''

JS_ONE = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; this.state = { more: false, ready: false }; }
@COMMON@
  renderVals() { return { ...this.common(), one: this.D.one }; }
}'''


def build_one():
    vm, r = holding_vm()
    ui.setup('Holdings')
    body = sub(ONE, SKEL=SKEL, BAR_D=level_bar(r, 'd'), BAR_M=level_bar(r, 'm'))
    sc = script(JS_ONE, dict(one=vm))
    ui.write('Holding', 'One holding', 'Holdings', body, sc, 1380, PROPS)
    ui.write('HoldingMobile', 'One holding on a phone', 'Holdings', body, sc, 844, PROPS, width=390)
