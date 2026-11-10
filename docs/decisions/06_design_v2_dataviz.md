# Kaibot v2 data-viz spec
*10 Nov 2026 · Data-viz specialist · implements 04_prd_v2.md. The 02_design.md system carries over.*

## 1. Live hero
- **Chart:** a line chart of $ change from the start amount, 09:30–16:00 ET.
- **Pot:** ink, 2px, after shadow costs, plotting **the worse ledger** (PRD).
- **Other ledger:** 1px dotted. The gap between them is a slate band, "cost gap −$6.20".
- **SPY-scaled:** slate 1px, with the same cost model. **Cash:** a dashed line at $0.
- **Labels:** end labels replace the legend: "Pot +$23.10 (+46 bp) · SPY +$41.00 · Cash $0.00".
- **Markers:** a dashed daily-stop line; a fill rug on the x-axis; hatched halt bands.
- **Toggle:** Today / Run.

## 2. Risk strip
Bullet meters, labelled on the marks: amber from 80% of the limit, crimson on breach.
- **Daily stop:** "−0.42 / −1.50% · 1.08% left".
- **Open risk:** Σ(entry − stop) × qty against the room to the stop. The tick moves with the day's P&L: "0.31% / room 1.08%".
- **Entries:** pips, "7 / 20". **Positions:** pips, "2 / 5".
- **Run drawdown:** "2.10 / 8.00% · run ends at 8%".
- **Status chip:** "Running", or e.g. "Paused: 3 losses · resumes 11:42 ET".

## 3. Tape and trade detail
**Tape row (32px, mono):** Time ET · Symbol · Strategy (DIP/MOM) · Entry/Exit · Qty · Fill $ · Stop/Target $ · R · Net P&L $ · Cost $ · Exit reason · Room ✓/▲/⛔ (links to the transcript). Open trades pin on top, with live R.

**Trade detail:**
- **Chart:** a 1-minute line with a pale high–low band, 15 min either side of the trade.
- **Levels:** entry, stop and target lines, with the 02 ladder at the right edge ($, %, R).
- **Fills:** broker fill as a filled marker, shadow fill as a hollow marker, with the slippage labelled.
- **Excursions:** MAE and MFE ticks in R. Reason code below.

## 4. Evidence counter (per strategy, never pooled)
Six rows, each against its own threshold. No combined score.
- **Trades:** a progress bar, "412 / 900". **Months:** "1.4 / 3".
- **t-stat:** a 0–4 bar with a tick at 3, "t 1.62 (needs > 3)", beside the expectancy and its 95% CI.
- **DSR:** a dot against a tick at 0, "−0.08 (needs > 0)".
- **Random entries:** a dot histogram of the 1,000 runs, with the strategy rule and a 95th tick, "71st (needs 95th)".
- **vs SPY:** "Sharpe 0.84 vs 1.10". **Drawdown:** ✓ or ⛔.
- **Anti-overclaiming:**
  - Below 900 trades, rows are slate (never pass green) and read "Provisional: can only kill".
  - A met kill criterion shows fail.
  - The header reads "3 of 6 met · not eligible". There is no "% to live" figure and no ETA beyond the trade-count pace.

## 5. Daily report card
- **Header:** net $ after costs, with bp against SPY and against cash.
- **P&L distribution:** a beeswarm of trade R with a zero line. Run view: a histogram of daily returns with today marked.
- **Win/loss:** "12W · 9L · 57%" as a split bar, with average win and loss R.
- **R-multiples:** sorted horizontal bars from 0.
- **Cost attribution:** a waterfall from gross through spread, slippage and fees to net. LLM spend is a separate line, outside trading P&L.
- **vs random:** today's percentile on the random-entry distribution.
- **Layout:** small multiples per strategy.

## 6. Shadow strategies
ORB, news continuation and rules-only sit in a "SHADOW · not traded" panel.
- **Marks:** dotted strokes, hollow markers and hatched bars, slate only, never P&L colours.
- **Labels:** every number reads "would have +$12.40".
- **Totals:** never summed into the pot. Overlaying them on the hero is an off-by-default toggle.
- **Room vs rules-only:** a difference bar, "veto lift +$3.10/day".

## 7. Number formats (changes from 02)
- **$:** primary. P&L and prices to 2 decimals.
- **€:** a muted reference only, "≈ €21.30 · 1.0842 · 09:30 ET". No FX split.
- **Returns:** daily returns in bp. Limits and drawdown in % to 2 decimals.
- **Statistics:** R, t, DSR and Sharpe to 2 decimals; percentiles as ordinals.
- **Signs:** always an explicit + or a true minus (−).
- **Times:** session events in 24-hour ET, with CET on hover. The audit trail uses ISO 8601.
