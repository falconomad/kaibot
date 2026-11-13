# Pre-registration: `ath_breakout`

- **Rule:** `ath_breakout`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1; it can fail Gate B, it cannot pass it)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 5 of 6)

## Hypothesis

Stocks that close at an all-time high keep trending often enough that a long-only book that buys them and rides
a wide trailing stop earns a positive, highly skewed return. Expectation written before the run: with 5 to 20
unlevered positions the result depends on catching the few trades that carry the profit, so the spread across
signal subsets is very wide and the median is about SPY or less. The result is a distribution, never one curve.

## Who is on the other side of the trade

Investors anchored to old highs who sell into new highs (the 52-week-high anchoring effect), and value-minded
holders who take profits early.

## Published evidence

Wilcox and Crittenden (2005): buy a close at or above the all-time high, 10-ATR trailing stop, results
insensitive to 8-12 ATRs. Zarattini, Pagani and Wilcox (2025): entry at the next open after an all-time-high
close, exit at the next open after a close below a ratcheting stop on a 42-day ATR; fewer than 7% of trades
make the profit; the portfolio result uses a 30% volatility target and up to 2x leverage, so it does not transfer
to 5-20 unlevered positions. Written up in `docs/research/engine_v3/notes_multi_day_strategies.md` section 2
("Breakout / all-time-high trend following"; the inference "report the distribution over random subsets of
signals, not one equity curve" is this test).

## Frozen settings

- Universe: the point-in-time S&P 500 (#123), harness filter (close >= $5, 20-day average dollar volume >= $50M)
  plus the published price floor (raw close >= $10); daily store (#124): signals on split-adjusted closes without
  dividends; orders, costs and shares on raw prices; dividends credited.
- Signal on the close of day k: the close is at or above the highest close of every earlier session in the data
  (the store starts in 2016, so "all-time" means since the first stored bar, with at least 252 earlier sessions).
- Entry: buy at the open of k + 1. A name already held is not bought again.
- Exit: a ratcheting trailing stop = the highest close since entry minus 10 x ATR(42) of that day (simple average
  of the true range over 42 sessions, at least 30 with a bar), raised only, never lowered; a close below it on
  day k sells at the open of k + 1. No target, no maximum holding period.
- Positions: equal weight, at most K names each sized to 1/K of equity (whole shares, from the previous close),
  under the level's maximum position (Balanced 15%). K = 5, 10 and 20 are reported; K = 10 is the base version.
- More signals than free slots: a uniformly random subset, drawn with a seeded generator. 200 seeds per K (seeds
  0..199). Reported per K: the 5th, 25th, 50th, 75th and 95th percentiles of CAGR, Sharpe and max drawdown over
  the seeds, and the share of seeds that pass Gate B mark 1.
- Cash reserve: buys are sized against cash minus 3% of equity. No index filter. Earnings: hold through.
  Level Balanced, 100% exposure cap. Each seed is one continuous $100,000 account from 2017-05, starting flat.

## Neighbours (at most five)

- `trail8`: trailing stop 8 x ATR(42) instead of 10.
- `trail12`: trailing stop 12 x ATR(42) instead of 10.
- `atr14`: ATR over 14 sessions instead of 42.
- `price5`: without the $10 price floor (the harness's $5 only).

## Fill model

BASE for this rule (published): decide after the close of T, buy and sell at the open of T + 1 at half a spread a
side plus impact (cost model v1). STRESS: the open of T + 1 with spread and impact doubled. Costs 1x, 2x, 3x and
0x for the break-even multiple, each over the same seeds at K = 10. A name without a bar on the fill day is not
bought; a sale waits for the next bar. The stop is evaluated on the close, so it never fills intraday.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` as committed together with this file (decision 15 section 7, marks
1-6, section 10), judged on the seed whose base-version Sharpe is the median of the 200 at K = 10 (the 100th of
200 by Sharpe), with SPY total return and the exposure-matched random control (500 runs) on that seed. Stricter,
promised here: at least half of the 200 seeds at K = 10 must pass mark 1, and every neighbour's median seed must
pass mark 1. Results stay provisional: the one-shot can fail, it cannot pass.
