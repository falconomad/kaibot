# Pre-registration: index_core_trend_momentum

- **Rule:** `index_core_trend_momentum`
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #459 (M11 ticket 2.8)
- **Role:** book
- **Kind:** none (a book is judged by the book test of decision 16 section 5)
- **Family:** none (listed under `books:` of `config/gate_v3.yaml`)
- **Candidate pool:** none for the book. The sleeve's pool is in docs/prereg/trend_momentum_sleeve.md
- **Components:** the SPY index core and its 15% volatility cap (docs/prereg/index_core.md, unchanged), and one return sleeve, `trend_momentum_sleeve`
- **Sleeves and budgets:** `trend_momentum_sleeve`: **10% of the book**, evidence-based, not proven here (decision 16 section 14: at most 10% for all such sleeves of a book together)

Written and committed together with the code that runs it, before any run. Changed only by appending
dated "## Amendment n" sections. **Tested once as a combined book** (ticket 2.8): one account through the engine with
`SimBroker`, shared cash, one cap per name. It is not a weighted sum of the core's and the sleeve's results.

## Hypothesis

The index core with a momentum sleeve at a fixed 10% budget earns close to SPY's return with a clearly shallower
drawdown, like the core alone, and the sleeve changes the book's result by little. Expected at Hunter on the
research window, net of costs, against SPY total return: **CAGR SPY -3 to -1 points a year, maximum drawdown 0.65
to 0.85 times SPY's**, within about half a point a year of `index_core`. A 10% sleeve with an active return of a
points a year moves the book by about 0.1 x a. The book is mostly the core, and its verdict will mostly be the
core's. That is the purpose of a budget: the sleeve's risk is bounded whatever its skill.

## Who is on the other side of the trade

For the core: nobody has to be wrong (docs/prereg/index_core.md). For the sleeve: as in
docs/prereg/trend_momentum_sleeve.md. Each sleeve buy is funded by selling SPY at the same close, and each sleeve
sale goes back into SPY at the next decision. Every swap pays two extra legs, the SPY sale and the buy-back,
at the print's cost.

## Published evidence

The core: docs/prereg/index_core.md. The sleeve: docs/prereg/trend_momentum_sleeve.md. Sleeve-on-core evaluation is
the audit's fifth innovation (`docs/research/engine_audit/report.md`, "Six innovations"; decision 16 section 2).

## Frozen settings

| # | Setting | Value |
|---|---|---|
| 1 | The core | every setting of docs/prereg/index_core.md: SPY, its own cap `max_index_pct` (27 / 57 / 97% by level, the stakeholder's to confirm), w = min(1, 0.15 / sigma) on 21 returns, the cuts of its row 8, the 10% band |
| 2 | The sleeve | `trend_momentum` as frozen in docs/prereg/trend_momentum_sleeve.md, scaled to one tenth: k = **0.01%** of equity per ATR(20) (0.1% x 10 / 100), per-name cap **1.5%** (15% x 0.1), everything else unchanged (weekly decisions, ranking, top 20% in and out, the 100-day and 15% conditions, the SPY 200-day filter for its buys, resizes every second week outside a 10% buffer) |
| 3 | Budget | the sleeve's stocks together at most **10% of equity** (`PortfolioBuilder(sleeve_budget_pct=10)`). Buying stops at the budget, best-ranked first |
| 4 | Names | at most 30 in the book, SPY counted as one: at most 29 stocks |
| 5 | Sector cap | 40% for the sleeve's stocks (Amendment 4 of the momentum pre-registrations); SPY is in no sector |
| 6 | One decision | after the close of the weekly decision day: the sleeve's list and the core's weight are decided together on the same bars, and one plan is sized for both |
| 7 | The swap | the plan sells, then reduces SPY, then resizes and buys stocks, then raises SPY. A sleeve buy is funded at the same close by the SPY sale. When the sleeve's market filter is off it buys nothing and its money stays in SPY |
| 8 | Exposure cap, cash, gap scenario | as the core: the level's cap (Hunter 100%), the 3% reserve, the 15% gap limit across the whole book |
| 9 | Judged level | Hunter (97% index cap, 100% exposure cap). Balanced and Careful are reported |
| 10 | Orders, costs, cash, dividends, account, window, universe | as docs/prereg/index_core.md, rows 10 to 17 |

## Neighbours (at most five)

Each must pass B1 (mark B5):

- `budget5`: the sleeve at 5% of the book (k = 0.005%, 0.75% per name)
- `nocap`: the core without its volatility cap (w = 1)
- `vol12`: the core's volatility target 12%
- `vol18`: the core's volatility target 18%
- `lookback63`: the core's volatility over 63 returns

## Fill model

As docs/prereg/index_core.md: near-close DAY market orders at 15:50 ET of the session after the decision, filled at
the closing print by `SimBroker`, priced by the engine's daily print model (`AuctionImpactCosts`) at 1x; 2x and 3x
reported, 0x for the break-even; the stress fill at the open, reported. The source states
`engine_daily_print_v1_impact` (see the note to Product in docs/prereg/index_core.md).

## Pass marks

The book test of decision 16 section 5 as amended by section 15, judged at Hunter: B1 to B6, all must hold. B6: the
sleeve `trend_momentum_sleeve` has the verdict `pass`, or `evidence_based` with its 10% budget as listed under
`books:`. A sleeve that fails, or is incomplete or provisional, fails B6. B2 may hold on either branch. The
hypothesis is the defensive one. After that comes the final check of section 7 for the book and for the sleeve,
under one opening of the hold-out each, committed before it is read. Printed beside the marks: the same book without
its sleeve (`index_core`, from its own report), the share of sessions the sleeve held anything, the sleeve's mean
share of the book, SPY orders a year, and turnover.

**Trials.** Six configurations count (n_trials 1): the judged book and its five neighbours. The sleeve test's runs
are counted as in docs/prereg/trend_momentum_sleeve.md (0). Cost, fill and level reports count 0.

## Amendment 1 (23 Nov 2026, quant research, before any run on real data)

The cost model, as in docs/prereg/index_core.md Amendment 2. Product ruled on 22 Nov 2026 (#559) that gate v3's
final verdicts use cost model v2, and #572 put it into the engine's daily path. The book and its neighbours run with
`engine/daily.run_daily(cost_model="v2")` (`broker.sim.AuctionCostsV2`). 2x and 3x are v2's multiples, 0x is the
break-even, and the stress fill is v2's opening venue. The source and every trial row record `cost_model: v2`. The
v1 print model is not run. Nothing else changes.

## Amendment 2 (23 Nov 2026, quant research, before any run on real data)

The core's index cap, as in docs/prereg/index_core.md Amendment 3: the stakeholder set `max_index_pct` to **27% at
every level** (issue #600). Row 1 reads "27% at every level", and row 9 reads "Hunter (27% index cap, 100% exposure
cap)". The sleeve's 10% budget, the neighbours, the pass marks and the trial count are unchanged. With at most 27% in
SPY and 10% in the sleeve, most of the book is cash at 0%, and the book test is judged on that.

## Amendment 3 (25 Nov 2026, quant research, before any run at this cap)

The core's index cap, as in docs/prereg/index_core.md Amendment 4, which gives the reasons, the hindsight and the
research-only scope. This amendment is written after the 24 Nov 2026 run at 27% was seen (job
`index-core-book-2026-11-24`, #459: book CAGR 4.15%, maximum drawdown 6.61%, fail). Product decided on 25 Nov 2026 to
test the book once more with its core allowed up to **80% of the book**: `scripts/index_core_book.py --index-cap 80`.

- Row 1 and row 9: the core's index cap is **80% at Hunter** (judged), under the 100% exposure cap. Balanced and
  Careful are reported at min(80, the exposure cap less the 3% reserve), so **57%** and **27%**.
- The sleeve (`trend_momentum_sleeve`, 10% budget, k = 0.01%, 1.5% per name), the swap, the budget check against
  `books:` of `config/gate_v3.yaml`, the five neighbours (each at 80%), the fill model (cost model v2) and the pass
  marks are all unchanged. The research window is unchanged (2017-05-01 to 2024-12-31), and the hold-out is not read.
- The sleeve test inside the script runs the same configurations as on 24 Nov 2026, with the same control seed. They
  stay n_trials 0, as before.
- No setting changes: the live cap and the hard ceiling stay 27%, and `config/index_core.yaml` is not touched.

**Expectation, before the run.** As the core at 80% (docs/prereg/index_core.md Amendment 4: SPY -6 to -3 points
a year, drawdown about 0.35 to 0.55 times SPY's). The sleeve moves the book by about 0.1 times its active return.
B2 is expected to fail on return. B6 depends on the sleeve's verdict, which this change does not move.

**Trials.** Six new configurations count (n_trials 1): the judged book at 80% and its five neighbours. The six of
24 Nov 2026 stay counted, so the book `index_core_trend_momentum` has tried **12 configurations**. The trial rows
record `params.index_cap_pct` (80), and the report is named `..._cap80_<ts>`. The 27% verdict (fail) stands. A
pass allows only a proposal to the stakeholder, as in the core's Amendment 4.

## Amendment 4 (26 Nov 2026, backend-quant, issue #705, after the runs of 24 and 25 Nov 2026): the design saw the hold-out

As docs/prereg/index_core.md Amendment 5. This book's core is that core, and its 15% volatility target came from
sums over 2017 to 2026, which include the hold-out:

- **design_saw_holdout:** true (the core's volatility target was set from 2017-2026 sums that include the hold-out)

No setting, neighbour, mark or run changes. The final check on the hold-out, if it is ever opened, is labelled a
consistency check.

## Amendment 5 (26 Nov 2026, backend-quant, issue #706, after the runs of 24 and 25 Nov 2026): the control book

Gate v3.1 adds mark B7 (docs/decisions/18_gate_v3_1.md section 10, M17 ticket 6.6), and this book's verdict depends
on it, so it is pre-registered here before the run that first computes it. This amendment is written after the 24
and 25 Nov 2026 results were seen (27%: book CAGR 4.15%; 80%: 10.87% against the core's 10.09%; the sleeve's
selection effect against exposure-matched SPY −1.14% a year). Gate v3.1 prints it as hindsight.

- **The control book:** `strategies.index_core.ControlBook`, the same core (15% volatility target, 21 sessions,
  weekly) decided on the book's own days, with the sleeve's whole **10% budget held in SPY** instead of stocks. Its
  index cap is the core's cap plus the budget (**90% at Hunter** when the core is at 80%, 37% at 27%), so it holds
  w × C + 10 % of equity in SPY. Hunter, 1x costs, cost model v2, the same fill model and window.
- **Start offsets:** the book and the control book are each run from **0, 5, 10, 15 and 20 sessions** after the
  window's first session (`book.b7_control_book.start_offsets_sessions` of `config/gate_v3.yaml`), to the same end.
  Offset 0 of the book is the judged run, `book|1x`.
- **B7:** the book's CAGR minus the control book's, in points, is **above 0 at the median of the five offsets**.
  Fewer than five offsets: unknown. The spread (median, min, max, range, standard deviation) of the CAGR, Sharpe and
  drawdown differences is reported, not judged. The sleeve's contribution is stated as book minus control book.
- **Unchanged:** the sleeve, its budget, the neighbours, B1 to B6, the window (2017-05-01 to 2024-12-31) and the
  hold-out, which is not read. `design_saw_holdout` stays true (Amendment 4).

**Expectation, before the run.** The audit's reading: "The sleeve adds exposure, not skill. The same budget held in
SPY would probably have done as well." A −1.14% a year selection effect on a 10% budget is about −0.1 points a year
on the book, inside one-path noise, so B7 is expected near 0 and more likely to fail than to hold. The verdict is
already a fail on B2 and B6 at both caps; B7 cannot change that.

**Trials.** The control book (a benchmark) and the book at offsets 5 to 20 (paths of a counted configuration) log
n_trials 0. The re-run at 80% (job `index-core-book-80-control-2026-11-26`, posting to #706) repeats the six
configurations of 25 Nov 2026, and the script logs them with n_trials 1 again, so the idea's N becomes 18, not 12.
Counting a repeat can only raise B2's hurdle, never lower it.
