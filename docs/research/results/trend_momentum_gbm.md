# trend_momentum_gbm: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/trend_momentum_gbm.md`. Family in gate v3: cross_sectional_momentum.

- Trials logged: 6 configurations for this rule, 760 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).

## ?, point_in_time

Report `trend_momentum_gbm_balanced_pit_20261124T185751.json`, run 2026-11-24T18:57:51+00:00 at code `525436e`. Account `rule|1x`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_total_return.

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 10.32% | 14.85% | 0.75 | 82% | 14.39% / 33.78% | 11.08% / 20.40% | -0.46% (-0.15) | 3.28% (0.83) | 0.50 | 0.23% |
| Research window: 2017-05-01 to 2024-12-31 | 10.32% | 14.85% | 0.75 | 82% | 14.39% / 33.78% | 11.08% / 20.40% | -0.46% (-0.15) | 3.28% (0.83) | 0.50 | 0.23% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 10.32%, Sharpe 0.75, alpha 3.28% (t 0.83). In bills: CAGR 10.77%, Sharpe above bills 0.62, alpha 2.55% (t 0.65). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 83.6 of 2000 runs (NOT matched), p 0.1644; kind exposure_matched; drew from: candidate_pool; drawdown percentile 98.9.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| depth2 | - | 8.09% | -2.22 points | 17.37% | 0.60 | 82% | -2.27% (-0.71) | 1.20% (0.30) | 0.51 |
| lr10 | - | 11.39% | +1.08 points | 15.83% | 0.82 | 82% | 0.73% (0.23) | 4.14% (1.05) | 0.51 |
| noscore | - | 9.43% | -0.88 points | 16.65% | 0.70 | 82% | -1.16% (-0.37) | 2.55% (0.64) | 0.49 |
| refit6m | - | 9.62% | -0.70 points | 14.78% | 0.71 | 82% | -1.25% (-0.39) | 2.78% (0.70) | 0.49 |
| ridge | - | 7.99% | -2.33 points | 16.19% | 0.61 | 81% | -2.58% (-0.83) | 1.34% (0.34) | 0.49 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| rule|2x | cost | 8.56% | 15.17% | 0.64 | 81.9% |
| rule|3x | cost | 7.60% | 15.77% | 0.58 | 81.9% |
| rule|0x | cost | 9.78% | 14.73% | 0.72 | 82.0% |
| rule|stress | fill | 9.36% | 13.45% | 0.70 | 81.9% |
