# Momentum core, ten-year test on the point-in-time S&P 500 (issues #127, #143)

**Point-in-time universe:** on each day the rules could buy only the companies that were in the S&P 500 that day (#123), including those that later left the index, were taken over or failed; prices come from the daily store (#124): signals on split-adjusted closes without dividends, orders, costs and share counts on raw prices, dividends credited, SPY with dividends reinvested.

**PROVISIONAL (decision 15):** research harness of #113, cost model v1. Gate B marks 1 to 6 are judged where their inputs exist, but nothing here can pass Gate B: the go/no-go run is the engine with `SimBroker` (S75), on cost model v2 (S71).

Out-of-sample 2017-05-01 to 2026-11-06. Each row is ONE continuous $100k account of one frozen, pre-registered configuration, starting flat; nothing is tuned. Signals use split-adjusted closes without dividends; orders, costs and holdings raw prices with dividends credited.

| Rule / version | Fill | Costs | Cap % | Earnings | Total return % | CAGR % | Sharpe | Sharpe x 0.5 | Max drawdown % | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Positive years | Avg exposure % | Max exposure % | Avg names | Turnover %/yr | Cost bp/yr | Trades | Avg holding days | Top-5 stocks' share of profit % | Break-even cost x | Sharpe pctile vs random | CAGR pctile vs random |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| trend_momentum | base | 1x | 100 | hold through | 112.31 | 8.24 | 0.59 | 0.29 | 18.27 | 11.75 | -6.57 | 8.03 | 19.35 | 30.44 | -3.7 | -0.53 | 9.34 | 8.02 | 6.87 | 7 of 10 | 83.4 | 98.0 | 21.4 | 533.4 | 20.1 | 1082 | 47.3 | 33.3 | 35.1 | 78.8 | 86.6 |
| trend_momentum | base | 2x | 100 | hold through | 103.41 | 7.76 | 0.56 | 0.28 | 18.25 | 12.24 | -6.83 | 8.05 | 18.13 | 30.1 | -3.73 | -1.21 | 7.16 | 7.45 | 6.96 | 7 of 10 | 83.4 | 98.0 | 21.4 | 533.7 | 38.6 | 1085 | 47.2 | 36.0 |  |  |  |
| trend_momentum | base | 3x | 100 | hold through | 103.04 | 7.74 | 0.56 | 0.28 | 18.27 | 12.08 | -7.02 | 7.95 | 18.41 | 29.94 | -3.92 | -1.47 | 8.23 | 7.24 | 6.76 | 7 of 10 | 83.4 | 98.0 | 21.4 | 533.6 | 57.0 | 1083 | 47.3 | 35.9 |  |  |  |
| trend_momentum | base | 1x | 60 | hold through | 65.95 | 5.47 | 0.56 | 0.28 | 14.18 | 7.65 | -3.7 | 3.89 | 12.13 | 17.45 | -1.72 | 2.15 | 2.95 | 6.79 | 6.0 | 8 of 10 | 52.3 | 62.1 | 14.5 | 329.2 | 12.3 | 720 | 48.1 | 40.0 | 15.9 | 67.4 | 83.4 |
| trend_momentum | base | 2x | 60 | hold through | 63.57 | 5.31 | 0.54 | 0.27 | 13.98 | 6.94 | -3.01 | 3.82 | 11.99 | 17.32 | -2.18 | 1.1 | 2.97 | 6.94 | 6.15 | 8 of 10 | 52.4 | 62.0 | 14.5 | 328.8 | 23.6 | 720 | 48.1 | 41.8 |  |  |  |
| trend_momentum | base | 3x | 60 | hold through | 60.97 | 5.14 | 0.53 | 0.27 | 14.54 | 6.87 | -3.52 | 4.29 | 11.89 | 17.56 | -2.23 | 0.95 | 2.85 | 6.51 | 5.26 | 8 of 10 | 52.3 | 62.0 | 14.5 | 327.9 | 34.9 | 716 | 48.4 | 42.5 |  |  |  |
| trend_momentum | base | 1x | 30 | hold through | 32.64 | 3.02 | 0.53 | 0.27 | 8.57 | 3.94 | -1.88 | 2.38 | 3.65 | 9.1 | 1.4 | 2.18 | 0.48 | 4.71 | 3.03 | 9 of 10 | 26.1 | 32.4 | 8.1 | 160.6 | 6.1 | 392 | 49.5 | 53.9 | 9.2 | 61.8 | 83.4 |
| trend_momentum | base | 2x | 30 | hold through | 31.8 | 2.95 | 0.52 | 0.26 | 8.79 | 4.24 | -2.19 | 2.45 | 3.59 | 9.06 | 1.39 | 2.22 | 0.23 | 4.54 | 2.84 | 9 of 10 | 26.1 | 32.4 | 8.1 | 160.6 | 11.7 | 394 | 49.4 | 55.7 |  |  |  |
| trend_momentum | base | 3x | 30 | hold through | 33.0 | 3.05 | 0.54 | 0.27 | 8.55 | 4.2 | -2.49 | 2.72 | 3.53 | 9.07 | 1.37 | 2.43 | 0.42 | 4.67 | 3.4 | 9 of 10 | 26.2 | 32.4 | 8.2 | 161.9 | 17.5 | 397 | 49.3 | 53.8 |  |  |  |
| trend_momentum | stress | 1x | 100 | hold through | 98.2 | 7.46 | 0.55 | 0.28 | 18.7 | 11.62 | -4.8 | 7.94 | 15.62 | 31.79 | -3.36 | -2.76 | 8.27 | 4.11 | 7.05 | 7 of 10 | 83.4 | 97.5 | 21.3 | 533.7 | 38.6 | 1082 | 47.2 | 36.7 | 9.7 |  |  |
| trend_momentum | stress | 2x | 100 | hold through | 91.8 | 7.09 | 0.52 | 0.26 | 18.43 | 11.28 | -5.43 | 7.68 | 15.86 | 30.93 | -3.42 | -3.0 | 8.43 | 3.45 | 6.16 | 7 of 10 | 83.4 | 97.5 | 21.3 | 532.1 | 75.3 | 1082 | 47.2 | 37.8 |  |  |  |
| trend_momentum | stress | 3x | 100 | hold through | 82.32 | 6.52 | 0.49 | 0.24 | 20.21 | 10.74 | -5.77 | 6.95 | 15.38 | 30.05 | -3.66 | -4.71 | 7.49 | 3.43 | 6.68 | 7 of 10 | 83.4 | 97.5 | 21.4 | 533.2 | 112.3 | 1084 | 47.3 | 41.1 |  |  |  |
| trend_momentum | stress | 1x | 60 | hold through | 70.98 | 5.81 | 0.59 | 0.29 | 12.07 | 7.27 | -2.52 | 3.76 | 9.84 | 20.15 | -1.95 | 0.59 | 3.04 | 8.84 | 7.96 | 8 of 10 | 52.4 | 62.1 | 14.5 | 325.7 | 23.5 | 712 | 48.7 | 35.4 | costs did not lower the profit |  |  |
| trend_momentum | stress | 2x | 60 | hold through | 65.14 | 5.42 | 0.56 | 0.28 | 12.68 | 6.47 | -2.28 | 3.46 | 8.69 | 19.73 | -1.43 | 0.94 | 2.83 | 7.71 | 6.97 | 8 of 10 | 52.3 | 62.1 | 14.5 | 326.6 | 46.0 | 717 | 48.5 | 38.0 |  |  |  |
| trend_momentum | stress | 3x | 60 | hold through | 63.53 | 5.31 | 0.55 | 0.28 | 12.37 | 6.29 | -2.47 | 5.37 | 9.06 | 19.1 | -1.48 | 0.73 | 2.4 | 6.39 | 6.6 | 8 of 10 | 52.4 | 62.1 | 14.5 | 327.2 | 68.9 | 718 | 48.5 | 41.8 |  |  |  |
| trend_momentum | stress | 1x | 30 | hold through | 28.1 | 2.64 | 0.48 | 0.24 | 9.98 | 2.07 | -0.47 | 3.09 | 2.89 | 8.92 | 1.68 | 1.81 | -0.87 | 3.08 | 3.18 | 8 of 10 | 26.2 | 32.5 | 8.1 | 162.6 | 11.9 | 393 | 49.5 | 64.2 | 5.7 |  |  |
| trend_momentum | stress | 2x | 30 | hold through | 28.44 | 2.67 | 0.48 | 0.24 | 9.15 | 1.98 | -0.51 | 2.92 | 2.71 | 9.42 | 1.64 | 1.74 | -0.37 | 3.46 | 2.68 | 8 of 10 | 26.3 | 32.5 | 8.1 | 163.7 | 23.4 | 397 | 49.2 | 63.1 |  |  |  |
| trend_momentum | stress | 3x | 30 | hold through | 26.27 | 2.48 | 0.45 | 0.23 | 10.3 | 1.9 | -0.94 | 2.32 | 2.51 | 9.34 | 2.0 | 1.69 | -1.72 | 2.99 | 3.9 | 8 of 10 | 26.1 | 32.5 | 8.1 | 163.0 | 34.7 | 397 | 49.2 | 66.2 |  |  |  |
| trend_momentum ~ neighbour reg60 | base | 1x | 100 | hold through | 38.77 | 3.51 | 0.31 | 0.15 | 24.05 | 9.71 | -11.18 | 5.14 | 13.94 | 16.02 | -7.58 | 0.92 | -2.88 | 8.64 | 4.1 | 7 of 10 | 82.6 | 97.9 | 20.9 | 703.7 | 26.7 | 1459 | 34.3 | 79.4 |  |  |  |
| trend_momentum ~ neighbour reg125 | base | 1x | 100 | hold through | 73.29 | 5.96 | 0.45 | 0.23 | 24.66 | 16.04 | -7.15 | 2.09 | 7.77 | 20.56 | -9.33 | -0.91 | 16.32 | 8.09 | 7.35 | 7 of 10 | 83.5 | 97.8 | 21.5 | 486.0 | 18.1 | 972 | 53.0 | 45.6 |  |  |  |
| trend_momentum ~ neighbour exit30 | base | 1x | 100 | hold through | 103.49 | 7.76 | 0.56 | 0.28 | 20.38 | 11.71 | -3.79 | 9.3 | 14.97 | 28.56 | -7.3 | -2.45 | 8.44 | 9.64 | 9.01 | 7 of 10 | 83.6 | 97.7 | 21.6 | 484.7 | 18.2 | 962 | 53.7 | 29.6 |  |  |  |
| trend_momentum ~ neighbour nofilter | base | 1x | 100 | hold through | 95.9 | 7.33 | 0.49 | 0.24 | 24.89 | 11.75 | -11.58 | 15.99 | 12.22 | 29.82 | -3.68 | -1.03 | 8.39 | 6.59 | 6.53 | 7 of 10 | 94.5 | 98.0 | 24.7 | 604.9 | 22.7 | 1276 | 46.3 | 39.3 |  |  |  |
| trend_momentum ~ neighbour risk15 | base | 1x | 100 | hold through | 111.82 | 8.22 | 0.55 | 0.28 | 21.64 | 12.41 | -6.54 | 7.25 | 20.04 | 31.25 | -3.94 | 2.5 | 1.47 | 12.85 | 5.84 | 8 of 10 | 84.6 | 98.2 | 15.5 | 534.8 | 20.4 | 771 | 48.3 | 47.4 |  |  |  |
| BENCHMARK: SPY buy-and-hold (total return, dividends reinvested) |  |  |  |  | 258.39 | 14.37 | 0.83 | 0.41 | 33.78 | 13.78 | -4.46 | 31.13 | 18.49 | 28.62 | -18.17 | 26.19 | 24.89 | 17.72 | 8.67 | 8 of 10 | 100.0 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: SPY buy-and-hold (price only, no dividends) |  |  |  |  | 208.79 | 12.6 | 0.74 | 0.37 | 34.18 | 12.1 | -6.32 | 28.7 | 16.28 | 26.91 | -19.48 | 24.29 | 23.3 | 16.35 | 7.83 | 8 of 10 | 100.0 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: equal-weight index members of each month, rebalanced monthly, after costs |  |  |  |  | 163.63 | 10.74 | 0.64 | 0.32 | 39.86 | 10.67 | -9.76 | 28.33 | 11.86 | 29.76 | -10.83 | 13.51 | 10.61 | 11.81 | 13.21 | 8 of 10 | 100.0 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: SPY (total return) scaled to trend_momentum's average exposure, rest in cash |  |  |  |  | 196.33 | 12.11 | 0.83 | 0.41 | 28.83 | 11.39 | -3.54 | 25.5 | 16.04 | 23.5 | -15.06 | 21.55 | 20.5 | 14.86 | 7.3 | 8 of 10 | 83.4 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: SPY (price only) scaled to trend_momentum's average exposure, rest in cash |  |  |  |  | 161.74 | 10.65 | 0.74 | 0.37 | 29.18 | 10.02 | -5.11 | 23.55 | 14.24 | 22.13 | -16.19 | 20.03 | 19.22 | 13.75 | 6.61 | 8 of 10 | 83.4 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for trend_momentum (median of 500 runs) | base | 1x | 30 | hold through | 23.25 | 2.22 | 0.48 | 0.24 | 9.91 |  |  |  |  |  |  |  |  |  |  |  | 25.65 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for trend_momentum (median of 500 runs) | base | 1x | 60 | hold through | 46.39 | 4.09 | 0.49 | 0.24 | 17.98 |  |  |  |  |  |  |  |  |  |  |  | 51.52 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for trend_momentum (median of 500 runs) | base | 1x | 100 | hold through | 74.44 | 6.03 | 0.49 | 0.24 | 25.65 |  |  |  |  |  |  |  |  |  |  |  | 82.78 |  |  |  |  |  |  |  |  |  |  |
| mom_12_1_band | base | 1x | 100 | hold through | 139.9 | 9.64 | 0.59 | 0.29 | 24.96 | 16.4 | -4.16 | 5.91 | 16.11 | 6.67 | -1.66 | 5.57 | 39.07 | 5.84 | 7.29 | 8 of 10 | 74.2 | 97.8 | 18.2 | 141.2 | 5.3 | 370 | 118.1 | 62.7 | 48.3 | 74.6 | 98.4 |
| mom_12_1_band | base | 2x | 100 | hold through | 136.8 | 9.49 | 0.58 | 0.29 | 25.11 | 16.37 | -4.19 | 5.83 | 16.24 | 6.62 | -1.7 | 5.51 | 39.13 | 5.67 | 6.21 | 8 of 10 | 74.3 | 97.9 | 18.2 | 141.8 | 10.3 | 371 | 117.8 | 63.8 |  |  |  |
| mom_12_1_band | base | 3x | 100 | hold through | 138.48 | 9.58 | 0.59 | 0.29 | 25.17 | 16.3 | -4.26 | 5.77 | 16.16 | 6.59 | -1.73 | 5.45 | 39.48 | 5.75 | 6.98 | 8 of 10 | 74.2 | 97.9 | 18.2 | 141.1 | 15.2 | 370 | 118.1 | 63.1 |  |  |  |
| mom_12_1_band | base | 1x | 60 | hold through | 110.15 | 8.13 | 0.59 | 0.29 | 19.77 | 10.48 | -2.51 | 2.49 | 16.22 | 5.77 | -2.17 | 4.75 | 33.96 | 6.22 | 6.19 | 8 of 10 | 54.7 | 63.8 | 15.5 | 99.4 | 3.8 | 309 | 119.9 | 67.0 | 89.2 | 60.0 | 99.2 |
| mom_12_1_band | base | 2x | 60 | hold through | 109.71 | 8.1 | 0.59 | 0.29 | 19.88 | 10.45 | -2.52 | 1.89 | 16.57 | 5.72 | -2.14 | 4.73 | 34.07 | 6.28 | 6.23 | 8 of 10 | 54.7 | 64.0 | 15.5 | 99.3 | 7.3 | 310 | 119.7 | 67.2 |  |  |  |
| mom_12_1_band | base | 3x | 60 | hold through | 107.16 | 7.96 | 0.58 | 0.29 | 19.95 | 10.41 | -2.57 | 1.82 | 16.5 | 5.66 | -2.15 | 4.26 | 34.08 | 5.45 | 6.52 | 8 of 10 | 54.8 | 64.0 | 15.4 | 99.5 | 10.8 | 309 | 119.6 | 68.4 |  |  |  |
| mom_12_1_band | base | 1x | 30 | hold through | 79.81 | 6.37 | 0.67 | 0.34 | 14.49 | 6.39 | 1.36 | -1.36 | 11.65 | 4.3 | 1.42 | 3.4 | 22.13 | -0.48 | 13.89 | 8 of 10 | 28.7 | 39.2 | 9.2 | 45.7 | 1.8 | 169 | 130.8 | 73.5 | costs did not lower the profit | 89.0 | 100.0 |
| mom_12_1_band | base | 2x | 30 | hold through | 79.79 | 6.37 | 0.67 | 0.34 | 14.52 | 6.37 | 1.35 | -1.37 | 11.64 | 4.28 | 1.4 | 3.38 | 22.06 | -0.45 | 14.03 | 8 of 10 | 28.7 | 39.4 | 9.2 | 45.7 | 3.5 | 169 | 130.8 | 73.5 |  |  |  |
| mom_12_1_band | base | 3x | 30 | hold through | 78.89 | 6.31 | 0.67 | 0.34 | 14.56 | 6.36 | 1.33 | -1.39 | 11.61 | 4.27 | 1.39 | 3.26 | 22.0 | -0.57 | 13.93 | 8 of 10 | 28.6 | 39.4 | 9.2 | 45.7 | 5.1 | 169 | 130.8 | 74.0 |  |  |  |
| mom_12_1_band | stress | 1x | 100 | hold through | 138.5 | 9.58 | 0.59 | 0.29 | 26.78 | 16.76 | -3.93 | 4.91 | 14.98 | 7.78 | -0.85 | 4.82 | 39.33 | 7.31 | 5.24 | 8 of 10 | 74.2 | 97.6 | 18.2 | 141.9 | 10.3 | 371 | 117.6 | 64.3 | 16.6 |  |  |
| mom_12_1_band | stress | 2x | 100 | hold through | 139.45 | 9.62 | 0.59 | 0.29 | 26.9 | 16.65 | -4.03 | 4.75 | 14.77 | 7.72 | -0.95 | 4.6 | 39.36 | 7.22 | 6.68 | 8 of 10 | 74.2 | 97.6 | 18.2 | 141.3 | 20.1 | 370 | 117.9 | 63.4 |  |  |  |
| mom_12_1_band | stress | 3x | 100 | hold through | 136.18 | 9.46 | 0.58 | 0.29 | 27.16 | 16.52 | -4.13 | 4.61 | 14.54 | 7.45 | -0.89 | 4.6 | 39.46 | 7.11 | 6.04 | 8 of 10 | 74.2 | 97.7 | 18.2 | 142.0 | 30.1 | 370 | 117.8 | 64.7 |  |  |  |
| mom_12_1_band | stress | 1x | 60 | hold through | 110.12 | 8.13 | 0.6 | 0.3 | 21.63 | 10.87 | -2.83 | 1.34 | 15.49 | 6.08 | -1.2 | 3.96 | 34.58 | 6.96 | 6.27 | 8 of 10 | 54.6 | 63.9 | 15.5 | 99.2 | 7.3 | 309 | 119.9 | 68.3 | 128.9 |  |  |
| mom_12_1_band | stress | 2x | 60 | hold through | 109.14 | 8.07 | 0.59 | 0.29 | 21.17 | 10.8 | -2.87 | 1.0 | 14.98 | 6.87 | -1.55 | 3.87 | 34.65 | 6.94 | 6.33 | 8 of 10 | 54.7 | 63.9 | 15.5 | 99.5 | 14.4 | 310 | 119.8 | 68.8 |  |  |  |
| mom_12_1_band | stress | 3x | 60 | hold through | 107.55 | 7.99 | 0.59 | 0.29 | 21.21 | 10.74 | -2.86 | 0.88 | 14.95 | 6.75 | -1.64 | 3.76 | 34.54 | 6.9 | 6.18 | 8 of 10 | 54.6 | 63.8 | 15.5 | 99.6 | 21.4 | 311 | 119.7 | 69.8 |  |  |  |
| mom_12_1_band | stress | 1x | 30 | hold through | 68.81 | 5.66 | 0.63 | 0.32 | 14.82 | 6.68 | 0.83 | -1.24 | 11.85 | 4.78 | 1.07 | 2.28 | 21.64 | -0.45 | 8.32 | 8 of 10 | 28.5 | 37.5 | 9.6 | 47.0 | 3.5 | 178 | 129.0 | 78.9 | 52.1 |  |  |
| mom_12_1_band | stress | 2x | 30 | hold through | 69.01 | 5.68 | 0.63 | 0.32 | 14.88 | 6.65 | 0.79 | -1.28 | 11.63 | 4.57 | 1.46 | 2.07 | 21.66 | -0.38 | 8.72 | 8 of 10 | 28.5 | 37.4 | 9.6 | 47.1 | 7.0 | 178 | 128.9 | 79.2 |  |  |  |
| mom_12_1_band | stress | 3x | 30 | hold through | 69.06 | 5.68 | 0.63 | 0.32 | 14.93 | 6.62 | 0.77 | -1.31 | 11.86 | 4.68 | 0.98 | 2.04 | 21.62 | -0.4 | 9.1 | 8 of 10 | 28.5 | 37.5 | 9.6 | 47.0 | 10.3 | 178 | 129.0 | 80.0 |  |  |  |
| mom_12_1_band ~ neighbour only12 | base | 1x | 100 | hold through | 106.52 | 7.93 | 0.47 | 0.23 | 42.53 | 14.69 | -11.18 | 0.98 | 18.47 | 7.11 | 5.11 | 8.78 | 27.38 | 5.25 | 3.22 | 9 of 10 | 75.9 | 98.0 | 18.8 | 108.4 | 4.2 | 290 | 155.1 | 57.8 |  |  |  |
| mom_12_1_band ~ neighbour only6 | base | 1x | 100 | hold through | 76.27 | 6.15 | 0.42 | 0.21 | 37.6 | 14.82 | -9.91 | 1.36 | 5.64 | 12.42 | -12.09 | 8.51 | 17.26 | 13.45 | 11.55 | 8 of 10 | 72.5 | 99.1 | 17.9 | 216.5 | 8.4 | 537 | 79.6 | 82.1 |  |  |  |
| mom_12_1_band ~ neighbour exit30 | base | 1x | 100 | hold through | 176.94 | 11.31 | 0.63 | 0.32 | 30.32 | 13.53 | -6.54 | 6.51 | 21.87 | 14.51 | -0.91 | 8.33 | 35.81 | 12.17 | 7.39 | 8 of 10 | 78.0 | 97.8 | 18.7 | 105.5 | 4.0 | 283 | 158.0 | 51.8 |  |  |  |
| mom_12_1_band ~ neighbour names10 | base | 1x | 100 | hold through | 84.11 | 6.63 | 0.61 | 0.3 | 16.46 | 9.12 | 1.48 | 0.42 | 12.86 | 5.35 | 4.84 | 4.03 | 30.11 | -2.57 | 0.71 | 9 of 10 | 37.6 | 58.2 | 9.4 | 58.6 | 2.3 | 167 | 134.1 | 73.7 |  |  |  |
| mom_12_1_band ~ neighbour risk15 | base | 1x | 100 | hold through | 204.38 | 12.43 | 0.63 | 0.32 | 29.71 | 16.29 | -3.25 | 4.41 | 25.2 | 8.66 | -2.22 | 6.91 | 56.74 | 8.35 | 7.28 | 8 of 10 | 86.9 | 97.9 | 16.5 | 159.7 | 6.2 | 329 | 120.2 | 74.7 |  |  |  |
| BENCHMARK: SPY (total return) scaled to mom_12_1_band's average exposure, rest in cash |  |  |  |  | 165.7 | 10.83 | 0.83 | 0.41 | 25.97 | 10.08 | -3.06 | 22.46 | 14.56 | 20.73 | -13.34 | 19.03 | 18.11 | 13.26 | 6.53 | 8 of 10 | 74.2 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: SPY (price only) scaled to mom_12_1_band's average exposure, rest in cash |  |  |  |  | 137.94 | 9.55 | 0.74 | 0.37 | 26.3 | 8.88 | -4.46 | 20.77 | 12.98 | 19.54 | -14.37 | 17.7 | 17.0 | 12.29 | 5.92 | 8 of 10 | 74.2 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band (median of 500 runs) | base | 1x | 30 | hold through | 22.39 | 2.15 | 0.47 | 0.23 | 12.23 |  |  |  |  |  |  |  |  |  |  |  | 23.59 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band (median of 500 runs) | base | 1x | 60 | hold through | 53.23 | 4.59 | 0.56 | 0.28 | 18.18 |  |  |  |  |  |  |  |  |  |  |  | 47.88 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band (median of 500 runs) | base | 1x | 100 | hold through | 70.81 | 5.79 | 0.51 | 0.26 | 30.52 |  |  |  |  |  |  |  |  |  |  |  | 66.84 |  |  |  |  |  |  |  |  |  |  |
| mom_12_1_band_nofilter | base | 1x | 100 | hold through | 158.87 | 10.53 | 0.58 | 0.29 | 34.33 | 16.4 | -9.3 | 11.43 | 15.35 | 6.48 | 5.11 | 6.14 | 37.58 | 6.08 | 10.03 | 9 of 10 | 78.7 | 97.8 | 19.8 | 143.7 | 5.5 | 398 | 119.1 | 56.5 | 353.2 | 73.2 | 99.2 |
| mom_12_1_band_nofilter | base | 2x | 100 | hold through | 158.39 | 10.5 | 0.58 | 0.29 | 34.38 | 16.37 | -9.3 | 11.29 | 15.35 | 6.45 | 5.15 | 6.13 | 37.58 | 6.08 | 10.01 | 9 of 10 | 78.7 | 97.9 | 19.8 | 143.6 | 10.5 | 398 | 119.1 | 56.7 |  |  |  |
| mom_12_1_band_nofilter | base | 3x | 100 | hold through | 157.53 | 10.47 | 0.58 | 0.29 | 34.43 | 16.3 | -9.37 | 11.27 | 15.35 | 6.43 | 5.14 | 6.08 | 37.6 | 5.99 | 9.96 | 9 of 10 | 78.7 | 97.9 | 19.8 | 143.6 | 15.5 | 397 | 119.2 | 56.9 |  |  |  |
| mom_12_1_band_nofilter | base | 1x | 60 | hold through | 123.93 | 8.85 | 0.61 | 0.3 | 22.47 | 10.48 | -7.72 | 8.81 | 13.56 | 5.6 | 4.44 | 4.49 | 33.28 | 6.53 | 8.64 | 9 of 10 | 58.8 | 64.4 | 17.2 | 102.8 | 3.9 | 334 | 123.4 | 62.7 | 128.9 | 66.4 | 99.2 |
| mom_12_1_band_nofilter | base | 2x | 60 | hold through | 123.57 | 8.83 | 0.61 | 0.3 | 22.58 | 10.45 | -7.77 | 8.73 | 13.5 | 5.56 | 4.41 | 4.51 | 33.42 | 6.61 | 8.54 | 9 of 10 | 58.8 | 64.4 | 17.2 | 102.8 | 7.5 | 334 | 123.4 | 62.9 |  |  |  |
| mom_12_1_band_nofilter | base | 3x | 60 | hold through | 126.79 | 9.0 | 0.61 | 0.3 | 22.79 | 10.41 | -7.83 | 8.69 | 14.06 | 4.96 | 4.43 | 5.07 | 33.56 | 6.93 | 9.28 | 9 of 10 | 58.7 | 64.4 | 17.2 | 102.5 | 11.2 | 333 | 123.6 | 61.3 |  |  |  |
| mom_12_1_band_nofilter | base | 1x | 30 | hold through | 77.33 | 6.21 | 0.66 | 0.33 | 13.92 | 6.39 | -2.7 | 3.71 | 11.63 | 5.36 | 4.83 | 3.7 | 21.37 | -0.12 | 6.58 | 8 of 10 | 30.5 | 37.4 | 11.1 | 50.3 | 2.0 | 204 | 129.8 | 72.9 | 21.4 | 85.2 | 100.0 |
| mom_12_1_band_nofilter | base | 2x | 30 | hold through | 77.91 | 6.25 | 0.66 | 0.33 | 13.96 | 6.37 | -2.72 | 3.69 | 11.62 | 5.3 | 4.92 | 3.69 | 21.32 | 0.31 | 6.55 | 9 of 10 | 30.5 | 39.5 | 10.9 | 50.1 | 3.7 | 202 | 129.5 | 72.9 |  |  |  |
| mom_12_1_band_nofilter | base | 3x | 30 | hold through | 78.96 | 6.32 | 0.66 | 0.33 | 13.95 | 6.36 | -2.74 | 3.67 | 11.7 | 5.48 | 5.16 | 3.67 | 21.54 | -0.61 | 7.56 | 8 of 10 | 30.5 | 37.6 | 10.8 | 50.0 | 5.5 | 202 | 128.1 | 71.8 |  |  |  |
| mom_12_1_band_nofilter | stress | 1x | 100 | hold through | 167.66 | 10.91 | 0.6 | 0.3 | 35.65 | 16.76 | -9.22 | 10.3 | 15.43 | 7.61 | 6.71 | 5.66 | 37.89 | 7.15 | 10.65 | 9 of 10 | 78.8 | 97.6 | 19.7 | 143.6 | 10.5 | 396 | 119.4 | 55.6 | 63.5 |  |  |
| mom_12_1_band_nofilter | stress | 2x | 100 | hold through | 164.85 | 10.79 | 0.6 | 0.3 | 35.75 | 16.65 | -9.34 | 10.17 | 15.23 | 7.56 | 6.73 | 5.51 | 38.16 | 7.11 | 10.05 | 9 of 10 | 78.8 | 97.6 | 19.7 | 143.3 | 20.5 | 396 | 119.4 | 56.2 |  |  |  |
| mom_12_1_band_nofilter | stress | 3x | 100 | hold through | 160.17 | 10.58 | 0.59 | 0.29 | 35.94 | 16.52 | -9.44 | 10.07 | 15.22 | 7.41 | 6.62 | 5.12 | 37.21 | 7.14 | 9.86 | 9 of 10 | 78.7 | 97.7 | 19.7 | 143.3 | 30.5 | 396 | 119.4 | 56.7 |  |  |  |
| mom_12_1_band_nofilter | stress | 1x | 60 | hold through | 132.46 | 9.28 | 0.62 | 0.31 | 25.47 | 10.87 | -8.02 | 7.87 | 15.48 | 6.74 | 5.96 | 4.38 | 31.27 | 7.87 | 9.5 | 9 of 10 | 59.0 | 64.4 | 17.3 | 102.7 | 7.5 | 336 | 123.0 | 60.4 | costs did not lower the profit |  |  |
| mom_12_1_band_nofilter | stress | 2x | 60 | hold through | 131.99 | 9.26 | 0.62 | 0.31 | 25.76 | 10.8 | -8.07 | 7.72 | 14.87 | 6.78 | 5.88 | 4.16 | 32.54 | 7.79 | 9.4 | 9 of 10 | 59.0 | 64.4 | 17.2 | 102.1 | 14.7 | 334 | 123.2 | 60.5 |  |  |  |
| mom_12_1_band_nofilter | stress | 3x | 60 | hold through | 130.22 | 9.17 | 0.62 | 0.31 | 25.72 | 10.74 | -8.08 | 7.65 | 15.37 | 6.64 | 5.82 | 4.06 | 32.5 | 7.87 | 8.52 | 9 of 10 | 58.9 | 64.4 | 17.2 | 102.1 | 21.9 | 334 | 123.3 | 61.4 |  |  |  |
| mom_12_1_band_nofilter | stress | 1x | 30 | hold through | 81.13 | 6.45 | 0.69 | 0.34 | 14.43 | 6.68 | -3.28 | 3.62 | 11.9 | 5.77 | 6.77 | 2.98 | 21.53 | 0.43 | 6.65 | 9 of 10 | 30.4 | 36.5 | 10.9 | 49.7 | 3.7 | 201 | 129.9 | 71.4 | 54.3 |  |  |
| mom_12_1_band_nofilter | stress | 2x | 30 | hold through | 79.12 | 6.32 | 0.66 | 0.33 | 14.54 | 6.65 | -3.33 | 3.57 | 11.51 | 6.31 | 5.39 | 3.53 | 23.27 | 0.46 | 4.73 | 9 of 10 | 30.6 | 37.2 | 10.8 | 48.0 | 7.1 | 197 | 131.8 | 71.4 |  |  |  |
| mom_12_1_band_nofilter | stress | 3x | 30 | hold through | 77.73 | 6.24 | 0.66 | 0.33 | 14.6 | 6.62 | -3.36 | 3.55 | 11.8 | 5.52 | 5.63 | 3.06 | 21.38 | 0.5 | 6.32 | 9 of 10 | 30.5 | 37.1 | 11.0 | 50.5 | 11.0 | 204 | 129.6 | 74.9 |  |  |  |
| mom_12_1_band_nofilter ~ neighbour only12 | base | 1x | 100 | hold through | 139.48 | 9.62 | 0.54 | 0.27 | 38.57 | 14.69 | -15.46 | 13.67 | 24.36 | 8.23 | 8.38 | 8.49 | 27.69 | 4.79 | 2.62 | 9 of 10 | 78.4 | 98.0 | 19.9 | 108.6 | 4.2 | 306 | 156.0 | 50.2 |  |  |  |
| mom_12_1_band_nofilter ~ neighbour only6 | base | 1x | 100 | hold through | 86.61 | 6.78 | 0.44 | 0.22 | 38.7 | 14.82 | -16.82 | 11.46 | 6.64 | 12.53 | -4.82 | 14.66 | 17.41 | 9.26 | 4.33 | 8 of 10 | 78.0 | 99.1 | 19.8 | 231.5 | 9.1 | 586 | 80.8 | 59.0 |  |  |  |
| mom_12_1_band_nofilter ~ neighbour exit30 | base | 1x | 100 | hold through | 201.2 | 12.3 | 0.64 | 0.32 | 35.49 | 13.53 | -10.97 | 10.41 | 24.32 | 12.61 | 5.54 | 8.99 | 37.3 | 14.53 | 6.59 | 9 of 10 | 80.8 | 97.8 | 20.0 | 104.8 | 4.0 | 301 | 158.8 | 44.5 |  |  |  |
| mom_12_1_band_nofilter ~ neighbour names10 | base | 1x | 100 | hold through | 82.83 | 6.55 | 0.59 | 0.29 | 17.95 | 9.12 | -2.77 | 3.99 | 13.76 | 5.31 | 6.31 | 3.36 | 27.95 | -0.97 | -0.66 | 7 of 10 | 38.9 | 57.9 | 10.0 | 59.7 | 2.3 | 177 | 135.1 | 76.3 |  |  |  |
| mom_12_1_band_nofilter ~ neighbour risk15 | base | 1x | 100 | hold through | 216.86 | 12.9 | 0.61 | 0.3 | 39.64 | 16.29 | -10.95 | 9.55 | 19.93 | 9.33 | 9.26 | 9.03 | 53.17 | 9.38 | 6.74 | 9 of 10 | 93.0 | 98.0 | 18.4 | 167.5 | 6.6 | 365 | 120.6 | 62.8 |  |  |  |
| BENCHMARK: SPY (total return) scaled to mom_12_1_band_nofilter's average exposure, rest in cash |  |  |  |  | 180.36 | 11.46 | 0.83 | 0.41 | 27.38 | 10.72 | -3.29 | 23.94 | 15.3 | 22.08 | -14.18 | 20.26 | 19.27 | 14.05 | 6.91 | 8 of 10 | 78.7 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: SPY (price only) scaled to mom_12_1_band_nofilter's average exposure, rest in cash |  |  |  |  | 149.38 | 10.09 | 0.74 | 0.37 | 27.72 | 9.43 | -4.77 | 22.12 | 13.61 | 20.8 | -15.26 | 18.83 | 18.08 | 13.01 | 6.26 | 8 of 10 | 78.7 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band_nofilter (median of 500 runs) | base | 1x | 30 | hold through | 27.75 | 2.61 | 0.5 | 0.25 | 12.89 |  |  |  |  |  |  |  |  |  |  |  | 25.5 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band_nofilter (median of 500 runs) | base | 1x | 60 | hold through | 64.76 | 5.39 | 0.55 | 0.28 | 24.88 |  |  |  |  |  |  |  |  |  |  |  | 51.74 |  |  |  |  |  |  |  |  |  |  |
| BENCHMARK: exposure-matched random portfolios for mom_12_1_band_nofilter (median of 500 runs) | base | 1x | 100 | hold through | 84.54 | 6.66 | 0.52 | 0.26 | 36.08 |  |  |  |  |  |  |  |  |  |  |  | 70.71 |  |  |  |  |  |  |  |  |  |  |

How to read it: fill `base` = decided after the close of T, bought and sold at the close of T+1 at half a spread a side plus impact; `stress` = the open of T+1 with spread and impact doubled. Costs `2x` / `3x` multiply spread and impact of cost model v1 (regulatory fees stay). Break-even is the multiple of spread and impact at which net profit is zero, from the 0x and 1x runs of that row's fill, cap and earnings version (pre-registered); the report JSON also keeps the multiple with the 1x trades held fixed as a cross-check. Sharpe is annualised from daily mark-to-market returns (no risk-free rate); the 50% haircut is beside it; a backtested Sharpe above about 1.0 is a warning sign. Max drawdown is mark-to-market at the close. Turnover is (bought + sold) / 2 a year over the average equity; cost is spread, impact and fees a year in bp of the average equity. Trades are closed positions (resize sales are not counted). The percentile columns say where the rule's net Sharpe and CAGR fall among the exposure-matched random portfolios (each copies the rule's positions: entry and exit session, share of equity, resizes; only the names are random, drawn from the stocks that pass the price and volume filter that day); the Sharpe percentile is the lower of the rule's run and its own names replayed through the control.

## Gate B marks (decision 15, section 7)

### `trend_momentum` (base fill, 1x costs, 100% cap, hold through earnings)

- Mark 1 (net return positive overall and in at least 6 of 10 calendar years): **pass**. net return 112.31%, 7 of 10 calendar years positive.
- Mark 2 (either CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's): **fail**. CAGR 8.24%, max drawdown 18.27% against SPY total return CAGR 14.37%, max drawdown 33.78%; against price-only SPY (CAGR 12.6%, max drawdown 34.18%) it would fail.
- Mark 3 (net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter, exposure and holding-period distribution, >= 500 runs)): **fail**. net Sharpe at percentile 78.8 of 500 exposure-matched random portfolios (lower of the rule's run 78.8 and its names replayed 83.4; CAGR at percentile 86.6; committed exposure within 0.485 points).
- Mark 4 (marks 1-2 still hold at 2x costs (3x and break-even reported)): **fail**. at 2x costs mark 1 holds (net return 103.41%, 7 of 10 years positive) and mark 2 against SPY total return fails (CAGR 7.76%, max drawdown 18.25%); at 3x: net return 103.04%, CAGR 7.74%; break-even at 35.1x costs (from the 0x and 1x runs; 36.6x with the 1x trades held fixed).
- Mark 5 (holds on the point-in-time universe, not only on today's list): **fail**. point-in-time base row: mark 1 holds (net return 112.31%, 7 of 10 years positive), mark 2 fails (CAGR 8.24%, max drawdown 18.27%).
- Mark 6 (every pre-declared neighbour passes mark 1): **pass**. 5 of 5 neighbours pass mark 1.
- Overall: fails Gate B (mark 2, 3, 4, 5); every result of this research harness stays provisional (the harness of #113, not the engine with `SimBroker`; cost model v1): it can fail Gate B, it cannot pass it.

### `mom_12_1_band` (base fill, 1x costs, 100% cap, hold through earnings)

- Mark 1 (net return positive overall and in at least 6 of 10 calendar years): **pass**. net return 139.9%, 8 of 10 calendar years positive.
- Mark 2 (either CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's): **fail**. CAGR 9.64%, max drawdown 24.96% against SPY total return CAGR 14.37%, max drawdown 33.78%; against price-only SPY (CAGR 12.6%, max drawdown 34.18%) it would fail.
- Mark 3 (net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter, exposure and holding-period distribution, >= 500 runs)): **fail**. net Sharpe at percentile 74.6 of 500 exposure-matched random portfolios (lower of the rule's run 74.6 and its names replayed 81.2; CAGR at percentile 98.4; committed exposure within 0.49 points).
- Mark 4 (marks 1-2 still hold at 2x costs (3x and break-even reported)): **fail**. at 2x costs mark 1 holds (net return 136.8%, 8 of 10 years positive) and mark 2 against SPY total return fails (CAGR 9.49%, max drawdown 25.11%); at 3x: net return 138.48%, CAGR 9.58%; break-even at 48.3x costs (from the 0x and 1x runs; 178.5x with the 1x trades held fixed).
- Mark 5 (holds on the point-in-time universe, not only on today's list): **fail**. point-in-time base row: mark 1 holds (net return 139.9%, 8 of 10 years positive), mark 2 fails (CAGR 9.64%, max drawdown 24.96%).
- Mark 6 (every pre-declared neighbour passes mark 1): **pass**. 5 of 5 neighbours pass mark 1.
- Overall: fails Gate B (mark 2, 3, 4, 5); every result of this research harness stays provisional (the harness of #113, not the engine with `SimBroker`; cost model v1): it can fail Gate B, it cannot pass it.

### `mom_12_1_band_nofilter` (base fill, 1x costs, 100% cap, hold through earnings)

- Mark 1 (net return positive overall and in at least 6 of 10 calendar years): **pass**. net return 158.87%, 9 of 10 calendar years positive.
- Mark 2 (either CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's): **fail**. CAGR 10.53%, max drawdown 34.33% against SPY total return CAGR 14.37%, max drawdown 33.78%; against price-only SPY (CAGR 12.6%, max drawdown 34.18%) it would fail.
- Mark 3 (net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter, exposure and holding-period distribution, >= 500 runs)): **fail**. net Sharpe at percentile 73.2 of 500 exposure-matched random portfolios (lower of the rule's run 73.2 and its names replayed 80.6; CAGR at percentile 99.2; committed exposure within 0.409 points).
- Mark 4 (marks 1-2 still hold at 2x costs (3x and break-even reported)): **fail**. at 2x costs mark 1 holds (net return 158.39%, 9 of 10 years positive) and mark 2 against SPY total return fails (CAGR 10.5%, max drawdown 34.38%); at 3x: net return 157.53%, CAGR 10.47%; break-even at 353.2x costs (from the 0x and 1x runs; 193.9x with the 1x trades held fixed).
- Mark 5 (holds on the point-in-time universe, not only on today's list): **fail**. point-in-time base row: mark 1 holds (net return 158.87%, 9 of 10 years positive), mark 2 fails (CAGR 10.53%, max drawdown 34.33%).
- Mark 6 (every pre-declared neighbour passes mark 1): **pass**. 5 of 5 neighbours pass mark 1.
- Overall: fails Gate B (mark 2, 3, 4, 5); every result of this research harness stays provisional (the harness of #113, not the engine with `SimBroker`; cost model v1): it can fail Gate B, it cannot pass it.

## Notes

- `trend_momentum`: pre-registration `docs/prereg/trend_momentum.md` (sha256 `d5b8ec5bbc57`; config/gate_v2.yaml sha256 `1951c0e41b6d`); exit-before-earnings variant skipped (NOT applied: the next earnings date cannot be established reliably from the history on disk (284 of 489 stocks have detected reports, 58% at a plausible 3-5 a year (median 3.9); flat the night before 95% of 11297 detected reports; 9% of stock-sessions blacked out; the bar is 80% of stocks and 85% of reports), so positions are held through earnings); random control 500 runs per exposure cap, seed 127; at most 2 buys of a run skipped because one share cost more than the position size; code `7c0123e`; report `core_balanced_trend_momentum_pit_20261113T084508.json`. SPY total return: table completed: 2 missing quarterly dividends added at the largest nearby amount (1.85 points a year over price only). Disputed splits (share count cannot be carried): APTV: no entries 2017-11-10 to 2017-12-11; a holding is sold at the close of 2017-11-16; DD: no entries 2016-01-04 to 2019-04-08; the other basis starts with the data; DOW@2017-08-24: no entries 2016-01-04 to 2026-06-30; the other basis starts with the data; PARA@2025-07-31: never bought (no Alpaca factor to locate the disputed sessions); POM@2016-03-16: never bought (no Alpaca factor to locate the disputed sessions); RTX: no entries 2016-01-04 to 2020-04-09; the other basis starts with the data; STI@2019-12-01: never bought (no Alpaca factor to locate the disputed sessions); TT: no entries 2016-01-04 to 2020-03-06; the other basis starts with the data.
- `mom_12_1_band`: pre-registration `docs/prereg/mom_12_1_band.md` (sha256 `e29a6efa22da`; config/gate_v2.yaml sha256 `1951c0e41b6d`); exit-before-earnings variant skipped (NOT applied: the next earnings date cannot be established reliably from the history on disk (284 of 489 stocks have detected reports, 58% at a plausible 3-5 a year (median 3.9); flat the night before 95% of 11297 detected reports; 9% of stock-sessions blacked out; the bar is 80% of stocks and 85% of reports), so positions are held through earnings); random control 500 runs per exposure cap, seed 127; at most 1 buys of a run skipped because one share cost more than the position size; code `7c0123e`; report `core_balanced_mom_12_1_band_pit_20261113T084643.json`. SPY total return: table completed: 2 missing quarterly dividends added at the largest nearby amount (1.85 points a year over price only). Disputed splits (share count cannot be carried): APTV: no entries 2017-11-10 to 2017-12-11; a holding is sold at the close of 2017-11-16; DD: no entries 2016-01-04 to 2019-04-08; the other basis starts with the data; DOW@2017-08-24: no entries 2016-01-04 to 2026-06-30; the other basis starts with the data; PARA@2025-07-31: never bought (no Alpaca factor to locate the disputed sessions); POM@2016-03-16: never bought (no Alpaca factor to locate the disputed sessions); RTX: no entries 2016-01-04 to 2020-04-09; the other basis starts with the data; STI@2019-12-01: never bought (no Alpaca factor to locate the disputed sessions); TT: no entries 2016-01-04 to 2020-03-06; the other basis starts with the data.
- `mom_12_1_band_nofilter`: pre-registration `docs/prereg/mom_12_1_band.md` (sha256 `e29a6efa22da`; config/gate_v2.yaml sha256 `1951c0e41b6d`); exit-before-earnings variant skipped (NOT applied: the next earnings date cannot be established reliably from the history on disk (284 of 489 stocks have detected reports, 58% at a plausible 3-5 a year (median 3.9); flat the night before 95% of 11297 detected reports; 9% of stock-sessions blacked out; the bar is 80% of stocks and 85% of reports), so positions are held through earnings); random control 500 runs per exposure cap, seed 127; at most 2 buys of a run skipped because one share cost more than the position size; code `7c0123e`; report `core_balanced_mom_12_1_band_nofilter_pit_20261113T084759.json`. SPY total return: table completed: 2 missing quarterly dividends added at the largest nearby amount (1.85 points a year over price only). Disputed splits (share count cannot be carried): APTV: no entries 2017-11-10 to 2017-12-11; a holding is sold at the close of 2017-11-16; DD: no entries 2016-01-04 to 2019-04-08; the other basis starts with the data; DOW@2017-08-24: no entries 2016-01-04 to 2026-06-30; the other basis starts with the data; PARA@2025-07-31: never bought (no Alpaca factor to locate the disputed sessions); POM@2016-03-16: never bought (no Alpaca factor to locate the disputed sessions); RTX: no entries 2016-01-04 to 2020-04-09; the other basis starts with the data; STI@2019-12-01: never bought (no Alpaca factor to locate the disputed sessions); TT: no entries 2016-01-04 to 2020-03-06; the other basis starts with the data.

## Today's list against the point-in-time universe (base rows)

The same frozen rules and settings; what changes is which stocks could be bought on each day (and the price data). The gap is the survivorship bias of today's list plus the data repairs.

### `trend_momentum`

| | Today's list (minute bars) | Point-in-time S&P 500 (daily store) |
|---|---|---|
| Total return % | 172.79 | 112.31 |
| CAGR % | 11.14 | 8.24 |
| Sharpe | 0.74 | 0.59 |
| Max drawdown % | 19.24 | 18.27 |
| Positive years | 8 of 10 | 7 of 10 |
| Avg exposure % | 86.0 | 83.4 |
| Avg names | 21.8 | 21.4 |
| Trades | 1120 | 1082 |
| Cost bp/yr | 22.5 | 20.1 |
| Top-5 stocks' share of profit % | 23.6 | 33.3 |
| Sharpe pctile vs random | 73.0 (entries_matched, 200 runs) | 78.8 (exposure_matched, 500 runs) |
| SPY total return: CAGR % / max drawdown % | not in the data | 14.37 / 33.78 |
| SPY price only: CAGR % / max drawdown % | 12.6 / 34.23 | 12.6 / 34.18 |
| Equal-weight (same universe): CAGR % / max drawdown % | 13.97 / 38.0 | 10.74 / 39.86 |
| Gate B marks 1-6 | 1: pass, 2: n/j, 3: n/j, 4: n/j, 5: n/j, 6: pass | 1: pass, 2: fail, 3: fail, 4: fail, 5: fail, 6: pass |

Point-in-time minus today: CAGR -2.90 points, Sharpe -0.15, max drawdown -0.97 points.

Gate B mark 5 (the base row still passes marks 1-2 on the point-in-time universe): **fail**. point-in-time base row: mark 1 holds (net return 112.31%, 7 of 10 years positive), mark 2 fails (CAGR 8.24%, max drawdown 18.27%).

### `mom_12_1_band`

| | Today's list (minute bars) | Point-in-time S&P 500 (daily store) |
|---|---|---|
| Total return % | 243.14 | 139.9 |
| CAGR % | 13.85 | 9.64 |
| Sharpe | 0.72 | 0.59 |
| Max drawdown % | 25.8 | 24.96 |
| Positive years | 9 of 10 | 8 of 10 |
| Avg exposure % | 78.2 | 74.2 |
| Avg names | 18.6 | 18.2 |
| Trades | 364 | 370 |
| Cost bp/yr | 6.0 | 5.3 |
| Top-5 stocks' share of profit % | 42.4 | 62.7 |
| Sharpe pctile vs random | 81.5 (entries_matched, 200 runs) | 74.6 (exposure_matched, 500 runs) |
| SPY total return: CAGR % / max drawdown % | not in the data | 14.37 / 33.78 |
| SPY price only: CAGR % / max drawdown % | 12.6 / 34.23 | 12.6 / 34.18 |
| Equal-weight (same universe): CAGR % / max drawdown % | 13.97 / 38.0 | 10.74 / 39.86 |
| Gate B marks 1-6 | 1: pass, 2: n/j, 3: n/j, 4: n/j, 5: n/j, 6: pass | 1: pass, 2: fail, 3: fail, 4: fail, 5: fail, 6: pass |

Point-in-time minus today: CAGR -4.21 points, Sharpe -0.13, max drawdown -0.84 points.

Gate B mark 5 (the base row still passes marks 1-2 on the point-in-time universe): **fail**. point-in-time base row: mark 1 holds (net return 139.9%, 8 of 10 years positive), mark 2 fails (CAGR 9.64%, max drawdown 24.96%).

### `mom_12_1_band_nofilter`

| | Today's list (minute bars) | Point-in-time S&P 500 (daily store) |
|---|---|---|
| Total return % | 256.07 | 158.87 |
| CAGR % | 14.3 | 10.53 |
| Sharpe | 0.71 | 0.58 |
| Max drawdown % | 29.7 | 34.33 |
| Positive years | 9 of 10 | 9 of 10 |
| Avg exposure % | 81.9 | 78.7 |
| Avg names | 20.0 | 19.8 |
| Trades | 389 | 398 |
| Cost bp/yr | 6.2 | 5.5 |
| Top-5 stocks' share of profit % | 42.6 | 56.5 |
| Sharpe pctile vs random | 89.5 (entries_matched, 200 runs) | 73.2 (exposure_matched, 500 runs) |
| SPY total return: CAGR % / max drawdown % | not in the data | 14.37 / 33.78 |
| SPY price only: CAGR % / max drawdown % | 12.6 / 34.23 | 12.6 / 34.18 |
| Equal-weight (same universe): CAGR % / max drawdown % | 13.97 / 38.0 | 10.74 / 39.86 |
| Gate B marks 1-6 | 1: pass, 2: n/j, 3: n/j, 4: n/j, 5: n/j, 6: pass | 1: pass, 2: fail, 3: fail, 4: fail, 5: fail, 6: pass |

Point-in-time minus today: CAGR -3.77 points, Sharpe -0.13, max drawdown +4.63 points.

Gate B mark 5 (the base row still passes marks 1-2 on the point-in-time universe): **fail**. point-in-time base row: mark 1 holds (net return 158.87%, 9 of 10 years positive), mark 2 fails (CAGR 10.53%, max drawdown 34.33%).

`n/j` = cannot be judged yet. Every number here is provisional: every result of this research harness stays provisional (the harness of #113, not the engine with `SimBroker`; cost model v1): it can fail Gate B, it cannot pass it.
