# Pre-registration: `mom_12_1_band` (banded 12-1 momentum, rule 2 of decision 15)

- **Date:** 12 November 2026. Committed before any code that runs this rule (issue #127, story S87).
- **Status of every result from this document:** PROVISIONAL. The test runs in the research harness of #113 on
  today's stock list (`config/universe_history.yaml`, survivorship bias), with cost model v1 and a price-only SPY
  (bars are split-adjusted and carry no dividends, for the rule and for the benchmarks). It cannot pass Gate B
  until S71 (cost model v2), S83 (point-in-time universe) and S84 (raw bars, corporate actions, total return)
  have landed and the test has been rerun, and until the go/no-go run comes from the engine with `SimBroker`.
- **Research only.** Nothing in the engine, the risk gate, the broker, the strategies or the config changes.

## 1. Hypothesis, and who is on the other side

Stocks in the top tenth by return over the past 6 to 12 months, skipping the latest month, keep outperforming for
some months (Jegadeesh and Titman momentum). A stricter rule to enter than to stay (enter in the top 10%, stay
until out of the top 20%) keeps most of the premium at about half the turnover (Novy-Marx and Velikov), and a
blend of lookbacks avoids betting on one lucky window. Expectation written before the run (research estimate, net
of costs): SPY minus 1 to plus 2 points a year, multi-year lags likely, drawdown similar or deeper than SPY's
without the market filter. "No better than holding SPY" is a legitimate finding.

The other side of the trade: investors who under-react to news and sell winners early, and benchmarked money
that rebalances away from what has risen. The premium halved after publication, is weakest in large caps, and in
2016-2026 mostly measured concentration in the mega-cap leaders that also drove the index.

Sources: `docs/research/engine_v3/report.md` ("Multi-day: trend-filtered momentum is the best-supported core")
and `docs/research/engine_v3/notes_multi_day_strategies.md` section 1.

## 2. Frozen construction (not tuned; published settings)

A decision for session `i` reads sessions `<= i - 1` only.

| Part | Setting |
|---|---|
| Decision day | the first session of each month; data up to the previous close |
| Score | average of the cross-sectional percentile ranks of three returns: months t-6..t-2, t-9..t-2 and t-12..t-2, that is the close 21 sessions ago divided by the close 126 / 189 / 252 sessions ago, minus 1 |
| Universe | the harness's filter: price >= $5 and 20-day average dollar volume >= $50M, point-in-time |
| Enter | stocks in the top 10% by score, best first, up to 20 names |
| Exit | a holding is kept until it falls out of the top 20% |
| Position size | shares = equity x 0.001 / ATR(20 sessions), whole shares; capped by the level's maximum position percent (Balanced: 15% of equity) and by the exposure cap; never more than the cash (no leverage) |
| Two pre-declared versions | `mom_12_1_band` with the SPY 200-day market filter for new buys (holdings are kept until their own exit), and `mom_12_1_band_nofilter` without it |
| Account | one continuous account from the first test month 2017-05 to the end of the data, starting flat with $100,000, level Balanced; no walk-forward tuning |

### Choices where the published rule is silent (made here, before any run)

1. Closes are the harness's closes (last regular-hours minute, split-adjusted, no dividends); a session without a
   bar carries the last close for at most 5 sessions. A month is 21 sessions.
2. A stock is ranked only when all three returns exist (about 273 sessions of history) and it passes the
   universe filter on the decision's data. The percentile rank of a return is its rank divided by the number of
   ranked stocks (ties share the average rank).
3. "Top 10%" and "top 20%" are the first `ceil(0.1 x N)` and `ceil(0.2 x N)` names by score; ties keep the
   universe file's order.
4. A holding that drops out of the ranked universe (no score, or fails price or liquidity) counts as "out of the
   top 20%" and is sold.
5. Exits, like entries, are checked on the decision day only (monthly). There is no stock-level trend or gap
   filter, no stop and no maximum holding period.
6. A position is sized once, at entry; it is not resized while held (the published banded rule has no resize).
7. ATR(20) is the simple average of the true range over 20 sessions (at least 15 with a bar). Sizing equity is
   the account's equity marked at the previous close.
8. A buy that does not fit in full under the exposure cap is cut to the room left (whole shares) and buying
   stops there. A stock whose size works out below one share is skipped.
9. Sale proceeds of a decision are available for its purchases at the same print.
10. The market filter is SPY's close above its 200-day simple average on the decision's data; a missing average
    counts as "filter off" (no buys).

## 3. Neighbours for the plateau check (five, declared now; each must pass mark 1)

| Neighbour | Change from the frozen rule |
|---|---|
| `only12` | score from the 12-1 return only (t-12..t-2) |
| `only6` | score from the 6-1 return only (t-6..t-2) |
| `exit30` | exit when out of the top 30% |
| `names10` | at most 10 names |
| `risk15` | ATR risk 0.0015 of equity per position |

Neighbours run for both versions at the base fill, 1x costs, 100% exposure cap, hold through earnings. They are
a plateau check, not a menu: no neighbour replaces the frozen rule.

## 4. Fill model

- **BASE:** decide after the close of T, buy and sell at the close of T+1, at half a spread a side plus the
  model's impact (cost model v1: `sim.buy_px` / `sim.sell_px`, fees from `shadow_costs.sell_fees`).
- **STRESS:** buy and sell at the open of T+1 with spread and impact doubled.
- A holding without a bar on the fill day waits: its sale fills on the next session that has a bar. A buy
  without a bar on the fill day is dropped.
- No stops exist in this rule, so "a stop gapped through fills at the open" does not apply.

## 5. Reporting grid (reports of ONE frozen rule per version, not a tuning grid; every run is logged as a trial)

- Fill model: base, stress.
- Costs: 1x, 2x, 3x of cost model v1 (the multiplier scales spread and impact; regulatory fees stay as they
  are), plus a 0x run used only to state the break-even cost multiple.
- Exposure cap: 30%, 60%, 100% of equity (at most 20 names; never leverage).
- Earnings: "hold through earnings" is the base. "Exit before earnings" is an APPROXIMATE variant on the
  harness's estimated earnings window (`kaibot.replay.earnings`), because point-in-time earnings dates do not
  exist yet (S85); it is skipped with a message when the estimate fails its own coverage check.
- The **base version** for the Gate B marks, for each of the two versions: base fill, 1x costs, 100% exposure
  cap, hold through earnings.

## 6. Benchmarks (same sessions, in every table)

- SPY buy-and-hold, **price only** (total return once S84 has landed).
- SPY scaled to the rule's average exposure (the rest in cash at 0%, no costs).
- The equal-weight universe (rebalanced monthly, after costs; carries the same survivorship bias).
- Matched random entries: same decision dates, same number of new entries per date, same sizing and caps, exits
  drawn from the rule's own holding-period distribution, random stocks from the same universe and liquidity
  filter instead of ranked ones; at least 200 seeded runs (500 for a Gate B verdict). Reported: the percentile
  of the rule's net Sharpe and CAGR among the runs.

## 7. Pass marks (Gate B of decision 15, section 7, copied)

All of:

1. net return positive overall and in at least 6 of 10 calendar years;
2. **either** CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, **or** CAGR >= SPY + 1
   point with max drawdown <= 1.1 x SPY's;
3. net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter,
   exposure and holding-period distribution, >= 500 runs);
4. marks 1-2 still hold at 2x costs (3x and break-even reported);
5. holds on the point-in-time universe, not only on today's list;
6. every pre-declared neighbour passes mark 1.

Statistics are on daily mark-to-market returns; a 50% haircut is quoted beside every Sharpe; a backtested Sharpe
above about 1.0 is a warning sign. Marks 2 (SPY total return) and 5 (point-in-time universe) cannot be judged on
today's data; the report says so instead of printing a verdict.

## Amendment 1 (12 Nov 2026, Product, before any run on real data)

Written after the synthetic end-to-end run and before the first run on the stakeholder's history; no real result had been seen.

1. **Break-even cost multiple is informational, not a pass mark.** The committed formula P0/(P0-P1) is kept and reported, with the fixed-trades cross-check beside it. On synthetic data it is unstable, because a different cost changes whole-share counts and therefore the trades. Gate B mark 4 is unchanged: marks 1 and 2 must still hold at 2x costs.
2. **The random-control Sharpe percentile is provisional.** The control matches entries but not exposure, so its Sharpe percentile is biased low. Both the Sharpe and CAGR percentiles are reported. Gate B mark 3 is judged only with the exposure-matched control of #126. Until then it reads "cannot be judged yet".

## Amendment 2 (12 Nov 2026, Product, before any point-in-time run)

For the `--universe pit` run, daily bars come from the daily bar store, which holds the official daily close (the closing-auction price) instead of the last regular-hours minute's close. Base fills "at the close of T+1" therefore use the official close. That is the price a closing-auction order receives, so it is the more faithful fill. Signals still use split-adjusted prices without dividends. The `--universe today` reports keep the minute-bar close, so earlier results reproduce. A rule that passes on one close definition and fails on the other is reported as fragile.

## Amendment 3 (12 Nov 2026, Product, before any engine run or point-in-time run on real data)

**A cash reserve, so that "never more than the cash" holds in the real auction.** An order sent before the closing auction cannot know its fill price. On the last buy of a decision, the harness trimmed the order to the cash left at the fill price; a real broker cannot do that. From now on, both the harness and the engine size every decision's buys against cash minus a reserve of **3% of equity**. At the 100% exposure cap, at most 97% of equity is therefore invested at the decision price. The 30% and 60% caps are unchanged, because they never bind on cash. Any buy that would still exceed the cash at the fill price is cut to whole shares that fit, the same in both. Fractional shares left over by a split are paid out in cash, as Alpaca does, in both the harness and the engine. No real-data result through the engine, and no point-in-time result, existed when this was written. The earlier today-universe reports were produced without the reserve and are labelled as such.

## Amendment 4 (13 Nov 2026, Product, before any point-in-time or engine run on real data)

What is tested must be exactly what trades. The live path sizes through the shared portfolio builder (#119, #168), so two of its rules now apply in the harness too.
1. **Sector cap of 40% of equity.** No sector may exceed 40% of equity, the hard ceiling in `BOOK_HARD_CEILINGS`. A buy that would exceed it is cut to fit, best-ranked first, exactly as the builder does. Stocks without a sector count as their own sector.
2. **A missing bar on the fill day.** When an order finds no bar on its fill day, it waits to the next session without re-checking the other caps. This is the engine's behaviour, and the harness now matches it.
The level-specific limits (for example the provisional Balanced values) are not part of the rule's test. The test runs at the rule's own limits within the hard ceilings, and the stakeholder confirms level limits separately (#132). No point-in-time or engine result on real data existed when this was written.
