# rel_strength: results of every run on record

Written 2026-11-23T12:27:50+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/rel_strength.md`. Family in gate v3: cross_sectional_momentum.

- Trials logged: 691 configurations for this rule, 748 for its family, 10,847 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_multiday_summary.md`](as_reported/experiments_multiday_summary.md).

## multiday, today

Report `walkforward_balanced_md_rel_strength_20261112T142003.json`, run 2026-11-12T14:20:03+00:00 at code `45f0dc6`. Account `rel_strength|default`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: provisional (decision 15): a first look from the research harness of #113, on today's stock list and with cost model v1; not a verdict, cannot pass Gate B

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 4.97% | 7.75% | 0.92 | 16% | 14.37% / 33.78% | 2.56% / 2.93% | 2.45% (1.71) | 3.21% (1.92) | 0.12 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 4.68% | 7.75% | 0.89 | 17% | 14.39% / 33.78% | 2.77% / 2.93% | 1.95% (1.25) | 3.02% (1.64) | 0.11 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 83% of the account was idle. At 0%: CAGR 4.68%, Sharpe 0.89, alpha 3.02% (t 1.64). In bills: CAGR 6.70%, Sharpe above bills 0.82, alpha 2.89% (t 1.58). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| rel_strength|tuned | variant | 5.48% | 17.27% | 0.87 | 18.7% |
