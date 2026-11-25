# wide_stop: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: none (a version of another rule, or a study). Family in gate v3: none.

- Trials logged: 0 configurations for this rule, 0 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).

## walkforward, today

Report `walkforward_balanced_wf_A_exp-wide_stop_20261112T114103.json`, run 2026-11-12T11:41:03+00:00 at code `23e8faf`. Account `wide_stop|default`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -4.73% | 37.54% | -1.30 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | -5.82% (-5.15) | 0.07 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | -4.53% | 30.62% | -1.27 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | -5.67% (-4.63) | 0.07 | n/a |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -1.90.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| wide_stop|tuned | variant | n/a | 29.76% | -1.16 | n/a |
