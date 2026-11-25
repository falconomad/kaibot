# news_continuation: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/news_continuation.md`. Family in gate v3: news_drift.

- Trials logged: 6 configurations for this rule, 12 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, today

Report `oneshots_news_continuation_20261113T085255.json`, run 2026-11-13T08:52:55.230993+00:00 at code `7c0123e`. Account `news_continuation`. Engine: research_harness. Universe: today. Cost model: None. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on today's stock list (survivorship bias), cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -1.48% | 19.88% | -0.41 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | -1.80% (-1.55) | 0.02 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | -1.64% | 19.88% | -0.49 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | -1.92% (-1.60) | 0.02 | n/a |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -1.18.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| move15 | - | -0.97% | +0.67 points | 16.78% | -0.30 | n/a | n/a (n/a) | -1.23% (-1.11) | 0.02 |
| relvol2 | - | -5.38% | -3.74 points | 38.60% | -1.17 | n/a | n/a (n/a) | -5.97% (-3.53) | 0.04 |
| relvol5 | - | -0.20% | +1.44 points | 8.89% | -0.09 | n/a | n/a (n/a) | -0.24% (-0.36) | 0.00 |
| stop1 | - | -1.25% | +0.39 points | 15.90% | -0.44 | n/a | n/a (n/a) | -1.44% (-1.42) | 0.01 |
| stop2 | - | -1.75% | -0.11 points | 20.48% | -0.48 | n/a | n/a (n/a) | -2.13% (-1.69) | 0.03 |
