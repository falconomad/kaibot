"""M5 · 10 My lists (custom tickers, issue #97): desktop + phone artboards.

Run from this folder: python3 mylists.py
Writes ../project/v3/MyLists.dc.html and ../project/v3/MyListsMobile.dc.html.
"""
import sys
sys.path.insert(0, '.')
import configure2  # noqa: E402,F401  (shared nav, tab bar and Strategy CSS; rewrites boards 09 unchanged)
import shell  # noqa: E402
from shell import CARD  # noqa: E402

shell.CSS += shell.MORE + '''
.tchip{animation:kb-rise .3s var(--ease) both}
.lcard{transition:border-color .2s var(--ease),background-color .2s var(--ease),transform .15s var(--ease)}
.lcard:active{transform:scale(.98)}
.lgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.field{transition:border-color .2s var(--ease)}.field:focus-within{border-color:#4F95F0}
button:disabled{cursor:not-allowed}
@media (max-width: 760px){
  .lgrid{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;margin:0 -14px;padding:0 14px 4px}
  .lgrid>*{flex:0 0 58%;scroll-snap-align:start}
  .tname{display:none}
  .addrow{flex-wrap:wrap}
}
'''

X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 6 L18 18 M18 6 L6 18" stroke-linecap="round"></path></svg>'
LOCK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3DDC97" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11 V8 a4 4 0 0 1 8 0 V11"></path></svg>'
WARN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#F5B64C" stroke-width="2" aria-hidden="true" style="flex: none; margin-top: 2px"><path d="M12 4 L21 20 H3 Z" stroke-linejoin="round"></path><path d="M12 10 V14 M12 17 V17.5" stroke-linecap="round"></path></svg>'

MYLISTS = '''
        <section aria-labelledby="ml-h" style="background: #1B1E24; border: 1px solid #262A31; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 16px">
          <div style="display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px">
            <h3 id="ml-h" style="margin: 0; flex: 1 1 200px; font-size: 16px; font-weight: 600">My lists</h3>
            <span class="num" aria-live="polite" style="font-size: 13px; font-weight: 600; color: {{countInk}}">{{countText}}</span>
            <span style="flex: 1 1 100%; font-size: 13px; color: #A1A8B3; line-height: 1.5">Your own tickers. Type any US stock and Kaibot checks it before it can be traded.</span>
          </div>
          <div class="lgrid" role="radiogroup" aria-label="Saved lists">
            <sc-for list="{{lists}}" as="l" hint-placeholder-count="2">
              <button type="button" role="radio" aria-checked="{{l.on}}" onClick="{{l.pick}}" class="lcard" style="display: flex; flex-direction: column; gap: 4px; min-height: 68px; padding: 12px 14px; border-radius: 16px; border: 1.5px solid {{l.bd}}; background: {{l.bg}}; color: #F4F5F7; font: inherit; text-align: left; cursor: pointer">
                <span style="display: flex; align-items: center; gap: 8px"><span style="flex: 1; min-width: 0; font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{l.name}}</span><span aria-hidden="true" style="flex: none; width: 16px; height: 16px; border-radius: 999px; border: 2px solid {{l.bd}}; background: {{l.dot}}; box-shadow: inset 0 0 0 3px {{l.bg}}"></span></span>
                <span class="num" style="font-size: 12px; color: {{l.sub}}">{{l.meta}}</span>
              </button>
            </sc-for>
          </div>
          <div style="display: flex; flex-direction: column; gap: 6px">
            <label for="ml-name" style="font-size: 12px; font-weight: 600; color: #A1A8B3">List name</label>
            <div class="field" style="display: flex; align-items: center; height: 44px; padding: 0 16px; border-radius: 999px; border: 1px solid #3A404A; background: #0A0B0D">
              <input id="ml-name" type="text" value="{{name}}" onChange="{{setName}}" maxlength="40" placeholder="e.g. AI infrastructure" style="flex: 1; min-width: 0; border: 0; background: transparent; color: #F4F5F7; font: inherit; font-size: 15px; outline: none">
            </div>
          </div>
          <div style="display: flex; flex-direction: column; gap: 6px">
            <label for="ml-add" style="font-size: 12px; font-weight: 600; color: #A1A8B3">Add tickers</label>
            <div class="addrow" style="display: flex; gap: 8px">
              <div class="field" style="flex: 1 1 220px; display: flex; align-items: center; gap: 8px; height: 44px; padding: 0 16px; border-radius: 999px; border: 1px solid #3A404A; background: #0A0B0D">
                <input id="ml-add" type="text" value="{{draft}}" onChange="{{setDraft}}" onKeyDown="{{draftKey}}" disabled="{{inputOff}}" autocapitalize="characters" autocomplete="off" spellcheck="false" aria-describedby="ml-help" placeholder="Type or paste: NBIS, CRWV, FSLY" style="flex: 1; min-width: 0; border: 0; background: transparent; color: #F4F5F7; font: inherit; font-size: 15px; outline: none; text-transform: uppercase">
              </div>
              <button type="button" onClick="{{commit}}" disabled="{{addOff}}" class="press" style="flex: none; height: 44px; padding: 0 18px; border-radius: 999px; border: 1px solid #3A404A; background: transparent; color: {{addInk}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Add</button>
            </div>
            <span id="ml-help" style="font-size: 12px; color: #A1A8B3; line-height: 1.5">{{helpText}}</span>
          </div>
          <ul aria-label="Tickers in this list" style="list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px">
            <sc-for list="{{items}}" as="t" hint-placeholder-count="4">
              <li class="tchip" style="display: flex; max-width: 100%">
                <sc-if value="{{t.checking}}" hint-placeholder-val="{{false}}">
                  <span role="status" aria-label="{{t.aria}}" style="display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px 0 8px; border-radius: 999px; border: 1px dashed #3A404A; background: #131519">
                    <span class="sk" aria-hidden="true" style="width: 28px; height: 28px; border-radius: 999px"></span>
                    <span class="num" style="font-size: 14px; font-weight: 600; color: #A1A8B3">{{t.s}}</span>
                    <span class="sk" aria-hidden="true" style="width: 74px; height: 12px; border-radius: 6px"></span>
                    <span style="font-size: 12px; color: #A1A8B3">checking</span>
                  </span>
                </sc-if>
                <sc-if value="{{t.ok}}" hint-placeholder-val="{{true}}">
                  <span aria-label="{{t.aria}}" style="display: inline-flex; align-items: center; gap: 8px; height: 44px; max-width: 100%; padding: 0 4px 0 8px; border-radius: 999px; border: 1px solid #262A31; background: #131519">
                    <span aria-hidden="true" style="flex: none; width: 28px; height: 28px; border-radius: 999px; background: {{t.c}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700">{{t.i}}</span>
                    <span class="num" style="font-size: 14px; font-weight: 600">{{t.s}}</span>
                    <span class="tname" style="min-width: 0; font-size: 13px; color: #A1A8B3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{t.name}}</span>
                    <button type="button" aria-label="Remove {{t.s}}" onClick="{{t.drop}}" class="press" style="flex: none; width: 36px; height: 36px; border-radius: 999px; border: 0; background: transparent; color: #A1A8B3; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button>
                  </span>
                </sc-if>
                <sc-if value="{{t.no}}" hint-placeholder-val="{{false}}">
                  <span aria-label="{{t.aria}}" style="display: inline-flex; align-items: center; gap: 8px; height: 44px; max-width: 100%; padding: 0 4px 0 12px; border-radius: 999px; border: 1px solid #5A2A2E; background: #2A1517">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#FF6B6B" stroke-width="2.2" aria-hidden="true" style="flex: none"><circle cx="12" cy="12" r="8.5"></circle><path d="M6 18 L18 6"></path></svg>
                    <span class="num" style="font-size: 14px; font-weight: 600; text-decoration: line-through; text-decoration-color: #FF6B6B">{{t.s}}</span>
                    <span style="min-width: 0; font-size: 13px; color: #FF9C9C; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">Refused · {{t.why}}</span>
                    <button type="button" aria-label="Remove {{t.s}}" onClick="{{t.drop}}" class="press" style="flex: none; width: 36px; height: 36px; border-radius: 999px; border: 0; background: transparent; color: #FF9C9C; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button>
                  </span>
                </sc-if>
              </li>
            </sc-for>
          </ul>
          <sc-if value="{{empty}}" hint-placeholder-val="{{false}}">
            <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5; padding: 14px 16px; border-radius: 14px; border: 1px dashed #3A404A">No tickers yet. Add at least 5 to trade only this list.</span>
          </sc-if>
          <span class="num" aria-live="polite" style="font-size: 13px; color: #A1A8B3">{{tally}}</span>
          <sc-if value="{{warn}}" hint-placeholder-val="{{true}}">
            <div class="swap" role="alert" style="display: flex; gap: 10px; padding: 12px 14px; border-radius: 14px; background: #33270F; border: 1px solid #5A4520; color: #F5B64C; font-size: 13px; line-height: 1.5">@WARN@<span>{{warnText}}</span></div>
          </sc-if>
          <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px">
            <button type="button" onClick="{{save}}" disabled="{{saveOff}}" class="press" style="height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: {{saveBg}}; color: {{saveInk}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">{{saveText}}</button>
            <span class="swap" style="flex: 1 1 200px; font-size: 13px; color: #A1A8B3; line-height: 1.5">{{saveNote}}</span>
          </div>
          <div style="display: flex; gap: 10px; padding-top: 14px; border-top: 1px solid #262A31; font-size: 13px; color: #A1A8B3; line-height: 1.5">
            <span style="flex: none; margin-top: 1px">@LOCK@</span>
            <span><b style="color: #F4F5F7; font-weight: 600">Safety filters always on.</b> Every ticker must be tradable at the broker, trade enough each day, have a tight spread, cost $5 or more and have enough history. These cannot be switched off.</span>
          </div>
        </section>'''

FOCUS = '''
      <section class="card enter d1" aria-labelledby="fo-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 16px">
        <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">
          <h2 id="fo-h" style="margin: 0; flex: 1; font-size: 18px; font-weight: 600">Focus</h2>
          <div role="radiogroup" aria-label="Focus mode" style="display: flex; gap: 2px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 3px">
            <sc-for list="{{modes}}" as="m" hint-placeholder-count="2">
              <button type="button" role="radio" aria-checked="{{m.on}}" onClick="{{m.pick}}" class="seg" style="height: 38px; padding: 0 14px; border-radius: 999px; border: 0; background: {{m.bg}}; color: {{m.ink}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{m.name}}</button>
            </sc-for>
          </div>
        </div>
        <nav aria-label="Focus source" style="display: flex; gap: 6px">
          <a href="Configure.dc.html" class="pill" style="display: inline-flex; align-items: center; height: 38px; padding: 0 16px; border-radius: 999px; border: 1px solid #262A31; color: #A1A8B3; font-size: 13px; font-weight: 600; text-decoration: none">Themes</a>
          <a href="#ml-h" aria-current="page" class="pill" style="display: inline-flex; align-items: center; height: 38px; padding: 0 16px; border-radius: 999px; border: 1px solid #4F95F0; background: #16263D; color: #F4F5F7; font-size: 13px; font-weight: 600; text-decoration: none">My lists</a>
        </nav>
@MYLISTS@
        <span class="swap" style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{modeNote}}</span>
      </section>'''

SIDE = '''
        <section class="card enter d2" aria-labelledby="rs-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 14px">
          <h2 id="rs-h" style="margin: 0; font-size: 18px; font-weight: 600">This list</h2>
          <div style="display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border-radius: 14px; background: #0A0B0D">
            <span style="font-size: 12px; color: #A1A8B3">Trades a month</span>
            <span class="num swap" style="font-size: 24px; font-weight: 700; letter-spacing: -0.02em; color: {{estInk}}">{{estBig}}</span>
            <span class="swap" style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{estNote}}</span>
          </div>
          <dl class="num" style="margin: 0; display: grid; grid-template-columns: 1fr auto; gap: 10px 14px; font-size: 14px">
            <dt style="color: #A1A8B3">Accepted</dt><dd class="swap" style="margin: 0; font-weight: 600; text-align: right">{{accText}}</dd>
            <dt style="color: #A1A8B3">Refused, never traded</dt><dd class="swap" style="margin: 0; font-weight: 600; text-align: right">{{refText}}</dd>
            <dt style="color: #A1A8B3">New to Kaibot</dt><dd class="swap" style="margin: 0; font-weight: 600; text-align: right">{{newText}}</dd>
            <dt style="color: #A1A8B3">Room left</dt><dd class="swap" style="margin: 0; font-weight: 600; text-align: right">{{roomText}}</dd>
          </dl>
        </section>
        <section class="card enter d3" aria-labelledby="ap-h" style="@CARD@; padding: 22px; display: flex; flex-direction: column; gap: 12px">
          <h2 id="ap-h" style="margin: 0; font-size: 18px; font-weight: 600">When you apply</h2>
          <ul style="margin: 0; padding: 0 0 0 18px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: #A1A8B3; line-height: 1.5">
            <li>Starts at the next open, Thu 12 Nov 09:30 ET. Open trades keep their rules.</li>
            <li>The list is stored as it is now. Later edits to a saved list do not change what is running.</li>
            <li>Your report starts a new chapter and the change is logged as a trial.</li>
            <li>No shorts, no leverage, nothing held overnight. Same as every other focus.</li>
          </ul>
        </section>'''

SKEL = '''
    <sc-if value="{{loading}}" hint-placeholder-val="{{false}}">
      <div aria-busy="true" aria-label="Loading your lists" class="twocol">
        <span class="sk" style="height: 640px; border-radius: 24px"></span>
        <div style="display: flex; flex-direction: column; gap: 20px"><span class="sk" style="height: 280px; border-radius: 24px"></span><span class="sk" style="height: 200px; border-radius: 24px"></span></div>
      </div>
    </sc-if>'''

OBAR = '''
  <div class="obar dock" role="region" aria-label="Result of these settings" style="display: flex; align-items: center; gap: 14px; padding: 10px 10px 10px 20px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 16px 40px rgba(0,0,0,.55); width: min(760px, calc(100% - 24px))">
    <div class="num ostats" style="flex: 1; display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: 13px; color: #A1A8B3">
      <span>trades/mo <b class="swap" style="color: #F4F5F7; font-size: 15px">{{estShort}}</b></span>
      <span><b style="color: #F4F5F7; font-size: 15px">$25</b> max a trade</span>
      <span class="hide-sm"><b style="color: #7FB2FF">{{chgText}}</b></span>
    </div>
    <sc-if value="{{scheduled}}" hint-placeholder-val="{{false}}"><span class="swap" role="status" style="font-size: 13px; font-weight: 600; color: #F5B64C; padding-right: 10px">Applies Thu 09:30 ET</span></sc-if>
    <sc-if value="{{showApply}}" hint-placeholder-val="{{true}}">
      <button type="button" onClick="{{openSheet}}" disabled="{{applyOff}}" aria-describedby="ob-why" class="press" style="flex: none; height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: {{applyBg}}; color: {{applyInk}}; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review &amp; apply</button>
    </sc-if>
    <span id="ob-why" style="position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap">{{applyWhy}}</span>
  </div>
  <sc-if value="{{sheet}}" hint-placeholder-val="{{false}}">
    <div class="mwrap">
      <div onClick="{{closeSheet}}" style="position: absolute; inset: 0; background: rgba(0,0,0,.6)" aria-hidden="true"></div>
      <section class="mpanel" role="dialog" aria-modal="true" aria-labelledby="rv-h" style="position: relative; background: #131519; border: 1px solid #262A31; padding: 22px 22px 28px; display: flex; flex-direction: column; gap: 16px">
        <div style="display: flex; align-items: center; gap: 8px"><h2 id="rv-h" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Apply “{{name}}”?</h2><button type="button" onClick="{{closeSheet}}" aria-label="Close" class="press" style="width: 44px; height: 44px; border-radius: 999px; border: 0; background: #1B1E24; color: #A1A8B3; cursor: pointer; display: flex; align-items: center; justify-content: center">@X@</button></div>
        <sc-for list="{{diff}}" as="d" hint-placeholder-count="2"><div style="display: flex; gap: 8px; font-size: 14px"><span style="flex: 1; color: #A1A8B3">{{d.k}}</span><span class="num" style="color: #7E8693; text-decoration: line-through">{{d.a}}</span><span class="num" style="font-weight: 600">{{d.b}}</span></div></sc-for>
        <span class="num" style="font-size: 13px; color: #A1A8B3; line-height: 1.6">{{symLine}}</span>
        <sc-if value="{{hasRefused}}" hint-placeholder-val="{{true}}"><span style="font-size: 13px; color: #FF9C9C; line-height: 1.5">{{refLine}}</span></sc-if>
        <div style="padding: 14px 16px; border-radius: 16px; background: #0A0B0D; display: flex; flex-direction: column; gap: 4px"><span class="num" style="font-size: 22px; font-weight: 700">{{sheetEst}} <span style="font-size: 13px; font-weight: 500; color: #A1A8B3">now ≈ 45</span></span><span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">{{estNote}}</span></div>
        <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">Starts at the next open, Thu 12 Nov 09:30 ET. Open trades keep their rules. Your report starts a new chapter and the change is logged as a trial.</span>
        <button type="button" onClick="{{apply}}" class="press" style="height: 52px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 16px; font-weight: 600; cursor: pointer">Apply at next open</button>
      </section>
    </div>
  </sc-if>
'''

H = 1300
PAGE = '''  <main class="page" style="max-width: 1120px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 140px; display: flex; flex-direction: column; gap: 22px">
    <div class="enter" style="display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 20px">
      <div style="flex: 1 1 360px; display: flex; flex-direction: column; gap: 6px">
        <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Strategy</h1>
        <span style="font-size: 15px; color: #A1A8B3">Trade your own list of stocks. Kaibot checks every ticker first.</span>
      </div>
      <a href="ConfigureList.dc.html" class="press" style="display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 16px; border-radius: 999px; border: 1px solid #262A31; color: #F4F5F7; font-size: 14px; font-weight: 600; text-decoration: none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20" stroke-linecap="round"></path><circle cx="16" cy="7" r="2"></circle><circle cx="10" cy="17" r="2"></circle></svg>Fine-tune all settings</a>
    </div>
@SKEL@
    <sc-if value="{{ready}}" hint-placeholder-val="{{true}}">
      <div class="twocol">
@FOCUS@
        <div style="display: flex; flex-direction: column; gap: 20px">
@SIDE@
        </div>
      </div>
    </sc-if>
  </main>
@OBAR@'''
for k, v in (('@FOCUS@', FOCUS), ('@MYLISTS@', MYLISTS), ('@SIDE@', SIDE), ('@SKEL@', SKEL), ('@OBAR@', OBAR),
             ('@CARD@', CARD), ('@X@', X), ('@LOCK@', LOCK), ('@WARN@', WARN)):
    PAGE = PAGE.replace(k, v)

SCRIPT = r'''class Component extends DCLogic {
  constructor(p) {
    super(p);
    this.CAP = 40; this.MIN = 5;
    // the 59-stock catalogue: these already have a measured trade rate
    this.CAT = 'MSFT AMZN GOOGL META ORCL NVDA AVGO AMD INTC QCOM TXN AAPL TSLA CRM ADBE CSCO NFLX DIS TMUS HD MCD NKE LOW SBUX BKNG UNH JNJ LLY MRK ABBV PFE TMO JPM BAC WFC GS MS V MA CAT BA GE HON UPS RTX DE LIN XOM CVX COP PG KO PEP COST WMT NEE DUK PLD AMT'.split(' ');
    this.N = { NBIS: 'Nebius Group', CRWV: 'CoreWeave', FSLY: 'Fastly', MSFT: 'Microsoft', AMZN: 'Amazon', GOOGL: 'Alphabet', META: 'Meta Platforms', ORCL: 'Oracle', NVDA: 'NVIDIA', AVGO: 'Broadcom', AMD: 'AMD', INTC: 'Intel', QCOM: 'Qualcomm', TXN: 'Texas Instruments', AAPL: 'Apple', TSLA: 'Tesla', CRM: 'Salesforce', ADBE: 'Adobe', CSCO: 'Cisco', NFLX: 'Netflix', DIS: 'Disney', COST: 'Costco' };
    // sample refusals: made-up tickers, one per filter
    this.R = { ZZZT: 'not tradable at the broker', ZZZV: 'trades too little', ZZZP: 'price under $5', ZZZH: 'too little history', ZZZS: 'spread too wide' };
    this.C = ['#7FB2FF', '#3DDC97', '#F5B64C', '#C9A8FF', '#FF9F8A', '#8FD8E8'];
    this.timers = [];
    const chk = s => ({ s, st: 'checking' });
    this.state = {
      mode: 'only', sel: 'new', name: 'AI infrastructure', draft: '',
      items: ['NBIS', 'CRWV', 'FSLY', 'ZZZT'].map(chk),
      lists: [{ id: 'l1', name: 'Cloud & chips', syms: ['MSFT', 'AMZN', 'GOOGL', 'ORCL', 'NVDA', 'AVGO', 'AMD', 'QCOM'] }],
      applied: false, sheet: false, ready: false, justSaved: false,
    };
  }
  componentDidMount() {
    this.later(() => {
      this.setState({ ready: true });
      if (!this.props.holdChecking) this.state.items.forEach((t, i) => this.later(() => this.settle(t.s), 700 + i * 350));
    }, 1000);
  }
  componentWillUnmount() { this.timers.forEach(clearTimeout); }
  later(fn, ms) { this.timers.push(setTimeout(fn, ms)); }
  verdict(s) {
    if (!/^[A-Z]{1,5}(\.[A-Z])?$/.test(s)) return { st: 'no', why: 'not a ticker symbol' };
    if (this.R[s]) return { st: 'no', why: this.R[s] };
    if (s.indexOf('ZZ') === 0) return { st: 'no', why: 'not tradable at the broker' };
    return { st: 'ok', name: this.N[s] || '' };
  }
  settle(s) { this.setState({ items: this.state.items.map(t => t.s === s && t.st === 'checking' ? { s, ...this.verdict(s) } : t) }); }
  add(raw) {
    const have = this.state.items.map(t => t.s); const fresh = [];
    raw.forEach(x => { const s = String(x).replace(/^\$/, '').toUpperCase().slice(0, 12);
      if (s && !have.includes(s) && !fresh.includes(s) && have.length + fresh.length < this.CAP) fresh.push(s); });
    if (!fresh.length) return;
    this.setState({ items: [...this.state.items, ...fresh.map(s => ({ s, st: 'checking' }))], applied: false, justSaved: false });
    fresh.forEach((s, i) => this.later(() => this.settle(s), 900 + i * 180));
  }
  tint(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997; return this.C[h % this.C.length]; }
  renderVals() {
    const st = this.state; const loading = !!this.props.showLoading || !st.ready;
    const ok = st.items.filter(t => t.st === 'ok'), no = st.items.filter(t => t.st === 'no'), busy = st.items.filter(t => t.st === 'checking');
    const total = st.items.length, full = total >= this.CAP, only = st.mode === 'only';
    const fresh = ok.filter(t => !this.CAT.includes(t.s));
    const short = this.MIN - ok.length;
    const warn = only && ok.length < this.MIN && total > 0;
    const cur = st.lists.find(l => l.id === st.sel);
    const same = cur && cur.name === st.name.trim() && cur.syms.join() === ok.map(t => t.s).join() && !no.length;
    const named = st.name.trim().length > 0;
    const canSave = named && ok.length > 0 && !busy.length && !same;
    const canApply = named && ok.length > 0 && !busy.length && !warn && !st.applied;
    const measured = ok.length > 0 && !fresh.length;
    const rate = only ? Math.round(42 * ok.length / 59) : 45;
    const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
    const pick = (l) => () => { this.setState({ sel: l.id, name: l.name, items: l.syms.map(s => ({ s, ...this.verdict(s) })), draft: '', applied: false, justSaved: false }); };
    const card = (on) => ({ on: String(on), bd: on ? '#4F95F0' : '#3A404A', bg: on ? '#16263D' : '#131519', dot: on ? '#4F95F0' : 'transparent', sub: on ? '#7FB2FF' : '#A1A8B3' });
    const lists = st.lists.map(l => ({ name: l.name, meta: plural(l.syms.length, 'ticker'), pick: pick(l), ...card(st.sel === l.id) }));
    lists.push({ name: 'New list', meta: st.sel === 'new' ? (total ? 'Not saved yet' : 'Empty') : 'Start from scratch', ...card(st.sel === 'new'),
      pick: () => { if (st.sel !== 'new') this.setState({ sel: 'new', name: '', items: [], draft: '', applied: false, justSaved: false }); } });
    const why = busy.length ? 'Wait for the checks to finish.' : !ok.length ? 'Add at least one accepted ticker.' : !named ? 'Give the list a name first.' : warn ? 'Only these needs at least 5 accepted tickers.' : '';
    const commit = () => { this.add(st.draft.split(/[\s,;]+/).filter(Boolean)); this.setState({ draft: '' }); };
    return {
      loading, ready: !loading,
      modes: [['prefer', 'Prefer these'], ['only', 'Only these']].map(([k, n]) => ({ name: n, on: String(st.mode === k), bg: st.mode === k ? '#F4F5F7' : 'transparent', ink: st.mode === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ mode: k, applied: false }) })),
      modeNote: only ? 'Only these: Kaibot trades nothing outside this list.' : 'Prefer these: this list goes first; Kaibot can still trade the other 59 stocks.',
      lists, name: st.name, setName: e => this.setState({ name: e.target.value, applied: false, justSaved: false }),
      draft: st.draft,
      setDraft: e => { const v = e.target.value;
        if (!/[\s,;]/.test(v)) { this.setState({ draft: v }); return; }
        const parts = v.split(/[\s,;]+/); const tail = /[\s,;]$/.test(v) ? '' : parts.pop();
        this.add(parts.filter(Boolean)); this.setState({ draft: tail }); },
      draftKey: e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } },
      commit, inputOff: full ? true : null, addOff: full || !st.draft.trim() ? true : null, addInk: full || !st.draft.trim() ? '#7E8693' : '#F4F5F7',
      helpText: full ? 'This list is full: 40 tickers is the most Kaibot can scan in time. Remove one to add another.' : 'Separate with commas or spaces, or paste a whole list. Each ticker is checked as you add it.',
      items: st.items.map(t => ({ s: t.s, i: t.s.slice(0, 2), c: this.tint(t.s), name: t.name || '', why: t.why || '',
        checking: t.st === 'checking', ok: t.st === 'ok', no: t.st === 'no',
        aria: t.st === 'checking' ? t.s + ', checking' : t.st === 'ok' ? t.s + (t.name ? ', ' + t.name : '') + ', accepted' : t.s + ', refused: ' + t.why,
        drop: () => this.setState({ items: st.items.filter(z => z.s !== t.s), applied: false, justSaved: false }) })),
      empty: total === 0,
      countText: total + ' of ' + this.CAP, countInk: full ? '#F5B64C' : '#A1A8B3',
      tally: !total ? 'Nothing added yet' : ok.length + ' accepted' + (no.length ? ' · ' + no.length + ' refused' : '') + (busy.length ? ' · ' + busy.length + ' checking' : ''),
      warn, warnText: '“Only these” needs at least 5 accepted tickers. This list has ' + ok.length + '. Add ' + short + ' more, or switch to “Prefer these”.',
      save: () => { const syms = ok.map(t => t.s); const nm = st.name.trim();
        const id = cur ? cur.id : 'l' + (st.lists.length + 1);
        const lists2 = cur ? st.lists.map(l => l.id === id ? { id, name: nm, syms } : l) : [...st.lists, { id, name: nm, syms }];
        this.setState({ lists: lists2, sel: id, name: nm, items: ok, justSaved: true }); },
      saveOff: canSave ? null : true, saveBg: canSave ? '#F4F5F7' : '#262A31', saveInk: canSave ? '#0A0B0D' : '#A1A8B3',
      saveText: same ? 'Saved' : cur ? 'Save changes' : 'Save list',
      saveNote: st.justSaved ? 'Saved. Refused tickers were left out.' : same ? 'Saved lists stay here for next time.' : busy.length ? 'Checking ' + plural(busy.length, 'ticker') + '…' : !ok.length ? 'Add an accepted ticker to save.' : !named ? 'Name the list to save it.' : no.length ? 'Saves the ' + ok.length + ' accepted. Refused tickers are left out.' : 'Saves ' + plural(ok.length, 'ticker') + '.',
      estBig: !ok.length ? 'No tickers yet' : measured ? '≈ ' + rate : 'not measured yet', estInk: measured ? '#F4F5F7' : '#A1A8B3',
      estShort: !ok.length ? '—' : measured ? '≈ ' + rate : 'not measured yet',
      estNote: !ok.length ? 'Add tickers to see what this list would do.' : measured ? 'From the replay of these catalogue stocks. Today, on all 59: about 45 a month.'
        : (fresh.length > 3 ? fresh.slice(0, 3).map(t => t.s).join(', ') + ' and ' + (fresh.length - 3) + ' more' : fresh.map(t => t.s).join(', ').replace(/, ([^,]*)$/, ' and $1')) + (fresh.length === 1 ? ' is' : ' are') + ' new to Kaibot, so there is no measured trade rate. The figure appears after this list’s first replay.',
      accText: String(ok.length), refText: String(no.length), newText: ok.length ? String(fresh.length) : '—', roomText: (this.CAP - total) + ' of ' + this.CAP,
      chgText: ok.length ? '1 change vs default' : 'Same as default',
      showApply: !st.applied, scheduled: st.applied, applyOff: canApply ? null : true, applyBg: canApply ? '#F4F5F7' : '#262A31', applyInk: canApply ? '#0A0B0D' : '#A1A8B3', applyWhy: why,
      diff: [{ k: 'Focus', a: 'All 59', b: (only ? 'Only ' : 'Prefer ') + ok.length + ' · my list' }],
      symLine: ok.map(t => t.s).slice(0, 12).join(' · ') + (ok.length > 12 ? ' +' + (ok.length - 12) : ''),
      hasRefused: no.length > 0, refLine: 'Left out, never traded: ' + no.map(t => t.s + ' (' + t.why + ')').join(', ') + '.',
      sheetEst: measured ? '≈ ' + rate + ' trades a month' : 'Trades a month not measured yet',
      sheet: st.sheet, openSheet: () => { if (canApply) this.setState({ sheet: true }); }, closeSheet: () => this.setState({ sheet: false }),
      apply: () => { const syms = ok.map(t => t.s); const nm = st.name.trim(); const id = cur ? cur.id : 'l' + (st.lists.length + 1);
        const lists2 = cur ? st.lists.map(l => l.id === id ? { id, name: nm, syms } : l) : [...st.lists, { id, name: nm, syms }];
        this.setState({ lists: lists2, sel: id, items: ok, applied: true, sheet: false }); },
    };
  }
}'''

PROPS = '"holdChecking":{"editor":"boolean","default":false},"showLoading":{"editor":"boolean","default":false},'


def build(title, w, h):
    out = shell.head(title, 'Strategy', h) + PAGE + shell.tail('Strategy', SCRIPT, h)
    return out.replace('data-props=\'{"$preview":{"width":1440', 'data-props=\'{' + PROPS + '"$preview":{"width":' + str(w))


open('../project/v3/MyLists.dc.html', 'w').write(build('My lists', 1440, H))
open('../project/v3/MyListsMobile.dc.html', 'w').write(build('My lists on a phone', 390, 844))
print('ok')
