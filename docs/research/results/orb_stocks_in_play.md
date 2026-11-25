# orb_stocks_in_play: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/orb_stocks_in_play.md`. Family in gate v3: opening_range_breakout.

- Trials logged: 4 configurations for this rule, 4 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_oneshots_summary.md`](as_reported/experiments_oneshots_summary.md).

## oneshots, today

Report `oneshots_orb_stocks_in_play_20261113T085035.json`, run 2026-11-13T08:50:35.137990+00:00 at code `7c0123e`. Account `orb_stocks_in_play`. Engine: research_harness. Universe: today. Cost model: None. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on today's stock list (survivorship bias), cost model v1; a one-shot can fail here, it cannot pass (the go/no-go run is the engine with `SimBroker`)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | -16.71% | 82.42% | -12.11 | n/a | 14.37% / 33.78% | n/a / n/a | n/a (n/a) | -18.52% (-38.98) | 0.02 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | -16.28% | 74.38% | -11.45 | n/a | 14.39% / 33.78% | n/a / n/a | n/a (n/a) | -18.02% (-33.50) | 0.02 | n/a |

- Idle cash: bills paid 2.32% a year. No exposure series, so interest cannot be added; the simulated curve's Sharpe above bills is -12.89.
- Matched random control: none in this report.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| top10 | - | -17.88% | -1.61 points | 77.92% | -9.88 | n/a | n/a (n/a) | -19.90% (-26.58) | 0.01 |
| top40 | - | -13.79% | +2.48 points | 67.94% | -11.66 | n/a | n/a (n/a) | -15.14% (-35.54) | 0.02 |
| stop20 | - | -13.83% | +2.45 points | 68.04% | -5.17 | n/a | n/a (n/a) | -15.65% (-17.49) | 0.05 |
