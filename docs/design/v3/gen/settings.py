import sys; sys.path.insert(0, '.')
from shell import head, tail, CARD
H = 1150
body = f'''  <main class="page" style="max-width: 1040px; margin: 0 auto; box-sizing: border-box; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 20px">
    <h1 class="h1 enter" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Run &amp; settings</h1>

    <section class="card enter d1" aria-labelledby="cr-h" style="{CARD}; padding: 24px; display: flex; flex-direction: column; gap: 20px">
      <div style="display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; align-items: center">
        <h2 id="cr-h" style="margin: 0; font-size: 18px; font-weight: 600">Current run</h2>
        <span style="font-size: 12px; font-weight: 600; color: #3DDC97; background: #12301F; border-radius: 999px; padding: 5px 10px">{{{{runStatus}}}}</span>
      </div>
      <div class="num" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px">
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Pot at start</span><span style="font-size: 22px; font-weight: 700">$5,000</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Pot now</span><span style="font-size: 22px; font-weight: 700">$5,021.57</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Peak</span><span style="font-size: 22px; font-weight: 700">$5,024.10</span></div>
        <div style="display: flex; flex-direction: column; gap: 4px"><span style="font-size: 12px; color: #7E8693">Started</span><span style="font-size: 22px; font-weight: 700">Tue 10 Nov</span></div>
      </div>
      <div style="display: flex; flex-direction: column; gap: 10px">
        <span id="lvl-l" style="font-size: 14px; font-weight: 500">Level</span>
        <div role="radiogroup" aria-labelledby="lvl-l" style="display: flex; flex-wrap: wrap; gap: 6px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 4px; align-self: flex-start">
          <sc-for list="{{{{levels}}}}" as="l" hint-placeholder-count="3">
            <button type="button" class="press" role="radio" aria-checked="{{{{l.on}}}}" onClick="{{{{l.pick}}}}" style="height: 44px; padding: 0 22px; border-radius: 999px; border: 0; background: {{{{l.bg}}}}; color: {{{{l.ink}}}}; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">{{{{l.name}}}}</button>
          </sc-for>
        </div>
        <span class="num swap" style="font-size: 13px; color: {{{{levelNoteInk}}}}">{{{{levelNote}}}}</span>
      </div>
    </section>

    <div style="display: flex; flex-wrap: wrap; gap: 20px">
      <section class="card enter d2" aria-labelledby="pr-h" style="flex: 1 1 420px; {CARD}; padding: 22px 8px 10px; display: flex; flex-direction: column">
        <h2 id="pr-h" style="margin: 0 14px 8px; font-size: 18px; font-weight: 600">All runs</h2>
        <sc-for list="{{{{runs}}}}" as="r" hint-placeholder-count="2">
          <div class="row" style="display: flex; align-items: center; gap: 12px; padding: 12px 14px; border-radius: 14px">
            <span style="flex: 1; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px; font-weight: 500">{{{{r.label}}}}</span><span class="num" style="font-size: 12px; color: #7E8693">{{{{r.when}}}}</span></span>
            <span style="text-align: right; display: flex; flex-direction: column; gap: 2px"><span class="num" style="font-size: 14px; font-weight: 600; color: {{{{r.ink}}}}">{{{{r.result}}}}</span><span style="font-size: 12px; color: #7E8693">{{{{r.state}}}}</span></span>
          </div>
        </sc-for>
      </section>
      <section class="card enter d3" aria-labelledby="cm-h" style="flex: 1 1 420px; {CARD}; padding: 22px; display: flex; flex-direction: column; gap: 10px">
        <h2 id="cm-h" style="margin: 0 0 4px; font-size: 18px; font-weight: 600">Your commands</h2>
        <sc-for list="{{{{commands}}}}" as="c" hint-placeholder-count="4">
          <div class="swap" style="display: flex; gap: 12px; align-items: center; padding: 8px 0; border-top: 1px solid #1B1E24">
            <span style="flex: 1; font-size: 14px">{{{{c.what}}}}</span>
            <span class="num" style="font-size: 12px; color: {{{{c.ink}}}}">{{{{c.state}}}}</span>
          </div>
        </sc-for>
      </section>
    </div>

    <section class="card enter d4" aria-labelledby="ac-h" style="{CARD}; padding: 22px; display: flex; flex-wrap: wrap; gap: 16px; align-items: center">
      <div style="flex: 1 1 320px; display: flex; flex-direction: column; gap: 4px">
        <h2 id="ac-h" style="margin: 0; font-size: 16px; font-weight: 600">Account</h2>
        <span style="font-size: 14px; color: #A1A8B3">Signed in as the owner · Alpaca paper account · data on Supabase (Frankfurt)</span>
      </div>
      <a href="SignIn.dc.html" class="press full-sm" style="display: inline-flex; align-items: center; justify-content: center; height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #262A31; background: #1B1E24; color: #F4F5F7; font-size: 15px; font-weight: 600; text-decoration: none">Sign out</a>
    </section>

    <section class="card enter d5" aria-labelledby="dz-h" style="{CARD}; border-color: #3A2226; padding: 22px; display: flex; flex-wrap: wrap; gap: 16px; align-items: center">
      <div style="flex: 1 1 320px; display: flex; flex-direction: column; gap: 4px">
        <h2 id="dz-h" style="margin: 0; font-size: 16px; font-weight: 600; color: #FF6B6B">End or replace the run</h2>
        <span style="font-size: 14px; color: #A1A8B3; line-height: 1.5">Flatten and stop sells every position at market and ends this run. A new run does the same first, then starts fresh with a new amount and level.</span>
      </div>
      <a href="Onboarding.dc.html" class="press full-sm" style="display: inline-flex; align-items: center; justify-content: center; height: 48px; padding: 0 22px; border-radius: 999px; background: #F4F5F7; color: #0A0B0D; font-size: 15px; font-weight: 600; text-decoration: none">Start a new run</a>
      <button type="button" class="press full-sm" style="height: 48px; padding: 0 22px; border-radius: 999px; border: 1px solid #5A2A2E; background: transparent; color: #FF6B6B; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer">Flatten and stop</button>
    </section>
  </main>
'''
script = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { level: 'aggressive', pending: null }; }
  renderVals() {
    const L = [['conservative', 'Steady'], ['balanced', 'Balanced'], ['aggressive', 'Hunter']];
    const cur = 'aggressive';
    const sel = this.state.pending || cur;
    const names = Object.fromEntries(L);
    const cmds = [
      { what: 'New run · $5,000 · Hunter', state: 'applied Tue 17:29', ink: '#3DDC97' },
    ];
    if (this.state.pending && this.state.pending !== cur) cmds.unshift({ what: 'Level → ' + names[this.state.pending] + ' from next session', state: 'waiting for the engine…', ink: '#F5B64C' });
    return {
      runStatus: 'Running · Hunter',
      levels: L.map(([k, n]) => ({ name: n, on: sel === k ? 'true' : 'false', bg: sel === k ? '#F4F5F7' : 'transparent', ink: sel === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ pending: k }) })),
      levelNote: sel === cur ? 'Hunter: 1% per trade · −3% daily stop · 20% max position · 8 positions' : 'Switches to ' + names[sel] + ' at the next open (Thu 12 Nov, 15:30). Today stays on Hunter.',
      levelNoteInk: sel === cur ? '#7E8693' : '#F5B64C',
      runs: [
        { label: 'Paper · Hunter · $5,000', when: 'since Tue 10 Nov', result: '▲ +$21.57', ink: '#3DDC97', state: 'running' },
        { label: 'Replay · Variant A · hold-out', when: '1 Jul – 6 Nov 2026 · $100,000', result: '▲ +$702.30', ink: '#3DDC97', state: 'past data' },
      ],
      commands: cmds,
    };
  }
}'''
open('../project/v3/Settings.dc.html', 'w').write(head('Run & settings', 'Run', H) + body + tail('Run', script, H))
print('ok')
