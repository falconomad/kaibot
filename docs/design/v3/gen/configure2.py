"""v3 · 09 Strategy, one-screen tune page (M5 proposal). Fine-tune list lives in configure.py."""
import sys
sys.path.insert(0, '.')
import configure  # noqa: E402,F401  (sets the shared nav, tab bar and CSS; regenerates ConfigureList)
import shell  # noqa: E402
from shell import CARD  # noqa: E402

shell.CSS += '''
.preset{transition:border-color .2s var(--ease),background-color .2s var(--ease),transform .15s var(--ease)}
.preset:active{transform:scale(.98)}
.tcard{transition:border-color .2s var(--ease),background-color .2s var(--ease),transform .15s var(--ease)}
.tcard:hover{transform:translateY(-2px)}.tcard:active{transform:scale(.97)}
.seg{transition:background-color .25s var(--ease),color .25s var(--ease)}
.pill{transition:background-color .2s var(--ease),border-color .2s var(--ease),color .2s var(--ease)}
.tgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.twocol{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:20px;align-items:start}
.presets{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
@media (max-width: 1000px){.twocol{grid-template-columns:minmax(0,1fr)}}
@media (max-width: 760px){
  .presets{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;margin:0 -16px;padding:0 16px 4px}
  .presets>*{flex:0 0 72%;scroll-snap-align:start}
  .tgrid{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;margin:0 -16px;padding:0 16px 4px}
  .tgrid>*{flex:0 0 46%;scroll-snap-align:start}
  .obar{bottom:76px!important;border-radius:20px!important}
  .ostats{font-size:12px!important;gap:10px!important}
}
'''

PRESETS = f'''
    <section class="enter d1" aria-labelledby="pr-h" style="display: flex; flex-direction: column; gap: 10px">
      <h2 id="pr-h" style="margin: 0; padding: 0 4px; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">Start from</h2>
      <div class="presets" role="radiogroup" aria-labelledby="pr-h">
        <sc-for list="{{{{presets}}}}" as="p" hint-placeholder-count="4">
          <button type="button" role="radio" aria-checked="{{{{p.on}}}}" onClick="{{{{p.pick}}}}" class="preset" style="display: flex; flex-direction: column; gap: 6px; padding: 16px; border-radius: 20px; border: 1.5px solid {{{{p.bd}}}}; background: {{{{p.bg}}}}; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer">
            <span style="display: flex; align-items: center; gap: 8px"><span style="font-size: 20px" aria-hidden="true">{{{{p.icon}}}}</span><span style="flex: 1; font-size: 15px; font-weight: 600">{{{{p.name}}}}</span><span aria-hidden="true" style="width: 18px; height: 18px; border-radius: 999px; border: 2px solid {{{{p.bd}}}}; background: {{{{p.dot}}}}; box-shadow: inset 0 0 0 3px #131519"></span></span>
            <span style="font-size: 13px; color: #A1A8B3; line-height: 1.4">{{{{p.blurb}}}}</span>
            <span class="num" style="font-size: 12px; color: #7E8693">{{{{p.stats}}}}</span>
          </button>
        </sc-for>
      </div>
    </section>'''

FOCUS = f'''
      <section class="card enter d2" aria-labelledby="fo-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">
          <h2 id="fo-h" style="margin: 0; flex: 1; font-size: 18px; font-weight: 600">Focus</h2>
          <div role="radiogroup" aria-label="Focus mode" style="display: flex; gap: 2px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 3px">
            <sc-for list="{{{{modes}}}}" as="m" hint-placeholder-count="2">
              <button type="button" role="radio" aria-checked="{{{{m.on}}}}" onClick="{{{{m.pick}}}}" class="seg" style="height: 32px; padding: 0 12px; border-radius: 999px; border: 0; background: {{{{m.bg}}}}; color: {{{{m.ink}}}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{{{m.name}}}}</button>
            </sc-for>
          </div>
        </div>
        <div class="tgrid" role="group" aria-label="Themes">
          <sc-for list="{{{{themes}}}}" as="t" hint-placeholder-count="12">
            <button type="button" aria-pressed="{{{{t.on}}}}" onClick="{{{{t.pick}}}}" class="tcard" style="position: relative; display: flex; flex-direction: column; gap: 10px; padding: 14px; border-radius: 16px; border: 1.5px solid {{{{t.bd}}}}; background: {{{{t.bg}}}}; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer; min-height: 96px">
              <span aria-hidden="true" style="display: flex">
                <sc-for list="{{{{t.faces}}}}" as="y" hint-placeholder-count="3"><span style="width: 24px; height: 24px; margin-right: -7px; border-radius: 999px; border: 2px solid {{{{t.ring}}}}; background: {{{{y.c}}}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700">{{{{y.i}}}}</span></sc-for>
              </span>
              <span style="font-size: 14px; font-weight: 600; line-height: 1.25">{{{{t.name}}}}</span>
              <span class="num" style="font-size: 12px; color: {{{{t.sub}}}}">{{{{t.meta}}}}</span>
              <sc-if value="{{{{t.check}}}}" hint-placeholder-val="{{{{false}}}}"><span aria-hidden="true" style="position: absolute; top: 10px; right: 10px; width: 20px; height: 20px; border-radius: 999px; background: #4F95F0; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 800">✓</span></sc-if>
            </button>
          </sc-for>
        </div>
        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; font-size: 13px">
          <span class="num swap" style="flex: 1; color: {{{{focusInk}}}}">{{{{focusNote}}}}</span>
          <a href="ConfigureList.dc.html" style="font-weight: 600; text-decoration: none">Exclude a stock</a>
        </div>
      </section>'''

BOLD = f'''
        <section class="card enter d3" aria-labelledby="bo-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <div style="display: flex; align-items: baseline; gap: 10px"><h2 id="bo-h" style="margin: 0; flex: 1; font-size: 18px; font-weight: 600">Boldness</h2><span class="swap" style="font-size: 15px; font-weight: 600; color: {{{{boldInk}}}}">{{{{boldName}}}}</span></div>
          <div role="radiogroup" aria-labelledby="bo-h" style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px">
            <sc-for list="{{{{bold}}}}" as="b" hint-placeholder-count="5">
              <button type="button" role="radio" aria-checked="{{{{b.on}}}}" aria-label="{{{{b.name}}}}" onClick="{{{{b.pick}}}}" class="seg" style="height: 44px; border: 0; border-radius: {{{{b.r}}}}; background: {{{{b.bg}}}}; cursor: pointer; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 8px">
                <span aria-hidden="true" style="width: 4px; height: {{{{b.h}}}}px; border-radius: 2px; background: {{{{b.tick}}}}"></span>
              </button>
            </sc-for>
          </div>
          <div style="display: flex; justify-content: space-between; font-size: 12px; color: #7E8693"><span>Careful</span><span>Max for Hunter</span></div>
          <div class="num" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; padding: 12px; border-radius: 14px; background: #0A0B0D">
            <span style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 11px; color: #7E8693">Max loss a trade</span><span class="swap" style="font-size: 16px; font-weight: 700">{{{{maxLoss}}}}</span></span>
            <span style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 11px; color: #7E8693">Stops for the day at</span><span class="swap" style="font-size: 16px; font-weight: 700">{{{{worstDay}}}}</span></span>
            <span style="display: flex; flex-direction: column; gap: 2px"><span style="font-size: 11px; color: #7E8693">Open at once</span><span class="swap" style="font-size: 16px; font-weight: 700">{{{{posText}}}}</span></span>
          </div>
        </section>'''

HOURS = f'''
        <section class="card enter d4" aria-labelledby="hr-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <h2 id="hr-h" style="margin: 0; font-size: 18px; font-weight: 600">Trading hours</h2>
          <div role="radiogroup" aria-labelledby="hr-h" style="display: flex; flex-wrap: wrap; gap: 6px">
            <sc-for list="{{{{hours}}}}" as="h" hint-placeholder-count="4">
              <button type="button" role="radio" aria-checked="{{{{h.on}}}}" onClick="{{{{h.pick}}}}" class="pill" style="height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid {{{{h.bd}}}}; background: {{{{h.bg}}}}; color: {{{{h.ink}}}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{{{h.name}}}}</button>
            </sc-for>
          </div>
          <svg viewBox="0 0 400 40" width="100%" height="40" role="img" aria-label="{{{{dayAria}}}}">
            <rect x="0" y="6" width="400" height="14" rx="7" fill="#1B1E24"></rect>
            <rect x="{{{{winX}}}}" y="6" width="{{{{winW}}}}" height="14" rx="7" fill="#4F95F0" style="transition: x .35s var(--ease), width .35s var(--ease)"></rect>
            <rect x="{{{{fomcX}}}}" y="3" width="{{{{fomcW}}}}" height="20" rx="4" fill="#F5B64C" opacity=".45" style="transition: x .35s var(--ease), width .35s var(--ease)"></rect>
            <line x1="395" y1="0" x2="395" y2="26" stroke="#FF6B6B" stroke-width="2"></line>
            <text x="0" y="38" fill="#7E8693" font-size="10" font-family="Inter">09:30</text><text x="186" y="38" fill="#7E8693" font-size="10" font-family="Inter">12:45</text><text x="370" y="38" fill="#7E8693" font-size="10" font-family="Inter">16:00</text>
          </svg>
          <span class="num swap" style="font-size: 12px; color: #7E8693">{{{{dayCap}}}}</span>
        </section>'''

SAFETY = f'''
        <section class="card enter d5" aria-labelledby="sf-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 12px">
          <h2 id="sf-h" style="margin: 0; font-size: 18px; font-weight: 600">Extra caution</h2>
          <div style="display: flex; flex-wrap: wrap; gap: 8px">
            <sc-for list="{{{{safety}}}}" as="x" hint-placeholder-count="3">
              <button type="button" role="switch" aria-checked="{{{{x.on}}}}" onClick="{{{{x.flip}}}}" class="pill" style="display: inline-flex; align-items: center; gap: 8px; height: 38px; padding: 0 14px 0 10px; border-radius: 999px; border: 1px solid {{{{x.bd}}}}; background: {{{{x.bg}}}}; color: {{{{x.ink}}}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer"><span aria-hidden="true" style="width: 18px; height: 18px; border-radius: 999px; background: {{{{x.dot}}}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 800">{{{{x.mark}}}}</span>{{{{x.name}}}}</button>
            </sc-for>
          </div>
          <span class="swap" style="font-size: 12px; color: #7E8693; line-height: 1.5">{{{{safetyNote}}}}</span>
          <a href="ConfigureList.dc.html" style="display: flex; align-items: center; gap: 8px; font-size: 13px; color: #A1A8B3; text-decoration: none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#3DDC97" stroke-width="2.5" aria-hidden="true"><path d="M5 12 L10 17 L19 7" stroke-linecap="round" stroke-linejoin="round"></path></svg>4 safety rules always on · sell-all 15:55, no shorts or leverage, news veto, broker stops</a>
        </section>'''

SKEL = f'''
    <sc-if value="{{{{loading}}}}" hint-placeholder-val="{{{{false}}}}">
      <div aria-busy="true" aria-label="Loading your setup" style="display: flex; flex-direction: column; gap: 20px">
        <div class="presets"><span class="sk" style="height: 110px; border-radius: 20px"></span><span class="sk" style="height: 110px; border-radius: 20px"></span><span class="sk" style="height: 110px; border-radius: 20px"></span><span class="sk" style="height: 110px; border-radius: 20px"></span></div>
        <div class="twocol"><span class="sk" style="height: 420px; border-radius: 24px"></span><div style="display: flex; flex-direction: column; gap: 20px"><span class="sk" style="height: 190px; border-radius: 24px"></span><span class="sk" style="height: 150px; border-radius: 24px"></span></div></div>
      </div>
    </sc-if>'''

OBAR = '''
  <div class="obar dock" role="region" aria-label="Result of these settings" style="display: flex; align-items: center; gap: 14px; padding: 10px 10px 10px 20px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 16px 40px rgba(0,0,0,.55); width: min(760px, calc(100% - 24px))">
    <div class="num ostats" style="flex: 1; display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 13px; color: #A1A8B3">
      <span><b class="swap" style="color: #F4F5F7; font-size: 15px">≈ {{est}}</b> trades/mo</span>
      <span><b class="swap" style="color: #F4F5F7; font-size: 15px">{{maxLoss}}</b> max a trade</span>
      <span class="hide-sm"><b style="color: {{chgInk}}">{{chgText}}</b></span>
    </div>
    <sc-if value="{{scheduled}}" hint-placeholder-val="{{false}}"><span class="swap" style="font-size: 13px; font-weight: 600; color: #F5B64C; padding-right: 10px">Applies Thu 09:30 ET</span></sc-if>
    <sc-if value="{{canApply}}" hint-placeholder-val="{{true}}">
      <button type="button" onClick="{{openSheet}}" class="press" style="height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review & apply</button>
    </sc-if>
  </div>
  <sc-if value="{{sheet}}" hint-placeholder-val="{{false}}">
    <div class="mwrap">
      <div onClick="{{closeSheet}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
      <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="rv-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 22px 22px 28px; display: flex; flex-direction: column; gap: 16px">
        <div style="display: flex; align-items: center; gap: 8px"><h2 id="rv-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Apply “{{presetName}}”?</h2><button type="button" onClick="{{closeSheet}}" aria-label="Close" class="press" style="width: 36px; height: 36px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; font-size: 16px">×</button></div>
        <sc-for list="{{diff}}" as="d" hint-placeholder-count="4"><div style="display: flex; gap: 8px; font-size: 14px"><span style="flex: 1; color: #A1A8B3">{{d.k}}</span><span class="num" style="color: #7E8693; text-decoration: line-through">{{d.a}}</span><span class="num" style="font-weight: 600">{{d.b}}</span></div></sc-for>
        <div style="padding: 14px 16px; border-radius: 16px; background: #0A0B0D; display: flex; flex-direction: column; gap: 4px"><span class="num" style="font-size: 24px; font-weight: 700">≈ {{est}} trades a month <span style="font-size: 13px; font-weight: 500; color: #7E8693">now ≈ 45</span></span><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{pace}}</span></div>
        <span style="font-size: 13px; color: #7E8693; line-height: 1.5">Starts at the next open, Thu 12 Nov 09:30 ET. Open trades keep their rules. Your report starts chapter 2.</span>
        <button type="button" onClick="{{apply}}" class="press" style="height: 52px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 16px; font-weight: 600; cursor: pointer">Apply at next open</button>
      </section>
    </div>
  </sc-if>
'''

H = 1250
PAGE = f'''  <main class="page" style="max-width: 1120px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 140px; display: flex; flex-direction: column; gap: 22px">
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 20px">
      <div style="flex: 1 1 360px; display: flex; flex-direction: column; gap: 6px">
        <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Strategy</h1>
        <span style="font-size: 15px; color: #A1A8B3">Kaibot trades on its own. Pick a starting point, nudge it, done.</span>
      </div>
      <a href="ConfigureList.dc.html" class="press" style="display: inline-flex; align-items: center; gap: 8px; height: 40px; padding: 0 16px; border-radius: 999px; border: 1px solid #262A31; color: #F4F5F7; font-size: 14px; font-weight: 600; text-decoration: none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20" stroke-linecap="round"></path><circle cx="16" cy="7" r="2"></circle><circle cx="10" cy="17" r="2"></circle></svg>Fine-tune all settings</a>
    </div>
{SKEL}
    <sc-if value="{{{{ready}}}}" hint-placeholder-val="{{{{true}}}}">
{PRESETS}
      <div class="twocol">
{FOCUS}
        <div style="display: flex; flex-direction: column; gap: 20px">
{BOLD}
{HOURS}
{SAFETY}
        </div>
      </div>
    </sc-if>
  </main>
{OBAR}'''

SCRIPT = r'''class Component extends DCLogic {
  constructor(p) {
    super(p);
    this.T = [
      ['All stocks', null], ['Hyperscalers', ['MSFT', 'AMZN', 'GOOGL', 'META', 'ORCL']], ['Semiconductors', ['NVDA', 'AVGO', 'AMD', 'INTC', 'QCOM', 'TXN']],
      ['Big Tech', ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA']], ['Software & IT', ['MSFT', 'ORCL', 'CRM', 'ADBE', 'CSCO']],
      ['Media & telecom', ['GOOGL', 'META', 'NFLX', 'DIS', 'TMUS']], ['Consumer', ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'LOW', 'SBUX', 'BKNG']],
      ['Healthcare', ['UNH', 'JNJ', 'LLY', 'MRK', 'ABBV', 'PFE', 'TMO']], ['Financials', ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'V', 'MA']],
      ['Industrials', ['CAT', 'BA', 'GE', 'HON', 'UPS', 'RTX', 'DE', 'LIN']], ['Energy', ['XOM', 'CVX', 'COP']], ['Defensives', ['PG', 'KO', 'PEP', 'COST', 'WMT', 'NEE', 'DUK', 'PLD', 'AMT']],
    ];
    this.C = ['#7FB2FF', '#3DDC97', '#F5B64C', '#C9A8FF', '#FF9F8A', '#8FD8E8'];
    this.B = [
      { name: 'Careful', risk: 0.25, dstop: 0.75, pos: 3 }, { name: 'Steady', risk: 0.35, dstop: 1.0, pos: 4 }, { name: 'Moderate', risk: 0.5, dstop: 1.5, pos: 5 },
      { name: 'Bold', risk: 0.75, dstop: 2.0, pos: 6 }, { name: 'Max', risk: 1.0, dstop: 3.0, pos: 8 },
    ];
    this.H = [['all', 'All day', 5, 375], ['skip', 'Skip the open', 30, 375], ['am', 'Mornings', 5, 180], ['pm', 'Afternoons', 180, 375]];
    this.P = {
      def: { name: 'Kaibot default', icon: '⚡', blurb: 'All 59 stocks, the full Hunter limits, all day.', themes: ['All stocks'], mode: 'prefer', bold: 4, hours: 'all', vix: false, pause: false, quiet: false },
      tech: { name: 'Big tech', icon: '◆', blurb: 'Big Tech, hyperscalers and chips only. Moderate size.', themes: ['Big Tech', 'Hyperscalers', 'Semiconductors'], mode: 'only', bold: 2, hours: 'skip', vix: false, pause: false, quiet: false },
      calm: { name: 'Calm', icon: '☾', blurb: 'All stocks, small trades, sits out shaky days.', themes: ['All stocks'], mode: 'prefer', bold: 0, hours: 'skip', vix: true, pause: true, quiet: true },
    };
    this.state = { ...this.P.tech, preset: 'tech', applied: false, sheet: false, ready: false };
  }
  componentDidMount() { this._t = setTimeout(() => this.setState({ ready: true }), 1000); }
  componentWillUnmount() { clearTimeout(this._t); }
  clock(m) { const t = 570 + m; return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); }
  edit(ch) { this.setState({ ...ch, preset: 'custom', applied: false }); }
  syms(themes) { if (themes.includes('All stocks')) return null; const o = []; this.T.forEach(([t, s]) => { if (themes.includes(t)) s.forEach(x => { if (!o.includes(x)) o.push(x); }); }); return o; }
  estimate(c) {
    const s = this.syms(c.themes); const n = !s || c.mode === 'prefer' ? 59 : s.length;
    const h = this.H.find(x => x[0] === c.hours); const win = (h[3] - h[2]) / 370;
    const spy = h[3] >= 375 ? 3 : 0; const vix = c.vix ? 0.85 : 1; const b = this.B[c.bold];
    return Math.round(42 * (n / 59) * win * vix * Math.min(1, b.pos / 5 + 0.3) + spy);
  }
  renderVals() {
    const st = this.state; const loading = !!this.props.showLoading || !st.ready;
    const syms = this.syms(st.themes); const b = this.B[st.bold]; const h = this.H.find(x => x[0] === st.hours);
    const est = this.estimate(st);
    const usd = x => '$' + Math.round(5021 * x / 100);
    const presets = ['def', 'tech', 'calm'].map(k => { const p = this.P[k]; const on = st.preset === k;
      return { icon: p.icon, name: p.name, blurb: p.blurb, stats: '≈ ' + this.estimate(p) + ' trades/mo · ' + usd(this.B[p.bold].risk) + ' max a trade',
        on: String(on), bd: on ? '#4F95F0' : '#262A31', bg: on ? '#16263D' : '#131519', dot: on ? '#4F95F0' : 'transparent',
        pick: () => this.setState({ ...p, preset: k, applied: false }) }; });
    const cus = st.preset === 'custom';
    presets.push({ icon: '✎', name: 'Custom', blurb: cus ? 'Your own mix. Tap a card below to keep shaping it.' : 'Change anything below and it lands here.', stats: cus ? '≈ ' + est + ' trades/mo · ' + usd(b.risk) + ' max a trade' : '—',
      on: String(cus), bd: cus ? '#4F95F0' : '#262A31', bg: cus ? '#16263D' : '#131519', dot: cus ? '#4F95F0' : 'transparent', pick: () => {} });
    const tooFew = st.mode === 'only' && syms && syms.length < 5;
    const themes = this.T.map(([t, s]) => { const on = st.themes.includes(t);
      const faces = (s || ['AAPL', 'JPM', 'XOM']).slice(0, 3).map((y, i) => ({ i: y.slice(0, 2), c: this.C[(i + t.length) % this.C.length] }));
      const n = s ? s.length : 59;
      return { name: t, faces, on: String(on), check: on, ring: on ? '#16263D' : '#1B1E24', bd: on ? '#4F95F0' : '#262A31', bg: on ? '#16263D' : '#1B1E24', sub: on ? '#7FB2FF' : '#7E8693',
        meta: n + ' stocks · ≈ ' + Math.round(42 * n / 59) + '/mo',
        pick: () => { let th = t === 'All stocks' ? ['All stocks'] : (on ? st.themes.filter(z => z !== t) : [...st.themes.filter(z => z !== 'All stocks'), t]);
          if (!th.length) th = ['All stocks']; this.edit({ themes: th, mode: th.includes('All stocks') ? 'prefer' : st.mode }); } }; });
    const winX = h[2] / 390 * 400, winW = (h[3] - h[2]) / 390 * 400; const q = st.quiet ? 60 : 30;
    const fx = (14 * 60 - 570) / 390 * 400;
    const def = this.P.def; const diff = [];
    const focusTxt = c => { const s = this.syms(c.themes); return !s ? 'All 59' : (c.mode === 'only' ? 'Only ' : 'Prefer ') + s.length; };
    if (focusTxt(st) !== focusTxt(def)) diff.push({ k: 'Focus', a: focusTxt(def), b: focusTxt(st) });
    if (st.bold !== def.bold) diff.push({ k: 'Boldness', a: this.B[def.bold].name, b: b.name });
    if (st.hours !== def.hours) diff.push({ k: 'Hours', a: 'All day', b: h[1] });
    [['vix', 'Sit out fear spikes'], ['pause', 'Longer pause after losses'], ['quiet', 'Wider Fed/CPI quiet']].forEach(([k, n]) => { if (st[k] !== def[k]) diff.push({ k: n, a: 'Off', b: 'On' }); });
    const months = est ? Math.round(900 / est) : 0;
    const name = st.preset === 'custom' ? 'Custom' : this.P[st.preset].name;
    return {
      loading, ready: !loading, presets, themes,
      modes: [['prefer', 'Prefer'], ['only', 'Only']].map(([k, n]) => ({ name: n, on: String(st.mode === k), bg: st.mode === k ? '#F4F5F7' : 'transparent', ink: st.mode === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.edit({ mode: k }) })),
      focusNote: tooFew ? '“Only” needs at least 5 stocks. Add a theme or switch to Prefer.' : !syms ? 'All 59 stocks. Pick themes to narrow it.' : (st.mode === 'only' ? 'Only these ' : 'These first, then the rest · ') + syms.length + ' stocks: ' + syms.slice(0, 4).join(', ') + (syms.length > 4 ? ' +' + (syms.length - 4) : ''),
      focusInk: tooFew ? '#F5B64C' : '#A1A8B3',
      bold: this.B.map((x, i) => ({ name: x.name, on: String(i === st.bold), bg: i <= st.bold ? (i === st.bold ? '#4F95F0' : '#2D4F7A') : '#1B1E24', tick: i <= st.bold ? '#F4F5F7' : '#3A404A', h: 8 + i * 5,
        r: i === 0 ? '12px 4px 4px 12px' : (i === 4 ? '4px 12px 12px 4px' : '4px'), pick: () => this.edit({ bold: i }) })),
      boldName: b.name, boldInk: st.bold === 4 ? '#F5B64C' : '#F4F5F7',
      maxLoss: usd(b.risk), worstDay: '−' + usd(b.dstop), posText: b.pos + ' trades',
      hours: this.H.map(x => ({ name: x[1], on: String(x[0] === st.hours), bd: x[0] === st.hours ? '#4F95F0' : '#262A31', bg: x[0] === st.hours ? '#16263D' : 'transparent', ink: x[0] === st.hours ? '#F4F5F7' : '#A1A8B3', pick: () => this.edit({ hours: x[0] }) })),
      winX: winX.toFixed(1), winW: winW.toFixed(1), fomcX: (fx - q / 390 * 400).toFixed(1), fomcW: (2 * q / 390 * 400).toFixed(1),
      dayCap: 'New trades ' + this.clock(h[2]) + '–' + this.clock(h[3]) + ' ET · amber = Fed/CPI quiet (±' + q + ' min) · red = sell-all 15:55',
      dayAria: 'New trades from ' + this.clock(h[2]) + ' to ' + this.clock(h[3]) + ' Eastern',
      safety: [['vix', 'Sit out fear spikes'], ['pause', 'Longer pause after losses'], ['quiet', 'Wider Fed/CPI quiet']].map(([k, n]) => { const on = st[k];
        return { name: n, on: String(on), bd: on ? '#3DDC97' : '#262A31', bg: on ? '#12301F' : 'transparent', ink: on ? '#F4F5F7' : '#A1A8B3', dot: on ? '#3DDC97' : '#3A404A', mark: on ? '✓' : '', flip: () => this.edit({ [k]: !on }) }; }),
      safetyNote: [st.vix ? 'No new trades when VIX is above 25.' : null, st.pause ? '2 losses in a row → 2 h pause.' : null, st.quiet ? '±60 min around Fed and CPI.' : null].filter(Boolean).join(' ') || 'Standard: VIX above 35 stops new trades, 3 losses → 1 h pause, ±30 min around Fed and CPI.',
      est, chgText: diff.length ? diff.length + (diff.length === 1 ? ' change' : ' changes') + ' vs default' : 'Same as default', chgInk: diff.length ? '#7FB2FF' : '#7E8693',
      canApply: diff.length > 0 && !st.applied && !tooFew, scheduled: st.applied, presetName: name, diff,
      pace: !est ? 'No trades expected.' : 'The go-live bar needs about 900 trades: about ' + (months >= 24 ? Math.round(months / 12) + ' years' : months + ' months') + ' at this pace.',
      sheet: st.sheet, openSheet: () => this.setState({ sheet: true }), closeSheet: () => this.setState({ sheet: false }), apply: () => this.setState({ applied: true, sheet: false }),
    };
  }
}'''

open('../project/v3/Configure.dc.html', 'w').write(shell.head('Strategy', 'Strategy', H) + PAGE + shell.tail('Strategy', SCRIPT, H))
mob = shell.head('Strategy on a phone', 'Strategy', 844) + PAGE + shell.tail('Strategy', SCRIPT, 844)
open('../project/v3/ConfigureMobile.dc.html', 'w').write(mob.replace('"width":1440', '"width":390'))
print('ok')
