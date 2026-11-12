# Pre-registration: `trend_momentum` (trend-filtered momentum, rule 1 of decision 15)

- **Date:** 12 November 2026. Committed before any code that runs this rule (issue #127, story S87).
- **Status of every result from this document:** PROVISIONAL. The test runs in the research harness of #113 on
  today's stock list (`config/universe_history.yaml`, survivorship bias), with cost model v1 and a price-only SPY
  (bars are split-adjusted and carry no dividends, for the rule and for the benchmarks). It cannot pass Gate B
  until S71 (cost model v2), S83 (point-in-time universe) and S84 (raw bars, corporate actions, total return)
  have landed and the test has been rerun, and until the go/no-go run comes from the engine with `SimBroker`.
- **Research only.** Nothing in the engine, the risk gate, the broker, the strategies or the config changes.

## 1. Hypothesis, and who is on the other side

Large, liquid US stocks that have risen steadily for about four months keep rising for some weeks more often than
not (cross-sectional momentum, the best-replicated price anomaly), and a book that buys them only while the index
is in an uptrend, sized so that each position moves the account by the same small amount a day, earns about the
index's return with a clearly shallower drawdown. Expectation written before the run (research estimate, net of
costs): SPY minus 2 to plus 1 points a year, similar or slightly higher Sharpe, materially shallower drawdown.
"No better than holding SPY" is a legitimate finding.

The other side of the trade: investors who under-react to slowly arriving good news and sell winners too early
(the disposition effect), and funds that must rebalance away from what has risen. The edge, if any, is a risk
premium with crash risk, not a free lunch; it halved after publication and is weakest in large caps.

Source of the construction: Andreas Clenow, "Stocks on the Move", as summarised in
`docs/research/engine_v3/report.md` ("Multi-day: trend-filtered momentum is the best-supported core") and
`docs/research/engine_v3/notes_multi_day_strategies.md` section 2.

## 2. Frozen construction (not tuned; published settings)

A decision for session `i` reads sessions `<= i - 1` only.

| Part | Setting |
|---|---|
| Decision day | once a week, on Wednesday; if there is no session that Wednesday, the next session of that week; data up to the previous close |
| Score | annualised slope of an ordinary least-squares regression of log close on time over the last 90 sessions (`exp(slope)^250 - 1`) multiplied by that regression's R-squared |
| Not buyable | close below its 100-day simple average; or a one-day close-to-close move larger than 15% (absolute) inside the 90-session window; or outside the harness's universe filter (price >= $5 and 20-day average dollar volume >= $50M, point-in-time) |
| Market filter | new buys only while SPY's close is above its 200-day simple average; when it is off, holdings are kept until their own exit |
| Buys | from the top of the ranking while there is room; at most 30 names |
| Position size | shares = equity x 0.001 / ATR(20 sessions), whole shares; capped by the level's maximum position percent (Balanced: 15% of equity) and by the exposure cap; never more than the cash (no leverage) |
| Sells | a holding that is no longer in the top 20% of the ranked universe, or closed below its 100-day average, or had a one-day move above 15% in the window. No fixed stop, no maximum holding period |
| Resize | every second week, a holding whose size differs from its target by more than 10% of the target is bought or sold to the target (partial trades, with costs) |
| Account | one continuous account from the first test month 2017-05 to the end of the data, starting flat with $100,000, level Balanced; no walk-forward tuning |

### Choices where the published rule is silent (made here, before any run)

1. Closes are the harness's closes (last regular-hours minute, split-adjusted, no dividends); a session without a
   bar carries the last close for at most 5 sessions. The regression needs all 90 closes; natural log; time
   0..89.
2. The "15% move" is the absolute close-to-close return (the book says "gap"; close-to-close also counts a move
   made during the session). The window is the last 90 returns.
3. ATR(20) is the simple average of the true range over 20 sessions (at least 15 with a bar).
4. The ranked universe is every stock that passes the price and liquidity filter and has a score. Stocks below
   their 100-day average or with a 15% move keep their place in the ranking but cannot be bought. "Top 20%" is
   the first `ceil(0.2 x N)` names; ties keep the universe file's order.
5. New buys come only from the top 20% (the book implies it: anything else would be sold a week later).
6. A holding that drops out of the ranked universe (no score, or fails price or liquidity) counts as "no longer
   in the top 20%" and is sold, like a stock that leaves the index in the book.
7. Exits, like buys, are checked on the decision day only (weekly).
8. Resize weeks are the even weeks of a calendar-anchored week count (Monday-based weeks since 1970), so the
   schedule does not depend on the start date. Order on a decision day: sells, then resizes (reductions first),
   then new buys. The resize target is today's size formula (current equity, current ATR) under the per-name
   cap.
9. Sizing equity is the account's equity marked at the previous close.
10. A buy that does not fit in full under the exposure cap is cut to the room left (whole shares) and buying
    stops there ("buy until the cash runs out"). A stock whose size works out below one share is skipped.
11. Sale proceeds of a decision are available for its purchases at the same print.
12. A missing 200-day average of SPY counts as "filter off" (no buys).

## 3. Neighbours for the plateau check (five, declared now; each must pass mark 1)

| Neighbour | Change from the frozen rule |
|---|---|
| `reg60` | regression window 60 sessions (the 15%-move window stays 90) |
| `reg125` | regression window 125 sessions (the 15%-move window stays 90) |
| `exit30` | sell when out of the top 30% (buys still from the top 20%) |
| `nofilter` | without the SPY 200-day market filter |
| `risk15` | ATR risk 0.0015 of equity per position |

Neighbours run at the base fill, 1x costs, 100% exposure cap, hold through earnings. They are a plateau check,
not a menu: no neighbour replaces the frozen rule.

## 4. Fill model

- **BASE:** decide after the close of T, buy and sell at the close of T+1, at half a spread a side plus the
  model's impact (cost model v1: `sim.buy_px` / `sim.sell_px`, fees from `shadow_costs.sell_fees`).
- **STRESS:** buy and sell at the open of T+1 with spread and impact doubled.
- A holding without a bar on the fill day waits: its sale fills on the next session that has a bar. A buy or a
  resize without a bar on the fill day is dropped.
- No stops exist in this rule, so "a stop gapped through fills at the open" does not apply.

## 5. Reporting grid (reports of ONE frozen rule, not a tuning grid; every run is logged as a trial)

- Fill model: base, stress.
- Costs: 1x, 2x, 3x of cost model v1 (the multiplier scales spread and impact; regulatory fees stay as they
  are), plus a 0x run used only to state the break-even cost multiple.
- Exposure cap: 30%, 60%, 100% of equity (at most 30 names; never leverage).
- Earnings: "hold through earnings" is the base. "Exit before earnings" is an APPROXIMATE variant on the
  harness's estimated earnings window (`kaibot.replay.earnings`), because point-in-time earnings dates do not
  exist yet (S85); it is skipped with a message when the estimate fails its own coverage check.
- The **base version** for the Gate B marks: base fill, 1x costs, 100% exposure cap, hold through earnings.

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
