# Pre-registration: trend_highs

**Retroactive, first look, cannot pass Gate B.** Written on 12 November 2026, after the rule was built and first
run in issue #113. It records what was built so that the run can be logged with a hash. It is not evidence that
the settings were chosen before the data was seen.

- **Rule:** `trend_highs`
- **Status:** retroactive, first look, cannot pass Gate B
- **Date:** 2026-11-12
- **Author:** qa-ops, from backend-quant's definition in `src/kaibot/replay/multiday_rules.py` (issue #113)
- **Issue:** #113 (rule), #126 (this file)

## Hypothesis

A stock that closes at a new 55-session high, in an uptrend and stronger than SPY, tends to keep rising for
some weeks. Decision 15 expects a first look only: "not a verdict".

## Who is on the other side of the trade

Holders who sell into a new high because they anchor on the old one or want to lock in a gain. Not verified
for this construction.

## Published evidence

Breakout and all-time-high rules on single stocks show a positive but highly skewed edge in backtests (fewer
than 7% of trades make the money), and the published results rely on hundreds of positions and leverage. No
peer-reviewed test was found of channel breakouts on single US stocks, long only, with five positions. See
`docs/research/engine_v3/notes_multi_day_strategies.md`, section 2.

## Frozen settings

As built in #113; the fixed account uses these values:

- Universe: the stocks of `config/universe_history.yaml` (today's list applied back to 2016), price at least $5,
  20-session average dollar volume at least $50M, both as of the day before entry.
- Signal: the close is above the highest close of the previous 55 sessions, above its 200-day average, and the
  20-day return is above SPY's. Strongest first when slots are short.
- Entry: next session's open. Exit: a stop 3 daily ATRs (14 sessions) under the fill, trailed up to 3 ATRs
  under the highest close; out after 40 sessions.
- Sizing: a 3-ATR gap against the position costs at most the level's risk per trade; at most 5 positions and
  60% of equity; long only, no leverage. Level: Balanced.
- Not frozen in #113: the "tuned" account re-chooses among nine settings every month. Only the fixed account
  matches these settings.

## Neighbours (at most five)

The #113 grid has eight other settings, more than the five allowed. That alone fails Gate A.

- `trend_highs_high_n20_trail_atr2`: 20-session high, 2 ATR
- `trend_highs_high_n20_trail_atr3`: 20-session high, 3 ATR
- `trend_highs_high_n20_trail_atr4`: 20-session high, 4 ATR
- `trend_highs_high_n55_trail_atr2`: 55-session high, 2 ATR
- `trend_highs_high_n55_trail_atr4`: 55-session high, 4 ATR
- `trend_highs_high_n100_trail_atr2`: 100-session high, 2 ATR
- `trend_highs_high_n100_trail_atr3`: 100-session high, 3 ATR
- `trend_highs_high_n100_trail_atr4`: 100-session high, 4 ATR

## Fill model

The research harness of #113, not the engine with `SimBroker`: buy at the open print of the first
regular-hours minute plus half a spread and impact (cost model v1); a stop fills at the stop when the day's low
reaches it and at the open when the open is already below it; the 40-session exit is at the close print. This is
not the base fill model of decision 15 (close of T+1 at half a spread).

## Pass marks

The Gate B marks of `config/gate_v2.yaml` are computed and shown, but this rule cannot pass: the
pre-registration is retroactive, the neighbours exceed five, and the run is on the research harness, cost model
v1, today's stock list and price-only SPY, which decision 15 labels provisional.
