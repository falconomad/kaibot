# index_core: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/index_core.md`. Family in gate v3: none.

- Trials logged: 6 configurations for this rule, 0 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).

## ?, point_in_time

Report `index_core_aggressive_pit_20261124T205432.json`, run 2026-11-24T20:54:32+00:00 at code `0945894`. Account `base|1x`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_total_return.

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 3.38% | 5.13% | 0.97 | 23% | 14.39% / 33.78% | 3.41% / 5.13% | -0.03% (-1.50) | 0.73% (1.43) | 0.18 | 0.01% |
| Research window: 2017-05-01 to 2024-12-31 | 3.38% | 5.13% | 0.97 | 23% | 14.39% / 33.78% | 3.41% / 5.13% | -0.03% (-1.50) | 0.73% (1.43) | 0.18 | 0.01% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 77% of the account was idle. At 0%: CAGR 3.38%, Sharpe 0.97, alpha 0.73% (t 1.43). In bills: CAGR 5.20%, Sharpe above bills 0.81, alpha 0.58% (t 1.17). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| vol12 | - | 3.12% | -0.26 points | 4.32% | 1.00 | 22% | -0.03% (-1.63) | 0.82% (1.60) | 0.15 |
| vol18 | - | 3.46% | +0.08 points | 6.16% | 0.90 | 25% | -0.03% (-1.41) | 0.51% (1.03) | 0.20 |
| lookback63 | - | 3.09% | -0.30 points | 6.94% | 0.84 | 24% | -0.02% (-1.25) | 0.22% (0.58) | 0.19 |
| daily | - | 3.21% | -0.17 points | 5.46% | 0.93 | 23% | -0.03% (-1.54) | 0.60% (1.13) | 0.17 |
| nocap | - | 3.87% | +0.49 points | 9.60% | 0.82 | 26% | -0.02% (-1.15) | 0.00% (0.06) | 0.26 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| base|2x | cost | 3.36% | 5.14% | 0.96 | 23.5% |
| base|3x | cost | 3.35% | 5.14% | 0.96 | 23.5% |
| base|0x | cost | 3.39% | 5.13% | 0.97 | 23.5% |
| base|stress | fill | 3.37% | 5.16% | 0.96 | 23.5% |
| level|balanced | variant | 3.38% | 5.13% | 0.97 | 23.5% |
| level|conservative | variant | 3.38% | 5.13% | 0.97 | 23.5% |
