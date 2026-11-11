"""v3 · Design system (issue #79): tokens, type, spacing, components and motion on one board.

Run from this folder: python3 designsystem.py
Writes ../project/v3/DesignSystem.dc.html.
"""
import sys
sys.path.insert(0, '.')
import configure2  # noqa: E402,F401  (shared nav, tab bar and component CSS; rewrites boards 09 unchanged)
import configure  # noqa: E402
import shell  # noqa: E402
from shell import CARD  # noqa: E402

shell.TAB = configure.tabbar('')
shell.CSS += shell.MORE + '''
.sw{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.demo{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.trow{display:grid;grid-template-columns:150px minmax(0,1fr) 210px;gap:8px 20px;align-items:baseline}
button:disabled{cursor:not-allowed}
@media (max-width: 760px){.trow{grid-template-columns:minmax(0,1fr)}}
'''

# name, hex, token in apps/web/src/tokens.css, use
COLOURS = [
    ("bg", "#0A0B0D", "--paper", "Page background, insets"),
    ("surface", "#131519", "--surface", "Cards"),
    ("surface-2", "#1B1E24", "--surface-2", "Raised rows, bars, tracks"),
    ("rule", "#262A31", "--rule", "Borders, dividers"),
    ("text", "#F4F5F7", "--ink", "Primary text, primary button"),
    ("text-2", "#A1A8B3", "--slate", "Secondary text"),
    ("text-3", "#7E8693", "--stale", "Captions on bg and surface only"),
    ("gain", "#3DDC97", "--gain", "Up, pass, always-on"),
    ("loss", "#FF6B6B", "--loss", "Down, refused, sell-all"),
    ("accent", "#4F95F0", "--sea", "Selection, focus ring, pot line"),
    ("SPY series", "#C4862F", "--s-spy", "Benchmark line, always dashed"),
    ("warn", "#F5B64C", "--warn", "Warnings, PAPER pill"),
]


def swatch(name, hx, tok, use):
    dash = ('<svg width="100%" height="56" viewBox="0 0 180 56" preserveAspectRatio="none" aria-hidden="true"><rect width="180" height="56" fill="#0A0B0D"></rect>'
            f'<path d="M0 40 L30 34 L60 38 L90 24 L120 28 L150 14 L180 18" fill="none" stroke="{hx}" stroke-width="2" stroke-dasharray="5 4"></path></svg>')
    chip = dash if name == "SPY series" else f'<span style="display: block; height: 56px; background: {hx}"></span>'
    return (f'<div style="border: 1px solid #262A31; border-radius: 16px; overflow: hidden; background: #131519; display: flex; flex-direction: column">{chip}'
            f'<div style="padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 2px"><span style="font-size: 14px; font-weight: 600">{name}</span>'
            f'<span class="num" style="font-size: 13px; color: #A1A8B3">{hx} · {tok}</span><span style="font-size: 12px; color: #A1A8B3">{use}</span></div></div>')


TYPE = [
    ("Hero number", "64 / 700 / −0.04em", '<span class="num" style="font-size: 64px; font-weight: 700; letter-spacing: -0.04em; line-height: 1">$5,021.40</span>'),
    ("Page title", "40 / 700 / −0.03em", '<span style="font-size: 40px; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1">Strategy</span>'),
    ("Card title", "18–20 / 600", '<span style="font-size: 20px; font-weight: 600">What to trade</span>'),
    ("Body", "15 / 400 / 1.5", '<span style="font-size: 15px; line-height: 1.5">Kaibot trades on its own. Pick a starting point, nudge it, done.</span>'),
    ("Secondary", "13 / 400 / 1.5", '<span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">These go first; Kaibot can still trade the other 59.</span>'),
    ("Caption", "12 / 400", '<span style="font-size: 12px; color: #A1A8B3">New trades 10:00–15:45 ET</span>'),
    ("Section label", "13 / 600 / +0.06em caps", '<span style="font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: #A1A8B3">Start from</span>'),
    ("Numbers", "tabular figures", '<span class="num" style="font-size: 16px; font-weight: 700">+$21.40 · −$150 · 1.81 sh</span>'),
]

SPACE = [4, 8, 12, 16, 20, 24, 40]
RADII = [("24", "Cards, sheets"), ("20", "Cards on phones, preset cards"), ("16", "Theme and list cards"), ("14", "Insets, notices"), ("999", "Buttons, chips, pills")]


def demo(title, spec, body):
    return (f'<section style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 14px; min-width: 0">'
            f'<div style="display: flex; flex-direction: column; gap: 2px"><h3 style="margin: 0; font-size: 15px; font-weight: 600">{title}</h3><span style="font-size: 12px; color: #A1A8B3; line-height: 1.5">{spec}</span></div>'
            f'<div style="display: flex; flex-wrap: wrap; align-items: center; gap: 10px">{body}</div></section>')


BTN = 'font: inherit; font-size: 14px; font-weight: 600; height: 44px; padding: 0 20px; border-radius: 999px; cursor: pointer'
X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M6 6 L18 18 M18 6 L6 18" stroke-linecap="round"></path></svg>'

COMPONENTS = "".join([
    demo("Buttons", "Pill, 44 px high, 600 weight. Press scales to 0.97. One primary per view.",
         f'<button type="button" class="press" style="{BTN}; border: 0; background: #F4F5F7; color: #0A0B0D">Review &amp; apply</button>'
         f'<button type="button" class="press" style="{BTN}; border: 1px solid #262A31; background: transparent; color: #F4F5F7">Fine-tune</button>'
         f'<button type="button" class="press" style="{BTN}; border: 0; background: transparent; color: #A1A8B3">Undo</button>'
         f'<button type="button" class="press" style="{BTN}; border: 1px solid #5A2A2E; background: transparent; color: #FF6B6B">Flatten and stop</button>'
         f'<button type="button" disabled="disabled" style="{BTN}; border: 0; background: #262A31; color: #A1A8B3">Disabled</button>'),
    demo("Chips", "Theme chip: 38 px, on = accent border and blue tint. Ticker chip: 44 px with checking, accepted and refused states.",
         '<sc-for list="{{chips}}" as="c" hint-placeholder-count="3"><button type="button" aria-pressed="{{c.on}}" onClick="{{c.pick}}" class="chip" style="height: 38px; padding: 0 14px; border-radius: 999px; border: 1px solid {{c.bd}}; background: {{c.bg}}; color: {{c.ink}}; font: inherit; font-size: 14px; font-weight: 500; cursor: pointer">{{c.name}}</button></sc-for>'
         '<span style="flex: 1 1 100%; height: 0"></span>'
         '<span role="status" aria-label="ABCD, checking" style="display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px 0 8px; border-radius: 999px; border: 1px dashed #3A404A; background: #131519"><span class="sk" aria-hidden="true" style="width: 28px; height: 28px; border-radius: 999px"></span><span class="sk" aria-hidden="true" style="width: 70px; height: 12px; border-radius: 6px"></span><span style="font-size: 12px; color: #A1A8B3">checking</span></span>'
         '<span style="display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px 0 8px; border-radius: 999px; border: 1px solid #262A31; background: #131519"><span aria-hidden="true" style="width: 28px; height: 28px; border-radius: 999px; background: #7FB2FF; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700">NV</span><span style="font-size: 14px; font-weight: 600">NVDA</span><span style="font-size: 13px; color: #A1A8B3">NVIDIA</span></span>'
         '<span style="display: inline-flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px 0 12px; border-radius: 999px; border: 1px solid #5A2A2E; background: #2A1517"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#FF6B6B" stroke-width="2.2" aria-hidden="true"><circle cx="12" cy="12" r="8.5"></circle><path d="M6 18 L18 6"></path></svg><span style="font-size: 14px; font-weight: 600; text-decoration: line-through; text-decoration-color: #FF6B6B">ZZZT</span><span style="font-size: 13px; color: #FF9C9C">Refused · not tradable at the broker</span></span>'),
    demo("Segmented control", "Track on bg with a rule border; the chosen option is text-on-dark inverted.",
         '<div role="radiogroup" aria-label="Focus mode" style="display: flex; gap: 2px; background: #0A0B0D; border: 1px solid #262A31; border-radius: 999px; padding: 3px"><sc-for list="{{segs}}" as="m" hint-placeholder-count="2"><button type="button" role="radio" aria-checked="{{m.on}}" onClick="{{m.pick}}" class="seg" style="height: 38px; padding: 0 14px; border-radius: 999px; border: 0; background: {{m.bg}}; color: {{m.ink}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer">{{m.name}}</button></sc-for></div>'),
    demo("Slider", "6 px track, accent fill, 22 px thumb with a soft accent halo. Value and bounds sit underneath.",
         '<div style="flex: 1 1 240px; display: flex; flex-direction: column; gap: 4px"><input class="kb" type="range" aria-label="Risk per trade" min="0.1" max="1" step="0.05" value="{{risk}}" onInput="{{setRisk}}" style="--p: {{riskPct}}" aria-valuetext="{{riskText}}"><div class="num" style="display: flex; justify-content: space-between; font-size: 12px; color: #A1A8B3"><span>0.10%</span><span style="color: #F4F5F7; font-weight: 600">{{riskText}}</span><span>1.00%</span></div></div>'),
    demo("Toggle", "48 × 28 px switch. Off is #3A404A, on is accent; the knob slides 20 px.",
         '<span style="flex: 1; font-size: 15px">Dip reversal</span><button type="button" role="switch" aria-checked="{{togOn}}" aria-label="Dip reversal" onClick="{{togFlip}}" class="tog press" style="flex: none; width: 48px; height: 28px; border-radius: 999px; border: 0; padding: 3px; cursor: pointer; background: {{togBg}}; display: flex"><span style="width: 22px; height: 22px; border-radius: 999px; background: #F4F5F7; transform: {{togX}}"></span></button>'),
    demo("Pill switch", "For optional extras. On = gain border, green tint and a check mark, so it never relies on colour alone.",
         '<sc-for list="{{pills}}" as="x" hint-placeholder-count="2"><button type="button" role="switch" aria-checked="{{x.on}}" onClick="{{x.flip}}" class="pill" style="display: inline-flex; align-items: center; gap: 8px; height: 38px; padding: 0 14px 0 10px; border-radius: 999px; border: 1px solid {{x.bd}}; background: {{x.bg}}; color: {{x.ink}}; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer"><span aria-hidden="true" style="width: 18px; height: 18px; border-radius: 999px; background: {{x.dot}}; color: #0A0B0D; display: flex; align-items: center; justify-content: center"><sc-if value="{{x.mark}}" hint-placeholder-val="{{false}}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#0A0B0D" stroke-width="3.5"><path d="M5 12 L10 17 L19 7" stroke-linecap="round" stroke-linejoin="round"></path></svg></sc-if></span>{{x.name}}</button></sc-for>'),
    demo("Card", "Surface on bg, 1 px rule border, 24 px radius (20 on phones), 22–24 px padding. No shadows, no coloured side borders.",
         f'<div style="flex: 1 1 100%; {CARD}; background: #0A0B0D; padding: 4px"><div style="{CARD}; padding: 22px; display: flex; flex-direction: column; gap: 6px"><span style="font-size: 18px; font-weight: 600">Boldness</span><span style="font-size: 13px; color: #A1A8B3">Card title 18 px, body 13–15 px.</span></div></div>'),
    demo("Locked row", "Rules the user cannot change: green lock, plain label, “Always on”.",
         '<div style="flex: 1 1 100%; display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 0 16px; border-radius: 14px; background: #0A0B0D"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3DDC97" stroke-width="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11 V8 a4 4 0 0 1 8 0 V11"></path></svg><span style="flex: 1; font-size: 15px">Sell everything at 15:55 ET</span><span style="font-size: 15px; font-weight: 600; color: #3DDC97">Always on</span></div>'),
    demo("Review bar", "Pinned to the bottom (above the tab bar on phones). Surface-2, #3A404A border, deep shadow; result first, one primary action.",
         '<div style="flex: 1 1 100%; display: flex; align-items: center; gap: 14px; padding: 10px 10px 10px 20px; border-radius: 999px; background: #1B1E24; border: 1px solid #3A404A; box-shadow: 0 16px 40px rgba(0,0,0,.55)"><span class="num" style="flex: 1; font-size: 13px; color: #A1A8B3"><b style="color: #F4F5F7; font-size: 15px">≈ 14</b> trades/mo</span><button type="button" class="press" style="height: 44px; padding: 0 20px; border-radius: 999px; border: 0; background: #F4F5F7; color: #0A0B0D; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer">Review &amp; apply</button></div>'),
    demo("Skeleton", "Shown while data loads, in the shape of what is coming. Shimmer sweeps left to right; stops under reduce-motion.",
         '<div aria-busy="true" aria-label="Loading sample" style="flex: 1 1 100%; display: flex; flex-direction: column; gap: 10px"><span class="sk" style="width: 45%; height: 18px"></span><span class="sk" style="height: 64px; border-radius: 16px"></span><span style="display: flex; gap: 8px"><span class="sk" style="width: 110px; height: 38px; border-radius: 999px"></span><span class="sk" style="width: 130px; height: 38px; border-radius: 999px"></span></span></div>'),
    demo("Stock badge", "Circle with the first two letters on one of six tints; dark text. Sizes 24, 28, 40. Stacks overlap by 7–8 px.",
         '<span aria-hidden="true" style="display: flex">' + "".join(
             f'<span style="width: 28px; height: 28px; margin-right: -8px; border-radius: 999px; border: 2px solid #131519; background: {c}; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700">{t}</span>'
             for t, c in (("MS", "#7FB2FF"), ("NV", "#3DDC97"), ("AM", "#F5B64C"), ("GO", "#C9A8FF"), ("ME", "#FF9F8A"), ("OR", "#8FD8E8"))) + '</span>'
         '<span style="display: inline-flex; align-items: center; gap: 12px; padding-left: 16px"><span aria-hidden="true" style="width: 40px; height: 40px; border-radius: 999px; background: #7FB2FF; color: #0A0B0D; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700">MS</span><span style="display: flex; flex-direction: column; line-height: 1.3"><b style="font-size: 15px">MSFT</b><span style="font-size: 12px; color: #A1A8B3">Microsoft</span></span></span>'),
])

MOTION_RULES = [
    ("Rise and stagger", "Cards enter 12 px up with a fade: 0.55 s, each 0.06 s after the last, five steps at most."),
    ("Baseline-rise charts", "Lines and areas grow up from the baseline in 1.1 s; bars in 0.8 s. Range changes morph the line in 0.45 s."),
    ("Shimmer", "Skeletons sweep a 1200 px gradient every 1.5 s, linear."),
    ("Press and swap", "Buttons scale to 0.97 in 0.15 s. A changed number rises in again in 0.35 s."),
    ("One easing", "cubic-bezier(.2, .7, .2, 1) for everything except the shimmer."),
    ("Reduced motion", "With “reduce motion” on, every animation and transition is off and content shows in its final state."),
]

H = 2900
PAGE = f'''  <main class="page" style="max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding: 28px 40px 80px; display: flex; flex-direction: column; gap: 40px">
    <div class="enter" style="display: flex; flex-direction: column; gap: 6px">
      <h1 class="h1" style="margin: 0; font-size: 40px; font-weight: 700; letter-spacing: -0.03em">Design system</h1>
      <span style="font-size: 15px; color: #A1A8B3">The v3 dark theme: colour, type, spacing, components and motion.</span>
    </div>
    <section class="enter d1" aria-labelledby="ds-c" style="display: flex; flex-direction: column; gap: 14px">
      <h2 id="ds-c" style="margin: 0; font-size: 20px; font-weight: 600">Colour</h2>
      <div class="sw">{"".join(swatch(*c) for c in COLOURS)}</div>
      <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">Gain and loss always come with a sign or a word. The SPY line is dashed as well as amber, so it reads without colour.</span>
    </section>
    <section class="enter d2" aria-labelledby="ds-t" style="display: flex; flex-direction: column; gap: 14px">
      <h2 id="ds-t" style="margin: 0; font-size: 20px; font-weight: 600">Type · Inter</h2>
      <div style="{CARD}; padding: 8px 22px; display: flex; flex-direction: column">
        {"".join(f'<div class="trow" style="padding: 14px 0; border-top: {"0" if i == 0 else "1px solid #1B1E24"}"><span style="font-size: 13px; font-weight: 600">{n}</span><span style="min-width: 0">{s}</span><span class="num" style="font-size: 12px; color: #A1A8B3">{spec}</span></div>' for i, (n, spec, s) in enumerate(TYPE))}
      </div>
    </section>
    <section class="enter d3" aria-labelledby="ds-s" style="display: flex; flex-direction: column; gap: 14px">
      <h2 id="ds-s" style="margin: 0; font-size: 20px; font-weight: 600">Spacing and radius</h2>
      <div class="demo">
        <div style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 12px">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600">Spacing steps</h3>
          <div style="display: flex; align-items: flex-end; gap: 14px">{"".join(f'<span style="display: flex; flex-direction: column; align-items: center; gap: 6px"><span style="width: {s}px; height: {s}px; background: #4F95F0; border-radius: 3px"></span><span class="num" style="font-size: 12px; color: #A1A8B3">{s}</span></span>' for s in SPACE)}</div>
          <span style="font-size: 12px; color: #A1A8B3; line-height: 1.5">Card padding 22–24 (16 on phones). Gap between cards 20. Page side padding 40 (16 on phones). Touch targets 44 or more.</span>
        </div>
        <div style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 12px">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600">Radius</h3>
          <div style="display: flex; flex-wrap: wrap; gap: 12px">{"".join(f'<span style="display: flex; flex-direction: column; gap: 6px; width: 96px"><span style="height: 56px; border: 1.5px solid #4F95F0; background: #16263D; border-radius: {r}px"></span><span class="num" style="font-size: 12px; font-weight: 600">{r if r != "999" else "pill"}</span><span style="font-size: 12px; color: #A1A8B3; line-height: 1.4">{u}</span></span>' for r, u in RADII)}</div>
        </div>
      </div>
    </section>
    <section class="enter d4" aria-labelledby="ds-k" style="display: flex; flex-direction: column; gap: 14px">
      <h2 id="ds-k" style="margin: 0; font-size: 20px; font-weight: 600">Components</h2>
      <div class="demo">{COMPONENTS}</div>
      <span style="font-size: 13px; color: #A1A8B3; line-height: 1.5">Focus ring on every control: 2 px accent outline, 2 px offset. Under 760 px the top nav becomes the bottom tab bar and cards stack.</span>
    </section>
    <section class="enter d5" aria-labelledby="ds-m" style="display: flex; flex-direction: column; gap: 14px">
      <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 12px">
        <h2 id="ds-m" style="margin: 0; flex: 1; font-size: 20px; font-weight: 600">Motion</h2>
        <button type="button" onClick="{{{{replay}}}}" class="press" style="{BTN}; border: 1px solid #262A31; background: transparent; color: #F4F5F7">Replay</button>
      </div>
      <div class="demo">
        <div style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 12px; min-height: 190px">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600">Rise and stagger</h3>
          <sc-if value="{{{{play}}}}" hint-placeholder-val="{{{{true}}}}">
            <div style="display: flex; gap: 10px"><span class="enter" style="flex: 1; height: 96px; border-radius: 16px; background: #1B1E24; border: 1px solid #262A31"></span><span class="enter d2" style="flex: 1; height: 96px; border-radius: 16px; background: #1B1E24; border: 1px solid #262A31"></span><span class="enter d4" style="flex: 1; height: 96px; border-radius: 16px; background: #1B1E24; border: 1px solid #262A31"></span></div>
          </sc-if>
        </div>
        <div style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 12px; min-height: 190px">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600">Baseline-rise chart</h3>
          <sc-if value="{{{{play}}}}" hint-placeholder-val="{{{{true}}}}">
            <svg viewBox="0 0 300 100" width="100%" height="100" role="img" aria-label="Sample: pot line above a dashed SPY line">
              <line x1="0" y1="96" x2="300" y2="96" stroke="#262A31"></line>
              <g class="rise" style="transform-origin: 50% 96%">
                <path d="M0 96 L0 70 L50 62 L100 66 L150 44 L200 48 L250 26 L300 20 L300 96 Z" fill="#4F95F0" opacity=".14"></path>
                <path d="M0 70 L50 62 L100 66 L150 44 L200 48 L250 26 L300 20" fill="none" stroke="#4F95F0" stroke-width="2.5" stroke-linejoin="round"></path>
                <path d="M0 74 L50 70 L100 60 L150 58 L200 50 L250 46 L300 38" fill="none" stroke="#C4862F" stroke-width="2" stroke-dasharray="5 4" stroke-linejoin="round"></path>
              </g>
            </svg>
          </sc-if>
        </div>
        <div style="{CARD}; border-radius: 20px; padding: 18px; display: flex; flex-direction: column; gap: 12px; min-height: 190px">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600">Shimmer</h3>
          <span class="sk" style="height: 96px; border-radius: 16px"></span>
        </div>
      </div>
      <dl style="margin: 0; {CARD}; padding: 8px 22px; display: flex; flex-direction: column">
        {"".join(f'<div class="trow" style="padding: 12px 0; border-top: {"0" if i == 0 else "1px solid #1B1E24"}; grid-template-columns: 190px minmax(0,1fr)"><dt style="font-size: 14px; font-weight: 600">{n}</dt><dd style="margin: 0; font-size: 14px; color: #A1A8B3; line-height: 1.5">{d}</dd></div>' for i, (n, d) in enumerate(MOTION_RULES))}
      </dl>
    </section>
  </main>
'''

SCRIPT = r'''class Component extends DCLogic {
  constructor(p) { super(p); this.state = { chips: ['Hyperscalers'], seg: 'only', risk: 0.5, tog: true, pills: { vix: true, pause: false }, play: true }; }
  componentWillUnmount() { clearTimeout(this._t); }
  renderVals() {
    const st = this.state;
    return {
      chips: ['Hyperscalers', 'Semiconductors', 'Energy'].map(n => { const on = st.chips.includes(n);
        return { name: n, on: String(on), bd: on ? '#4F95F0' : '#262A31', bg: on ? '#16263D' : '#131519', ink: on ? '#F4F5F7' : '#A1A8B3',
          pick: () => this.setState({ chips: on ? st.chips.filter(z => z !== n) : [...st.chips, n] }) }; }),
      segs: [['prefer', 'Prefer these'], ['only', 'Only these']].map(([k, n]) => ({ name: n, on: String(st.seg === k), bg: st.seg === k ? '#F4F5F7' : 'transparent', ink: st.seg === k ? '#0A0B0D' : '#A1A8B3', pick: () => this.setState({ seg: k }) })),
      risk: st.risk, riskText: st.risk.toFixed(2) + '%', riskPct: ((st.risk - 0.1) / 0.9 * 100).toFixed(1) + '%', setRisk: e => this.setState({ risk: Number(e.target.value) }),
      togOn: String(st.tog), togBg: st.tog ? '#4F95F0' : '#3A404A', togX: st.tog ? 'translateX(20px)' : 'none', togFlip: () => this.setState({ tog: !st.tog }),
      pills: [['vix', 'Sit out fear spikes'], ['pause', 'Longer pause after losses']].map(([k, n]) => { const on = st.pills[k];
        return { name: n, on: String(on), mark: on, bd: on ? '#3DDC97' : '#262A31', bg: on ? '#12301F' : 'transparent', ink: on ? '#F4F5F7' : '#A1A8B3', dot: on ? '#3DDC97' : '#3A404A',
          flip: () => this.setState({ pills: { ...st.pills, [k]: !on } }) }; }),
      play: st.play, replay: () => { this.setState({ play: false }); clearTimeout(this._t); this._t = setTimeout(() => this.setState({ play: true }), 60); },
    };
  }
}'''

open('../project/v3/DesignSystem.dc.html', 'w').write(shell.head('Design system', '-', H) + PAGE + shell.tail('-', SCRIPT, H))
print('ok')
