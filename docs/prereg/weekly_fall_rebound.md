# Pre-registration: `weekly_fall_rebound`

- **Rule:** `weekly_fall_rebound`
- **Status:** pre-registered (every result PROVISIONAL: research harness of #113, cost model v1; it can fail Gate B, it cannot pass it)
- **Date:** 2026-11-13
- **Author:** backend-quant
- **Issue:** #112 (story S72, one-shot test 6 of 6: the stakeholder's earlier question, "buy a big stock after its worst week")

## Hypothesis

A large stock that has had one of the worst five-session falls in the index (bottom decile, at least 15% down)
rebounds over the next week or month. **Low prior, stated before the run:** the reversal literature says the
unconditional version is about zero or negative after costs; a fall this large is often news (an earnings miss, a
guidance cut) that keeps drifting down. Expectation: about zero or below SPY net of costs.

## Who is on the other side of the trade

Sellers who demand liquidity after a sharp fall (forced selling, stop-outs, index or mandate exits), paying a
short-term reversal premium; and better-informed sellers when the fall is news, in which case the rule loses.

## Published evidence

Novy-Marx and Velikov: one-month reversal gross 0.37% a month (t 1.71), net of costs -1.28% a month. de Groot,
Huij and Zhou: weekly reversal in the 100 largest US stocks, long-short, a net 0.29% a week at broker costs.
Blitz et al.: single short-term signals net about -2% a year at 25 bp per trade. Written up in
`docs/research/engine_v3/notes_multi_day_strategies.md` section 4 (and the cost arithmetic there: about 52 round
trips a year leave no margin for the unconditional version).

## Frozen settings

- Universe: the point-in-time S&P 500 (#123), harness filter (close >= $5, 20-day average dollar volume >= $50M);
  daily store (#124): signals on split-adjusted closes without dividends; orders, costs and shares on raw prices;
  dividends credited. "Large stock" = an index member that passes the filter.
- Signal on the close of day k: the five-session return close(k) / close(k - 5) - 1 is in the bottom decile of the
  stocks that pass the filter that day and have the return (percentile rank at most 10%), and is at most -15%.
- Entry: decide after the close of k, buy at the close of k + 1. More candidates than free slots: the worst
  five-session return first. A name already held is not bought again.
- Exit: sell at the close of k + 1 + H. Two pre-declared versions: H = 5 sessions (`weekly_fall_rebound`, the
  base) and H = 20 sessions (`weekly_fall_rebound_20`). No stop.
- Slots: at most 10 names at 10% of equity each (whole shares, from the previous close), under the level's
  maximum position (Balanced 15%).
- Cash reserve: buys are sized against cash minus 3% of equity. No index filter. Earnings: hold through.
  Level Balanced, 100% exposure cap. One continuous $100,000 account per version from 2017-05, starting flat.

## Neighbours (at most five)

- `fall10`: at least a 10% fall instead of 15%.
- `fall20`: at least a 20% fall instead of 15%.
- `slots5`: at most 5 names at 20% of equity each (capped at 15% by the level).
- `market_filter`: new buys only while SPY closes above its 200-day average.

## Fill model

BASE (decision 15, "next-close fills"): decide after the close of T, buy and sell at the close of T + 1 at half a
spread a side plus impact (cost model v1). STRESS: the open of T + 1 with spread and impact doubled. Costs 1x, 2x,
3x and 0x for the break-even multiple. A name without a bar on the fill day is not bought; a sale waits for the
next bar. No stops.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` as committed together with this file (decision 15 section 7, marks
1-6, section 10), judged on each version's base run (base fill, 1x costs, 100% cap) on the point-in-time
universe, with SPY total return and the exposure-matched random control (500 runs). Neighbours are run around
the 5-session version. Results stay provisional: the one-shot can fail, it cannot pass.
