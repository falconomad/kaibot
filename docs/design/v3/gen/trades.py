import sys; sys.path.insert(0, '.')
from shell import head, tail, CARD
H = 1100
body = f'''  <main class="page" style="max-width: 1120px; margin: 0 auto; box-sizing: border-box; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 20px">
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 16px">
      <div style="display: flex; flex-direction: column; gap: 6px">
        <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Trades</h1>
        <span style="font-size: 15px; color: #A1A8B3">Paper · Hunter · $5,000 · since Tue 10 Nov</span>
      </div>
      <div class="num" style="display: flex; gap: 10px; flex-wrap: wrap">
        <span style="{CARD}; border-radius: 16px; padding: 10px 14px; font-size: 13px; color: #A1A8B3">Closed <b style="color: #F4F5F7">10</b></span>
        <span style="{CARD}; border-radius: 16px; padding: 10px 14px; font-size: 13px; color: #A1A8B3">Won <b style="color: #F4F5F7">5 of 10</b></span>
        <span style="{CARD}; border-radius: 16px; padding: 10px 14px; font-size: 13px; color: #A1A8B3">Net <b style="color: #3DDC97">▲ +$38.65</b></span>
      </div>
    </div>
    <div class="enter d1" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
      <div role="group" aria-label="Filter" style="display: flex; flex-wrap: wrap; gap: 6px">
        <sc-for list="{{{{filters}}}}" as="f" hint-placeholder-count="5">
          <button type="button" class="press" aria-pressed="{{{{f.pressed}}}}" onClick="{{{{f.pick}}}}" style="height: 40px; padding: 0 16px; border-radius: 999px; border: 1px solid {{{{f.border}}}}; background: {{{{f.bg}}}}; color: {{{{f.ink}}}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{{{f.label}}}} <span class="num" style="opacity: .7">{{{{f.count}}}}</span></button>
        </sc-for>
      </div>
      <div style="flex: 1"></div>
      <label class="full-sm" style="display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 14px; border-radius: 999px; background: #131519; border: 1px solid #262A31; color: #7E8693">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20 L16 16" stroke-linecap="round"></path></svg>
        <span style="position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0)">Search by stock</span>
        <input type="text" placeholder="Stock, e.g. GE" style="border: 0; background: transparent; color: #F4F5F7; font: inherit; font-size: 14px; width: 160px; outline: none">
      </label>
    </div>
    <sc-for list="{{{{days}}}}" as="d" hint-placeholder-count="2">
      <section class="card swap" aria-label="{{{{d.label}}}}" style="{CARD}; padding: 8px 8px 8px">
        <div class="num" style="display: flex; justify-content: space-between; padding: 14px 14px 8px; font-size: 13px; color: #7E8693"><span style="color: #A1A8B3; font-weight: 600">{{{{d.label}}}}</span><span style="color: {{{{d.ink}}}}; font-weight: 600">{{{{d.total}}}}</span></div>
        <sc-for list="{{{{d.rows}}}}" as="r" hint-placeholder-count="4">
          <a class="row" href="TradeDetail.dc.html" style="display: flex; align-items: center; gap: 14px; padding: 12px 14px; border-radius: 16px; color: #F4F5F7; text-decoration: none">
            <span class="num" style="flex: none; width: 46px; font-size: 13px; color: #7E8693">{{{{r.t}}}}</span>
            <span style="width: 40px; height: 40px; flex: none; border-radius: 999px; background: {{{{r.tint}}}}; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700">{{{{r.sym}}}}</span>
            <span style="flex: 1 1 200px; min-width: 0; display: flex; flex-direction: column; gap: 2px">
              <strong style="font-size: 15px">{{{{r.title}}}}</strong>
              <span class="num" style="font-size: 13px; color: #7E8693">{{{{r.sub}}}}</span>
            </span>
            <span class="hide-sm" style="flex: 0 0 140px; font-size: 13px; color: #A1A8B3">{{{{r.reason}}}}</span>
            <span style="flex: 0 0 104px; text-align: right; display: flex; flex-direction: column; gap: 2px">
              <span class="num" style="font-size: 15px; font-weight: 600; color: {{{{r.ink}}}}">{{{{r.pnl}}}}</span>
              <span class="num" style="font-size: 12px; color: #7E8693">{{{{r.r}}}}</span>
            </span>
          </a>
        </sc-for>
      </section>
    </sc-for>
    <sc-if value="{{{{empty}}}}" hint-placeholder-val="{{{{false}}}}">
      <p class="swap" style="margin: 0; text-align: center; color: #7E8693; padding: 40px 0">Nothing here for this filter.</p>
    </sc-if>
    <p style="margin: 0; font-size: 12px; color: #7E8693; text-align: center">Sample data · P&amp;L is the worse of broker and shadow-cost fills · times in Berlin</p>
  </main>
'''
script = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { f: 'all' }; }
  renderVals() {
    const T = { GE: '#1E3A5F', COP: '#3A2E1A', DE: '#1F3A2C', NFLX: '#3B1E2A', DUK: '#2A2540', NEE: '#1C3540', AMT: '#3A2A22', PLD: '#26323F', TMO: '#2F2440', JNJ: '#3A2228' };
    const all = [
      { day: 'Wed 11 Nov', t: '18:50', sym: 'COP', kind: 'open', title: 'Bought 8 COP', sub: 'in at $116.87 · open', reason: 'No-news dip', pnl: null, r: '' },
      { day: 'Wed 11 Nov', t: '18:15', sym: 'DE', kind: 'win', title: 'Sold 4 DE', sub: '$621.21 → $623.38', reason: 'Target reached', pnl: 9.32, r: '+0.7R' },
      { day: 'Wed 11 Nov', t: '17:20', sym: 'NFLX', kind: 'skip', title: 'Skipped NFLX', sub: 'not traded', reason: 'News in 24 h', pnl: null, r: '' },
      { day: 'Wed 11 Nov', t: '16:05', sym: 'GE', kind: 'open', title: 'Bought 3 GE', sub: 'in at $306.67 · open', reason: 'No-news dip', pnl: null, r: '' },
      { day: 'Tue 10 Nov', t: '21:55', sym: 'TMO', kind: 'win', title: 'Sold 1 TMO', sub: '$702.36 → $704.22', reason: 'Time exit 15:55', pnl: 1.86, r: '+0.1R' },
      { day: 'Tue 10 Nov', t: '20:40', sym: 'JNJ', kind: 'win', title: 'Sold 3 JNJ', sub: '$268.62 → $272.58', reason: 'Target reached', pnl: 11.88, r: '+2.0R' },
      { day: 'Tue 10 Nov', t: '19:05', sym: 'DUK', kind: 'loss', title: 'Sold 8 DUK', sub: '$120.32 → $119.77', reason: 'Stop hit', pnl: -4.40, r: '−1.0R' },
      { day: 'Tue 10 Nov', t: '18:30', sym: 'PLD', kind: 'win', title: 'Sold 7 PLD', sub: '$128.16 → $128.38', reason: 'Trailing stop', pnl: 1.54, r: '+0.3R' },
      { day: 'Tue 10 Nov', t: '17:45', sym: 'AMT', kind: 'loss', title: 'Sold 5 AMT', sub: '$180.64 → $178.97', reason: 'Stop hit', pnl: -8.35, r: '−1.0R' },
      { day: 'Tue 10 Nov', t: '17:10', sym: 'NEE', kind: 'win', title: 'Sold 12 NEE', sub: '$83.13 → $83.66', reason: 'Trailing stop', pnl: 6.36, r: '+0.8R' },
    ];
    const F = [['all', 'All'], ['win', 'Wins'], ['loss', 'Losses'], ['open', 'Open'], ['skip', 'Skipped']];
    const keep = (x) => this.state.f === 'all' || x.kind === this.state.f;
    const rows = all.filter(keep);
    const days = [];
    rows.forEach((x) => { let d = days.find((y) => y.label === x.day); if (!d) { d = { label: x.day, rows: [], sum: 0 }; days.push(d); } d.rows.push(x); d.sum += x.pnl || 0; });
    const money = (v) => (v >= 0 ? '▲ +$' : '▼ −$') + Math.abs(v).toFixed(2);
    return {
      filters: F.map(([k, l]) => ({ label: l, count: k === 'all' ? all.length : all.filter((x) => x.kind === k).length, pressed: this.state.f === k ? 'true' : 'false',
        bg: this.state.f === k ? '#F4F5F7' : '#131519', ink: this.state.f === k ? '#0A0B0D' : '#A1A8B3', border: this.state.f === k ? '#F4F5F7' : '#262A31', pick: () => this.setState({ f: k }) })),
      days: days.map((d) => ({ label: d.label, total: d.sum ? money(d.sum) : '', ink: d.sum >= 0 ? '#3DDC97' : '#FF6B6B',
        rows: d.rows.map((x) => ({ ...x, tint: T[x.sym] || '#1B1E24', pnl: x.pnl === null ? (x.kind === 'skip' ? '—' : 'open') : money(x.pnl),
          ink: x.pnl === null ? '#A1A8B3' : x.pnl >= 0 ? '#3DDC97' : '#FF6B6B' })) })),
      empty: rows.length === 0,
    };
  }
}'''
open('../project/v3/Trades.dc.html', 'w').write(head('Trades', 'Trades', H) + body + tail('Trades', script, H))
print('ok')
