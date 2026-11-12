"""M7 · Next close: what Kaibot will do at the next close, with the reason for each order, and what happened at the
last close. Built by multiday.py.

Kaibot decides once a day after the close and trades at the next close, so the plan for tonight is already fixed.
"""
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_ui as ui  # noqa: E402
from m7_ui import IC, PROPS, TONE, icon, script, sub  # noqa: E402

KIND = {'sell': ('Selling', IC['minus']), 'buy': ('Buying', IC['plus']), 'add': ('Resizing', IC['plus']), 'trim': ('Resizing', IC['minus']),
        'skip': ('Not buying', IC['skip'])}


# ================================================================== view-model

def plan_row(kind, sym, name, sh, about, head, why, state='sent'):
    money = f'about {d.usd(about, False)}'
    line = f'One share costs {d.usd(about)}' if kind == 'skip' else f'{head} · {money}'
    states = {'sent': ('At the broker', IC['ok'], TONE['ok'][0]), 'none': ('No order', IC['off'], '#A1A8B3'),
              'cancelled': ('Cancelled', IC['off'], TONE['warn'][0]), 'waiting': ('Not sent yet', IC['clock'], TONE['warn'][0])}
    word, ic, ink = states['none' if kind == 'skip' else state]
    usual = kind == 'skip' or state == 'sent'                 # phones show the order state only when it is not the usual one
    return dict(sym=sym, name=name, icon=KIND[kind][1], line=line, why=why, status=word, sIcon=ic, sInk=ink, sCls='hide-sm' if usual else '',
                dim='0.55' if state == 'cancelled' and kind != 'skip' else '1')


def plan_groups(cancel_buys=False):
    groups = []
    for title, kinds in (('Selling', ('sell',)), ('Buying', ('buy',)), ('Resizing', ('add', 'trim')), ('Not buying', ('skip',))):
        rows = []
        for kind, sym, name, _rule, sh, about, head, why in d.PLAN:
            if kind in kinds:
                rows.append(plan_row(kind, sym, name, sh, about, head, why, 'cancelled' if cancel_buys and kind in ('buy', 'add') else 'sent'))
        groups.append(dict(title=title, n=str(len(rows)), rows=rows))
    return groups


def next_vm():
    trades = []
    for kind, sym, name, sh, px, res, pc, days, why in d.LAST_TRADES:
        if kind == 'buy':
            line, res_t, ink, has = f'Bought {sh} at {d.usd(px)} · {d.usd(sh * px)}', '', '#A1A8B3', False
        else:
            line, res_t, ink, has = f'Sold {sh} at {d.usd(px)} after {days} days', f'{d.arrow(res)} {d.signed(res)} · {d.pct(pc, 1)}', d.ink(res), True
        trades.append(dict(sym=sym, name=name, icon=KIND[kind][1], line=line, res=res_t, ink=ink, hasRes=has, why=why))
    r1, r2 = d.RULES['r1'], d.RULES['r2']
    return dict(
        how='Kaibot decides once a day, after the market closes, and trades at the next close.',
        day=f'Tonight, {d.day(d.TODAY)}', time='22:00', zone='Berlin · 16:00 in New York', away='in 1 h 46 min',
        steps=[dict(icon=IC['ok'], ink=TONE['ok'][0], k='Decided', v=d.PLAN_DECIDED, s='after last night’s close'),
               dict(icon=IC['ok'], ink=TONE['ok'][0], k='At the broker', v=f'6 orders since {d.PLAN_SENT}', s='they can change until 21:50'),
               dict(icon=IC['clock'], ink='#7FB2FF', k='Trades', v='Tonight 22:00', s='at the closing price')],
        stepsShort=f'Decided {d.PLAN_DECIDED}. All 6 orders have been at the broker since {d.PLAN_SENT} and trade at the closing price.',
        after=f'After tonight: {len(d.ROWS)} stocks, about {d.AFTER_PCT:.0f}% of the pot invested.',
        hasPlan=True, noPlan=False, groups=plan_groups(),
        note='Dollar amounts are estimates at the latest price. The real price is tonight’s closing price.',
        rule=f'If the pot is down {d.DAILY_LIMIT}% on the day, Kaibot cancels the buys. Sales still go ahead and every stock keeps its safety stop.',
        emptyTitle='Nothing planned for tonight’s close', emptyText='',
        last=dict(day=f"Last close · {d.day(d.sessions(d.D0, d.TODAY)[-2])}", title='Nothing was planned and nothing traded.',
                  checks=['Safety stops checked: 12 of 12 in place', 'Kaibot’s books match the broker']),
        trades=dict(day=f'Last trades · {d.day(d.LAST_TRADES_DAY)}', sum='3 planned · 3 done at the closing price', rows=trades),
        coming=[dict(when=d.day(r1['next_check']), what='Steady risers checks its list', sub='after the close · trades Thu 18 Mar'),
                dict(when='Wed 24 Mar', what='Steady risers checks sizes', sub='every second week'),
                dict(when=d.day(r2['next_check']), what='12-month winners checks its list', sub='first trading day of the month · trades Fri 2 Apr')],
    )


# ================================================================== templates

KICON = ('<span aria-hidden="true" style="width: 36px; height: 36px; border-radius: 999px; background: #1B1E24; color: #F4F5F7; display: flex; align-items: center; '
         'justify-content: center"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
         'stroke-linejoin="round"><path d="{{@R@.icon}}"></path></svg></span>')
NAME = ('<span style="display: flex; align-items: baseline; gap: 8px; min-width: 0"><b style="font-size: 15px; font-weight: 600">{{@R@.sym}}</b>'
        '<span style="min-width: 0; font-size: 13px; color: #A1A8B3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{@R@.name}}</span></span>')

PLAN = '''        <section class="card pad-sm enter d2" aria-labelledby="pl-h@ID@" style="@CARD@; padding: 22px 8px 14px; display: flex; flex-direction: column">
          <div style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; padding: 0 14px 6px"><h2 id="pl-h@ID@" style="margin: 0; flex: 1 1 auto; font-size: 18px; font-weight: 600">Tonight’s plan</h2><span class="num" style="font-size: 13px; color: #A1A8B3">{{@P@.after}}</span></div>
          <sc-if value="{{@P@.noPlan}}" hint-placeholder-val="{{false}}">
            <div style="display: flex; flex-direction: column; gap: 6px; padding: 14px 14px 10px"><b style="font-size: 17px; font-weight: 600">{{@P@.emptyTitle}}</b><span style="font-size: 14px; color: #A1A8B3; line-height: 1.55; max-width: 620px">{{@P@.emptyText}}</span></div>
          </sc-if>
          <sc-if value="{{@P@.hasPlan}}" hint-placeholder-val="{{true}}">
            <sc-for list="{{@P@.groups}}" as="g" hint-placeholder-count="4">
              <div>
                <h3 style="margin: 0; padding: 16px 14px 6px; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">{{g.title}} · <span class="num">{{g.n}}</span></h3>
                <ul style="list-style: none; margin: 0; padding: 0">
                  <sc-for list="{{g.rows}}" as="r" hint-placeholder-count="2">
                    <li class="plrow" style="padding: 12px 14px; border-top: 1px solid #1B1E24; opacity: {{r.dim}}">
                      @KICON@
                      <span style="min-width: 0; display: flex; flex-direction: column; gap: 3px">@NAME@<span class="num" style="font-size: 14px; font-weight: 500">{{r.line}}</span><span class="num" style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{r.why}}</span></span>
                      <span class="full-row {{r.sCls}}" style="display: flex; align-items: center; gap: 6px; min-height: 36px; font-size: 13px; color: #A1A8B3; white-space: nowrap"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="{{r.sInk}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none"><path d="{{r.sIcon}}"></path></svg>{{r.status}}</span>
                    </li>
                  </sc-for>
                </ul>
              </div>
            </sc-for>
            <p style="margin: 0; padding: 14px 14px 0; border-top: 1px solid #1B1E24; font-size: 13px; color: #A1A8B3; line-height: 1.55">{{@P@.note}} {{@P@.rule}}</p>
          </sc-if>
        </section>'''

WHEN = '''    <section class="card enter d1" aria-labelledby="wn-h@ID@" style="@CARD@; padding: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 18px 36px">
      <div style="flex: 0 1 auto; display: flex; flex-direction: column; gap: 4px">
        <h2 id="wn-h@ID@" style="margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">{{@P@.day}}</h2>
        <div style="display: flex; align-items: baseline; gap: 12px"><span class="num" style="font-size: 44px; font-weight: 700; letter-spacing: -0.03em; line-height: 1">{{@P@.time}}</span><span class="num" style="font-size: 15px; font-weight: 600; color: #7FB2FF; white-space: nowrap">{{@P@.away}}</span></div>
        <span class="num" style="font-size: 13px; color: #A1A8B3">{{@P@.zone}}</span>
      </div>
      <span class="num show-sm" style="flex: 1 1 100%; font-size: 14px; color: #A1A8B3; line-height: 1.5">{{@P@.stepsShort}}</span>
      <ol class="steps hide-sm" style="flex: 1 1 520px; list-style: none; margin: 0; padding: 0">
        <sc-for list="{{@P@.steps}}" as="s" hint-placeholder-count="3">
          <li style="display: flex; gap: 10px; align-items: flex-start; min-width: 0; padding: 12px 14px; border-radius: 16px; background: #0A0B0D">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{s.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 1px"><path d="{{s.icon}}"></path></svg>
            <span style="min-width: 0; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 12px; color: #A1A8B3">{{s.k}}</span><b class="num" style="font-size: 15px; font-weight: 600">{{s.v}}</b><span class="num" style="font-size: 12px; color: #A1A8B3; line-height: 1.4">{{s.s}}</span></span>
          </li>
        </sc-for>
      </ol>
    </section>'''

SIDE = '''        <section class="card enter d3" aria-labelledby="lc-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 10px">
          <h2 id="lc-h" style="margin: 0; font-size: 18px; font-weight: 600">{{nx.last.day}}</h2>
          <span style="font-size: 15px; line-height: 1.5">{{nx.last.title}}</span>
          <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px">
            <sc-for list="{{nx.last.checks}}" as="c" hint-placeholder-count="2"><li class="num" style="display: flex; gap: 8px; font-size: 14px; color: #A1A8B3; line-height: 1.4">@OK16@{{c}}</li></sc-for>
          </ul>
        </section>
        <section class="card pad-sm enter d4" aria-labelledby="lt-h" style="@CARD@; padding: 22px 8px 14px; display: flex; flex-direction: column">
          <h2 id="lt-h" style="margin: 0; padding: 0 14px 10px; font-size: 18px; font-weight: 600">{{nx.trades.day}}</h2>
          <ul style="list-style: none; margin: 0; padding: 0">
            <sc-for list="{{nx.trades.rows}}" as="r" hint-placeholder-count="3">
              <li style="display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 4px 14px; padding: 12px 14px; border-top: 1px solid #1B1E24">
                @KICON@
                <span style="min-width: 0; display: flex; flex-direction: column; gap: 2px">@NAME@<span class="num" style="font-size: 14px; font-weight: 500">{{r.line}}</span><sc-if value="{{r.hasRes}}" hint-placeholder-val="{{true}}"><b class="num" style="font-size: 14px; font-weight: 600; color: {{r.ink}}">{{r.res}}</b></sc-if><span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{r.why}}</span></span>
              </li>
            </sc-for>
          </ul>
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 6px 14px 0; border-top: 1px solid #1B1E24"><span class="num" style="flex: 1 1 auto; font-size: 13px; color: #A1A8B3">{{nx.trades.sum}}</span><a href="Report.dc.html" style="@LINK@">Earlier closes <span aria-hidden="true">→</span></a></div>
        </section>
        <section class="card enter d5" aria-labelledby="cu-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 4px">
          <h2 id="cu-h" style="margin: 0 0 6px; font-size: 18px; font-weight: 600">Coming up</h2>
          <ol style="list-style: none; margin: 0; padding: 0">
            <sc-for list="{{nx.coming}}" as="c" hint-placeholder-count="3"><li style="display: flex; gap: 14px; padding: 10px 0; border-top: 1px solid #1B1E24"><span class="num" style="flex: none; width: 84px; font-size: 13px; color: #A1A8B3; padding-top: 1px">{{c.when}}</span><span style="min-width: 0; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px; font-weight: 500">{{c.what}}</span><span class="num" style="font-size: 13px; color: #A1A8B3">{{c.sub}}</span></span></li></sc-for>
          </ol>
        </section>'''

SKEL = '''    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">
      <div aria-busy="true" aria-label="Loading" style="display: flex; flex-direction: column; gap: 20px">
        <span class="sk" style="width: 240px; height: 40px; border-radius: 12px"></span>
        <span class="sk" style="height: 120px; border-radius: 24px"></span>
        <div class="two"><span class="sk" style="height: 560px; border-radius: 24px"></span><div style="display: flex; flex-direction: column; gap: 20px"><span class="sk" style="height: 150px; border-radius: 24px"></span><span class="sk" style="height: 300px; border-radius: 24px"></span></div></div>
      </div>
    </sc-if>'''

PAGE = '''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 20px">
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
    <div class="enter" style="display: flex; flex-direction: column; gap: 6px">
      <div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Next close</h1>@SAMPLE@</div>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5">{{nx.how}}</span>
    </div>
@WHEN@
    <div class="two">
@PLAN@
      <div style="display: flex; flex-direction: column; gap: 20px; min-width: 0">
@SIDE@
      </div>
    </div>
    @FOOT@
    </sc-if>
  </main>
'''

JS = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; this.state = { more: false, ready: false }; }
@COMMON@
  renderVals() { return { ...this.common(), nx: this.D.nx }; }
}'''


def when_block(P, ident=''):
    return sub(WHEN, P=P, ID=ident)


def plan_block(P, ident=''):
    return sub(PLAN, P=P, ID=ident, KICON=KICON, NAME=NAME, R='r')


def build():
    ui.setup('Next')
    ok16 = icon(IC['ok'], 16, TONE['ok'][0], 2.2, '; margin-top: 2px')
    body = sub(PAGE, SKEL=SKEL, WHEN=when_block('nx'), PLAN=plan_block('nx'), SIDE=sub(SIDE, KICON=KICON, NAME=NAME, R='r', OK16=ok16))
    sc = script(JS, dict(nx=next_vm()))
    ui.write('NextClose', 'Next close', 'Next close', body, sc, 1500, PROPS)
    ui.write('NextCloseMobile', 'Next close on a phone', 'Next close', body, sc, 844, PROPS, width=390)
