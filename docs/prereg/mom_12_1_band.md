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

## Amendment 10 (19 Nov 2026, QA-Ops second key on issue #363, before the third live paper session): limits on the carry-over of Amendment 9

Written on 19 Nov 2026, with Amendment 9 and before its first use. It only narrows Amendment 9: every item
below leaves a carried buy out; none sends more shares, and nothing is sized again. No research result is affected
(item 5). The same text is Amendment 12 of trend_momentum.

1. **This run only.** The plan is the latest decision's send of the paper run that is running. A new run started
   since then (the old one is flattened first) carries nothing of the old run's plan: its share counts were sized
   on the old run's equity. The new run buys at its own first decision.
2. **Sold since the decision, by any path.** Amendment 9 (item 2, "Order history") leaves out a stock sold since
   the decision. That is read from the shares, not only from the rule's own orders: a sleeve that holds less than
   it held at the decision plus what the decision's buys have filled since has lost shares, whatever sold them
   (the watchdog's or the dead-man's emergency stop while the engine was down, a protective stop, a flatten). It
   is not bought back before the next decision.
3. **A fill that is not booked yet.** A stock with a finished buy whose fill the venue reported and the ledger
   has not booked yet is not carried until it is booked. Otherwise the difference to the target would be bought a
   second time.
4. **Cash and the book's limits, at the previous close.** A carried buy keeps the plan's shares, which were sized
   at the decision's prices and against that day's cash. Alpaca paper is a margin account: it lends, it does not
   refuse. So a carried buy is sent only while it still fits at the previous close's prices: its position under
   the per-name cap, its sector under the sector cap (Amendment 4), the cash less the 3% reserve (Amendment 3),
   and the book under its exposure cap with the cash floor. Best-ranked first, in the plan's order. A stock over
   its own or its sector's cap is left out and the next may fit; once the cash or the exposure cap is reached,
   buying stops, as in the ranked fill. A stock that is left out is tried again at the next session. A stock the
   ledger booked a split for on that session is left out too. Live path only: the research venue checks the cash
   at the print.
5. **Research and Gate B unchanged.** Nothing here can add a carry-over, and on the research venue nothing is
   carried (Amendment 9, item 4); the same test passes for both momentum rules. With a protective stop
   (an experiment option of the research runner; neither momentum rule has one) a stopped-out stock is not
   bought back either: a test compares the runs with and without the carry-over.
6. **Journal.** Each stock left out is in the session's `md_send` entry (`carry.skipped`) with its reason, and the
   `multi_day_carry` event is sent also when nothing is carried. A failure inside the carry-over carries nothing,
   is an event (`multi_day_error`, step `carry`) and never holds back the session's sales.

## Amendment 11 (19 Nov 2026, backend-quant, the author's review of QA-Ops's key on issue #363, before the third live paper session): a carried buy must be in the rule's ranked universe at the previous close

Written on 19 Nov 2026, with Amendments 9 and 10 and before the carry-over's first use. It corrects one sentence of
Amendment 9 and only narrows it: it leaves a carried buy out, sends no more shares and sizes nothing. No research
result is affected (item 3).

1. **The correction.** Amendment 9 (item 2, "Not buyable") names a 100-day average and a 15% move. Those are
   trend_momentum's conditions. This rule has neither (choice 5: no stock-level trend or gap filter), so the code of
   Amendment 9 left no stock out as not buyable. What this rule has is the Universe row of section 2. A stock that
   left the universe filter after the decision would have been carried.
2. **The rule.** A stock is carried only if, at the previous close, it is in the rule's ranked universe, as a
   decision reads it: it passes the universe filter of section 2 (price >= $5, 20-day average dollar volume >= $50M,
   point-in-time membership) and it has a score (choice 2; a stock without a score is never bought, Amendment 7). A
   stock that does not is left out that session, with the reason `not buyable: universe` or `not buyable: no score`
   in `md_send.carry.skipped`, and is tried again at the next session. Its place in the ranking is still not read
   again: that is the next decision's (Amendment 9, item 2).
3. **Research and Gate B unchanged.** This can only leave a carried buy out. On the research venue nothing is
   carried (Amendment 9, item 4), and the same bit-identity test passes for both momentum rules. A test finds, on
   the fixture, a session where one planned stock is in the ranked universe and another is outside the universe
   filter: only the first is carried, for this rule and for trend_momentum (Amendment 13 there).

## Amendment 12 (21 Nov 2026, platform, issue #441, M9 ticket 1.16, before its first live paper session): a pre-send check that can withhold a near-close order

Written on 21 Nov 2026, before the check's first use. It changes whether and when a live order goes out, so it is
recorded here. It can only withhold an order for a session: it never adds an order, and it never changes a share
count, a rank or a target. No research result is affected (item 6). The same text is Amendment 14 of
trend_momentum.

1. **The gap.** Amendment 8's near-close market orders go out with the share counts sized at the previous
   close. Nothing read the stock's quote, whether it was trading, or how far it had moved that day (engine audit of
   21 Nov 2026, "confirmed absent"; reproduced by a failing test before the change).
2. **The check (live path only).** Just before an order would be sent, the engine reads the stock's latest IEX
   quote (one request per tick for the whole plan, ended after 8 seconds) and the close its shares were sized at
   (the previous session's raw close, on today's share basis). The order is withheld when:
   - the quote is older than 300 seconds (the stock is halted or not trading; a limit-up/limit-down pause lasts
     five minutes);
   - both the bid and the ask are more than 10% away from the sizing close (the side nearer the close is read,
     because IEX is one exchange's book and one side can sit far from the market);
   - a buy's value at that price is above the book's per-name cap plus 10% (a wrong quantity; a sale is exempt: it
     only reduces exposure and can never be larger than the shares held).
   The constants are fixed here and are not tuned on results.
3. **No quote: the prior close.** When the quote cannot be read, or the feed has none for the stock, the order is
   checked at the sizing close and goes out. A market-data outage must not become one more way to miss a rebalance;
   the read failure is one `multi_day_error` event (step `presend_quotes`) per session.
4. **Withheld, not dropped.** The check is asked again on every tick of the send window, so an order goes out the
   minute its quote is fresh and inside the collar. What is still withheld when the window ends is sent again at
   the next session, checked against that session's previous close: a buy by the carry-over of Amendments
   9 to 11 and under their limits (it never had an order, so nothing in the order history is against
   it); a sale to zero as a pending sale, as an unfilled sale already is; a reduction (a resize down) as the same
   reduction to the same target shares while the sleeve still holds more. The last is new: a reduction that the
   venue let expire is still not sent again (section 4); only one this check withheld is.
5. **Journal.** One `md_withheld` entry per (rule, session, stock, side) with the reason, and the reasons in the
   session's `multi_day_orders` event (`withheld`). No new event name.
6. **Research and Gate B unchanged.** The research venue has no quotes and fills at the closing print; the check
   exists only where a quote source is given, and only the live engine's market-data client is one (a replay's
   stored bars are not). Tests: with fresh quotes at the previous close the live path on the simulator sends the
   same orders as without the check; with the feed down for the whole run, too; a stock without a fresh quote and
   a stock 20% from its sizing close are withheld and bought at the next session at the plan's own shares.
7. **What this can cost.** A stock that really moves more than 10% in a day (an earnings day) is not traded that
   session: a sale is a day late, a buy waits for the carry-over's conditions. IEX quotes of a thinly traded stock may be older than
   300 seconds without a halt; the reasons in `md_withheld` are to be reviewed after the first ten live sessions.

## Amendment 13 (21 Nov 2026, QA-Ops second key on issue #441, PR #490, before the first use of Amendment 12): the pre-send check never withholds a sale

Written on 21 Nov 2026, with Amendment 12 and before its first use. It only narrows Amendment 12: the check
withholds fewer orders, never more; it adds no order and changes no share count, rank or target. No research result
is affected (item 4). The same text is Amendment 15 of trend_momentum.

1. **Sales are exempt.** The check of Amendment 12 (item 2) applies to buys and resizes up only. A sale to zero
   and a reduction go out in their own session whatever the quote says: stale, far from the sizing close, or
   missing. Where Amendment 12 says "the order is withheld", read "the buy is withheld".
2. **Why.** Decision 15 (section 12) lets sales go out even when entries are blocked, so that the book is never
   locked in. A sale only takes risk off and is never larger than the shares held. A stock whose IEX quote is
   stale or far from the close day after day could otherwise never be sold.
3. **What falls away.** Item 4 of Amendment 12 carried a withheld sale to zero as a pending sale and a withheld
   reduction as the same reduction. Neither can happen now, and the carry of a withheld reduction is removed from
   the code. A buy still withheld at the end of the window is carried as that item says. In item 7, "a sale is a
   day late" no longer applies. A sale of a halted stock is sent and may expire unfilled; a sale to zero is then
   sent again at the next session, as before (decision 15, section 12).
4. **Research and Gate B unchanged.** As Amendment 12, item 6. Added tests: the check gives no reason for a sale
   with any quote; every sale of the fixture run, with a stale quote or a quote 20% under its sizing close, goes
   out in its own session exactly as without the check; a sale does not wait for the quote read, which is made
   only when a buy is about to go out.

## Amendment 14 (21 Nov 2026, platform, issue #436, M9 ticket 1.11, before its first live paper session): an order is called lost only after the venue's orders, fills and position were read

Written on 21 Nov 2026, before the change's first use. It can change whether a second attempt of an order goes
out, so it is recorded here. It can only hold a second attempt back: it never adds an order and changes no share
count. No research result is affected (item 4). The same text is Amendment 16 of trend_momentum. (The number follows
the pre-send check of issue #441, written the same day in another change.)

1. **The gap.** An order whose submission got no answer is looked up by its client order id. After two lookups
   that find nothing, at least 30 seconds apart and 60 seconds after the intent, it was called lost and the same
   decision's order went out again under the next attempt number. If the venue had accepted the order and only
   that lookup lagged, two orders existed (engine audit of 21 Nov 2026, "suspected": the code path is reproduced
   by a failing test; whether Alpaca's lookup can lag is not known and is measured by
   `scripts/probe_alpaca_idempotency.py`).
2. **The rule.** Before an order is called lost the engine also reads the open orders, the fills since the intent,
   and the venue's position in the stock against the ledger plus the fills its other orders have not booked yet.
   An order found is taken over as the same order (its fills are booked once, as always). A fill in the stock, on
   the order's side, on an order that cannot be read; shares at the venue that the ledger and the known orders do
   not explain, in the order's direction; or a read that fails: the order stays unconfirmed, nothing new is sent
   in that stock, and everything is read again at the next lookup (30 seconds later or more).
3. **What this can cost.** While such evidence stands, the stock's order of that decision is not sent again in
   that session; a buy is then carried under Amendments 11 to 13 of trend_momentum (9 to 11 of mom_12_1_band) once
   the order is resolved. Shares at the venue that the ledger does not have are a reconciliation difference in
   any case, and that already refuses buys in the stock.
4. **Research and Gate B unchanged.** The research venue answers every submission; no order is unconfirmed there,
   so the new reads never run. Tests: an accepted order whose lookup lags is found in the open orders (one order
   at the venue); a filled one is found by its fill and booked once; an order that never arrived is lost as before.

## Amendment 15 (21 Nov 2026, QA-Ops second key on issue #436, PR #495, before the first use of Amendment 14): the hold of an unconfirmed order ends with the order's day

Written on 21 Nov 2026, with Amendment 14 and before its first use. It only bounds Amendment 14: it adds no
order and changes no share count. No research result is affected (item 4). The same text is Amendment 17 of
trend_momentum. (The numbers follow those of the pre-send check of issue #441 once both are on main.)

1. **The gap.** Under Amendment 14 (item 2) an order with evidence against "lost" stays unconfirmed. An
   unconfirmed order holds back every order in its stock, sales included. A position difference, or a fill on an
   order that cannot be read, can stand for days for a reason that has nothing to do with the order, so the hold
   had no end and a sale of the stock could wait for days.
2. **The rule.** On the order's own New York day Amendment 14 applies unchanged. From the next New York day on,
   only the order itself counts: found among the open orders or by its fill, it is taken over as the same order;
   a venue that cannot be read still keeps it unconfirmed; otherwise it is lost and the stock is free again. A
   lookup cannot lag overnight.
3. **What this can cost.** While the evidence stands, no other order in the stock goes out that day: a sale is at
   most one session late. Shares the ledger does not have stay a reconciliation difference afterwards, which
   refuses buys in the stock and never a sale (decision 15).
4. **Research and Gate B unchanged.** As Amendment 14, item 4. Added tests: a buy and a sale held by a position
   difference stay unconfirmed through their day and are lost the day after, with the stock free again; after its
   day an order the venue has is still taken over, and an unreadable venue still keeps it unconfirmed.

## Amendment 16 (21 Nov 2026, platform, issue #433, M9 ticket 1.8, before its first live paper session): the sale of a full flatten goes through the order manager

Written on 21 Nov 2026, before the change's first use. It changes how the sales of a full flatten are sent (the
book's run end of section 2, a new run, "Flatten and stop"), so it is recorded here. It changes no rule, no trigger,
no share count and no price. No research result is affected (item 4). The same text is Amendment 18 of trend_momentum.
(The number follows the amendments for issues #441 and #436, written the same day in other changes.)

1. **The gap.** These sales were sent beside the order manager, under a client id with a timestamp, and booked
   only when completely filled. A sale the venue cancelled or let expire after a partial fill left its filled
   shares out of the ledger (engine audit of 21 Nov 2026, confirmed; reproduced by a failing test). A sale whose
   answer was lost was unknown to the engine until the next restart: its fill was not booked, or, while it was
   still open, the next flatten cancelled it and sent another (the audit's "suspected" double send; no second fill
   was reproduced).
2. **The rule.** With the live daily path on, each sleeve's sale is one DAY market order sent by the order
   manager under `kb<run>-<rule>-<symbol>-<day>-flatten-<n>`. An open sale of the same day is reused, never doubled;
   a sale without an answer is looked up by its client id before anything else is sent in that stock; every fill is
   booked from the account activities at its own quantity and price. What a cancelled or expired sale left unsold
   is sold by the next flatten under the next attempt number, as before.
3. **Unchanged.** When a flatten happens, what it sells, the cancel of the stock's open orders before the sale, the
   limit to the shares the venue holds, the time budget, and that a new run waits until the sales are booked.
4. **Research and Gate B unchanged.** The research runner ends a run inside the simulator and never calls this
   path. Tests: a partial fill then a cancel books the filled shares and the rest is sold by the next flatten; a
   kill between the submission and its record, and a lost answer, each end in one sale booked once.

## Amendment 17 (22 Nov 2026, backend-quant, issue #428, before the session of Mon 23 Nov 2026): a decision the engine missed overnight is taken during the session

Written on 22 Nov 2026 (a Sunday), after the engine audit of 21 Nov (finding M1) and before any session that
uses it. On 20 Nov the Mac started late. A session that opens without a decision record carries nothing
(Amendment 9, item 1: "A session with no decision taken after the last close carries nothing"), and the same
late start on a fill day skips the rule's whole rebalance. No research result is
affected (item 4). This amendment changes when the record of a decision is written on the live path, not the rule,
its decision, its targets or its sizing. The same text is Amendment 19 of trend_momentum.

1. **The late decision.** On the live path, when a session is open and the rule has no decision record for it
   (the engine was not running between the previous close and this open), the after-close step of the previous
   session is taken during the session, by the same code, as soon as the engine is up and again in the send
   window. It reads the daily view of the previous close: rows up to that close only, once that close's bars are
   available, which is all a decision may read (section 2). The decision is therefore
   the one an engine running on time would have journalled. The record carries `late: true` and its own
   `decided_at`. Code: `engine/live_daily.LiveDaily.catch_up`.
2. **What follows from it.** After a decision day, the decision's orders go out in that session's near-close
   window as decided, sized as on any fill session (they were skipped before). After a session that was not a
   decision day, the record "not a decision day" is written late, and the session's pending sales and carry-over
   run as they do after an on-time start, under every limit of the carry-over amendments.
   A paper run started during a session that has no decision record is the same case (QA-Ops second key on
   PR #517): it takes the previous close's decision under its own run and, after a decision day, buys in that
   session's window, sized on its starting amount, as a run started the evening before does. Section 2 is
   kept (a decision for session i reads sessions <= i - 1; the account starts flat), and so is Amendment 10,
   item 1 (the new run buys at its own first decision).
3. **Fail closed, as before.** If the daily store does not hold the previous close, or fails its check, or the
   step fails, no decision is taken, nothing is bought and nothing is carried that session. The heartbeat says why
   and the try is repeated (at most every five minutes before the window, on every tick inside it). The shares
   held are read when the late decision is taken, so a stock sold since the close is not counted as held.
4. **Research and Gate B unchanged.** The research runner decides after every close, so it never takes a late
   decision; `engine/daily.py` is not changed. No trial row, report or Gate B mark changes, and no configuration
   is added (`n_trials` unchanged).
5. **Known deviation, live against research.** None in the orders when the store is unchanged since the close: a
   test starts the live path at 10:00 ET on a fill day and finds the decision record, the orders and the fills of
   an on-time start. If the daily store was corrected between the close and the late decision (a split row
   verified overnight), the late decision reads the corrected store, as a decision taken late the same evening
   would. Paper only.
