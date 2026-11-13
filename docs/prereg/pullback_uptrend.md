# Pre-registration: `pullback_uptrend`

- **Rule:** `pullback_uptrend`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1; it can fail Gate B, it cannot pass it)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 4 of 6)

## Hypothesis

A stock in a long-term uptrend that has just had a sharp two-day pullback tends to bounce within days (buy short-
term losers among long-term winners). Expectation written before the run: about zero net of costs against SPY;
the published figures are unaudited blog numbers without costs. The failure mode is a regime where dips keep
falling (2020, 2022).

## Who is on the other side of the trade

Short-term sellers who demand liquidity after a sharp drop (forced or nervous sellers), paying the patient buyer a
small reversal premium.

## Published evidence

Connors' RSI-2 rule set as tested on Nasdaq 100 constituents, Dec 2006 to 2025, 4 slots: 17.8% a year, Sharpe
1.10, no costs, unaudited; CXO Advisory: Connors' tests exclude costs and lack out-of-sample checks. Academic
short-term reversal: unconditional one-month reversal is destroyed by costs (Novy-Marx and Velikov); conditional
versions in liquid, low-volatility, uptrending names survive better. Written up in
`docs/research/engine_v3/notes_multi_day_strategies.md` section 4.

## Frozen settings

- Universe: the point-in-time S&P 500 (#123), harness filter (close >= $5, 20-day average dollar volume >= $50M),
  daily store (#124): signals on split-adjusted closes without dividends; orders, costs, shares on raw prices;
  dividends credited.
- Signal on the close of day k (the published rule): close above its 200-day simple average; close below its
  5-day simple average; RSI(2) below 10. RSI(2) is Wilder's: gains and losses of the close-to-close changes,
  each averaged by Wilder smoothing with period 2 (the first average is the simple mean of the first two),
  RSI = 100 - 100 / (1 + average gain / average loss), 100 when the average loss is zero.
- Entry: buy at the open of k + 1. More signals than free slots: the lowest RSI(2) first (published), ties in
  column order. A name already held is not bought again.
- Exit: a close above its 5-day simple average on day k; sell at the open of k + 1. No stop, no maximum holding
  period (published).
- Slots: 4 (published). Each is sized to a quarter of equity (whole shares, from the previous close), under the
  level's maximum position (Balanced 15%), so at most 60% is invested: Kaibot's level cap binds, stated here.
- Cash reserve: buys are sized against cash minus 3% of equity (Amendment 3 of the momentum pre-registrations).
- No index filter (the 200-day condition is per stock). Earnings: hold through. Level Balanced, 100% exposure cap.
- Account: one continuous $100,000 account from 2017-05 to the end of the data, starting flat.

## Neighbours (at most five)

- `rsi5`: RSI(2) below 5 instead of 10.
- `rsi15`: RSI(2) below 15 instead of 10.
- `slots10`: 10 slots at 10% of equity each.
- `no_sma5`: without the "close below the 5-day average" entry condition.
- `sma150`: a 150-day trend average instead of 200.

## Fill model

BASE for this rule (issue #112: realistic next-open fills, as published): decide after the close of T, buy and
sell at the open of T + 1 at half a spread a side plus impact (cost model v1). STRESS: the open of T + 1 with
spread and impact doubled. Costs 1x, 2x, 3x and a 0x run for the break-even multiple. A name without a bar on the
fill day is not bought; a sale waits for the next bar. No stops.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` as committed together with this file (decision 15 section 7, marks
1-6, section 10), judged on the base version on the point-in-time universe, with SPY total return and the
exposure-matched random control (500 runs). Results stay provisional: the one-shot can fail, it cannot pass.
