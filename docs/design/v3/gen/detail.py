import sys; sys.path.insert(0, '.')
from shell import head, tail, CARD
H = 1250
body = f'''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 24px 40px 64px; display: flex; flex-direction: column; gap: 20px">
    <a href="Trades.dc.html" class="press" style="align-self: flex-start; display: flex; align-items: center; gap: 6px; font-size: 14px; color: #A1A8B3; text-decoration: none; min-height: 44px">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 5 L8 12 L15 19" stroke-linecap="round" stroke-linejoin="round"></path></svg>Trades</a>
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: center; gap: 18px">
      <span style="width: 56px; height: 56px; border-radius: 999px; background: #3A2228; display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 700">JNJ</span>
      <div style="flex: 1 1 260px; display: flex; flex-direction: column; gap: 4px">
        <h1 class="h1" style="margin: 0; font-size: 32px; font-weight: 700; letter-spacing: -0.02em">Johnson &amp; Johnson</h1>
        <span class="num" style="font-size: 14px; color: #A1A8B3">Dip reversal · Tue 10 Nov · 16:20 → 20:40 Berlin · 3 shares</span>
      </div>
      <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 6px">
        <span style="font-size: 12px; font-weight: 600; color: #3DDC97; background: #12301F; border-radius: 999px; padding: 5px 10px">Closed · target reached</span>
        <span class="num hero" style="font-size: 40px; font-weight: 700; letter-spacing: -0.03em; color: #3DDC97">▲ +$11.88</span>
        <span class="num" style="font-size: 14px; color: #A1A8B3">+2.0R · risked $5.94</span>
      </div>
    </div>

    <div style="display: flex; flex-wrap: wrap; gap: 20px">
      <figure class="card enter d1" style="flex: 999 1 600px; min-width: 0; margin: 0; {CARD}; padding: 20px; display: flex; flex-direction: column; gap: 10px">
        <figcaption style="display: flex; justify-content: space-between; font-size: 13px; color: #A1A8B3"><span>JNJ · 1-minute price · Tue 10 Nov</span><span class="num">Berlin time</span></figcaption>
        <svg viewBox="0 0 720 300" role="img" aria-label="Price dipped to 268.62 where Kaibot bought, held above the 266.64 stop, and rose to the 272.58 target" style="width: 100%; height: auto">
          <rect x="0" y="40" width="720" height="62" fill="#3DDC97" opacity="0.06"></rect>
          <rect x="0" y="182" width="720" height="62" fill="#FF6B6B" opacity="0.06"></rect>
          <line x1="0" y1="40" x2="720" y2="40" stroke="#3DDC97" stroke-width="1.5" stroke-dasharray="6 5"></line>
          <text x="716" y="32" text-anchor="end" font-size="12" fill="#3DDC97" font-family="Inter" font-weight="600">target $272.58</text>
          <line x1="0" y1="182" x2="720" y2="182" stroke="#7E8693" stroke-width="1"></line>
          <text x="716" y="174" text-anchor="end" font-size="12" fill="#A1A8B3" font-family="Inter">entry $268.62</text>
          <line x1="0" y1="244" x2="720" y2="244" stroke="#FF6B6B" stroke-width="1.5" stroke-dasharray="6 5"></line>
          <text x="716" y="262" text-anchor="end" font-size="12" fill="#FF6B6B" font-family="Inter" font-weight="600">stop $266.64</text>
          <path class="draw" d="M0 120 L30 128 L60 140 L90 158 L120 172 L150 186 L180 192 L200 184 L230 178 L260 186 L290 170 L320 160 L350 150 L380 152 L410 134 L440 122 L470 118 L500 104 L530 96 L560 80 L590 70 L620 56 L650 44 L670 40" fill="none" stroke="#F4F5F7" stroke-width="2.25" stroke-linejoin="round"></path>
          <circle class="fade" cx="200" cy="184" r="7" fill="#4F95F0" stroke="#131519" stroke-width="3"></circle>
          <text class="fade" x="200" y="214" text-anchor="middle" font-size="12" fill="#F4F5F7" font-family="Inter" font-weight="600">Buy 16:20</text>
          <circle class="fade" cx="670" cy="40" r="7" fill="#3DDC97" stroke="#131519" stroke-width="3"></circle>
          <text class="fade" x="640" y="20" text-anchor="middle" font-size="12" fill="#F4F5F7" font-family="Inter" font-weight="600">Sell 20:40</text>
        </svg>
      </figure>
      <section class="card enter d2" aria-label="Key numbers" style="flex: 1 1 300px; {CARD}; padding: 20px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-content: start">
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Entry (broker)</span><span class="num" style="font-size: 20px; font-weight: 700">$268.62</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Exit</span><span class="num" style="font-size: 20px; font-weight: 700">$272.58</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Stop</span><span class="num" style="font-size: 20px; font-weight: 700">$266.64</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Target (2R)</span><span class="num" style="font-size: 20px; font-weight: 700">$272.58</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Held</span><span class="num" style="font-size: 20px; font-weight: 700">4 h 20 m</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Size bound by</span><span style="font-size: 20px; font-weight: 700">20% cap</span></div>
        <p style="grid-column: 1 / -1; margin: 0; font-size: 13px; color: #7E8693; line-height: 1.45">3 shares × $268.62 = $806, under the 20% position cap of the $5,000 pot ($1,000).</p>
      </section>
    </div>

    <section class="card enter d3" style="{CARD}; padding: 8px 20px 22px; display: flex; flex-direction: column; gap: 16px">
      <div role="tablist" aria-label="Trade details" style="display: flex; gap: 4px; overflow-x: auto; border-bottom: 1px solid #262A31">
        <sc-for list="{{{{tabs}}}}" as="t" hint-placeholder-count="4">
          <button type="button" class="press" role="tab" aria-selected="{{{{t.sel}}}}" onClick="{{{{t.pick}}}}" style="flex: none; height: 52px; padding: 0 16px; border: 0; border-bottom: 2px solid {{{{t.line}}}}; background: transparent; color: {{{{t.ink}}}}; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">{{{{t.label}}}}</button>
        </sc-for>
      </div>
      <sc-if value="{{{{isWhy}}}}" hint-placeholder-val="{{{{true}}}}">
        <div class="swap" role="tabpanel" style="display: flex; flex-direction: column; gap: 14px">
          <p style="margin: 0; font-size: 16px; line-height: 1.55; max-width: 760px">JNJ fell <b>2.1σ</b> since the open while its sector barely moved, on <b>normal volume</b> and with <b>no company news</b> in 24 hours. The next 5-minute bar made a higher low, so Kaibot bought, betting the drop was noise and would partly reverse.</p>
          <div style="display: flex; flex-wrap: wrap; gap: 8px">
            <span class="num" style="background: #1B1E24; border-radius: 999px; padding: 8px 12px; font-size: 13px">Drop <b>−2.1σ</b></span>
            <span class="num" style="background: #1B1E24; border-radius: 999px; padding: 8px 12px; font-size: 13px">Stock-specific <b>0.4σ</b></span>
            <span class="num" style="background: #1B1E24; border-radius: 999px; padding: 8px 12px; font-size: 13px">Volume vs normal <b>1.1×</b></span>
            <span class="num" style="background: #1B1E24; border-radius: 999px; padding: 8px 12px; font-size: 13px">News in 24 h <b>0</b></span>
            <span class="num" style="background: #1B1E24; border-radius: 999px; padding: 8px 12px; font-size: 13px">VIX <b>19.9</b></span>
          </div>
        </div>
      </sc-if>
      <sc-if value="{{{{isGate}}}}" hint-placeholder-val="{{{{false}}}}">
        <div class="swap" role="tabpanel" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px">
          <sc-for list="{{{{checks}}}}" as="c" hint-placeholder-count="8">
            <div style="display: flex; align-items: center; gap: 12px; background: #1B1E24; border-radius: 16px; padding: 12px 14px">
              <span style="width: 24px; height: 24px; border-radius: 999px; background: #12301F; color: #3DDC97; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 700" aria-hidden="true">✓</span>
              <span style="flex: 1; font-size: 14px">{{{{c.rule}}}}</span>
              <span class="num" style="font-size: 13px; color: #A1A8B3">{{{{c.val}}}}</span>
            </div>
          </sc-for>
        </div>
      </sc-if>
      <sc-if value="{{{{isRoom}}}}" hint-placeholder-val="{{{{false}}}}">
        <div class="swap" role="tabpanel" style="display: flex; gap: 14px; align-items: flex-start; background: #1B1E24; border-radius: 16px; padding: 18px">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#A1A8B3" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 8 V12 M12 16 H12.01" stroke-linecap="round"></path></svg>
          <span style="font-size: 15px; line-height: 1.5; color: #A1A8B3">The agent room is off in M1, so this trade is rules-only and the go-live test measures the rules alone. When it's on, it can only veto a trade or halve its size, never set the price, size or exit.</span>
        </div>
      </sc-if>
      <sc-if value="{{{{isFills}}}}" hint-placeholder-val="{{{{false}}}}">
        <div class="swap" role="tabpanel" style="overflow-x: auto">
          <table class="num" style="width: 100%; border-collapse: collapse; font-size: 14px; min-width: 520px">
            <thead><tr style="color: #7E8693; font-size: 12px; text-align: left"><th style="padding: 10px 8px; font-weight: 500">Side</th><th style="padding: 10px 8px; font-weight: 500">Time</th><th style="padding: 10px 8px; font-weight: 500; text-align: right">Broker</th><th style="padding: 10px 8px; font-weight: 500; text-align: right">Shadow</th><th style="padding: 10px 8px; font-weight: 500; text-align: right">Fees</th></tr></thead>
            <tbody>
              <tr style="border-top: 1px solid #262A31"><td style="padding: 12px 8px">Buy 3</td><td style="padding: 12px 8px; color: #A1A8B3">16:20:03</td><td style="padding: 12px 8px; text-align: right">$268.62</td><td style="padding: 12px 8px; text-align: right">$268.67</td><td style="padding: 12px 8px; text-align: right">$0.00</td></tr>
              <tr style="border-top: 1px solid #262A31"><td style="padding: 12px 8px">Sell 3</td><td style="padding: 12px 8px; color: #A1A8B3">20:40:41</td><td style="padding: 12px 8px; text-align: right">$272.58</td><td style="padding: 12px 8px; text-align: right">$272.58</td><td style="padding: 12px 8px; text-align: right">$0.02</td></tr>
              <tr style="border-top: 1px solid #262A31; font-weight: 600"><td style="padding: 12px 8px" colspan="2">P&amp;L · reported = worse of the two</td><td style="padding: 12px 8px; text-align: right; color: #3DDC97">+$11.86</td><td style="padding: 12px 8px; text-align: right; color: #3DDC97">+$11.71</td><td style="padding: 12px 8px; text-align: right">→ +$11.71</td></tr>
            </tbody>
          </table>
        </div>
      </sc-if>
    </section>
    <p style="margin: 0; font-size: 12px; color: #7E8693; text-align: center">Sample data · every number here comes from rules and the risk gate, never from an AI</p>
  </main>
'''
script = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { tab: 'why' }; }
  renderVals() {
    const T = [['why', 'Why it traded'], ['gate', 'Risk checks · 13/13'], ['room', 'Agent room'], ['fills', 'Fills & costs']];
    const t = this.state.tab;
    return {
      tabs: T.map(([k, l]) => ({ label: l, sel: t === k ? 'true' : 'false', line: t === k ? '#4F95F0' : 'transparent', ink: t === k ? '#F4F5F7' : '#7E8693', pick: () => this.setState({ tab: k }) })),
      isWhy: t === 'why', isGate: t === 'gate', isRoom: t === 'room', isFills: t === 'fills',
      checks: [
        { rule: 'Risk per trade ≤ 1%', val: '$5.94 / $50' }, { rule: 'Position ≤ 20% of pot', val: '$806 / $1,000' },
        { rule: 'Open risk ≤ room to daily stop', val: '$5.94 / $150' }, { rule: 'Positions ≤ 8', val: '3 / 8' },
        { rule: 'New trades today ≤ 30', val: '4 / 30' }, { rule: 'Quote fresh', val: '1.2 s / 5 s' },
        { rule: 'Inside entry window', val: '16:20' }, { rule: 'No Fed or CPI blackout', val: 'clear' },
        { rule: 'Account reconciled', val: 'yes' }, { rule: 'Market regime on', val: 'VIX 19.9' },
      ],
    };
  }
}'''
open('../project/v3/TradeDetail.dc.html', 'w').write(head('Trade detail', 'Trades', H) + body + tail('Trades', script, H))
print('ok')
