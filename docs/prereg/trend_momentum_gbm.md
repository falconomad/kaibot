# Pre-registration: trend_momentum_gbm

- **Rule:** `trend_momentum_gbm`
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #474 (M12 ticket 3.12)
- **Role:** sleeve
- **Kind:** selection
- **Family:** `cross_sectional_momentum` (one more rule in the family: its Bonferroni factor grows by one for every later verdict)
- **Candidate pool:** the names `trend_momentum` itself could buy that day (in its top 20%, buyable, with the market filter on), before any ordering. The random control draws from them only, so S3 asks whether the model's ORDER beats random order among the same names
- **Tier:** full (decision 16): the published evidence for machine-learned ranking in large, liquid US stocks is small (below), so this rule must pass S1 to S6

Pre-registration only, committed before any code that runs it and before any run. It is a non-LLM ranking overlay,
allowed only as a pre-registered rule with fixed settings and counted trials (Product's decision D6, 21 Nov 2026,
`docs/research/engine_audit/decisions.md`). Changed only by appending dated "## Amendment n" sections.
**Dependency, stated:** the model is scikit-learn's `HistGradientBoostingRegressor`, which is not yet a declared
dependency of the repository. Adding it (with a hash-pinned lock, ticket 3.6) is part of the code pull request, and
Backend-quant and QA-Ops give two keys for it. The code cannot run before that.

## Hypothesis

Inside `trend_momentum`, ordering the week's buy candidates by a small gradient-boosted model of their next 21
sessions' return, instead of by the regression score alone, earns more than the same amount of SPY **and** more
than `trend_momentum` itself. Expected, net of costs, on the research window: an active return against
path-matched SPY between -2 and +2 points a year, and against `trend_momentum` between -1 and +1 point a year. The
benchmark study puts machine-learned return prediction at 0.3% to 0.7% of monthly return variation, concentrated
in small and hard-to-trade stocks, which this universe excludes. **A null result is the expected one.** A backtest
Sharpe above 1.0 is read as a data leak, not as skill (decision 15 section 7), and stops the analysis until the leak is
found.

## Who is on the other side of the trade

The same as for `trend_momentum` (investors who under-react, funds that rebalance away from winners). The model adds
no new counterparty. It can only help if the order inside the momentum list carries information that the
regression score misses.

## Published evidence

- Gu, Kelly and Xiu (2020, *Empirical Asset Pricing via Machine Learning*, Review of Financial Studies): trees and
  neural networks beat linear models in predicting US stock returns, but the gain is small (about 0.3% to 0.7% of
  monthly variation) and concentrated in small stocks (via the audit). Cited from memory, not re-read for this file.
- The audit (`docs/research/engine_audit/report.md`, "Honest uses of AI and trained models") recommends exactly this
  form: a small ranking model with fixed settings, judged against random selection from the same candidates.
- Against it: published anomalies lose about half their return after publication, more in large stocks (Chen and
  Zimmermann, via the audit); a flexible model on 7.7 years of weekly decisions overfits easily.

## Frozen settings

| # | Setting | Value |
|---|---|---|
| 1 | The rule underneath | `trend_momentum` as frozen in docs/prereg/trend_momentum.md, amended to 22 Nov 2026: universe, filters, weekly decisions, the top 20% to enter and to stay, the 100-day and 15% conditions, the SPY 200-day filter, the sizing (k 0.1%, 15% per name, 30 names, 40% sector, 3% reserve), sales and resizes. **Unchanged** |
| 2 | What the model changes | **only the order of the new buys** in the week's list (best first, which the ranked fill buys until the room runs out). Which names may be bought, held or sold is the rule's, not the model's |
| 3 | Features, per stock and decision day, each ranked across the day's stocks (0 to 1), from closes at or before the decision close | the return over 21 sessions; over 126 sessions skipping 21; over 252 sessions skipping 21; the 63-session volatility of daily returns; the close over its 100-session average; the close over its 252-session high; the rule's regression score; the 20-session average dollar volume (8 features) |
| 4 | Target | the next 21 sessions' return minus SPY's, ranked across the day's stocks |
| 5 | Training rows | every decision day (`momentum.is_weekly_decision`) and every stock of the ranked universe of that day, from the first day with all features, with a target that **ends at least 21 sessions before the refit day** (a 21-session embargo) |
| 6 | Model | `sklearn.ensemble.HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_depth=3, min_samples_leaf=200, l2_regularization=1.0, early_stopping=False, random_state=474)`; no tuning |
| 7 | Refit | on the first decision day of each calendar year, on every training row available then (expanding window). Until the first refit with at least 52 decision days of rows, the order is the regression score's (as `trend_momentum`), and the report says on how many sessions that held |
| 8 | Ties and missing | a candidate with a missing feature gets the model's prediction for the remaining ones (the model handles missing values natively); equal predictions keep the regression score's order |
| 9 | Orders, fills, costs, account, window, universe | as docs/prereg/trend_momentum_band.md rows 11 and 12: near-close orders at the closing print by `SimBroker`, the engine's daily print model (v2 once #572 has merged; the report states which), one $100,000 account, data from 2016-01-04, research window 2017-05-01 to 2024-12-31, point-in-time store; the hold-out is never read |
| 10 | Control | the exposure-matched random control (decision 15 section 10) drawn from the candidate pool above, **2,000 runs** (decision 16 section 15), seed 474 |

## Neighbours (at most five)

Each must have an active return above zero (mark S6):

- `depth2`: `max_depth=2`
- `lr10`: `learning_rate=0.10`
- `noscore`: the seven features without the rule's regression score
- `refit6m`: refit every six months (the first decision days of January and July)
- `ridge`: a ridge regression (`sklearn.linear_model.Ridge(alpha=1.0)`) on the same features, instead of the boosted trees

## Fill model

As `trend_momentum` (docs/prereg/trend_momentum.md, Amendments 10 to 13): decide after the close of T, near-close
DAY market orders at 15:50 ET of T + 1, filled at the closing print by `SimBroker`. The stress fill is reported. The
model's fitting and prediction run after the close of T, on data up to T only.

## Pass marks

The sleeve test of decision 16 (sections 4 and 15), all of S1 to S6. In addition, stated now and judged with it:
**the rule must beat `trend_momentum`** on the same window, data and costs, with an annualised mean of the daily
return difference above zero (mark `vs_rule`). A model that does not add to the simple rule it sits in is not worth
its complexity. The final check is that of decision 16 section 7. A backtest Sharpe above 1.0 makes the report
`suspect: data leak` and blocks any verdict until it is explained in an amendment.

**Trials.** Six configurations count (n_trials 1): the rule and its five neighbours. Every refit is part of its
configuration's run, not a new trial. **Every other run of the model counts too**, because no tuning is allowed: a
debugging run on real data, a re-run with changed code, or a feature check on the research window is logged with
n_trials 1. Only runs on synthetic data or the committed fixture are not trials.

## Amendment 1 (22 Nov 2026, QA-Ops review of #576, before any code and any run): the correction, the hold-out and the model's reach, stated

Written before the code that runs this rule exists and before any run. No setting, feature, neighbour or mark
changes; this states three effects the file left implicit.

1. **Correction.** The rule joins the existing family `cross_sectional_momentum`, so the number of families in
   Holm's correction across families (decision 16 section 6: 12) does not change. Inside the family, Bonferroni
   multiplies the family's smallest p-value by the number of member rules, which this file raises by one. That
   applies to every verdict given after this file is merged; no verdict or trial row already recorded is re-scored
   (decision 16 section 6).
2. **Hold-out.** Sessions from 2025-01-01 on (`holdout_from` in config/holdout.yaml) are never read by the training,
   the refits, the neighbours or any debugging run. The only read is decision 16 section 7's final check, under an
   opening appended to config/holdout.yaml before that run, and only after every research-window mark holds.
3. **What the model may not do.** The model's output is a number per candidate, used only to order the new buys of
   row 2. It never sets or changes a price, a size, a stop, an exit, a sale, a resize or the set of names the rule
   allows; those stay exactly as frozen in row 1 (and CLAUDE.md: no model output sets price, size, stop or exit).
   The model is not an LLM, and no LLM output enters the features, the training or the order.
