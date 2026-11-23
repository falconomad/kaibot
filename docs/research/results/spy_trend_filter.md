# spy_trend_filter: results of every run on record

Written 2026-11-23T12:27:51+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/spy_trend_filter.md`. Family in gate v3: index_trend_timing.

- Trials logged: 6 configurations for this rule, 6 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_spy_trend_summary.md`](as_reported/experiments_spy_trend_summary.md).

## spy_trend, point_in_time, costs v2

Report `spy_trend_20261113T112924.json`, run 2026-11-13T11:29:24+00:00 at code `4168cb9`. Account `spy_trend_filter|v2|base|1x`. Engine: research_harness. Universe: point_in_time. Cost model: v2. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine on the daily store (raw SPY bars, dividends, SPY total return), cost models v1 and v2; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 7.66% | 27.51% | 0.64 | 83% | 14.37% / 33.78% | 7.77% / 27.43% | -0.10% (-1.74) | 0.67% (0.21) | 0.50 | 0.04% |
| Research window: 2017-05-01 to 2024-12-31 | 8.24% | 27.51% | 0.68 | 82% | 14.39% / 33.78% | 8.35% / 27.43% | -0.11% (-1.60) | 1.24% (0.34) | 0.50 | 0.04% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 8.24%, Sharpe 0.68, alpha 1.24% (t 0.34). In bills: CAGR 8.69%, Sharpe above bills 0.53, alpha 0.50% (t 0.14). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 11.6 of 500 runs, p 0.8842; kind random_timing; drew from: random_timing; drawdown percentile not in the report.
- Gate v2 as the report stored it, cost model v1: verdict **fail**; marks 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.
- Gate v2 as the report stored it, cost model v2: verdict **fail**; marks 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| sma150d | - | 11.14% | +2.90 points | 23.63% | 0.91 | 79% | -0.10% (-1.48) | 4.43% (1.25) | 0.46 |
| sma200d | - | 9.72% | +1.49 points | 27.51% | 0.80 | 81% | -0.10% (-1.48) | 2.95% (0.82) | 0.47 |
| sma250d | - | 9.69% | +1.45 points | 22.20% | 0.79 | 80% | -0.10% (-1.48) | 2.81% (0.77) | 0.48 |
| weekly | - | 10.19% | +1.95 points | 19.20% | 0.86 | 79% | -0.14% (-2.04) | 3.89% (1.09) | 0.43 |
| band1 | - | 13.08% | +4.84 points | 15.59% | 1.07 | 79% | -0.09% (-1.29) | 6.32% (1.80) | 0.44 |
| sma150d | - | 11.14% | +2.90 points | 23.64% | 0.91 | 79% | -0.10% (-1.53) | 4.42% (1.25) | 0.46 |
| sma200d | - | 9.72% | +1.48 points | 27.51% | 0.80 | 81% | -0.10% (-1.53) | 2.94% (0.82) | 0.47 |
| sma250d | - | 9.68% | +1.45 points | 22.21% | 0.79 | 80% | -0.10% (-1.53) | 2.80% (0.77) | 0.48 |
| weekly | - | 10.18% | +1.95 points | 19.22% | 0.86 | 79% | -0.15% (-2.14) | 3.89% (1.09) | 0.43 |
| band1 | - | 13.08% | +4.84 points | 15.60% | 1.07 | 79% | -0.09% (-1.32) | 6.32% (1.80) | 0.44 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_trend_filter|v1|base|0x | cost | 7.71% | 27.44% | 0.64 | 82.5% |
| spy_trend_filter|v1|base|2x | cost | 7.63% | 27.57% | 0.64 | 82.5% |
| spy_trend_filter|v1|base|3x | cost | 7.59% | 27.64% | 0.64 | 82.5% |
| spy_trend_filter|v1|stress|1x | fill | 7.23% | 25.77% | 0.61 | 82.5% |
| spy_trend_filter|v2|base|0x | cost | 7.71% | 27.44% | 0.64 | 82.5% |
| spy_trend_filter|v2|base|2x | cost | 7.62% | 27.59% | 0.64 | 82.5% |
| spy_trend_filter|v2|base|3x | cost | 7.58% | 27.67% | 0.63 | 82.5% |
| spy_trend_filter|v2|stress|1x | fill | 7.22% | 25.79% | 0.61 | 82.5% |

## spy_trend, point_in_time, costs v1

Report `spy_trend_20261113T112924.json`, run 2026-11-13T11:29:24+00:00 at code `4168cb9`. Account `spy_trend_filter|v1|base|1x`. Engine: research_harness. Universe: point_in_time. Cost model: v1. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine on the daily store (raw SPY bars, dividends, SPY total return), cost models v1 and v2; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 7.67% | 27.51% | 0.64 | 83% | 14.37% / 33.78% | 7.77% / 27.43% | -0.09% (-1.67) | 0.68% (0.21) | 0.50 | 0.04% |
| Research window: 2017-05-01 to 2024-12-31 | 8.24% | 27.51% | 0.68 | 82% | 14.39% / 33.78% | 8.35% / 27.43% | -0.11% (-1.54) | 1.25% (0.34) | 0.50 | 0.04% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 8.24%, Sharpe 0.68, alpha 1.25% (t 0.34). In bills: CAGR 8.70%, Sharpe above bills 0.53, alpha 0.51% (t 0.14). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 11.6 of 500 runs, p 0.8842; kind random_timing; drew from: random_timing; drawdown percentile not in the report.
- Gate v2 as the report stored it, cost model v1: verdict **fail**; marks 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.
- Gate v2 as the report stored it, cost model v2: verdict **fail**; marks 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| sma150d | - | 11.14% | +2.90 points | 23.63% | 0.91 | 79% | -0.10% (-1.48) | 4.43% (1.25) | 0.46 |
| sma200d | - | 9.72% | +1.48 points | 27.51% | 0.80 | 81% | -0.10% (-1.48) | 2.95% (0.82) | 0.47 |
| sma250d | - | 9.69% | +1.45 points | 22.20% | 0.79 | 80% | -0.10% (-1.48) | 2.81% (0.77) | 0.48 |
| weekly | - | 10.19% | +1.95 points | 19.20% | 0.86 | 79% | -0.14% (-2.04) | 3.89% (1.09) | 0.43 |
| band1 | - | 13.08% | +4.84 points | 15.59% | 1.07 | 79% | -0.09% (-1.29) | 6.32% (1.80) | 0.44 |
| sma150d | - | 11.14% | +2.89 points | 23.64% | 0.91 | 79% | -0.10% (-1.53) | 4.42% (1.25) | 0.46 |
| sma200d | - | 9.72% | +1.48 points | 27.51% | 0.80 | 81% | -0.10% (-1.53) | 2.94% (0.82) | 0.47 |
| sma250d | - | 9.68% | +1.44 points | 22.21% | 0.79 | 80% | -0.10% (-1.53) | 2.80% (0.77) | 0.48 |
| weekly | - | 10.18% | +1.94 points | 19.22% | 0.86 | 79% | -0.15% (-2.14) | 3.89% (1.09) | 0.43 |
| band1 | - | 13.08% | +4.84 points | 15.60% | 1.07 | 79% | -0.09% (-1.32) | 6.32% (1.80) | 0.44 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_trend_filter|v1|base|0x | cost | 7.71% | 27.44% | 0.64 | 82.5% |
| spy_trend_filter|v1|base|2x | cost | 7.63% | 27.57% | 0.64 | 82.5% |
| spy_trend_filter|v1|base|3x | cost | 7.59% | 27.64% | 0.64 | 82.5% |
| spy_trend_filter|v1|stress|1x | fill | 7.23% | 25.77% | 0.61 | 82.5% |
| spy_trend_filter|v2|base|0x | cost | 7.71% | 27.44% | 0.64 | 82.5% |
| spy_trend_filter|v2|base|2x | cost | 7.62% | 27.59% | 0.64 | 82.5% |
| spy_trend_filter|v2|base|3x | cost | 7.58% | 27.67% | 0.63 | 82.5% |
| spy_trend_filter|v2|stress|1x | fill | 7.22% | 25.79% | 0.61 | 82.5% |
