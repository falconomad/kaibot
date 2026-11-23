# spy_noise_band_carry: results of every run on record

Written 2026-11-23T12:27:51+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: none (a version of another rule, or a study). Family in gate v3: spy_intraday_momentum.

- Trials logged: 2 configurations for this rule, 14 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).

## spy, point_in_time, 0.5 bp

Report `spy_balanced_spy_noise_band_carry_20261113T084929.json`, run 2026-11-13T08:49:29+00:00 at code `7c0123e`. Account `spy_noise_band_carry|0.5 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 0.5 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 4.62% | 9.85% | 0.84 | 17% | 14.37% / 33.78% | 1.63% / 12.35% | 2.84% (1.41) | 3.02% (2.11) | 0.11 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 5.56% | 9.85% | 0.98 | 17% | 14.39% / 33.78% | 2.82% / 12.35% | 2.58% (1.15) | 3.96% (2.41) | 0.11 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 83% of the account was idle. At 0%: CAGR 5.56%, Sharpe 0.98, alpha 3.96% (t 2.41). In bills: CAGR 7.56%, Sharpe above bills 0.90, alpha 3.80% (t 2.32). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 99.4 of 500 runs, p 0.0080; kind random_timing; drew from: random_timing; drawdown percentile not in the report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band_carry|0 (gross) | cost | 5.86% | 9.57% | 1.05 | n/a |

## spy, point_in_time, 1 bp

Report `spy_balanced_spy_noise_band_carry_20261113T084929.json`, run 2026-11-13T08:49:29+00:00 at code `7c0123e`. Account `spy_noise_band_carry|1 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 1 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 3.39% | 10.12% | 0.63 | 17% | 14.37% / 33.78% | 1.63% / 12.35% | 1.66% (0.82) | 1.87% (1.31) | 0.11 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 4.33% | 10.12% | 0.77 | 17% | 14.39% / 33.78% | 2.82% / 12.35% | 1.42% (0.63) | 2.83% (1.72) | 0.10 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 83% of the account was idle. At 0%: CAGR 4.33%, Sharpe 0.77, alpha 2.83% (t 1.72). In bills: CAGR 6.31%, Sharpe above bills 0.70, alpha 2.65% (t 1.62). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band_carry|0 (gross) | cost | 5.86% | 9.57% | 1.05 | n/a |

## spy, point_in_time, 2 bp

Report `spy_balanced_spy_noise_band_carry_20261113T084929.json`, run 2026-11-13T08:49:29+00:00 at code `7c0123e`. Account `spy_noise_band_carry|2 bp`. Engine: research_harness. Universe: point_in_time. Cost model: 2 bp. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 0.99% | 12.12% | 0.20 | 17% | 14.37% / 33.78% | 1.63% / 12.35% | -0.69% (-0.34) | -0.42% (-0.29) | 0.10 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 1.92% | 12.12% | 0.36 | 17% | 14.39% / 33.78% | 2.82% / 12.35% | -0.92% (-0.40) | 0.55% (0.34) | 0.10 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 83% of the account was idle. At 0%: CAGR 1.92%, Sharpe 0.36, alpha 0.55% (t 0.34). In bills: CAGR 3.86%, Sharpe above bills 0.29, alpha 0.37% (t 0.23). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band_carry|0 (gross) | cost | 5.86% | 9.57% | 1.05 | n/a |

## spy, point_in_time, model v1

Report `spy_balanced_spy_noise_band_carry_20261113T084929.json`, run 2026-11-13T08:49:29+00:00 at code `7c0123e`. Account `spy_noise_band_carry|model v1`. Engine: research_harness. Universe: point_in_time. Cost model: model v1. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 1.67% | 11.51% | 0.32 | 17% | 14.37% / 33.78% | 1.63% / 12.35% | -0.02% (-0.01) | 0.24% (0.17) | 0.10 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 2.60% | 11.51% | 0.48 | 17% | 14.39% / 33.78% | 2.82% / 12.35% | -0.26% (-0.11) | 1.20% (0.73) | 0.10 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 83% of the account was idle. At 0%: CAGR 2.60%, Sharpe 0.48, alpha 1.20% (t 0.73). In bills: CAGR 4.55%, Sharpe above bills 0.41, alpha 1.02% (t 0.62). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| spy_noise_band_carry|0 (gross) | cost | 5.86% | 9.57% | 1.05 | n/a |
