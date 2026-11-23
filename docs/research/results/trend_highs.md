# trend_highs: results of every run on record

Written 2026-11-23T12:27:51+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/trend_highs.md`. Family in gate v3: new_highs_breakout.

- Trials logged: 1,036 configurations for this rule, 1,045 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_multiday_summary.md`](as_reported/experiments_multiday_summary.md).

## multiday, today

Report `walkforward_balanced_md_trend_highs_20261112T141956.json`, run 2026-11-12T14:19:56+00:00 at code `45f0dc6`. Account `trend_highs|default`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: provisional (decision 15): a first look from the research harness of #113, on today's stock list and with cost model v1; not a verdict, cannot pass Gate B

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 0.54% | 17.60% | 0.12 | 28% | 14.37% / 33.78% | 3.32% / 8.56% | -2.67% (-1.76) | -1.68% (-1.06) | 0.16 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 0.49% | 17.60% | 0.12 | 29% | 14.39% / 33.78% | 3.69% / 8.56% | -3.07% (-1.96) | -1.83% (-1.06) | 0.16 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 71% of the account was idle. At 0%: CAGR 0.49%, Sharpe 0.12, alpha -1.83% (t -1.06). In bills: CAGR 2.13%, Sharpe above bills -0.01, alpha -2.13% (t -1.24). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| trend_highs|tuned | variant | 1.05% | 13.76% | 0.21 | 27.6% |
