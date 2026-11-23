# SPY trend filter (`spy_trend_filter`), ten-year test (issue #201)

**PROVISIONAL (decision 15): SPY-only research routine on the daily store (raw SPY bars, dividends, SPY total return), cost models v1 and v2; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`).**

Out-of-sample 2017-05-01 to 2026-11-06 (2395 sessions). Each row is ONE continuous $100k account of a frozen, pre-registered configuration, starting in cash; nothing is tuned. Signal: SPY's split-adjusted close; holdings and costs on raw prices with dividends credited and reinvested at the ex-date close; cash earns 0 (the `BIL` row, when present, earns the T-bill ETF's total return and is not judged).

| Rule / version | Costs | Fill | Cash | Total return % | CAGR % | Sharpe | Sharpe x 0.5 | Max drawdown % | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Positive years | Sessions in % | Round trips | Avg holding sessions | Turnover %/yr | Cost bp/yr | Break-even cost x |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| spy_trend_filter | v1 1x | base | 0% | 101.82 | 7.67 | 0.64 | 0.32 | 27.51 | 13.52 | -6.99 | 8.12 | 19.06 | 28.62 | -21.92 | 7.62 | 24.88 | 11.37 | -1.23 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 3.5 | 155.8 |
| spy_trend_filter | v1 2x | base | 0% | 101.16 | 7.63 | 0.64 | 0.32 | 27.57 | 13.5 | -7.02 | 8.05 | 19.02 | 28.62 | -21.97 | 7.56 | 24.88 | 11.34 | -1.26 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 6.7 |  |
| spy_trend_filter | v1 3x | base | 0% | 100.51 | 7.59 | 0.64 | 0.32 | 27.64 | 13.49 | -7.05 | 7.99 | 18.99 | 28.62 | -22.02 | 7.49 | 24.88 | 11.3 | -1.29 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 9.9 |  |
| spy_trend_filter | v1 1x | stress | 0% | 94.14 | 7.23 | 0.61 | 0.31 | 25.77 | 13.48 | -7.97 | 6.08 | 15.58 | 28.61 | -21.84 | 9.94 | 24.89 | 11.58 | -1.54 | 7 of 10 | 82.5 | 11 | 179.6 | 104.3 | 6.7 |  |
| spy_trend_filter ~ neighbour band1 | v1 1x | base | 0% | 182.15 | 11.53 | 0.95 | 0.48 | 15.59 | 13.52 | 3.26 | 16.93 | 19.06 | 28.62 | -9.49 | 8.1 | 24.88 | 11.37 | -1.23 | 8 of 10 | 80.8 | 7 | 276.6 | 68.9 | 2.3 |  |
| spy_trend_filter ~ neighbour sma150d | v1 1x | base | 0% | 147.12 | 9.99 | 0.83 | 0.42 | 23.63 | 13.52 | -0.03 | 16.93 | 19.06 | 28.62 | -17.75 | 7.62 | 24.88 | 11.37 | -1.23 | 7 of 10 | 80.8 | 10 | 193.6 | 97.3 | 3.3 |  |
| spy_trend_filter ~ neighbour sma200d | v1 1x | base | 0% | 123.97 | 8.85 | 0.74 | 0.37 | 27.51 | 13.52 | 3.26 | 8.07 | 19.06 | 28.62 | -21.92 | 7.62 | 24.88 | 11.37 | -1.23 | 8 of 10 | 81.7 | 10 | 195.7 | 96.9 | 3.3 |  |
| spy_trend_filter ~ neighbour sma250d | v1 1x | base | 0% | 145.81 | 9.93 | 0.81 | 0.4 | 22.2 | 13.52 | -6.99 | 8.12 | 19.06 | 28.62 | -17.2 | 12.35 | 24.88 | 11.37 | 8.67 | 8 of 10 | 82.4 | 9 | 219.3 | 77.4 | 2.6 |  |
| spy_trend_filter ~ neighbour weekly | v1 1x | base | 0% | 145.7 | 9.92 | 0.85 | 0.42 | 19.2 | 13.52 | -6.9 | 10.03 | 14.35 | 28.62 | -13.04 | 13.26 | 24.88 | 10.56 | 5.64 | 8 of 10 | 80.5 | 19 | 101.5 | 168.7 | 5.7 |  |
| spy_trend_filter | v2 1x | base | 0% | 101.74 | 7.66 | 0.64 | 0.32 | 27.51 | 13.52 | -6.99 | 8.11 | 19.06 | 28.62 | -21.92 | 7.61 | 24.88 | 11.37 | -1.23 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 3.9 | 137.0 |
| spy_trend_filter | v2 2x | base | 0% | 101.0 | 7.62 | 0.64 | 0.32 | 27.59 | 13.5 | -7.02 | 8.03 | 19.01 | 28.62 | -21.98 | 7.54 | 24.88 | 11.33 | -1.27 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 7.5 |  |
| spy_trend_filter | v2 3x | base | 0% | 100.25 | 7.58 | 0.63 | 0.32 | 27.67 | 13.48 | -7.06 | 7.96 | 18.97 | 28.62 | -22.03 | 7.46 | 24.88 | 11.29 | -1.3 | 7 of 10 | 82.5 | 11 | 179.6 | 104.5 | 11.2 |  |
| spy_trend_filter | v2 1x | stress | 0% | 93.98 | 7.22 | 0.61 | 0.31 | 25.79 | 13.48 | -7.98 | 6.07 | 15.57 | 28.61 | -21.86 | 9.93 | 24.89 | 11.57 | -1.55 | 7 of 10 | 82.5 | 11 | 179.6 | 104.3 | 7.5 |  |
| spy_trend_filter ~ neighbour band1 | v2 1x | base | 0% | 182.09 | 11.53 | 0.95 | 0.48 | 15.6 | 13.52 | 3.26 | 16.93 | 19.06 | 28.62 | -9.49 | 8.09 | 24.88 | 11.37 | -1.23 | 8 of 10 | 80.8 | 7 | 276.6 | 68.9 | 2.6 |  |
| spy_trend_filter ~ neighbour sma150d | v2 1x | base | 0% | 147.04 | 9.98 | 0.83 | 0.42 | 23.64 | 13.52 | -0.04 | 16.93 | 19.06 | 28.62 | -17.75 | 7.61 | 24.88 | 11.37 | -1.23 | 7 of 10 | 80.8 | 10 | 193.6 | 97.3 | 3.6 |  |
| spy_trend_filter ~ neighbour sma200d | v2 1x | base | 0% | 123.9 | 8.85 | 0.74 | 0.37 | 27.51 | 13.52 | 3.26 | 8.07 | 19.06 | 28.62 | -21.92 | 7.61 | 24.88 | 11.37 | -1.23 | 8 of 10 | 81.7 | 10 | 195.7 | 96.9 | 3.6 |  |
| spy_trend_filter ~ neighbour sma250d | v2 1x | base | 0% | 145.74 | 9.92 | 0.81 | 0.4 | 22.21 | 13.52 | -6.99 | 8.11 | 19.06 | 28.62 | -17.2 | 12.35 | 24.88 | 11.37 | 8.67 | 8 of 10 | 82.4 | 9 | 219.3 | 77.4 | 2.9 |  |
| spy_trend_filter ~ neighbour weekly | v2 1x | base | 0% | 145.54 | 9.91 | 0.85 | 0.42 | 19.22 | 13.52 | -6.92 | 10.02 | 14.34 | 28.62 | -13.05 | 13.25 | 24.88 | 10.56 | 5.63 | 8 of 10 | 80.5 | 19 | 101.5 | 168.8 | 6.3 |  |
| BENCHMARK: SPY buy-and-hold (total return, dividends reinvested) |  |  |  | 258.39 | 14.37 | 0.83 | 0.42 | 33.78 | 13.78 | -4.46 | 31.13 | 18.49 | 28.62 | -18.17 | 26.19 | 24.89 | 17.72 | 8.67 | 8 of 10 | 100.0 |  |  |  |  |  |
| BENCHMARK: SPY buy-and-hold (price only, no dividends) |  |  |  | 208.79 | 12.6 | 0.74 | 0.37 | 34.18 | 12.1 | -6.32 | 28.7 | 16.28 | 26.91 | -19.48 | 24.29 | 23.3 | 16.35 | 7.83 | 8 of 10 | 100.0 |  |  |  |  |  |
| BENCHMARK: SPY (total return) at the rule's time in the market (82.5%), rest in cash |  |  |  | 193.24 | 11.99 | 0.83 | 0.42 | 28.55 | 11.26 | -3.49 | 25.2 | 15.9 | 23.23 | -14.89 | 21.31 | 20.26 | 14.71 | 7.23 | 8 of 10 | 82.5 |  |  |  |  |  |
| BENCHMARK: random-timing accounts, median of 500 (same round trips and holding durations) | v1 1x | base | 0% | 193.41 |  | 0.7572 |  |  |  |  |  |  |  |  |  |  |  |  |  |  | 11 |  |  |  |  |
| BENCHMARK: random-timing accounts, median of 500 (same round trips and holding durations) | v2 1x | base | 0% | 193.3 |  | 0.7569 |  |  |  |  |  |  |  |  |  |  |  |  |  |  | 11 |  |  |  |  |

How to read it: fill `base` = decided after the close of the check session, bought or sold at the next session's close; `stress` = the next open with spread and impact doubled (v1) or the opening auction (v2). `2x` / `3x` multiply spread and impact, never fees. Sharpe is annualised from daily mark-to-market returns with no risk-free rate, its 50% haircut beside it. Max drawdown is mark-to-market at the close. Round trips include a position still open at the end. Break-even is the multiple of spread and impact at which net profit reaches zero (0x and 1x runs).

## Gate B marks (decision 15, sections 7 and 10), base configuration

### Cost model v1

- Mark 1 (net return positive overall and in at least 6 of 10 calendar years): **pass**. net return 101.82%, 7 of 10 years positive.
- Mark 2 (CAGR >= SPY total return - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's): **FAIL**. CAGR 7.67% against SPY total return 14.37% (-6.71 points); max drawdown 27.51% = 0.81 x SPY's 33.78% (neither branch holds).
- Mark 3 (net Sharpe at or above the 90th percentile of >= 500 random-timing accounts (section 10)): **FAIL**. net Sharpe 0.641 at percentile 11.6 of 500 random-timing accounts (median 0.7572, 90th 0.9194); matched True (below the required percentile).
- Mark 4 (marks 1-2 still hold at 2x costs): **FAIL**. at 2x: mark 1 True, mark 2 False; CAGR 7.63%, max drawdown 27.58%; break-even 196.3x the daily costs (neither branch holds).
- Mark 5 (holds on the point-in-time universe (SPY only: this run)): **FAIL**. source: this run is on the point-in-time universe (marks 1 to 4 do not all hold on the point-in-time universe).
- Mark 6 (every pre-declared neighbour passes mark 1): **pass**. sma150d pass, sma200d pass, sma250d pass, weekly pass, band1 pass.
- Overall: fails Gate B (mark 2, 3, 4, 5); engine is 'research_harness', a pass needs 'simbroker'; cost_model is 'v1', a pass needs 'v2'.

### Cost model v2

- Mark 1 (net return positive overall and in at least 6 of 10 calendar years): **pass**. net return 101.74%, 7 of 10 years positive.
- Mark 2 (CAGR >= SPY total return - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's): **FAIL**. CAGR 7.66% against SPY total return 14.37% (-6.71 points); max drawdown 27.51% = 0.81 x SPY's 33.78% (neither branch holds).
- Mark 3 (net Sharpe at or above the 90th percentile of >= 500 random-timing accounts (section 10)): **FAIL**. net Sharpe 0.6407 at percentile 11.6 of 500 random-timing accounts (median 0.7569, 90th 0.9191); matched True (below the required percentile).
- Mark 4 (marks 1-2 still hold at 2x costs): **FAIL**. at 2x: mark 1 True, mark 2 False; CAGR 7.62%, max drawdown 27.60%; break-even 177.3x the daily costs (neither branch holds).
- Mark 5 (holds on the point-in-time universe (SPY only: this run)): **FAIL**. source: this run is on the point-in-time universe (marks 1 to 4 do not all hold on the point-in-time universe).
- Mark 6 (every pre-declared neighbour passes mark 1): **pass**. sma150d pass, sma200d pass, sma250d pass, weekly pass, band1 pass.
- Overall: fails Gate B (mark 2, 3, 4, 5); engine is 'research_harness', a pass needs 'simbroker'.

## Notes

- Pre-registration `docs/prereg/spy_trend_filter.md` (sha256 `64fdba635907`); config/gate_v2.yaml sha256 `1951c0e41b6d`; code `4168cb9`; report `spy_trend_20261113T112924.json`.
- SPY total return: table completed: 2 missing quarterly dividends added at the largest nearby amount (1.85 points a year over price only).
- T-bill variant: skipped: BIL is not in the daily store (python scripts/fetch_daily.py --symbols BIL).
- Random-timing control: 500 runs per cost model, seed (201, k); 11 round trips, durations [381, 19, 64, 168, 441, 20, 21, 189, 332, 209, 132] sessions; per-run numbers in control_timing_<model>.csv. Trades of the base account (v1) are in the report JSON (`trips`); the full gate v2 reports in gate_b_<model>.md.
- Trials: the base and each neighbour at cost model v1, base fill, 1x count one configuration each (data/trials.jsonl, `python scripts/trial_registry.py count`); every other row counts 0.
