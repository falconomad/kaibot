# Pre-registration: `news_continuation`

- **Rule:** `news_continuation`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1, today's stock list; it can fail the intraday bar, it cannot pass it)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 2 of 6)

## Hypothesis

After fresh company news, a stock that has moved up strongly on heavy volume and then pulls back without losing
the session VWAP keeps drifting up to the close. Expectation written before the run: about zero net of costs;
anything positive and significant is a bonus.

## Who is on the other side of the trade

Investors who under-react to news that arrives during or just before the session (they sell into the first
pullback or have not read it yet), and liquidity providers who sell the pullback expecting a reversal.

## Published evidence

Chan (2003): stocks with public news continue rather than reverse. LLM-scored headline studies: the drift runs
from about 15 minutes after release to the close, while the first 1-15 minutes are insignificant, hence the
15-minute minimum news age. No independent long-only, large-cap, net-of-cost test of this construction exists.
Kaibot's own memo: `docs/debate/v2_eng_quant.md` section 1; research summary in
`docs/research/engine_v3/notes_intraday_overnight_strategies.md` section 4 (stock-level intraday continuation is
weak outside the first minutes) and `docs/research/engine_v3/report.md`.

## Frozen settings

- Construction: `src/kaibot/strategies/news_continuation.py` as committed (`NewsParams`): a headline with
  `created_at` in [16:00 ET of the prior session, decision time - 15 minutes]; up-move from the prior close of at
  least 1.0 x the 20-session daily sigma; cumulative volume at least 3 x the 20-session average at the same
  minute; price above the session VWAP; a 5-minute pullback whose low is below the previous 5-minute low, holds
  the VWAP and closes up; decisions at 5-minute bar closes from minute 9 to 374; stop 1.5 x ATR of 14 five-minute
  bars below the entry reference; no target; exit at the 15:55 close. One signal per symbol per day (the first).
- Universe: the stocks of `config/universe_history.yaml` with minute bars (`data/bars_1m`) and stored headlines
  (`data/news`). Today's list (survivorship bias): former members have neither, so the point-in-time universe is
  not possible here.
- Months with complete news history only. A calendar month counts as complete when all of: (a) at least 90% of
  the universe stocks with minute bars that month have at least one stored headline in it; (b) the month's
  headline count is at least half the median monthly count over all months of the store; (c) the month ends on
  or before the day of the last stored headline. Signals in other months are not counted. The complete months are
  listed in every report as date ranges.
- Sizing: each trade gets 10% of the start-of-day equity; at most 10 trades a day (by decision minute, then the
  universe file's order). Long only, never above 1x.
- No index filter, no earnings rule (nothing held overnight).

## Neighbours (at most five)

- `move15`: up-move of at least 1.5 x the daily sigma instead of 1.0.
- `relvol2`: relative volume of at least 2 instead of 3.
- `relvol5`: relative volume of at least 5 instead of 3.
- `stop1`: stop 1.0 x the 5-minute ATR instead of 1.5.
- `stop2`: stop 2.0 x the 5-minute ATR instead of 1.5.

## Fill model

Decide at the close of minute m; buy at the open of minute m + 1 at half a spread plus impact (cost model v1,
`replay/sim.py` `buy_px`); the stop is re-anchored on the fill (fill minus the same distance), as the live shadow
book does; a stop fills at the stop or at a minute's open below it; otherwise sold at the 15:55 close; sales pay
half a spread, impact and the regulatory fees (`sell_px`, `sell_fees`).

## Pass marks

The intraday bar of `config/gate.yaml`, which decision 15 section 7 keeps for intraday rules, as committed with
this file: at least 900 trades; per-trade net t-statistic above 3; at or above the 95th percentile of 1,000
random-entry runs (same symbol-days as the rule's trades, entry at a uniformly random 5-minute decision minute of
the rule's window, the same stop distance and exit; the statistic is the mean net R per trade); daily Sharpe of
the rule's account above SPY's over the same sessions. The deflated Sharpe of the bar is computed from the trial
registry (`scripts/trial_report.py`) and not in this report, so this report can fail the rule but never pass it.
Also reported: mean net R, years positive, the neighbours (each must have positive mean net R to count as a
plateau).
