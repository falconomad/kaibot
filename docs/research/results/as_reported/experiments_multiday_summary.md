# Multi-day walk-forward: four momentum rules that are allowed to hold (issue #113)

**Survivorship bias:** the universe is today's 300 large caps applied back to 2016, so every rule that holds these stocks is flattered by hindsight (they are the survivors and the winners); read each result against the equal-weight universe row, not against zero.

**Provisional (decision 15):** a first look from the research harness of #113, on today's stock list and with cost model v1. It is not a verdict and cannot pass Gate B; go/no-go numbers come only from the engine with `SimBroker`.

Out-of-sample 2017-05-01 to 2026-11-06; train 12 months, test the next month, roll monthly; selection by train Sharpe after costs. One continuous $100k account per row: equity and positions are carried across months, nothing is force-closed at a month end. "Tuned monthly" is the honest result; the second row of each pair is the rule's fixed, pre-declared setting.

| Experiment | Trades | Trades/month | Avg holding days | Net return % | CAGR % | Gross bp/trade | Net bp/trade | t-stat | Hit rate | Sharpe | Max drawdown % | Months positive | % time invested | Deflated Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_highs (tuned monthly) | 703 | 6.1 | 15.57 | 10.41 | 1.05 | 2.54 | 1.6 | 0.95 | 0.441 | 0.21 | 13.76 | 61 of 115 | 99.5 | 0.045 |
| trend_highs (fixed 55-day high, 3 ATR) | 677 | 5.9 | 16.2 | 5.2 | 0.54 | 1.74 | 0.95 | 0.56 | 0.455 | 0.12 | 17.6 | 60 of 115 | 99.2 | 0.024 |
| rel_strength (tuned monthly) | 701 | 6.1 | 13.3 | 65.97 | 5.48 | 7.82 | 7.57 | 4.08 | 0.544 | 0.87 | 17.27 | 66 of 115 | 80.0 | 0.831 |
| rel_strength (fixed 6 months, top 5) | 574 | 5.0 | 13.81 | 58.63 | 4.97 | 8.61 | 8.35 | 3.88 | 0.542 | 0.92 | 7.75 | 65 of 115 | 79.7 | 0.87 |
| winners_run (tuned monthly) | 17928 | 155.9 | 0.23 | -71.03 | -12.22 | 0.17 | -0.68 | -8.33 | 0.429 | -1.8 | 71.09 | 35 of 115 | 81.2 | 0.0 |
| winners_run (fixed 5 sessions) | 18045 | 156.9 | 0.2 | -72.22 | -12.61 | 0.14 | -0.7 | -8.77 | 0.431 | -1.9 | 72.4 | 32 of 115 | 76.5 | 0.0 |
| overnight_spy (tuned monthly) | 2178 | 18.9 | 1.0 | 1.75 | 0.18 | 0.51 | 0.08 | 0.58 | 0.535 | 0.19 | 2.86 | 64 of 115 | 90.9 | 0.537 |
| overnight_spy (fixed VIX < 30) | 2240 | 19.5 | 1.0 | 1.24 | 0.13 | 0.49 | 0.06 | 0.41 | 0.531 | 0.13 | 3.78 | 63 of 115 | 93.5 | 0.468 |
| SPY buy-and-hold (benchmark, price only) | n/a | n/a | n/a | 208.84 | 12.6 | n/a | n/a | n/a | n/a | 0.73 | 34.23 | n/a | 100.0 | n/a |
| Universe equal-weight, rebalanced monthly, after costs (benchmark) | n/a | n/a | n/a | 246.43 | 13.97 | n/a | n/a | n/a | n/a | 0.8 | 38.0 | n/a | 100.0 | n/a |

Basis points per trade are of the account's equity on the entry day; gross is before spread, impact, gap slippage and fees. Holding days are sessions between entry and exit (0 = bought and sold the same session). % time invested is the share of sessions with a position held at the close. Deflated Sharpe uses all 10705 configurations in the trial log. The t-stat treats trades as independent; positions held at the same time are not, so read it as an upper bound.

## Against the benchmarks (same sessions)

| Experiment | Net return % | Minus SPY (points) | Minus equal-weight universe (points) | CAGR minus SPY | CAGR minus equal-weight | Sharpe | Sharpe minus SPY | Sharpe minus equal-weight |
|---|---|---|---|---|---|---|---|---|
| trend_highs (tuned monthly) | 10.41 | -198.43 | -236.02 | -11.55 | -12.92 | 0.21 | -0.52 | -0.59 |
| trend_highs (fixed 55-day high, 3 ATR) | 5.2 | -203.64 | -241.23 | -12.06 | -13.43 | 0.12 | -0.61 | -0.68 |
| rel_strength (tuned monthly) | 65.97 | -142.87 | -180.46 | -7.12 | -8.49 | 0.87 | 0.14 | 0.07 |
| rel_strength (fixed 6 months, top 5) | 58.63 | -150.21 | -187.8 | -7.63 | -9.0 | 0.92 | 0.19 | 0.12 |
| winners_run (tuned monthly) | -71.03 | -279.87 | -317.46 | -24.82 | -26.19 | -1.8 | -2.53 | -2.6 |
| winners_run (fixed 5 sessions) | -72.22 | -281.06 | -318.65 | -25.21 | -26.58 | -1.9 | -2.63 | -2.7 |
| overnight_spy (tuned monthly) | 1.75 | -207.09 | -244.68 | -12.42 | -13.79 | 0.19 | -0.54 | -0.61 |
| overnight_spy (fixed VIX < 30) | 1.24 | -207.6 | -245.19 | -12.47 | -13.84 | 0.13 | -0.6 | -0.67 |

Points are percentage points of total return (and of CAGR per year). A rule that is mostly in cash can have a decent Sharpe and still make far less than holding the universe: read both columns.

## Costs

| Experiment | Avg position $ | Gross $ | Spread $ | Impact $ | Gap slippage $ | Fees $ | Net $ | Gross bp of position | Costs bp of position | Stops jumped by the open |
|---|---|---|---|---|---|---|---|---|---|---|
| trend_highs (tuned monthly) | 6056.0 | 16978.31 | 1043.91 | 829.65 | 4569.91 | 129.55 | 10405.29 | 39.88 | 15.44 | 75 |
| trend_highs (fixed 55-day high, 3 ATR) | 6192.0 | 10719.04 | 1018.47 | 690.45 | 3679.85 | 127.14 | 5203.13 | 25.57 | 13.16 | 60 |
| rel_strength (tuned monthly) | 6842.0 | 68275.17 | 1309.73 | 840.53 | 0.0 | 150.42 | 65974.49 | 142.36 | 4.8 | 0 |
| rel_strength (fixed 6 months, top 5) | 6719.0 | 60505.53 | 1071.01 | 679.09 | 0.0 | 121.7 | 58633.73 | 156.88 | 4.85 | 0 |
| winners_run (tuned monthly) | 6932.0 | 8015.78 | 27485.44 | 43075.56 | 4709.46 | 3775.58 | -71030.25 | 0.64 | 6.36 | 31 |
| winners_run (fixed 5 sessions) | 6848.0 | 6300.73 | 27338.37 | 42799.5 | 4627.69 | 3754.47 | -72219.29 | 0.51 | 6.35 | 24 |
| overnight_spy (tuned monthly) | 13140.0 | 11218.86 | 5724.08 | 2932.51 | 0.0 | 807.8 | 1754.47 | 3.92 | 3.31 | 0 |
| overnight_spy (fixed VIX < 30) | 13007.0 | 10874.46 | 5827.49 | 2986.01 | 0.0 | 822.45 | 1238.5 | 3.73 | 3.31 | 0 |

bp of position = per $ put into the trades. `overnight_spy` lives or dies on this table: one night in SPY earns a few bp gross, and a round trip costs spread and impact twice.

## Rules and notes

- Known limits of every row: entries fill at the next session's open (the close is the last regular-hours minute, not the auction); the cost model is the uncalibrated one under review in #111; prices carry no dividends, for the rules and for both benchmarks; the universe is today's list (first line).
- `trend_highs`: buy at the next open a stock that closes above its highest close of the previous 55 sessions (20 / 55 / 100), above its 200-day average, with a 20-day return above SPY's; stop and trailing stop 3 daily ATRs (2 / 3 / 4); out after 40 sessions; at most 5 positions and 60% of equity. Earnings blackout: on. Report `walkforward_balanced_md_trend_highs_20261112T141956.json`.
- `rel_strength`: on each week's first session hold the top 5 (5 / 10) stocks by 6-month (3 / 6 / 9) return skipping the last month, equal risk, replaced weekly; flat when SPY closed the week under its 200-day average; a position is sold after 60 sessions at the latest; at most 60% of equity. Earnings blackout: on. Report `walkforward_balanced_md_rel_strength_20261112T142003.json`.
- `winners_run`: variant A's intraday dip entry unchanged; a trade still open and up at least 0.5R at 15:55 is kept overnight (the part that fits the gap sizing) with a stop 2 daily ATRs under the highest close, for at most 5 sessions (3 / 5 / 10); everything else is sold at 15:55 as before; at most 3 carried positions and 25% of equity (the intraday leg can use the other 75%). Earnings blackout: on. Report `walkforward_balanced_md_winners_run_20261112T143008.json`.
- `overnight_spy`: buy SPY at the close and sell it at the next open, only when the previous day's VIX close is under 30 (no filter / 25 / 30); one position, sized like every other (a 3 ATR gap costs at most the per-trade risk, and never more than 15% of equity). Earnings blackout: not applicable. Report `walkforward_balanced_md_overnight_spy_20261112T142006.json`.
- Earnings blackout, `trend_highs`, `rel_strength`, `winners_run`: on: window around an estimated date from past results headlines, not a known date: 288 of 288 stocks have detected reports, 100% at a plausible 3-5 a year (median 3.9); flat the night before 95% of 11426 detected reports; 14% of stock-sessions blacked out.
- Earnings blackout, `overnight_spy`: not applicable: SPY has no earnings date.
