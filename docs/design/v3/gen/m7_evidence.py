"""M7 · Evidence: where each rule stands in gates A to D (a reason, ten years of history, three months on paper, real
money), in plain words. Built by multiday.py. Every verdict and number on this board is invented sample data.
"""
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_ui as ui  # noqa: E402
from m7_ui import IC, PROPS, TONE, script, sub  # noqa: E402

GATES = [('A', 'A reason', 'Written down before any test, with published research behind it.'),
         ('B', 'Ten years of history', 'Tested on ten years of real prices, after costs: against the S&P 500 and against picking at random.'),
         ('C', 'Three months on paper', 'At least 3 months and 60 trading days of paper trading that behaves like the test.'),
         ('D', 'Real money', 'Your decision, never automatic. It starts small.')]
STEP_ICON = {'ok': IC['ok'], 'info': IC['clock'], 'off': IC['off'], 'crit': IC['crit']}


# ================================================================== view-model

def step(k, tone, label, part=None):
    return dict(k=k, name=dict((g[0], g[1]) for g in GATES)[k], label=label, icon=STEP_ICON[tone], ink=TONE[tone][0],
                bd=TONE['info'][2] if tone == 'info' else '#262A31', hasBar=part is not None, w=f'{(part or 0) * 100:.0f}%',
                word={'ok': 'passed', 'info': 'in progress', 'off': 'not started', 'crit': 'failed'}[tone])


def mark(tone, text):
    return dict(icon=STEP_ICON[tone], ink=TONE[tone][0], text=text)


def paper_rule(key, done, end, written, years, grew, fall, beat, paper_fall, allowed):
    rule = d.RULES[key]
    return dict(
        key=key, name=rule['name'], what=rule['what'], tag=f"On paper since {d.short(rule['since'])}",
        steps=[step('A', 'ok', 'Passed'), step('B', 'ok', 'Passed'), step('C', 'info', f'{done} of 60 days', done / 60), step('D', 'off', 'Not started')],
        line=f"Gate C can finish on {d.day(end)} at the earliest. After that the decision about real money is yours.",
        detail=[
            dict(title='A · A reason', marks=[
                mark('ok', f'The plan was written down on {written}, before the first test.'),
                mark('ok', 'Published research supports this kind of rule.'),
                mark('ok', 'The settings were fixed in advance, with 5 nearby settings named for the check.')]),
            dict(title='B · Ten years of history', marks=[
                mark('ok', f'It made money after costs over the ten years, and in {years} of the 10 years (needs 6).'),
                mark('ok', f'It grew {grew}% a year; the S&P 500 grew {d.SPY_10Y}%. Its worst fall was {fall}%; the S&P 500’s was {d.SPY_FALL}%.'),
                mark('ok', f'It beat {beat} of 100 portfolios picked at random with the same habits (needs 90).'),
                mark('ok', 'It still passes when trading costs are doubled.'),
                mark('ok', 'It passes on the stocks that were really in the index at the time.'),
                mark('ok', 'All 5 nearby settings made money too.')]),
            dict(title='C · Three months on paper', marks=[
                mark('info', f'{done} of 60 trading days done. Three months are up on {d.day(end)}.'),
                mark('ok', f'Kaibot’s books matched the broker on all {done} days.'),
                mark('ok', 'Its daily results stay close to the test run over the same days.'),
                mark('ok', f'Worst fall on paper so far: {paper_fall}%. The test allows up to {allowed}%.'),
                mark('ok', 'The rule for stopping it early was written down before the start.')]),
            dict(title='D · Real money', marks=[
                mark('off', 'Not started. Kaibot never moves to real money by itself: when gate C is passed, you get the numbers and decide.')]),
        ])


def evidence_vm():
    return dict(
        sub='A rule has to pass four gates, in this order, before real money. Kaibot cannot skip one.',
        sample='Sample data. The verdicts and ten-year numbers on this screen are invented to show the layout; the real tests are still running.',
        gates=[dict(k=k, name=n, text=t) for k, n, t in GATES],
        paper=[paper_rule('r1', d.C1_DONE, d.C1_END, '14 Dec 2026', 8, 12.4, 21, 93, 3.2, 9.5),
               paper_rule('r2', d.C2_DONE, d.C2_END, '11 Jan 2027', 9, 13.1, 24, 91, 2.4, 11)],
        others=[
            dict(name='S&P 500 day moves', tag='Being tested', tagTone='info',
                 text='Buys the S&P 500 fund on days it breaks out of its usual range and sells by the close.',
                 steps=[step('A', 'ok', 'Passed'), step('B', 'info', 'Being tested'), step('C', 'off', 'Not started'), step('D', 'off', 'Not started')]),
            dict(name='S&P 500 day moves, kept overnight', tag='Waiting', tagTone='off',
                 text='The same rule, but it keeps the fund until the next morning. Tested once the rule above has its verdict.',
                 steps=[step('A', 'ok', 'Passed'), step('B', 'off', 'Not started'), step('C', 'off', 'Not started'), step('D', 'off', 'Not started')]),
            dict(name='Intraday dip rule', tag='Retired', tagTone='crit',
                 text='Failed gate B and was retired on 12 Nov 2026. Over ten years it lost 59% while the S&P 500 rose 209%.',
                 steps=[step('A', 'ok', 'Passed'), step('B', 'crit', 'Failed'), step('C', 'off', 'Not started'), step('D', 'off', 'Not started')]),
        ],
        oneShots='Five smaller ideas each get one ten-year test and are kept only if they pass. 2 were tested and dropped; 3 are waiting.',
    )


# ================================================================== templates

STEPS = '''              <ol class="gsteps" aria-label="Gates A to D" style="list-style: none; margin: 0; padding: 0">
                <sc-for list="{{@R@.steps}}" as="s" hint-placeholder-count="4">
                  <li class="gstep" style="min-width: 0; display: flex; flex-direction: column; gap: 6px; padding: 12px; border-radius: 14px; background: #0A0B0D; border: 1px solid {{s.bd}}">
                    <span style="display: flex; align-items: center; gap: 6px; min-width: 0"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{s.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none"><path d="{{s.icon}}"></path></svg><b style="font-size: 14px; font-weight: 700">{{s.k}}</b><span class="hide-sm" style="min-width: 0; font-size: 12px; color: #A1A8B3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{s.name}}</span></span>
                    <b class="num" style="font-size: 14px; font-weight: 600; line-height: 1.3">{{s.label}}<span class="sr">: {{s.word}}</span></b>
                    <sc-if value="{{s.hasBar}}" hint-placeholder-val="{{false}}"><span aria-hidden="true" style="position: relative; height: 4px; border-radius: 999px; background: #243A5C"><span style="position: absolute; left: 0; top: 0; bottom: 0; width: {{s.w}}; border-radius: 999px; background: #4F95F0"></span></span></sc-if>
                  </li>
                </sc-for>
              </ol>'''

RULE = '''        <sc-for list="{{rules}}" as="r" hint-placeholder-count="2">
          <section class="card enter d2" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 14px">
            <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px">
              <h3 style="margin: 0; flex: 1 1 auto; font-size: 20px; font-weight: 600">{{r.name}}</h3>
              <span class="num" style="font-size: 12px; font-weight: 600; color: #7FB2FF; background: #16263D; border: 1px solid #2D4F7A; border-radius: 999px; padding: 4px 10px; white-space: nowrap">{{r.tag}}</span>
              <span style="flex: 1 1 100%; font-size: 14px; color: #A1A8B3; line-height: 1.5">{{r.what}}</span>
            </div>
@STEPS@
            <span class="num" style="font-size: 14px; line-height: 1.5">{{r.line}}</span>
            <button type="button" class="press" aria-expanded="{{r.openS}}" onClick="{{r.tap}}" style="align-self: flex-start; display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 16px 0 18px; border-radius: 999px; border: 1px solid #262A31; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{r.btn}}<svg class="chev {{r.chev}}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#A1A8B3" stroke-width="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg></button>
            <sc-if value="{{r.open}}" hint-placeholder-val="{{true}}">
              <div class="expand checks">
                <sc-for list="{{r.detail}}" as="g" hint-placeholder-count="4">
                  <div style="min-width: 0; display: flex; flex-direction: column; gap: 8px">
                    <h4 style="margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">{{g.title}}</h4>
                    <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px">
                      <sc-for list="{{g.marks}}" as="m" hint-placeholder-count="4"><li class="num" style="display: flex; gap: 8px; font-size: 14px; line-height: 1.45"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="{{m.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex: none; margin-top: 2px"><path d="{{m.icon}}"></path></svg><span>{{m.text}}</span></li></sc-for>
                    </ul>
                  </div>
                </sc-for>
              </div>
            </sc-if>
          </section>
        </sc-for>'''

OTHERS = '''        <section class="card pad-sm enter d4" style="@CARD@; padding: 8px 22px">
          <ul style="list-style: none; margin: 0; padding: 0">
            <sc-for list="{{ev.others}}" as="o" hint-placeholder-count="3">
              <li class="orow" style="padding: 18px 0; border-bottom: 1px solid #1B1E24">
                <div style="min-width: 0; display: flex; flex-direction: column; gap: 4px">
                  <span style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px"><b style="font-size: 16px; font-weight: 600">{{o.name}}</b><span style="font-size: 12px; font-weight: 600; color: {{o.tagInk}}; background: {{o.tagBg}}; border: 1px solid {{o.tagBd}}; border-radius: 999px; padding: 2px 9px; white-space: nowrap">{{o.tag}}</span></span>
                  <span class="num" style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{o.text}}</span>
                </div>
                <ol aria-label="Gates A to D" style="list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px">
                  <sc-for list="{{o.steps}}" as="s" hint-placeholder-count="4"><li style="min-width: 0; display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 8px 10px; border-radius: 12px; background: #0A0B0D; border: 1px solid {{s.bd}}"><span style="display: flex; align-items: center; gap: 5px"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="{{s.ink}}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{{s.icon}}"></path></svg><b style="font-size: 13px; font-weight: 700">{{s.k}}</b></span><span class="num" style="font-size: 12px; color: #A1A8B3; line-height: 1.3">{{s.label}}</span></li></sc-for>
                </ol>
              </li>
            </sc-for>
          </ul>
          <p style="margin: 0; padding: 16px 0; font-size: 14px; color: #A1A8B3; line-height: 1.5">{{ev.oneShots}}</p>
        </section>'''

SKEL = '''    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">
      <div aria-busy="true" aria-label="Loading" style="display: flex; flex-direction: column; gap: 20px">
        <span class="sk" style="width: 240px; height: 40px; border-radius: 12px"></span>
        <span class="sk" style="height: 130px; border-radius: 24px"></span>
        <span class="sk" style="height: 320px; border-radius: 24px"></span>
        <span class="sk" style="height: 220px; border-radius: 24px"></span>
      </div>
    </sc-if>'''

PAGE = '''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 64px; display: flex; flex-direction: column; gap: 20px">
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
    <div class="enter" style="display: flex; flex-direction: column; gap: 6px">
      <div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Evidence</h1>@SAMPLE@</div>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5">{{ev.sub}}</span>
    </div>
    <p class="enter" role="note" style="margin: 0; padding: 12px 16px; border-radius: 14px; border: 1px dashed #3A404A; font-size: 14px; color: #A1A8B3; line-height: 1.5">{{ev.sample}}</p>
    <section class="card enter d1" aria-labelledby="gt-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 14px">
      <h2 id="gt-h" style="margin: 0; font-size: 18px; font-weight: 600">The four gates</h2>
      <ol class="gates" style="list-style: none; margin: 0; padding: 0">
        <sc-for list="{{ev.gates}}" as="g" hint-placeholder-count="4">
          <li style="min-width: 0; display: flex; flex-direction: column; gap: 6px"><span style="display: flex; align-items: center; gap: 8px"><span aria-hidden="true" style="flex: none; width: 28px; height: 28px; border-radius: 999px; background: #1B1E24; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700">{{g.k}}</span><b style="font-size: 15px; font-weight: 600"><span class="sr">Gate {{g.k}}: </span>{{g.name}}</b></span><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{g.text}}</span></li>
        </sc-for>
      </ol>
    </section>
    <h2 class="enter d2" style="margin: 8px 0 -6px; padding: 0 4px; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">Trading on paper now</h2>
@RULE@
    <h2 class="enter d3" style="margin: 8px 0 -6px; padding: 0 4px; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">Not trading</h2>
@OTHERS@
    @FOOT@
    </sc-if>
  </main>
'''

JS = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.D = @DATA@; this.state = { open: { r1: false, r2: false }, more: false, ready: false }; }
@COMMON@
  renderVals() {
    const ev = this.D.ev, st = this.state; const T = this.D.tone;
    return { ...this.common(), ev: { ...ev, others: ev.others.map((o) => ({ ...o, tagInk: T[o.tagTone][0], tagBg: T[o.tagTone][1], tagBd: T[o.tagTone][2] })) },
      rules: ev.paper.map((r) => { const on = !!st.open[r.key];
        return { ...r, open: on, openS: String(on), chev: on ? 'open-chev' : '', btn: on ? 'Hide the checks' : 'Show the checks', tap: () => this.setState({ open: { ...st.open, [r.key]: !on } }) }; }),
    };
  }
}'''


def build():
    ui.setup('Evidence')
    body = sub(PAGE, SKEL=SKEL, RULE=sub(RULE, STEPS=STEPS, R='r'), OTHERS=OTHERS)
    sc = script(JS, dict(ev=evidence_vm(), tone={k: list(v) for k, v in TONE.items()}))
    ui.write('Evidence', 'Evidence', 'Evidence', body, sc, 1620, PROPS)
    ui.write('EvidenceMobile', 'Evidence on a phone', 'Evidence', body, sc, 844, PROPS, width=390)
