# Experiments 001: can variant A (dip reversal) be rescued? No.

Run by the stakeholder on 12 Nov 2026 with `./scripts/run_experiments.sh` (issue #109). Out-of-sample 2017-05-01 to
2026-11-06, 300 stocks, train 12 months, test the next month, roll monthly. Deflated Sharpe counts all 8,286
configurations in the trial log.

| Experiment (tuned monthly) | Trades | Net return | Gross bp/trade | Net bp/trade | t-stat | Hit rate | Sharpe | Max drawdown | Months positive |
|---|---|---|---|---|---|---|---|---|---|
| control (variant A) | 10,124 | -58.99% | -0.041 | -0.87 | -8.09 | 44.7% | -1.81 | 59.7% | 30 of 115 |
| mornings (no entry after ~10:30) | 6,438 | -40.03% | 0.050 | -0.78 | -5.03 | 46.1% | -1.19 | 40.5% | 40 of 115 |
| wide_stop (2.5-4 ATR, same $ risk) | 11,085 | -29.14% | -0.006 | -0.31 | -5.45 | 48.8% | -1.16 | 29.8% | 43 of 115 |
| high_vix (VIX above 18-26) | 3,841 | -27.36% | -0.164 | -0.82 | -5.67 | 42.7% | -1.17 | 27.5% | 22 of 115 |
| mornings_wide_stop | 6,568 | -19.25% | 0.026 | -0.32 | -3.52 | 48.7% | -0.80 | 20.0% | 44 of 115 |

Fixed (pre-declared) settings gave the same picture; the best was high_vix with VIX > 22: -14.89%, gross +0.19 bp,
net -0.44 bp, t = -2.92, trading in 59 of 115 months. Basis points are of the $ equity per trade.

## Cost split of the control (10,124 trades, average position $13,907)

| | $ | bp of the position per trade |
|---|---|---|
| Gross P&L (no costs) | -4,139 | -0.29 |
| Spread | -31,374 | 2.23 |
| Price impact | -44,193 | 3.14 |
| Slippage past stops | -3,611 | 0.26 |
| Fees | -4,260 | 0.30 |
| **Net** | **-87,578** | |

By exit, gross: stops -376,696; targets +140,868; 15:55 exits +130,491; trailing stops +101,199. They cancel out.

## Answer to #109

- **The signal has no edge before costs.** Gross P&L is zero to within noise in every experiment (between -0.16 and
  +0.19 bp of equity per trade). Dips in large liquid stocks bounce exactly as often and as far as they keep falling.
- **The whole loss is trading costs**, about 6 bp of the position per round trip.
- **The experiments only shrink the bleeding** by trading less (mornings, high VIX) or smaller (wide stop). None makes
  the gross number positive, so no further tuning of this rule can work. After 8,286 configurations, more tuning would
  only find luck.
- **Variant A is retired as a candidate for the go-live gate.** Decision on the live paper run is the stakeholder's.

## Caveat on the cost model

Price impact is the largest cost (3.1 bp on a $14k order in stocks trading over $50M a day), which looks high for
orders this small. Halving it would halve the loss but cannot create a profit, because gross is zero. Reviewed in #111.

## Next

1. #112: the same ten-year walk-forward for the momentum-type rules that already exist in shadow (opening-range
   breakout in stocks in play, news continuation) and the SPY late-momentum trade.
2. #94 / #95: the high-beta test group.
3. Product proposal (stakeholder decision): allow overnight / multi-day holds, where the best-documented edges are.
