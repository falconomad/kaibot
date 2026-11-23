# Research results on record

Written 2026-11-23T12:27:51+00:00 by `scripts/export_results.py` from 28 reports under `data/reports` on the research Mac. No rule was re-run. One page per pre-registration; the JSON beside each page holds every number.

Trial registry: 10,847 configurations in 434 rows. Research window: 2017-05-01 to 2024-12-31. Gate v3 marks: [`gate_v3_rescore.md`](gate_v3_rescore.md).

| Rule | Pre-registered | Family | Runs on record | Newest report | Headline run: CAGR / max drawdown / mean exposure / alpha (t), research window |
|---|---|---|---|---|---|
| [ath_breakout](ath_breakout.md) | yes | new_highs_breakout | 3 | `oneshots_ath_breakout_20261113T085319.json` | oneshots, point_in_time: 7.11% / 26.36% / 95% / -1.43% (-0.34) |
| [control](control.md) | no | - | 1 | `walkforward_balanced_wf_A_exp-control_20261112T112013.json` | walkforward, today: -13.51% / 67.73% / n/a / -15.90% (-8.05) |
| [dip_reversal](dip_reversal.md) | yes | intraday_dip_reversal | 0 | - | no report on this machine |
| [filing_flags_study](filing_flags_study.md) | yes | filing_flags | 0 | - | no report on this machine |
| [high_vix](high_vix.md) | no | - | 1 | `walkforward_balanced_wf_A_exp-high_vix_20261112T115122.json` | walkforward, today: -2.00% / 17.40% / n/a / -2.57% (-2.79) |
| [market_alarm_study](market_alarm_study.md) | yes | market_event_alarm | 0 | - | no report on this machine |
| [mom_12_1_band](mom_12_1_band.md) | yes | cross_sectional_momentum | 4 | `core_balanced_mom_12_1_band_pit_20261113T084643.json` | core, point_in_time, costs v1: 10.25% / 24.96% / 75% / 0.16% (0.04) |
| [mornings](mornings.md) | no | - | 1 | `walkforward_balanced_wf_A_exp-mornings_20261112T113047.json` | walkforward, today: -6.99% / 43.72% / n/a / -8.18% (-5.08) |
| [mornings_wide_stop](mornings_wide_stop.md) | no | - | 1 | `walkforward_balanced_wf_A_exp-mornings_wide_stop_20261112T120228.json` | walkforward, today: -2.68% / 21.00% / n/a / -3.50% (-3.37) |
| [news_continuation](news_continuation.md) | yes | news_drift | 1 | `oneshots_news_continuation_20261113T085255.json` | oneshots, today: -1.64% / 19.88% / n/a / -1.92% (-1.60) |
| [news_gap_week](news_gap_week.md) | yes | news_drift | 1 | `oneshots_news_gap_week_20261121T171857.json` | oneshots, point_in_time: 1.18% / 13.94% / 11% / 0.19% (0.11) |
| [orb_stocks_in_play](orb_stocks_in_play.md) | yes | opening_range_breakout | 1 | `oneshots_orb_stocks_in_play_20261113T085035.json` | oneshots, today: -16.28% / 74.38% / n/a / -18.02% (-33.50) |
| [overnight_spy](overnight_spy.md) | yes | overnight_premium | 1 | `walkforward_balanced_md_overnight_spy_20261112T142006.json` | multiday, today: 0.10% / 3.78% / 12% / -0.25% (-0.78) |
| [post_earnings_week](post_earnings_week.md) | yes | earnings_drift | 1 | `oneshots_post_earnings_week_20261113T085630.json` | oneshots, point_in_time: 5.51% / 24.41% / 36% / 0.27% (0.07) |
| [pullback_uptrend](pullback_uptrend.md) | yes | short_term_reversal | 1 | `oneshots_pullback_uptrend_20261113T085258.json` | oneshots, point_in_time: 8.98% / 27.14% / 53% / 3.46% (1.04) |
| [rel_strength](rel_strength.md) | yes | cross_sectional_momentum | 1 | `walkforward_balanced_md_rel_strength_20261112T142003.json` | multiday, today: 4.68% / 7.75% / 17% / 3.02% (1.64) |
| [spy_late_momentum](spy_late_momentum.md) | yes | spy_intraday_momentum | 0 | - | no report on this machine |
| [spy_noise_band](spy_noise_band.md) | yes | spy_intraday_momentum | 7 | `spy_engine_balanced_20261121T172654.json` | spy_engine, point_in_time, 0.5: 0.48% / 0.50% / n/a / 0.38% (2.83) |
| [spy_noise_band_carry](spy_noise_band_carry.md) | no | spy_intraday_momentum | 4 | `spy_balanced_spy_noise_band_carry_20261113T084929.json` | spy, point_in_time, 0.5 bp: 5.56% / 9.85% / 17% / 3.96% (2.41) |
| [spy_trend_filter](spy_trend_filter.md) | yes | index_trend_timing | 2 | `spy_trend_20261113T112924.json` | spy_trend, point_in_time, costs v2: 8.24% / 27.51% / 82% / 1.24% (0.34) |
| [trend_highs](trend_highs.md) | yes | new_highs_breakout | 1 | `walkforward_balanced_md_trend_highs_20261112T141956.json` | multiday, today: 0.49% / 17.60% / 29% / -1.83% (-1.06) |
| [trend_momentum](trend_momentum.md) | yes | cross_sectional_momentum | 2 | `core_balanced_trend_momentum_pit_20261113T084508.json` | core, point_in_time, costs v1: 8.28% / 17.46% / 82% / 0.98% (0.23) |
| [trend_momentum_band](trend_momentum_band.md) | yes | cross_sectional_momentum | 0 | - | no report on this machine |
| [trend_momentum_gbm](trend_momentum_gbm.md) | yes | cross_sectional_momentum | 0 | - | no report on this machine |
| [weekly_fall_rebound](weekly_fall_rebound.md) | yes | short_term_reversal | 2 | `oneshots_weekly_fall_rebound_20261113T085648.json` | oneshots, point_in_time: 7.41% / 47.63% / 21% / -0.86% (-0.12) |
| [wide_stop](wide_stop.md) | no | - | 1 | `walkforward_balanced_wf_A_exp-wide_stop_20261112T114103.json` | walkforward, today: -4.53% / 30.62% / n/a / -5.67% (-4.63) |
| [winners_run](winners_run.md) | yes | intraday_dip_reversal | 1 | `walkforward_balanced_md_winners_run_20261112T143008.json` | multiday, today: -12.59% / 65.26% / 11% / -15.46% (-7.02) |

Pages the run scripts wrote themselves are copied unchanged into [`as_reported/`](as_reported/): `experiments_core_pit_summary.md`, `experiments_core_summary.md`, `experiments_multiday_summary.md`, `experiments_oneshots_summary.md`, `experiments_spy_engine.md`, `experiments_spy_summary.md`, `experiments_spy_trend_summary.md`, `experiments_summary.md`, `filing_flags_study.md`, `market_alarm_backfill.md`.

Files in `data/reports` that hold no account and were not read: `filing_flags_study.json`, `market_alarm_backfill.json`, `trial_report.json`, `walkforward_balanced_wf_A_20261112T102028.json`.
