# pullback_uptrend: results of every run on record

Written 2026-11-23T12:27:50+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/pullback_uptrend.md`. Family in gate v3: short_term_reversal.

- Trials logged: 8 configurations for this rule, 18 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, point_in_time

Report `oneshots_pullback_uptrend_20261113T085258.json`, run 2026-11-13T08:52:58.964994+00:00 at code `7c0123e`. Account `pullback_uptrend|base|1x|cap100`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 4.82% | 27.14% | 0.44 | 53% | 14.37% / 33.78% | 8.41% / 16.49% | -3.03% (-0.94) | -0.57% (-0.17) | 0.40 | 1.37% |
| Research window: 2017-05-01 to 2024-12-31 | 8.98% | 27.14% | 0.80 | 53% | 14.39% / 33.78% | 8.08% / 16.49% | 1.08% (0.34) | 3.46% (1.04) | 0.38 | 1.39% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 47% of the account was idle. At 0%: CAGR 8.98%, Sharpe 0.80, alpha 3.46% (t 1.04). In bills: CAGR 10.14%, Sharpe above bills 0.69, alpha 3.10% (t 0.94). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 46.0 of 500 runs, p 0.5409; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.
- The report's own verdict line: pullback_uptrend: FAIL (mark 2, 3, 4, 5).

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| rsi5 | - | 6.06% | -2.91 points | 29.53% | 0.61 | 46% | 0.77% (0.26) | 1.22% (0.40) | 0.35 |
| rsi15 | - | 8.24% | -0.74 points | 29.49% | 0.72 | 55% | -0.30% (-0.09) | 2.43% (0.73) | 0.41 |
| slots10 | - | 10.06% | +1.09 points | 40.35% | 0.65 | 80% | -0.26% (-0.07) | 1.37% (0.32) | 0.64 |
| no_sma5 | - | 8.98% | +0.00 points | 27.14% | 0.80 | 53% | 1.08% (0.34) | 3.46% (1.04) | 0.38 |
| sma150 | - | 9.78% | +0.81 points | 24.88% | 0.86 | 53% | 1.98% (0.62) | 3.94% (1.21) | 0.40 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| pullback_uptrend|base|0x|cap100 | cost | 6.16% | 25.66% | 0.55 | 53.5% |
| pullback_uptrend|base|2x|cap100 | cost | 3.48% | 29.10% | 0.34 | 53.4% |
| pullback_uptrend|base|3x|cap100 | cost | 2.19% | 31.05% | 0.24 | 53.4% |
| pullback_uptrend|stress|0x|cap100 | cost | 6.16% | 25.66% | 0.55 | 53.5% |
| pullback_uptrend|stress|1x|cap100 | fill | 3.48% | 29.10% | 0.34 | 53.4% |
| pullback_uptrend|stress|2x|cap100 | cost | 0.90% | 32.83% | 0.13 | 53.4% |
| pullback_uptrend|stress|3x|cap100 | cost | -1.57% | 36.39% | -0.07 | 53.4% |
| pullback_uptrend|base|1x|cap30 | exposure | 1.44% | 22.18% | 0.23 | 27.9% |
| pullback_uptrend|base|1x|cap60 | exposure | 4.82% | 27.14% | 0.44 | 53.4% |
