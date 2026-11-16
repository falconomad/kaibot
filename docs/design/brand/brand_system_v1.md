# kaibo brand system v1

Issue #265. The stakeholder picked the "Rising k" logo and asked (16 Nov 2026) that the whole app match it. This
is the system that follows from the logo. It is a proposal for Frontend to apply: nothing here is loaded by the app.

- Values to apply: [`tokens_v1.css`](tokens_v1.css) (same token names as the app, new values, dark and light).
- Every contrast ratio: [`contrast_v1.md`](contrast_v1.md) (96 pairs, 0 below the line).
- For the stakeholder: [`brand_system_preview.png`](brand_system_preview.png).
- Proof: [`proof/`](proof/) (before and after of six screens, a components sheet, the light theme).

## 1. Colour

Three values are read straight from the logo files (`docs/design/logo/concepts/1-rising-k/`): blue `#4F95F0`
(`#2F6FDE` on light), white `#F4F5F7`, black `#0A0B0D`. Both scales are built in OKLCH, so each step is an even step
in lightness as the eye sees it. The blue scale passes through both logo blues (steps 400 and 500). The neutral scale
runs from the logo's white to its black and keeps their faint blue cast (hue about 262), so greys sit with the blue.

| Blue | 50 | 100 | 200 | 300 | **400** | **500** | 600 | 700 | 800 | 900 | 950 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | `#ECF4FF` | `#D9E9FF` | `#B4D4FE` | `#7FB4F9` | `#4F95F0` | `#2F6FDE` | `#1A54B7` | `#174189` | `#142C56` | `#0F1F39` | `#081327` |

| Grey | 0 | **50** | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 850 | 900 | **950** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | `#FFFFFF` | `#F4F5F7` | `#E4E6EA` | `#CDD1D9` | `#B2B8C1` | `#989FAB` | `#808692` | `#5D646F` | `#40454F` | `#262A31` | `#1B1E23` | `#131519` | `#0A0B0D` |

### Roles (dark is the default theme; light is defined and proven, opt-in)

| Role | Token | Dark | Light | Contrast, dark / light |
|---|---|---|---|---|
| Page | `--paper` | `#0A0B0D` | `#F4F5F7` | |
| Card, sheet | `--surface` | `#131519` | `#FFFFFF` | |
| Raised, pressed | `--surface-2` | `#1B1E23` | `#E4E6EA` | |
| Text | `--ink` | `#F4F5F7` | `#0A0B0D` | 18.1 / 18.1 on the page |
| Second-level text | `--slate` | `#B2B8C1` | `#40454F` | 9.9 / 8.8 |
| Third-level text | `--stale` | `#989FAB` | `#5D646F` | 7.4 / 5.5 |
| Hairline (decoration) | `--rule` | `#262A31` | `#CDD1D9` | not relied on |
| Border of a control | `--control` (new) | `#808692` | `#5D646F` | 5.0 / 5.5 (needs 3) |
| Primary action | `--sea` + `--on-sea` (new) | `#4F95F0`, label `#0A0B0D` | `#2F6FDE`, label `#FFFFFF` | label 6.5 / 4.7 |
| Links, accent text | `--sea-ink` | `#7FB4F9` | `#1A54B7` | 9.2 / 6.4 |
| Focus ring | `--focus` (new) | `#7FB4F9`, 2 px, 2 px offset | `#2F6FDE` | 9.2 / 4.3 (needs 3) |
| Selection | `--selection` (new) | `#174189` under `#F4F5F7` | `#B4D4FE` under `#0A0B0D` | 8.9 / 12.9 |
| The live point | `--live` (new) | `#4F95F0` | `#2F6FDE` | 6.5 / 4.3 (needs 3) |

Blue is kept for four things only: the logo, the one primary action on a screen, links and focus, and the live
point. It no longer carries "information".

### Meaning colours, tuned beside the blue

Same lightness family as the blue so none shouts louder than the brand, hues spaced away from it.

| Meaning | Token | Dark | Light | Contrast | Never colour alone |
|---|---|---|---|---|---|
| Gain | `--gain` | `#3ADD98` | `#00764C` | 11.2 / 5.2 | ▲ and a plus sign |
| Loss | `--loss` | `#FF716D` | `#BC262F` | 7.4 / 5.6 | ▼ and a minus sign |
| Paper, caution | `--warn` | `#F7B746` | `#F7B746` fill, text `#835B05` | PAPER label 11.1 on amber | the word, a shield icon |
| Halt, breach | `--loss-tint` + `--loss-line` (new) | row `#371715`, edge `#AD4B48` | `#FFE9E6`, `#D14E4D` | text 14.9, edge 3.6 / 3.9 | red dot, plain words, first on the screen |
| Information | neutral: `--surface-2`, `--slate` | | | 8.4 | words |
| Agent | `--ai` | `#C9B7FD` | `#6844AA` | 10.2 / 7.0 on a card | the word "Agent" |

Gain and loss as text measure ΔE 7.7 apart for red-green colour-blind readers, inside the "only with a second
cue" band. So the rule is absolute: a gain or loss figure always carries its arrow and its sign.

Nothing honest gets quieter. Halts stay red and get a stronger edge than today (`#5A2A2E`, 1.5:1 against its row, becomes `#AD4B48`,
3.6:1 against the page). The PAPER pill stays solid amber with a black label. Amber stays amber.

### The one conflict: "Experiment: failed the ten-year test"

Today this row and its tag are blue (`.status-row.info`, `.tag.info`), the same blue as the logo and now the primary
button. It would read as a brand element or as something to press.

New treatment, a **lab note**: neutral raised surface (`#1B1E23`), a **dashed** grey edge (`#808692`, 4.6:1), an 8 px
hatched strip on the left, a flask icon, and the text at full strength (`#F4F5F7`, 15.3:1, was the same). The words do
not change. It keeps its place on the first screen, directly under any halt. As a tag it is a dashed neutral pill
with the flask. Dashed means "provisional" everywhere in the system; nothing else uses a dashed edge except the
benchmark line. Not blue (brand), not violet (agent), not amber (it is not a warning about today).

## 2. Charts

- Pot line: brand blue, 2.5 px, round joins, with a soft blue fade under it. It ends in **the live point**: a 5 px
  blue dot with one faint ring, the same point as in the logo.
- Benchmark (S&P 500): neutral `#808692`, 1.5 px, dotted with round caps. It was orange, which competed with amber.
  It is identified by its dash and its label, not its colour.
- Gain and loss fills and bars use their own chart colours, since the text colours are too light to be marks:
  `--s-up` `#10A66E`, `--s-down` `#BC262F` (dark); `#039F68`, `#A7021D` (light).

Measured with the dataviz palette validator (`validate_palette.js --pairs all`), which simulates the three
colour-blindness types (Machado) and reports the worst pair:

| Theme | Marks | Surface | Worst pair, colour-blind | Worst pair, full colour | Contrast | Result |
|---|---|---|---|---|---|---|
| Dark | `#4F95F0` `#10A66E` `#BC262F` | `#131519` | ΔE 11.8 (deutan), target 8 | 22.1, floor 15 | all 3:1 or more | pass |
| Light | `#2F6FDE` `#039F68` `#A7021D` | `#FFFFFF` | ΔE 15.3 | 25.6 | all 3:1 or more | pass |

Not measured: the benchmark grey is below the validator's colour floor on purpose, so it was left out of the run.

## 3. Type

**Outfit** for the whole interface, including numbers. Licence: SIL Open Font License 1.1 (© 2021 The Outfit Project
Authors), free to self-host and embed. It was chosen because its shapes are the wordmark's shapes: single-storey
"a", circular "o" and "b", even stroke, round feel. Checked against Jost, Manrope, Sora, Figtree, DM Sans, Urbanist.

- Numbers do **not** keep Inter. Outfit has tabular numerals (`tnum`), but they are off by default, so
  `font-variant-numeric: tabular-nums` must be on wherever money, shares or percentages appear (set on `body`).
- Inter stays as the fallback: `"Outfit", "Inter", system-ui, sans-serif`.
- Outfit's lower case is a little smaller than Inter's (x-height 0.48 against 0.55) and its word space is tight. So:
  no text below 12 px, and `word-spacing: 0.06em` on body text.
- Weights: 400 text, 500 status rows, 600 headings and buttons, 700 the hero figure and titles. Self-host four
  `woff2` files, latin subset, `font-display: swap`.

| Step | Token | Size / weight | Tracking | Used for |
|---|---|---|---|---|
| Hero | `--t-hero` | 44 (60 desktop) / 700 | -0.035em | the pot |
| Title | `--t-xl` | 28 / 700 | -0.03em | page title, sheet figure |
| Heading | `--t-lg` | 20 / 600 | -0.01em | sheet title |
| Body | `--t-md` | 16 / 400 or 600 | 0 | rows, buttons |
| Small | `--t-sm` | 14 / 400 or 500 | 0 | second lines, status rows |
| Caption | `--t-cap` | 12 / 600 | +0.02em (caps +0.08em) | labels, PAPER pill |

The scale is the v4 scale, unchanged. Specimen: [`type_specimen.png`](type_specimen.png).

## 4. Shape, icons, motion

All from the logo's stem (15 units wide, 88 tall), its round ends and its point (radius 9).

- **Unit**: the stem is 4 px in the app. Spacing stays on the v4 4 px steps (4, 8, 12, 16, 24, 32).
- **Stroke**: icons and chart lines are 2 px with round caps and round joins (`--stroke`), the logo's stem to height
  ratio at 24 px. No sharp line ends anywhere.
- **Radius**: tag 8, input 12, row 16, card and sheet 24, pill for buttons and chips. Unchanged from v4; they already
  step in the stem unit.
- **The point** (`--point`, 8 px, `--live`): the one live indicator. It marks the end of the pot line, "now" on a
  position's stop-to-target track, the engine chip while the engine checks in, and the active tab (6 px above the
  icon). If the engine is stale or stopped the point is not shown; a red or amber status row says so in words.
  Health ticks on the Engine page stay green.
- **Icons**: 24 px grid, 2 px stroke, round caps, outline only, one colour (`currentColor`). The flask is reserved
  for the experiment.
- **Motion**, the logo's load animation as a family: things rise (sheets, rows: 320 ms in, 240 ms out), lines draw
  (`--d-draw` 300 ms, chart line on first load), the point pops (`--d-point` 180 ms, `--ease-pop`) and pulses once
  when a new value arrives. Ease `cubic-bezier(.2, .7, .2, 1)`. Nothing loops. With "reduce motion" on: no draw, no
  pop, no pulse; values just change.

## 5. Components

See [`proof/components-dark-light.png`](proof/components-dark-light.png).

| Component | Rule |
|---|---|
| Primary button | Brand blue fill, `--on-sea` label, pill, 52 px. One per screen or sheet. Was white. |
| Secondary button | `--surface-2` fill, `--control` border, ink label. |
| Destructive (flatten, stop) | Secondary shape, `--loss` label, `--loss-line` border. Never blue. |
| Disabled | 45% opacity, plus the reason in words nearby. |
| Chip (filter) | Pill, 44 px, `--control` border. Selected: ink fill, page-colour label. Not blue, so filters do not compete with the action. |
| Tag | 22 px pill, tint and text of its meaning. Gain and loss tags carry ▲ ▼. Experiment tag: dashed neutral with flask. |
| Bottom tab bar | Resting: `--stale` icon and label (7.4:1). Active: ink icon and label plus the 6 px blue point above. Two cues, neither colour alone. |
| Header | The wordmark (21 px tall) replaces mark plus text; PAPER pill; engine chip with the point. Height unchanged (56 + 1). |
| Status rows | Halt: red tint, red edge, red dot. Caution: amber tint, amber edge, shield. Experiment: lab note (section 1). Order: halt, caution, experiment. |
| Sheet | `--surface`, 24 px top corners, grab handle, `--rule-strong` top edge. One primary button at the bottom. |
| Meter | 8 px track `--surface-2`, blue fill, round ends. The stop-to-target track ends in loss and gain colours with the point for "now". Amber then red fill when a limit is near or hit, with words. |
| Skeleton | `--surface-2` to `--rule` shimmer, row-shaped, 8 px radius. No blue. |
| Tables that remain | 12 px / 600 `--slate` headers, `--rule` row lines, right-aligned tabular figures, no zebra. |
| Forms | 48 px, 12 px radius, `--control` border, page-colour fill. Error: `--loss` border and a message in words. |
| Focus | 2 px `--focus` ring, 2 px offset, on every control, on both themes. |

## 6. What needs more than a value (for Frontend)

Changing `tokens.css` to the values in `tokens_v1.css` recolours most of the app. These need a code change:

1. **Hard-coded colours in `app.css`** must point at tokens: `#16263D` (9 places) and `#2D4F7A`, `#243A5C` to the
   experiment or neutral tokens; `#5A2A2E` (4) to `--loss-line`; `#4A3A16` (3), `#5A4520` to `--warn-line`;
   `#0A0B0D` (7) to `--paper` or `--on-sea`; `#FF9C9C`, `#B3D1FF`, and the `rgba(10,11,13,…)` bars to `--bar-bg`.
2. **Experiment**: split it from `.tag.info` and `.status-row.info` into its own class with the dashed edge, hatch
   and flask (`--exp-bg`, `--exp-line`, `--exp-text`). Other "info" statuses become neutral.
3. **Primary button**: `button.primary` goes from ink fill to `--sea` fill with `--on-sea` label.
4. **New tokens**: `--on-sea`, `--control`, `--focus`, `--selection`, `--live`, `--sea-line`, `--loss-line`,
   `--warn-line`, `--warn-text`, `--paper-edge`, `--s-up`, `--s-down`, `--exp-*`, `--bar-bg`, `--stroke`, `--point`,
   `--d-draw`, `--d-point`, `--ease-pop`. Control borders move from `--rule` to `--control`; focus outlines from
   `--sea` to `--focus`.
5. **Font**: self-host Outfit, set `--font-ui` and `--font-num`, add `tabular-nums` and the word spacing.
6. **Tab bar** active point; **chart** benchmark dash, `--s-spy` neutral, end point; the point in the engine chip.
7. **Light theme**: a `data-theme` switch and `color-scheme`. In light, amber text uses `--warn-text`, not `--warn`.

**Run & settings** keeps its old layout. Only colours, type and tokens reach it; items 1, 3 and 5 apply there, no
layout rule changes.

## 7. How this was made

`gen/tokens.py` reads the logo files, builds the scales, checks every pair and writes `tokens_v1.css` and
`contrast_v1.md`. `gen/mock.py` and `gen/render.py` draw the proofs with Playwright from static mockups in the v4
layout, with invented sample numbers; "before" uses the values on main today. Outfit is embedded at render time from a
local copy (`gen/fonts.example.json`) and is not in the repository. Original work; no other brand's assets.
