# Pre-registration: `orb_stocks_in_play`

- **Rule:** `orb_stocks_in_play`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1, today's stock list; it can lead to deletion, it cannot put the rule on paper)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 1 of 6: keep or delete)

## Hypothesis

On a day when a large stock trades unusually heavily in its first five minutes and the first five-minute candle is
up, a break above that candle's high keeps going for the rest of the session often enough to pay for the entry.
Expectation written before the run: long only and unlevered, after a spread, impact and a pessimistic stop-entry
fill, about zero or negative per trade. Anything that clears the keep marks below is a bonus; otherwise the rule
and its shadow code are proposed for deletion.

## Who is on the other side of the trade

Traders who fade the opening move (liquidity providers selling into the breakout), and holders who sell into the
first rally after news. The rule needs late buyers who arrive after the first five minutes and keep pushing
until the close. Market makers are paid by the spread and by the stop-entry slippage that this test charges.

## Published evidence

Zarattini, Barbon and Aziz (2024), "A Profitable Day Trading Strategy For The U.S. Equity Market" (SFI 24-98):
top-20 relative-volume 5-minute ORB, 2016-2023, long and short, up to 4x, commission only: Sharpe 2.81; average
trade -0.02R below 100% relative volume, +0.08R above it; three names contributed more than 300R each.
Independent replications: Sharpe about 1.4 over 2016-2024, "not worth it" against SPY; measured stop-entry
slippage on a similar tight-stop ORB 0.47R median against a break-even of about 0.23R. Written up in
`docs/research/engine_v3/notes_intraday_overnight_strategies.md` section 1 and "Rank 3" (the test design used
here), and `docs/research/engine_v3/report.md`.

## Frozen settings

- Construction: `src/kaibot/strategies/orb.py` (`OrbParams` as committed: 5-minute range, 14-session lookback,
  opening price > $5, 14-session average volume >= 1,000,000 shares, ATR(14) > $0.50, relative volume >= 1.0,
  top 20 by relative volume, stop 0.10 x ATR(14)). Down-candle names keep their place among the 20, as in the
  paper, and are not traded (long only).
- Universe: the stocks of `config/universe_history.yaml` with SIP minute bars in `data/bars_1m`, 2016-01-01 to
  the end of the data. Today's list (survivorship bias): former members have no minute history, so the
  point-in-time universe is not possible for this rule. Stated in every report.
- Relative volume only from data Kaibot has at decision time: the ranking reads the consolidated minutes
  09:30-09:34, which the free SIP feed delivers at least 15 minutes late, so the ranking exists at 09:50 ET.
  Orders are placed at 09:50 (minute 20) and never earlier.
- Entry: one buy-stop per traded name at the opening-range high (highest high 09:30-09:34), resting from 09:50 to
  15:44 ET (minute 374). It triggers in the first minute from 09:50 whose high reaches the stop; a minute that
  opens at or above the stop triggers at its open.
- Exit: protective stop 0.10 x ATR(14) below the fill (1R = 0.10 x ATR(14) per share), checked from the fill
  minute on; a stop fills at the stop or at a minute's open below it; otherwise sold at the 15:55 ET close.
- Sizing: long only, 1x. Each trade gets 1/20 of the start-of-day equity, so the book never exceeds 1x. The
  per-trade statistics are in R and do not depend on the account; the order quantity (for the impact term) is
  that of a $100,000 account.
- One trade per symbol per day. No index filter, no earnings rule (intraday: nothing is held overnight). Level
  Balanced for the cost model only; the risk gate's intraday sizing is not used (1x sizing above instead).

## Neighbours (at most five)

- `top10`: the top 10 by relative volume instead of 20.
- `top40`: the top 40 by relative volume instead of 20.
- `stop20`: the stop at 0.20 x ATR(14) instead of 0.10 (1R doubles with it).

## Fill model

Cost model v1 (`kaibot.broker.shadow_costs` through `replay/sim.py` `buy_px` / `sell_px` / `sell_fees`).
Pessimistic stop-entry fill (research note, "Rank 3"): the fill price before costs is the worse (higher) of the
trigger price (the stop, or the trigger minute's open when it opened above the stop) and the next minute's high;
half a spread plus impact are added on top. Stops fill at the stop or at the minute's open below it, as a market
sell; the time exit sells at the 15:55 close; both pay half a spread, impact and the regulatory fees. Reported
beside it, never instead of it: the same trades filled at the trigger price (what a backtest without slippage
shows), and gross R before any cost.

## Pass marks

Keep only if ALL of (issue #112):

1. net expectancy of at least +0.05R per trade with a t-statistic of at least 3 (per-trade net R, base settings);
2. still positive (total net R > 0) after removing every trade of the ten symbols with the largest total net R.

Otherwise the verdict is "propose deletion": the rule, its shadow wiring and its tests are removed in a
follow-up story (the deletion touches `src/kaibot/engine/shadow.py` and `config/profiles.yaml`, which this story
does not edit). Reported, not judged: years with positive net R, the result without the top 1% of trades, the
neighbours, the trigger-price fill, gross R. Long-only is the only side tested, so the research note's
"long-only at least half of long-short" check does not apply. Nothing here can put the rule on paper: that
still needs the intraday bar of `config/gate.yaml` on the engine with `SimBroker`.
