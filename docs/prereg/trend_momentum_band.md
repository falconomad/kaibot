# Pre-registration: trend_momentum_band

- **Rule:** `trend_momentum_band`
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #460 (M11 ticket 2.9)
- **Role:** sleeve
- **Kind:** selection
- **Family:** `cross_sectional_momentum` (one more rule in the family: its Bonferroni factor grows by one for every later verdict)
- **Candidate pool:** every index member of the day that passes the rule's price and volume filter ($5, $50M 20-day dollar volume, point-in-time membership), before the rule's own trend and rank conditions
- **Tier:** evidence-based (decision 16 section 14). In every report and wherever a book shows it, this sleeve is **evidence-based, not proven here**.

Pre-registration only, committed before any code that runs it and before any run. The code (a `TrendMomentumBand`
strategy with tranches, its run script) comes in later pull requests and cannot change anything written here. Changed
only by appending dated "## Amendment n" sections. The book this sleeve joins (an index core with this sleeve as an
over-weight) is pre-registered separately, with its budget, under `books:` of `config/gate_v3.yaml`, before the
book's first run. That waits for tickets 2.7 and 2.8 (#548, #550).

**Why this rule.** The audit (`docs/research/engine_audit/report.md`, "Strategies that hold up are dull"; Stage 2, row
2.9) names two construction faults of `trend_momentum`. First, it enters and leaves at the same 20% line every week,
and a stricter rule to enter than to stay cut momentum's monthly turnover from 34.5% to 18.8% and raised net return
(Novy-Marx and Velikov). Second, it trades on one fixed weekday, and the same rule run on each of the 21 trading days
of a month returned between 4.7% and 8.1% a year (Alpha Architect); a 2026 re-test recommends splitting a book into
tranches traded on different days (Concretum). This rule changes those two things and nothing else. **Hindsight,
stated:** the authors know `trend_momentum`'s ten-year result (8.24% a year, Sharpe 0.59). The changes are the
audit's published remedies, written down here before any run of them, not chosen from runs.

## Hypothesis

`trend_momentum`'s signal, entered only in the top 10% and held until it leaves the top 20%, rebalanced monthly in
four tranches a week apart, earns more than the same amount of SPY at clearly lower turnover than `trend_momentum`.
Expected on the research window (2017-05-01 to 2024-12-31), net of costs, as a sleeve on its own account: an
annualised active return of -2 to +2 points against SPY held at the sleeve's own exposure, and **turnover at most
two thirds of `trend_momentum`'s** in the same window. A negative active return is a real possibility: momentum
funds roughly matched SPY over 2017 to 2026 (the audit's sums). S3 and S4 cannot confirm an information ratio of
about 0.25 to 0.4 on this much data (decision 16 section 9).

## Who is on the other side of the trade

As for `trend_momentum` (docs/prereg/trend_momentum.md section 1): investors who under-react to slowly arriving news
and sell winners too early, and funds that must rebalance away from what has risen. The band and the tranches change
no counterparty. They trade less often and spread the trades over the month, so less of the edge goes to the spread.

## Published evidence

- Cross-sectional momentum, as in docs/prereg/trend_momentum_sleeve.md (#550): Jegadeesh and Titman (1993, 2001);
  Asness, Moskowitz and Pedersen (2013); Fama and French (2012), cited from memory, not re-read for this file.
- The band: Novy-Marx and Velikov, *A Taxonomy of Anomalies and Their Trading Costs* (via the audit): a buy-hold
  spread (enter in the top 10%, stay until out of the top 20%) cut monthly turnover from 34.5% to 18.8% and raised the
  net return.
- The tranches: Alpha Architect, the day of the month a rule trades (via the audit): 4.7% to 8.1% a year across the
  21 trading days for the same ten-month rule; Concretum (2026, via the audit) recommends tranches traded on
  different days.
- Against it, stated: published anomalies lose about half their return after publication, and more in large stocks
  (Chen and Zimmermann, via the audit).

## Frozen settings

| # | Setting | Value | From |
|---|---|---|---|
| 1 | Universe, price and volume filter, point-in-time membership | as `trend_momentum` (docs/prereg/trend_momentum.md section 2, as amended to 22 Nov 2026) | |
| 2 | Score | `trend_momentum`'s: the annualised 90-session exponential regression slope times R-squared, on split-adjusted closes | unchanged |
| 3 | Not buyable, and sold | below its 100-day average, or a one-day move of 15% or more in the last 90 returns (as `trend_momentum`) | unchanged |
| 4 | Market filter | no new buys while SPY is not above its 200-day average; sales go through (as `trend_momentum`) | unchanged |
| 5 | **Entry** | only in the **top 10%** of the ranked universe | Novy-Marx and Velikov |
| 6 | **Exit** | sold when it is **out of the top 20%** (or by row 3) | Novy-Marx and Velikov |
| 7 | **Tranches** | **four** tranches, each a quarter of the sleeve. Tranche t (t = 0, 1, 2, 3) decides only on the weekly decision days (`momentum.is_weekly_decision`) of the weeks with `week_number % 4 == t`, so each tranche rebalances about monthly and the four are a week apart | Alpha Architect; Concretum |
| 8 | Sizing inside a tranche | shares = equity x k / 4 / ATR(20), k = 0.1% (a quarter of `trend_momentum`'s risk per tranche), whole shares, at most 8 names a tranche (30 / 4, rounded up), per-name cap 15% across all tranches together (Balanced), 40% sector cap across the sleeve, the 3% cash reserve | `trend_momentum`'s sizing, split in four |
| 9 | Resize | a tranche's held names are resized on its own decision days only, outside the 10% buffer | `trend_momentum`'s buffer |
| 10 | Exposure cap (as a sleeve on its own account) | 100% (the pre-registered sizing of `trend_momentum`) | |
| 11 | Orders, fills, costs | near-close DAY market orders at 15:50 ET of the session after the decision, filled at the closing print by `SimBroker`; the engine's daily print model (`AuctionImpactCosts`) at 1x, 2x and 3x; stated as `engine_daily_print_v1_impact` | as `trend_momentum` |
| 12 | Account, window, universe | one continuous $100,000 account through `engine.daily.run_daily`, data from 2016-01-04, research window 2017-05-01 to 2024-12-31, point-in-time store. The hold-out is not read | decision 16 section 3 |
| 13 | Control | the exposure-matched random control from the candidate pool, **2,000 runs** (decision 16 section 15), seed 460 | |

## Neighbours (at most five)

Each must have an active return above zero (mark S6):

- `noband`: entry and exit both at the top 20% (only the tranches change from `trend_momentum`)
- `exit30`: sold only when out of the top 30%
- `onetranche`: one tranche, decided on the first weekly decision day of each month, sized as the whole sleeve
- `twotranches`: two tranches two weeks apart
- `nofilter`: no SPY 200-day filter for buys

## Fill model

Decide after the close of T on a tranche's decision day. Send near-close DAY market orders at 15:50 ET of T + 1, filled
at the closing print of T + 1 (`SimBroker`, `near_close`), at the engine's daily print model at 1x. Stress fill:
the opening print of T + 1 at twice the cost, reported. A sale of one tranche and a buy of the same name by another
tranche on the same close are netted into one order per symbol (decision design.md 2.7: one net order per symbol and
close).

## Pass marks

The sleeve test of decision 16 (section 4) as amended by section 15, under the evidence-based tier of section 14:
S1, S2, S5 and S6 are judged, and S3 and S4 are computed and printed. The verdict is `pass` if all six hold, otherwise
`evidence_based` (labelled "evidence-based, not proven here") if S1, S2, S5 and S6 hold, otherwise a fail. Also
stated now and judged with it: **turnover** (the value traded a year over the mean equity) at most two thirds of
`trend_momentum`'s on the same window and data. If it is not lower, the rule has not done what it was built for, and
the report says "fails its own turnover mark". Then comes the final check of section 7, under one opening of the
hold-out. As an over-weight inside a book, the book test of its own pre-registration applies.

**Trials.** Six configurations count (n_trials 1): the rule and its five neighbours. Cost and fill reports count 0.

## Amendment 1 (22 Nov 2026, quant research, before any code and any run): the tranches' calendar and the resize, made exact

Written before the code that runs this rule exists and before any run. It settles two settings that rows 7 and 9
left open to interpretation. Nothing else changes.

1. **Calendar (row 7 and the neighbours `onetranche`, `twotranches`).** There are four weekly slots, the weeks with
   `week_number % 4` = 0, 1, 2, 3, decided on their weekly decision day (`momentum.is_weekly_decision`). With n
   tranches (n = 1, 2 or 4), tranche t (t = 0 .. n - 1) decides in the slot `t x 4 / n`. So the rule (n = 4) uses
   slots 0, 1, 2 and 3, `twotranches` slots 0 and 2, and `onetranche` slot 0: every tranche rebalances once every
   four weeks. This replaces "the first weekly decision day of each month" for `onetranche`, so that the three
   versions differ only in the number of tranches.
2. **Sizing and resize (rows 8 and 9).** The book holds one position per stock, as the venue does (one net order per
   symbol and close). A stock's target weight is the number of tranches that hold it divided by n. Its size is
   equity x 0.1% x weight / ATR(20), so each tranche adds a quarter of `trend_momentum`'s risk at n = 4. On every
   tranche decision day the whole book is sized at once by the portfolio builder's ranked fill: held stocks are
   resized outside the 10% buffer (`TargetList.resize` true), and new stocks are bought best first. Row 9's "resized
   on its own decision days only" is replaced by this, because the engine keeps one position per symbol and cannot
   resize one tranche's share of it alone. At most `ceil(30 / n)` stocks a tranche, and at most 30 in the book.
3. **What a tranche remembers.** Each tranche keeps the list of stocks it chose, in its rank order, from one of its
   decisions to the next. A stock leaves a tranche only at that tranche's decision (row 6, or row 3's conditions on
   that day). A stock that the builder could not buy stays in the tranche's list and is listed again for purchase
   at every decision until the tranche drops it.
