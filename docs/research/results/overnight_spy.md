# overnight_spy: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/overnight_spy.md`. Family in gate v3: overnight_premium.

- Trials logged: 346 configurations for this rule, 346 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_multiday_summary.md`](as_reported/experiments_multiday_summary.md).

## multiday, today

Report `walkforward_balanced_md_overnight_spy_20261112T142006.json`, run 2026-11-12T14:20:06+00:00 at code `45f0dc6`. Account `overnight_spy|default`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: provisional (decision 15): a first look from the research harness of #113, on today's stock list and with cost model v1; not a verdict, cannot pass Gate B

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 0.13% | 3.78% | 0.13 | 12% | 14.37% / 33.78% | 1.30% / 3.78% | -1.17% (-2.69) | -0.25% (-0.84) | 0.03 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 0.10% | 3.78% | 0.11 | 12% | 14.39% / 33.78% | 1.37% / 3.78% | -1.26% (-2.62) | -0.25% (-0.78) | 0.02 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 88% of the account was idle. At 0%: CAGR 0.10%, Sharpe 0.11, alpha -0.25% (t -0.78). In bills: CAGR 2.13%, Sharpe above bills -0.18, alpha -0.49% (t -1.56). SPY's Sharpe above bills: 0.70.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| overnight_spy|tuned | variant | 0.18% | 2.86% | 0.19 | 11.9% |
