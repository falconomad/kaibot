import sys; sys.path.insert(0, '.')
from shell import head, tail, CARD
H = 1200
body = f'''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 20px">
    <section class="card enter" aria-labelledby="eh-h" style="{CARD}; padding: 24px; display: flex; flex-wrap: wrap; align-items: center; gap: 24px; background: radial-gradient(90% 140% at 0% 0%, #10261D 0%, #131519 55%)">
      <span class="live-dot" aria-hidden="true" style="width: 18px; height: 18px; background: #3DDC97; flex: none"></span>
      <div style="flex: 1 1 320px; display: flex; flex-direction: column; gap: 6px">
        <h1 id="eh-h" class="h1" style="margin: 0; font-size: 34px; font-weight: 700; letter-spacing: -0.02em">Engine is running</h1>
        <span class="num" style="font-size: 15px; color: #A1A8B3">On Mac mini · Docker · since 16:29 · code 016898f · last heartbeat 6 s ago</span>
      </div>
      <div class="num" style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; flex: 1 1 420px">
        <div style="background: rgba(255,255,255,.04); border-radius: 16px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Minute ticks today</span><span style="font-size: 22px; font-weight: 700">253 <span style="font-size: 13px; color: #7E8693; font-weight: 500">/ 253</span></span></div>
        <div style="background: rgba(255,255,255,.04); border-radius: 16px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">5-min scans</span><span style="font-size: 22px; font-weight: 700">50</span></div>
        <div style="background: rgba(255,255,255,.04); border-radius: 16px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Errors today</span><span style="font-size: 22px; font-weight: 700">0</span></div>
      </div>
    </section>

    <section class="card enter d1" aria-labelledby="hb-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
      <div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px"><h2 id="hb-h" style="margin: 0; font-size: 18px; font-weight: 600">Last 60 minutes</h2><span style="font-size: 13px; color: #7E8693">one bar per minute · hover for detail</span></div>
      <div role="img" aria-label="60 minute ticks, 58 on time, 2 slow during the 5-minute scans" style="display: grid; grid-template-columns: repeat(60, minmax(0, 1fr)); gap: 3px; height: 44px; align-items: end">
        <sc-for list="{{{{beats}}}}" as="b" hint-placeholder-count="60">
          <span title="{{{{b.tip}}}}" style="display: block; height: {{{{b.h}}}}; border-radius: 3px; background: {{{{b.c}}}}; transition: height .4s cubic-bezier(.2,.7,.2,1)"></span>
        </sc-for>
      </div>
      <div class="num" style="display: flex; justify-content: space-between; font-size: 12px; color: #7E8693"><span>19:13</span><span>now 20:13</span></div>
      <div style="display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: #A1A8B3">
        <span style="display: flex; align-items: center; gap: 6px"><span aria-hidden="true" style="width: 10px; height: 10px; border-radius: 3px; background: #3DDC97"></span>on time (&lt; 10 s)</span>
        <span style="display: flex; align-items: center; gap: 6px"><span aria-hidden="true" style="width: 10px; height: 10px; border-radius: 3px; background: #F5B64C"></span>slow (10–40 s)</span>
        <span style="display: flex; align-items: center; gap: 6px"><span aria-hidden="true" style="width: 10px; height: 10px; border-radius: 3px; background: #FF6B6B"></span>missed</span>
      </div>
    </section>

    <div style="display: flex; flex-wrap: wrap; gap: 20px">
      <section class="card enter d2" aria-labelledby="df-h" style="flex: 1 1 460px; {CARD}; padding: 22px 8px 10px; display: flex; flex-direction: column">
        <h2 id="df-h" style="margin: 0 14px 8px; font-size: 18px; font-weight: 600">Data and connections</h2>
        <sc-for list="{{{{sources}}}}" as="s" hint-placeholder-count="6">
          <div class="row" style="display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 14px">
            <span aria-hidden="true" style="width: 10px; height: 10px; border-radius: 999px; background: {{{{s.c}}}}; flex: none"></span>
            <span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px; font-weight: 500">{{{{s.name}}}}</span><span style="font-size: 12px; color: #7E8693">{{{{s.what}}}}</span></span>
            <span class="num" style="font-size: 13px; color: #A1A8B3; text-align: right">{{{{s.age}}}}</span>
          </div>
        </sc-for>
      </section>
      <section class="card enter d3" aria-labelledby="sc-h" style="flex: 1 1 460px; {CARD}; padding: 22px; display: flex; flex-direction: column; gap: 16px">
        <h2 id="sc-h" style="margin: 0; font-size: 18px; font-weight: 600">Today's schedule · Berlin</h2>
        <div style="position: relative; display: flex; flex-direction: column; gap: 14px; padding-left: 22px">
          <span aria-hidden="true" style="position: absolute; left: 6px; top: 6px; bottom: 6px; width: 2px; background: #262A31"></span>
          <span aria-hidden="true" class="grow" style="position: absolute; left: 6px; top: 6px; height: 62%; width: 2px; background: #3DDC97; transform-origin: top"></span>
          <sc-for list="{{{{schedule}}}}" as="t" hint-placeholder-count="6">
            <div style="position: relative; display: flex; gap: 12px; align-items: baseline">
              <span aria-hidden="true" style="position: absolute; left: -21px; top: 4px; width: 12px; height: 12px; border-radius: 999px; background: {{{{t.dot}}}}; border: 2px solid #131519"></span>
              <span class="num" style="width: 52px; font-size: 14px; font-weight: 600; color: {{{{t.ink}}}}">{{{{t.at}}}}</span>
              <span style="font-size: 14px; color: {{{{t.ink}}}}">{{{{t.what}}}}</span>
            </div>
          </sc-for>
        </div>
      </section>
    </div>

    <section class="card enter d4" aria-labelledby="lg-h" style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 14px">
      <div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; align-items: center">
        <h2 id="lg-h" style="margin: 0; font-size: 18px; font-weight: 600">Event log</h2>
        <div role="group" aria-label="Filter log" style="display: flex; gap: 6px">
          <sc-for list="{{{{logFilters}}}}" as="f" hint-placeholder-count="3">
            <button type="button" class="press" aria-pressed="{{{{f.pressed}}}}" onClick="{{{{f.pick}}}}" style="height: 36px; padding: 0 14px; border-radius: 999px; border: 0; background: {{{{f.bg}}}}; color: {{{{f.ink}}}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{{{f.label}}}}</button>
          </sc-for>
        </div>
      </div>
      <sc-for list="{{{{log}}}}" as="e" hint-placeholder-count="6">
        <div class="swap" style="display: flex; gap: 14px; padding: 10px 0; border-top: 1px solid #1B1E24">
          <span class="num" style="flex: none; width: 64px; font-size: 13px; color: #7E8693">{{{{e.t}}}}</span>
          <span style="flex: none; font-size: 11px; font-weight: 700; letter-spacing: .04em; color: {{{{e.ink}}}}; background: {{{{e.bg}}}}; border-radius: 6px; padding: 3px 7px; height: fit-content">{{{{e.kind}}}}</span>
          <span style="font-size: 14px; line-height: 1.45">{{{{e.text}}}}</span>
        </div>
      </sc-for>
    </section>

    <section class="card enter d5" aria-labelledby="sl-h" style="{CARD}; padding: 22px; display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#7FB2FF" stroke-width="2" aria-hidden="true"><path d="M20 14.5 A8 8 0 1 1 9.5 4 A6.5 6.5 0 0 0 20 14.5 Z" stroke-linejoin="round"></path></svg>
      <div style="flex: 1 1 400px; display: flex; flex-direction: column; gap: 6px">
        <h2 id="sl-h" style="margin: 0; font-size: 16px; font-weight: 600">If the Mac sleeps or the engine stops</h2>
        <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">Open trades keep their stop and target at Alpaca. The 15:55 close-out won't happen; the engine sells anything left from an earlier day at the next open. This page turns grey and says "Engine offline" after 90 seconds without a heartbeat.</span>
      </div>
      <code class="num" style="flex: 1 1 260px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 12px; padding: 12px 14px; font-size: 13px; color: #F4F5F7; font-family: ui-monospace, Menlo, monospace">./scripts/run_engine.sh</code>
    </section>
  </main>
'''
script = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { lf: 'all' }; }
  renderVals() {
    const beats = [];
    for (let i = 0; i < 60; i++) {
      const slow = i === 24 || i === 49;
      beats.push({ h: slow ? '70%' : (i % 5 === 4 ? '85%' : '55%'), c: slow ? '#F5B64C' : '#3DDC97', tip: slow ? 'tick took 14 s (5-minute scan)' : 'tick on time' });
    }
    const L = [
      { t: '20:10:07', kind: 'SCAN', cat: 'trading', text: '5-minute scan · 0 setups' },
      { t: '19:50:07', kind: 'ORDER', cat: 'trading', text: 'Bracket buy 8 COP · stop $115.52 · target $119.57 · filled $116.87' },
      { t: '19:15:41', kind: 'FILL', cat: 'trading', text: 'DE target filled at $623.38' },
      { t: '18:20:07', kind: 'VETO', cat: 'trading', text: 'NFLX skipped: company news in 24 h' },
      { t: '16:39:54', kind: 'SESSION', cat: 'system', text: 'Session start · history loaded in 10 min (first run) · account clean · Hunter' },
      { t: '16:29:16', kind: 'COMMAND', cat: 'system', text: 'New run applied · $5,000 · Hunter' },
      { t: '16:29:16', kind: 'START', cat: 'system', text: 'Engine started · code 3cbe714 · Docker on Mac mini' },
    ];
    const K = { SCAN: ['#A1A8B3', '#1B1E24'], ORDER: ['#7FB2FF', '#132A4A'], FILL: ['#3DDC97', '#12301F'], VETO: ['#F5B64C', '#33280F'], SESSION: ['#A1A8B3', '#1B1E24'], COMMAND: ['#C9B6FF', '#2A2240'], START: ['#A1A8B3', '#1B1E24'], ERROR: ['#FF6B6B', '#3A1719'] };
    const F = [['all', 'All'], ['trading', 'Trading'], ['system', 'System']];
    return {
      beats: beats,
      sources: [
        { name: 'Alpaca paper · orders', what: 'bracket orders, fills, positions', age: '2 s ago', c: '#3DDC97' },
        { name: 'Alpaca · 1-minute bars (IEX)', what: 'today, 71 symbols', age: '13 s ago', c: '#3DDC97' },
        { name: 'Alpaca · quotes (IEX)', what: 'checked at each decision', age: '1.2 s at last check', c: '#3DDC97' },
        { name: 'Alpaca · news', what: '24-hour check per candidate', age: '20:10', c: '#3DDC97' },
        { name: 'FRED · VIX', what: 'previous close', age: '19.92 · yesterday', c: '#3DDC97' },
        { name: 'Supabase', what: 'trades, ledger, heartbeat, commands', age: '6 s ago', c: '#3DDC97' },
      ],
      schedule: [
        { at: '14:45', what: 'Load price history (seconds after the first day)', dot: '#3DDC97', ink: '#A1A8B3' },
        { at: '15:30', what: 'Market opens', dot: '#3DDC97', ink: '#A1A8B3' },
        { at: '15:35', what: 'First scan for setups', dot: '#3DDC97', ink: '#A1A8B3' },
        { at: '20:13', what: 'Now · scanning every 5 minutes', dot: '#4F95F0', ink: '#F4F5F7' },
        { at: '21:45', what: 'Last new trade', dot: '#3A404A', ink: '#A1A8B3' },
        { at: '21:55', what: 'Sell everything', dot: '#3A404A', ink: '#A1A8B3' },
        { at: '22:00', what: 'Market closes · daily result saved', dot: '#3A404A', ink: '#A1A8B3' },
      ],
      logFilters: F.map(([k, l]) => ({ label: l, pressed: this.state.lf === k ? 'true' : 'false', bg: this.state.lf === k ? '#F4F5F7' : '#1B1E24', ink: this.state.lf === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ lf: k }) })),
      log: L.filter((e) => this.state.lf === 'all' || e.cat === this.state.lf).map((e) => ({ ...e, ink: K[e.kind][0], bg: K[e.kind][1] })),
    };
  }
}'''
open('../project/v3/Engine.dc.html', 'w').write(head('Engine health', 'Engine', H) + body + tail('Engine', script, H))
print('ok')
