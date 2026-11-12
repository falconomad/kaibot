"""M7 · Holding settings (issue #132, S92): the owner's tighten-only settings for a multi-day book. Desktop and phone.

Run from this folder: python3 m7_settings.py (also built by multiday.py).
Writes ../project/v3/HoldingSettings.dc.html and HoldingSettingsMobile.dc.html.

Every setting can only make Kaibot more careful than the level (Balanced in the sample). The level values are the
PROVISIONAL ones in config/profiles.yaml (multi_day.levels.balanced) and the Balanced RiskPolicy; the hard ceilings
behind them are BOOK_HARD_CEILINGS and HARD_CEILINGS in src/kaibot/risk/gate.py. Neither file is changed here: the
level limits for multi-day books stay with the stakeholder (decision 15 section 6). If a level limit is later set
lower than an owner's value, the owner's value follows it down.
"""
import sys

sys.path.insert(0, '.')
import m7_data as d  # noqa: E402
import m7_ui as ui  # noqa: E402
import shell  # noqa: E402
from m7_ui import IC, TONE, icon, script, sub  # noqa: E402

# ================================================================== sample data

# key, label, help, unit, decimals, level value (provisional), step, lowest, highest, saved owner value, kind, group
# kind "cap": the owner may only lower it (highest = the level value). kind "floor": only raise it (lowest = level).
SETTINGS = [
    ('gross', 'Most of the pot invested', 'Everything the rules hold together. The rest stays in cash.', '%', 0, 100, 5, 10, 100, d.LIMIT_INVESTED, 'cap', 'book'),
    ('names', 'Most stocks at once', 'Both rules together.', '', 0, 20, 1, 1, 20, 20, 'cap', 'book'),
    ('name', 'Most in one stock', 'Share of the pot, both rules together.', '%', 0, 15, 1, 1, 15, d.LIMIT_STOCK, 'cap', 'book'),
    ('sector', 'Most in one sector', 'Share of the pot in one sector, for example technology.', '%', 0, 30, 5, 5, 30, 30, 'cap', 'book'),
    ('cash', 'Cash always kept aside', 'Never invested. This one can only go up.', '%', 0, 2, 1, 2, 50, 2, 'floor', 'book'),
    ('risk', 'Risk per stock', 'A normal day’s move in one stock costs at most this share of the pot. Lower means smaller positions.', '%', 2, 0.10, 0.01, 0.02, 0.10, 0.10, 'cap', 'more'),
    ('gap', 'Loss on a bad morning', 'Kaibot tests a bad morning: every stock opens 10% lower, or three normal days lower if that is more. The pot may lose at most this.', '%', 0, 12, 1, 2, 12, 12, 'cap', 'more'),
    ('daily', 'Stop buying for the day after a loss of', 'Counts the overnight move. Stocks you hold keep their safety stops.', '%', 2, 1.5, 0.25, 0.25, 1.5, 1.5, 'cap', 'stop'),
    ('run', 'End the run after a fall of', 'Measured from the pot’s highest value. The run ends, as it does today.', '%', 1, 8.0, 0.5, 2.0, 8.0, 8.0, 'cap', 'stop'),
]
# Layer 1, the hard ceiling in src/kaibot/risk/gate.py (BOOK_HARD_CEILINGS; daily and run from HARD_CEILINGS).
# The same for every level and every owner; nothing on this screen or in profiles.yaml can raise it. Cash is a floor.
CEILING = {'gross': 100, 'names': 30, 'name': 20, 'sector': 40, 'cash': 1, 'risk': 0.25, 'gap': 15, 'daily': 3.0, 'run': 12.0}

# what the engine field is called, for the design note and for Frontend (shown nowhere on the screen)
FIELD = {'gross': 'max_gross_pct and max_overnight_pct', 'names': 'max_names', 'name': 'max_name_pct', 'sector': 'max_sector_pct',
         'cash': 'min_cash_pct (a floor)', 'risk': 'atr_risk_pct', 'gap': 'max_gap_loss_pct', 'daily': 'daily_stop_pct',
         'run': 'run_drawdown_end_pct'}

HELD = len(d.ROWS)
INVESTED_PCT = d.INVESTED / d.POT * 100
BIGGEST = max(r['share'] for r in d.ROWS)
BIGGEST_SYM = max(d.ROWS, key=lambda r: r['share'])['sym']

RULES = [
    dict(k='r1', name=d.RULES['r1']['name'], what=d.RULES['r1']['what'], held=sum(r['rule'] == 'r1' for r in d.ROWS),
         check='locked', checkWhy='Always on for this rule. It was tested this way.'),
    dict(k='r2', name=d.RULES['r2']['name'], what=d.RULES['r2']['what'], held=sum(r['rule'] == 'r2' for r in d.ROWS),
         check='off', checkWhy='Runs without it now. Switching it on uses the version that was tested with it.'),
]


def data():
    s = [dict(k=k, label=lab, help=h, unit=u, dec=dec, level=lvl, ceil=CEILING[k], step=st, lo=lo, hi=hi, v=v, kind=kind, group=g)
         for k, lab, h, u, dec, lvl, st, lo, hi, v, kind, g in SETTINGS]
    return dict(settings=s, rules=RULES, pot=round(d.POT, 2), held=HELD, invested=round(INVESTED_PCT, 1),
                biggest=round(BIGGEST, 1), biggestSym=BIGGEST_SYM, today=d.day(d.TODAY), now=d.NOW)


# ================================================================== templates

CSS = '''
.dock{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:6;width:min(640px,calc(100% - 24px));animation:kb-dock .4s var(--ease) both}
@keyframes kb-dock{from{opacity:0;transform:translate(-50%,16px)}to{opacity:1;transform:translate(-50%,0)}}
.tog{transition:background-color .25s var(--ease)}.tog>span{transition:transform .25s var(--ease)}
.setrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 24px;align-items:center;padding:16px 20px;border-top:1px solid #1B1E24}
.stepper{display:flex;align-items:center;gap:4px;background:#0A0B0D;border:1px solid #262A31;border-radius:999px;padding:3px}
.stepper button{width:44px;height:44px;border-radius:999px;border:0;background:#1B1E24;color:#F4F5F7;cursor:pointer;display:flex;align-items:center;justify-content:center}
.stepper button:disabled{background:transparent;color:#5C6370}
.seg2{display:flex;gap:2px;background:#0A0B0D;border:1px solid #262A31;border-radius:999px;padding:3px}
.seg2 button{min-height:44px;padding:0 14px;border-radius:999px;border:0;font:inherit;font-size:14px;font-weight:600;cursor:pointer}
body:has(.mwrap) .tabbar{display:none!important}
.seg2 button{white-space:nowrap}
.layers{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;font-size:13px;color:#A1A8B3;padding-top:2px}
.layers .ly{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}.layers b{color:#D0D5DD;font-weight:600}
.layers i{font-style:normal;color:#7FB2FF;font-size:12px}.layers .ly-you b{color:#F4F5F7}.lysep{color:#A1A8B3}
.stack{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0}
.stack>li{padding:16px 20px;display:flex;flex-direction:column;gap:4px;border-left:1px solid #1B1E24}.stack>li:first-child{border-left:0}
.rrow{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;min-height:56px;padding:10px 0;border-top:1px solid #1B1E24}
@media (max-width: 760px){
  .dock{bottom:84px}
  .setrow{grid-template-columns:minmax(0,1fr);padding:14px 16px}
  .stepper{justify-self:start}
  .stack{grid-template-columns:minmax(0,1fr)}.stack>li{border-left:0;border-top:1px solid #1B1E24;padding:14px 16px}.stack>li:first-child{border-top:0}
  .card.flush{padding:0!important}
}
'''

MINUS_I = icon(IC['minus'], 18, width=2.4)
PLUS_I = icon(IC['plus'], 18, width=2.4)
LOCK = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true" style="flex: none">'
        '<rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11 V8 a4 4 0 0 1 8 0 V11"></path></svg>')


def switch(on, click, label, off=''):
    """A 52 x 32 track inside a 44 px high button, so the whole target meets the phone minimum."""
    dis = f' disabled="{{{{{off}}}}}"' if off else ''
    return (f'<button type="button" role="switch" aria-checked="{{{{{on}S}}}}" aria-label="{label}" onClick="{{{{{click}}}}}"{dis} class="press" '
            f'style="flex: none; width: 60px; height: 44px; border: 0; padding: 0; background: transparent; cursor: pointer; display: flex; align-items: center; justify-content: center">'
            f'<span class="tog" style="width: 52px; height: 32px; border-radius: 999px; padding: 4px; box-sizing: border-box; background: {{{{{on}Bg}}}}; display: flex">'
            f'<span style="width: 24px; height: 24px; border-radius: 999px; background: #F4F5F7; transform: {{{{{on}X}}}}"></span></span></button>')


SETROW = '''            <div class="setrow">
              <div style="min-width: 0; display: flex; flex-direction: column; gap: 3px">
                <span style="display: flex; align-items: center; gap: 8px"><b id="sl-{{s.k}}" style="font-size: 15px; font-weight: 600">{{s.label}}</b><sc-if value="{{s.changed}}" hint-placeholder-val="{{false}}"><span style="font-size: 12px; font-weight: 600; color: #7FB2FF">changed</span></sc-if></span>
                <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{s.help}}</span>
                <span class="num layers" aria-label="{{s.layersA}}"><span class="ly">@LOCK@Hard ceiling <b>{{s.ceilT}}</b></span><span class="lysep" aria-hidden="true">›</span><span class="ly">Balanced <b>{{s.levelT}}</b> <i>provisional</i></span><span class="lysep" aria-hidden="true">›</span><span class="ly ly-you">Yours <b>{{s.valT}}</b> <span style="color: #A1A8B3">{{s.youT}}</span></span></span>
                <sc-if value="{{s.hasWarn}}" hint-placeholder-val="{{false}}"><span class="num swap" style="display: flex; gap: 6px; font-size: 13px; color: #F5B64C; line-height: 1.5">@WARN@<span>{{s.warn}}</span></span></sc-if>
              </div>
              <div class="stepper" role="group" aria-labelledby="sl-{{s.k}}">
                <button type="button" class="press" onClick="{{s.down}}" disabled="{{s.downOff}}" aria-label="Lower: {{s.label}}">@MINUS@</button>
                <output class="num" aria-live="polite" style="min-width: 76px; text-align: center; font-size: 17px; font-weight: 700">{{s.valT}}</output>
                <button type="button" class="press" onClick="{{s.up}}" disabled="{{s.upOff}}" aria-label="Raise: {{s.label}}">@PLUS@</button>
              </div>
            </div>'''

GROUP = '''        <section class="card flush enter @D@" aria-labelledby="@ID@-h" style="@CARD@; overflow: hidden; display: flex; flex-direction: column">
          <div style="display: flex; flex-direction: column; gap: 4px; padding: 20px 20px 14px">
            <h2 id="@ID@-h" style="margin: 0; font-size: 18px; font-weight: 600">@TITLE@</h2>
            <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">@SUB@</span>
          </div>
          <sc-for list="{{@LIST@}}" as="s" hint-placeholder-count="@N@">
@ROW@
          </sc-for>
@EXTRA@
        </section>'''

ADV = '''          <button type="button" class="rowlink" onClick="{{advTap}}" aria-expanded="{{advS}}" style="display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 0 20px; border: 0; border-top: 1px solid #1B1E24; background: transparent; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer; width: 100%">
            <span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 15px; font-weight: 600">More limits</span><span style="font-size: 13px; color: #A1A8B3">Risk per stock and the bad-morning test</span></span>
            <span class="num" style="font-size: 13px; color: #A1A8B3">{{advSum}}</span>
            <span class="chev {{advChev}}" style="display: flex">@CHEV@</span>
          </button>
          <sc-if value="{{adv}}" hint-placeholder-val="{{false}}">
            <div class="expand">
              <sc-for list="{{advList}}" as="s" hint-placeholder-count="2">
@ROW@
              </sc-for>
            </div>
          </sc-if>'''

MASTER = '''    <section class="card enter d1" aria-labelledby="md-h" style="@CARD@; padding: 22px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px 24px">
      <div style="flex: 1 1 420px; min-width: 0; display: flex; flex-direction: column; gap: 4px">
        <h2 id="md-h" style="margin: 0; font-size: 18px; font-weight: 600">Multi-day holding</h2>
        <span class="swap" style="font-size: 14px; color: #A1A8B3; line-height: 1.5">{{mdNote}}</span>
      </div>
      <span class="num" style="font-size: 15px; font-weight: 600; color: {{mdInk}}">{{mdWord}}</span>
      @MSWITCH@
    </section>'''

RULECARD = '''        <section class="card enter d3" aria-labelledby="ru-h" style="@CARD@; padding: 20px 20px 8px; display: flex; flex-direction: column; gap: 4px">
          <h2 id="ru-h" style="margin: 0; font-size: 18px; font-weight: 600">Each rule</h2>
          <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5; padding-bottom: 6px">You can switch a rule off or make it stricter. You cannot switch on a version that was not tested.</span>
          <sc-for list="{{rules}}" as="r" hint-placeholder-count="2">
            <div style="display: flex; flex-direction: column; padding: 8px 0 10px; border-top: 1px solid #262A31">
              <div class="rrow" style="border-top: 0">
                <span style="flex: 1 1 200px; min-width: 0; display: flex; flex-direction: column; gap: 2px"><b id="rn-{{r.k}}" style="font-size: 16px; font-weight: 600">{{r.name}}</b><span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{r.sub}}</span></span>
                @RSWITCH@
              </div>
              <div class="rrow">
                <span style="flex: 1 1 200px; min-width: 0; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px; font-weight: 500">Market check</span><span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">No new buys while the S&amp;P 500 is below its 200-day average. {{r.checkWhy}}</span></span>
                <sc-if value="{{r.checkLocked}}" hint-placeholder-val="{{true}}"><span style="display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; color: #3DDC97">@LOCK@Always on</span></sc-if>
                <sc-if value="{{r.checkFree}}" hint-placeholder-val="{{false}}">@CSWITCH@</sc-if>
              </div>
              <div class="rrow">
                <span style="flex: 1 1 100%; min-width: 0; display: flex; flex-direction: column; gap: 2px"><span id="en-{{r.k}}" style="font-size: 14px; font-weight: 500">Around earnings</span><span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{r.earnWhy}}</span></span>
                <div class="seg2" role="radiogroup" aria-labelledby="en-{{r.k}}">
                  <sc-for list="{{r.earn}}" as="e" hint-placeholder-count="2">
                    <button type="button" role="radio" class="seg" aria-checked="{{e.on}}" onClick="{{e.pick}}" disabled="{{e.off}}" style="background: {{e.bg}}; color: {{e.ink}}">{{e.name}}</button>
                  </sc-for>
                </div>
              </div>
            </div>
          </sc-for>
        </section>'''

TODAY = '''        <section class="card enter d4" aria-labelledby="td-h" style="@CARD@; padding: 20px; display: flex; flex-direction: column; gap: 12px">
          <h2 id="td-h" style="margin: 0; font-size: 18px; font-weight: 600">What this means today</h2>
          <dl style="margin: 0; display: flex; flex-direction: column">
            <sc-for list="{{today}}" as="t" hint-placeholder-count="4">
              <div style="display: flex; flex-wrap: wrap; gap: 2px 12px; padding: 10px 0; border-top: 1px solid #1B1E24"><dt style="flex: 1 1 160px; font-size: 14px; color: #A1A8B3">{{t.k}}</dt><dd class="num swap" style="margin: 0; font-size: 14px; font-weight: 600; text-align: right">{{t.v}}</dd></div>
            </sc-for>
          </dl>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">Kaibot never sells a stock only because you lowered a limit. Lower limits stop new buys and make new positions smaller. To sell everything now, use Flatten and stop on Home.</span>
        </section>'''

DOCK = '''    <sc-if value="{{dirty}}" hint-placeholder-val="{{false}}">
      <div class="dock" role="region" aria-label="Unsaved changes" style="display: flex; align-items: center; gap: 10px; padding: 10px 10px 10px 20px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 12px 40px rgba(0,0,0,.5)">
        <span class="num" style="flex: 1; font-size: 14px; font-weight: 600">{{dirtyT}}</span>
        <button type="button" class="press" onClick="{{undo}}" style="height: 44px; padding: 0 16px; border-radius: 999px; border: 1px solid #3A404A; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Undo</button>
        <button type="button" class="press" onClick="{{openReview}}" style="height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review</button>
      </div>
    </sc-if>'''

SHEETS = '''    <sc-if value="{{review}}" hint-placeholder-val="{{false}}">
      <div class="mwrap">
        <div onClick="{{closeSheet}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
        <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="rv-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <div style="display: flex; align-items: center; gap: 8px"><h2 id="rv-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Review changes</h2><button type="button" onClick="{{closeSheet}}" aria-label="Close" class="press" style="width: 44px; height: 44px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button></div>
          <ul style="list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column">
            <sc-for list="{{diff}}" as="x" hint-placeholder-count="2">
              <li style="display: flex; flex-wrap: wrap; gap: 2px 10px; padding: 10px 0; border-top: 1px solid #1B1E24; font-size: 14px"><span style="flex: 1 1 180px; color: #A1A8B3">{{x.k}}</span><span class="num" style="color: #A1A8B3; text-decoration: line-through">{{x.a}}</span><span aria-hidden="true" style="color: #A1A8B3">→</span><b class="num" style="font-weight: 600">{{x.b}}</b></li>
            </sc-for>
          </ul>
          <ol style="margin: 0; padding: 0 0 0 20px; display: flex; flex-direction: column; gap: 8px; font-size: 14px; color: #A1A8B3; line-height: 1.5">
            <li>Kaibot first replays both rules over ten years with your settings and logs the run. This takes a few minutes.</li>
            <li class="num">Then the settings apply from the next decision, after tonight’s close at 22:00. Tonight’s orders are already at the broker; a buy that would break a new limit is cancelled.</li>
            <li>Every setting stays inside the Balanced limits, so nothing here can make Kaibot hold more.</li>
          </ol>
          <div style="display: flex; flex-wrap: wrap; gap: 10px; padding-top: 4px">
            <button type="button" class="press full-sm" onClick="{{apply}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Save and replay</button>
            <button type="button" class="press full-sm" onClick="{{closeSheet}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #3A404A; background: transparent; color: #F4F5F7; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Keep editing</button>
          </div>
        </section>
      </div>
    </sc-if>
    <sc-if value="{{offSheet}}" hint-placeholder-val="{{false}}">
      <div class="mwrap">
        <div onClick="{{closeSheet}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
        <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="of-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <div style="display: flex; align-items: center; gap: 8px"><h2 id="of-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Switch off multi-day holding?</h2><button type="button" onClick="{{closeSheet}}" aria-label="Close" class="press" style="width: 44px; height: 44px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button></div>
          <span class="num" style="font-size: 14px; color: #A1A8B3; line-height: 1.5">Kaibot holds {{held}} stocks for these rules. Choose what happens to them.</span>
          <div role="radiogroup" aria-labelledby="of-h" style="display: flex; flex-direction: column; gap: 8px">
            <sc-for list="{{offModes}}" as="m" hint-placeholder-count="2">
              <button type="button" role="radio" aria-checked="{{m.on}}" onClick="{{m.pick}}" class="press" style="display: flex; gap: 12px; align-items: flex-start; text-align: left; padding: 14px 16px; border-radius: 16px; border: 1px solid {{m.bd}}; background: {{m.bg}}; color: #F4F5F7; font: inherit; cursor: pointer">
                <span aria-hidden="true" style="flex: none; width: 20px; height: 20px; margin-top: 1px; border-radius: 999px; border: 2px solid {{m.ring}}; box-sizing: border-box; display: flex; align-items: center; justify-content: center"><span style="width: 10px; height: 10px; border-radius: 999px; background: {{m.dot}}"></span></span>
                <span style="display: flex; flex-direction: column; gap: 3px"><b style="font-size: 15px; font-weight: 600">{{m.name}}</b><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{m.sub}}</span></span>
              </button>
            </sc-for>
          </div>
          <div style="display: flex; flex-wrap: wrap; gap: 10px; padding-top: 4px">
            <button type="button" class="press full-sm" onClick="{{confirmOff}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Switch off</button>
            <button type="button" class="press full-sm" onClick="{{closeSheet}}" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #3A404A; background: transparent; color: #F4F5F7; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Keep it on</button>
          </div>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">Either way, nothing changes before you review and save.</span>
        </section>
      </div>
    </sc-if>'''

BANNER = '''    <sc-if value="{{@IF@}}" hint-placeholder-val="{{false}}">
      <div role="status" class="swap" style="display: flex; gap: 12px; align-items: flex-start; padding: 16px 18px; border-radius: 18px; background: @BG@; border: 1px solid @BD@">
        <span style="color: @INK@; display: flex; padding-top: 1px">@ICON@</span>
        <span style="display: flex; flex-direction: column; gap: 4px; min-width: 0"><b style="font-size: 15px; font-weight: 600">@HEAD@</b><span class="num" style="font-size: 14px; color: #D0D5DD; line-height: 1.5">@TEXT@</span></span>
      </div>
    </sc-if>'''


def banner(cond, tone, ic, head, text):
    ink, bg, bd = TONE[tone]
    return sub(BANNER, IF=cond, BG=bg, BD=bd, INK=ink, ICON=icon(IC[ic], 20, ink), HEAD=head, TEXT=text)


STACK = '''    <section class="card enter d1" aria-labelledby="sk-h" style="@CARD@; padding: 0; overflow: hidden; display: flex; flex-direction: column">
      <h2 id="sk-h" style="margin: 0; padding: 18px 20px 4px; font-size: 18px; font-weight: 600">Three layers, each only stricter</h2>
      <span style="padding: 0 20px 12px; font-size: 14px; color: #A1A8B3; line-height: 1.5">Every limit below shows all three. The strictest one is the one Kaibot uses.</span>
      <ol class="stack" style="list-style: none; margin: 0; padding: 0; border-top: 1px solid #1B1E24">
        <li><span style="display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: #A1A8B3">@LOCK@1 · Hard ceiling</span><b style="font-size: 15px; font-weight: 600">Built into Kaibot</b><span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.5">The same for everyone, for example 100% invested, 30 stocks, 20% in one stock, at least 1% cash. Nothing can raise it.</span></li>
        <li><span style="font-size: 13px; font-weight: 600; color: #7FB2FF">2 · Your level</span><b style="font-size: 15px; font-weight: 600">Balanced, provisional</b><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">At or inside the hard ceiling. Set from the first ten-year results and confirmed before the first paper start.</span></li>
        <li><span style="font-size: 13px; font-weight: 600; color: #F4F5F7">3 · Your settings</span><b style="font-size: 15px; font-weight: 600">Only stricter than the level</b><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">The + stops at the level. A looser value is refused, never rounded down.</span></li>
      </ol>
    </section>'''

SKEL = '''    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">
      <div aria-busy="true" aria-label="Loading" style="display: flex; flex-direction: column; gap: 20px">
        <span class="sk" style="width: 300px; height: 40px; border-radius: 12px"></span>
        <span class="sk" style="height: 72px; border-radius: 18px"></span>
        <span class="sk" style="height: 96px; border-radius: 24px"></span>
        <div class="two"><span class="sk" style="height: 640px; border-radius: 24px"></span><div style="display: flex; flex-direction: column; gap: 20px"><span class="sk" style="height: 420px; border-radius: 24px"></span><span class="sk" style="height: 240px; border-radius: 24px"></span></div></div>
      </div>
    </sc-if>'''

PAGE = '''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 120px; display: flex; flex-direction: column; gap: 20px">
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
    <div class="enter" style="display: flex; flex-direction: column; gap: 6px">
      <a href="Configure.dc.html" style="@LINK@; align-self: flex-start">@BACK@ Strategy</a>
      <div style="display: flex; align-items: center; gap: 12px"><h1 class="h1" style="margin: 0; flex: 1 1 auto; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Holding settings</h1>@SAMPLE@</div>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5; max-width: 760px">For the rules that keep stocks for days or weeks. Each setting can only make Kaibot more careful than your level, Balanced. Nothing here can raise a limit.</span>
    </div>
@B_APPLIED@
@B_NOTYET@
@B_PROV@
@STACK@
@MASTER@
    <div class="two">
      <div style="display: flex; flex-direction: column; gap: 20px; min-width: 0">
@G_BOOK@
@G_STOP@
      </div>
      <div style="display: flex; flex-direction: column; gap: 20px; min-width: 0">
@RULES@
@TODAY@
      </div>
    </div>
    @FOOT@
    </sc-if>
@DOCK@
@SHEETS@
  </main>
'''

JS = r'''class Component extends DCLogic {
  constructor(p) {
    super(p); this.D = @DATA@;
    const vals = {}; this.D.settings.forEach((s) => { vals[s.k] = s.v; });
    const rules = { r1: { on: true, check: true, earn: 'hold' }, r2: { on: true, check: false, earn: 'hold' } };
    this.saved = { vals: { ...vals }, rules: JSON.parse(JSON.stringify(rules)), md: true, offMode: 'keep' };
    this.state = { more: false, ready: false, vals, rules, md: true, offMode: 'keep', pickMode: 'keep', sheet: null, adv: false, applied: false };
  }
@COMMON@
  fmt(s, v) { return (s.unit === '%' ? v.toFixed(s.dec) + '%' : String(v)); }
  usd(v) { return '$' + Math.round(v).toLocaleString('en-US'); }
  sw(on, extra) { return { [extra + 'S']: String(on), [extra + 'Bg']: on ? '#3D7BD9' : '#3A404A', [extra + 'X']: on ? 'translateX(20px)' : 'none' }; }
  set(k, v) { this.setState({ vals: { ...this.state.vals, [k]: v }, applied: false }); }
  row(s) {
    const st = this.state, v = st.vals[s.k], D = this.D, eps = 1e-9;
    const r = (x) => Math.round(x / s.step) * s.step;
    const atLimit = s.kind === 'cap' ? v >= s.level - eps : v <= s.level + eps;
    let warn = '';
    const notYet = !!this.props.notStartedYet;
    if (s.k === 'names' && !notYet && v < D.held) warn = 'You hold ' + D.held + ' now. Kaibot sells none of them for this; it buys again once fewer than ' + v + ' are held.';
    if (s.k === 'gross' && !notYet && v < D.invested) warn = D.invested.toFixed(0) + '% is invested now. Kaibot sells nothing for this; it buys again once less than ' + v + '% is invested.';
    if (s.k === 'name' && !notYet && v < D.biggest) warn = D.biggestSym + ' is ' + D.biggest.toFixed(1) + '% of the pot now. It is kept; Kaibot does not add to it above ' + v + '%.';
    if (s.k === 'cash' && v > 100 - st.vals.gross) warn = 'With ' + v + '% kept aside, at most ' + (100 - v) + '% can be invested.';
    const floor = s.kind === 'floor', pre = floor ? 'at least ' : '';
    const youT = atLimit ? '· at the Balanced limit' : (floor ? '· stricter: more cash' : '· stricter');
    return { k: s.k, label: s.label, help: s.help, valT: this.fmt(s, v), changed: Math.abs(v - this.saved.vals[s.k]) > eps,
      ceilT: pre + this.fmt(s, s.ceil), levelT: pre + this.fmt(s, s.level), youT,
      layersA: 'Hard ceiling ' + pre + this.fmt(s, s.ceil) + ', Balanced limit ' + pre + this.fmt(s, s.level) + ' provisional, yours ' + this.fmt(s, v),
      hasWarn: !!warn, warn,
      downOff: v <= s.lo + eps ? true : null, upOff: v >= s.hi - eps ? true : null,
      down: () => this.set(s.k, Math.max(s.lo, r(v - s.step))), up: () => this.set(s.k, Math.min(s.hi, r(v + s.step))) };
  }
  renderVals() {
    const st = this.state, D = this.D, S = this.saved, P = this.props;
    const notYet = !!P.notStartedYet, tested = !!P.earningsTested;
    const rows = D.settings.map((s) => this.row(s));
    const by = (g) => rows.filter((r, i) => D.settings[i].group === g);
    const diff = [];
    if (st.md !== S.md) diff.push({ k: 'Multi-day holding', a: 'On', b: st.offMode === 'sell' ? 'Off, sell at the next close' : 'Off, keep what is held' });
    D.settings.forEach((s) => { if (Math.abs(st.vals[s.k] - S.vals[s.k]) > 1e-9) diff.push({ k: s.label, a: this.fmt(s, S.vals[s.k]), b: this.fmt(s, st.vals[s.k]) }); });
    D.rules.forEach((ru) => {
      const a = S.rules[ru.k], b = st.rules[ru.k];
      if (a.on !== b.on) diff.push({ k: ru.name, a: a.on ? 'On' : 'Off', b: b.on ? 'On' : 'Off' });
      if (a.check !== b.check) diff.push({ k: ru.name + ' · market check', a: a.check ? 'On' : 'Off', b: b.check ? 'On' : 'Off' });
      if (a.earn !== b.earn) diff.push({ k: ru.name + ' · earnings', a: a.earn === 'hold' ? 'Hold through' : 'Sell before', b: b.earn === 'hold' ? 'Hold through' : 'Sell before' });
    });
    const setRule = (k, patch) => this.setState({ rules: { ...st.rules, [k]: { ...st.rules[k], ...patch } }, applied: false });
    const rules = D.rules.map((ru) => {
      const r = st.rules[ru.k], wasOn = S.rules[ru.k].on, wasCheck = S.rules[ru.k].check;
      const earn = [['hold', 'Hold through'], ['sell', 'Sell before']].map(([k, n]) => {
        const can = k === 'hold' ? S.rules[ru.k].earn === 'hold' : tested;      // only stricter, and only a tested version
        const on = r.earn === k;
        return { name: n, on: String(on), off: can ? null : true, pick: () => setRule(ru.k, { earn: k }),
          bg: on ? '#F4F5F7' : 'transparent', ink: on ? '#0A0B0D' : (can ? '#A1A8B3' : '#7E8693') };
      });
      return { k: ru.k, name: ru.name,
        sub: (notYet ? 'Not started · ' : r.on ? ru.held + ' stocks held · ' : 'Off: buys nothing new, keeps its ' + ru.held + ' stocks until it sells them · ') + ru.what,
        ...this.sw(r.on, 'on'), onOff: !wasOn && !r.on ? true : null, flip: () => { if (r.on || wasOn) setRule(ru.k, { on: !r.on }); },
        checkLocked: ru.check === 'locked', checkFree: ru.check !== 'locked', checkWhy: ru.checkWhy,
        ...this.sw(r.check, 'ck'), ckOff: wasCheck && r.check ? true : null, flipCheck: () => { if (!r.check || !wasCheck) setRule(ru.k, { check: !r.check }); },
        earnWhy: tested ? 'Both choices were tested. Selling before earnings means more trades.' : 'Holds through earnings, as it was tested. “Sell before” is offered once that version has passed its own tests.',
        earn };
    });
    const v = st.vals, pot = D.pot;
    const cap = Math.min(v.gross, 100 - v.cash);
    const today = [
      { k: 'Most invested', v: cap + '% · ' + this.usd(pot * cap / 100) + ' of ' + this.usd(pot) },
      { k: 'Invested now', v: notYet ? 'Nothing yet' : D.invested.toFixed(0) + '% · ' + D.held + ' stocks' },
      { k: 'Most in one stock', v: v.name + '% · ' + this.usd(pot * v.name / 100) },
      { k: 'Buying stops for the day at', v: '−' + this.usd(pot * v.daily / 100) + ' on the day' },
      { k: 'The run ends at', v: '−' + v.run.toFixed(1) + '% from the pot’s highest value' },
    ];
    const mdOn = st.md && !notYet;
    const advChanged = by('more').some((r) => r.changed);
    return { ...this.common(), notYet, held: D.held,
      book: by('book'), advList: by('more'), stop: by('stop'), adv: st.adv, advS: String(st.adv), advChev: st.adv ? 'open-chev' : '',
      advTap: () => this.setState({ adv: !st.adv }), advSum: advChanged ? 'changed' : 'at the Balanced limits',
      mdWord: notYet ? 'Not started' : (st.md ? 'On' : 'Off'), mdInk: mdOn ? '#3DDC97' : '#A1A8B3',
      mdNote: notYet ? 'Off at every level until a rule has passed its tests and the paper start is approved.' :
        (st.md ? 'Kaibot may keep stocks overnight and for weeks, inside the limits below.' :
          (st.offMode === 'sell' ? 'Off after you save: Kaibot sells every multi-day holding at the next close.' : 'Off after you save: Kaibot buys nothing new and keeps what it holds until each rule sells it.')),
      ...this.sw(mdOn, 'md'), mdOff: notYet || (!S.md && !st.md) ? true : null,
      flipMd: () => { if (notYet) return; if (st.md) this.setState({ sheet: 'off', pickMode: st.offMode }); else this.setState({ md: true, applied: false }); },
      offSheet: st.sheet === 'off', review: st.sheet === 'review',
      offModes: [['keep', 'Stop buying, keep what is held', 'Each rule sells its stocks by its own conditions. Safety stops stay at the broker.'],
                 ['sell', 'Sell everything at the next close', 'Every multi-day holding is sold at tonight’s closing price.']].map(([k, n, s]) => {
        const on = st.pickMode === k;
        return { name: n, sub: s, on: String(on), pick: () => this.setState({ pickMode: k }), bd: on ? '#4F95F0' : '#262A31', bg: on ? '#16263D' : 'transparent', ring: on ? '#7FB2FF' : '#5C6370', dot: on ? '#7FB2FF' : 'transparent' };
      }),
      confirmOff: () => this.setState({ md: false, offMode: st.pickMode, sheet: null, applied: false }),
      closeSheet: () => this.setState({ sheet: null }),
      rules, today, diff,
      dirty: diff.length > 0 && !st.sheet, dirtyT: diff.length === 1 ? '1 change, not saved' : diff.length + ' changes, not saved',
      undo: () => this.setState({ vals: { ...S.vals }, rules: JSON.parse(JSON.stringify(S.rules)), md: S.md, offMode: S.offMode }),
      openReview: () => this.setState({ sheet: 'review' }),
      apply: () => { this.saved = { vals: { ...st.vals }, rules: JSON.parse(JSON.stringify(st.rules)), md: st.md, offMode: st.offMode }; this.setState({ sheet: null, applied: true }); },
      applied: st.applied, prov: !st.applied,
    };
  }
}'''


def build():
    ui.setup('Strategy')
    if CSS not in shell.CSS:
        shell.CSS = shell.CSS + CSS
    warn = icon(IC['warn'], 16, TONE['warn'][0], 2.2, '; margin-top: 2px')
    row = sub(SETROW, LOCK=LOCK, WARN=warn, MINUS=MINUS_I, PLUS=PLUS_I)
    g_book = sub(GROUP, D='d2', ID='bk', TITLE='Limits for the whole book', SUB='Both rules together, at today’s prices.', LIST='book',
                 N='5', ROW=row, EXTRA=sub(ADV, ROW=row))
    g_stop = sub(GROUP, D='d3', ID='st', TITLE='When to stop', SUB='These also count the move from one close to the next morning.', LIST='stop',
                 N='2', ROW=row, EXTRA='')
    rsw = switch('r.on', 'r.flip', 'Use {{r.name}}', 'r.onOff')
    csw = switch('r.ck', 'r.flipCheck', 'Market check for {{r.name}}', 'r.ckOff')
    rules = sub(RULECARD, LOCK=LOCK, RSWITCH=rsw, CSWITCH=csw)
    master = sub(MASTER, MSWITCH=switch('md', 'flipMd', 'Multi-day holding', 'mdOff'))
    b_prov = banner('prov', 'info', 'info', 'The Balanced limits are provisional',
                    'They are set from the first ten-year results and confirmed before the first paper start. Your settings stay inside them: '
                    'if a limit is set lower than yours, yours follows it down.')
    b_not = banner('notYet', 'off', 'clock', 'Multi-day holding has not started',
                   'You can set your limits now. They apply from the first paper start.')
    b_app = banner('applied', 'ok', 'clock', 'Saved · replay running',
                   'Kaibot is replaying both rules over ten years with your settings and logs the run. The settings apply from the next decision, after tonight’s close at 22:00.')
    body = sub(PAGE, SKEL=SKEL, STACK=sub(STACK, LOCK=LOCK), B_APPLIED=b_app, B_NOTYET=b_not, B_PROV=b_prov, MASTER=master, G_BOOK=g_book, G_STOP=g_stop,
               RULES=rules, TODAY=TODAY, DOCK=DOCK, SHEETS=SHEETS)
    sc = script(JS, data())
    props = (ui.PROPS + '"notStartedYet":{"editor":"boolean","default":false,"section":"States"},'
             '"earningsTested":{"editor":"boolean","default":false,"section":"States"},')
    ui.write('HoldingSettings', 'Holding settings', 'Strategy', body, sc, 2040, props)
    ui.write('HoldingSettingsMobile', 'Holding settings on a phone', 'Strategy', body, sc, 844, props, width=390)


if __name__ == '__main__':
    build()
    print('ok')
