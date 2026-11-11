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
.rail{position:sticky;top:24px}
@media (max-width: 1100px){.rail{position:static}}
@media (max-width: 760px){.rail-wrap{display:none!important}.mbar{display:flex!important}}
'''
shell.CSS = shell.CSS + EXTRA_CSS

# ---------------------------------------------------------------- shared pieces


def slider(key, label, help_):
    return f'''
        <div style="display: flex; flex-direction: column; gap: 6px">
          <div style="display: flex; align-items: baseline; gap: 8px">
            <label for="s-{key}" style="font-size: 14px; font-weight: 500">{label}</label>
            <sc-if value="{{{{s.{key}.changed}}}}" hint-placeholder-val="{{{{false}}}}"><span aria-label="changed" style="width: 6px; height: 6px; border-radius: 999px; background: #4F95F0"></span></sc-if>
            <span style="flex: 1"></span>
            <span class="num" style="font-size: 15px; font-weight: 600">{{{{s.{key}.text}}}}</span>
          </div>
          <input id="s-{key}" class="kb" type="range" min="{{{{s.{key}.min}}}}" max="{{{{s.{key}.max}}}}" step="{{{{s.{key}.step}}}}" value="{{{{s.{key}.v}}}}" onInput="{{{{s.{key}.set}}}}" style="--p: {{{{s.{key}.pct}}}}" aria-valuetext="{{{{s.{key}.text}}}}">
          <div class="num" style="display: flex; justify-content: space-between; font-size: 12px; color: #7E8693"><span>{help_}</span><span>{{{{s.{key}.note}}}}</span></div>
        </div>'''


def toggle(on, click, label):
    return (f'<button type="button" role="switch" aria-checked="{on}" aria-label="{label}" onClick="{click}" class="tog press" '
            f'style="flex: none; width: 48px; height: 28px; border-radius: 999px; border: 0; padding: 3px; cursor: pointer; background: {{{{TOGBG}}}}; display: flex">'
            f'<span style="width: 22px; height: 22px; border-radius: 999px; background: #F4F5F7; transform: {{{{TOGX}}}}"></span></button>')


FOCUS = f'''
    <section class="card enter d1" aria-labelledby="fo-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 18px">
      <div style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center">
        <div style="flex: 1 1 260px; display: flex; flex-direction: column; gap: 4px">
          <h2 id="fo-h" style="margin: 0; font-size: 18px; font-weight: 600">What to trade</h2>
          <span style="font-size: 13px; color: #A1A8B3">Pick themes or single stocks. Kaibot only looks for setups inside your focus.</span>
        </div>
        <div role="radiogroup" aria-label="Focus mode" style="display: flex; gap: 4px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 4px">
          <sc-for list="{{{{modes}}}}" as="m" hint-placeholder-count="2">
            <button type="button" role="radio" aria-checked="{{{{m.on}}}}" onClick="{{{{m.pick}}}}" class="press" style="height: 38px; padding: 0 16px; border-radius: 999px; border: 0; background: {{{{m.bg}}}}; color: {{{{m.ink}}}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{{{m.name}}}}</button>
          </sc-for>
        </div>
      </div>
      <span class="swap" style="font-size: 13px; color: #7E8693">{{{{modeNote}}}}</span>
      <div style="display: flex; flex-wrap: wrap; gap: 8px" role="group" aria-label="Themes">
        <sc-for list="{{{{themes}}}}" as="t" hint-placeholder-count="11">
          <button type="button" aria-pressed="{{{{t.on}}}}" onClick="{{{{t.pick}}}}" class="chip" style="display: inline-flex; align-items: center; gap: 8px; height: 40px; padding: 0 14px; border-radius: 999px; border: 1px solid {{{{t.bd}}}}; background: {{{{t.bg}}}}; color: {{{{t.ink}}}}; font: inherit; font-size: 14px; font-weight: 500; cursor: pointer">{{{{t.name}}}}<span class="num" style="font-size: 12px; color: {{{{t.sub}}}}">{{{{t.n}}}}</span></button>
        </sc-for>
      </div>
      <div style="display: flex; flex-direction: column; gap: 10px; border-top: 1px solid #1B1E24; padding-top: 16px">
        <div style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center">
          <span class="num" style="font-size: 14px; font-weight: 600">{{{{picked}}}}</span>
          <span style="flex: 1"></span>
          <label style="display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 14px; border-radius: 999px; border: 1px solid #262A31; background: #0A0B0D; color: #7E8693">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20 L16 16" stroke-linecap="round"></path></svg>
            <input type="text" placeholder="Add a stock, e.g. COST" aria-label="Add a stock" style="border: 0; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; width: 170px; outline: none">
          </label>
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px">
          <sc-for list="{{{{syms}}}}" as="y" hint-placeholder-count="11">
            <div class="swap lift" style="display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 14px; background: #1B1E24; border: 1px solid #262A31">
              <span aria-hidden="true" style="flex: none; width: 30px; height: 30px; border-radius: 999px; background: {{{{y.c}}}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700">{{{{y.i}}}}</span>
              <span style="flex: 1; min-width: 0; display: flex; flex-direction: column"><span style="font-size: 14px; font-weight: 600">{{{{y.s}}}}</span><span style="font-size: 11px; color: #7E8693; white-space: nowrap; overflow: hidden; text-overflow: ellipsis">{{{{y.n}}}}</span></span>
              <button type="button" aria-label="Remove {{{{y.s}}}}" onClick="{{{{y.drop}}}}" class="press" style="flex: none; width: 28px; height: 28px; border-radius: 999px; border: 0; background: transparent; color: #7E8693; cursor: pointer; font-size: 16px">×</button>
            </div>
          </sc-for>
        </div>
        <sc-if value="{{{{tooFew}}}}" hint-placeholder-val="{{{{false}}}}">
          <span class="swap" role="alert" style="font-size: 13px; color: #F5B64C">“Only these” needs at least 5 stocks, so one stock’s bad day can’t decide the run. Add more or switch to “Prefer these”.</span>
        </sc-if>
      </div>
    </section>'''

RISK = f'''
    <section class="card enter d2" aria-labelledby="rk-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 20px">
      <div style="display: flex; flex-direction: column; gap: 4px">
        <h2 id="rk-h" style="margin: 0; font-size: 18px; font-weight: 600">How much to risk</h2>
        <span style="font-size: 13px; color: #A1A8B3">Each limit can go down from your level (Hunter), never up. Dollar amounts use today’s pot, $5,021.</span>
      </div>
      <div class="cols" style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 32px">
{slider("risk", "Risk per trade", "Max loss if the stop is hit")}
{slider("pos", "Max open positions", "At the same time")}
{slider("size", "Max position size", "Share of the pot in one stock")}
{slider("entries", "Max new trades a day", "Counted from the open")}
{slider("dstop", "Daily stop", "No new trades after this loss today")}
{slider("dd", "End the run at drawdown", "From the run’s peak")}
      </div>
    </section>'''

STRATS = f'''
    <section class="card enter d3" aria-labelledby="st-h" style="{CARD}; padding: 24px 24px 12px; display: flex; flex-direction: column; gap: 6px">
      <div style="display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px">
        <h2 id="st-h" style="margin: 0; font-size: 18px; font-weight: 600">Strategies</h2>
        <span style="font-size: 13px; color: #A1A8B3">At least one stays on. Strategies under test run on paper in the background and never touch your pot.</span>
      </div>
      <sc-for list="{{{{strats}}}}" as="g" hint-placeholder-count="4">
        <div class="row" style="display: flex; align-items: center; gap: 16px; padding: 14px 0; border-top: 1px solid #1B1E24">
          <div style="flex: 1; display: flex; flex-direction: column; gap: 3px">
            <span style="display: flex; gap: 8px; align-items: center; font-size: 15px; font-weight: 600">{{{{g.name}}}}<span style="font-size: 11px; font-weight: 600; color: {{{{g.tagInk}}}}; background: {{{{g.tagBg}}}}; border-radius: 999px; padding: 3px 8px">{{{{g.tag}}}}</span></span>
            <span style="font-size: 13px; color: #A1A8B3; line-height: 1.45">{{{{g.blurb}}}}</span>
          </div>
          <sc-if value="{{{{g.can}}}}" hint-placeholder-val="{{{{true}}}}">
            {toggle("{{g.on}}", "{{g.flip}}", "{{g.name}}").replace("{{TOGBG}}", "{{g.tbg}}").replace("{{TOGX}}", "{{g.tx}}")}
          </sc-if>
          <sc-if value="{{{{g.cant}}}}" hint-placeholder-val="{{{{false}}}}">
            <span style="flex: none; font-size: 12px; color: #7E8693">{{{{g.why}}}}</span>
          </sc-if>
        </div>
      </sc-for>
    </section>'''

TIMING = f'''
    <section class="card enter d4" aria-labelledby="tm-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 20px">
      <div style="display: flex; flex-direction: column; gap: 4px">
        <h2 id="tm-h" style="margin: 0; font-size: 18px; font-weight: 600">When to trade, and when to sit out</h2>
        <span style="font-size: 13px; color: #A1A8B3">The window can only get shorter. Everything is sold at 15:55 ET every day.</span>
      </div>
      <figure style="margin: 0; display: flex; flex-direction: column; gap: 8px" aria-label="{{{{dayAria}}}}">
        <svg viewBox="0 0 780 64" width="100%" height="64" role="img" aria-hidden="true">
          <defs><pattern id="hz" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="6" fill="#F5B64C" opacity=".55"></rect></pattern></defs>
          <rect x="0" y="22" width="780" height="20" rx="10" fill="#1B1E24"></rect>
          <rect class="grow" x="{{{{winX}}}}" y="22" width="{{{{winW}}}}" height="20" rx="10" fill="#4F95F0" style="transition: x .3s var(--ease), width .3s var(--ease)"></rect>
          <rect x="{{{{fomcX}}}}" y="18" width="{{{{fomcW}}}}" height="28" rx="6" fill="url(#hz)" stroke="#F5B64C" stroke-width="1" style="transition: x .3s var(--ease), width .3s var(--ease)"></rect>
          <line x1="770" y1="12" x2="770" y2="52" stroke="#FF6B6B" stroke-width="2"></line>
          <text x="0" y="62" fill="#7E8693" font-size="11" font-family="Inter">09:30</text><text x="360" y="62" fill="#7E8693" font-size="11" font-family="Inter">12:45</text><text x="740" y="62" fill="#7E8693" font-size="11" font-family="Inter">16:00</text>
          <text x="{{{{fomcX}}}}" y="12" fill="#F5B64C" font-size="11" font-family="Inter">Fed day 14:00</text>
          <text x="700" y="10" fill="#FF6B6B" font-size="11" font-family="Inter">sell-all 15:55</text>
        </svg>
        <figcaption class="num" style="font-size: 13px; color: #A1A8B3">{{{{dayCap}}}}</figcaption>
      </figure>
      <div class="cols" style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 22px 32px">
{slider("start", "First new trade", "Earliest 09:35")}
{slider("end", "Last new trade", "Latest 15:45")}
{slider("vix", "Sit out when VIX is above", "Fear index; 35 always stops new trades")}
{slider("quiet", "Fed and CPI quiet window", "Before and after; always on")}
{slider("streak", "Pause after losses in a row", "Then wait")}
{slider("wait", "Pause length", "After a losing streak")}
      </div>
      <div style="display: flex; flex-direction: column; gap: 0; border-top: 1px solid #1B1E24; padding-top: 8px">
        <span style="font-size: 12px; font-weight: 600; letter-spacing: 0.06em; color: #7E8693; padding: 8px 0">ALWAYS ON</span>
        <sc-for list="{{{{locked}}}}" as="k" hint-placeholder-count="4">
          <div style="display: flex; gap: 12px; align-items: center; padding: 10px 0">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#7E8693" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11 V8 a4 4 0 0 1 8 0 V11"></path></svg>
            <span style="flex: 1; font-size: 14px; color: #A1A8B3">{{{{k}}}}</span>
          </div>
        </sc-for>
      </div>
    </section>'''

ADV = f'''
    <details class="card enter d5" style="{CARD}; padding: 0">
      <summary style="display: flex; align-items: center; gap: 12px; padding: 20px 24px">
        <span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 16px; font-weight: 600">Advanced</span><span style="font-size: 13px; color: #7E8693">Liquidity floors. Raising them skips thinner stocks.</span></span>
        <svg class="chev" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#A1A8B3" stroke-width="2" aria-hidden="true"><path d="M6 9 L12 15 L18 9" stroke-linecap="round" stroke-linejoin="round"></path></svg>
      </summary>
      <div class="cols" style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 22px 32px; padding: 0 24px 24px">
{slider("price", "Min share price", "Floor $5")}
{slider("adv", "Min daily $ volume", "Floor $50M")}
{slider("spread", "Max bid-ask spread", "Ceiling 5 bp")}
      </div>
    </details>
    <section class="enter d5" aria-labelledby="cl-h" style="border: 1px dashed #262A31; border-radius: 24px; padding: 20px 24px; display: flex; flex-direction: column; gap: 8px">
      <h2 id="cl-h" style="margin: 0; font-size: 14px; font-weight: 600; color: #A1A8B3">Coming later, once each passes a replay</h2>
      <span style="font-size: 13px; color: #7E8693; line-height: 1.6">Exit style presets (tighter / standard / looser) · more stocks in the catalogue (e.g. more chip and software names) · your own themes · days off (e.g. Fridays) · how strict the news room is</span>
    </section>'''

REVIEW = '''
        <div style="display: flex; align-items: center; gap: 8px">
          <h2 id="rv-h" style="margin: 0; flex: 1; font-size: 18px; font-weight: 600">Your setup</h2>
          <span class="swap" style="font-size: 12px; font-weight: 600; color: {{stateInk}}; background: {{stateBg}}; border-radius: 999px; padding: 5px 10px">{{stateText}}</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 6px">
          <span style="font-size: 12px; color: #7E8693">Expected trades a month</span>
          <div style="display: flex; align-items: baseline; gap: 10px"><span class="num swap" style="font-size: 40px; font-weight: 700; letter-spacing: -0.02em">≈ {{est}}</span><span class="num" style="font-size: 13px; color: #7E8693">now ≈ {{estNow}}</span></div>
          <svg viewBox="0 0 300 26" width="100%" height="26" aria-hidden="true">
            <rect x="0" y="4" width="300" height="8" rx="4" fill="#1B1E24"></rect>
            <rect x="0" y="4" width="{{estNowW}}" height="8" rx="4" fill="#3A404A"></rect>
            <rect class="grow" x="0" y="4" width="{{estW}}" height="8" rx="4" fill="#4F95F0" style="transition: width .35s var(--ease)"></rect>
            <line x1="{{estNowW}}" y1="0" x2="{{estNowW}}" y2="16" stroke="#A1A8B3" stroke-width="2"></line>
            <text x="0" y="25" fill="#7E8693" font-size="10" font-family="Inter">0</text><text x="276" y="25" fill="#7E8693" font-size="10" font-family="Inter">60</text>
          </svg>
          <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{pace}}</span>
        </div>
        <div class="num" style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; border-top: 1px solid #1B1E24; padding-top: 14px">
          <div style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 12px; color: #7E8693">Max loss a trade</span><span style="font-size: 18px; font-weight: 700">{{maxLoss}}</span></div>
          <div style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 12px; color: #7E8693">Worst day</span><span style="font-size: 18px; font-weight: 700">{{worstDay}}</span></div>
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px; border-top: 1px solid #1B1E24; padding-top: 14px">
          <span class="num" style="font-size: 13px; font-weight: 600">{{diffTitle}}</span>
          <sc-for list="{{diff}}" as="d" hint-placeholder-count="3">
            <div class="swap" style="display: flex; gap: 8px; font-size: 13px"><span style="flex: 1; color: #A1A8B3">{{d.k}}</span><span class="num" style="color: #7E8693; text-decoration: line-through">{{d.a}}</span><span class="num" style="font-weight: 600">{{d.b}}</span></div>
          </sc-for>
        </div>
        <sc-if value="{{dirty}}" hint-placeholder-val="{{true}}">
          <span class="swap" style="font-size: 12px; color: #7E8693; line-height: 1.5">Applies at the next open, Thu 12 Nov 09:30 ET. Open trades keep their rules. Starts chapter 2 in your report.</span>
        </sc-if>
        <div style="display: flex; gap: 8px">
          <button type="button" onClick="{{apply}}" disabled="{{cantApply}}" class="press" style="flex: 1; height: 48px; border-radius: 999px; border: 0; background: {{applyBg}}; color: {{applyInk}}; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">{{applyText}}</button>
          <button type="button" onClick="{{reset}}" class="press" style="height: 48px; padding: 0 18px; border-radius: 999px; border: 1px solid #262A31; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Reset</button>
        </div>'''

SKEL = f'''
    <sc-if value="{{{{loading}}}}" hint-placeholder-val="{{{{false}}}}">
      <div aria-busy="true" aria-label="Loading your setup" style="display: flex; gap: 24px; align-items: flex-start">
        <div style="flex: 1; display: flex; flex-direction: column; gap: 20px">
          <div style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 14px"><span class="sk" style="width: 180px; height: 18px"></span><span class="sk" style="width: 70%; height: 14px"></span><div style="display: flex; gap: 8px; flex-wrap: wrap"><span class="sk" style="width: 120px; height: 40px; border-radius: 999px"></span><span class="sk" style="width: 140px; height: 40px; border-radius: 999px"></span><span class="sk" style="width: 110px; height: 40px; border-radius: 999px"></span><span class="sk" style="width: 130px; height: 40px; border-radius: 999px"></span></div><span class="sk" style="height: 120px; border-radius: 14px"></span></div>
          <div style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 18px"><span class="sk" style="width: 160px; height: 18px"></span><span class="sk" style="height: 28px"></span><span class="sk" style="height: 28px"></span><span class="sk" style="height: 28px"></span></div>
        </div>
        <div class="rail-wrap" style="flex: 0 0 340px; {CARD}; padding: 24px; display: flex; flex-direction: column; gap: 14px"><span class="sk" style="width: 120px; height: 18px"></span><span class="sk" style="width: 160px; height: 44px"></span><span class="sk" style="height: 8px"></span><span class="sk" style="height: 60px"></span><span class="sk" style="height: 48px; border-radius: 999px"></span></div>
      </div>
    </sc-if>'''

H = 2350
DESK = f'''  <main class="page" style="max-width: 1280px; margin: 0 auto; box-sizing: border-box; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 20px">
    <div class="enter" style="display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end">
      <div style="flex: 1 1 420px; display: flex; flex-direction: column; gap: 6px">
        <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Strategy</h1>
        <span style="font-size: 15px; color: #A1A8B3">Kaibot still trades on its own. These settings make it pickier or smaller, never riskier than your level.</span>
      </div>
      <span style="font-size: 12px; font-weight: 600; color: #A1A8B3; background: #131519; border: 1px solid #262A31; border-radius: 999px; padding: 7px 12px">Level Hunter · chapter 1 since Tue 10 Nov</span>
    </div>
{SKEL}
    <sc-if value="{{{{ready}}}}" hint-placeholder-val="{{{{true}}}}">
      <div style="display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start">
        <div style="flex: 1 1 640px; min-width: 0; display: flex; flex-direction: column; gap: 20px">
{FOCUS}
{RISK}
{STRATS}
{TIMING}
{ADV}
        </div>
        <div class="rail-wrap" style="flex: 1 0 320px; max-width: 360px; align-self: stretch">
          <aside class="card rail enter d2" aria-labelledby="rv-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 16px">
{REVIEW}
          </aside>
        </div>
      </div>
    </sc-if>
  </main>
  <div class="mbar" style="display: none; position: fixed; left: 12px; right: 12px; bottom: 84px; z-index: 6; align-items: center; gap: 12px; padding: 10px 10px 10px 18px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 12px 32px rgba(0,0,0,.5)">
    <span class="num" style="flex: 1; font-size: 13px"><b>{{{{diffShort}}}}</b> · ≈ {{{{est}}}} trades/mo</span>
    <button type="button" onClick="{{{{openSheet}}}}" class="press" style="height: 40px; padding: 0 18px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review</button>
  </div>
  <sc-if value="{{{{sheet}}}}" hint-placeholder-val="{{{{false}}}}">
    <div onClick="{{{{closeSheet}}}}" style="position: fixed; inset: 0; z-index: 7; background: rgba(0,0,0,.55)" aria-hidden="true"></div>
    <section class="sheet" role="dialog" aria-modal="true" aria-labelledby="rv-h" style="position: fixed; left: 0; right: 0; bottom: 0; z-index: 8; max-height: 86%; overflow: auto; background: #131519; border-top: 1px solid #262A31; border-radius: 24px 24px 0 0; padding: 12px 20px 32px; display: flex; flex-direction: column; gap: 16px">
      <span aria-hidden="true" style="align-self: center; width: 40px; height: 4px; border-radius: 999px; background: #3A404A"></span>
{REVIEW}
    </section>
  </sc-if>
'''

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
    this.state = { mode: 'only', themes: ['Hyperscalers', 'Semiconductors'], dropped: [], v, dip: true, spy: true, applied: false, sheet: false, ready: false };
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
      s[k] = { min: d.min, max: d.max, step: d.step, v: x, text: d.f(x), note: d.usd(x), changed: x !== d.def,
        pct: ((x - d.min) / (d.max - d.min) * 100).toFixed(1) + '%', set: e => this.set(k, e.target.value) };
    });
    const diff = [];
    if (st.mode === 'only') diff.push({ k: 'Focus', a: 'All 59', b: syms.length + ' stocks' });
    else if (st.themes.length) diff.push({ k: 'Focus', a: 'None', b: 'Prefer ' + syms.length });
    Object.keys(S).forEach(k => { if (v[k] !== S[k].def) diff.push({ k: { risk: 'Risk per trade', pos: 'Positions', size: 'Position size', entries: 'Trades a day', dstop: 'Daily stop', dd: 'Run drawdown', start: 'First trade', end: 'Last trade', vix: 'VIX sit-out', quiet: 'Fed/CPI window', streak: 'Loss streak', wait: 'Pause length', price: 'Min price', adv: 'Min $ volume', spread: 'Max spread' }[k], a: S[k].f(S[k].def), b: S[k].f(v[k]) }); });
    if (!st.dip) diff.push({ k: 'Dip reversal', a: 'On', b: 'Off' });
    if (!st.spy) diff.push({ k: 'SPY late momentum', a: 'On', b: 'Off' });
    const dirty = diff.length > 0 && !st.applied;
    const months = est ? Math.round(900 / est) : 0;
    const pace = !est ? 'No trades expected with these settings.' : 'The go-live bar needs about 900 trades. At ≈ ' + est + ' a month that is about ' + (months >= 24 ? Math.round(months / 12) + ' years' : months + ' months') + '. A wider focus gets there faster.';
    const tog = (on) => ({ tbg: on ? '#4F95F0' : '#3A404A', tx: on ? 'translateX(20px)' : 'none' });
    const winX = 0 + (v.start / 390) * 780, winW = ((v.end - v.start) / 390) * 780;
    const fomcC = (14 * 60 - 9 * 60 - 30) / 390 * 780, fomcHalf = v.quiet / 390 * 780;
    return {
      loading, ready: !loading,
      modes: [['only', 'Only these'], ['prefer', 'Prefer these']].map(([k, nm]) => ({ name: nm, on: st.mode === k ? 'true' : 'false', bg: st.mode === k ? '#F4F5F7' : 'transparent', ink: st.mode === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ mode: k, applied: false }) })),
      modeNote: st.mode === 'only' ? 'Only these: Kaibot trades nothing outside your focus. SPY and sector funds are still read as reference.' : 'Prefer these: your focus goes first when slots are short; Kaibot still trades the rest of the 59.',
      themes: Object.keys(this.T).map(t => { const on = st.themes.includes(t); return { name: t, n: this.T[t].length, on: on ? 'true' : 'false', bg: on ? '#16263D' : '#131519', bd: on ? '#4F95F0' : '#262A31', ink: on ? '#F4F5F7' : '#A1A8B3', sub: on ? '#7FB2FF' : '#7E8693', pick: () => this.setState({ themes: on ? st.themes.filter(z => z !== t) : [...st.themes, t], applied: false }) }; }),
      picked: syms.length + (syms.length === 1 ? ' stock' : ' stocks') + (st.mode === 'only' ? ' in focus' : ' preferred'),
      syms: syms.map((y, i) => ({ s: y, n: this.N[y] || y, i: y.slice(0, 2), c: this.C[i % this.C.length], drop: () => this.setState({ dropped: [...st.dropped, y], applied: false }) })),
      tooFew, s,
      strats: [
        { name: 'Dip reversal', tag: 'live', tagInk: '#3DDC97', tagBg: '#12301F', blurb: 'Buys a sharp intraday dip in a strong stock, with a stop at 1.5× ATR and a target at 2R.', can: true, cant: false, on: String(st.dip), flip: () => this.setState({ dip: !st.dip || !st.spy ? true : false, applied: false }), ...tog(st.dip) },
        { name: 'SPY late momentum', tag: 'live', tagInk: '#3DDC97', tagBg: '#12301F', blurb: 'One SPY trade in the last half hour when the day has trended. Needs the window to run to 15:45.', can: true, cant: false, on: String(st.spy), flip: () => this.setState({ spy: !st.spy || !st.dip ? true : false, applied: false }), ...tog(st.spy) },
        { name: 'Opening range breakout', tag: 'under test', tagInk: '#F5B64C', tagBg: '#33270F', blurb: 'Runs on paper in the background. It can be switched on once it passes the evidence bar.', can: false, cant: true, why: 'Not yet' },
        { name: 'News continuation', tag: 'under test', tagInk: '#F5B64C', tagBg: '#33270F', blurb: 'Runs on paper in the background. It can be switched on once it passes the evidence bar.', can: false, cant: true, why: 'Not yet' },
      ],
      winX: winX.toFixed(1), winW: Math.max(0, winW).toFixed(1), fomcX: (fomcC - fomcHalf).toFixed(1), fomcW: (2 * fomcHalf).toFixed(1),
      dayCap: 'New trades ' + this.clock(v.start) + '–' + this.clock(v.end) + ' ET. Quiet ±' + v.quiet + ' min around Fed and CPI releases. Everything sold at 15:55.',
      dayAria: 'Trading day from 09:30 to 16:00. New trades allowed from ' + this.clock(v.start) + ' to ' + this.clock(v.end) + '.',
      locked: ['Sell everything at 15:55 ET, no overnight holds', 'No short selling, no leverage, paper money only', 'Skip a stock with market-moving news in the last 24 hours', 'Stop and target sit at the broker from the moment a trade opens'],
      est: est, estNow, estW: (Math.min(est, 60) / 60 * 300).toFixed(1), estNowW: (estNow / 60 * 300).toFixed(1), pace,
      maxLoss: '$' + Math.round(5021 * v.risk / 100), worstDay: '−$' + Math.round(5021 * v.dstop / 100),
      diff: diff.slice(0, 7), diffTitle: diff.length ? diff.length + (diff.length === 1 ? ' change' : ' changes') + ' from chapter 1' : 'No changes from chapter 1',
      diffShort: diff.length + (diff.length === 1 ? ' change' : ' changes'),
      dirty: dirty, cantApply: !dirty || tooFew ? 'true' : null,
      applyBg: st.applied ? '#1B1E24' : (!dirty || tooFew ? '#262A31' : '#F4F5F7'), applyInk: st.applied ? '#3DDC97' : (!dirty || tooFew ? '#7E8693' : '#0A0B0D'),
      applyText: st.applied ? 'Scheduled for Thu 09:30' : 'Apply at next open',
      stateText: st.applied ? 'Scheduled' : (dirty ? 'Draft' : 'Applied'), stateInk: st.applied ? '#F5B64C' : (dirty ? '#7FB2FF' : '#3DDC97'), stateBg: st.applied ? '#33270F' : (dirty ? '#16263D' : '#12301F'),
      apply: () => this.setState({ applied: true }),
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
