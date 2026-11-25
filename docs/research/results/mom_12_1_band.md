# mom_12_1_band: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/mom_12_1_band.md`. Family in gate v3: cross_sectional_momentum.

- Trials logged: 38 configurations for this rule, 760 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_core_pit_summary.md`](as_reported/experiments_core_pit_summary.md), [`experiments_core_summary.md`](as_reported/experiments_core_summary.md).

## core, point_in_time, costs v1

Report `core_balanced_mom_12_1_band_pit_20261113T084643.json`, run 2026-11-13T08:46:43+00:00 at code `7c0123e`. Account `mom_12_1_band|base|1x|cap100|hold`. Engine: research_harness. Universe: point_in_time. Cost model: v1. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on the point-in-time S&P 500 (#123) and the daily store (#124: raw prices, dividends, SPY total return), cost model v1; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`, and cost model v2 is S71)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 9.64% | 24.96% | 0.59 | 74% | 14.37% / 33.78% | 10.29% / 25.69% | 0.37% (0.11) | -0.79% (-0.21) | 0.78 | 0.05% |
| Research window: 2017-05-01 to 2024-12-31 | 10.25% | 24.96% | 0.64 | 75% | 14.39% / 33.78% | 10.41% / 25.69% | 0.63% (0.17) | 0.16% (0.04) | 0.74 | 0.05% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 25% of the account was idle. At 0%: CAGR 10.25%, Sharpe 0.64, alpha 0.16% (t 0.04). In bills: CAGR 10.80%, Sharpe above bills 0.54, alpha 0.06% (t 0.01). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 74.6 of 500 runs, p 0.2555; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| only12 | score from the 12-1 return only (t-12..t-2) | 8.74% | -1.51 points | 42.53% | 0.52 | 76% | -0.25% (-0.06) | -2.66% (-0.62) | 0.86 |
| only6 | score from the 6-1 return only (t-6..t-2) | 4.42% | -5.83 points | 37.60% | 0.34 | 73% | -4.05% (-1.14) | -4.49% (-1.15) | 0.68 |
| exit30 | exit when out of the top 30% | 11.48% | +1.22 points | 30.32% | 0.67 | 77% | 1.37% (0.34) | 0.61% (0.14) | 0.80 |
| names10 | at most 10 names | 8.56% | -1.69 points | 15.88% | 0.81 | 38% | 3.41% (1.36) | 2.45% (0.90) | 0.42 |
| risk15 | ATR risk 0.0015 of equity per position | 13.39% | +3.13 points | 29.71% | 0.68 | 87% | 2.37% (0.49) | 1.40% (0.26) | 0.90 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| mom_12_1_band|base|0x|cap30|hold | cost | 5.63% | 14.48% | 0.62 | 28.5% |
| mom_12_1_band|base|1x|cap30|hold | exposure | 6.37% | 14.49% | 0.67 | 28.7% |
| mom_12_1_band|base|2x|cap30|hold | cost | 6.37% | 14.52% | 0.67 | 28.7% |
| mom_12_1_band|base|3x|cap30|hold | cost | 6.31% | 14.56% | 0.67 | 28.6% |
| mom_12_1_band|base|0x|cap60|hold | cost | 8.19% | 19.73% | 0.60 | 54.7% |
| mom_12_1_band|base|1x|cap60|hold | exposure | 8.13% | 19.77% | 0.59 | 54.7% |
| mom_12_1_band|base|2x|cap60|hold | cost | 8.10% | 19.88% | 0.59 | 54.7% |
| mom_12_1_band|base|3x|cap60|hold | cost | 7.96% | 19.95% | 0.58 | 54.8% |
| mom_12_1_band|base|0x|cap100|hold | cost | 9.78% | 24.89% | 0.59 | 74.3% |
| mom_12_1_band|base|2x|cap100|hold | cost | 9.49% | 25.11% | 0.58 | 74.3% |
| mom_12_1_band|base|3x|cap100|hold | cost | 9.58% | 25.17% | 0.59 | 74.2% |
| mom_12_1_band|stress|0x|cap30|hold | cost | 5.75% | 14.75% | 0.63 | 28.4% |
| mom_12_1_band|stress|1x|cap30|hold | fill | 5.66% | 14.82% | 0.63 | 28.5% |
| mom_12_1_band|stress|2x|cap30|hold | cost | 5.68% | 14.88% | 0.63 | 28.5% |
| mom_12_1_band|stress|3x|cap30|hold | cost | 5.68% | 14.93% | 0.63 | 28.5% |
| mom_12_1_band|stress|0x|cap60|hold | cost | 8.17% | 21.42% | 0.60 | 54.6% |
| mom_12_1_band|stress|1x|cap60|hold | fill | 8.13% | 21.63% | 0.60 | 54.6% |
| mom_12_1_band|stress|2x|cap60|hold | cost | 8.07% | 21.17% | 0.59 | 54.7% |
| mom_12_1_band|stress|3x|cap60|hold | cost | 7.99% | 21.21% | 0.59 | 54.6% |
| mom_12_1_band|stress|0x|cap100|hold | cost | 9.97% | 26.64% | 0.60 | 74.4% |
| mom_12_1_band|stress|1x|cap100|hold | fill | 9.58% | 26.78% | 0.59 | 74.2% |
| mom_12_1_band|stress|2x|cap100|hold | cost | 9.62% | 26.90% | 0.59 | 74.2% |
| mom_12_1_band|stress|3x|cap100|hold | cost | 9.46% | 27.16% | 0.58 | 74.2% |

## core, today

Report `core_balanced_mom_12_1_band_20261112T160713.json`, run 2026-11-12T16:07:13+00:00 at code `4dce03a`. Account `mom_12_1_band|base|1x|cap100|hold`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on today's stock list, cost model v1, price-only SPY; cannot pass Gate B until S71, S83 and S84 have landed and the test is rerun

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 13.85% | 25.80% | 0.72 | 78% | 14.37% / 33.78% | 11.03% / 25.13% | 3.95% (0.98) | 1.91% (0.45) | 0.88 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 15.16% | 25.80% | 0.82 | 78% | 14.39% / 33.78% | 11.10% / 25.13% | 4.69% (1.16) | 3.55% (0.82) | 0.83 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 22% of the account was idle. At 0%: CAGR 15.16%, Sharpe 0.82, alpha 3.55% (t 0.82). In bills: CAGR 15.67%, Sharpe above bills 0.72, alpha 3.59% (t 0.83). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 81.5 of 200 runs (NOT matched), p 0.1891; kind not stated; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: cannot be judged yet (provisional data); 3: cannot be judged yet (provisional data); 4: cannot be judged yet (provisional data); 5: cannot be judged yet (provisional data); 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| only12 | score from the 12-1 return only (t-12..t-2) | 15.19% | +0.03 points | 28.95% | 0.78 | 79% | 5.41% (1.30) | 2.68% (0.61) | 0.90 |
| only6 | score from the 6-1 return only (t-6..t-2) | 17.62% | +2.46 points | 26.63% | 0.91 | 79% | 7.12% (1.58) | 6.18% (1.26) | 0.80 |
| exit30 | exit when out of the top 30% | 16.35% | +1.19 points | 26.80% | 0.84 | 79% | 5.87% (1.40) | 4.10% (0.92) | 0.87 |
| names10 | at most 10 names | 10.10% | -5.06 points | 13.92% | 0.88 | 39% | 5.04% (1.90) | 3.38% (1.21) | 0.46 |
| risk15 | ATR risk 0.0015 of equity per position | 18.67% | +3.51 points | 27.52% | 0.83 | 91% | 6.90% (1.32) | 5.04% (0.91) | 0.99 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| mom_12_1_band|base|0x|cap30|hold | cost | 7.08% | 14.85% | 0.72 | 29.0% |
| mom_12_1_band|base|1x|cap30|hold | exposure | 7.03% | 15.02% | 0.72 | 29.0% |
| mom_12_1_band|base|2x|cap30|hold | cost | 7.50% | 13.16% | 0.76 | 29.0% |
| mom_12_1_band|base|3x|cap30|hold | cost | 7.50% | 13.13% | 0.76 | 29.0% |
| mom_12_1_band|base|0x|cap60|hold | cost | 10.53% | 19.65% | 0.70 | 55.8% |
| mom_12_1_band|base|1x|cap60|hold | exposure | 10.34% | 20.00% | 0.69 | 55.7% |
| mom_12_1_band|base|2x|cap60|hold | cost | 10.45% | 19.59% | 0.69 | 55.8% |
| mom_12_1_band|base|3x|cap60|hold | cost | 10.41% | 19.58% | 0.69 | 55.8% |
| mom_12_1_band|base|0x|cap100|hold | cost | 13.92% | 25.80% | 0.72 | 78.2% |
| mom_12_1_band|base|2x|cap100|hold | cost | 13.80% | 25.86% | 0.72 | 78.3% |
| mom_12_1_band|base|3x|cap100|hold | cost | 13.46% | 25.86% | 0.70 | 78.2% |
| mom_12_1_band|stress|0x|cap30|hold | cost | 7.75% | 13.11% | 0.78 | 29.0% |
| mom_12_1_band|stress|1x|cap30|hold | fill | 7.65% | 13.11% | 0.78 | 29.0% |
| mom_12_1_band|stress|2x|cap30|hold | cost | 7.64% | 13.13% | 0.77 | 29.0% |
| mom_12_1_band|stress|3x|cap30|hold | cost | 6.63% | 17.38% | 0.68 | 29.0% |
| mom_12_1_band|stress|0x|cap60|hold | cost | 10.65% | 19.19% | 0.70 | 55.7% |
| mom_12_1_band|stress|1x|cap60|hold | fill | 10.54% | 19.34% | 0.70 | 55.7% |
| mom_12_1_band|stress|2x|cap60|hold | cost | 10.45% | 19.40% | 0.69 | 55.8% |
| mom_12_1_band|stress|3x|cap60|hold | cost | 10.32% | 19.34% | 0.68 | 55.7% |
| mom_12_1_band|stress|0x|cap100|hold | cost | 14.17% | 26.70% | 0.73 | 78.2% |
| mom_12_1_band|stress|1x|cap100|hold | fill | 14.03% | 26.62% | 0.73 | 78.1% |
| mom_12_1_band|stress|2x|cap100|hold | cost | 13.92% | 26.69% | 0.72 | 78.2% |
| mom_12_1_band|stress|3x|cap100|hold | cost | 13.79% | 26.72% | 0.72 | 78.3% |
| mom_12_1_band|base|0x|cap30|exit_approx | cost | 3.74% | 11.81% | 0.65 | 17.5% |
| mom_12_1_band|base|1x|cap30|exit_approx | exposure | 3.71% | 11.91% | 0.64 | 17.5% |
| mom_12_1_band|base|2x|cap30|exit_approx | cost | 3.64% | 12.01% | 0.63 | 17.5% |
| mom_12_1_band|base|3x|cap30|exit_approx | cost | 3.59% | 12.04% | 0.63 | 17.5% |
| mom_12_1_band|base|0x|cap60|exit_approx | cost | 5.16% | 15.98% | 0.59 | 33.6% |
| mom_12_1_band|base|1x|cap60|exit_approx | exposure | 5.07% | 16.17% | 0.58 | 33.6% |
| mom_12_1_band|base|2x|cap60|exit_approx | cost | 4.92% | 16.31% | 0.56 | 33.5% |
| mom_12_1_band|base|3x|cap60|exit_approx | cost | 4.83% | 16.43% | 0.55 | 33.5% |
| mom_12_1_band|base|0x|cap100|exit_approx | cost | 6.81% | 16.04% | 0.68 | 41.9% |
| mom_12_1_band|base|1x|cap100|exit_approx | variant | 6.68% | 16.18% | 0.67 | 41.9% |
| mom_12_1_band|base|2x|cap100|exit_approx | cost | 6.56% | 16.18% | 0.66 | 41.9% |
| mom_12_1_band|base|3x|cap100|exit_approx | cost | 6.41% | 16.37% | 0.65 | 41.9% |
| mom_12_1_band|stress|0x|cap30|exit_approx | cost | 3.79% | 12.37% | 0.65 | 17.5% |
| mom_12_1_band|stress|1x|cap30|exit_approx | fill | 3.65% | 12.64% | 0.63 | 17.5% |
| mom_12_1_band|stress|2x|cap30|exit_approx | cost | 3.54% | 12.74% | 0.61 | 17.5% |
| mom_12_1_band|stress|3x|cap30|exit_approx | cost | 3.41% | 12.86% | 0.59 | 17.5% |
| mom_12_1_band|stress|0x|cap60|exit_approx | cost | 5.02% | 17.37% | 0.57 | 33.4% |
| mom_12_1_band|stress|1x|cap60|exit_approx | fill | 4.86% | 17.77% | 0.55 | 33.4% |
| mom_12_1_band|stress|2x|cap60|exit_approx | cost | 4.63% | 17.98% | 0.53 | 33.4% |
| mom_12_1_band|stress|3x|cap60|exit_approx | cost | 4.48% | 18.24% | 0.51 | 33.4% |
| mom_12_1_band|stress|0x|cap100|exit_approx | cost | 6.67% | 17.69% | 0.67 | 41.7% |
| mom_12_1_band|stress|1x|cap100|exit_approx | fill | 6.40% | 17.94% | 0.64 | 41.7% |
| mom_12_1_band|stress|2x|cap100|exit_approx | cost | 6.15% | 18.29% | 0.62 | 41.7% |
| mom_12_1_band|stress|3x|cap100|exit_approx | cost | 5.90% | 18.55% | 0.60 | 41.7% |

## core, point_in_time, costs v1 (version mom_12_1_band_nofilter)

Report `core_balanced_mom_12_1_band_nofilter_pit_20261113T084759.json`, run 2026-11-13T08:47:59+00:00 at code `7c0123e`. Account `mom_12_1_band_nofilter|base|1x|cap100|hold`. Engine: research_harness. Universe: point_in_time. Cost model: v1. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on the point-in-time S&P 500 (#123) and the daily store (#124: raw prices, dividends, SPY total return), cost model v1; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`, and cost model v2 is S71)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 10.53% | 34.33% | 0.58 | 79% | 14.37% / 33.78% | 11.28% / 30.96% | 0.48% (0.13) | -1.65% (-0.43) | 0.92 | 0.06% |
| Research window: 2017-05-01 to 2024-12-31 | 10.96% | 34.33% | 0.62 | 80% | 14.39% / 33.78% | 11.37% / 30.96% | 0.58% (0.15) | -1.02% (-0.25) | 0.89 | 0.06% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 20% of the account was idle. At 0%: CAGR 10.96%, Sharpe 0.62, alpha -1.02% (t -0.25). In bills: CAGR 11.40%, Sharpe above bills 0.52, alpha -0.88% (t -0.22). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 73.2 of 500 runs, p 0.2695; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| only12 | score from the 12-1 return only (t-12..t-2) | 11.01% | +0.06 points | 38.57% | 0.62 | 79% | 0.84% (0.21) | -1.13% (-0.27) | 0.90 |
| only6 | score from the 6-1 return only (t-6..t-2) | 6.64% | -4.31 points | 38.70% | 0.44 | 80% | -3.41% (-0.91) | -3.90% (-1.01) | 0.79 |
| exit30 | exit when out of the top 30% | 12.51% | +1.55 points | 35.49% | 0.67 | 81% | 1.72% (0.41) | 0.24% (0.06) | 0.90 |
| names10 | at most 10 names | 8.42% | -2.53 points | 17.95% | 0.76 | 40% | 3.01% (1.15) | 1.74% (0.64) | 0.46 |
| risk15 | ATR risk 0.0015 of equity per position | 13.92% | +2.96 points | 39.64% | 0.66 | 94% | 1.69% (0.34) | 0.01% (0.00) | 1.05 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| mom_12_1_band_nofilter|base|0x|cap30|hold | cost | 6.44% | 13.84% | 0.69 | 30.4% |
| mom_12_1_band_nofilter|base|1x|cap30|hold | exposure | 6.21% | 13.92% | 0.66 | 30.5% |
| mom_12_1_band_nofilter|base|2x|cap30|hold | cost | 6.25% | 13.96% | 0.66 | 30.5% |
| mom_12_1_band_nofilter|base|3x|cap30|hold | cost | 6.32% | 13.95% | 0.66 | 30.5% |
| mom_12_1_band_nofilter|base|0x|cap60|hold | cost | 8.90% | 22.43% | 0.61 | 58.9% |
| mom_12_1_band_nofilter|base|1x|cap60|hold | exposure | 8.85% | 22.47% | 0.61 | 58.8% |
| mom_12_1_band_nofilter|base|2x|cap60|hold | cost | 8.83% | 22.58% | 0.61 | 58.8% |
| mom_12_1_band_nofilter|base|3x|cap60|hold | cost | 9.00% | 22.79% | 0.61 | 58.7% |
| mom_12_1_band_nofilter|base|0x|cap100|hold | cost | 10.54% | 34.33% | 0.58 | 78.8% |
| mom_12_1_band_nofilter|base|2x|cap100|hold | cost | 10.50% | 34.38% | 0.58 | 78.7% |
| mom_12_1_band_nofilter|base|3x|cap100|hold | cost | 10.47% | 34.43% | 0.58 | 78.7% |
| mom_12_1_band_nofilter|stress|0x|cap30|hold | cost | 6.54% | 14.41% | 0.68 | 30.5% |
| mom_12_1_band_nofilter|stress|1x|cap30|hold | fill | 6.45% | 14.43% | 0.69 | 30.4% |
| mom_12_1_band_nofilter|stress|2x|cap30|hold | cost | 6.32% | 14.54% | 0.66 | 30.6% |
| mom_12_1_band_nofilter|stress|3x|cap30|hold | cost | 6.24% | 14.60% | 0.66 | 30.5% |
| mom_12_1_band_nofilter|stress|0x|cap60|hold | cost | 8.99% | 23.93% | 0.62 | 58.9% |
| mom_12_1_band_nofilter|stress|1x|cap60|hold | fill | 9.28% | 25.47% | 0.62 | 59.0% |
| mom_12_1_band_nofilter|stress|2x|cap60|hold | cost | 9.26% | 25.76% | 0.62 | 59.0% |
| mom_12_1_band_nofilter|stress|3x|cap60|hold | cost | 9.17% | 25.72% | 0.62 | 58.9% |
| mom_12_1_band_nofilter|stress|0x|cap100|hold | cost | 11.03% | 35.60% | 0.61 | 78.7% |
| mom_12_1_band_nofilter|stress|1x|cap100|hold | fill | 10.91% | 35.65% | 0.60 | 78.8% |
| mom_12_1_band_nofilter|stress|2x|cap100|hold | cost | 10.79% | 35.75% | 0.60 | 78.8% |
| mom_12_1_band_nofilter|stress|3x|cap100|hold | cost | 10.58% | 35.94% | 0.59 | 78.7% |

## core, today (version mom_12_1_band_nofilter)

Report `core_balanced_mom_12_1_band_nofilter_20261112T160737.json`, run 2026-11-12T16:07:37+00:00 at code `4dce03a`. Account `mom_12_1_band_nofilter|base|1x|cap100|hold`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on today's stock list, cost model v1, price-only SPY; cannot pass Gate B until S71, S83 and S84 have landed and the test is rerun

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 14.30% | 29.70% | 0.71 | 82% | 14.37% / 33.78% | 11.69% / 28.83% | 3.75% (0.89) | 1.36% (0.32) | 0.96 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 15.80% | 29.70% | 0.80 | 83% | 14.39% / 33.78% | 11.81% / 28.83% | 4.65% (1.07) | 2.98% (0.67) | 0.92 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 17% of the account was idle. At 0%: CAGR 15.80%, Sharpe 0.80, alpha 2.98% (t 0.67). In bills: CAGR 16.17%, Sharpe above bills 0.71, alpha 3.12% (t 0.71). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 89.5 of 200 runs (NOT matched), p 0.1095; kind not stated; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: cannot be judged yet (provisional data); 3: cannot be judged yet (provisional data); 4: cannot be judged yet (provisional data); 5: cannot be judged yet (provisional data); 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| only12 | score from the 12-1 return only (t-12..t-2) | 16.50% | +0.69 points | 30.00% | 0.83 | 81% | 6.09% (1.47) | 3.48% (0.81) | 0.93 |
| only6 | score from the 6-1 return only (t-6..t-2) | 19.40% | +3.60 points | 30.25% | 0.94 | 84% | 7.72% (1.64) | 6.27% (1.30) | 0.91 |
| exit30 | exit when out of the top 30% | 18.35% | +2.54 points | 30.54% | 0.88 | 83% | 6.84% (1.56) | 4.75% (1.05) | 0.96 |
| names10 | at most 10 names | 10.37% | -5.44 points | 13.54% | 0.89 | 41% | 4.92% (1.78) | 3.51% (1.23) | 0.47 |
| risk15 | ATR risk 0.0015 of equity per position | 20.56% | +4.76 points | 35.09% | 0.84 | 98% | 7.56% (1.35) | 4.84% (0.86) | 1.15 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| mom_12_1_band_nofilter|base|0x|cap30|hold | cost | 8.14% | 12.18% | 0.79 | 31.1% |
| mom_12_1_band_nofilter|base|1x|cap30|hold | exposure | 8.03% | 12.18% | 0.78 | 31.1% |
| mom_12_1_band_nofilter|base|2x|cap30|hold | cost | 8.08% | 12.22% | 0.78 | 31.1% |
| mom_12_1_band_nofilter|base|3x|cap30|hold | cost | 8.03% | 12.19% | 0.78 | 31.1% |
| mom_12_1_band_nofilter|base|0x|cap60|hold | cost | 11.52% | 23.22% | 0.72 | 59.2% |
| mom_12_1_band_nofilter|base|1x|cap60|hold | exposure | 11.53% | 23.25% | 0.72 | 59.1% |
| mom_12_1_band_nofilter|base|2x|cap60|hold | cost | 11.41% | 23.26% | 0.71 | 59.2% |
| mom_12_1_band_nofilter|base|3x|cap60|hold | cost | 11.35% | 23.29% | 0.71 | 59.2% |
| mom_12_1_band_nofilter|base|0x|cap100|hold | cost | 14.36% | 29.64% | 0.72 | 81.8% |
| mom_12_1_band_nofilter|base|2x|cap100|hold | cost | 14.22% | 29.71% | 0.71 | 81.9% |
| mom_12_1_band_nofilter|base|3x|cap100|hold | cost | 14.17% | 29.71% | 0.71 | 81.8% |
| mom_12_1_band_nofilter|stress|0x|cap30|hold | cost | 8.32% | 12.37% | 0.80 | 31.2% |
| mom_12_1_band_nofilter|stress|1x|cap30|hold | fill | 8.17% | 11.65% | 0.79 | 31.1% |
| mom_12_1_band_nofilter|stress|2x|cap30|hold | cost | 8.20% | 11.62% | 0.79 | 31.1% |
| mom_12_1_band_nofilter|stress|3x|cap30|hold | cost | 7.86% | 12.28% | 0.77 | 31.1% |
| mom_12_1_band_nofilter|stress|0x|cap60|hold | cost | 11.65% | 23.60% | 0.72 | 59.2% |
| mom_12_1_band_nofilter|stress|1x|cap60|hold | fill | 11.67% | 23.63% | 0.73 | 59.1% |
| mom_12_1_band_nofilter|stress|2x|cap60|hold | cost | 11.61% | 23.62% | 0.73 | 59.2% |
| mom_12_1_band_nofilter|stress|3x|cap60|hold | cost | 11.51% | 23.66% | 0.72 | 59.2% |
| mom_12_1_band_nofilter|stress|0x|cap100|hold | cost | 14.67% | 30.18% | 0.73 | 81.7% |
| mom_12_1_band_nofilter|stress|1x|cap100|hold | fill | 14.52% | 30.17% | 0.72 | 81.7% |
| mom_12_1_band_nofilter|stress|2x|cap100|hold | cost | 14.38% | 30.23% | 0.72 | 81.8% |
| mom_12_1_band_nofilter|stress|3x|cap100|hold | cost | 14.26% | 30.25% | 0.71 | 81.9% |
| mom_12_1_band_nofilter|base|0x|cap30|exit_approx | cost | 4.11% | 11.40% | 0.62 | 20.2% |
| mom_12_1_band_nofilter|base|1x|cap30|exit_approx | exposure | 4.01% | 11.51% | 0.61 | 20.2% |
| mom_12_1_band_nofilter|base|2x|cap30|exit_approx | cost | 3.93% | 11.64% | 0.60 | 20.2% |
| mom_12_1_band_nofilter|base|3x|cap30|exit_approx | cost | 3.89% | 11.83% | 0.59 | 20.2% |
| mom_12_1_band_nofilter|base|0x|cap60|exit_approx | cost | 6.03% | 21.28% | 0.58 | 38.8% |
| mom_12_1_band_nofilter|base|1x|cap60|exit_approx | exposure | 5.87% | 21.30% | 0.57 | 38.8% |
| mom_12_1_band_nofilter|base|2x|cap60|exit_approx | cost | 5.78% | 21.18% | 0.56 | 38.8% |
| mom_12_1_band_nofilter|base|3x|cap60|exit_approx | cost | 5.70% | 21.16% | 0.55 | 38.8% |
| mom_12_1_band_nofilter|base|0x|cap100|exit_approx | cost | 7.39% | 21.77% | 0.64 | 47.1% |
| mom_12_1_band_nofilter|base|1x|cap100|exit_approx | variant | 7.26% | 21.77% | 0.64 | 47.1% |
| mom_12_1_band_nofilter|base|2x|cap100|exit_approx | cost | 7.11% | 21.75% | 0.62 | 47.1% |
| mom_12_1_band_nofilter|base|3x|cap100|exit_approx | cost | 6.99% | 21.75% | 0.61 | 47.2% |
| mom_12_1_band_nofilter|stress|0x|cap30|exit_approx | cost | 4.16% | 12.51% | 0.62 | 20.1% |
| mom_12_1_band_nofilter|stress|1x|cap30|exit_approx | fill | 3.97% | 12.63% | 0.60 | 20.1% |
| mom_12_1_band_nofilter|stress|2x|cap30|exit_approx | cost | 3.89% | 12.93% | 0.59 | 20.1% |
| mom_12_1_band_nofilter|stress|3x|cap30|exit_approx | cost | 3.76% | 13.10% | 0.57 | 20.1% |
| mom_12_1_band_nofilter|stress|0x|cap60|exit_approx | cost | 5.92% | 20.28% | 0.57 | 38.6% |
| mom_12_1_band_nofilter|stress|1x|cap60|exit_approx | fill | 5.76% | 20.28% | 0.56 | 38.6% |
| mom_12_1_band_nofilter|stress|2x|cap60|exit_approx | cost | 5.51% | 20.34% | 0.54 | 38.6% |
| mom_12_1_band_nofilter|stress|3x|cap60|exit_approx | cost | 5.30% | 20.47% | 0.52 | 38.6% |
| mom_12_1_band_nofilter|stress|0x|cap100|exit_approx | cost | 7.41% | 20.94% | 0.64 | 46.9% |
| mom_12_1_band_nofilter|stress|1x|cap100|exit_approx | fill | 7.13% | 21.05% | 0.62 | 47.0% |
| mom_12_1_band_nofilter|stress|2x|cap100|exit_approx | cost | 6.85% | 21.04% | 0.60 | 47.0% |
| mom_12_1_band_nofilter|stress|3x|cap100|exit_approx | cost | 6.58% | 21.08% | 0.58 | 47.0% |
