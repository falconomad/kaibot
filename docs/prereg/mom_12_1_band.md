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

## Amendment 5 (13 Nov 2026, backend-quant, issue #203, before any run that reads the SEC earnings dates)

Written after #209 (the code that reads the SEC dates) was reviewed and before any run with
`data/earnings/announcements.parquet`; no result of the variant on SEC dates, on real or synthetic history, had been
seen. The only SEC-related number known when this was written is S85's "91% of SEC reports detected within one
session", which measures the headline detector against SEC, not the bar below. It names the source of the "exit
before earnings" variant of section 5 on the point-in-time universe and changes nothing else.

1. **Source.** On `--universe pit`, when `data/earnings/announcements.parquet` exists (S85, #125: SEC 8-K Item 2.02
   filings with their EDGAR acceptance time, `kaibot.ingest.earnings_pit`), the variant is built from those dates
   (`exit_before_earnings: sec_pit`, reported as "exit before (SEC point-in-time dates)", label `exit_sec`) instead
   of the headline estimate. A filing is a quarterly report when it comes 30 days or more after the company's
   previous report filing; 8-K/A amendments are not used.
2. **Point in time.** Each report counts from the first session that opens after its acceptance time
   (`earnings_pit.report_events`). A decision at the close of session t uses only reports counted at or before t,
   so a filing accepted during a session counts from the next one. Companies are mapped to store columns through
   the membership log.
3. **Window unchanged.** The window of `kaibot.replay.earnings.calendar` is the same as on the headline path: flat
   from 10 calendar days before the date expected from earlier reports, until the report or 10 days after that
   date, and no new position in the 7 days before the window. Only the dates that anchor it change.
4. **Reliability bar unchanged.** The SEC dates are judged by the same function and the same pre-declared bar as
   the headline estimate (`replay/earnings.coverage` against `replay/earnings.RELIABLE`). At least 80% of the
   stocks with bars must report at a plausible 3 to 5 times a year, and the account must be flat the night before
   at least 85% of the reports after each stock's first. A stock with no SEC rows counts against the first share
   and gets no blackout. Below the bar, or without the file, the APPROXIMATE headline variant of section 5 runs
   exactly as before (or is skipped when it fails its own check). `--earnings on` cannot force SEC dates below
   the bar. The `--universe today` reports never read the SEC file. The report states which source was used and
   its coverage, and both coverages when the SEC dates fall back.
5. **Base version and verdict unchanged.** For each of the two versions the base version is still base fill, 1x costs, 100% exposure cap, hold through earnings. The Gate B marks and the verdict never read earnings dates.
   Every hold-through row is identical with or without the SEC file.
6. **Trials.** At the base fill and 1x costs, the SEC variant is one configuration per exposure cap (`n_trials` 1).
   Its cost and fill stress runs are reports of it (`n_trials` 0). The first core_pit run with the SEC file
   repeats the hold-through and neighbour runs of the earlier report exactly. A row with the same label and the
   same events digest as a row of the earlier finished report of the same window was counted there, so it logs
   `n_trials` 0. The SEC variant does not replace the headline variant's earlier rows; both stay in the registry.

## Amendment 6 (16 Nov 2026, backend-quant, issue #266, before any rerun on corrected data): spin-offs in the inputs, a data correction

Written before any run with the change; no result with spin-off-adjusted inputs had been seen, on real or synthetic
history. It corrects the rule's **inputs**, not the rule. Section 2 says "split-adjusted, no dividends". A spin-off
is neither a split nor a dividend, so the inputs read it as a one-day fall of the parent (#262: CTVA about -85% on
2026-10-01, DD -58%, BDX -21%, and others, inside the 12-1 window). No published version of the rule meant that.

1. **Signals.** A spin-off is value kept. Every split-adjusted price of the parent before its ex-date is
   multiplied by P / (P + V). P is the parent's close on the ex-date, per share held before; V is the spun value per
   parent share. The record is the daily store's corporate-actions table (type `spin_off`, Alpaca's row). V is
   taken, in this order, from: the record's ratio times the new company's first close (when the store has it);
   else Alpaca's own close factor that day, net of the table's split and dividend; else the ex-day fall itself.
   Point in time: only rows before the ex-date move, and V reads nothing after the ex-date close. Disputed keys
   (section 2's split dispute windows) are left alone. Code: `replay/multiday_core.spin_off_factors`, through
   `Market.from_panel`. It is the one core for research and live (decision 15).
2. **Research P&L.** V is credited as cash at the ex-date, as a dividend is, to a position held over it. On the
   live paper path, the spun shares Alpaca credits are adopted at Alpaca's cost basis and sold at the next closing
   auction (#266), so both pay the same value up to one session's move of the new shares.
3. **Dividends unchanged.** Cash dividends are still not in the signals. Nothing else in sections 2-7 changes.
4. **Trials.** A data correction adds no configuration. The first runs on corrected data repeat the earlier
   frozen base, neighbour and stress rows exactly, each logged as a `trial` row with `n_trials` 0 and the new data
   digest. The earlier rows stay in the registry, marked as before this amendment, and the Gate B verdict is read
   from the corrected runs only. If a corrected run changes a verdict, both results are reported side by side. The
   Data fixes of #264 (verified split rows) count as part of the same correction.

## Amendment 7 (17 Nov 2026, backend-quant with QA-Ops's review of #275, before any rerun on corrected data): no unsourced spin-off value

Written before any run with Amendment 6's correction; no result of it had been seen. It narrows Amendment 6's item 1
and changes nothing else.

1. **Two sources only.** V, the spun value per parent share, comes only from the record's ratio times the new
   company's first close, or from Alpaca's own close factor that day (net of the table's split and dividend).
   Amendment 6's third source, "the ex-day fall itself", is not used. It would count the parent's own move that
   day as spun value and could lift its momentum.
2. **A spin-off with neither source** is not adjusted and no value is credited. Its key is not rankable, so it is
   never bought, from the ex-date for 273 sessions (12-1's 252 plus the skipped month), while the unadjusted fall
   is inside the look-back. Before the ex-date nothing changes (point in time). Code:
   `replay/multiday_core.SPIN_UNSOURCED_SESSIONS`.
3. **Trials.** As Amendment 6's item 4. A configuration an earlier finished report counted, made before this data
   correction, logs `n_trials` 0 on its first corrected run (`scripts/momentum_core.n_trials_of`; reports record
   `data_corrections`).

## Amendment 8 (18 Nov 2026, backend-quant, issue #333, before the second live paper session): near-close market orders on Alpaca paper

Written on the evening of 18 Nov 2026, after the rule's first live paper session and before its second. It changes
the **orders** that carry the fills, not the fill model, the costs, the rule or its sizing. No research number is
affected (item 3). The one live result that led to it is the fill record of 18 Nov: Alpaca paper filled 4 of 30
`cls` market buys in full and 3 in part, and 23 expired with nothing filled. About 16% of the $95,000 book was
invested against a plan of about 90%.

1. **Why.** Alpaca's paper venue does not run a closing auction. Its documentation says paper orders are "matched
   against the best available current market price (NBBO)" and "receive partial fills for a random size 10% of
   the time". It says nothing about auctions (docs.alpaca.markets, "Paper Trading"; `cls` orders "execute only in
   the market closing auction", and the unfilled ones "will be cancelled", "Placing Orders"). Alpaca's staff
   confirm that "paper trading treats these simply like market orders placed at the close. They will not fill at
   the actual end of day auction price", and that such an order "may expire before filling completely"
   (forum.alpaca.markets, 24 Jun 2026). Our fills were stamped 15:59:55-15:59:58 ET and the expiries 16:00:09-16:02:26
   ET. A paper `cls` order therefore has about five seconds to fill. The paper record would not measure the rule
   as section 4 prices it.
2. **The change.** On the live paper path the daily orders are **DAY market orders, whole shares, sent from 15:50
   to 15:57 ET**. On an early-close day the window is the close minus 10 to the close minus 3 minutes
   (`ExecutionPolicy("near_close")`, `engine/live_daily.py`). Before, they were `cls` orders sent from 09:31. The
   plan is the same: sales, then resizes, then buys best first, journalled before any order, every order the
   difference between the plan's target and the shares held. Nothing outside the plan's targets is ever sent.
   A remainder that still expires at the close is **not sent again** that session or later. A buy waits for the
   rule's next decision, as before; a sale is sent again at the next session, as section 4 says. Each expired
   remainder is journalled once and reported as `multi_day_unfilled`, so the dashboard shows it as not filled.
3. **Fill model, costs and Gate B unchanged.** Section 4 stands: BASE fills at the close of T+1 at half a spread
   plus the model's impact (cost model v1), with fees. The research runner (`engine/daily.run_daily`, decision 15:
   one core) sends the same near-close market orders at 15:50. SimBroker (`near_close`) fills them at the closing
   print at cost model v1, with the cash checked at the print (Amendment 3), exactly as it filled the `cls`
   orders. A test asserts that the fills (session, symbol, side, shares, price) and the daily equity are
   identical, to the last bit, with `cls` orders and with near-close orders, for this rule and for
   trend_momentum (Amendment 10 there). No trial row, report or Gate B mark changes, and no new configuration is added (`n_trials` unchanged).
   The stress fill (`opg` at the open, research only) is unchanged.
4. **Known deviation, live against research.** A paper market order sent between 15:50 and 15:57 fills at the
   quote of that moment, not at the official close. Paper prices it on IEX-fed quotes, with no impact and no
   queue. The research run prices it at the close with modelled spread and impact. The price move over the last
   3-10 minutes averages zero and is not modelled. Gate C (live against research tracking) counts it as a known
   deviation of the venue, like Alpaca paper's random partial fills.
5. **Not for real money.** This amendment is for Alpaca paper only. Before any real-money decision the execution
   is reviewed again: a live account's `cls` order does reach the closing auction.

## Amendment 9 (19 Nov 2026, backend-quant, issue #363, before the third live paper session): the decision's unfilled buys are carried to the next sessions

Written on 19 Nov 2026. On 18 Nov, Alpaca paper's closing-auction simulation left 23 of 30 buys of trend_momentum's
first decision with nothing filled and 3 in part (Amendment 10 of trend_momentum, 8 of mom_12_1_band). The rule then
bought nothing until its next decision (the first session of the next month), so the book sat at about 16% exposure against a plan of about 90%.
No research result is affected (item 4). This amendment changes how the decision's own buys are executed, not the
rule, its decision, its targets or its sizing.

1. **Carry-over.** On every session that is not a decision's fill session, at the near-close window, the latest
   decision's buys (and resize increases) that are still short of their targets are sent again. The order is the
   plan's own target shares minus the shares held minus open orders. It is never above the target, and the plan is
   never re-sized. The plan is the decision's journalled send of its fill session: after the pre-open review, the
   corporate-action guard (Amendment 6 of trend_momentum) and the overnight cap of that day. The carry-over ends
   with the next decision. A session with no decision taken after the last close carries nothing. Code:
   `engine/daily.carry_targets` and `ended_buys`, shared by `engine/daily.run_daily` and
   `engine/live_daily.LiveDaily._carry` (decision 15: one core).
2. **What is not carried.**
   - **The market filter.** No carry-over while SPY's close at the previous session is below its 200-day average.
     Section 2 allows new buys only while the filter is on, and a carried buy is a buy of its own session. The
     construction checks the filter on decision days only (section 2's decision day), because there every buy happens on a
     decision day.
   - **Not buyable.** No carry-over of a stock the rule could not buy at the previous close: below its 100-day
     average, or a 15% move in the window (section 2's "Not buyable"). The ranking is not re-read: that is the
     next decision's.
   - **No bar.** No carry-over of a stock that had no bar on the decision's fill day. Section 4 drops such a buy.
   - **Corporate actions.** No carry-over of a stock with an announced corporate action. Amendment 6's exclusion
     applies to a carried buy as to any buy, on the live path only.
   - **Order history.** No carry-over of a stock whose last order was cancelled or refused by the venue, that was
     sold since the decision (for example the forced sale before a disputed split), or that split since then.
3. **Journal.** Every carry-over is in the session's `md_send` entry, written before any order:
   `carry: {of, carried: [[symbol, target, held]], skipped, risk_off}`. It is also an event, `multi_day_carry`.
   Its orders carry the purpose `carry` in their client ids.
4. **Research and Gate B unchanged.** On the research venue a buy fills whole at the closing print, is cut to the
   cash at the print (the cancelled rest is never carried), or has no bar on its fill day (dropped). So the
   carry-over never fires there. A test runs both momentum rules on the fixture with and without it: the orders,
   the fills (session, symbol, side, shares and price, to the last bit) and the daily equity are identical, and
   no carry order exists. This rule is also compared in that test with trend_momentum (Amendment 11 there). No trial row, report or Gate B mark
   changes, and no configuration is added (`n_trials` unchanged).
5. **Known deviation, live against research.** A carried buy fills at a later session's price than the decision's
   fill day. That is a deviation of the venue (Alpaca paper's auction simulation), counted by Gate C as such, like
   the near-close timing of Amendment 10 of trend_momentum (8 of mom_12_1_band). Paper only, as that amendment.
