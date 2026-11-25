# index_core_trend_momentum: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/index_core_trend_momentum.md`. Family in gate v3: none.

- Trials logged: 6 configurations for this rule, 0 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).

## ?, point_in_time

Report `index_core_trend_momentum_aggressive_pit_20261124T205816.json`, run 2026-11-24T20:58:16+00:00 at code `0945894`. Account `book|1x`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_total_return.

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 4.15% | 6.61% | 0.88 | 32% | 14.39% / 33.78% | 4.46% / 7.14% | -0.30% (-0.87) | 0.72% (0.91) | 0.23 | 0.05% |
| Research window: 2017-05-01 to 2024-12-31 | 4.15% | 6.61% | 0.88 | 32% | 14.39% / 33.78% | 4.46% / 7.14% | -0.30% (-0.87) | 0.72% (0.91) | 0.23 | 0.05% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 68% of the account was idle. At 0%: CAGR 4.15%, Sharpe 0.88, alpha 0.72% (t 0.91). In bills: CAGR 5.76%, Sharpe above bills 0.72, alpha 0.49% (t 0.63). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 88.6 of 2000 runs, p 0.1144; kind exposure_matched; drew from: not stated; drawdown percentile 99.9.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| budget5 | - | 3.75% | -0.39 points | 5.96% | 0.92 | 28% | -0.18% (-1.08) | 0.71% (1.13) | 0.20 |
| vol12 | - | 3.93% | -0.21 points | 5.82% | 0.91 | 30% | -0.28% (-0.83) | 0.87% (1.09) | 0.20 |
| vol18 | - | 4.29% | +0.14 points | 7.23% | 0.86 | 33% | -0.28% (-0.83) | 0.57% (0.75) | 0.25 |
| lookback63 | - | 3.92% | -0.23 points | 8.44% | 0.81 | 32% | -0.27% (-0.78) | 0.29% (0.43) | 0.24 |
| nocap | - | 4.66% | +0.51 points | 11.09% | 0.81 | 35% | -0.29% (-0.83) | 0.02% (0.05) | 0.31 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| book|2x | cost | 4.09% | 6.64% | 0.87 | 31.7% |
| book|3x | cost | 4.02% | 6.65% | 0.86 | 31.7% |
| book|0x | cost | 4.17% | 6.60% | 0.89 | 31.8% |
| book|stress | fill | 4.04% | 6.72% | 0.86 | 31.7% |
| book_level|balanced | variant | 4.15% | 6.61% | 0.88 | 31.8% |
| book_level|conservative | variant | 3.51% | 6.09% | 0.84 | 27.2% |
