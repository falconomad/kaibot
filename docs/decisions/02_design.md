# Kaibot v1 design decisions: final rulings from the design team
Team: UX Lead · Visual/Brand Designer · Data-Viz & Density Specialist. Two rounds, resolved by the facilitator.

## Concept
"The chart room": a navigator's desk of paper, ink and ruled lines, with instruments that tell the truth quietly. The design keeps three voices apart: what Python calculated, what the AI thinks and what the broker reports.
Mark: a keel line. A solid stroke (calculated) over a dotted stroke (AI), joined by a square node (the gate). Wordmark: "kaibot" in lowercase IBM Plex Sans Medium.

## Shell (1440px primary)
- Safety header: 44px, surface background with a 1px ink bottom rule (not a dark strip; a dark header in a light app reads as browser chrome). Seven slots:
  - Chips: PAPER (hatched, "PAPER · PA3…"), Kill switch, Reconciliation.
  - Numbered meters: AI spend, Daily loss, Open tactical risk, Drawdown, each reading e.g. "1.42 / 2.00%" with a 3px under-bar and a limit tick.
  - Neutral below 80% of the limit, amber from 80%. On a breach or kill switch, the whole bar fills crimson. No sparklines.
- Left nav: 200px with labels (Command Centre · Discover & Research · My Portfolio · Trade approval, then Budget and Settings), with count badges. Collapses to 64px icons when the AI rail is expanded.
- AI rail: collapsed to 48px by default and showing the latest call ("HOLD · 3 sources"). "/" expands it to 380px; it reflows the page, never overlays it, and remembers its state.

## Approve button: fixed position and style, never moves, no hotkey
- Clean: solid sea, e.g. "Approve: buy 42 NBIS ≤ $61.20".
- Warning: the warnings are listed against their limits, with one checkbox: "I've read all n warnings". Approve stays hatched until it's ticked; the acknowledgement is audited.
  - Ruling: Approve keeps the same solid-sea style in every enabled state. The UX lead's stability argument beats the visual designer's outlined-warn proposal.
- Hard fail: hatched, a lock icon, and the button text names the failing rule. Adjust and Reject stay available.
- Expired: becomes "Recheck prices".
- Staged: "Approve conditionally", showing max entry, qty and loss. Afterwards it becomes a card reading "Fires 15:35 · lapses 16:00 CET", with Cancel.
- After submit: Submitting → Submitted → Reconciled ✓, or a mismatch alert.

## Screen layouts
- Command Centre
  - Main column: "Needs you" queue (type, ticker, one-line reason, age, Review), then a briefing of overnight changes and catalysts in CET, then macro dates.
  - Side column: € value and P&L with the FX split, the core/satellite bar against 70/30, 3 candidates, latest notes.
- Discover & Research
  - Filter row with 6 filters. Results take 40% of the width, the report 60%.
  - A staleness strip sits above the report tabs, in this order: Portfolio fit → Bull/Base/Bear → Valuation → Financials → Evidence (n independent).
  - Primary action: Save as thesis. Secondary: Draft trade.
- My Portfolio
  - Reconciliation banner when there's a mismatch (not dismissible), then a risk strip, then holdings, then allocation, then transactions.
  - Primary action: "Review thesis" on rows that aren't Intact.
- Trade approval
  - Left 65%: setup card with the ladder, qty and stop steppers, gate results, exposure after the trade, then the AI commentary.
  - Right 35%: a sticky Decision panel holding the countdown, Approve and the Reject reason chips.
  - The AI commentary always shows a two-line summary (call, strongest counter-argument, n sources). It is expandable, sits below the gate, and uses no serif on this screen.

## Visual system
- Theme: light "paper" is the default. Dark "night watch" is a full alternative (manual, or automatic after 20:00 CET).
- Palette (light / dark):
  - Base: paper #F6F4EF / #11161C · surface #FFFFFF / #171E26 · rule #DAD6CC / #2A333D · ink #1B2430 / #E6E3DC · slate #5B6573 / #98A1AC
  - Brand: sea #1F5F6B / #5FA8B4 (brand and primary action; Approve is never green)
  - P&L: gain #1F5FAE / #6FA3E8 · loss #B4500A / #F0904A
  - States: pass #2F6F4F / #6DBF94 · warn #94600F / #E0A845 · fail #B42318 / #F07466 (breach, hard fail and kill switch only)
  - AI: ai #5B4B8A / #A898DA · ai-tint #F2EFF8 / #1E1A2B
  - Stale: stale #7D848D / #7A838E (checked against WCAG)
- Colour rules:
  - P&L text is blue or orange, always signed with a true minus and ▲/▼. Meters and gate rows use only neutral, amber and crimson, so "down today" never reads as "near a limit".
  - Gate states use shape, word and colour: pass ✓, warn ▲, fail ⛔.
  - Thesis states use shape, word and colour: Intact ● solid ring, Under pressure ◐ half ring, Invalidated ✕ crossed ring, Needs review dashed slate ring. The review age turns amber after 30 days.
- Provenance cues (any single one is enough):
  - Calculated values: ink, IBM Plex Mono with tnum, plus a source chip ("calc · 14:02" or "alpaca · 14:02").
  - AI: ai-tint panel, dashed violet left rule, "AI analysis" eyebrow. Its prose is Source Serif 4 at 14/21 in the rail and the case narratives, and the call is in sans caps. Numbers the AI quotes are mono and link to their calculation.
- Stale values: full opacity in the stale colour, a clock glyph and a dotted underline. The relative age ("3 min") is always visible; the absolute CET time shows on hover. (40% opacity was rejected as unreadable.)
- Type:
  - IBM Plex Sans 400/500/600 for UI; IBM Plex Mono for every number; Source Serif 4 for AI prose.
  - Scale: 11 eyebrow caps · 13 table · 14 body · 16 card title · 20 section · 28 screen · 40 portfolio value (Mono Light).
- Components:
  - 4px spacing base, compact density. Radius 4px for controls, 6px for cards. No pills except the PAPER chip.
  - 1px borders everywhere; shadows only on popovers.
  - Tables: 32px rows, hairline rules, no zebra striping, sticky header.
  - Icons: Lucide at 1.5px stroke.

## Data visualisation
- One limit, one meter: a bullet bar from 0 to 125% of the limit, a hard limit tick, a warn band from 80%, and labels on the marks ("Open risk 1.42% / 2.00% · 0.58% left"). On Trade approval, a ghost segment shows the after-trade value ("1.42 → 1.91%").
- Allocation: sorted bars with Stock / Sector / Theme tabs, a limit line and headroom; core and satellite in two tints. No pies, donuts or gauges.
- Core/satellite: one 100% bar with a target tick at 70% ("Core 73.4% · target 70% · +3.4 pp").
- Scenario strip on a price axis: a bear-to-bull line, a base dot, the DCF range as a band and the current price as a rule; flip assumptions below; model version and data date in the footer.
- Trade ladder: vertical and to scale, showing target, entry, stop and last price (with its timestamp). The risk zone takes a 12% loss-orange tint and the reward zone an 8% gain-blue tint. Crimson appears only if a gate fails. Distances in $ and %; R:R.
- Correlation: a 0–1 dot strip with a tick at 0.7 ("⚠ 0.78 vs NVDA").
- Evidence: one line per item (claim · source · age · "n outlets"), the first 5 shown, then "Show all".
- Holdings, 8 columns: Ticker · Sleeve · Thesis · Value € · Weight with a 10% micro-meter · Day P&L € · Total P&L € with the FX effect as a second line · Sector. Qty, average cost and price in $ sit in the row expansion. (Ruling: 8 columns so the table fits at 1440px without horizontal scroll.)
- Gate results: Rule · Actual · Limit · Headroom · Meter · State · As-of. FAILs first, then WARNs; PASS rows collapse to "n passed".
- Formatting (en-GB locale):
  - € to 0 decimals on overviews, 2 on Trade approval and Transactions. $ prices to 2 decimals.
  - Weights to 1 decimal, risk values to 2; weight changes in pp.
  - Always an explicit + or −. R:R to 1 decimal, correlation to 2.
  - Times in 24-hour CET/CEST; the audit trail in ISO 8601.
  - FX shows as its own signed number: "Total +€412 = price +€530 · FX −€118".
