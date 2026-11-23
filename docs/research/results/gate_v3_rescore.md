# Gate v3 re-score of the existing rule reports

Generated 2026-11-23T12:27:54+00:00 by `scripts/gate_v3_rescore.py` from 28 reports. No rule was re-run. Marks are those of decision 16 (`docs/decisions/16_gate_v3.md`), judged on 2017-05-01 to 2024-12-31; later sessions were dropped before any number was computed from the daily files. The exception is the control's p-value (S3, the Holm column and the families table): it is the earlier report's own figure over that report's whole window. Where that window ran into the hold-out the p-value is marked with a dagger (†): it has seen hold-out sessions and is not a research-window number.

**This is a re-score, not a verdict.** Every rule below was pre-registered before decision 16, so none can pass gate v3; `provisional` means every mark that could be scored holds. Gate v2's verdicts stand as they were given.

Idle cash: Treasury bills on both sides. Trial registry: 10,847 configurations in all.

| Rule | Run | Exposure | Active return a year | 90% interval | t | At 2x costs | Control p | Holm-adjusted | Deflated Sharpe (N) | S1 | S2 | S3 | S4 | S5 | S6 | Gate v3 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ath_breakout | oneshots, point_in_time | 95% | -5.52% | -12.98% to 1.48% | -1.26 | -5.55% | 0.9062† | n/a | 0.000 (1,045) | FAIL | FAIL | ? | FAIL | FAIL | FAIL | fail |
| ath_breakout_k20 | oneshots, point_in_time | 95% | -2.85% | -8.73% to 3.00% | -0.76 | -2.88% | n/a | n/a | 0.000 (1,045) | FAIL | FAIL | ? | FAIL | FAIL | ? | fail |
| ath_breakout_k5 | oneshots, point_in_time | 81% | -4.51% | -12.07% to 2.78% | -0.99 | -4.55% | n/a | n/a | 0.000 (1,045) | FAIL | FAIL | ? | FAIL | FAIL | ? | fail |
| mom_12_1_band | core, point_in_time, costs v1 | 75% | 0.63% | -4.88% to 6.18% | 0.17 | 0.58% | 0.2555† | n/a | 0.001 (748) | pass | pass | ? | FAIL | FAIL | FAIL | fail |
| mom_12_1_band | core, today | 78% | 4.69% | -1.23% to 10.66% | 1.16 | n/a | 0.1891† | n/a | 0.020 (748) | pass | ? | ? | FAIL | ? | pass | fail |
| mom_12_1_band_nofilter | core, point_in_time, costs v1 | 80% | 0.58% | -5.28% to 6.53% | 0.15 | 0.52% | 0.2695† | n/a | 0.001 (748) | pass | pass | ? | FAIL | FAIL | FAIL | fail |
| mom_12_1_band_nofilter | core, today | 83% | 4.65% | -1.72% to 11.02% | 1.07 | n/a | 0.1095† | n/a | 0.017 (748) | pass | ? | ? | FAIL | ? | pass | fail |
| news_gap_week | oneshots, point_in_time | 11% | -0.77% | -3.01% to 1.46% | -0.52 | -0.99% | 0.1178† | n/a | 0.008 (12) | FAIL | FAIL | ? | FAIL | FAIL | FAIL | fail |
| overnight_spy | multiday, today | 12% | -1.72% | -2.60% to -0.82% | -2.71 | n/a | n/a | n/a | 0.000 (346) | FAIL | ? | ? | FAIL | ? | ? | fail |
| post_earnings_week | oneshots, point_in_time | 36% | -0.18% | -4.14% to 3.91% | -0.07 | -1.00% | 0.2295† | n/a | 0.072 (7) | FAIL | FAIL | ? | FAIL | FAIL | FAIL | fail |
| pullback_uptrend | oneshots, point_in_time | 53% | 1.08% | -4.83% to 6.79% | 0.34 | -0.31% | 0.5409† | n/a | 0.065 (18) | pass | FAIL | ? | FAIL | FAIL | FAIL | fail |
| rel_strength | multiday, today | 17% | 1.95% | -0.51% to 4.59% | 1.25 | n/a | n/a | n/a | 0.029 (748) | pass | ? | ? | FAIL | ? | ? | fail |
| spy_noise_band | spy, point_in_time, 0.5 bp | 0% | 3.93% | 1.97% to 6.00% | 3.00 | n/a | 0.0020† | n/a | 0.828 (14) | pass | ? | ? | FAIL | FAIL | pass | fail |
| spy_noise_band | spy, point_in_time, 1 bp | 0% | 2.76% | 0.81% to 4.83% | 2.10 | n/a | n/a | n/a | 0.545 (14) | pass | ? | ? | FAIL | FAIL | pass | fail |
| spy_noise_band | spy, point_in_time, 2 bp | 0% | 0.42% | -1.54% to 2.48% | 0.32 | n/a | n/a | n/a | 0.071 (14) | pass | ? | ? | FAIL | FAIL | pass | fail |
| spy_noise_band | spy, point_in_time, model v1 | 0% | 1.09% | -0.88% to 3.17% | 0.83 | n/a | n/a | n/a | 0.153 (14) | pass | ? | ? | FAIL | FAIL | pass | fail |
| spy_noise_band_carry | spy, point_in_time, 0.5 bp | 17% | 3.00% | 0.12% to 6.02% | 1.75 | n/a | 0.0080† | n/a | 0.411 (14) | pass | ? | ? | FAIL | FAIL | ? | fail |
| spy_noise_band_carry | spy, point_in_time, 1 bp | 17% | 1.83% | -1.05% to 4.85% | 1.07 | n/a | n/a | n/a | 0.207 (14) | pass | ? | ? | FAIL | FAIL | ? | fail |
| spy_noise_band_carry | spy, point_in_time, 2 bp | 17% | -0.51% | -3.36% to 2.52% | -0.29 | n/a | n/a | n/a | 0.023 (14) | FAIL | ? | ? | FAIL | FAIL | ? | fail |
| spy_noise_band_carry | spy, point_in_time, model v1 | 17% | 0.16% | -2.70% to 3.19% | 0.09 | n/a | n/a | n/a | 0.048 (14) | pass | ? | ? | FAIL | FAIL | ? | fail |
| spy_trend_filter | spy_trend, point_in_time, costs v2 | 82% | -3.60% | -9.08% to 2.07% | -0.99 | -3.64% | 0.8842† | 1.0000 | 0.014 (6) | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | fail |
| spy_trend_filter | spy_trend, point_in_time, costs v1 | 82% | -3.59% | -9.08% to 2.07% | -0.99 | -3.63% | 0.8842† | 1.0000 | 0.014 (6) | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | fail |
| trend_highs | multiday, today | 29% | -3.07% | -5.43% to -0.85% | -1.96 | n/a | n/a | n/a | 0.000 (1,045) | FAIL | ? | ? | FAIL | ? | ? | fail |
| trend_momentum | core, point_in_time, costs v1 | 82% | -1.76% | -6.88% to 3.90% | -0.51 | -1.95% | 0.2136† | n/a | 0.000 (748) | FAIL | FAIL | ? | FAIL | FAIL | FAIL | fail |
| trend_momentum | core, today | 84% | 1.74% | -3.57% to 7.57% | 0.49 | n/a | 0.2736† | n/a | 0.004 (748) | pass | ? | ? | FAIL | ? | FAIL | fail |
| weekly_fall_rebound | oneshots, point_in_time | 21% | 4.40% | -5.05% to 15.59% | 0.72 | 3.67% | 0.0559† | n/a | 0.136 (18) | pass | pass | ? | FAIL | FAIL | FAIL | fail |
| weekly_fall_rebound_20 | oneshots, point_in_time | 54% | 0.57% | -10.12% to 10.41% | 0.09 | 0.13% | 0.8723† | n/a | 0.039 (18) | pass | pass | ? | FAIL | FAIL | ? | fail |
| winners_run | multiday, today | 11% | -14.57% | -18.23% to -11.06% | -6.89 | n/a | n/a | n/a | 0.000 (8,632) | FAIL | ? | ? | FAIL | ? | ? | fail |

## Why a mark is unknown or failed

- **ath_breakout** (oneshots, point_in_time): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.000 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: trail8: active return -7.28% a year; trail12: active return -5.65% a year; atr14: active return -8.71% a year; price5: active return -5.52% a year.
- **ath_breakout_k20** (oneshots, point_in_time): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: no matched random control was supplied; S4: deflated Sharpe 0.000 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: trail8: no result supplied; trail12: no result supplied; atr14: no result supplied; price5: no result supplied.
- **ath_breakout_k5** (oneshots, point_in_time): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: no matched random control was supplied; S4: deflated Sharpe 0.000 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: trail8: no result supplied; trail12: no result supplied; atr14: no result supplied; price5: no result supplied.
- **mom_12_1_band** (core, point_in_time, costs v1): S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.001 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: only12: active return -0.25% a year; only6: active return -4.05% a year.
- **mom_12_1_band** (core, today): S2: no daily cost series was supplied; S3: 200 runs, 2000 needed; S4: deflated Sharpe 0.020 is below 0.9; S5: no run on the point-in-time universe was supplied.
- **mom_12_1_band_nofilter** (core, point_in_time, costs v1): S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.001 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: only6: active return -3.41% a year.
- **mom_12_1_band_nofilter** (core, today): S2: no daily cost series was supplied; S3: 200 runs, 2000 needed; S4: deflated Sharpe 0.017 is below 0.9; S5: no run on the point-in-time universe was supplied.
- **news_gap_week** (oneshots, point_in_time): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.008 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: move2: active return -0.09% a year; relvol2: active return -5.00% a year; info8k: active return -1.86% a year.
- **overnight_spy** (multiday, today): S1: the active return is not above zero; S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.000 is below 0.9; S5: no run on the point-in-time universe was supplied; S6: overnight_spy_vix_max0: no result supplied; overnight_spy_vix_max25: no result supplied.
- **post_earnings_week** (oneshots, point_in_time): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: the control drew from universe, not from the rule's own candidate pool; S4: deflated Sharpe 0.072 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: day2: active return -2.06% a year; hold10: active return -2.32% a year; top10pct: active return -0.59% a year; slots20: active return -0.29% a year.
- **pullback_uptrend** (oneshots, point_in_time): S2: the active return is not above zero at 2x costs; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.065 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: rsi15: active return -0.30% a year; slots10: active return -0.26% a year.
- **rel_strength** (multiday, today): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.029 is below 0.9; S5: no run on the point-in-time universe was supplied; S6: rel_strength_months3_top_n5: no result supplied; rel_strength_months3_top_n10: no result supplied; rel_strength_months6_top_n10: no result supplied; rel_strength_months9_top_n5: no result supplied; rel_strength_months9_top_n10: no result supplied.
- **spy_noise_band** (spy, point_in_time, 0.5 bp): S2: no daily cost series was supplied; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.828 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe.
- **spy_noise_band** (spy, point_in_time, 1 bp): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.545 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe.
- **spy_noise_band** (spy, point_in_time, 2 bp): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.071 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe.
- **spy_noise_band** (spy, point_in_time, model v1): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.153 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe.
- **spy_noise_band_carry** (spy, point_in_time, 0.5 bp): S2: no daily cost series was supplied; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.411 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: no pre-registration and no neighbour was supplied.
- **spy_noise_band_carry** (spy, point_in_time, 1 bp): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.207 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: no pre-registration and no neighbour was supplied.
- **spy_noise_band_carry** (spy, point_in_time, 2 bp): S1: the active return is not above zero; S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.023 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: no pre-registration and no neighbour was supplied.
- **spy_noise_band_carry** (spy, point_in_time, model v1): S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.048 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: no pre-registration and no neighbour was supplied.
- **spy_trend_filter** (spy_trend, point_in_time, costs v2): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: family p 0.8842 is above 0.0091 (Holm, 12 families, level 0.1); S4: deflated Sharpe 0.014 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: sma150d: active return -0.73% a year; sma200d: active return -2.13% a year; sma250d: active return -2.09% a year; weekly: active return -1.61% a year.
- **spy_trend_filter** (spy_trend, point_in_time, costs v1): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: family p 0.8842 is above 0.0091 (Holm, 12 families, level 0.1); S4: deflated Sharpe 0.014 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: sma150d: active return -0.73% a year; sma200d: active return -2.13% a year; sma250d: active return -2.09% a year; weekly: active return -1.61% a year.
- **trend_highs** (multiday, today): S1: the active return is not above zero; S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.000 is below 0.9; S5: no run on the point-in-time universe was supplied; S6: trend_highs_high_n20_trail_atr2: no result supplied; trend_highs_high_n20_trail_atr3: no result supplied; trend_highs_high_n20_trail_atr4: no result supplied; trend_highs_high_n55_trail_atr2: no result supplied; trend_highs_high_n55_trail_atr4: no result supplied; trend_highs_high_n100_trail_atr2: no result supplied; trend_highs_high_n100_trail_atr3: no result supplied; trend_highs_high_n100_trail_atr4: no result supplied.
- **trend_momentum** (core, point_in_time, costs v1): S1: the active return is not above zero; S2: the active return is not above zero at 2x costs; S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.000 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: reg60: active return -7.17% a year; reg125: active return -4.37% a year; exit30: active return -2.58% a year; nofilter: active return -3.66% a year; risk15: active return -2.29% a year.
- **trend_momentum** (core, today): S2: no daily cost series was supplied; S3: 200 runs, 2000 needed; S4: deflated Sharpe 0.004 is below 0.9; S5: no run on the point-in-time universe was supplied; S6: reg60: active return -2.34% a year.
- **weekly_fall_rebound** (oneshots, point_in_time): S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.136 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: market_filter: active return -3.31% a year.
- **weekly_fall_rebound_20** (oneshots, point_in_time): S3: 500 runs, 2000 needed; S4: deflated Sharpe 0.039 is below 0.9; S5: S1 to S4 do not all hold on the point-in-time universe; S6: fall10: no result supplied; fall20: no result supplied; slots5: no result supplied; market_filter: no result supplied.
- **winners_run** (multiday, today): S1: the active return is not above zero; S2: no daily cost series was supplied; S3: no matched random control was supplied; S4: deflated Sharpe 0.000 is below 0.9; S5: no run on the point-in-time universe was supplied; S6: winners_run_hold3: no result supplied; winners_run_hold10: no result supplied.

## Rule families and the correction

Holm's step-down at a family-wise level of 0.1 over 12 families. A family's p-value is the smallest control p-value of its rules times the number of rules it lists; a family without a usable control counts as 1.

| Family | Rules | Family p | Holm-adjusted | Clears the level |
|---|---|---|---|---|
| intraday_dip_reversal | dip_reversal, winners_run | n/a | n/a | no |
| cross_sectional_momentum | trend_momentum, trend_momentum_gbm, mom_12_1_band, trend_momentum_band, mom_12_1_band_nofilter, rel_strength | 1.0000 | 1.0000 | no |
| new_highs_breakout | trend_highs, ath_breakout | n/a | n/a | no |
| spy_intraday_momentum | spy_noise_band, spy_noise_band_carry, spy_late_momentum | 0.0060 | 0.0719 | yes |
| overnight_premium | overnight_spy | n/a | n/a | no |
| index_trend_timing | spy_trend_filter | 0.8842 | 1.0000 | no |
| opening_range_breakout | orb_stocks_in_play | n/a | n/a | no |
| news_drift | news_continuation, news_gap_week | n/a | n/a | no |
| earnings_drift | post_earnings_week | n/a | n/a | no |
| short_term_reversal | pullback_uptrend, weekly_fall_rebound | 1.0000 | 1.0000 | no |
| filing_flags | filing_flags_study | n/a | n/a | no |
| market_event_alarm | market_alarm_study | n/a | n/a | no |

## Runs that could not be scored

| Rule | Run | Report | Why |
|---|---|---|---|
| control | walkforward, today | `walkforward_balanced_wf_A_exp-control_20261112T112013.json` | the daily file has no exposure column |
| high_vix | walkforward, today | `walkforward_balanced_wf_A_exp-high_vix_20261112T115122.json` | the daily file has no exposure column |
| mornings | walkforward, today | `walkforward_balanced_wf_A_exp-mornings_20261112T113047.json` | the daily file has no exposure column |
| mornings_wide_stop | walkforward, today | `walkforward_balanced_wf_A_exp-mornings_wide_stop_20261112T120228.json` | the daily file has no exposure column |
| news_continuation | oneshots, today | `oneshots_news_continuation_20261113T085255.json` | the daily file has no exposure column |
| orb_stocks_in_play | oneshots, today | `oneshots_orb_stocks_in_play_20261113T085035.json` | the daily file has no exposure column |
| spy_noise_band | spy_engine, point_in_time, 0.5 | `spy_engine_balanced_20261121T172654.json` | the daily file has no exposure column |
| spy_noise_band | spy_engine, point_in_time, 1 | `spy_engine_balanced_20261121T172654.json` | the daily file has no exposure column |
| spy_noise_band | spy_engine, point_in_time, v2 | `spy_engine_balanced_20261121T172654.json` | the daily file has no exposure column |
| wide_stop | walkforward, today | `walkforward_balanced_wf_A_exp-wide_stop_20261112T114103.json` | the daily file has no exposure column |

gate_v3.yaml sha256 `e55a96ee62e9e66690b8561da85c9a6d4e5a7565b1c49f19bf689a4f18cf23e4`.
