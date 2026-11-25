# trend_momentum: results of every run on record

Written 2026-11-24T20:58:16+00:00 by `scripts/export_results.py` from the reports on the research Mac. No rule was re-run. Pre-registration: `docs/prereg/trend_momentum.md`. Family in gate v3: cross_sectional_momentum.

- Trials logged: 25 configurations for this rule, 760 for its family, 10,876 in the whole registry.
- The research window is 2017-05-01 to 2024-12-31. A run's own window may go on into what is now the hold-out; those runs were made before it was frozen.
- Gate v3 marks for these runs: [`gate_v3_rescore.md`](gate_v3_rescore.md).
- Pages the run scripts wrote themselves, copied unchanged: [`experiments_core_pit_summary.md`](as_reported/experiments_core_pit_summary.md), [`experiments_core_summary.md`](as_reported/experiments_core_summary.md).

## engine_core, point_in_time

Report `engine_balanced_trend_momentum_pit_20261123T130025.json`, run 2026-11-23T13:00:25+00:00 at code `0743ef0`. Account `trend_momentum|base|1x|cap100|hold`. Engine: simbroker. Universe: point_in_time. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: engine + SimBroker (decision 15): strategy plug-ins, closing-auction orders, cost model v1 with impact as pre-registered; the ledger is the documented stand-in until #117 lands

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 8.80% | 16.91% | 0.62 | 82% | 14.39% / 33.78% | 10.46% / 20.67% | -1.14% (-0.32) | 1.45% (0.34) | 0.54 | 0.20% |
| Research window: 2017-05-01 to 2024-12-31 | 8.80% | 16.91% | 0.62 | 82% | 14.39% / 33.78% | 10.46% / 20.67% | -1.14% (-0.32) | 1.45% (0.34) | 0.54 | 0.20% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 8.80%, Sharpe 0.62, alpha 1.45% (t 0.34). In bills: CAGR 9.25%, Sharpe above bills 0.50, alpha 0.81% (t 0.19). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 92.8 of 2000 runs, p 0.0725; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: pass; 4: fail; 5: fail; 6: fail.

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| trend_momentum|base|2x|cap100|hold | cost | 8.16% | 17.31% | 0.58 | 81.7% |
| trend_momentum|base|3x|cap100|hold | cost | 7.92% | 17.50% | 0.57 | 81.7% |
| trend_momentum|base|0x|cap100|hold | cost | 8.67% | 17.03% | 0.61 | 81.7% |

## core, point_in_time, costs v1

Report `core_balanced_trend_momentum_pit_20261113T084508.json`, run 2026-11-13T08:45:08+00:00 at code `7c0123e`. Account `trend_momentum|base|1x|cap100|hold`. Engine: research_harness. Universe: point_in_time. Cost model: v1. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on the point-in-time S&P 500 (#123) and the daily store (#124: raw prices, dividends, SPY total return), cost model v1; it can fail Gate B, it cannot pass it (the go/no-go run is the engine with `SimBroker`, and cost model v2 is S71)

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 8.24% | 18.27% | 0.59 | 83% | 14.37% / 33.78% | 10.48% / 19.88% | -1.73% (-0.53) | 0.95% (0.26) | 0.54 | 0.20% |
| Research window: 2017-05-01 to 2024-12-31 | 8.28% | 17.46% | 0.59 | 82% | 14.39% / 33.78% | 10.62% / 19.88% | -1.76% (-0.51) | 0.98% (0.23) | 0.54 | 0.20% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 8.28%, Sharpe 0.59, alpha 0.98% (t 0.23). In bills: CAGR 8.72%, Sharpe above bills 0.47, alpha 0.34% (t 0.08). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 78.8 of 500 runs, p 0.2136; kind exposure_matched; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: fail; 3: fail; 4: fail; 5: fail; 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| reg60 | regression window 60 sessions (the 15%-move window stays 90) | 2.71% | -5.57 points | 24.05% | 0.25 | 81% | -7.17% (-2.13) | -4.07% (-1.01) | 0.52 |
| reg125 | regression window 125 sessions (the 15%-move window stays 90) | 5.37% | -2.90 points | 24.66% | 0.41 | 82% | -4.37% (-1.25) | -1.58% (-0.37) | 0.53 |
| exit30 | sell when out of the top 30% (buys still from the top 20%) | 7.19% | -1.08 points | 20.38% | 0.52 | 82% | -2.58% (-0.75) | -0.05% (-0.01) | 0.54 |
| nofilter | without the SPY 200-day market filter | 7.38% | -0.90 points | 24.89% | 0.49 | 94% | -3.66% (-0.95) | -2.59% (-0.68) | 0.75 |
| risk15 | ATR risk 0.0015 of equity per position | 7.76% | -0.51 points | 18.87% | 0.53 | 83% | -2.29% (-0.58) | 0.35% (0.07) | 0.56 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| trend_momentum|base|0x|cap30|hold | cost | 3.31% | 8.49% | 0.58 | 26.2% |
| trend_momentum|base|1x|cap30|hold | exposure | 3.02% | 8.57% | 0.53 | 26.1% |
| trend_momentum|base|2x|cap30|hold | cost | 2.95% | 8.79% | 0.52 | 26.1% |
| trend_momentum|base|3x|cap30|hold | cost | 3.05% | 8.55% | 0.54 | 26.2% |
| trend_momentum|base|0x|cap60|hold | cost | 5.74% | 13.91% | 0.58 | 52.4% |
| trend_momentum|base|1x|cap60|hold | exposure | 5.47% | 14.18% | 0.56 | 52.3% |
| trend_momentum|base|2x|cap60|hold | cost | 5.31% | 13.98% | 0.54 | 52.4% |
| trend_momentum|base|3x|cap60|hold | cost | 5.14% | 14.54% | 0.53 | 52.3% |
| trend_momentum|base|0x|cap100|hold | cost | 8.40% | 18.53% | 0.60 | 83.4% |
| trend_momentum|base|2x|cap100|hold | cost | 7.76% | 18.25% | 0.56 | 83.4% |
| trend_momentum|base|3x|cap100|hold | cost | 7.74% | 18.27% | 0.56 | 83.4% |
| trend_momentum|stress|0x|cap30|hold | cost | 3.09% | 9.51% | 0.55 | 26.2% |
| trend_momentum|stress|1x|cap30|hold | fill | 2.64% | 9.98% | 0.48 | 26.2% |
| trend_momentum|stress|2x|cap30|hold | cost | 2.67% | 9.15% | 0.48 | 26.3% |
| trend_momentum|stress|3x|cap30|hold | cost | 2.48% | 10.30% | 0.45 | 26.1% |
| trend_momentum|stress|0x|cap60|hold | cost | 5.78% | 13.53% | 0.59 | 52.3% |
| trend_momentum|stress|1x|cap60|hold | fill | 5.81% | 12.07% | 0.59 | 52.4% |
| trend_momentum|stress|2x|cap60|hold | cost | 5.42% | 12.68% | 0.56 | 52.3% |
| trend_momentum|stress|3x|cap60|hold | cost | 5.31% | 12.37% | 0.55 | 52.4% |
| trend_momentum|stress|0x|cap100|hold | cost | 8.01% | 18.50% | 0.58 | 83.4% |
| trend_momentum|stress|1x|cap100|hold | fill | 7.46% | 18.70% | 0.55 | 83.4% |
| trend_momentum|stress|2x|cap100|hold | cost | 7.09% | 18.43% | 0.52 | 83.4% |
| trend_momentum|stress|3x|cap100|hold | cost | 6.52% | 20.21% | 0.49 | 83.4% |

## core, today

Report `core_balanced_trend_momentum_20261112T160641.json`, run 2026-11-12T16:06:41+00:00 at code `4dce03a`. Account `trend_momentum|base|1x|cap100|hold`. Engine: research_harness. Universe: today. Cost model: 1.0. SPY series: spy_total_return.

Status as the report states it: PROVISIONAL (decision 15): research harness of #113 on today's stock list, cost model v1, price-only SPY; cannot pass Gate B until S71, S83 and S84 have landed and the test is rerun

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2026-11-06 | 11.14% | 19.24% | 0.74 | 86% | 14.37% / 33.78% | 10.46% / 22.07% | 0.98% (0.30) | 3.22% (0.84) | 0.57 | n/a |
| Research window: 2017-05-01 to 2024-12-31 | 11.96% | 16.28% | 0.78 | 84% | 14.39% / 33.78% | 10.51% / 22.07% | 1.74% (0.49) | 3.95% (0.90) | 0.57 | n/a |

- Idle cash, both versions (research window): bills paid 2.32% a year and 16% of the account was idle. At 0%: CAGR 11.96%, Sharpe 0.78, alpha 3.95% (t 0.90). In bills: CAGR 12.35%, Sharpe above bills 0.66, alpha 3.32% (t 0.76). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 73.0 of 200 runs (NOT matched), p 0.2736; kind not stated; drew from: candidate_pool; drawdown percentile not in the report.
- Gate v2 marks as the report stored them: 1: pass; 2: cannot be judged yet (provisional data); 3: cannot be judged yet (provisional data); 4: cannot be judged yet (provisional data); 5: cannot be judged yet (provisional data); 6: pass.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| reg60 | regression window 60 sessions (the 15%-move window stays 90) | 8.39% | -3.57 points | 19.11% | 0.60 | 83% | -2.34% (-0.70) | 1.09% (0.26) | 0.54 |
| reg125 | regression window 125 sessions (the 15%-move window stays 90) | 10.22% | -1.74 points | 19.71% | 0.68 | 84% | 0.04% (0.01) | 2.47% (0.56) | 0.57 |
| exit30 | sell when out of the top 30% (buys still from the top 20%) | 11.81% | -0.14 points | 16.12% | 0.77 | 85% | 1.46% (0.42) | 3.83% (0.89) | 0.57 |
| nofilter | without the SPY 200-day market filter | 11.54% | -0.42 points | 24.58% | 0.68 | 97% | 0.06% (0.02) | 0.91% (0.23) | 0.78 |
| risk15 | ATR risk 0.0015 of equity per position | 13.79% | +1.83 points | 17.52% | 0.82 | 86% | 3.34% (0.83) | 5.26% (1.09) | 0.61 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| trend_momentum|base|0x|cap30|hold | cost | 4.47% | 8.76% | 0.72 | 26.5% |
| trend_momentum|base|1x|cap30|hold | exposure | 4.39% | 8.76% | 0.71 | 26.5% |
| trend_momentum|base|2x|cap30|hold | cost | 4.30% | 8.79% | 0.69 | 26.5% |
| trend_momentum|base|3x|cap30|hold | cost | 4.30% | 8.85% | 0.69 | 26.5% |
| trend_momentum|base|0x|cap60|hold | cost | 9.07% | 13.26% | 0.85 | 52.8% |
| trend_momentum|base|1x|cap60|hold | exposure | 9.13% | 13.26% | 0.86 | 52.8% |
| trend_momentum|base|2x|cap60|hold | cost | 8.59% | 13.29% | 0.81 | 52.8% |
| trend_momentum|base|3x|cap60|hold | cost | 8.59% | 13.33% | 0.81 | 52.8% |
| trend_momentum|base|0x|cap100|hold | cost | 11.26% | 19.08% | 0.74 | 86.0% |
| trend_momentum|base|2x|cap100|hold | cost | 10.70% | 19.27% | 0.71 | 86.0% |
| trend_momentum|base|3x|cap100|hold | cost | 10.57% | 19.25% | 0.71 | 86.0% |
| trend_momentum|stress|0x|cap30|hold | cost | 4.20% | 8.87% | 0.69 | 26.5% |
| trend_momentum|stress|1x|cap30|hold | fill | 4.35% | 8.89% | 0.70 | 26.5% |
| trend_momentum|stress|2x|cap30|hold | cost | 4.10% | 8.94% | 0.67 | 26.5% |
| trend_momentum|stress|3x|cap30|hold | cost | 4.08% | 8.99% | 0.67 | 26.5% |
| trend_momentum|stress|0x|cap60|hold | cost | 9.04% | 13.52% | 0.85 | 52.8% |
| trend_momentum|stress|1x|cap60|hold | fill | 8.68% | 13.54% | 0.82 | 52.8% |
| trend_momentum|stress|2x|cap60|hold | cost | 8.48% | 13.60% | 0.80 | 52.8% |
| trend_momentum|stress|3x|cap60|hold | cost | 8.24% | 13.88% | 0.78 | 52.8% |
| trend_momentum|stress|0x|cap100|hold | cost | 11.28% | 19.48% | 0.75 | 86.0% |
| trend_momentum|stress|1x|cap100|hold | fill | 10.39% | 19.77% | 0.70 | 86.0% |
| trend_momentum|stress|2x|cap100|hold | cost | 10.22% | 19.62% | 0.69 | 86.0% |
| trend_momentum|stress|3x|cap100|hold | cost | 9.77% | 19.76% | 0.66 | 86.0% |
| trend_momentum|base|0x|cap30|exit_approx | cost | 4.09% | 7.89% | 0.82 | 21.0% |
| trend_momentum|base|1x|cap30|exit_approx | exposure | 4.06% | 8.02% | 0.82 | 21.0% |
| trend_momentum|base|2x|cap30|exit_approx | cost | 3.99% | 7.92% | 0.80 | 21.0% |
| trend_momentum|base|3x|cap30|exit_approx | cost | 3.90% | 8.09% | 0.78 | 21.0% |
| trend_momentum|base|0x|cap60|exit_approx | cost | 6.41% | 13.27% | 0.76 | 41.8% |
| trend_momentum|base|1x|cap60|exit_approx | exposure | 6.24% | 13.65% | 0.73 | 41.8% |
| trend_momentum|base|2x|cap60|exit_approx | cost | 6.15% | 13.76% | 0.73 | 41.8% |
| trend_momentum|base|3x|cap60|exit_approx | cost | 5.87% | 13.38% | 0.70 | 41.8% |
| trend_momentum|base|0x|cap100|exit_approx | cost | 8.44% | 19.67% | 0.69 | 68.0% |
| trend_momentum|base|1x|cap100|exit_approx | variant | 8.17% | 19.82% | 0.67 | 68.1% |
| trend_momentum|base|2x|cap100|exit_approx | cost | 7.90% | 19.72% | 0.65 | 68.0% |
| trend_momentum|base|3x|cap100|exit_approx | cost | 7.47% | 19.84% | 0.62 | 68.0% |
| trend_momentum|stress|0x|cap30|exit_approx | cost | 3.96% | 8.31% | 0.79 | 21.0% |
| trend_momentum|stress|1x|cap30|exit_approx | fill | 3.82% | 8.18% | 0.76 | 21.0% |
| trend_momentum|stress|2x|cap30|exit_approx | cost | 3.64% | 8.46% | 0.73 | 21.0% |
| trend_momentum|stress|3x|cap30|exit_approx | cost | 3.50% | 8.46% | 0.70 | 21.0% |
| trend_momentum|stress|0x|cap60|exit_approx | cost | 6.05% | 13.74% | 0.71 | 41.8% |
| trend_momentum|stress|1x|cap60|exit_approx | fill | 5.68% | 13.73% | 0.67 | 41.8% |
| trend_momentum|stress|2x|cap60|exit_approx | cost | 5.42% | 13.72% | 0.64 | 41.8% |
| trend_momentum|stress|3x|cap60|exit_approx | cost | 5.09% | 13.90% | 0.61 | 41.8% |
| trend_momentum|stress|0x|cap100|exit_approx | cost | 7.87% | 20.15% | 0.65 | 68.0% |
| trend_momentum|stress|1x|cap100|exit_approx | fill | 7.29% | 20.21% | 0.60 | 68.1% |
| trend_momentum|stress|2x|cap100|exit_approx | cost | 6.81% | 21.08% | 0.57 | 68.1% |
| trend_momentum|stress|3x|cap100|exit_approx | cost | 6.10% | 22.55% | 0.52 | 68.1% |

## ?, point_in_time

Report `index_core_trend_momentum_aggressive_pit_20261124T205816.json`, run 2026-11-24T20:58:16+00:00 at code `0945894`. Account `sleeve|1x`. Engine: research_harness. Universe: point_in_time. Cost model: 1.0. SPY series: spy_total_return.

| Window | CAGR | Max drawdown | Sharpe | Mean exposure | SPY: CAGR / drawdown | SPY at the rule's exposure: CAGR / drawdown | Return above it a year (t) | Alpha a year (t) | Beta | Costs a year |
|---|---|---|---|---|---|---|---|---|---|---|
| As run: 2017-05-01 to 2024-12-31 | 8.19% | 17.69% | 0.59 | 82% | 14.39% / 33.78% | 10.49% / 20.62% | -1.72% (-0.50) | 0.91% (0.22) | 0.54 | 0.36% |
| Research window: 2017-05-01 to 2024-12-31 | 8.19% | 17.69% | 0.59 | 82% | 14.39% / 33.78% | 10.49% / 20.62% | -1.72% (-0.50) | 0.91% (0.22) | 0.54 | 0.36% |

- Idle cash, both versions (research window): bills paid 2.32% a year and 18% of the account was idle. At 0%: CAGR 8.19%, Sharpe 0.59, alpha 0.91% (t 0.22). In bills: CAGR 8.64%, Sharpe above bills 0.46, alpha 0.27% (t 0.06). SPY's Sharpe above bills: 0.70.
- Matched random control: percentile 88.6 of 2000 runs, p 0.1144; kind exposure_matched; drew from: candidate_pool; drawdown percentile 99.9.
- Gate v2 marks: none stored in this report.

### Neighbours (the plateau check)

Research window. "Against the frozen rule" is the neighbour's CAGR minus the frozen rule's.

| Neighbour | What it changes | CAGR | Against the frozen rule | Max drawdown | Sharpe | Mean exposure | Return above SPY at its exposure (t) | Alpha (t) | Beta |
|---|---|---|---|---|---|---|---|---|---|
| reg60 | regression window 60 sessions (the 15%-move window stays 90) | 3.23% | -4.96 points | 23.68% | 0.29 | 81% | -6.43% (-1.92) | -3.68% (-0.93) | 0.53 |
| reg125 | regression window 125 sessions (the 15%-move window stays 90) | 6.52% | -1.67 points | 24.90% | 0.48 | 82% | -3.25% (-0.93) | -0.53% (-0.12) | 0.53 |
| exit30 | sell when out of the top 30% (buys still from the top 20%) | 7.52% | -0.68 points | 19.49% | 0.54 | 82% | -2.37% (-0.69) | 0.26% (0.06) | 0.54 |
| nofilter | without the SPY 200-day market filter | 7.99% | -0.21 points | 25.13% | 0.52 | 94% | -3.00% (-0.78) | -2.04% (-0.54) | 0.75 |
| risk15 | ATR risk 0.0015 of equity per position | 8.70% | +0.51 points | 17.53% | 0.58 | 83% | -1.36% (-0.35) | 1.19% (0.26) | 0.57 |

### Other accounts of the same report (the report's own numbers)

| Account | Role | CAGR | Max drawdown | Sharpe | Mean exposure |
|---|---|---|---|---|---|
| sleeve|2x | cost | 7.85% | 18.09% | 0.57 | 81.7% |
