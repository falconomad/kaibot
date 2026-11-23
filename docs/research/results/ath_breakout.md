# ath_breakout: results of every run on record

Written 2026-11-23T12:27:50+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/ath_breakout.md`. Family in gate v3: new_highs_breakout.

- Trials logged: 9 configurations for this rule, 1,045 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, point_in_time

Report `oneshots_ath_breakout_20261113T085319.json`, run 2026-11-13T08:53:19.142259+00:00 at code `7c0123e`. Account `ath_breakout|base|1x|cap100|seed171`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 5.36% | 26.36% | 0.40 | 95% | 14.37% / 33.78% | 13.40% / 26.35% | -7.23% (-1.85) | -2.87% (-0.77) | 0.63 | 0.04% |
| Research window: 2017-05-01 to 2024-12-31 | 7.11% | 26.36% | 0.49 | 95% | 14.39% / 33.78% | 13.35% / 26.35% | -5.52% (-1.26) | -1.43% (-0.34) | 0.64 | 0.04% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 5% of the account was idle. At 0%: CAGR 7.11%, Sharpe 0.49, alpha -1.43% (t -0.34). In bills: CAGR 7.21%, Sharpe above bills 0.36, alpha -2.15% (t -0.51). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 9.4 of 500 runs (NOT matched), p 0.9062; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: cannot be judged yet (provisional data); 4: fail; 5: fail; 6: fail.
- The report's own verdict line: ath_breakout: FAIL (mark 2, 4, 5, 6).

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| trail8 | - | 5.19% | -1.92 points | 28.24% | 0.39 | 95% | -7.28% (-1.68) | -2.54% (-0.64) | 0.59 |
| trail12 | - | 7.04% | -0.07 points | 26.44% | 0.49 | 96% | -5.65% (-1.38) | -1.89% (-0.49) | 0.67 |
| atr14 | - | 3.56% | -3.54 points | 28.50% | 0.30 | 95% | -8.71% (-2.12) | -4.78% (-1.24) | 0.63 |
| price5 | - | 7.11% | +0.00 points | 26.36% | 0.49 | 95% | -5.52% (-1.26) | -1.43% (-0.34) | 0.64 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| ath_breakout|base|0x|cap100|seed171 | cost | 5.39% | 26.32% | 0.40 | 95.3% |
| ath_breakout|base|2x|cap100|seed171 | cost | 5.37% | 26.41% | 0.40 | 95.4% |
| ath_breakout|base|3x|cap100|seed171 | cost | 5.34% | 26.42% | 0.39 | 95.4% |
| ath_breakout|stress|0x|cap100|seed171 | cost | 5.39% | 26.32% | 0.40 | 95.3% |
| ath_breakout|stress|1x|cap100|seed171 | fill | 5.37% | 26.41% | 0.40 | 95.4% |
| ath_breakout|stress|2x|cap100|seed171 | cost | 5.27% | 26.45% | 0.39 | 95.4% |
| ath_breakout|stress|3x|cap100|seed171 | cost | 5.13% | 26.52% | 0.38 | 95.3% |
| ath_breakout|base|1x|cap30|seed171 | exposure | 0.82% | 17.26% | 0.17 | 31.2% |
| ath_breakout|base|1x|cap60|seed171 | exposure | 1.77% | 21.85% | 0.21 | 60.5% |

## oneshots, point_in_time (version ath_breakout_k20)

Report `oneshots_ath_breakout_20261113T085319.json`, run 2026-11-13T08:53:19.142259+00:00 at code `7c0123e`. Account `ath_breakout_k20|base|1x|cap100|seed60`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 7.10% | 21.54% | 0.52 | 95% | 14.37% / 33.78% | 13.30% / 24.15% | -5.59% (-1.65) | -1.19% (-0.36) | 0.62 | 0.04% |
| Research window: 2017-05-01 to 2024-12-31 | 9.98% | 21.54% | 0.68 | 95% | 14.39% / 33.78% | 13.22% / 24.15% | -2.85% (-0.76) | 1.43% (0.38) | 0.62 | 0.03% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 5% of the account was idle. At 0%: CAGR 9.98%, Sharpe 0.68, alpha 1.43% (t 0.38). In bills: CAGR 10.09%, Sharpe above bills 0.54, alpha 0.64% (t 0.17). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

## oneshots, point_in_time (version ath_breakout_k5)

Report `oneshots_ath_breakout_20261113T085319.json`, run 2026-11-13T08:53:19.142259+00:00 at code `7c0123e`. Account `ath_breakout_k5|base|1x|cap100|seed11`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 4.95% | 31.70% | 0.39 | 81% | 14.37% / 33.78% | 11.30% / 25.83% | -5.61% (-1.34) | -1.60% (-0.39) | 0.51 | 0.03% |
| Research window: 2017-05-01 to 2024-12-31 | 6.02% | 31.70% | 0.44 | 81% | 14.39% / 33.78% | 11.29% / 25.83% | -4.51% (-0.99) | -1.38% (-0.31) | 0.56 | 0.03% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 19% of the account was idle. At 0%: CAGR 6.02%, Sharpe 0.44, alpha -1.38% (t -0.31). In bills: CAGR 6.44%, Sharpe above bills 0.32, alpha -1.98% (t -0.44). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.
