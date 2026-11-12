"""M7 · the states that are not "normal": loading, empty, halted and error. Built by multiday.py.

Two sheets. The desktop sheet stacks each state at full width with a caption. The phone sheet shows eight phone
frames side by side. Both reuse the templates of the real screens with a different view-model, so a state can never
drift away from the screen it belongs to. Nothing here is interactive.
"""
import copy
import re
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_holdings as hold  # noqa: E402
import m7_next as nxt  # noqa: E402
import m7_overview as ov  # noqa: E402
import m7_ui as ui  # noqa: E402
import shell  # noqa: E402
from m7_ui import IC, TONE, script, static, sub  # noqa: E402


# ================================================================== view-models per state

def base():
    return copy.deepcopy(ov.overview_vm())


def vm_empty():
    v = base()
    v['status'] = ov.status(bad={'stops': ('ok', 'None needed yet')})
    v['hero'].update(value=d.usd(d.START), asof='Nothing bought yet.')
    grey = '#A1A8B3'
    v['stats'] = [dict(label=f'Since {d.short(d.D0)}', value='No change yet', ink=grey, sub=''),
                  dict(label='Today', value='No change', ink=grey, sub=''),
                  dict(label='S&P 500 (SPY), same start', value='Starts at the first close', ink=grey, sub='')]
    v['inv'] = ov.invested(0, 0, d.START, by_rule=False)
    v['next'] = ov.next_close(lines=[], short='First check Wed 6 Jan', away='nothing planned',
                              foot='Steady risers first checks its list on Wed 6 Jan, after the close. What it picks is bought at Thursday’s close.')
    v['held'] = ov.held_summary([])
    return v


def vm_halted():
    v = base()
    pot = d.PREV_POT * (1 - 0.0162)
    chg, gap = pot - d.PREV_POT, -61.30
    since = pot - d.START
    spy = d.SPY_PREV * (1 - 0.021)
    v['alarm'] = ov.alarm('warn', 'Buying is stopped for today',
                          f'The pot is down 1.62% since yesterday’s close; the daily loss limit is {d.DAILY_LIMIT}%. Tonight’s 3 buys are cancelled. '
                          'Sales still go ahead and every stock keeps its safety stop. Buying is allowed again tomorrow.')
    v['status'] = ov.status('warn', 'Buying stopped today', 'Daily loss limit reached')
    v['hero'].update(value=d.usd(pot))
    v['stats'] = [dict(label=f'Since {d.short(d.D0)}', value=d.delta(since, since / d.START * 100), ink=d.ink(since), sub=''),
                  dict(label='Today', value=d.delta(chg, chg / d.PREV_POT * 100), ink=d.ink(chg),
                       sub=f'Overnight\u00a0{d.signed(gap)} · since the open\u00a0{d.signed(chg - gap)}'),
                  dict(label='S&P 500 (SPY), same start', value=f'{d.arrow(1)} {d.pct((spy / d.START - 1) * 100)}', ink='#A1A8B3',
                       sub=f'The pot is {d.usd(spy - pot)} behind')]
    v['next'] = ov.next_close(lines=[dict(k='Sell 2', names='XOM, GM'), dict(k='Trim 1', names='NEM'), dict(k='Cancelled', names='UBER, CVS, 1 more WMT')],
                              foot='3 orders are at the broker. 3 buys were cancelled by the daily loss limit.', short='2 sells · 1 trim · 3 buys cancelled')
    return v


def vm_wait():
    v = base()
    v['status'] = ov.status(subline='No alarms · the market check says wait')
    v['inv'] = ov.invested(d.INVESTED / d.POT * 100, len(d.ROWS), d.CASH, market_ok=False)
    v['next'] = ov.next_close(lines=[dict(k='Sell 2', names='XOM, GM'), dict(k='Trim 1', names='NEM'), dict(k='On hold', names='UBER, CVS, 1 more WMT')],
                              foot='3 orders are at the broker. Buys wait until the S&P 500 is back above its 200-day average.',
                              short='2 sells · 1 trim · 3 buys on hold')
    return v


def vm_offline():
    v = base()
    v['alarm'] = ov.alarm('warn', 'Kaibot’s engine is offline',
                          'Last seen at 17:02, 3 h 12 min ago. Your 12 stocks keep their safety stops, and tonight’s 6 orders are already at the '
                          'broker, so they still trade at the close. The engine restarts by itself; if it is not back in a few minutes, start it on your Mac.',
                          action='How to start it')
    v['status'] = ov.status('warn', 'Engine offline', 'Last seen 17:02', bad={
        'engine': ('warn', 'Offline · last seen 17:02'), 'stops': ('off', '12 of 12 at 17:02'), 'books': ('off', 'Matched at the close')})
    v['hero'].update(ink='#A1A8B3', asof='Prices as of 17:02 Berlin, when the engine was last seen.')
    return v


def vm_stop():
    v = base()
    v['alarm'] = ov.alarm('crit', 'One stock has no safety stop',
                          'General Motors (GM) has no safety stop at the broker. Kaibot is placing it again now. If this message is still here in a few '
                          'minutes, open Engine health.', action='Engine health')
    v['status'] = ov.status('crit', 'Needs attention', '1 safety stop is missing', bad={'stops': ('crit', '11 of 12 · GM is missing')})
    return v


def vm_books():
    v = base()
    v['alarm'] = ov.alarm('crit', 'Kaibot’s books and the broker disagree',
                          'Kaibot counts 3 shares of General Motors (GM); the broker counts 2. Buying is stopped until the two match again. '
                          'Every stock keeps its safety stop.', action='Engine health')
    v['status'] = ov.status('crit', 'Needs attention', 'Books do not match the broker', bad={'books': ('crit', 'GM: 3 here, broker 2')})
    return v


def vm_quiet_next():
    v = nxt.next_vm()
    v.update(day='Tonight, Fri 12 Mar', away='in 1 h 46 min', hasPlan=False, noPlan=True, groups=[],
             steps=[dict(icon=IC['ok'], ink=TONE['ok'][0], k='Decided', v='Thu 11 Mar, 22:19', s='nothing to do'),
                    dict(icon=IC['off'], ink='#A1A8B3', k='At the broker', v='No orders', s='only the 12 safety stops'),
                    dict(icon=IC['clock'], ink='#7FB2FF', k='Next check', v='Tonight, after 22:00', s='as every trading day')],
             stepsShort='Decided Thu 11 Mar, 22:19: nothing to do. No orders are at the broker, only the 12 safety stops.',
             after='Stays at 12 stocks, about 53% invested.',
             emptyText='Kaibot checked after last night’s close and found nothing to do. Most days are like this. Steady risers checks its list next on '
                       'Wed 17 Mar, 12-month winners on Thu 1 Apr. Every stock keeps its safety stop.')
    return v


def states_vm():
    empty_hold = hold.holdings_vm([])
    empty_hold.update(subShort='No stocks · all of the pot is cash')
    stale = base()
    stale['hero'].update(ink='#A1A8B3', asof='Loaded at 19:58 Berlin. It may be out of date.')
    return dict(empty=vm_empty(), halted=vm_halted(), wait=vm_wait(), offline=vm_offline(), stop=vm_stop(), books=vm_books(),
                stale=stale, hold=empty_hold, quiet=vm_quiet_next())


# ================================================================== pieces

CAP = '''      <div style="display: flex; flex-direction: column; gap: 6px; padding-top: 22px; border-top: 1px dashed #3A404A">
        <div style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px"><span style="font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #0A0B0D; background: #A1A8B3; border-radius: 999px; padding: 3px 9px">@KIND@</span><h2 style="margin: 0; font-size: 20px; font-weight: 600">@TITLE@</h2></div>
        <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5; max-width: 900px">@TEXT@</span>
      </div>'''

NO_CHART = '''        <figure class="chartcard" style="margin: 0; border: 1px dashed #3A404A; border-radius: 24px; padding: 28px 24px; min-height: 150px; display: flex; flex-direction: column; justify-content: center; gap: 6px">
          <b style="font-size: 16px; font-weight: 600">No history yet</b>
          <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5; max-width: 520px">The pot line starts at the first close. From then on it runs next to the S&amp;P 500 line, from the same day and the same amount.</span>
        </figure>'''

LOAD_ERROR = '''      <div role="alert" style="@CARD@; padding: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 12px 18px">
        @ICON@
        <div style="flex: 1 1 260px; min-width: 0; display: flex; flex-direction: column; gap: 4px"><b style="font-size: 16px; font-weight: 600">Couldn’t load the latest numbers</b><span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">The dashboard cannot reach the database. What you see was loaded at 19:58. Trading is not affected: the engine keeps its own records and the safety stops rest at the broker.</span></div>
        <button type="button" class="press full-sm" style="height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Try again</button>
      </div>'''

PAUSED = '''      <section class="card" aria-label="Controls while paused" style="@CARD@; padding: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 16px">
        <div style="flex: 1 1 320px; display: flex; flex-direction: column; gap: 4px">
          <h3 style="margin: 0; font-size: 16px; font-weight: 600">Controls</h3>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">No stock-by-stock control. The engine applies a command within about 10 seconds.</span>
        </div>
        <span role="status" style="font-size: 13px; color: #F5B64C; font-weight: 500">New buys paused · Kaibot still sells by its rules</span>
        <button type="button" class="press full-sm" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #262A31; background: #1B1E24; color: #F4F5F7; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Resume new buys</button>
        <button type="button" class="press full-sm" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #5A2A2E; background: transparent; color: #FF6B6B; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Flatten and stop</button>
      </section>'''


def cap(kind, title, text):
    return sub(CAP, KIND=kind, TITLE=title, TEXT=text)


def top(P, ident, side=('INVESTED', 'NEXT_CARD', 'HELD_CARD'), chart='', hero=True, alarm=True):
    """The top of the Overview for one state: banner, status, pot and the side cards asked for."""
    cards = '\n'.join(getattr(ov, c) for c in side)
    left = ('''        <section aria-labelledby="pot-h@ID@" style="flex: 999 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 18px">
@HERO@
@ANSWERS@
@CHART@
        </section>''' if hero else '')
    aside = f'        <aside class="side hide-sm" style="flex: 1 1 340px; min-width: 0">\n{cards}\n        </aside>' if side else ''
    t = (('@ALARM@\n' if alarm else '') + '@STATUS@\n' + ('      <div style="display: flex; flex-wrap: wrap; gap: 24px">\n' + left + '\n' + aside + '\n      </div>' if (hero or side) else ''))
    return static(sub(t, ALARM=ov.ALARM, STATUS=ov.STATUS, HERO=ov.HERO, ANSWERS=ov.ANSWERS, CHART=chart, METER=ov.METER, MARKET=ov.MARKET, P=P, ID=ident))


def desk_sheet():
    warn = ui.icon(IC['crit'], 22, TONE['crit'][0])
    strips = [
        cap('Loading', 'While the numbers load', 'Grey shapes stand where the content will be, in the same layout, so nothing jumps when it arrives. '
            'A number is never shown as zero while it is unknown.'),
        ov.SKELETON,
        cap('Empty', 'A new run: nothing bought yet', 'The pot is all cash, the invested bar is at 0% and the page says when the first check happens. '
            'The chart area explains itself instead of showing an empty frame.'),
        top('st.empty', 'e', chart=NO_CHART),
        cap('Empty', 'No stocks yet, and nothing planned tonight', 'The Holdings list and the Next close plan say in one or two sentences why they are '
            'empty and what happens next. “Nothing planned” is the usual state: a weekly rule trades about once a week.'),
        '      <div class="two" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)">\n' + static(hold.holdings_list('st.hold'))
        + '\n' + static(nxt.plan_block('st.quiet', 'q')) + '\n      </div>',
        cap('Stopped', 'Buying stopped for today: the daily loss limit', 'An amber banner under the header on every page, in plain words: what happened, '
            'what is cancelled, what still goes ahead. The headline changes from “All good” to “Buying stopped today”. Holdings are not sold.'),
        top('st.halted', 'h', side=('NEXT_CARD',)),
        cap('Stopped', 'The market check says wait', 'Not an alarm, so no banner. The Invested card turns its market line amber with a pause icon and '
            'says which rule is waiting; the Next close card shows the buys as on hold.'),
        '      <div style="display: flex; flex-wrap: wrap; gap: 24px">\n        <div class="side" style="flex: 1 1 340px; max-width: 420px">\n'
        + static(sub(ov.INVESTED, METER=ov.METER, MARKET=ov.MARKET, P='st.wait', ID='w')) + '\n        </div>\n        <div class="side" style="flex: 1 1 340px; max-width: 420px">\n'
        + static(sub(ov.NEXT_CARD, P='st.wait', ID='w')) + '\n        </div>\n      </div>',
        cap('Stopped', 'You paused new buys', 'The Controls card stays where it is today, at the bottom of Home. While paused it says so in words and '
            'the button reads “Resume new buys”. “Flatten and stop” keeps its place and its two-step confirmation.'),
        sub(PAUSED),
        cap('Problem', 'The engine is offline', 'Amber banner: when it was last seen, what protects the holdings meanwhile and what to do. The pot turns '
            'grey and carries the time of its prices, because it may be out of date. The header chip reads “Offline”.'),
        top('st.offline', 'o', side=()),
        cap('Problem', 'A safety stop is missing', 'Red banner with a cross icon and the word “Needs attention”. This is the one state where a holding '
            'is unprotected, so it is the loudest. The failing check is named.'),
        top('st.stop', 's', side=(), hero=False),
        cap('Problem', 'Kaibot’s books and the broker disagree', 'Red banner. Buying stops until the two match again.'),
        top('st.books', 'b', side=(), hero=False),
        cap('Problem', 'The dashboard cannot load', 'The screen keeps the last numbers it had, says how old they are and offers one button. It also says '
            'that trading is not affected, because that is the first worry.'),
        sub(LOAD_ERROR, ICON=warn),
    ]
    return '''  <main class="page" style="max-width: 1320px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 22px">
    <div style="display: flex; flex-direction: column; gap: 6px">
      <div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">States</h1>''' + ui.SAMPLE + '''</div>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5">How the screens look when things are not normal: while loading, when empty, when buying is stopped and when something is wrong. Each strip is the real screen with different data.</span>
    </div>
''' + '\n'.join(strips) + '\n    ' + ui.FOOT + '\n  </main>\n'


# ------------------------------------------------------------------ phone frames

def phone_bar(offline=False):
    bar = re.search(r'<header class="bar".*?</header>', ui.page_head('x', 'Home'), re.S).group(0)
    if offline:
        bar = bar.replace('class="live-dot" aria-hidden="true" style="width: 8px; height: 8px; background: #3DDC97"',
                          'aria-hidden="true" style="width: 8px; height: 8px; border-radius: 999px; background: #A1A8B3"').replace('>Running<', '>Offline<')
    return bar


def frame(kind, title, inner, tab='Home', offline=False):
    """One phone, 390 by 844, cut off at the bottom of the first screen like a real phone."""
    return f'''      <figure style="margin: 0; display: flex; flex-direction: column; gap: 12px">
        <figcaption style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; min-height: 48px"><span style="font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: #0A0B0D; background: #A1A8B3; border-radius: 999px; padding: 3px 9px">{kind}</span><b style="font-size: 16px; font-weight: 600">{title}</b></figcaption>
        <div class="phone" style="position: relative; width: 390px; height: 844px; overflow: hidden; transform: translateZ(0); border-radius: 28px; border: 1px solid #3A404A; background: #0A0B0D">
          {phone_bar(offline)}
          <main class="page" style="box-sizing: border-box; display: flex; flex-direction: column; gap: 16px">
{inner}
          </main>
{ui.tabbar(tab, live=False)}        </div>
      </figure>'''


def phone_sheet():
    title = ('<div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 30px; font-weight: 700; letter-spacing: -0.03em">@T@</h1>'
             + ui.SAMPLE + '</div><span class="num" style="font-size: 15px; color: #A1A8B3; line-height: 1.5">@S@</span>')
    hold_page = (title.replace('@T@', 'Holdings').replace('@S@', '{{st.hold.subShort}}') + static(hold.holdings_list('st.hold')))
    next_page = (title.replace('@T@', 'Next close').replace('@S@', '{{st.quiet.how}}') + static(nxt.when_block('st.quiet', 'pq'))
                 + static(nxt.plan_block('st.quiet', 'pq')))
    warn = ui.icon(IC['crit'], 22, TONE['crit'][0])
    frames = [
        frame('Loading', 'While the numbers load', ov.SKELETON),
        frame('Empty', 'A new run: nothing bought yet', top('st.empty', 'pe', side=(), chart=NO_CHART)),
        frame('Empty', 'Nothing planned tonight', next_page, tab='Next'),
        frame('Empty', 'No stocks yet', hold_page, tab='Holdings'),
        frame('Stopped', 'Buying stopped: daily loss limit', top('st.halted', 'ph', side=())),
        frame('Stopped', 'The market check says wait', top('st.wait', 'pw', side=())),
        frame('Problem', 'The engine is offline', top('st.offline', 'po', side=()), offline=True),
        frame('Problem', 'The dashboard cannot load', sub(LOAD_ERROR, ICON=warn) + '\n' + top('st.stale', 'pl', side=(), alarm=False)),
    ]
    return '''  <main style="box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 24px">
    <div style="display: flex; flex-direction: column; gap: 6px">
      <h1 style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">States on a phone</h1>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5">The first screen of each state, as a 390 by 844 phone shows it. Sample data.</span>
    </div>
    <div style="display: grid; grid-template-columns: repeat(4, 390px); gap: 36px 40px">
''' + '\n'.join(frames) + '\n    </div>\n  </main>\n'


JS = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; }
  renderVals() { return { st: this.D.st }; }
}'''


def build():
    data = dict(st=states_vm())
    sc = script(JS, data)
    ui.setup('Home')
    shell.TAB = ui.tabbar('Home', live=False)                  # a sheet: nothing on it reacts
    ui.write('MultidayStates', 'States', '-', desk_sheet(), sc, 4960)
    keep = shell.CSS
    shell.CSS = shell.CSS + '\n' + ui.phone_scope(shell.CSS) + '\n.phone .bar{border-bottom:1px solid #1B1E24}\n'
    shell.TAB = ''
    out = ui.write('MultidayStatesMobile', 'States on a phone', '-', phone_sheet(), sc, 2050, width=1760)
    shell.CSS = keep
    bar = re.search(r'  <header class="bar".*?</header>\n', out, re.S).group(0)       # a sheet of phones needs no desktop top bar
    open('../project/v3/MultidayStatesMobile.dc.html', 'w').write(out.replace(bar, '', 1))
