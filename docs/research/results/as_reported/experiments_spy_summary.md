# SPY noise band, long only, 1x, and the overnight carry: ten-year test (issue #128)

**PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker.**

Out-of-sample 2017-05-01 to 2026-11-06. Each row is ONE continuous $100k account of one frozen, pre-registered configuration (`docs/prereg/spy_noise_band.md`); nothing is tuned.

| Rule / version | Costs a side | Round trips | Net bp / trip | t | Gross bp / trip | Hit rate | Total return % | CAGR % | Sharpe | Sharpe x 0.5 | Max drawdown % | Days invested % | Positive years | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Jun 2022-Jul 2023 % | Break-even bp a side (per trip / account) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spy_noise_band | 0 (gross) | 1119 | 3.48 | 2.97 | 3.48 | 0.445 | 46.36 | 4.09 | 1.0 | 0.5 | 4.45 | 33.0 | 9 of 10 | 1.11 | 5.82 | 2.21 | 0.51 | 10.4 | 9.96 | 7.48 | 2.36 | 1.35 | -1.63 | 16.77 | 1.74 / 1.7 |
| spy_noise_band | 0.5 bp | 1119 | 2.48 | 2.12 | 3.48 | 0.424 | 30.87 | 2.87 | 0.71 | 0.35 | 5.35 | 33.0 | 8 of 10 | 0.3 | 4.57 | 1.17 | -0.54 | 9.09 | 8.7 | 6.09 | 1.1 | 0.21 | -2.71 | 15.13 |  |
| spy_noise_band | 1 bp | 1119 | 1.48 | 1.26 | 3.48 | 0.408 | 17.01 | 1.67 | 0.43 | 0.21 | 7.98 | 33.0 | 5 of 10 | -0.5 | 3.34 | 0.15 | -1.58 | 7.8 | 7.44 | 4.72 | -0.15 | -0.91 | -3.79 | 13.5 |  |
| spy_noise_band | 2 bp | 1119 | -0.52 | -0.44 | 3.48 | 0.374 | -6.45 | -0.7 | -0.15 | -0.07 | 13.52 | 33.0 | 4 of 10 | -2.08 | 0.91 | -1.88 | -3.62 | 5.26 | 4.98 | 2.04 | -2.6 | -3.13 | -5.9 | 10.33 |  |
| spy_noise_band | model v1 | 1119 | 0.06 | 0.05 | 3.48 | 0.38 | -0.19 | -0.02 | 0.02 | 0.01 | 11.62 | 33.0 | 4 of 10 | -1.58 | 1.58 | -1.29 | -3.12 | 6.02 | 5.63 | 2.84 | -1.87 | -2.5 | -5.25 | 11.21 |  |
| spy_noise_band ~ neighbour lb10 | 0.5 bp | 1134 | 2.37 | 2.08 | 3.37 | 0.411 | 29.79 | 2.78 | 0.7 | 0.35 | 6.44 | 33.1 | 8 of 10 | 1.23 | 3.66 | 1.55 | -1.42 | 8.97 | 8.91 | 5.74 | 1.03 | 0.14 | -2.67 | 14.47 |  |
| spy_noise_band ~ neighbour lb20 | 0.5 bp | 1091 | 2.52 | 2.09 | 3.52 | 0.415 | 30.44 | 2.84 | 0.72 | 0.36 | 4.62 | 31.6 | 8 of 10 | 0.56 | 3.21 | 2.26 | -0.07 | 6.87 | 10.17 | 4.05 | 2.34 | 0.49 | -2.38 | 12.71 |  |
| spy_noise_band ~ neighbour lb30 | 0.5 bp | 1061 | 3.17 | 2.22 | 4.17 | 0.412 | 38.39 | 3.48 | 0.74 | 0.37 | 4.91 | 31.1 | 9 of 10 | 1.09 | 3.88 | 3.08 | 2.05 | 6.77 | 8.31 | 2.12 | 1.13 | 6.57 | -1.56 | 12.05 |  |
| spy_noise_band ~ neighbour check15 | 0.5 bp | 1497 | 1.75 | 1.93 | 2.75 | 0.369 | 28.7 | 2.69 | 0.64 | 0.32 | 7.34 | 35.5 | 6 of 10 | -0.52 | 2.86 | 0.25 | -4.4 | 9.21 | 11.61 | 8.04 | 3.22 | -2.09 | -1.39 | 17.12 |  |
| spy_noise_band ~ neighbour exit1555 | 0.5 bp | 1119 | 2.88 | 2.46 | 3.88 | 0.425 | 36.79 | 3.35 | 0.82 | 0.41 | 4.1 | 33.0 | 9 of 10 | 0.26 | 3.56 | 0.55 | 1.91 | 11.27 | 8.76 | 5.76 | 1.19 | 1.41 | -2.12 | 15.67 |  |
| context: spy_noise_band round trips entered at the 15:30 check | 0.5 bp | 54 | 3.3 | 0.69 | 4.3 | 0.519 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| context: spy_noise_band round trips entered at the 15:30 check | 1 bp | 54 | 2.3 | 0.48 | 4.3 | 0.481 |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| context: live last-30-minutes SPY trade (S13 rules, gate sizing) | model v1 | 776 | -4.94 | -5.42 |  | 0.38 | -5.52 | -0.6 | -1.73 | -0.86 | 5.52 | 32.4 | 0 of 10 | -0.3 | -0.87 | -0.25 | -0.7 | -1.23 | -0.38 | -0.38 | -0.52 | -0.67 | -0.35 |  |  |
| spy_noise_band_carry | 0 (gross) | 1119 | 4.95 | 3.44 | 4.95 | 0.426 | 71.75 | 5.86 | 1.05 | 0.53 | 9.57 | 43.9 | 10 of 10 | 2.3 | 8.98 | 0.14 | 3.99 | 13.69 | 4.3 | 10.27 | 9.03 | 2.68 | 1.06 | 13.2 | 2.47 / 2.42 |
| spy_noise_band_carry | 0.5 bp | 1119 | 3.95 | 2.75 | 4.95 | 0.406 | 53.57 | 4.62 | 0.84 | 0.42 | 9.85 | 43.9 | 8 of 10 | 1.49 | 7.69 | -0.87 | 2.9 | 12.35 | 3.1 | 8.85 | 7.68 | 1.52 | -0.05 | 11.61 |  |
| spy_noise_band_carry | 1 bp | 1119 | 2.95 | 2.05 | 4.95 | 0.391 | 37.31 | 3.39 | 0.63 | 0.32 | 10.12 | 43.9 | 8 of 10 | 0.68 | 6.42 | -1.88 | 1.83 | 11.02 | 1.91 | 7.44 | 6.36 | 0.38 | -1.15 | 10.03 |  |
| spy_noise_band_carry | 2 bp | 1119 | 0.95 | 0.66 | 4.95 | 0.362 | 9.78 | 0.99 | 0.2 | 0.1 | 12.12 | 43.9 | 4 of 10 | -0.92 | 3.91 | -3.86 | -0.29 | 8.41 | -0.43 | 4.68 | 3.75 | -1.86 | -3.32 | 6.95 |  |
| spy_noise_band_carry | model v1 | 1119 | 1.52 | 1.06 | 4.95 | 0.37 | 17.03 | 1.67 | 0.32 | 0.16 | 11.51 | 43.9 | 6 of 10 | -0.42 | 4.6 | -3.29 | 0.22 | 9.18 | 0.17 | 5.5 | 4.52 | -1.24 | -2.67 | 7.79 |  |
| BENCHMARK: SPY buy-and-hold (price only, no dividends) | none |  |  |  |  |  | 208.84 | 12.6 | 0.73 | 0.36 | 34.23 | 100.0 | 8 of 10 | 12.09 | -6.28 | 28.69 | 16.14 | 27.02 | -19.47 | 24.29 | 23.28 | 16.35 | 7.85 | 10.86 |  |

How to read it: costs are all-in per side on every buy and sell (0.5 / 1 / 2 bp), or cost model v1 (half spread of at least 1 bp plus impact, plus fees). Net and gross bp are per round trip (a carry counts once), t is the mean over its standard error. Returns, Sharpe (annualised, no risk-free rate, 50% haircut beside it) and drawdown are daily mark-to-market. Break-even: the cost a side at which the mean round trip (half the gross mean) and the account's total return reach zero. Days invested: sessions with any position. Context rows are not configurations.

## Gate B mark 3: random-timing control (decision 15 section 10, issue #149)

- `spy_noise_band`: **pass**. Net Sharpe 0.71 at the 100.0th percentile of 500 random-timing runs (random median -0.15, 90th 0.22); total return 30.87% at the 99.6th percentile (random median -8.0%). 1119 round trips, median hold 60 min, seed 149.
- `spy_noise_band_carry`: **pass**. Net Sharpe 0.84 at the 99.4th percentile of 500 random-timing runs (random median 0.09, 90th 0.43); total return 53.57% at the 98.4th percentile (random median 3.66%). 1119 round trips (397 carried overnight), median hold 60 min, seed 149.

Each random account makes the rule's number of SPY round trips with the rule's own holding durations (in a random order), entered at random sessions and check times (:00/:30 from 10:00), one at a time and fully invested when long, at the same 0.5 bp a side. Percentile: share of random accounts strictly below the rule (ties count against it). The mark needs the net Sharpe at or above the 90th percentile over at least 500 runs. PROVISIONAL like every verdict here.

## Pass marks (issue #128, pre-registered)

### `spy_noise_band` (0.5 bp a side unless stated)

- Mark 1 (at least +1.5 bp net per round trip with t >= 2 (0.5 bp a side)): **pass**. 2.48 bp, t 2.12, 1119 round trips.
- Mark 2 (total net return positive at 1 bp a side): **pass**. 17.01%.
- Mark 3 (at least 7 of the 10 calendar years 2017-2026 positive): **pass**. 8 of 10.
- Mark 4 (maximum drawdown under 15%): **pass**. 5.35%.
- Mark 5 (lb10, lb20, lb30 each >= +0.75 bp net per round trip and a positive total return): **pass**. lb10 2.37 bp, 29.79%; lb20 2.52 bp, 30.44%; lb30 3.17 bp, 38.39%.
- Overall: PROVISIONAL; a pass here only earns the engine run with `SimBroker`.

### `spy_noise_band_carry` (0.5 bp a side unless stated)

- Mark 6 (carry nights beat other nights with t >= 2): **fail**. 4.19 bp vs 3.78 bp a night, Welch t 0.12 (397 carry nights).
- Mark 7 (carry nights positive from June 2022 to July 2023): **fail**. -3.06% over 55 carry nights.
- Overall: PROVISIONAL; a pass here only earns the engine run with `SimBroker`.

## Notes

- **Live last-30-minutes SPY trade: ABSORB** (the noise-band rule passes marks 1-5; its 15:30 check covers the live trade; prereg section 6, PROVISIONAL; a recommendation to Product, nothing live changes here).
- `spy_noise_band`: pre-registration `docs/prereg/spy_noise_band.md` (sha256 `054b6baf502b`); code `7c0123e`; report `spy_balanced_spy_noise_band_20261113T084923.json`; runtime 5.6s.
- Carry nights (long at the close, SPY close to next open, gross): 397 nights, mean 4.19 bp (t 1.47); other nights 1997, mean 3.78 bp (t 2.16); difference 0.41 bp, Welch t 0.12. June 2022-July 2023: -3.06% over 55 carry nights. The carry adds 1.75 CAGR points at 0.5 bp a side (expected about +2).
- `spy_noise_band_carry`: pre-registration `docs/prereg/spy_noise_band.md` (sha256 `054b6baf502b`); code `7c0123e`; report `spy_balanced_spy_noise_band_carry_20261113T084929.json`; runtime 5.4s.
