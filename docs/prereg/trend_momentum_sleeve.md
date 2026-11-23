# Pre-registration: trend_momentum_sleeve

- **Rule:** `trend_momentum_sleeve`
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #459 (M11 ticket 2.8)
- **Role:** sleeve
- **Kind:** selection
- **Family:** `cross_sectional_momentum` (its fifth rule: the family's Bonferroni factor becomes 5 for every later verdict)
- **Candidate pool:** every index member of the day that passes the rule's price and volume filter ($5, $50M 20-day dollar volume, point-in-time membership), before the rule's own trend and rank conditions are applied (decision 16 section 4, "a rule whose signal is the price alone")
- **Tier:** evidence-based (decision 16 section 14). In every report and wherever the book shows it, this sleeve is **evidence-based, not proven here**.

Written and committed together with the code that runs it, before any run. Changed only by appending
dated "## Amendment n" sections.

**What this is.** This is no new trading idea. It is `trend_momentum` (docs/prereg/trend_momentum.md, with every
amendment committed up to 22 Nov 2026, Amendment 25 the last), frozen as it trades on paper today. It gets a new
name for one reason only: the old pre-registration predates decision 16 and does not declare the gate v3 fields
(decision 16 section 8), so its re-score can never be a verdict. This file declares them. It also declares the
sleeve's evidence-based tier, so the sleeve can join the book `index_core_trend_momentum` at the 10% budget that
section 14 allows. Decision 15 section 11 is unchanged. `trend_momentum` failed Gate B and keeps trading on paper as
a labelled experiment, and real money stays barred for it, whatever this sleeve's verdict.

## Hypothesis

Large, liquid US stocks that have risen steadily for about four months keep rising for some weeks more often than
not (cross-sectional momentum). Held at the rule's own exposure, the sleeve earns more than the same amount of SPY.
Expected on the research window (2017-05-01 to 2024-12-31), net of costs: an annualised active return of -2 to +2
points against SPY held at the sleeve's own exposure, session by session. Stated before the run: the audit's
reading of the ten-year result (8.24% a year, Sharpe 0.59 against SPY's 0.83) makes a negative active return
likely. The audit also expects a long-only stock-picking sleeve to have an information ratio of about 0.25 to 0.4,
which S3 and S4 cannot confirm on this much data (decision 16 section 9). A fail of S1 is a real possibility and
will be reported as found.

## Who is on the other side of the trade

Investors who under-react to slowly arriving good news and sell winners too early (the disposition effect), and
funds that must rebalance away from what has risen. The edge, if any, is a risk premium with crash risk. It halved
after publication and is weakest in large caps (docs/prereg/trend_momentum.md section 1).

## Published evidence

The tier of decision 16 section 14 needs strong published, replicated evidence for the signal. For
cross-sectional momentum:

- Jegadeesh and Titman (1993, *Returns to Buying Winners and Selling Losers*, Journal of Finance): 3- to 12-month
  winners outperform losers over the next 3 to 12 months in US stocks, 1965 to 1989; their 2001 follow-up (*Profitability
  of Momentum Strategies*, Journal of Finance) finds the effect again out of sample, 1990 to 1998.
- Asness, Moskowitz and Pedersen (2013, *Value and Momentum Everywhere*, Journal of Finance): momentum in eight
  markets and asset classes.
- Fama and French (2012, *Size, value, and momentum in international stock returns*, Journal of Financial
  Economics): momentum in every region except Japan.
- Against it, and stated: published anomalies lose about half their return within ten years of the sample's end,
  and restricting them to tradable large stocks removes a further 25% to 35% (Chen and Zimmermann, via
  `docs/research/engine_audit/report.md`). Momentum funds roughly matched SPY over 2017 to 2026 (MTUM 13.6% a
  year against 13.7%, the audit's own sums). A stricter rule to enter than to stay raises net returns (Novy-Marx and
  Velikov, via the audit). This sleeve does not use one; ticket 2.9 tests that separately.
- Clenow's construction (*Stocks on the Move*) is a practitioner's book, not a peer-reviewed test. The evidence is
  for the signal family, not for this exact construction.

The 1993, 2001, 2012 and 2013 papers are cited from memory, not re-read for this file.

## Frozen settings

Every setting of docs/prereg/trend_momentum.md as amended to 22 Nov 2026 (Amendment 25), unchanged. That is: weekly
decisions (the first session of the week on a Wednesday or later), ranking by the 90-session exponential
regression slope times R-squared, buys only from the top 20% while SPY is above its 200-day average, sold out of
the top 20%, below the 100-day average or after a 15% one-day move in 90 sessions, sized k = 0.1% of equity per
ATR(20), at most 30 names, 15% per name (Balanced), a 40% sector cap, the 3% cash reserve, near-close market orders.

- **For the sleeve test** (decision 16 section 4): the sleeve's own account at its pre-registered exposure cap of
  100% (`engine.daily.momentum_builder`), one continuous $100,000 account through `engine.daily.run_daily` with
  `SimBroker`, on the point-in-time universe, on the research window. Benchmark `path_matched`, with idle cash in
  Treasury bills on both sides when the bill file is present.
- **Control** (S3): the exposure-matched random control of decision 15 section 10 (`daily_report.control_for`),
  drawn from the candidate pool above, **2,000 runs** (decision 16 section 15: a family of several rules), seed 459.
- **Inside the book** `index_core_trend_momentum`, the sleeve is the same rule at one tenth of its size (that
  book's pre-registration): k = 0.01%, 1.5% per name, at most 10% of the book, at most 29 stocks (SPY is the
  thirtieth name).

## Neighbours (at most five)

The five neighbours of docs/prereg/trend_momentum.md section 3, unchanged (mark S6: each has an active return above
zero):

- `reg60`: regression window 60 sessions
- `reg125`: regression window 125 sessions
- `exit30`: sold only out of the top 30%
- `nofilter`: no SPY 200-day filter for buys
- `risk15`: k = 0.15%

## Fill model

As docs/prereg/trend_momentum.md (Amendments 10 to 13): decide after the close of T, near-close DAY market orders at
15:50 ET of T + 1, filled at the closing print of T + 1 by `SimBroker`. Costs: the engine's daily print model
(`broker.sim.AuctionImpactCosts`, half a spread plus impact) at 1x, 2x for S2, 3x reported. As for `index_core`,
the report states its cost model as `engine_daily_print_v1_impact`, which `final_requires` (cost model v2) does not
name. The verdict is provisional until Product rules on that.

## Pass marks

Decision 16, as amended by section 15. S1, S2, S5 and S6 are judged. S3 (2,000 runs, Bonferroni over the five rules
of the family, Holm over the twelve families) and S4 (the deflated Sharpe against the family's configurations, with
the 1/(T-1) floor) are computed and printed, not judged. If they hold too, the verdict is `pass`; otherwise, with S1,
S2, S5 and S6 holding, it is `evidence_based`, labelled **evidence-based, not proven here**. Then comes the final
check of section 7, under one opening of the hold-out committed before it is read.

**Trials.** The sleeve's run and its five neighbours are re-runs of configurations already counted for
`trend_momentum` (decision 15 section 10). They are logged with n_trials 0 under this name, and the family's count
is unchanged.

## Amendment 1 (23 Nov 2026, quant research, before any run)

The size of the family. "Its fifth rule" (the header) and "the five rules of the family" (pass marks) were
written when `cross_sectional_momentum` listed four rules. Main has since added `trend_momentum_band` (#460) and
`trend_momentum_gbm` (#474), so the family lists six rules before this one and **seven** with it. The Bonferroni
factor of S3 is the number of rules `config/gate_v3.yaml` lists at the run (`g3.family_p`). That is seven at the
time of writing, and more if the family grows before the run. Nothing else changes.

The cost model, in the same amendment: Product's ruling of 22 Nov 2026 (#559) and #572 put cost model v2 into the
engine's daily path, so "Fill model" changes only in the model that prices the prints. The sleeve is run with
`engine/daily.run_daily(cost_model="v2")` (`broker.sim.AuctionCostsV2`). 2x for S2 and 3x are v2's multiples, and the
stress fill is v2's opening venue. The report and the trial rows record `cost_model: v2`, which `final_requires`
names, so the verdict is no longer provisional on this count.
