# Pre-registration: `spy_noise_band` and `spy_noise_band_carry` (rules 3 and 4 of decision 15)

- **Date:** 12 November 2026. Committed before any code that runs these rules (issue #128, story S88).
- **Status of every result from this document:** PROVISIONAL. The test runs in a small SPY-only research routine
  (`kaibot.replay.spy_noise_band`) that reuses the cost functions of `kaibot.replay.sim`; it is not the engine with
  `SimBroker`. SPY bars are split-adjusted and carry no dividends (rule and benchmark alike); the close is the last
  regular-hours minute, not the closing auction. Nothing here can pass Gate B until the go/no-go run comes from the
  engine with `SimBroker` and cost model v2 (S71) has landed.
- **Research only.** Nothing in the engine, the risk gate, the broker, the strategies or the config changes.
- **Two versions, both declared now:** `spy_noise_band` (version 1, flat at the close) and `spy_noise_band_carry`
  (version 2, the same rule but an open long at the close is held to the next session's open).

## 1. Hypothesis, and who is on the other side

When SPY moves beyond its usual intraday range for the time of day, the move tends to continue until the close
(intraday time-series momentum). A long-only, unlevered version that buys SPY only on such upside breaks, checked
on the half hour, earns a small positive amount per round trip after SPY-specific costs, on capital that is
otherwise idle. Expectation written before the run (research estimate, `docs/research/engine_v3/report.md`):
**3 to 5% a year before SPY-specific costs**, about **2 bp per round trip**, so the rule needs fills under about
0.5 bp a side; 2016 and 2017 were losing years in the paper. The carry is expected to add about 2 points a year,
with gap risk. "Nothing beats holding SPY" is a legitimate finding.

The other side of the trade: option market makers delta-hedging a short-gamma book (they must buy into rises and
sell into falls, which extends intraday trends, strongest late in the day), leveraged and inverse ETFs
rebalancing into the close in the direction of the day's move, and slow institutional orders worked through the
day. They are paid for providing liquidity or forced by mandate, not wrong; the edge is small and lives in
sub-basis-point execution. The carry adds the overnight premium (about 4 bp a night in SPY), paid to whoever holds
gap risk while the market is closed, conditional on a strong close.

Source of the construction: Zarattini, Aziz and Barbon (2024), "Beat the Market: An Effective Intraday Momentum
Strategy for S&P500 ETF (SPY)", as summarised in `docs/research/engine_v3/notes_intraday_overnight_strategies.md`
section 2; the carry from Concretum, "Breaking the Rules of Intraday Trading" (March 2026), same notes.

## 2. Frozen construction and fill model (published parameters; not tuned)

Times are ET. Minute `m` is the 1-minute bar starting at 09:30 + m minutes; a decision "at 10:00" is taken at the
close of the 09:59 bar (m = 29) and fills at the open of the 10:00 bar (m = 30), the convention of the live
last-30-minutes SPY rule (`kaibot.strategies.spy_momentum`: decide at the close of the 15:29 bar, fill at the 15:30
bar's open).

| Part | Setting |
|---|---|
| Instrument | SPY only, long only, 1x: when long, the whole account is in SPY (fractional shares, never more than the cash); otherwise all cash at 0% |
| Average move | for each minute m: sigma(m) = mean over the previous **14** sessions of abs(close(m) / open(09:30) - 1) |
| Upper band | UB(m) = max(today's open, yesterday's close) x (1 + sigma(m)) |
| Checks | only at HH:00 and HH:30, from **10:00** to 15:30 (12 checks a day) |
| Entry | at a check, when flat: long if the 1-minute close is **above** UB(m) |
| Exit | at a check, when long: sell if the 1-minute close is **below max(UB(m), session VWAP(m))**; otherwise at the **close** (version 1) |
| Fill | at the **next bar's open** after the decision; the close exit at the last bar's close |
| Carry (version 2) | a long still open at the close is not sold; it is sold at the next session's open (the 09:30 bar's open). The next session starts flat after that sale and runs the same rule |
| Account | one continuous $100,000 account from the first test month **2017-05** (the walk-forward windows of the other reports: data from 2016-01, 120-day warm-up, 12 months of training) to the end of the data; no walk-forward tuning |

### Choices where the published rule is silent (made here, before any run)

1. "Today's open" is the open of the session's first 1-minute bar; "yesterday's close" is the close of the
   previous session's last regular-hours bar.
2. sigma(m) needs at least 12 of the previous 14 sessions with a value at minute m. Early-close sessions (last
   bar at or before 13:00) have no value after their close, so for afternoon minutes the mean is over the
   remaining sessions. A session where sigma is missing at a check cannot enter at that check.
3. A session's last bar is its last minute with volume; a session whose last bar is before 14:30 is an early-close
   session: checks stop at the last one that can still fill inside the session, and "the close" is that last
   bar's close.
4. VWAP(m) is the session's cumulative sum of bar VWAP x volume divided by cumulative volume, through minute m
   (the bar close when no volume has traded).
5. At a check the rule evaluates only the exit when long and only the entry when flat, so an exit and a re-entry
   never happen at the same check; after an exit a later check may enter again (the paper trades about 1.8 times
   a day). A long entered while the price is below VWAP is sold at the next check if it is still below
   max(UB, VWAP) — the published logic, not changed.
6. Only completed bars up to the decision minute are read; a missing minute carries the last close (the harness's
   loader), with zero volume.
7. Model v1 costs (below) need a 1-minute volatility: the standard deviation of the previous session's 1-minute
   log returns (point-in-time), and the fill bar's volume (the convention of `kaibot.replay.sim`).
8. Statistics: daily mark-to-market returns of the account (a carried long is marked at the close); Sharpe
   annualised with 252 sessions and no risk-free rate; the t of a round trip is the mean net return per round
   trip over its standard error; a carry round trip counts as one round trip.

## 3. Neighbours for the plateau check (five, declared now; version 1 only)

| Neighbour | Change from the frozen rule |
|---|---|
| `lb10` | 10-day lookback for sigma (needs 9 of 10 sessions) |
| `lb20` | 20-day lookback (needs 17 of 20) |
| `lb30` | 30-day lookback (needs 26 of 30) |
| `check15` | checks every 15 minutes from 10:00 to 15:45 |
| `exit1555` | the close exit becomes a sale at the open of the 15:55 bar (checks unchanged) |

They are a plateau check, not a menu: no neighbour replaces the frozen rule.

## 4. Costs (all-in, per side, every buy and every sell)

- **0.5 bp, 1 bp and 2 bp a side** (SPY-specific scenarios; the percentage includes spread, impact and fees).
- **Cost model v1**: `sim.buy_px` / `sim.sell_px` (half a spread of at least 1 bp, plus the model's impact) and
  `shadow_costs.sell_fees` on every sale; the close exit and the next-open exit are priced the same way.
- A 0-cost run states the gross edge. **Break-even cost per side** is reported two ways: half the mean gross return
  per round trip (where net per round trip reaches zero), and the cost per side at which the account's total
  return reaches zero (by bisection on the round trips of the 0-cost run).
- The **base scenario for the pass marks is 0.5 bp a side** (decision 15: the rule needs fills under about 0.5 bp a
  side); mark 2 uses 1 bp a side.

## 5. Pass marks (issue #128, copied; judged on the base scenario unless stated)

Version 1 (`spy_noise_band`):

1. at least **+1.5 bp net per round trip with t >= 2**;
2. total net return **positive at 1 bp a side**;
3. at least **7 of the 10 calendar years** 2017-2026 positive (2017 from May and 2026 to the end of the data count
   as years);
4. maximum drawdown (mark-to-market) **under 15%**;
5. **stable for 10- to 30-day lookbacks:** `lb10`, `lb20` and `lb30` each have at least +0.75 bp net per round trip
   (half of mark 1) and a positive total return.

Version 2 (`spy_noise_band_carry`):

6. **carry nights beat other nights with t >= 2:** the mean SPY close-to-next-open return on nights the rule is
   long at the close minus the mean on all other nights of the window (Welch t of the difference);
7. carry nights are **positive from June 2022 to July 2023**: the compounded close-to-next-open return of the
   carry nights between 2022-06-01 and 2023-07-31 is above zero (the window in which the one live overnight fund
   lost 6.9%).

Reported beside the marks (not marks): the carry's extra CAGR over version 1 (expected about +2 points), both
versions over 2022-06-01 to 2023-07-31, every cost scenario, the neighbours' full rows, SPY buy-and-hold (price
only) over the same sessions, and the share of sessions invested.

Every verdict is PROVISIONAL (section "Status" above); a pass here only earns the engine run with `SimBroker`.

## 6. The live last-30-minutes SPY trade: absorbed or retired (declared now)

The live trade (`spy_momentum`, story S13: z of the close-to-10:00 move > 0.5, buy at 15:30, sell at the close) is
in effect the 15:30 check of this rule. The report shows two context rows: the round trips of version 1 whose
entry decision was the 15:30 check, and a replay of the live rule itself (`kaibot.replay.spy_momentum.run`, cost
model v1, gate sizing). The statement follows mechanically:

- version 1 passes marks 1-5 → **absorb**: the 15:30 check is part of the noise-band rule, and the separate live
  trade is removed when (and only when) the noise-band rule passes the engine run with `SimBroker` and goes live;
- version 1 fails, and the 15:30-check round trips alone have at least +1.5 bp net with t >= 2 at 0.5 bp a side
  and a positive sum at 1 bp a side → **neither yet**: the 15:30 entry needs its own pre-registration before
  anything changes;
- otherwise → **retire**: the live trade has no support from the rule that contains it (a recommendation to
  Product; this test changes nothing live).

## 7. Trials

Every run is a row in `data/trials.jsonl` with this file's sha256. A configuration counts once: version 1, version
2 and each neighbour at the base scenario are one configuration each (`n_trials` 1); the other cost scenarios and
the context rows are reports of them (`n_trials` 0).
