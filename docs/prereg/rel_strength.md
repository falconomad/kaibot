# Pre-registration: rel_strength

**Retroactive, first look, cannot pass Gate B.** Written on 12 November 2026, after the rule was built and first
run in issue #113. It records what was built so that the run can be logged with a hash. It is not evidence that
the settings were chosen before the data was seen.

- **Rule:** `rel_strength`
- **Status:** retroactive, first look, cannot pass Gate B
- **Date:** 2026-11-12
- **Author:** qa-ops, from backend-quant's definition in `src/kaibot/replay/multiday_rules.py` (issue #113)
- **Issue:** #113 (rule), #126 (this file)

## Hypothesis

Stocks with the highest return over the past six months, skipping the latest month, keep outperforming over the
following weeks, and holding them only while SPY is above its 200-day average avoids the worst of a falling
market. Decision 15 expects a first look only: "not a verdict".

## Who is on the other side of the trade

Investors who react slowly to good news and holders who sell winners early. Not verified for this
construction.

## Published evidence

Stock momentum (12-month return skipping the latest month) is the best-replicated price anomaly, but the
premium is about half as large after publication, weaker in large caps, and long-only large-cap versions have
been roughly index-like over 2016-2026. See `docs/research/engine_v3/notes_multi_day_strategies.md`, section 1.
This rule uses a 6-month lookback and weekly replacement, which is faster than the published monthly form.

## Frozen settings

As built in #113; the fixed account uses these values:

- Universe: the stocks of `config/universe_history.yaml` (today's list applied back to 2016), price at least $5,
  20-session average dollar volume at least $50M, both as of the day before entry.
- Signal: on each week's first session, rank by the return from 126 sessions ago to 21 sessions ago (six months,
  skipping the latest month) and hold the top 5.
- Entry and exit: at that session's open; a stock that leaves the top 5 is sold at the open; everything is sold
  when SPY closed the previous session under its 200-day average; a position is sold after 60 sessions at the
  latest.
- Sizing: equal risk (a 3-ATR gap costs at most the level's risk per trade), each name at most a fifth of the
  60% exposure cap; long only, no leverage. Level: Balanced.
- Not frozen in #113: the "tuned" account re-chooses among six settings every month. Only the fixed account
  matches these settings.

## Neighbours (at most five)

The five other settings of the #113 grid:

- `rel_strength_months3_top_n5`: 3-month return, top 5
- `rel_strength_months3_top_n10`: 3-month return, top 10
- `rel_strength_months6_top_n10`: 6-month return, top 10
- `rel_strength_months9_top_n5`: 9-month return, top 5
- `rel_strength_months9_top_n10`: 9-month return, top 10

## Fill model

The research harness of #113, not the engine with `SimBroker`: buys and sells at the open print of the first
regular-hours minute plus or minus half a spread and impact (cost model v1); the 60-session exit is at the close
print. This is not the base fill model of decision 15 (close of T+1 at half a spread).

## Pass marks

The Gate B marks of `config/gate_v2.yaml` are computed and shown, but this rule cannot pass: the
pre-registration is retroactive, and the run is on the research harness, cost model v1, today's stock list and
price-only SPY, which decision 15 labels provisional.
