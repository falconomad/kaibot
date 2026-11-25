# spy_noise_band: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/spy_noise_band.md`. Family in gate v3: spy_intraday_momentum.

- Trials logged: 13 configurations for this rule, 15 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_spy_cost_rerun.md`](as_reported/experiments_spy_cost_rerun.md), [`experiments_spy_engine.md`](as_reported/experiments_spy_engine.md), [`experiments_spy_summary.md`](as_reported/experiments_spy_summary.md).

## spy_engine, point_in_time, 0.5

Report `spy_engine_balanced_20261121T172654.json`, run 2026-11-21T17:26:54+00:00 at code `8b7afd0`. Account `spy_noise_band|0.5`. Engine: simbroker. Universe: point_in_time. Cost model: 0.5. SPY series: spy_total_return.

Status as the report states it: engine + SimBroker + SimClock (decision 15): the frozen rule as an engine strategy, every order through the risk gate and SimBroker, cost model v2 for the go/no-go

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 0.35% | 0.65% | 0.78 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | 0.25% (2.10) | 0.01 | 0.15% |
| Research window: 2017-05-01 to 2024-12-31 | 0.48% | 0.50% | 1.06 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | 0.38% (2.83) | 0.01 | 0.15% |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -3.76.
- Matched random control: none in this report.
- Gate v2 as the report stored it: verdict **fail**; marks 1: fail; 2: fail; 3: pass; 4: fail; 5: fail; 6: fail.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| lb10 | 10-day lookback for sigma (needs 9 of 10 sessions) | 0.47% | -0.01 points | 0.52% | 1.04 | n/a | n/a (n/a) | 0.38% (2.77) | 0.01 |
| lb20 | 20-day lookback (needs 17 of 20) | 0.43% | -0.04 points | 0.35% | 0.99 | n/a | n/a (n/a) | 0.34% (2.53) | 0.01 |
| lb30 | 30-day lookback (needs 26 of 30) | 0.38% | -0.10 points | 0.60% | 0.83 | n/a | n/a (n/a) | 0.28% (1.95) | 0.01 |

## spy_engine, point_in_time, 1

Report `spy_engine_balanced_20261121T172654.json`, run 2026-11-21T17:26:54+00:00 at code `8b7afd0`. Account `spy_noise_band|1`. Engine: simbroker. Universe: point_in_time. Cost model: 1. SPY series: spy_total_return.

Status as the report states it: engine + SimBroker + SimClock (decision 15): the frozen rule as an engine strategy, every order through the risk gate and SimBroker, cost model v2 for the go/no-go

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 0.19% | 1.01% | 0.43 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | 0.10% (0.86) | 0.01 | 0.31% |
| Research window: 2017-05-01 to 2024-12-31 | 0.33% | 0.62% | 0.72 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | 0.24% (1.75) | 0.01 | 0.31% |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -4.06.
- Matched random control: none in this report.
- Gate v2 as the report stored it: verdict **fail**; marks 1: fail; 2: fail; 3: pass; 4: fail; 5: fail; 6: fail.

## spy_engine, point_in_time, v2

Report `spy_engine_balanced_20261121T172654.json`, run 2026-11-21T17:26:54+00:00 at code `8b7afd0`. Account `spy_noise_band|v2`. Engine: simbroker. Universe: point_in_time. Cost model: v2. SPY series: spy_total_return.

Status as the report states it: engine + SimBroker + SimClock (decision 15): the frozen rule as an engine strategy, every order through the risk gate and SimBroker, cost model v2 for the go/no-go

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -0.08% | 1.82% | -0.17 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | -0.16% (-1.28) | 0.01 | 0.58% |
| Research window: 2017-05-01 to 2024-12-31 | 0.06% | 0.87% | 0.14 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | -0.01% (-0.10) | 0.01 | 0.57% |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -4.56.
- Matched random control: none in this report.
- Gate v2 as the report stored it: verdict **fail**; marks 1: fail; 2: fail; 3: pass; 4: fail; 5: fail; 6: fail.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| lb10 | 10-day lookback for sigma (needs 9 of 10 sessions) | 0.05% | -0.01 points | 1.03% | 0.12 | n/a | n/a (n/a) | -0.02% (-0.14) | 0.00 |
| lb20 | 20-day lookback (needs 17 of 20) | 0.03% | -0.03 points | 0.82% | 0.07 | n/a | n/a (n/a) | -0.05% (-0.37) | 0.01 |
| lb30 | 30-day lookback (needs 26 of 30) | -0.01% | -0.08 points | 1.33% | -0.03 | n/a | n/a (n/a) | -0.10% (-0.68) | 0.01 |
| check15 | checks every 15 minutes from 10:00 to 15:45 | -0.02% | -0.09 points | 1.94% | -0.05 | n/a | n/a (n/a) | -0.10% (-0.68) | 0.01 |
| exit1555 | the close exit becomes a sale at the open of the 15:55 bar (checks unchanged) | 0.09% | +0.02 points | 0.92% | 0.20 | n/a | n/a (n/a) | 0.01% (0.07) | 0.01 |

## spy_cost_rerun, today, v2idx

Report `spy_cost_rerun_balanced_20261123T125724.json`, run 2026-11-23T12:57:24+00:00 at code `0743ef0`. Account `spy_noise_band|v2idx`. Engine: research_harness. Universe: today. Cost model: v2idx. SPY series: spy_total_return.

Status as the report states it: cost re-run of the configuration of issue #202; not a verdict

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 0.54% | 0.45% | 1.19 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | 0.44% (3.25) | 0.01 | 0.09% |
| Research window: 2017-05-01 to 2024-12-31 | 0.54% | 0.45% | 1.19 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | 0.44% (3.25) | 0.01 | 0.09% |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -3.64.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

## spy, point_in_time, 0.5 bp

Report `spy_balanced_spy_noise_band_20261113T084923.json`, run 2026-11-13T08:49:23+00:00 at code `7c0123e`. Account `spy_noise_band|0.5 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 0.5 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 2.87% | 5.35% | 0.71 | 0% | 14.37% / 33.78% | 0.00% / 0.00% | 2.91% (2.54) | 1.94% (1.90) | 0.06 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 3.92% | 4.80% | 0.95 | 0% | 14.39% / 33.78% | 0.00% / 0.00% | 3.93% (3.00) | 3.02% (2.57) | 0.06 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 100% of the account was idle. At 0%: CAGR 3.92%, Sharpe 0.95, alpha 3.02% (t 2.57). In bills: CAGR 6.33%, Sharpe above bills 0.95, alpha 3.15% (t 2.66). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 100.0 of 500 runs, p 0.0020; kind random_timing; drew from: random_timing; drawdown percentile not in the report.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| lb10 | 10-day lookback for sigma (needs 9 of 10 sessions) | 3.81% | -0.11 points | 5.57% | 0.94 | 0% | 3.82% (2.98) | 2.97% (2.59) | 0.06 |
| lb20 | 20-day lookback (needs 17 of 20) | 3.79% | -0.13 points | 4.60% | 0.95 | 0% | 3.80% (3.00) | 2.86% (2.41) | 0.06 |
| lb30 | 30-day lookback (needs 26 of 30) | 3.68% | -0.24 points | 3.46% | 0.89 | 0% | 3.70% (2.87) | 2.66% (2.18) | 0.07 |
| check15 | checks every 15 minutes from 10:00 to 15:45 | 3.82% | -0.09 points | 7.34% | 0.90 | 0% | 3.84% (2.67) | 2.92% (2.33) | 0.06 |
| exit1555 | the close exit becomes a sale at the open of the 15:55 bar (checks unchanged) | 4.27% | +0.36 points | 4.10% | 1.03 | 0% | 4.27% (3.23) | 3.36% (2.83) | 0.06 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band|0 (gross) | cost | 4.09% | 4.45% | 1.00 | n/a |

## spy, point_in_time, 1 bp

Report `spy_balanced_spy_noise_band_20261113T084923.json`, run 2026-11-13T08:49:23+00:00 at code `7c0123e`. Account `spy_noise_band|1 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 1 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 1.67% | 7.98% | 0.43 | 0% | 14.37% / 33.78% | 0.00% / 0.00% | 1.74% (1.51) | 0.81% (0.79) | 0.06 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 2.71% | 5.79% | 0.67 | 0% | 14.39% / 33.78% | 0.00% / 0.00% | 2.76% (2.10) | 1.89% (1.61) | 0.06 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 100% of the account was idle. At 0%: CAGR 2.71%, Sharpe 0.67, alpha 1.89% (t 1.61). In bills: CAGR 5.09%, Sharpe above bills 0.67, alpha 2.02% (t 1.70). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band|0 (gross) | cost | 4.09% | 4.45% | 1.00 | n/a |

## spy, point_in_time, 2 bp

Report `spy_balanced_spy_noise_band_20261113T084923.json`, run 2026-11-13T08:49:23+00:00 at code `7c0123e`. Account `spy_noise_band|2 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 2 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -0.70% | 13.52% | -0.15 | 0% | 14.37% / 33.78% | 0.00% / 0.00% | -0.62% (-0.53) | -1.46% (-1.40) | 0.06 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 0.34% | 8.08% | 0.10 | 0% | 14.39% / 33.78% | 0.00% / 0.00% | 0.42% (0.32) | -0.36% (-0.30) | 0.05 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 100% of the account was idle. At 0%: CAGR 0.34%, Sharpe 0.10, alpha -0.36% (t -0.30). In bills: CAGR 2.67%, Sharpe above bills 0.10, alpha -0.24% (t -0.20). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band|0 (gross) | cost | 4.09% | 4.45% | 1.00 | n/a |

## spy, point_in_time, model v1

Report `spy_balanced_spy_noise_band_20261113T084923.json`, run 2026-11-13T08:49:23+00:00 at code `7c0123e`. Account `spy_noise_band|model v1`. Engine: research_harness. Universe: point_in_time. Cost model: model v1. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -0.02% | 11.62% | 0.02 | 0% | 14.37% / 33.78% | 0.00% / 0.00% | 0.06% (0.05) | -0.80% (-0.77) | 0.06 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 1.01% | 7.46% | 0.26 | 0% | 14.39% / 33.78% | 0.00% / 0.00% | 1.09% (0.83) | 0.29% (0.24) | 0.05 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 100% of the account was idle. At 0%: CAGR 1.01%, Sharpe 0.26, alpha 0.29% (t 0.24). In bills: CAGR 3.36%, Sharpe above bills 0.26, alpha 0.41% (t 0.34). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band|0 (gross) | cost | 4.09% | 4.45% | 1.00 | n/a |
