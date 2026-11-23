# post_earnings_week: results of every run on record

Written 2026-11-23T12:27:50+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/post_earnings_week.md`. Family in gate v3: earnings_drift.

- Trials logged: 7 configurations for this rule, 7 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, point_in_time

Report `oneshots_post_earnings_week_20261113T085630.json`, run 2026-11-13T08:56:30.479497+00:00 at code `7c0123e`. Account `post_earnings_week|base|1x|cap100`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 6.01% | 24.41% | 0.58 | 36% | 14.37% / 33.78% | 6.49% / 15.16% | -0.21% (-0.10) | 1.23% (0.39) | 0.35 | 0.80% |
| Research window: 2017-05-01 to 2024-12-31 | 5.51% | 24.41% | 0.52 | 36% | 14.39% / 33.78% | 5.99% / 15.16% | -0.18% (-0.07) | 0.27% (0.07) | 0.38 | 0.82% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 64% of the account was idle. At 0%: CAGR 5.51%, Sharpe 0.52, alpha 0.27% (t 0.07). In bills: CAGR 7.07%, Sharpe above bills 0.45, alpha 0.32% (t 0.09). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 77.2 of 500 runs, p 0.2295; kind exposure_matched; drew from: universe; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.
- The report's own verdict line: post_earnings_week: FAIL (mark 2, 3, 4, 5).

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| day2 | - | 4.45% | -1.06 points | 23.52% | 0.43 | 36% | -2.06% (-0.82) | -0.67% (-0.19) | 0.38 |
| hold10 | - | 4.80% | -0.71 points | 28.53% | 0.40 | 52% | -2.32% (-0.84) | -2.62% (-0.72) | 0.55 |
| top10pct | - | 3.32% | -2.20 points | 13.62% | 0.42 | 21% | -0.59% (-0.34) | -0.09% (-0.03) | 0.25 |
| slots20 | - | 3.58% | -1.93 points | 15.43% | 0.48 | 23% | -0.29% (-0.22) | 0.11% (0.05) | 0.25 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| post_earnings_week|base|0x|cap100 | cost | 6.81% | 24.15% | 0.65 | 36.1% |
| post_earnings_week|base|2x|cap100 | cost | 5.18% | 24.66% | 0.51 | 36.1% |
| post_earnings_week|base|3x|cap100 | cost | 4.44% | 25.61% | 0.45 | 36.1% |
| post_earnings_week|stress|0x|cap100 | cost | 4.94% | 28.65% | 0.47 | 36.1% |
| post_earnings_week|stress|1x|cap100 | fill | 3.39% | 31.84% | 0.35 | 36.1% |
| post_earnings_week|stress|2x|cap100 | cost | 1.87% | 34.93% | 0.22 | 36.1% |
| post_earnings_week|stress|3x|cap100 | cost | 0.37% | 37.74% | 0.09 | 36.1% |
| post_earnings_week|base|1x|cap30 | exposure | 3.41% | 9.25% | 0.62 | 18.0% |
| post_earnings_week|base|1x|cap60 | exposure | 3.62% | 21.64% | 0.46 | 27.9% |
