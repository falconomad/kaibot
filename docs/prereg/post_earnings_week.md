# Pre-registration: `post_earnings_week`

- **Rule:** `post_earnings_week`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1; it can fail Gate B, it cannot pass it)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 3 of 6; needs S85, #125)

## Hypothesis

Stocks whose price reacts most positively to an earnings release keep drifting up for about a week (days +2 to
+6 after the announcement). Expectation written before the run: about zero alpha in S&P 500 stocks net of costs
(classic drift has been gone in large caps since about 2006); the short window is the only part with recent
support. "No better than SPY" is a legitimate finding.

## Who is on the other side of the trade

Investors who under-react to the earnings news (slow re-pricing of a surprise) and holders who sell into the
post-announcement rally to lock in the gain (the disposition effect).

## Published evidence

Kettell, McInnis and Zhao (2022, 1974-2020): the drift in trading days +2 to +6 has strengthened over time
(t 3.92) while the later window declined. Martineau (Critical Finance Review): drift in large stocks non-existent
since 2006. Subrahmanyam (2025): not significant once microcaps are excluded (2001-2024). Brandt et al.: ranking
on the announcement-window return (EAR) works without analyst data. Written up in
`docs/research/engine_v3/notes_multi_day_strategies.md` section 3 (the inference "enter at the close of day +1 or
+2, hold about one week, rank by the announcement-window price reaction" is this construction).

## Frozen settings

- Universe: the point-in-time S&P 500 (#123): a stock can be bought only on a decision day when it is a member and
  passes the harness filter (close >= $5, 20-day average dollar volume >= $50M, raw prices). Bars from the daily
  store (#124): signals on split-adjusted closes without dividends; orders, costs and share counts on raw prices;
  dividends credited.
- Announcements: SEC 8-K Item 2.02 filings, the `report` rows of `data/earnings/announcements.parquet` (S85, #125).
  Day 0 = the first regular session that opens after the filing was accepted (`session`), so a decision after the
  close of day 0 can know it. When `data/earnings` is missing the run is skipped with a message.
- Reaction: EAR = the stock's return from the close of day -1 to the close of day 0 minus SPY's return over the
  same close-to-close, both split-adjusted.
- Selection on the decision day k = day 0: EAR at or above the 80th percentile of the EARs of every announcement
  whose day 0 lies in the 252 sessions up to and including k (point in time; fewer than 100 such announcements:
  no buy), and EAR > 0.
- Entry: decide after the close of day 0, buy at the close of day +1. Exit: sell at the close of day +6 (five
  sessions held, days +2 to +6). No stop.
- Slots: at most 10 names, each sized to 10% of equity (whole shares, from the previous close), under the level's
  maximum position (Balanced 15%); more candidates than free slots: highest EAR first. A name already held is not
  bought again.
- Cash reserve (Amendment 3 of the momentum pre-registrations, applied here too): buys are sized against cash
  minus 3% of equity; a buy still above the cash at the fill is cut to the whole shares that fit.
- No index filter. Earnings: not applicable (the rule trades the announcement). Level Balanced, 100% exposure cap.
- Account: one continuous $100,000 account from 2017-05 to the end of the data, starting flat.

## Neighbours (at most five)

- `day2`: decide after the close of day +1, buy at the close of day +2, sell at the close of day +7.
- `hold10`: hold 10 sessions instead of 5.
- `top10pct`: EAR at or above the 90th percentile instead of the 80th.
- `slots20`: at most 20 names at 5% of equity each.

## Fill model

BASE (decision 15): decide after the close of T, buy and sell at the close of T + 1 at half a spread a side plus
impact (cost model v1, `multiday.Engine`). STRESS: the open of T + 1 with spread and impact doubled. Costs 1x, 2x,
3x and a 0x run for the break-even multiple. A name without a bar on the fill day is not bought; a sale waits for
the next bar. No stops, so "a stop gapped through fills at the open" does not apply.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` as committed together with this file (decision 15 section 7, marks
1-6, with the section 10 clarifications), judged on the base version (base fill, 1x costs, 100% cap) on the
point-in-time universe, with SPY total return from the daily store and the exposure-matched random control (500
runs). Results stay provisional (the go/no-go run is the engine with `SimBroker`): the one-shot can fail, it
cannot pass.
