# Decision 14: Multi-day holding is allowed (in principle)

- **Status:** approved in principle by the stakeholder on 12 Nov 2026: "there is no problem with holding; there is no
  rule to sell the stock every day if it has good momentum." Engine changes are **not** approved yet: they come back
  as a specific proposal once a multi-day rule has passed the ten-year test.
- **Why now:** the intraday dip-reversal rule has zero edge before costs over ten years (`docs/m0/experiments_001.md`).
  The best-documented edges in the research report need holds of days to weeks.
- **Changes PRD v2:** "no overnight positions" becomes "overnight positions only under the rules below". No shorts and
  no leverage stay. Paper only stays.

## Order of work

1. **Test first (research only, no engine change):** multi-day rules in the same honest walk-forward on the 2016-2026
   history, with gap-aware fills (#113). Each rule is a trial.
2. **Only if one passes** (clearly positive after costs, out of sample): Product brings the engine proposal below for
   approval, with the numbers.
3. **Then** a fresh paper period for that rule, judged by the unchanged go-live bar.

## Risk rules a multi-day engine must have (proposal, to be confirmed in step 2)

| Risk | Rule |
|---|---|
| Overnight gaps jump past stops | Size every position on a gap scenario, not the stop distance (e.g. loss if it opens 3 daily ATRs lower stays within the per-trade risk) |
| Too much held overnight | Cap on total overnight exposure as a share of the pot, and on the number of overnight positions, per level |
| Engine is off overnight | Stops rest at the broker as good-till-cancelled orders; the engine re-syncs before the open; the dead-man switch still flattens if the engine stays silent in market hours |
| Earnings surprises | Never hold through a company's earnings date unless the rule is explicitly an earnings rule |
| Trades that go nowhere | Maximum holding period per rule (e.g. 10 or 20 sessions) |
| Weekends and holidays | Counted as one gap; the same sizing rule covers them |
| Daily loss stop | Overnight gaps count toward the day's loss; a breached daily stop blocks new entries, and open positions keep their stops |
| The user's settings (M5) | Still tighten-only: the user can lower overnight exposure or switch multi-day holding off, never raise it past the level |
| Run drawdown end, pause, flatten | Unchanged: "Flatten and stop" sells everything, overnight positions included |

The 15:55 sell-all stays for any rule that is intraday by design.
