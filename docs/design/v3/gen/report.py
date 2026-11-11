import sys; sys.path.insert(0, '.')
from shell import head, tail, CARD
H = 1500
tile = lambda label, val, sub, ink='#F4F5F7', d='d1': f'''        <div class="card lift enter {d}" style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 6px">
          <span style="font-size: 13px; color: #7E8693">{label}</span>
          <span class="num" style="font-size: 28px; font-weight: 700; letter-spacing: -0.02em; color: {ink}">{val}</span>
          <span class="num" style="font-size: 13px; color: #A1A8B3">{sub}</span>
        </div>
'''
body = f'''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 24px">
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px">
      <div style="display: flex; flex-direction: column; gap: 6px">
        <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Report</h1>
        <span style="font-size: 15px; color: #A1A8B3">Does the strategy beat SPY and cash after costs? Judged only against rules set in advance.</span>
      </div>
      <div role="group" aria-label="Period" style="display: flex; gap: 6px">
        <sc-for list="{{{{periods}}}}" as="p" hint-placeholder-count="3">
          <button type="button" class="press" aria-pressed="{{{{p.pressed}}}}" onClick="{{{{p.pick}}}}" style="height: 40px; padding: 0 16px; border-radius: 999px; border: 0; background: {{{{p.bg}}}}; color: {{{{p.ink}}}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{{{p.label}}}}</button>
        </sc-for>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px">
{tile('Net after costs', '▲ +$18.04', '+0.36% · SPY −0.21% · cash +0.02%', '#3DDC97', 'd1')}{tile('Win rate', '50%', '5 won · 5 lost · 2 open', '#F4F5F7', 'd2')}{tile('Avg win / avg loss', '$6.16 / $6.38', 'payoff 0.97 : 1', '#F4F5F7', 'd3')}{tile('Profit factor', '2.41', 'gross wins ÷ gross losses', '#F4F5F7', 'd4')}{tile('Worst drawdown', '0.3%', 'run ends at 12% from peak', '#F4F5F7', 'd5')}    </div>

    <section class="card enter d2" aria-labelledby="gl-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 16px">
      <div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: baseline">
        <h2 id="gl-h" style="margin: 0; font-size: 18px; font-weight: 600">Go-live test</h2>
        <span style="font-size: 13px; color: #F5B64C; font-weight: 600">Not enough evidence yet · can only rule out</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px">
        <sc-for list="{{{{criteria}}}}" as="c" hint-placeholder-count="6">
          <div style="background: #1B1E24; border-radius: 16px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px">
            <div style="display: flex; justify-content: space-between; gap: 10px; font-size: 14px"><span style="font-weight: 500">{{{{c.name}}}}</span><span class="num" style="color: {{{{c.ink}}}}; font-weight: 600">{{{{c.state}}}}</span></div>
            <div style="height: 6px; border-radius: 6px; background: #262A31; overflow: hidden" aria-hidden="true"><div class="grow" style="height: 6px; width: {{{{c.pct}}}}; border-radius: 6px; background: {{{{c.bar}}}}"></div></div>
            <span class="num" style="font-size: 12px; color: #7E8693">{{{{c.detail}}}}</span>
          </div>
        </sc-for>
      </div>
    </section>

    <div style="display: flex; flex-wrap: wrap; gap: 20px">
      <figure class="card enter d3" style="flex: 999 1 560px; min-width: 0; margin: 0; {CARD}; padding: 22px; display: flex; flex-direction: column; gap: 12px">
        <figcaption style="display: flex; justify-content: space-between; align-items: baseline"><strong style="font-size: 18px; font-weight: 600">Daily P&amp;L</strong><span style="font-size: 13px; color: #7E8693">each trading day adds a bar</span></figcaption>
        <svg viewBox="0 0 640 220" role="img" aria-label="Tue 10 Nov plus $8.73, Wed 11 Nov plus $9.31 realized so far" style="width: 100%; height: auto">
          <line x1="0" y1="140" x2="640" y2="140" stroke="#3A404A" stroke-width="1"></line>
          <text x="0" y="156" font-size="11" fill="#7E8693" font-family="Inter">$0</text>
          <rect class="grow" x="40" y="104" width="44" height="36" rx="4" fill="#3DDC97" style="transform-origin: 62px 140px; transform-box: view-box"></rect>
          <text x="62" y="96" text-anchor="middle" font-size="12" fill="#F4F5F7" font-family="Inter" font-weight="600">+$8.73</text>
          <text x="62" y="174" text-anchor="middle" font-size="12" fill="#A1A8B3" font-family="Inter">Tue 10</text>
          <rect class="grow" x="100" y="102" width="44" height="38" rx="4" fill="#3DDC97" style="transform-origin: 122px 140px; transform-box: view-box"></rect>
          <text x="122" y="94" text-anchor="middle" font-size="12" fill="#F4F5F7" font-family="Inter" font-weight="600">+$9.31</text>
          <text x="122" y="174" text-anchor="middle" font-size="12" fill="#A1A8B3" font-family="Inter">Wed 11</text>
          <rect x="160" y="60" width="44" height="80" rx="4" fill="none" stroke="#262A31" stroke-dasharray="4 4"></rect>
          <rect x="220" y="60" width="44" height="80" rx="4" fill="none" stroke="#262A31" stroke-dasharray="4 4"></rect>
          <rect x="280" y="60" width="44" height="80" rx="4" fill="none" stroke="#262A31" stroke-dasharray="4 4"></rect>
          <text x="420" y="110" font-size="13" fill="#7E8693" font-family="Inter">Expect 4–5 red days in 10.</text>
        </svg>
      </figure>
      <section class="card enter d4" aria-labelledby="ex-h" style="flex: 1 1 360px; {CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
        <h2 id="ex-h" style="margin: 0; font-size: 18px; font-weight: 600">How trades ended</h2>
        <sc-for list="{{{{exits}}}}" as="e" hint-placeholder-count="4">
          <div style="display: flex; flex-direction: column; gap: 6px">
            <div class="num" style="display: flex; justify-content: space-between; font-size: 14px"><span>{{{{e.name}}}} <span style="color: #7E8693">· {{{{e.n}}}}</span></span><span style="color: {{{{e.ink}}}}; font-weight: 600">{{{{e.avg}}}}</span></div>
            <div style="height: 8px; border-radius: 8px; background: #1B1E24; overflow: hidden" aria-hidden="true"><div class="grow" style="height: 8px; width: {{{{e.pct}}}}; border-radius: 8px; background: #4F95F0"></div></div>
          </div>
        </sc-for>
        <span style="font-size: 12px; color: #7E8693">Average result per trade, in R (1R = the amount risked).</span>
      </section>
    </div>

    <div style="display: flex; flex-wrap: wrap; gap: 20px">
      <section class="card enter d4" aria-labelledby="st-h" style="flex: 1 1 460px; {CARD}; padding: 22px 8px 10px; display: flex; flex-direction: column">
        <h2 id="st-h" style="margin: 0 14px 10px; font-size: 18px; font-weight: 600">By stock</h2>
        <sc-for list="{{{{stocks}}}}" as="s" hint-placeholder-count="6">
          <div class="row" style="display: flex; align-items: center; gap: 12px; padding: 10px 14px; border-radius: 14px">
            <span style="width: 34px; height: 34px; border-radius: 999px; background: {{{{s.tint}}}}; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700">{{{{s.sym}}}}</span>
            <span style="flex: 1; font-size: 14px">{{{{s.name}}}}</span>
            <span class="num" style="font-size: 13px; color: #7E8693">{{{{s.n}}}}</span>
            <span class="num" style="width: 84px; text-align: right; font-size: 14px; font-weight: 600; color: {{{{s.ink}}}}">{{{{s.pnl}}}}</span>
          </div>
        </sc-for>
      </section>
      <section class="card enter d5" aria-labelledby="co-h" style="flex: 1 1 460px; {CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
        <h2 id="co-h" style="margin: 0; font-size: 18px; font-weight: 600">Costs: broker vs our cost model</h2>
        <p style="margin: 0; font-size: 14px; color: #A1A8B3; line-height: 1.5">Paper fills can be kinder than real ones. Kaibot re-prices every fill with a stricter model (spread, impact, fees) and always reports the worse of the two.</p>
        <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px">
          <div style="background: #1B1E24; border-radius: 16px; padding: 14px; display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Broker P&amp;L</span><span class="num" style="font-size: 22px; font-weight: 700">+$19.62</span></div>
          <div style="background: #1B1E24; border-radius: 16px; padding: 14px; display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Cost-model P&amp;L · reported</span><span class="num" style="font-size: 22px; font-weight: 700; color: #3DDC97">+$18.04</span></div>
        </div>
        <span class="num" style="font-size: 13px; color: #A1A8B3">Model costs $0.16 more per trade on average (0.03R).</span>
      </section>
    </div>
    <p style="margin: 0; font-size: 12px; color: #7E8693; text-align: center">Sample data · run so far: 2 sessions, 10 closed trades</p>
  </main>
'''
script = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { period: 'run' }; }
  renderVals() {
    const P = [['run', 'Run so far'], ['week', 'This week'], ['month', 'This month']];
    const C = [
      { name: 'Trades', state: '10 / 900', pct: '1.1%', bar: '#4F95F0', ink: '#A1A8B3', detail: 'at least 900 closed trades' },
      { name: 'Time', state: '2 days / 3 months', pct: '3%', bar: '#4F95F0', ink: '#A1A8B3', detail: 'at least 3 months of paper trading' },
      { name: 't-stat after costs', state: '0.9 / 3.0', pct: '30%', bar: '#4F95F0', ink: '#A1A8B3', detail: 'how sure the average trade is above zero' },
      { name: 'Beats random entries', state: 'not run yet', pct: '0%', bar: '#4F95F0', ink: '#A1A8B3', detail: 'above the 95th percentile of 1,000 random runs' },
      { name: 'Risk-adjusted vs SPY', state: 'ahead', pct: '100%', bar: '#3DDC97', ink: '#3DDC97', detail: 'Sharpe above SPY over the same days' },
      { name: 'Drawdown within cap', state: '0.3% / 12%', pct: '2.5%', bar: '#3DDC97', ink: '#3DDC97', detail: 'worst fall from peak' },
    ];
    const tint = { JNJ: '#3A2228', DE: '#1F3A2C', NEE: '#1C3540', TMO: '#2F2440', PLD: '#26323F', DUK: '#2A2540', AMT: '#3A2A22' };
    const S = [['JNJ', 'Johnson & Johnson', 1, 11.71], ['DE', 'Deere', 1, 9.32], ['NEE', 'NextEra Energy', 1, 6.36], ['TMO', 'Thermo Fisher', 1, 1.86], ['PLD', 'Prologis', 1, 1.54], ['DUK', 'Duke Energy', 1, -4.40], ['AMT', 'American Tower', 1, -8.35]];
    const money = (v) => (v >= 0 ? '▲ +$' : '▼ −$') + Math.abs(v).toFixed(2);
    return {
      periods: P.map(([k, l]) => ({ label: l, pressed: this.state.period === k ? 'true' : 'false', bg: this.state.period === k ? '#F4F5F7' : '#131519', ink: this.state.period === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ period: k }) })),
      criteria: C,
      exits: [
        { name: 'Target reached', n: 2, avg: '+1.4R', ink: '#3DDC97', pct: '20%' }, { name: 'Trailing stop', n: 2, avg: '+0.6R', ink: '#3DDC97', pct: '20%' },
        { name: 'Stop hit', n: 2, avg: '−1.0R', ink: '#FF6B6B', pct: '20%' }, { name: 'Time exit 15:55', n: 4, avg: '+0.1R', ink: '#3DDC97', pct: '40%' },
      ],
      stocks: S.map(([sym, name, n, p]) => ({ sym: sym, name: name, n: n + (n === 1 ? ' trade' : ' trades'), pnl: money(p), ink: p >= 0 ? '#3DDC97' : '#FF6B6B', tint: tint[sym] })),
    };
  }
}'''
open('../project/v3/Report.dc.html', 'w').write(head('Report', 'Report', H) + body + tail('Report', script, H))
print('ok')
