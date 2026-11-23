# weekly_fall_rebound: results of every run on record

Written 2026-11-23T12:27:51+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/weekly_fall_rebound.md`. Family in gate v3: short_term_reversal.

- Trials logged: 10 configurations for this rule, 18 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, point_in_time

Report `oneshots_weekly_fall_rebound_20261113T085648.json`, run 2026-11-13T08:56:48.569377+00:00 at code `7c0123e`. Account `weekly_fall_rebound|base|1x|cap100`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 9.70% | 47.63% | 0.52 | 22% | 14.37% / 33.78% | 5.38% / 26.94% | 5.89% (1.14) | 0.96% (0.16) | 0.71 | 0.71% |
| Research window: 2017-05-01 to 2024-12-31 | 7.41% | 47.63% | 0.43 | 21% | 14.39% / 33.78% | 4.71% / 26.94% | 4.40% (0.72) | -0.86% (-0.12) | 0.69 | 0.73% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 79% of the account was idle. At 0%: CAGR 7.41%, Sharpe 0.43, alpha -0.86% (t -0.12). In bills: CAGR 9.43%, Sharpe above bills 0.41, alpha 0.29% (t 0.04). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 94.6 of 500 runs, p 0.0559; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: pass; 4: fail; 5: fail; 6: pass.
- The report's own verdict line: weekly_fall_rebound: FAIL (mark 2, 4, 5); weekly_fall_rebound_20: FAIL (mark 2, 3, 4, 5).

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| fall10 | - | 9.80% | +2.39 points | 58.53% | 0.47 | 53% | 2.45% (0.33) | -2.67% (-0.35) | 1.07 |
| fall20 | - | 5.12% | -2.30 points | 50.55% | 0.34 | 9% | 3.71% (0.60) | -0.58% (-0.08) | 0.51 |
| slots5 | - | 5.64% | -1.77 points | 45.16% | 0.36 | 24% | 2.63% (0.40) | -1.75% (-0.25) | 0.63 |
| market_filter | - | -0.09% | -7.50 points | 30.20% | 0.04 | 13% | -3.31% (-1.23) | -1.82% (-0.60) | 0.14 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| weekly_fall_rebound|base|0x|cap100 | cost | 10.46% | 47.04% | 0.55 | 21.9% |
| weekly_fall_rebound|base|2x|cap100 | cost | 8.96% | 48.24% | 0.49 | 21.9% |
| weekly_fall_rebound|base|3x|cap100 | cost | 8.25% | 48.83% | 0.46 | 21.9% |
| weekly_fall_rebound|stress|0x|cap100 | cost | 10.92% | 57.51% | 0.55 | 21.9% |
| weekly_fall_rebound|stress|1x|cap100 | fill | 9.41% | 58.44% | 0.49 | 21.9% |
| weekly_fall_rebound|stress|2x|cap100 | cost | 7.95% | 59.32% | 0.44 | 21.9% |
| weekly_fall_rebound|stress|3x|cap100 | cost | 6.53% | 60.23% | 0.38 | 21.9% |
| weekly_fall_rebound|base|1x|cap30 | exposure | 5.28% | 22.16% | 0.55 | 13.1% |
| weekly_fall_rebound|base|1x|cap60 | exposure | 7.30% | 39.53% | 0.51 | 18.4% |

## oneshots, point_in_time (version weekly_fall_rebound_20)

Report `oneshots_weekly_fall_rebound_20261113T085648.json`, run 2026-11-13T08:56:48.569377+00:00 at code `7c0123e`. Account `weekly_fall_rebound_20|base|1x|cap100`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 6.37% | 55.12% | 0.36 | 56% | 14.37% / 33.78% | 9.51% / 29.84% | -0.30% (-0.05) | -5.61% (-0.90) | 1.02 | 0.44% |
| Research window: 2017-05-01 to 2024-12-31 | 7.29% | 55.12% | 0.40 | 54% | 14.39% / 33.78% | 9.32% / 29.84% | 0.57% (0.09) | -4.31% (-0.63) | 0.98 | 0.45% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 46% of the account was idle. At 0%: CAGR 7.29%, Sharpe 0.40, alpha -4.31% (t -0.63). In bills: CAGR 8.47%, Sharpe above bills 0.35, alpha -3.27% (t -0.48). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 12.8 of 500 runs, p 0.8723; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: cannot be judged yet (provisional data).
- The report's own verdict line: weekly_fall_rebound: FAIL (mark 2, 4, 5); weekly_fall_rebound_20: FAIL (mark 2, 3, 4, 5).

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| weekly_fall_rebound_20|base|0x|cap100 | cost | 6.77% | 55.06% | 0.38 | 56.3% |
| weekly_fall_rebound_20|base|2x|cap100 | cost | 5.95% | 55.15% | 0.35 | 56.3% |
| weekly_fall_rebound_20|base|3x|cap100 | cost | 5.45% | 55.22% | 0.33 | 56.3% |
| weekly_fall_rebound_20|stress|0x|cap100 | cost | 6.56% | 57.33% | 0.37 | 56.3% |
| weekly_fall_rebound_20|stress|1x|cap100 | fill | 5.63% | 57.42% | 0.34 | 56.3% |
| weekly_fall_rebound_20|stress|2x|cap100 | cost | 4.80% | 57.53% | 0.31 | 56.3% |
| weekly_fall_rebound_20|stress|3x|cap100 | cost | 3.86% | 57.60% | 0.28 | 56.4% |
| weekly_fall_rebound_20|base|1x|cap30 | exposure | 0.37% | 24.91% | 0.09 | 23.7% |
| weekly_fall_rebound_20|base|1x|cap60 | exposure | 3.46% | 41.77% | 0.27 | 41.2% |
