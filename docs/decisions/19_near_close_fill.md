# Decision 19 (for the owner): which fill is the official one for a daily rule's buys, the close or 15:50

- **Status:** **open, for the owner** (#731, row "Send buys as market-on-close"). Backend-quant prepared it on
  26 Nov 2026 for M17 ticket 6.7 (#707). **Nothing is implemented.** No order, setting, pre-registration or gate
  changes when this file is merged. Whichever option the owner picks gets its own pull request, tests and
  amendments.
- **Evidence:** the re-run of #454 (job `near-close-1550-2026-11-25`, report
  `near_close_1550_trend_momentum_balanced_pit_20261125T182333.json`, Amendment 31 of `docs/prereg/trend_momentum.md`);
  #333 and #619 (the closing auction on Alpaca paper); decision 15 section 12, question 1; the second engine audit,
  Stage 6, ticket 6.7.

## The gap

Every research run fills a daily rule's near-close orders at the **closing print**. The live engine sends them as
DAY market orders from **15:50 to 15:57 ET** (`ExecutionPolicy("near_close")`, since #333 / PR #337), so they fill
at roughly the 15:50 price. #454 re-ran `trend_momentum`'s base configuration once both ways (engine +
`SimBroker`, cost model v2, 2017-05-01 to 2024-12-31, point-in-time universe, Balanced):

| Fill | CAGR | Sharpe | Max drawdown | Cost, bp a side |
|---|---|---|---|---|
| closing print (what research judges) | 8.19% | 0.59 | 17.7% | 3.37 |
| 15:50 price (what live gets) | 7.74% | 0.57 | 20.2% | 3.39 |
| **difference** | **−0.45 pt a year** | −0.02 | **+2.5 pt** | +0.02 |

- **Buys are 3.4 bp worse at 15:50** than at the close (3.36 bp, dollar-weighted, against the order). Sales are
  2.1 bp better, so all orders together come to 0.7 bp worse. The cost model charges almost the same at both
  venues. The gap is the price move from 15:50 to the close, not the cost model.
- **Coverage.** Only 296 of 501 symbols have minute history, so 79% of the 15:50 fills (by dollars, 80%) were
  priced at a real 15:50 minute. The rest fell back to the close. The true gap is probably somewhat larger than
  measured.
- **One path.** This is one configuration on one path. The drawdown step of 2.5 points in particular is partly
  path noise (audit ticket 6.6).

Research on closing prints is therefore mildly optimistic for buys. The owner can close the gap from either side.

## Option A: live buys go to the closing auction

Live buys become market-on-close orders (`time_in_force: cls`), sent before 15:50 ET. Simulated buys already fill
at the close, so the two would then match.

- **The window.** Alpaca rejects `cls` orders sent from 15:50 to 19:00 ET, on paper and live
  (`CLS_UNTIL, CLS_FROM` in `engine/execution.py` and `broker/sim.py`; Alpaca's order docs, cited on #619). The send
  window would have to move earlier, to about 15:40 to 15:49. The pre-send gate (#441) would then read its quote
  10 minutes earlier. At the exchanges, an on-close order can no longer be cancelled after 15:50.
- **Paper has no real auction.** Alpaca paper runs no closing cross. It works a `cls` order as a market order in the
  last seconds before 16:00 and lets the rest expire. On 18 Nov 2026, 30 `cls` buys were sent: 4 filled in full,
  3 in part, and **23 got nothing** (#333, #619). That is why #333 moved every daily order to `near_close`. On this
  paper account, option A would bring that failure back. Most buys would expire and be carried to the next day.
  The book would be under-invested, and live would drift further from research, not closer.
- **What it touches.** The live order path (`engine/execution.py`, `live_daily.py`), the send window, the pre-send
  gate's timing, and an amendment to every daily rule's pre-registration (it reverses Amendment 10 of
  `trend_momentum` and Amendment 8 of `mom_12_1_band`). It is an engine and broker change, so it needs tests first
  and two keys.
- **When it would be right.** On a live account with a real closing auction. That is "later" on #600 (item 5), and
  this repository is paper only (`CLAUDE.md`).

## Option B: research judges the 15:50 fill

The live engine keeps sending at 15:50. Research makes the 15:50 price the **judged** fill of a near-close order,
as #454's re-run did (`run_daily(execution="at_1550")`, v2's continuous venue). The closing-print run is printed
beside it as a report.

- **No live change.** No order, window, broker or risk-gate code moves.
- **The work is in research.**
  - Fetch minute bars for all 501 symbols. This is the second half of ticket 6.7. Former members may have no
    minute history (`docs/m0/reproduce.md`). Any fallback to the close stays printed in each report, with its
    share of fills.
  - Give each daily rule an amendment before its next run.
  - Write a decision record that adds the fill to `final_requires` in `config/gate_v3.yaml`. That block names
    the engine, the cost model, the data repairs and the benchmark today, but no fill. A daily run would then
    pass only when its source records the 15:50 fill.
  - Verdicts given before that record are not reopened.
- **The cost.** Research results get slightly worse, by about 0.45 points a year for `trend_momentum` on today's
  coverage. They become honest about what live paper trading actually gets. Every daily candidate has failed so
  far, so no live decision depends on the change.

## Recommendation: B

1. **It closes the gap on the account we actually trade.** Kaibot is paper only. On paper, A brings back #333:
   23 of 30 closing-auction buys got nothing. B makes research match the fill live already gets.
2. **It errs the safe way.** A rule must clear its marks at the price live really pays. Under A, it would clear
   them at a price paper cannot deliver.
3. **It touches no live code.** A changes the order path, the send window and the pre-send gate, and reverses a
   fix that was made for a reason. B changes the research yardstick only, and the execution mode it needs already
   exists.
4. **A stays open for later.** When a live broker with a real closing auction is on the table (#600 item 5), A
   becomes worth measuring. That would be its own record, with a paper-versus-live fill study first.

**If the owner picks B:** Backend-quant fetches the minute history for the missing symbols and reports its coverage.
Product then writes the gate decision record, and each daily rule gets its amendment before its next run.
**If the owner picks A:** the engine team prepares the change with tests first and two keys. It needs a plan for
`cls` buys that expire on paper, because #333's carry-over alone leaves the book short for a day.
