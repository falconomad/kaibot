# Walk-forward experiments for variant A (issue #109)

Out-of-sample 2017-05-01 to 2026-11-06; train 12 months, test the next month, roll monthly; selection by train Sharpe after costs. "Tuned monthly" is the honest result; the second row of each pair is the experiment's fixed, pre-declared setting.

| Experiment | Trades | Trades/month | Net return % | Mean bp gross | Mean bp net | t-stat | Hit rate | Sharpe | Max drawdown % | Months positive | Deflated Sharpe |
|---|---|---|---|---|---|---|---|---|---|---|---|
| control (tuned monthly) | 10124 | 88.0 | -58.99 | -0.041 | -0.87 | -8.09 | 0.447 | -1.81 | 59.69 | 30 of 115 | 0.0 |
| control (A defaults) | 17298 | 150.4 | -75.69 | 0.061 | -0.8 | -11.17 | 0.424 | -2.47 | 75.84 | 22 of 115 | 0.0 |
| mornings (tuned monthly) | 6438 | 56.0 | -40.03 | 0.05 | -0.78 | -5.03 | 0.461 | -1.19 | 40.49 | 40 of 115 | 0.0 |
| mornings (fixed cutoff 10:30) | 8468 | 73.6 | -49.49 | 0.053 | -0.79 | -6.07 | 0.459 | -1.41 | 50.38 | 37 of 115 | 0.0 |
| wide_stop (tuned monthly) | 11085 | 96.4 | -29.14 | -0.006 | -0.31 | -5.45 | 0.488 | -1.16 | 29.76 | 43 of 115 | 0.0 |
| wide_stop (fixed stop 3 ATR) | 12052 | 104.8 | -36.89 | 0.002 | -0.38 | -6.1 | 0.479 | -1.3 | 37.54 | 42 of 115 | 0.0 |
| high_vix (tuned monthly) | 3841 | 33.4 | -27.36 | -0.164 | -0.82 | -5.67 | 0.427 | -1.17 | 27.51 | 22 of 115 | 0.0 |
| high_vix (fixed VIX > 22) | 3628 | 31.5 | -14.89 | 0.192 | -0.44 | -2.92 | 0.444 | -0.62 | 17.4 | 19 of 115 | 0.0 |
| mornings_wide_stop (tuned monthly) | 6568 | 57.1 | -19.25 | 0.026 | -0.32 | -3.52 | 0.487 | -0.8 | 19.99 | 44 of 115 | 0.0 |
| mornings_wide_stop (fixed cutoff 10:30, stop 3 ATR) | 7815 | 68.0 | -24.81 | 0.022 | -0.36 | -4.18 | 0.488 | -0.94 | 25.52 | 44 of 115 | 0.0 |

Mean bp are basis points of the $ equity per trade; gross is before spread, impact, slippage past stops and fees. Deflated Sharpe uses all 8286 configurations in the trial log.

- `control`: variant A as run in docs/m0/walk_forward_10y.md. 115 of 115 months traded. Report `walkforward_balanced_wf_A_exp-control_20261112T112013.json`.
- `mornings`: no entry after 10:30 ET (cutoff tuned over 10:00 / 10:30 / 11:00). 115 of 115 months traded. Report `walkforward_balanced_wf_A_exp-mornings_20261112T113047.json`.
- `wide_stop`: stop 3 ATR (tuned over 2.5 / 3 / 4) with the dollar risk of the 1.5 ATR stop. 115 of 115 months traded. Report `walkforward_balanced_wf_A_exp-wide_stop_20261112T114103.json`.
- `high_vix`: entries only when yesterday's VIX close is above 22 (tuned over 18 / 22 / 26). 59 of 115 months traded. Report `walkforward_balanced_wf_A_exp-high_vix_20261112T115122.json`.
- `mornings_wide_stop`: mornings and wide_stop together (3 x 3 configs). 115 of 115 months traded. Report `walkforward_balanced_wf_A_exp-mornings_wide_stop_20261112T120228.json`.
