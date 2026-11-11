"""v3 · 09 Strategy (configuration panel, M5 proposal): desktop + phone artboards."""
import sys
sys.path.insert(0, '.')
import shell  # noqa: E402
from shell import CARD  # noqa: E402

shell.NAV = [("Home", "Home.dc.html"), ("Trades", "Trades.dc.html"), ("Strategy", "Configure.dc.html"),
             ("Report", "Report.dc.html"), ("Engine", "Engine.dc.html"), ("Run &amp; settings", "Settings.dc.html")]

ICON = {
    "Home": '<path d="M3 11 L12 4 L21 11 V20 H14 V14 H10 V20 H3 Z" stroke-linejoin="round"></path>',
    "Trades": '<path d="M4 7 H20 M4 12 H20 M4 17 H14" stroke-linecap="round"></path>',
    "Strategy": '<path d="M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20" stroke-linecap="round"></path><circle cx="16" cy="7" r="2"></circle><circle cx="10" cy="17" r="2"></circle>',
    "Report": '<path d="M5 20 V12 M12 20 V5 M19 20 V9" stroke-linecap="round"></path>',
    "Run": '<circle cx="12" cy="12" r="3"></circle><path d="M12 3 V6 M12 18 V21 M3 12 H6 M18 12 H21" stroke-linecap="round"></path>',
}
HREF = {"Home": "Home.dc.html", "Trades": "Trades.dc.html", "Strategy": "Configure.dc.html",
        "Report": "Report.dc.html", "Run": "Settings.dc.html"}


def tabbar(active):
    items = "\n".join(
        f'    <a href="{HREF[k]}" class="press" {"aria-current=\"page\" " if k == active else ""}style="display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 60px; min-height: 44px; color: {"#F4F5F7" if k == active else "#7E8693"}; text-decoration: none; font-size: 11px; font-weight: 600"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">{v}</svg>{k}</a>'
        for k, v in ICON.items())
    return f'  <nav class="tabbar" aria-label="Main">\n{items}\n  </nav>\n'


shell.TAB = tabbar("Strategy")

EXTRA_CSS = '''
input[type=range].kb{-webkit-appearance:none;appearance:none;width:100%;height:28px;background:transparent;margin:0;cursor:pointer}
input[type=range].kb::-webkit-slider-runnable-track{height:6px;border-radius:999px;background:linear-gradient(90deg,#4F95F0 0,#4F95F0 var(--p),#262A31 var(--p),#262A31 100%)}
input[type=range].kb::-moz-range-track{height:6px;border-radius:999px;background:#262A31}
input[type=range].kb::-moz-range-progress{height:6px;border-radius:999px;background:#4F95F0}
input[type=range].kb::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;margin-top:-8px;border-radius:999px;background:#F4F5F7;border:0;box-shadow:0 0 0 4px rgba(79,149,240,.25);transition:transform .15s var(--ease)}
input[type=range].kb::-moz-range-thumb{width:22px;height:22px;border-radius:999px;background:#F4F5F7;border:0}
input[type=range].kb:active::-webkit-slider-thumb{transform:scale(1.15)}
.chip{transition:background-color .2s var(--ease),border-color .2s var(--ease),color .2s var(--ease),transform .15s var(--ease)}
.chip:active{transform:scale(.96)}
.tog{transition:background-color .25s var(--ease)}.tog>span{transition:transform .25s var(--ease)}
.sheet{animation:kb-sheet .35s var(--ease) both}
@keyframes kb-sheet{from{transform:translateY(100%)}to{transform:none}}
details>summary{list-style:none;cursor:pointer}details>summary::-webkit-details-marker{display:none}
details[open] .chev{transform:rotate(180deg)}.chev{transition:transform .25s var(--ease)}
.srow{transition:background-color .2s var(--ease)}.srow:hover{background:#171A1F}
.open-chev{transform:rotate(90deg)}
.expand{animation:kb-rise .3s var(--ease) both}
.dock{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:6;width:min(640px,calc(100% - 24px));animation:kb-dock .4s var(--ease) both}
@keyframes kb-dock{from{opacity:0;transform:translate(-50%,16px)}to{opacity:1;transform:translate(-50%,0)}}
.mwrap{position:fixed;inset:0;z-index:8;display:flex;align-items:center;justify-content:center;padding:24px}
.mpanel{width:min(440px,100%);max-height:86vh;overflow:auto;border-radius:24px;animation:kb-rise .3s var(--ease) both}
@media (max-width: 760px){.dock{bottom:84px}.mwrap{align-items:flex-end;padding:0}.mpanel{width:100%;border-radius:24px 24px 0 0;animation:kb-sheet .35s var(--ease) both}.sumline{font-size:13px!important}}
'''
shell.CSS = shell.CSS + EXTRA_CSS

# ---------------------------------------------------------------- shared pieces


def slider(key, label):
    return f'''
            <div style="display: flex; flex-direction: column; gap: 4px">
              <input class="kb" type="range" aria-label="{label}" min="{{{{s.{key}.min}}}}" max="{{{{s.{key}.max}}}}" step="{{{{s.{key}.step}}}}" value="{{{{s.{key}.v}}}}" onInput="{{{{s.{key}.set}}}}" style="--p: {{{{s.{key}.pct}}}}" aria-valuetext="{{{{s.{key}.text}}}}">
              <div class="num" style="display: flex; justify-content: space-between; font-size: 12px; color: #7E8693"><span>{{{{s.{key}.lo}}}}</span><span>{{{{s.{key}.note}}}}</span><span>{{{{s.{key}.hi}}}}</span></div>
            </div>'''


def row(key, label, value=None, sliders=None, first=False):
    value = value or f"{{{{s.{key}.text}}}}"
    inner = "".join(slider(k, lab) for k, lab in (sliders or [(key, label)]))
    if sliders:
        inner = "".join(f'<div style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 12px; color: #A1A8B3">{lab}</span>{slider(k, lab)}</div>' for k, lab in sliders)
    bt = "" if first else "border-top: 1px solid #1B1E24; "
    return f'''
          <div style="{bt}display: flex; flex-direction: column">
            <button type="button" class="srow" aria-expanded="{{{{s.{key}.openS}}}}" onClick="{{{{s.{key}.tap}}}}" style="display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 0 20px; border: 0; background: transparent; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer; width: 100%">
              <span style="flex: 1; font-size: 15px">{label}</span>
              <sc-if value="{{{{s.{key}.changed}}}}" hint-placeholder-val="{{{{false}}}}"><span aria-label="changed" style="width: 6px; height: 6px; border-radius: 999px; background: #4F95F0"></span></sc-if>
              <span class="num" style="font-size: 15px; font-weight: 600; color: {{{{s.{key}.ink}}}}">{value}</span>
              <svg class="chev {{{{s.{key}.chev}}}}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7E8693" stroke-width="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            </button>
            <sc-if value="{{{{s.{key}.open}}}}" hint-placeholder-val="{{{{false}}}}">
              <div class="expand" style="display: flex; flex-direction: column; gap: 14px; padding: 4px 20px 18px">{inner}</div>
            </sc-if>
          </div>'''


def group(title, body, sub=""):
    s2 = f'<span style="font-size: 13px; color: #7E8693">{sub}</span>' if sub else ""
    return f'''
    <section class="enter" style="display: flex; flex-direction: column; gap: 10px">
      <div style="display: flex; flex-direction: column; gap: 2px; padding: 0 4px"><h2 style="margin: 0; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">{title}</h2>{s2}</div>
      <div style="{CARD}; overflow: hidden; display: flex; flex-direction: column">{body}
      </div>
    </section>'''


def toggle(on, click, label):
    return (f'<button type="button" role="switch" aria-checked="{on}" aria-label="{label}" onClick="{click}" class="tog press" '
            f'style="flex: none; width: 48px; height: 28px; border-radius: 999px; border: 0; padding: 3px; cursor: pointer; background: {{{{TOGBG}}}}; display: flex">'
            f'<span style="width: 22px; height: 22px; border-radius: 999px; background: #F4F5F7; transform: {{{{TOGX}}}}"></span></button>')



FOCUS = f"""
    <section class="card enter d1" aria-labelledby="fo-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 16px">
      <div style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center">
        <h2 id="fo-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">What to trade</h2>
        <div role="radiogroup" aria-label="Focus mode" style="display: flex; gap: 2px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 3px">
          <sc-for list="{{{{modes}}}}" as="m" hint-placeholder-count="2">
            <button type="button" role="radio" aria-checked="{{{{m.on}}}}" onClick="{{{{m.pick}}}}" class="press" style="height: 34px; padding: 0 14px; border-radius: 999px; border: 0; background: {{{{m.bg}}}}; color: {{{{m.ink}}}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{{{m.name}}}}</button>
          </sc-for>
        </div>
      </div>
      <div style="display: flex; flex-wrap: wrap; gap: 8px" role="group" aria-label="Themes">
        <sc-for list="{{{{themes}}}}" as="t" hint-placeholder-count="11">
          <button type="button" aria-pressed="{{{{t.on}}}}" onClick="{{{{t.pick}}}}" class="chip" style="height: 38px; padding: 0 14px; border-radius: 999px; border: 1px solid {{{{t.bd}}}}; background: {{{{t.bg}}}}; color: {{{{t.ink}}}}; font: inherit; font-size: 14px; font-weight: 500; cursor: pointer">{{{{t.name}}}}</button>
        </sc-for>
      </div>
      <div style="display: flex; align-items: center; gap: 12px; padding-top: 14px; border-top: 1px solid #1B1E24">
        <div aria-hidden="true" style="display: flex">
          <sc-for list="{{{{faces}}}}" as="y" hint-placeholder-count="4">
            <span style="width: 28px; height: 28px; margin-right: -8px; border-radius: 999px; border: 2px solid #131519; background: {{{{y.c}}}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700">{{{{y.i}}}}</span>
          </sc-for>
        </div>
        <span class="num swap" style="flex: 1; min-width: 0; padding-left: 8px; font-size: 14px; color: #A1A8B3">{{{{picked}}}}</span>
        <button type="button" onClick="{{{{editTap}}}}" aria-expanded="{{{{editS}}}}" class="press" style="height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid #262A31; background: transparent; color: #F4F5F7; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{{{editText}}}}</button>
      </div>
      <sc-if value="{{{{edit}}}}" hint-placeholder-val="{{{{false}}}}">
        <div class="expand" style="display: flex; flex-direction: column; gap: 10px">
          <label style="display: flex; align-items: center; gap: 8px; height: 42px; padding: 0 14px; border-radius: 999px; border: 1px solid #262A31; background: #0A0B0D; color: #7E8693">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20 L16 16" stroke-linecap="round"></path></svg>
            <input type="text" placeholder="Add a stock, e.g. COST" aria-label="Add a stock" style="flex: 1; border: 0; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; outline: none">
          </label>
          <div style="display: flex; flex-wrap: wrap; gap: 6px">
            <sc-for list="{{{{syms}}}}" as="y" hint-placeholder-count="11">
              <span class="swap" style="display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 4px 0 12px; border-radius: 999px; background: #1B1E24; font-size: 13px; font-weight: 600">{{{{y.s}}}}<button type="button" aria-label="Remove {{{{y.s}}}}" onClick="{{{{y.drop}}}}" class="press" style="width: 26px; height: 26px; border-radius: 999px; border: 0; background: transparent; color: #7E8693; cursor: pointer; font-size: 15px">×</button></span>
            </sc-for>
          </div>
        </div>
      </sc-if>
      <span class="swap" style="font-size: 13px; color: {{{{modeInk}}}}; line-height: 1.5">{{{{modeNote}}}}</span>
    </section>"""

RISK = group("Risk", row("risk", "Risk per trade", first=True) + row("dstop", "Daily stop") + row("pos", "Open positions")
             + row("size", "Position size") + row("entries", "New trades a day") + row("dd", "End run at drawdown"),
             "Can only go down from Hunter.")

STRATS = group("Strategies", """
          <sc-for list="{{strats}}" as="g" hint-placeholder-count="2">
            <div style="display: flex; align-items: center; gap: 14px; min-height: 64px; padding: 10px 20px; border-bottom: 1px solid #1B1E24">
              <span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 15px">{{g.name}}</span><span style="font-size: 12px; color: #7E8693">{{g.blurb}}</span></span>
              """ + toggle("{{g.on}}", "{{g.flip}}", "{{g.name}}").replace("{{TOGBG}}", "{{g.tbg}}").replace("{{TOGX}}", "{{g.tx}}") + """
            </div>
          </sc-for>
          <div style="display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 0 20px">
            <span style="flex: 1; font-size: 13px; color: #7E8693">Opening range breakout · News continuation</span>
            <span style="font-size: 11px; font-weight: 600; color: #F5B64C; background: #33270F; border-radius: 999px; padding: 3px 8px">under test</span>
          </div>""")

TIMELINE = """
          <figure style="margin: 0; padding: 18px 20px 10px" aria-label="{{dayAria}}">
            <svg viewBox="0 0 780 46" width="100%" height="46" aria-hidden="true">
              <defs><pattern id="hz" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="6" fill="#F5B64C" opacity=".55"></rect></pattern></defs>
              <rect x="0" y="8" width="780" height="14" rx="7" fill="#1B1E24"></rect>
              <rect class="grow" x="{{winX}}" y="8" width="{{winW}}" height="14" rx="7" fill="#4F95F0" style="transition: x .3s var(--ease), width .3s var(--ease)"></rect>
              <rect x="{{fomcX}}" y="5" width="{{fomcW}}" height="20" rx="5" fill="url(#hz)" style="transition: x .3s var(--ease), width .3s var(--ease)"></rect>
              <line x1="770" y1="2" x2="770" y2="28" stroke="#FF6B6B" stroke-width="2"></line>
              <text x="0" y="42" fill="#7E8693" font-size="11" font-family="Inter">09:30</text><text x="{{winX}}" y="42" fill="#A1A8B3" font-size="11" font-family="Inter" dx="4">{{winStart}}</text><text x="740" y="42" fill="#7E8693" font-size="11" font-family="Inter">16:00</text>
            </svg>
            <figcaption style="display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: #7E8693"><span><span style="display: inline-block; width: 10px; height: 10px; border-radius: 3px; background: #4F95F0; vertical-align: -1px"></span> new trades</span><span><span style="display: inline-block; width: 10px; height: 10px; border-radius: 3px; background: #5A4520; border: 1px solid #F5B64C; vertical-align: -1px"></span> Fed/CPI quiet</span><span><span style="display: inline-block; width: 2px; height: 10px; background: #FF6B6B; vertical-align: -1px"></span> sell-all 15:55</span></figcaption>
          </figure>"""

TIMING = group("Timing", TIMELINE + row("start", "First new trade") + row("end", "Last new trade")
               + row("quiet", "Fed & CPI quiet window") + row("vix", "Sit out when VIX is above")
               + row("streak", "Pause after a losing streak", "{{streakText}}", [("streak", "Losses in a row"), ("wait", "Pause length")]))

MORE = group("More", row("price", "Advanced liquidity floors", "{{advText}}", [("price", "Min share price"), ("adv", "Min daily $ volume"), ("spread", "Max bid-ask spread")], first=True) + """
          <div style="border-top: 1px solid #1B1E24; display: flex; flex-direction: column">
            <button type="button" class="srow" onClick="{{lockTap}}" aria-expanded="{{lockS}}" style="display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 0 20px; border: 0; background: transparent; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer; width: 100%">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3DDC97" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11 V8 a4 4 0 0 1 8 0 V11"></path></svg>
              <span style="flex: 1; font-size: 15px">Safety rules</span>
              <span style="font-size: 15px; font-weight: 600; color: #3DDC97">Always on</span>
              <svg class="chev {{lockChev}}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7E8693" stroke-width="2" aria-hidden="true"><path d="M9 6 L15 12 L9 18" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            </button>
            <sc-if value="{{lock}}" hint-placeholder-val="{{false}}">
              <ul class="expand" style="margin: 0; padding: 0 20px 18px 46px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: #A1A8B3; line-height: 1.45">
                <sc-for list="{{locked}}" as="k" hint-placeholder-count="4"><li>{{k}}</li></sc-for>
              </ul>
            </sc-if>
          </div>""")

REVIEW = """
        <div style="display: flex; align-items: center; gap: 8px">
          <h2 id="rv-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Review changes</h2>
          <button type="button" onClick="{{closeSheet}}" aria-label="Close" class="press" style="width: 36px; height: 36px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; font-size: 16px">×</button>
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px">
          <sc-for list="{{diff}}" as="d" hint-placeholder-count="4">
            <div style="display: flex; gap: 8px; font-size: 14px"><span style="flex: 1; color: #A1A8B3">{{d.k}}</span><span class="num" style="color: #7E8693; text-decoration: line-through">{{d.a}}</span><span class="num" style="font-weight: 600">{{d.b}}</span></div>
          </sc-for>
        </div>
        <div style="display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; border-radius: 16px; background: #0A0B0D">
          <span style="font-size: 12px; color: #7E8693">Expected trades a month</span>
          <span class="num" style="font-size: 30px; font-weight: 700; letter-spacing: -0.02em">≈ {{est}} <span style="font-size: 14px; font-weight: 500; color: #7E8693">now ≈ {{estNow}}</span></span>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{pace}}</span>
        </div>
        <span style="font-size: 13px; color: #7E8693; line-height: 1.5">Applies at the next open, Thu 12 Nov 09:30 ET. Open trades keep their rules. Your report starts chapter 2.</span>
        <button type="button" onClick="{{apply}}" class="press" style="height: 52px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 16px; font-weight: 600; cursor: pointer">Apply at next open</button>"""

SKEL = f"""
    <sc-if value="{{{{loading}}}}" hint-placeholder-val="{{{{false}}}}">
      <div aria-busy="true" aria-label="Loading your setup" style="display: flex; flex-direction: column; gap: 20px">
        <div style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 14px"><span class="sk" style="width: 160px; height: 20px"></span><div style="display: flex; gap: 8px; flex-wrap: wrap"><span class="sk" style="width: 120px; height: 38px; border-radius: 999px"></span><span class="sk" style="width: 140px; height: 38px; border-radius: 999px"></span><span class="sk" style="width: 100px; height: 38px; border-radius: 999px"></span><span class="sk" style="width: 130px; height: 38px; border-radius: 999px"></span></div></div>
        <div style="{CARD}; padding: 8px 20px; display: flex; flex-direction: column"><span class="sk" style="height: 18px; margin: 19px 0"></span><span class="sk" style="height: 18px; margin: 19px 0"></span><span class="sk" style="height: 18px; margin: 19px 0"></span></div>
      </div>
    </sc-if>"""

H = 1700
DESK = f"""  <main class="page" style="max-width: 720px; margin: 0 auto; box-sizing: border-box; padding: 32px 24px 140px; display: flex; flex-direction: column; gap: 28px">
    <div class="enter" style="display: flex; flex-direction: column; gap: 8px">
      <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Strategy</h1>
      <span style="font-size: 15px; color: #A1A8B3; line-height: 1.5">Kaibot trades on its own. Tune it here: pickier or smaller, never riskier than Hunter.</span>
      <div class="num sumline swap" style="display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 14px; color: #7E8693; padding-top: 4px"><span><b style="color: #F4F5F7">≈ {{{{est}}}}</b> trades a month</span><span><b style="color: #F4F5F7">{{{{maxLoss}}}}</b> max loss a trade</span><span><b style="color: #F4F5F7">{{{{worstDay}}}}</b> worst day</span></div>
    </div>
{SKEL}
    <sc-if value="{{{{ready}}}}" hint-placeholder-val="{{{{true}}}}">
{FOCUS}
{RISK}
{STRATS}
{TIMING}
{MORE}
      <span class="enter" style="font-size: 13px; color: #7E8693; line-height: 1.6; padding: 0 4px">Coming later, once each passes a replay: exit style presets, more stocks, your own themes, days off, news-room strictness.</span>
    </sc-if>
  </main>
  <sc-if value="{{{{dirty}}}}" hint-placeholder-val="{{{{true}}}}">
    <div class="dock" style="display: flex; align-items: center; gap: 12px; padding: 10px 10px 10px 20px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 16px 40px rgba(0,0,0,.55)">
      <span class="num" style="flex: 1; font-size: 14px"><b>{{{{diffShort}}}}</b><span style="color: #A1A8B3"> · ≈ {{{{est}}}} trades/mo</span></span>
      <button type="button" onClick="{{{{reset}}}}" class="press" style="height: 40px; padding: 0 14px; border-radius: 999px; border: 0; background: transparent; color: #A1A8B3; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Undo</button>
      <button type="button" onClick="{{{{openSheet}}}}" disabled="{{{{cantApply}}}}" class="press" style="height: 40px; padding: 0 18px; border-radius: 999px; border: 0; background: {{{{applyBg}}}}; color: {{{{applyInk}}}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review</button>
    </div>
  </sc-if>
  <sc-if value="{{{{scheduled}}}}" hint-placeholder-val="{{{{false}}}}">
    <div class="dock" role="status" style="display: flex; align-items: center; gap: 10px; padding: 14px 20px; border-radius: 999px; background: #33270F; border: 1px solid #5A4520; color: #F5B64C; font-size: 14px; font-weight: 600">Scheduled · applies Thu 12 Nov 09:30 ET</div>
  </sc-if>
  <sc-if value="{{{{sheet}}}}" hint-placeholder-val="{{{{false}}}}">
    <div class="mwrap">
      <div onClick="{{{{closeSheet}}}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
      <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="rv-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 22px 22px 28px; display: flex; flex-direction: column; gap: 18px">
{REVIEW}
      </section>
    </div>
  </sc-if>
"""
SCRIPT = r'''class Component extends DCLogic {
  constructor(p) {
    super(p);
    this.T = {
      'Hyperscalers': ['MSFT', 'AMZN', 'GOOGL', 'META', 'ORCL'],
      'Semiconductors': ['NVDA', 'AVGO', 'AMD', 'INTC', 'QCOM', 'TXN'],
      'Big Tech': ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA'],
      'Software & IT': ['MSFT', 'ORCL', 'CRM', 'ADBE', 'CSCO'],
      'Communication & media': ['GOOGL', 'META', 'NFLX', 'DIS', 'TMUS'],
      'Consumer': ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'LOW', 'SBUX', 'BKNG'],
      'Healthcare': ['UNH', 'JNJ', 'LLY', 'MRK', 'ABBV', 'PFE', 'TMO'],
      'Financials': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'V', 'MA'],
      'Industrials & defence': ['CAT', 'BA', 'GE', 'HON', 'UPS', 'RTX', 'DE', 'LIN'],
      'Energy': ['XOM', 'CVX', 'COP'],
      'Defensives': ['PG', 'KO', 'PEP', 'COST', 'WMT', 'NEE', 'DUK', 'PLD', 'AMT'],
    };
    this.N = { MSFT: 'Microsoft', AMZN: 'Amazon', GOOGL: 'Alphabet', META: 'Meta Platforms', ORCL: 'Oracle', NVDA: 'NVIDIA', AVGO: 'Broadcom', AMD: 'AMD', INTC: 'Intel', QCOM: 'Qualcomm', TXN: 'Texas Instruments', AAPL: 'Apple', TSLA: 'Tesla', CRM: 'Salesforce', ADBE: 'Adobe', CSCO: 'Cisco', NFLX: 'Netflix', DIS: 'Disney', TMUS: 'T-Mobile US', COST: 'Costco' };
    this.C = ['#7FB2FF', '#3DDC97', '#F5B64C', '#C9A8FF', '#FF9F8A', '#8FD8E8'];
    // Hunter bounds; applied v1 = level defaults
    this.S = {
      risk: { min: 0.1, max: 1.0, step: 0.05, def: 1.0, f: v => v.toFixed(2) + '%', usd: v => '≈ $' + Math.round(5021 * v / 100) + ' a trade' },
      pos: { min: 1, max: 8, step: 1, def: 8, f: v => String(v), usd: v => 'Hunter max 8' },
      size: { min: 5, max: 20, step: 1, def: 20, f: v => v + '%', usd: v => '≈ $' + Math.round(50.21 * v).toLocaleString('en-US') + ' max' },
      entries: { min: 1, max: 30, step: 1, def: 30, f: v => String(v), usd: v => 'Hunter max 30' },
      dstop: { min: 0.25, max: 3.0, step: 0.25, def: 3.0, f: v => '−' + v.toFixed(2) + '%', usd: v => '≈ −$' + Math.round(5021 * v / 100) },
      dd: { min: 2, max: 12, step: 0.5, def: 12, f: v => '−' + v + '%', usd: v => 'ends near $' + Math.round(5024 * (1 - v / 100)).toLocaleString('en-US') },
      start: { min: 5, max: 120, step: 5, def: 5, f: v => this.clock(v), usd: v => v > 5 ? 'skips the first ' + v + ' min' : 'from 09:35' },
      end: { min: 150, max: 375, step: 5, def: 375, f: v => this.clock(v), usd: v => 'Hunter max 15:45' },
      vix: { min: 20, max: 35, step: 1, def: 35, f: v => String(v), usd: v => v < 35 ? 'VIX today 17.9' : 'only the crisis gate' },
      quiet: { min: 30, max: 90, step: 15, def: 30, f: v => '±' + v + ' min', usd: v => 'next: Thu 08:30 CPI' },
      streak: { min: 2, max: 3, step: 1, def: 3, f: v => v + ' losses', usd: v => 'then pause' },
      wait: { min: 60, max: 240, step: 30, def: 60, f: v => v + ' min', usd: v => v > 60 ? 'longer than standard' : 'standard' },
      price: { min: 5, max: 50, step: 5, def: 5, f: v => '$' + v, usd: v => '' },
      adv: { min: 50, max: 500, step: 50, def: 50, f: v => '$' + v + 'M', usd: v => '' },
      spread: { min: 1, max: 5, step: 1, def: 5, f: v => v + ' bp', usd: v => '' },
    };
    const v = {}; Object.keys(this.S).forEach(k => v[k] = this.S[k].def);
    v.risk = 0.5; v.dstop = 1.5; v.start = 30;
    this.state = { mode: 'only', themes: ['Hyperscalers', 'Semiconductors'], dropped: [], v, dip: true, spy: true, applied: false, sheet: false, ready: false, open: 'risk', edit: false, lock: false };
  }
  componentDidMount() { this._t = setTimeout(() => this.setState({ ready: true }), 1000); }
  componentWillUnmount() { clearTimeout(this._t); }
  clock(m) { const t = 9 * 60 + 30 + m; return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); }
  set(k, x) { this.setState({ v: { ...this.state.v, [k]: Number(x) }, applied: false }); }
  symbols() {
    const st = this.state; const out = [];
    st.themes.forEach(t => this.T[t].forEach(s => { if (!out.includes(s) && !st.dropped.includes(s)) out.push(s); }));
    return out;
  }
  renderVals() {
    const st = this.state, v = st.v, S = this.S;
    const loading = !!this.props.showLoading || !st.ready;
    const syms = this.symbols();
    const n = st.mode === 'only' ? syms.length : 59;
    const winFrac = Math.max(0, v.end - v.start) / (375 - 5);
    const vixF = v.vix >= 35 ? 1 : 1 - (35 - v.vix) * 0.012;
    const dipRate = st.dip ? 42 * (n / 59) * winFrac * vixF : 0;
    const spyRate = st.spy && v.end >= 375 ? 3 : 0;
    const est = Math.max(0, Math.round(Math.min(dipRate, v.entries * 21) + spyRate));
    const estNow = 45;
    const tooFew = st.mode === 'only' && syms.length < 5;
    const s = {};
    Object.keys(S).forEach(k => {
      const d = S[k]; const x = v[k];
      s[k] = { min: d.min, max: d.max, step: d.step, v: x, text: d.f(x), note: d.usd(x), changed: x !== d.def, ink: x !== d.def ? '#F4F5F7' : '#A1A8B3', lo: d.f(d.min), hi: d.f(d.max),
        open: st.open === k, openS: String(st.open === k), chev: st.open === k ? 'open-chev' : '', tap: () => this.setState({ open: st.open === k ? null : k }),
        pct: ((x - d.min) / (d.max - d.min) * 100).toFixed(1) + '%', set: e => this.set(k, e.target.value) };
    });
    const diff = [];
    if (st.mode === 'only') diff.push({ k: 'Focus', a: 'All 59', b: syms.length + ' stocks' });
    else if (st.themes.length) diff.push({ k: 'Focus', a: 'None', b: 'Prefer ' + syms.length });
    Object.keys(S).forEach(k => { if (v[k] !== S[k].def) diff.push({ k: { risk: 'Risk per trade', pos: 'Positions', size: 'Position size', entries: 'Trades a day', dstop: 'Daily stop', dd: 'Run drawdown', start: 'First trade', end: 'Last trade', vix: 'VIX sit-out', quiet: 'Fed/CPI window', streak: 'Loss streak', wait: 'Pause length', price: 'Min price', adv: 'Min $ volume', spread: 'Max spread' }[k], a: S[k].f(S[k].def), b: S[k].f(v[k]) }); });
    if (!st.dip) diff.push({ k: 'Dip reversal', a: 'On', b: 'Off' });
    if (!st.spy) diff.push({ k: 'SPY late momentum', a: 'On', b: 'Off' });
    const dirty = diff.length > 0 && !st.applied;
    ['wait', 'adv', 'spread'].forEach(k => { s[k].changed = s[k].changed; });
    s.streak.changed = s.streak.changed || s.wait.changed; s.price.changed = s.price.changed || s.adv.changed || s.spread.changed;
    s.streak.ink = s.streak.changed ? '#F4F5F7' : '#A1A8B3'; s.price.ink = s.price.changed ? '#F4F5F7' : '#A1A8B3';
    const months = est ? Math.round(900 / est) : 0;
    const pace = !est ? 'No trades expected with these settings.' : 'The go-live bar needs about 900 trades. At ≈ ' + est + ' a month that is about ' + (months >= 24 ? Math.round(months / 12) + ' years' : months + ' months') + '. A wider focus gets there faster.';
    const tog = (on) => ({ tbg: on ? '#4F95F0' : '#3A404A', tx: on ? 'translateX(20px)' : 'none' });
    const winX = 0 + (v.start / 390) * 780, winW = ((v.end - v.start) / 390) * 780;
    const fomcC = (14 * 60 - 9 * 60 - 30) / 390 * 780, fomcHalf = v.quiet / 390 * 780;
    return {
      loading, ready: !loading,
      modes: [['only', 'Only these'], ['prefer', 'Prefer these']].map(([k, nm]) => ({ name: nm, on: st.mode === k ? 'true' : 'false', bg: st.mode === k ? '#F4F5F7' : 'transparent', ink: st.mode === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ mode: k, applied: false }) })),
      modeNote: tooFew ? '“Only these” needs at least 5 stocks. Add a theme or switch to “Prefer these”.' : st.mode === 'only' ? 'Kaibot trades nothing outside these stocks.' : 'These go first; Kaibot can still trade the other 59.',
      themes: Object.keys(this.T).map(t => { const on = st.themes.includes(t); return { name: t, n: this.T[t].length, on: on ? 'true' : 'false', bg: on ? '#16263D' : '#131519', bd: on ? '#4F95F0' : '#262A31', ink: on ? '#F4F5F7' : '#A1A8B3', sub: on ? '#7FB2FF' : '#7E8693', pick: () => this.setState({ themes: on ? st.themes.filter(z => z !== t) : [...st.themes, t], applied: false }) }; }),
      picked: !syms.length ? 'All 59 stocks' : syms.slice(0, 3).join(', ') + (syms.length > 3 ? ' +' + (syms.length - 3) : '') + ' · ' + syms.length + (st.mode === 'only' ? ' in focus' : ' preferred'),
      syms: syms.map((y, i) => ({ s: y, n: this.N[y] || y, i: y.slice(0, 2), c: this.C[i % this.C.length], drop: () => this.setState({ dropped: [...st.dropped, y], applied: false }) })),
      tooFew, s,
      faces: syms.slice(0, 4).map((y, i) => ({ i: y.slice(0, 2), c: this.C[i % this.C.length] })),
      edit: st.edit, editS: String(st.edit), editText: st.edit ? 'Done' : 'Edit', editTap: () => this.setState({ edit: !st.edit }),
      modeInk: tooFew ? '#F5B64C' : '#7E8693',
      streakText: v.streak + ' losses → ' + v.wait + ' min',
      advText: (v.price === 5 && v.adv === 50 && v.spread === 5) ? 'Standard' : 'Tighter',
      lock: st.lock, lockS: String(st.lock), lockChev: st.lock ? 'open-chev' : '', lockTap: () => this.setState({ lock: !st.lock }),
      winStart: this.clock(v.start), scheduled: st.applied,
      strats: [
        { name: 'Dip reversal', blurb: 'Buys sharp intraday dips in strong stocks', on: String(st.dip), flip: () => this.setState({ dip: !st.dip || !st.spy ? true : false, applied: false }), ...tog(st.dip) },
        { name: 'SPY late momentum', blurb: 'One SPY trade late on trending days', on: String(st.spy), flip: () => this.setState({ spy: !st.spy || !st.dip ? true : false, applied: false }), ...tog(st.spy) },
      ],
      winX: winX.toFixed(1), winW: Math.max(0, winW).toFixed(1), fomcX: (fomcC - fomcHalf).toFixed(1), fomcW: (2 * fomcHalf).toFixed(1),
      dayCap: 'New trades ' + this.clock(v.start) + '–' + this.clock(v.end) + ' ET. Quiet ±' + v.quiet + ' min around Fed and CPI releases. Everything sold at 15:55.',
      dayAria: 'Trading day from 09:30 to 16:00. New trades allowed from ' + this.clock(v.start) + ' to ' + this.clock(v.end) + '.',
      locked: ['Sell everything at 15:55 ET, no overnight holds', 'No short selling, no leverage, paper money only', 'Skip a stock with market-moving news in the last 24 hours', 'Stop and target sit at the broker from the moment a trade opens'],
      est: est, estNow, estW: (Math.min(est, 60) / 60 * 300).toFixed(1), estNowW: (estNow / 60 * 300).toFixed(1), pace,
      maxLoss: '$' + Math.round(5021 * v.risk / 100), worstDay: '−$' + Math.round(5021 * v.dstop / 100),
      diff: diff, diffTitle: diff.length ? diff.length + (diff.length === 1 ? ' change' : ' changes') + ' from chapter 1' : 'No changes from chapter 1',
      diffShort: diff.length + (diff.length === 1 ? ' change' : ' changes'),
      dirty: dirty, cantApply: tooFew ? 'true' : null,
      applyBg: tooFew ? '#262A31' : '#F4F5F7', applyInk: tooFew ? '#7E8693' : '#0A0B0D',
      applyText: st.applied ? 'Scheduled for Thu 09:30' : 'Apply at next open',
      stateText: st.applied ? 'Scheduled' : (dirty ? 'Draft' : 'Applied'), stateInk: st.applied ? '#F5B64C' : (dirty ? '#7FB2FF' : '#3DDC97'), stateBg: st.applied ? '#33270F' : (dirty ? '#16263D' : '#12301F'),
      apply: () => this.setState({ applied: true, sheet: false }),
      reset: () => { const v0 = {}; Object.keys(S).forEach(k => v0[k] = S[k].def); this.setState({ v: v0, themes: [], dropped: [], mode: 'prefer', dip: true, spy: true, applied: false }); },
      sheet: st.sheet, openSheet: () => this.setState({ sheet: true }), closeSheet: () => this.setState({ sheet: false }),
    };
  }
}'''

open('../project/v3/Configure.dc.html', 'w').write(shell.head('Strategy', 'Strategy', H) + DESK + shell.tail('Strategy', SCRIPT, H))

# phone artboard: same markup, 390-wide preview, sheet open on load to show the review step
PH = 844
mob = (shell.head('Strategy on a phone', 'Strategy', PH) + DESK + shell.tail('Strategy', SCRIPT, PH))
mob = mob.replace('"width":1440', '"width":390')
open('../project/v3/ConfigureMobile.dc.html', 'w').write(mob)
print('ok')
