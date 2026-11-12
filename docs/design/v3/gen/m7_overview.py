"""M7 · Overview (Home) for a multi-day book: engine and alarm state, the pot against the S&P 500, how much is invested,
what happens at the next close. Built by multiday.py."""
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_ui as ui  # noqa: E402
from m7_ui import IC, PROPS, TONE, script, sub  # noqa: E402


# ================================================================== view-models (what each screen shows, as text)

def status(tone='ok', title='All good', subline='No alarms', bad=None):
    """Engine and alarm state: one headline and four checks. `bad` maps a check to (tone, text)."""
    bad = bad or {}
    facts = [('engine', 'Engine', 'Running · 6 s ago'), ('daily', 'Daily check', 'Done Wed 22:21'),
             ('stops', 'Safety stops', '12 of 12 in place'), ('books', 'Books', 'Match the broker')]
    rows = []
    for key, label, value in facts:
        t, v = bad.get(key, ('ok', value))
        rows.append(dict(label=label, value=v, icon=IC[t if t != 'off' else 'off'], ink=TONE[t][0],
                         word={'ok': 'fine', 'warn': 'needs a look', 'crit': 'problem', 'off': 'unknown'}[t]))
    n_ok = sum(1 for r in rows if r['word'] == 'fine')
    # on a phone the four checks stay folded while all is well and open by themselves when one is not
    return dict(title=title, sub=subline, icon=IC[tone], ink=TONE[tone][0], tint=TONE[tone][1], facts=rows,
                short=f'{n_ok} of 4 checks fine' if n_ok < 4 else '4 checks passed',
                fCls='hide-sm' if n_ok == 4 else '', fChev='' if n_ok == 4 else 'open-chev', fS=str(n_ok < 4).lower())


def alarm(tone, title, text, action='What this means', href='Engine.dc.html'):
    return dict(show=True, title=title, text=text, icon=IC[tone], ink=TONE[tone][0], bg=TONE[tone][1], bd=TONE[tone][2],
                action=action, href=href, role='alert' if tone == 'crit' else 'status')


NO_ALARM = dict(show=False, title='', text='', icon=IC['ok'], ink='#3DDC97', bg='#131519', bd='#262A31', action='', href='Engine.dc.html', role='status')


def invested(pct_now, n_stocks, cash, market_ok=True, limit=d.LIMIT_INVESTED, by_rule=True):
    shift = '-100%' if limit > 86 else '0%' if limit < 14 else '-50%'
    if market_ok:
        f = dict(icon=IC['ok'], ink=TONE['ok'][0], bg='#0A0B0D', bd='#0A0B0D', title='Buying allowed',
                 text=f'The S&P 500 is {d.SPY_VS_200}% above its 200-day average.')
    else:
        f = dict(icon=IC['pause'], ink=TONE['warn'][0], bg=TONE['warn'][1], bd=TONE['warn'][2], title='Not buying for now',
                 text='The S&P 500 is 1.8% below its 200-day average. Steady risers buys nothing new until it is back above. '
                      'Kaibot keeps what it holds and still sells by its rules.')
    rules = []
    if by_rule and n_stocks:
        for k in ('r1', 'r2'):
            rows = [r for r in d.ROWS if r['rule'] == k]
            share = sum(r['share'] for r in rows)
            rules.append(dict(name=d.RULES[k]['short'], w=f'{share:.1f}%', pct=f'{share:.0f}%'))
        rules.append(dict(name='Cash', w=f'{100 - pct_now:.1f}%', pct=f'{100 - pct_now:.0f}%'))
    stocks = f"{d.usd(d.POT * pct_now / 100, False)} in {n_stocks} stocks" if n_stocks else 'No stocks yet'
    return dict(pct=f'{pct_now:.0f}%', now=round(pct_now), w=f'{pct_now:.1f}%', limitLeft=f'{limit}%', limitShift=shift,
                limitText=f'Limit {limit}%', line=f'{stocks} · {d.usd(cash, False)} in cash',
                aria=f'{pct_now:.0f} percent of the pot is invested in stocks; the limit is {limit} percent', f=f, rules=rules,
                hasRules=bool(rules))


def next_close(lines=None, foot=None, short=None, when='Tonight 22:00', away='in 1 h 46 min'):
    if lines is None:
        lines = [dict(k='Sell 2', names='XOM, GM'), dict(k='Buy 2', names='UBER, CVS'), dict(k='Resize 2', names='WMT, NEM')]
        foot = '6 orders are at the broker, waiting for the close.'
        short = '2 sells · 2 buys · 2 resizes'
    return dict(when=when, away=away, lines=lines, foot=foot, short=short, hasLines=bool(lines))


def held_summary(rows):
    if not rows:
        return dict(n='None yet', faces=[], more='', line='Kaibot holds no stocks yet.', best='', short='No stocks yet', none=True, has=False)
    up = sum(1 for r in rows if r['gain'] > 0)
    best, worst = max(rows, key=lambda r: r['gain_pct']), min(rows, key=lambda r: r['gain_pct'])
    seen, faces = set(), []
    for r in sorted(rows, key=lambda r: -r['value']):
        if r['sym'] not in seen and len(faces) < 5:
            seen.add(r['sym'])
            faces.append(dict(i=r['sym'][:2], c=d.tint(r['sym'])))
    names = len({r['sym'] for r in rows})
    return dict(n=f'{len(rows)} stocks', faces=faces, more=f'+{names - len(faces)}' if names > len(faces) else '',
                line=f'{up} up · {len(rows) - up} down', short=f'{len(rows)} stocks · {up} up · {len(rows) - up} down',
                best=f"up most {best['sym']} {d.pct(best['gain_pct'])} · down most {worst['sym']} {d.pct(worst['gain_pct'])}", none=False, has=True)


def overview_vm():
    since = d.POT - d.START
    spy_pct = (d.SPY_NOW / d.START - 1) * 100
    behind = d.SPY_NOW - d.POT
    return dict(
        alarm=NO_ALARM,
        status=status(),
        hero=dict(label='Pot, at today’s prices', asof=f'Prices as of {d.PRICES_AT} Berlin.', value=d.usd(d.POT), ink='#F4F5F7',
                  note='Cash plus every stock at its latest price, after costs.'),
        stats=[
            dict(label=f'Since {d.short(d.D0)}', value=d.delta(since, since / d.START * 100), ink=d.ink(since), sub=''),
            dict(label='Today', value=d.delta(d.TODAY_CHG, d.TODAY_CHG / d.PREV_POT * 100), ink=d.ink(d.TODAY_CHG),
                 sub=f'Overnight\u00a0{d.signed(d.GAP)} · since the open\u00a0{d.signed(d.INTRADAY)}'),
            dict(label='S&P 500 (SPY), same start', value=f'{d.arrow(spy_pct)} {d.pct(spy_pct)}', ink='#A1A8B3',
                 sub=f"The pot is {d.usd(abs(behind))} {'behind' if behind > 0 else 'ahead'}"),
        ],
        inv=invested(d.INVESTED / d.POT * 100, len(d.ROWS), d.CASH),
        next=next_close(),
        held=held_summary(d.ROWS),
        chartOn=True, chartOff=False, chartNote='',
        controls=dict(show=True, confirm=f'Sell all {len(d.ROWS)} stocks at the market price and end this run?'),
    )


def chart_ranges():
    """Three ranges. In each, the S&P 500 line is re-based so both lines start at the same value."""
    out, svgs = {}, {}
    n = len(d.DAYS)
    for key, label, back in (('w', '1W', 5), ('m', '1M', 21), ('all', 'All', n - 1)):
        i0 = n - 1 - back
        days, pot = d.DAYS[i0:], d.POT_LINE[i0:]
        k = pot[0] / d.SPY_LINE[i0]
        spy = [round(v * k, 2) for v in d.SPY_LINE[i0:]]
        m = len(days)
        desk = [(0, d.short(days[0]))] + [(j, d.short(days[j])) for j in ((m - 1) // 3, 2 * (m - 1) // 3) if m > 6] + [(m - 1, 'today')]
        phone = [(0, d.short(days[0])), (m - 1, 'today')]
        sd, gd = ui.chart(days, pot, spy, desk, 'd', key + 'd')
        sm, gm = ui.chart(days, pot, spy, phone, 'm', key + 'm')
        svgs[key] = (sd, sm)
        dp, ds = pot[-1] - pot[0], spy[-1] - spy[0]
        out[key] = dict(
            label=label, dates=[d.day(x) for x in days[:-1]] + ['Today, so far'], pot=[d.usd(v) for v in pot], spy=[d.usd(v) for v in spy],
            d=gd, m=gm,
            say=(f"Since {d.short(days[0])}: pot {d.pct(dp / pot[0] * 100)}, S&P 500 {d.pct(ds / spy[0] * 100)}. "
                 f"The pot is {d.usd(abs(pot[-1] - spy[-1]))} {'ahead' if pot[-1] >= spy[-1] else 'behind'} over this period."),
            aria=(f"Line chart from {d.day(days[0])} to today. The pot went from {d.usd(pot[0])} to {d.usd(pot[-1])} ({d.pct(dp / pot[0] * 100)}). "
                  f"The same money in the S&P 500 went to {d.usd(spy[-1])} ({d.pct(ds / spy[0] * 100)})."))
    return out, svgs


# ================================================================== templates

ALARM = '''    <sc-if value="{{@P@.alarm.show}}" hint-placeholder-val="{{false}}">
      <div role="{{@P@.alarm.role}}" class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-start; gap: 10px 16px; padding: 14px 18px; border-radius: 20px; background: {{@P@.alarm.bg}}; border: 1px solid {{@P@.alarm.bd}}">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="{{@P@.alarm.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 1px"><path d="{{@P@.alarm.icon}}"></path></svg>
        <div style="flex: 1 1 280px; min-width: 0; display: flex; flex-direction: column; gap: 3px"><b style="font-size: 15px; font-weight: 600">{{@P@.alarm.title}}</b><span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{@P@.alarm.text}}</span></div>
        <a href="{{@P@.alarm.href}}" style="@LINK@; color: #F4F5F7; align-self: center">{{@P@.alarm.action}} <span aria-hidden="true">→</span></a>
      </div>
    </sc-if>'''

STATUS = '''    <section class="card statusbar enter" aria-labelledby="st-h@ID@" style="@CARD@; border-radius: 20px; padding: 14px 20px; display: flex; flex-wrap: wrap; align-items: center; gap: 12px 28px">
      <div class="full-sm" style="flex: 0 1 auto; display: flex; align-items: center; gap: 12px; min-width: 200px">
        <span aria-hidden="true" style="flex: none; width: 36px; height: 36px; border-radius: 999px; background: {{@P@.status.tint}}; display: flex; align-items: center; justify-content: center"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="{{@P@.status.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="{{@P@.status.icon}}"></path></svg></span>
        <div style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px"><h2 id="st-h@ID@" style="margin: 0; font-size: 17px; font-weight: 600; white-space: nowrap">{{@P@.status.title}}</h2><span style="font-size: 13px; color: #A1A8B3"><span class="hide-sm">{{@P@.status.sub}}</span><span class="show-sm">{{@P@.status.short}}</span></span></div>
        <button type="button" class="show-sm-flex press" onClick="{{@P@.status.tap}}" aria-expanded="{{@P@.status.fS}}" aria-controls="st-f@ID@" aria-label="Show the four checks" style="flex: none; width: 44px; height: 44px; border-radius: 999px; border: 0; background: transparent; color: #A1A8B3; cursor: pointer; align-items: center; justify-content: center"><svg class="chev {{@P@.status.fChev}}" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg></button>
      </div>
      <ul id="st-f@ID@" class="facts {{@P@.status.fCls}}" style="flex: 1 1 560px; list-style: none; margin: 0; padding: 0">
        <sc-for list="{{@P@.status.facts}}" as="f" hint-placeholder-count="4">
          <li style="display: flex; gap: 8px; align-items: flex-start; min-width: 0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="{{f.ink}}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 2px"><path d="{{f.icon}}"></path></svg>
            <span style="min-width: 0; display: flex; flex-direction: column; gap: 1px"><span style="font-size: 12px; color: #A1A8B3">{{f.label}} <span style="position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0)">: {{f.word}}</span></span><span style="font-size: 14px; font-weight: 500; line-height: 1.35">{{f.value}}</span></span>
          </li>
        </sc-for>
      </ul>
      <a href="Engine.dc.html" class="hide-sm" style="@LINK@">Engine health <span aria-hidden="true">→</span></a>
    </section>'''

HERO = '''        <div style="display: flex; flex-direction: column; gap: 6px">
          <div style="display: flex; align-items: center; gap: 12px"><h2 id="pot-h@ID@" style="margin: 0; flex: 1 1 auto; font-size: 15px; font-weight: 500; color: #A1A8B3">{{@P@.hero.label}}</h2>@SAMPLE@</div>
          <div class="num hero" style="font-size: 64px; font-weight: 700; letter-spacing: -0.04em; line-height: 1; color: {{@P@.hero.ink}}">{{@P@.hero.value}}</div>
          <span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.45"><span class="hide-sm">{{@P@.hero.note}} </span>{{@P@.hero.asof}}</span>
        </div>
        <dl class="stats" style="margin: 0">
          <sc-for list="{{@P@.stats}}" as="s" hint-placeholder-count="3">
            <div class="stat"><dt style="@LABEL@">{{s.label}}</dt><dd class="num" style="margin: 0; font-size: 16px; font-weight: 600; color: {{s.ink}}; white-space: nowrap">{{s.value}}</dd><dd class="num sub" style="margin: 0; font-size: 13px; color: #A1A8B3; line-height: 1.4">{{s.sub}}</dd></div>
          </sc-for>
        </dl>'''

METER = '''          <div role="meter" aria-label="Share of the pot invested in stocks" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{{@P@.inv.now}}" aria-valuetext="{{@P@.inv.aria}}" style="position: relative; padding-top: 22px">
            <span class="num" style="position: absolute; top: 0; left: {{@P@.inv.limitLeft}}; transform: translateX({{@P@.inv.limitShift}}); font-size: 12px; color: #A1A8B3; white-space: nowrap">{{@P@.inv.limitText}}</span>
            <div style="position: relative; height: 12px; border-radius: 999px; background: #243A5C">
              <div class="grow" style="position: absolute; left: 0; top: 0; bottom: 0; width: {{@P@.inv.w}}; border-radius: 999px; background: #4F95F0"></div>
              <span aria-hidden="true" style="position: absolute; left: {{@P@.inv.limitLeft}}; top: -4px; bottom: -4px; width: 2px; margin-left: -1px; border-radius: 1px; background: #F4F5F7"></span>
            </div>
            <div style="display: flex; justify-content: space-between; padding-top: 6px; font-size: 12px; color: #A1A8B3"><span>All cash</span><span>Fully invested</span></div>
          </div>'''

MARKET = '''          <div style="display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 14px; background: {{@P@.inv.f.bg}}; border: 1px solid {{@P@.inv.f.bd}}">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{@P@.inv.f.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 1px"><path d="{{@P@.inv.f.icon}}"></path></svg>
            <span style="min-width: 0; display: flex; flex-direction: column; gap: 2px"><b style="font-size: 14px; font-weight: 600">{{@P@.inv.f.title}}</b><span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{@P@.inv.f.text}}</span></span>
          </div>'''

# phone: the three answers in one card, right under the pot, so the first screen answers everything
ANSWERS = '''        <div class="show-sm-flex" style="@CARD@; border-radius: 20px; flex-direction: column; overflow: hidden">
          <section aria-labelledby="inv-m@ID@" style="padding: 16px; display: flex; flex-direction: column; gap: 12px">
            <div style="display: flex; align-items: baseline; gap: 10px"><h2 id="inv-m@ID@" style="margin: 0; flex: 1; font-size: 16px; font-weight: 600">Invested</h2><span class="num" style="font-size: 24px; font-weight: 700; letter-spacing: -0.02em; line-height: 1">{{@P@.inv.pct}}</span></div>
@METER@
@MARKET@
          </section>
          <a href="NextClose.dc.html" class="rowlink" style="display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 8px 16px; border-top: 1px solid #262A31; color: #F4F5F7; text-decoration: none">
            <span style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 13px; color: #A1A8B3">Next close · {{@P@.next.when}}</span><b class="num" style="font-size: 15px; font-weight: 600">{{@P@.next.short}}</b></span>@CHEV@
          </a>
          <a href="Holdings.dc.html" class="rowlink" style="display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 8px 16px; border-top: 1px solid #262A31; color: #F4F5F7; text-decoration: none">
            <span style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 13px; color: #A1A8B3">Holdings</span><b class="num" style="font-size: 15px; font-weight: 600">{{@P@.held.short}}</b></span>@CHEV@
          </a>
        </div>'''

INVESTED = '''        <section class="card enter d2" aria-labelledby="inv-h@ID@" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <div style="display: flex; align-items: baseline; gap: 10px"><h2 id="inv-h@ID@" style="margin: 0; flex: 1; font-size: 16px; font-weight: 600">Invested</h2><span class="num" style="font-size: 28px; font-weight: 700; letter-spacing: -0.02em; line-height: 1">{{@P@.inv.pct}}</span></div>
@METER@
          <sc-if value="{{@P@.inv.hasRules}}" hint-placeholder-val="{{true}}">
            <ul aria-label="Where the pot is" style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px">
              <sc-for list="{{@P@.inv.rules}}" as="r" hint-placeholder-count="3">
                <li style="display: grid; grid-template-columns: minmax(0, 1fr) 96px 40px; gap: 10px; align-items: center; font-size: 13px"><span style="color: #A1A8B3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{r.name}}</span><span aria-hidden="true" style="position: relative; height: 6px; border-radius: 999px; background: #243A5C"><span style="position: absolute; left: 0; top: 0; bottom: 0; width: {{r.w}}; border-radius: 999px; background: #4F95F0"></span></span><span class="num" style="text-align: right; font-weight: 600">{{r.pct}}</span></li>
              </sc-for>
            </ul>
          </sc-if>
@MARKET@
        </section>'''

NEXT_CARD = '''        <section class="card enter d3" aria-labelledby="nx-h@ID@" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; align-items: center; gap: 10px; margin: -8px 0"><h2 id="nx-h@ID@" style="margin: 0; flex: 1 1 auto; font-size: 16px; font-weight: 600">Next close</h2><a href="NextClose.dc.html" aria-label="See the plan for the next close" style="@LINK@">See the plan <span aria-hidden="true">→</span></a></div>
          <span class="num" style="font-size: 13px; color: #A1A8B3">{{@P@.next.when}} · {{@P@.next.away}}</span>
          <sc-if value="{{@P@.next.hasLines}}" hint-placeholder-val="{{true}}">
            <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px">
              <sc-for list="{{@P@.next.lines}}" as="l" hint-placeholder-count="3"><li style="display: flex; gap: 10px; align-items: baseline; font-size: 14px"><b class="num" style="flex: none; width: 76px; font-weight: 600">{{l.k}}</b><span class="num" style="color: #A1A8B3">{{l.names}}</span></li></sc-for>
            </ul>
          </sc-if>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{@P@.next.foot}}</span>
        </section>'''

HELD_CARD = '''        <section class="card enter d4" aria-labelledby="hd-h@ID@" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; align-items: center; gap: 10px; margin: -8px 0"><h2 id="hd-h@ID@" style="margin: 0; flex: 1 1 auto; font-size: 16px; font-weight: 600">Holdings</h2><a href="Holdings.dc.html" aria-label="See all holdings" style="@LINK@">See all <span aria-hidden="true">→</span></a></div>
          <sc-if value="{{@P@.held.has}}" hint-placeholder-val="{{true}}">
            <div style="display: flex; align-items: center; gap: 14px">
              <span aria-hidden="true" style="display: flex; padding-right: 8px"><sc-for list="{{@P@.held.faces}}" as="y" hint-placeholder-count="6"><span style="width: 28px; height: 28px; margin-right: -8px; border-radius: 999px; border: 2px solid #131519; background: {{y.c}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700">{{y.i}}</span></sc-for></span>
              <span class="num" style="flex: 1; font-size: 13px; color: #A1A8B3; white-space: nowrap">{{@P@.held.more}}</span>
              <span class="num" style="flex: none; font-size: 15px; font-weight: 600; white-space: nowrap">{{@P@.held.n}}</span>
            </div>
            <span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.5"><b style="color: #F4F5F7; font-weight: 600">{{@P@.held.line}}</b> · {{@P@.held.best}}</span>
          </sc-if>
          <sc-if value="{{@P@.held.none}}" hint-placeholder-val="{{false}}"><span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{@P@.held.line}}</span></sc-if>
        </section>'''

CONTROLS = '''    <section class="card enter d5" aria-labelledby="ctl-h@ID@" style="@CARD@; padding: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 16px">
      <div style="flex: 1 1 320px; display: flex; flex-direction: column; gap: 4px">
        <h2 id="ctl-h@ID@" style="margin: 0; font-size: 16px; font-weight: 600">Controls</h2>
        <span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">No stock-by-stock control. The engine applies a command within about 10 seconds.</span>
      </div>
      <sc-if value="{{paused}}" hint-placeholder-val="{{false}}">
        <span class="swap" role="status" style="font-size: 13px; color: #F5B64C; font-weight: 500">New buys paused · Kaibot still sells by its rules</span>
      </sc-if>
      <button type="button" class="press full-sm" onClick="{{togglePause}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #262A31; background: #1B1E24; color: #F4F5F7; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">{{pauseLabel}}</button>
      <sc-if value="{{notConfirming}}" hint-placeholder-val="{{true}}">
        <button type="button" class="press full-sm" onClick="{{askFlatten}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #5A2A2E; background: transparent; color: #FF6B6B; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Flatten and stop</button>
      </sc-if>
      <sc-if value="{{confirming}}" hint-placeholder-val="{{false}}">
        <div class="swap full-sm" role="alertdialog" aria-label="Confirm flatten and stop" style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px; background: #2A1517; border: 1px solid #5A2A2E; border-radius: 24px; padding: 6px 6px 6px 18px">
          <span style="flex: 1 1 200px; font-size: 14px; line-height: 1.4">{{@P@.controls.confirm}}</span>
          <button type="button" class="press" onClick="{{cancelFlatten}}" style="height: 44px; padding: 0 16px; border-radius: 999px; border: 0; background: #1B1E24; color: #F4F5F7; font: inherit; font-size: 14px; cursor: pointer">Cancel</button>
          <button type="button" class="press" onClick="{{cancelFlatten}}" style="height: 44px; padding: 0 16px; border-radius: 999px; border: 0; background: #FF6B6B; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 700; cursor: pointer">Flatten and stop</button>
        </div>
      </sc-if>
    </section>'''


def chart_block(svgs):
    """Legend with a read-out, the range buttons and one pre-drawn chart per range (desktop and phone size)."""
    charts = ''
    for key, flag in (('w', 'isW'), ('m', 'isM'), ('all', 'isAll')):
        sd, sm = svgs[key]
        charts += f'''
          <sc-if value="{{{{{flag}}}}}" hint-placeholder-val="{{{{{'true' if key == 'all' else 'false'}}}}}">
            <div class="hide-sm swap" onMouseMove="{{{{moveD}}}}" onMouseLeave="{{{{leave}}}}" onClick="{{{{moveD}}}}" style="cursor: crosshair">{sd}</div>
            <div class="show-sm swap" onClick="{{{{moveM}}}}">{sm}</div>
          </sc-if>'''
    return '''        <div role="group" aria-label="Chart range" style="display: flex; gap: 6px">
          <sc-for list="{{ranges}}" as="r" hint-placeholder-count="3">
            <button type="button" class="press tap" aria-pressed="{{r.pressed}}" onClick="{{r.pick}}" style="height: 36px; min-width: 56px; padding: 0 14px; border-radius: 999px; border: 0; background: {{r.bg}}; color: {{r.ink}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{r.label}}</button>
          </sc-for>
        </div>
        <figure class="chartcard" style="margin: 0; background: #131519; border: 1px solid #262A31; border-radius: 24px; padding: 18px 20px 12px; display: flex; flex-direction: column; gap: 10px">
          <figcaption style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; font-size: 13px; color: #A1A8B3">
            <span style="display: flex; align-items: center; gap: 8px"><svg width="18" height="4" aria-hidden="true"><line x1="0" y1="2" x2="18" y2="2" stroke="#4F95F0" stroke-width="3"></line></svg>Pot <b class="num" style="color: #F4F5F7; font-weight: 600">{{rd.pot}}</b></span>
            <span style="display: flex; align-items: center; gap: 8px"><svg width="18" height="4" aria-hidden="true"><line x1="0" y1="2" x2="18" y2="2" stroke="#C4862F" stroke-width="2" stroke-dasharray="4 3"></line></svg><span>S&amp;P 500<span class="hide-sm"> (SPY), same start</span></span><b class="num" style="color: #F4F5F7; font-weight: 600">{{rd.spy}}</b></span>
            <span class="num" aria-live="polite" style="flex: 1 1 auto">{{rd.when}}</span>
            <button type="button" class="press tap" onClick="{{tableTap}}" aria-pressed="{{tableS}}" style="height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid #262A31; background: transparent; color: #F4F5F7; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{tableLabel}}</button>
          </figcaption>
          <sc-if value="{{chartShown}}" hint-placeholder-val="{{true}}">
            <div role="group" tabindex="0" aria-roledescription="chart" aria-label="{{chartAria}}" onKeyDown="{{chartKey}}" style="border-radius: 12px">''' + charts + '''
            </div>
          </sc-if>
          <p class="num swap" style="margin: 0; font-size: 13px; color: #A1A8B3; line-height: 1.5">{{chartSay}}</p>
          <sc-if value="{{tableShown}}" hint-placeholder-val="{{false}}">
            <div role="table" aria-label="Pot and S&amp;P 500 by day" class="num swap" style="max-height: 300px; overflow: auto; font-size: 13px">
              <div role="row" style="display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; padding: 8px 4px; color: #A1A8B3; position: sticky; top: 0; background: #131519"><span role="columnheader">Day</span><span role="columnheader" style="text-align: right">Pot</span><span role="columnheader" style="text-align: right">S&amp;P 500, same start</span></div>
              <sc-for list="{{tableRows}}" as="t" hint-placeholder-count="6"><div role="row" style="display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; padding: 8px 4px; border-top: 1px solid #1B1E24"><span role="cell">{{t.d}}</span><span role="cell" style="text-align: right; font-weight: 600">{{t.p}}</span><span role="cell" style="text-align: right; color: #A1A8B3">{{t.s}}</span></div></sc-for>
            </div>
          </sc-if>
        </figure>'''


SKELETON = '''      <div aria-busy="true" aria-label="Loading the overview" style="display: flex; flex-direction: column; gap: 24px">
        <span class="sk" style="height: 64px; border-radius: 20px"></span>
        <div style="display: flex; flex-wrap: wrap; gap: 24px">
          <div style="flex: 999 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 14px">
            <span class="sk" style="width: 200px; height: 14px"></span>
            <span class="sk" style="width: 320px; max-width: 80%; height: 56px; border-radius: 14px"></span>
            <span class="sk" style="width: 100%; max-width: 560px; height: 40px"></span>
            <span class="sk show-sm" style="height: 300px; border-radius: 20px"></span>
            <div style="display: flex; gap: 6px"><span class="sk" style="width: 56px; height: 36px; border-radius: 999px"></span><span class="sk" style="width: 56px; height: 36px; border-radius: 999px"></span><span class="sk" style="width: 56px; height: 36px; border-radius: 999px"></span></div>
            <span class="sk" style="width: 100%; height: 300px; border-radius: 24px"></span>
          </div>
          <div class="side hide-sm" style="flex: 1 1 340px"><span class="sk" style="height: 250px; border-radius: 24px"></span><span class="sk" style="height: 170px; border-radius: 24px"></span><span class="sk" style="height: 140px; border-radius: 24px"></span></div>
        </div>
      </div>'''
SKEL_OVERVIEW = '    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">\n' + SKELETON + '\n    </sc-if>'


def overview_body(P, svgs, ident=''):
    """The Overview page for the view-model at path P."""
    t = '''  <main class="page" style="max-width: 1320px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 24px">
    <h1 class="sr">Overview</h1>
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
@ALARM@
@STATUS@
    <div style="display: flex; flex-wrap: wrap; gap: 24px">
      <section class="enter d1" aria-labelledby="pot-h@ID@" style="flex: 999 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 18px">
@HERO@
@ANSWERS@
@CHART@
      </section>
      <aside class="side hide-sm" style="flex: 1 1 340px; min-width: 0">
@INVESTED@
@NEXT_CARD@
@HELD_CARD@
      </aside>
    </div>
@CONTROLS@
    @FOOT@
    </sc-if>
  </main>
'''
    return sub(t, SKEL=SKEL_OVERVIEW, ALARM=ALARM, STATUS=STATUS, HERO=HERO, ANSWERS=ANSWERS, CHART=chart_block(svgs), INVESTED=INVESTED,
               NEXT_CARD=NEXT_CARD, HELD_CARD=HELD_CARD, CONTROLS=CONTROLS, METER=METER, MARKET=MARKET, P=P, ID=ident)


JS_OVERVIEW = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; this.state = { range: 'all', hover: null, table: false, paused: false, confirming: false, facts: null, more: false, ready: false }; }
@COMMON@
  renderVals() {
    const D = this.D, st = this.state; const R = D.chart[st.range]; const n = R.dates.length;
    const i = st.hover === null ? n - 1 : Math.max(0, Math.min(n - 1, st.hover));
    const at = (g) => (e) => { const b = e.currentTarget.getBoundingClientRect(); const x = (e.clientX - b.left) / b.width * g.w;
      this.setState({ hover: Math.round((x - g.l) / (g.w - g.l - g.r) * (n - 1)) }); };
    const open = st.facts === null ? D.ov.status.fS === 'true' : st.facts;
    return { ...this.common(), ov: { ...D.ov, status: { ...D.ov.status, fS: String(open), fCls: open ? '' : 'hide-sm', fChev: open ? 'open-chev' : '', tap: () => this.setState({ facts: !open }) } },
      ranges: [['w', '1W'], ['m', '1M'], ['all', 'All']].map(([k, l]) => ({ label: l, pressed: String(st.range === k), bg: st.range === k ? '#F4F5F7' : '#131519', ink: st.range === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ range: k, hover: null }) })),
      isW: st.range === 'w', isM: st.range === 'm', isAll: st.range === 'all',
      hv: { o: st.hover === null ? 0 : 1, xd: R.d.x[i], pd: R.d.p[i], sd: R.d.s[i], xm: R.m.x[i], pm: R.m.p[i], sm: R.m.s[i] },
      rd: { pot: R.pot[i], spy: R.spy[i], when: st.hover === null ? 'now' : R.dates[i] },
      moveD: at(R.d), moveM: at(R.m), leave: () => this.setState({ hover: null }),
      chartKey: (e) => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); this.setState({ hover: Math.max(0, Math.min(n - 1, i + (e.key === 'ArrowLeft' ? -1 : 1))) }); } else if (e.key === 'Escape') this.setState({ hover: null }); },
      chartAria: R.aria + ' Use the left and right arrow keys to read a day.', chartSay: R.say,
      chartShown: !st.table, tableShown: st.table, tableS: String(st.table), tableLabel: st.table ? 'Show chart' : 'Show table', tableTap: () => this.setState({ table: !st.table }),
      tableRows: R.dates.map((x, k) => ({ d: x, p: R.pot[k], s: R.spy[k] })).reverse(),
      paused: st.paused, pauseLabel: st.paused ? 'Resume new buys' : 'Pause new buys', togglePause: () => this.setState({ paused: !st.paused }),
      confirming: st.confirming, notConfirming: !st.confirming, askFlatten: () => this.setState({ confirming: true }), cancelFlatten: () => this.setState({ confirming: false }),
    };
  }
}'''


def build():
    ranges, svgs = chart_ranges()
    data = dict(ov=overview_vm(), chart=ranges)
    ui.setup('Home')
    body = overview_body('ov', svgs)
    sc = script(JS_OVERVIEW, data)
    ui.write('Overview', 'Overview', 'Home', body, sc, 1160, PROPS)
    ui.write('OverviewMobile', 'Overview on a phone', 'Home', body, sc, 844, PROPS, width=390)
    return data, svgs
