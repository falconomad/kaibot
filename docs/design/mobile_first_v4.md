# Dashboard v4: mobile first

Issue #226. Answers the stakeholder's ask of 16 Nov 2026: the phone views carry too much text and data; make it easy,
friendly and good-looking; desktop only needs targeted fixes. The measurements behind this are in `audit_2026-11-16.md`.

**Status: proposal.** Needs the stakeholder's approval (through Product) before Frontend builds any of it.

## The idea in five lines

1. **One thing per screen.** Each page has one headline, two or three supporting things, and everything else one tap away.
2. **Five tabs:** Home, Positions, Activity, Engine, More. Every page is reachable on a phone (today Go / no-go is not).
3. **Rows, not tables.** A phone list row is a name, one line of plain words, and one number. Detail opens in a bottom sheet.
4. **Honesty stays on the first screen.** Paper, the halt and "Experiment: failed the ten-year test" are never behind a tap.
   What moves behind a tap is the explanation, not the fact.
5. **Calm motion.** Short, quiet, and off when the phone asks for reduced motion.

Measured on the mockups at 390x844, with the same script as the audit:

| Page | Words in the first screen today | In the mockup | Change |
|---|---|---|---|
| Home | 129 | 46 (41 without the five tab labels) | −64% |
| Positions (today: Trades) | 148 | 88 | −41% |
| Activity (today: Tape) | 98, with "Why" off-screen | 78, all on screen | −20% |
| Engine | 142 | 80 | −44% |

Home lands at 46 words against the target of about 40. The two honesty rows (halt, experiment) are 10 of them. Without
a halt the screen has 41. I chose to keep both rows rather than hit the number.

## Rules that do not bend

- **Paper** is always in the top bar, on every page.
- **A halt or a real breach** shows as a red row at the top of Home and Engine, and as a red dot on the Engine tab.
- **"Experiment: failed the ten-year test"** shows in full on Home, Positions, the position sheet, Engine and Go / no-go.
  It is never shortened to "Experiment" alone on its first appearance on a page, and never cut off (audit finding #5).
- **Colour roles are fixed:** blue = experiment (calm, informational); amber = ended as planned or needs attention;
  red = a limit or safety halt in force. Green and red on numbers mean gain and loss only.
- **No new claims.** Copy keeps the `explain.ts` tone: short, plain, never promising a result. Codes stay in `title`.
- **No trade-by-trade controls** appear anywhere. Sheets say so in one line.

## Patterns borrowed, and from where

Described in my own words; no brand's assets, marks or layouts are copied. The mockups are original.

| Pattern | Seen in | How Kaibot uses it |
|---|---|---|
| One hero number, one chart, range chips under it | Stock apps such as Robinhood, Apple Stocks, Trade Republic, Public, Coinbase. A design critique of Robinhood praises the single performance graph with quick time-range switches and colour as an instant up/down cue ([Pratt IXD](https://ixd.prattsi.org/2025/02/design-critique-robinhood-ios-app/)). | Home: paper pot, one line of change, one chart, 1W / 1M / 3M / All. |
| A feed of cards with one prominent number each, and no order books or indicators while browsing | Polymarket. One review describes it as closer to a social feed than a trading terminal, with a large probability per card and plain line charts ([Avark](https://avark.agency/learn/prediction-market-design-patterns)). | Activity and Positions rows: one number per row, no table columns. |
| Dense detail up front reads as desktop-first on a phone | The same review says this of Kalshi's information hierarchy. | This is what the audit measured on Kaibot today. v4 moves the detail behind a tap. |
| Bottom tab bar with 3 to 5 equal destinations, inside thumb reach | Platform guidance summarised by [UXPin](https://www.uxpin.com/studio/blog/mobile-navigation-examples/): 3–5 items, overflow goes to a menu, navigation in the bottom 40% of the screen. | Five tabs; the rest under More. |
| Bottom sheets for progressive disclosure | Same source; common in every app above for detail and confirmation. | Position detail, halt explanation, experiment explanation, "Flatten and stop" confirmation. |
| Tap targets of at least 44 px (Apple) or 48 dp (Material) | Same source; WCAG 2.2 names 44 px as the enhanced size. | 44 px minimum everywhere, 48 px for tab bar items, 64 px list rows. |
| Skeletons shaped like the content; small number changes animate | Common across the stock apps. | Kaibot already has shaped skeletons. v4 adds a short tick on changed numbers. |

Search results for some of these apps gave only review articles, not design documentation, so the first row also rests on
general knowledge of those apps rather than a cited source.

## Navigation

**Phone (under 760 px): bottom tab bar, five items, labels always shown.**

| Tab | Holds | Replaces |
|---|---|---|
| Home | Pot, chart, status rows, top positions | Home |
| Positions | Open and closed trades as rows; position sheet | Trades, Trade detail (as a sheet first, full page second) |
| Activity | What Kaibot did and skipped, by day | Tape |
| Engine | Is it running, is it safe, today's scan, experiment, Go / no-go summary | Engine, entry to Go / no-go |
| More | Report, Go / no-go, Strategy, Run & settings, New run, Sign out | Report, Strategy, Run tabs; the footer |

- The Engine tab carries a red dot while a halt is in force and an amber dot when the engine is offline.
- **Top bar, one row, 56 px:** mark, "kaibot", Paper pill, engine state pill. The run picker moves to More (and to the top of
  Run & settings). The "Day vs stop" meter moves into Home's details. This takes the header from 146 px to 56 px.
- **Tablet and desktop (760 px and up):** the current top navigation stays.

## Information hierarchy per page

| Page | The one thing | Supporting (2–3) | Behind a tap | Removed on phones |
|---|---|---|---|---|
| **Home** | Paper pot now | Change all-time and today; chart with range; top 3 positions | "Details": vs S&P 500, day vs stop, run drawdown, evidence toward the bar, rules for the level. Halt and experiment rows open sheets. Controls move to More › Run. | Test-phase paragraph (becomes the experiment row); level rules card; latest-trades table (it is the Activity tab) |
| **Positions** | What Kaibot holds now | Invested amount and share of pot; Open / Closed switch; experiment row | Position sheet: stop–now–target bar, why it bought, loss if stopped, link to the full trade page | Table columns "Entry → exit", "R", "Room" (they are in the sheet) |
| **Trade detail** | Result of this trade | Why it closed; the rule with its label; stop and target | Accordions: Gate checks, Agent room, Fills and costs | Feature list (z-scores) moves into "Why", collapsed |
| **Activity** | What happened, newest first | Filter chips (All, Buys & sells, Skipped, Alerts); day headers | Row sheet: the full "why", prices, counterfactual, agent-room transcript | The 5-column table; shadow rows are under a "Test only" chip, off by default |
| **Engine** | Is it running, and is it safe | Halt row; four safety checks; today (scan, experiment, Go / no-go) | Sheets per check; "Technical details": version, fingerprints, LLM budget, operations log | Process table "Detail" column, fingerprint hashes, the log table (all in Technical details) |
| **Go / no-go** | The verdict in one sentence | Trades so far against the 900 needed; "There is no go-live button" | Per rule: criteria as a checklist in an accordion; verdict history; how the bar was fixed | The three tables as tables |
| **Report** | Yesterday in one line: P&L against S&P 500 and cash | Day picker; trades that day as rows | Accordions: evidence, what worked and what did not, agent room, chapters | Attribution tables become "Top 3 / Bottom 3" rows with "See all" |
| **Strategy** | The starting point in use | Focus; boldness; sticky "Review & apply" | Trading hours, extra caution, Fine-tune | Nothing; carousels are fixed so the page fits (audit #1) |
| **My lists** | The list in use | Add tickers; accepted and refused counts | Per-ticker reason sheet | Company names on chips (already hidden) |
| **Fine-tune** | One group of settings at a time | Accordion groups; value and its limit | Locked rows explanation | — |
| **Run & settings** | This run: amount, level, status | Pause; Flatten and stop (confirmation sheet); level from next session | Level comparison (cards, not a 7-column table); past runs | Level table as a table |
| **New run** | One question per step | Step dots; Next | "What does this level mean" sheet | — (fix the cut-off "+", audit #2) |
| **Sign in** | Email and one button | Paper-only line | — | — |

## Wireframes, before and after (390 px)

Before is traced from the audit screenshots. After matches the mockups.

### Home

```
BEFORE  129 words in the first screen           AFTER  46 words
+----------------------------------+           +----------------------------------+
| kaibot        [Paper 1 v][Running]|           | kaibot            [Paper][Running]|
| [PAPER · BALANCED]                |           | (!) New buys paused until 13:05  >|
| DAY VS STOP    -0.62% / -1.50%    |           | Experiment: failed the 10-yr test>|
|##################------------------|           |                                  |
| Limit or safety halt in force:    |           | Paper pot                        |
| Engine halted entries for today · |           | $10,107.10                       |
| 3 losses in a row ... Engine health|           | +$107.10 all time  -$63.40 today |
| The engine is in a test phase     |           |        __/\    /\__/\            |
| while Engine v3 is built: the ... |           |   _/\_/    \__/      \.          |
| Read the Engine v3 plan           |           |  /                               |
| Pot, after costs                  |           | [1W]  [1M]  [3M]  [All]          |
| $10,107.10                        |           | Holding 4 of 5                  >|
| +$107.10 · +1.07% today -$63.40   |           | (NVDA) NVDA --o--   $2,070.88    |
| SPY same start +1.29%             |           |                       +$80.36    |
| [1W][1M][3M][ALL]                 |           | (AVGO) AVGO -o---   $1,308.20    |
| chart with legend, axis, table btn|           |                       -$17.40    |
| Home Trades Tape Report Strat Run |           | Home Positions Activity Engine More|
+----------------------------------+           +----------------------------------+
  then 3.5 more screens: evidence,                then: 4th position, "Details" rows
  rules, experiment, positions,                   (vs S&P 500, limits, evidence)
  controls, 12-row trades table
```

### Positions (today: Trades)

```
BEFORE  table 596 px wide in 324 px             AFTER  rows, nothing scrolls sideways
+----------------------------------+           +----------------------------------+
| All    37     [Newer] Page [Older]|           | Positions                        |
| trades total          1 of        |           | 4 open · $5,979 invested · 59%   |
| · Paper               1           |           | [Open] [Closed]                  |
| Stock                         Tim |           | Trend momentum is an experiment: |
| (NVDA) NVDA      [Experiment—fai] |           | it failed the ten-year test     >|
|        3 sh · Trend [only     ]Fr |           | Open now                         |
| (AVGO) AVGO      [Experiment—fai] |           | (NVDA) NVDA            $2,070.88 |
|        4 sh · Trend [only     ]Fr |           |  14 shares · in at $142.18 +$80  |
| ...                               |           |  ----------o-------              |
+----------------------------------+           | ... three more rows              |
                                                +----------------------------------+
```

### Activity (today: Tape)

```
BEFORE  table 1,432 px wide; "Why" off-screen   AFTER  one sentence per row
+----------------------------------+           +----------------------------------+
| Tape                              |           | Activity                         |
| Every setup Kaibot saw, what it   |           | What Kaibot did, and passed on.  |
| did, why, and every fill ...      |           | [All][Buys & sells][Skipped][Ale]|
| All  Fills  Setups  Agent room  Sh|           | TODAY                            |
| Time (ET)       What     Stock    |           | (||) New buys paused            >|
| Mon, Nov 16 05:50 [sold] (NVDA) NV|           |      3 losses in a row · 12:05   |
| 05:27          [taken] (AVGO) AV  |           | (v)  Sold CAT            -$48.10 |
| 05:04          [taken] (LLY)  LL  |           |      Stop hit · 12:04            |
+----------------------------------+           | (x)  Skipped AMD                >|
                                                |      Company news in the last day|
                                                +----------------------------------+
```

### Engine

```
BEFORE  142 words; 3 tables up to 1,020 px      AFTER  status first, detail behind rows
+----------------------------------+           +----------------------------------+
| Processes                         |           | Engine                           |
| Process          State   Last hea |           | (ok) Running                     |
| o Engine         trading 12 s ago |           |      Checked in 12 seconds ago   |
|   trades the run: reads prices... |           | (!) New buys paused until 13:05 >|
| o Watchdog       watching 20 s    |           |     3 losses in a row. Open ...  |
|   separate process: cancels ...   |           | Safety checks                    |
| Paper experiment                  |           | o Engine      Trading · your Mac>|
| Trend momentum [Experiment — ...] |           | o Watchdog    Watching          >|
| This rule did not pass ...        |           | o Safety net  Armed · 15:56     >|
| Engine version                    |           | o Paper account  Clean          >|
| Code version running now  e6deca3 |           | Today                            |
+----------------------------------+           | 3 setups found · Trend momentum  |
                                                | · Go / no-go · Technical details |
                                                +----------------------------------+
```

### Bottom sheet (position)

```
+----------------------------------+
|               ----               |   grabber; swipe down or X closes
| (NVDA) NVDA                  (X) |
|        NVIDIA · 14 shares        |
| $2,070.88   +$80.36 · +4.0%      |
| -----------------o-------------- |
| Stop $133.65  Now $147.92  Target|
| [ Experiment: failed the ten-year|
|   test. Trend momentum trades on |
|   paper only ... never real money]|
| Bought        Nov 12 at $142.18  |
| Why    Steady uptrend, 3 of 40   |
| If it hits the stop     -$119.42 |
| Kaibot manages each trade itself.|
| [       Full trade details      ]|
+----------------------------------+
```

## Mockups

Static HTML, self-contained, 390 px wide, generated by `mockups/v4/gen.py`. Invented numbers.

| Screen | File | Screenshot |
|---|---|---|
| Home | `mockups/v4/home.html` | `mockups/v4/home.png` |
| Positions | `mockups/v4/positions.html` | `mockups/v4/positions.png` |
| Activity | `mockups/v4/activity.html` | `mockups/v4/activity.png` |
| Engine | `mockups/v4/engine.html` | `mockups/v4/engine.png` |
| Bottom sheet (position) | `mockups/v4/sheet.html` | `mockups/v4/sheet.png` |

All five: no sideways overflow, no tap target under 44 px, no text under 12 px (measured with the audit script).

## Tokens

Dark first: the app has one theme and v4 keeps it. Names are roles, not colours, so a light theme can follow later.

**Colour roles** (values unchanged from v3 "night", so nothing already built shifts)

| Role | Token | Value | Use |
|---|---|---|---|
| Background | `--bg` | `#0A0B0D` | Page |
| Surface | `--surface` / `--surface-2` | `#131519` / `#1B1E24` | Cards, sheets / pressed, chips |
| Line | `--line` / `--line-strong` | `#262A31` / `#3A404A` | Dividers / sheet edge, grabber |
| Text | `--text` / `--text-2` / `--text-3` | `#F4F5F7` / `#A1A8B3` / `#8A929E` | Primary / secondary / captions |
| Accent | `--accent` / `--accent-text` / `--accent-tint` | `#4F95F0` / `#7FB2FF` / `#132A4A` | Links, chart, focus ring; **experiment state** |
| Gain | `--up` / `--up-tint` | `#3DDC97` / `#12301F` | Positive numbers, healthy dot |
| Loss / breach | `--down` / `--down-tint` | `#FF6B6B` / `#2A1517` | Negative numbers; **halt or breach in force** |
| Attention | `--amber` / `--amber-tint` | `#F5B64C` / `#33280F` | Paper pill; **experiment ended as planned**; engine offline |

**Spacing** (4-pt): 4, 8, 12, 16, 20, 24, 32, 48. Page gutter 16 on phones, 32 on desktop. Card padding 16 / 24.

**Type** (Inter, tabular numbers on every figure)

| Token | Size / weight | Use |
|---|---|---|
| `--t-hero` | 44 / 700 phone, 60 / 700 desktop | The pot |
| `--t-xl` | 28 / 700 | Page title, sheet figure |
| `--t-lg` | 20 / 600 | Card and sheet titles |
| `--t-md` | 16 / 400–600 | Row titles, body |
| `--t-sm` | 14 / 400–600 | Supporting line, chips |
| `--t-cap` | 12 / 600 | Captions, tags, tab labels. **Nothing below 12 px** (today: 11, 10 and 7 px) |

**Radius:** 8 (tags), 12 (inputs), 16 (status rows), 24 (cards, sheet top), pill (buttons, chips).

**Elevation:** 0 page; 1 card = surface + 1 px line, no shadow; 2 sheet = `0 -12px 40px rgba(0,0,0,.55)` + scrim 60% black;
3 toast.

**Sizes:** tap target 44 minimum; tab bar item 48 high; list row 64; primary button 52; top bar 56; tab bar 76 with safe area.

## Motion

| Token | Value | Use |
|---|---|---|
| `--d-fast` | 120 ms | Press feedback, chip select |
| `--d-base` | 200 ms | Fades, colour changes, number tick |
| `--d-sheet` | 320 ms | Sheet open; 240 ms to close |
| `--ease` | `cubic-bezier(.2, .7, .2, 1)` | Everything entering |
| `--ease-out` | `cubic-bezier(.4, 0, 1, 1)` | Everything leaving |

- **Tab change:** content cross-fades in 200 ms with a 4 px rise. No stagger. Top bar and tab bar do not move. (Today:
  550 ms plus up to 300 ms stagger.)
- **Push to a detail page:** new page slides in 24 px from the right and fades, 240 ms; back reverses it.
- **Bottom sheet:** slides up with `transform: translateY`, 320 ms; scrim fades 200 ms. Closes on swipe down, scrim tap, X
  and Escape. Focus moves into the sheet and returns to the row that opened it. Height never animates.
- **Number changes:** when a polled figure changes, the new value fades in over 200 ms and the figure tints green or red
  for 600 ms, then returns to its colour. No rolling digits. Only the figures that changed animate.
- **Live indicator:** the dot in the engine pill pulses with `transform: scale` and `opacity` on a pseudo-element (not
  `box-shadow`), once every 2 s while the engine is live. No pull-to-refresh: the page polls already, and a pull gesture
  would suggest it does not. A one-line "Updated 5 s ago" sits under Home's details.
- **Chart range:** the line morphs to the new range in 300 ms (keep one chart instance; stop re-creating it per range).
- **Skeletons:** keep the shaped skeletons. Each skeleton has the height of the content it stands for, so nothing jumps.
  Space for the halt row is reserved while the overview loads (fixes audit #9).
- **Accordions:** content fades in 200 ms; the row's chevron turns 90°. Use `grid-template-rows: 0fr → 1fr` for height.
- **Only `transform` and `opacity` animate.** No `width`, `height`, `box-shadow` or `background-position` animations.
- **Reduced motion:** every duration becomes 0, the pulse stops, sheets and pages appear in place, the number tint stays
  (colour is not motion). The existing global rule already does this; keep it and its test.

## Desktop: targeted changes only

1. **Stop the load jump** (audit #9): reserve the halt strip's row; skeleton heights match content.
2. **One-row header at 1280 and up:** Paper pill and the two meters join the navigation row (today 126 px in two rows).
3. **A clearer hero:** pot, change and "vs S&P 500" on the left; today's positions summary on the right of the same band,
   so the first screen answers "how is it doing" and "what does it hold" without scrolling. Chart height capped at 320 px.
4. **Consistent cards:** one padding (24), one title size (20 / 600), one caption size (12). Tables keep their columns on
   desktop but get a max line length of 72 characters for paragraphs above them.
5. **Chart axis:** distinct tick labels (no repeated "$10.1k"); the end label uses the same figure as the hero.
6. **Density:** Go / no-go, Report and Tape show 285 to 305 words in the first screen at 1440. Move the "how the bar was
   fixed" and page-intro paragraphs behind an info button; keep the verdict sentence.
7. **Links in text** get an underline (audit #12).

## Implementation plan (small PRs, in order)

Each PR keeps tests green and is checked with the audit script before and after.

| PR | Scope | Done when (measured at 375, 390, 430) |
|---|---|---|
| **1. Fixes, no redesign** | Strategy and My lists page width; New run "+" button; experiment label wraps instead of overlapping; Trades header; tables get `tabindex="0"` and a label; `role="img"` on the Go / no-go bar; underline links in text; nothing under 12 px; shell tap targets to 44 px | No page wider than the screen; 0 tap targets under 44 px in the shell; axe-core clean |
| **2. Tokens, shell, tab bar** | Token names above (aliases of today's values); one-row phone top bar; five-tab bar with More page; Engine-tab dot; bottom-sheet component with focus handling; reserved halt row | Header 56 px; all 13 routes reachable on a phone; layout shift under 0.1 at every width |
| **3. Home** | Hero, chart, status rows with sheets, top positions, Details section | 50 words or fewer in the first screen with a halt and the experiment showing |
| **4. Positions, Activity, Trade detail** | Rows in place of tables on phones; position and activity sheets; Trade detail accordions | No table wider than its box; "Why" visible without sideways scrolling |
| **5. Engine, Go / no-go, Report** | Status-first Engine with Technical details; Go / no-go checklist; Report summary and accordions | 100 words or fewer in each first screen; no table wider than its box |
| **6. Settings, Strategy, New run** | Level cards; Flatten-and-stop confirmation sheet; Strategy sticky action; New run one question per step | 0 tap targets under 44 px on these pages |
| **7. Motion** | Durations and easings above; number tick; chart morph; transform-only animations | Tab change settles within 200 ms; reduced motion still shows 0 running animations |

Desktop items 2 to 6 ride along with the PR that touches the same page.

## Open questions for Product

1. **Tab names.** "Positions" and "Activity" replace "Trades" and "Tape". Plain words, but a rename. Agree?
2. **Shadow (test-only) signals** are off by default in Activity. They stay one chip away. Agree, or keep them visible?
3. **Run picker** moves from the header to More. With two runs (paper and a replay) that is one more tap to switch.
4. **Home at 46 words** with both honesty rows, against the target of about 40. I recommend accepting it.
