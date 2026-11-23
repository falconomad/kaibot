# news_gap_week: results of every run on record

Written 2026-11-23T12:27:50+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/news_gap_week.md`. Family in gate v3: news_drift.

- Trials logged: 6 configurations for this rule, 12 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, point_in_time

Report `oneshots_news_gap_week_20261121T171857.json`, run 2026-11-21T17:18:57.769089+00:00 at code `8b7afd0`. Account `news_gap_week|base|1x|cap100`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_not_stated.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 2.76% | 13.94% | 0.54 | 11% | 14.37% / 33.78% | 1.74% / 6.87% | 1.10% (0.78) | 1.62% (1.00) | 0.08 | 0.24% |
| Research window: 2017-05-01 to 2024-12-31 | 1.18% | 13.94% | 0.26 | 11% | 14.39% / 33.78% | 2.05% / 6.87% | -0.77% (-0.52) | 0.19% (0.11) | 0.07 | 0.23% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 89% of the account was idle. At 0%: CAGR 1.18%, Sharpe 0.26, alpha 0.19% (t 0.11). In bills: CAGR 3.26%, Sharpe above bills 0.21, alpha 0.09% (t 0.05). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 88.4 of 500 runs, p 0.1178; kind exposure_matched; drew from: universe; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: cannot be judged yet (provisional data); 6: fail.
- The report's own verdict line: news_gap_week: FAIL (mark 2, 3, 4, 6).

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| move2 | - | 1.33% | +0.15 points | 9.95% | 0.33 | 7% | -0.09% (-0.07) | 0.63% (0.45) | 0.05 |
| relvol2 | - | -1.04% | -2.23 points | 52.66% | 0.01 | 36% | -5.00% (-1.26) | -6.88% (-1.23) | 0.46 |
| hold10 | - | 2.68% | +1.50 points | 13.08% | 0.41 | 21% | 0.27% (0.13) | 0.60% (0.25) | 0.15 |
| info8k | - | -1.40% | -2.59 points | 12.40% | -0.48 | 3% | -1.86% (-2.06) | -1.79% (-1.75) | 0.03 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| news_gap_week|base|0x|cap100 | cost | 2.98% | 13.70% | 0.59 | 11.0% |
| news_gap_week|base|2x|cap100 | cost | 2.55% | 14.04% | 0.51 | 11.0% |
| news_gap_week|base|3x|cap100 | cost | 2.29% | 14.22% | 0.46 | 11.0% |
| news_gap_week|stress|0x|cap100 | cost | 0.80% | 17.75% | 0.17 | 11.0% |
| news_gap_week|stress|1x|cap100 | fill | 0.35% | 18.29% | 0.09 | 11.0% |
| news_gap_week|stress|2x|cap100 | cost | -0.09% | 20.25% | 0.01 | 11.0% |
| news_gap_week|stress|3x|cap100 | cost | -0.55% | 22.34% | -0.07 | 11.0% |
