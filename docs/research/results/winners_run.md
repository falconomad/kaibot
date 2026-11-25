# winners_run: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/winners_run.md`. Family in gate v3: intraday_dip_reversal.

- Trials logged: 346 configurations for this rule, 8,632 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_multiday_summary.md`](as_reported/experiments_multiday_summary.md).

## multiday, today

Report `walkforward_balanced_md_winners_run_20261112T143008.json`, run 2026-11-12T14:30:08+00:00 at code `45f0dc6`. Account `winners_run|default`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: provisional (decision 15): a first look from the research harness of #113, on today's stock list and with cost model v1; not a verdict, cannot pass Gate B

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -12.61% | 72.40% | -1.90 | 11% | 14.37% / 33.78% | 1.27% / 5.55% | -14.51% (-7.30) | -15.35% (-7.42) | 0.14 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | -12.59% | 65.26% | -1.98 | 11% | 14.39% / 33.78% | 1.34% / 5.55% | -14.57% (-6.89) | -15.46% (-7.02) | 0.15 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 89% of the account was idle. At 0%: CAGR -12.59%, Sharpe -1.98, alpha -15.46% (t -7.02). In bills: CAGR -10.79%, Sharpe above bills -2.02, alpha -15.39% (t -7.05). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| winners_run|tuned | variant | -12.22% | 71.09% | -1.80 | 12.1% |
