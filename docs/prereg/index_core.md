# Pre-registration: index_core

- **Rule:** `index_core`
- **Status:** pre-registered
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #458 (M11 ticket 2.7)
- **Role:** book
- **Kind:** none (a book is not a sleeve: it is judged by the book test of decision 16, section 5)
- **Family:** none (listed under `books:` of `config/gate_v3.yaml`, decision 16 section 15, item 5)
- **Candidate pool:** none (no return sleeve, so no control: decision 16 section 5, "Gate v2's mark 3 is not asked of the book")
- **Components:** an SPY index core, and a volatility cap on the core as its risk overlay
- **Sleeves and budgets:** none (`sleeves: {}`)

Written and committed BEFORE any code that runs it, and before any run of it on synthetic or real data. The code
(`risk/portfolio.py` index mode, `strategies/index_core.py`, `scripts/index_core.py`) comes in later commits of the
same pull request. This file is changed only by appending dated "## Amendment n" sections.

**What this file decides and what it does not.** Product accepted an SPY index core in principle on 21 Nov 2026
(decision D3 of `docs/research/engine_audit/decisions.md`): it goes live only after this pre-registration, a run
through the engine with `SimBroker`, and two keys (Backend-quant and QA-Ops). The stakeholder decided the same day
that index funds get their own cap, separate from the per-name cap. **The size of that cap per level is a level
limit for multi-day books, and only the stakeholder sets those** (decision 15 section 6, decision 16 section 13). The
values below (27 / 57 / 97%) are the proposal of `docs/research/event_driven/design.md` section 2.9. They are frozen
here for the test only. If the stakeholder confirms other values, those values are a new configuration: a new trial
and an amendment written before it runs. No live setting changes with this file. `config/index_core.yaml` ships
with `enabled: false`, and the live profiles (`config/profiles.yaml`) do not change.

**Hindsight, stated plainly.** The research window (2017-05-01 to 2024-12-31) is history the authors know: the 2018,
2020 and 2022 falls are common knowledge, and the audit's own monthly sums (report, "Strategies that hold up are dull")
already put a 15% volatility cap on SPY at 11.7% a year against 13.7%, with about a quarter less drawdown, over
2017 to 2026. That is why the volatility target below is the one the audit already wrote down, the lookback is the
published one-month window, and the neighbours are the textbook alternatives around them, declared now. Nothing may
be chosen after the first run.

## Hypothesis

An account that holds SPY with the cash a book does not use, at most `max_index_pct` of equity, and that scales the
SPY holding down when SPY's realised volatility is above 15% a year, earns close to SPY's return with a clearly
shallower drawdown. Expected net of costs, at Hunter (97% cap) on the research window, against SPY total return:
**CAGR SPY -3 to -1 points a year, maximum drawdown 0.65 to 0.85 times SPY's.** Whether that meets the defensive
branch of B2 (CAGR at least SPY -2 points AND drawdown at most 0.75 times SPY's) is uncertain before the run; a fail
on B2 is a real possibility and will be reported as found. The plain core without the cap (neighbour `nocap`) is
expected to sit about 0.5 to 1 point a year behind SPY (3% cash and costs) with about 0.97 times its drawdown: it
cannot meet the defensive branch, and it is not meant to. Because idle cash earns 0% in the judged run (decision 16
section 3, book test), every point of the book in cash costs its share of SPY's return. At Balanced (57%) and Careful
(27%) the book trails SPY by construction and cannot meet B2. Those two levels are reported, not judged.

## Who is on the other side of the trade

Nobody has to be wrong for the core. It is the market itself: SPY is bought from and sold to the ETF's
market makers at the near-close print, and the core earns the index's return less its costs. No skill is
claimed and none is tested, so the book has no random control (decision 16 section 5). The volatility cap is
insurance, not an edge. It sells SPY to buyers who supply liquidity when volatility is high and buys it back when
volatility falls. Its price is the return lost when the market recovers fast after a fall (spring 2020). The
published record (next section) says the cap reduces drawdowns and the left tail without raising the Sharpe ratio
in real time.

## Published evidence

- Holding the index: about 4% of US firms account for all the wealth created above Treasury bills since 1926
  (Bessembinder, via the audit). 79% of active US large-cap funds trailed the S&P 500 in 2025, and a majority beat it in
  only 3 of 25 years (S&P Indexology, via the audit). The audit lists cash drag as the largest of its nine
  structural reasons: about 0.14 points a year for every point of idle cash (`docs/research/engine_audit/report.md`,
  "Why long-only stock picking lost to the index in this decade").
- Volatility targeting without borrowing: no higher Sharpe ratio in real time (0.42 against 0.46 for the unmanaged
  market), but smaller drawdowns (Cederburg, O'Doherty, Wang and Yan, *On the performance of volatility-managed
  portfolios*, via the audit). Moreira and Muir (2017, *Volatility-Managed Portfolios*, Journal of Finance) scale by
  the previous month's realised variance. Harvey, Hoyle, Korgaonkar, Rattray, Sargaison and Van Hemert (2018, *The
  Impact of Volatility Targeting*, Journal of Portfolio Management) find that for equities the target reduces the
  left tail and the drawdowns more than it changes the Sharpe ratio. These two are cited from memory, not re-read for
  this file. A stock, bond and gold mix diluted with cash to a 7% volatility target reached a Sharpe of 0.82 and a
  15.9% drawdown against SPY's 0.46 and 55.2% over 2006 to 2026 (Boyd et al., via the audit). That mix is out of
  scope: the stakeholder excluded non-stock diversifiers on 21 Nov 2026.
- The audit's own monthly sums (2017 to 2026, no costs, not a published source): a 15% volatility cap on SPY at 11.7%
  a year against 13.7%, with an unchanged Sharpe and about a quarter less drawdown.

## Frozen settings

| # | Setting | Value | Where from |
|---|---|---|---|
| 1 | Instrument | the universe's market symbol, `market` in `config/universe_history.yaml`: SPY. It is the only index fund (`index_symbols: [SPY]` in `config/index_core.yaml`) | design.md 2.9, item 2 |
| 2 | The index fund's own cap, `BookLimits.max_index_pct` (% of equity) | Careful 27, Balanced 57, Hunter 97. Hard ceiling 97 in `BOOK_HARD_CEILINGS` (100% less the builder's 3% cash reserve; the 1% floor `min_cash_pct` stays). Separate from the per-name cap `max_name_pct` (10 / 15 / 20%), which does not apply to the index fund | design.md 2.9 (proposal; the stakeholder confirms the level values) |
| 3 | Exposure cap of the book (gross and overnight) | Careful 30, Balanced 60, Hunter 100 (the level caps of decision 15 section 11) | decision 15 |
| 4 | Judged level | **Hunter: 97% index cap under the 100% exposure cap.** Balanced and Careful are reported, not judged | the goal (SPY-like return) is reachable only near full investment |
| 5 | Decision | after the close of the first session of each week that falls on a Wednesday or later (`strategies/momentum.is_weekly_decision`, the live rule's calendar), on daily bars up to that close | the live rule's calendar; neighbour `daily` |
| 6 | Volatility estimate | sigma = the standard deviation (n - 1) of SPY's last **21** daily close-to-close returns (split-adjusted closes without dividends, the signal side of the daily store), times sqrt(252). No estimate (fewer than 21 finite returns, or sigma not above 0): no scaling (weight 1) | Moreira and Muir: the previous month; neighbour `lookback63` |
| 7 | Weight of the index | w = min(1, 0.15 / sigma): **a 15% volatility target**, never above 1 (no borrowing) | the audit's 15% cap; neighbours `vol12`, `vol18`, `nocap` |
| 8 | Size of the index holding | target dollars = w x `max_index_pct` x equity at the previous close mark, then cut, each cut only making it smaller, to: (a) the exposure cap less everything else held; (b) the cash plus the SPY held, less the 3% cash reserve x equity (the builder's Amendment 3 reserve); (c) the room left under the gap scenario (15% of equity; SPY's gap = max(10%, 3 x ATR20)). Whole shares, rounded down. Not volatility-sized per position (the k x ATR rule does not apply), not in any sector bucket, counts as one name | design.md 2.9, items 2 and 3 |
| 9 | Trade band | the held shares are kept when they are within 10% of the target and fit every cut of row 8; otherwise the holding is traded to the target | the builder's 10% buffer |
| 10 | Orders | near-close DAY market orders at 15:50 ET of the session after the decision, filled at the closing print (`SimBroker(near_close)`, the live path's orders since #333) | decision 15 section 12 |
| 11 | Costs | the engine's daily print model (`broker.sim.AuctionImpactCosts`: half a spread of at least 1 bp, plus the model's impact), at 1x; 2x and 3x reported, 0x for the break-even | the momentum pre-registrations' fill model |
| 12 | Cash | 0% in the judged run (what the paper account shows); the Treasury-bill version printed beside it | decision 16 section 3 |
| 13 | Dividends | SPY's cash dividends credited at the ex-date (the ledger, from the daily store) | as every daily run |
| 14 | Protective stops, run ends | none in the test: no GTC stop (`protect_atr` off, as in every Gate B run of a daily rule) and no book run end (the runner has none; ticket 2.10 replaces the live one) | |
| 15 | Account | one continuous $100,000 account from flat, through `engine.daily.run_daily` with `SimBroker`; data from 2016-01-04 for the warm-up | |
| 16 | Window | research window 2017-05-01 to 2024-12-31 (`config/gate_v3.yaml`); the hold-out (from 2025-01-01) is not read | decision 16 section 3; `config/holdout.yaml` |
| 17 | Universe | the point-in-time daily store (`--universe pit`). The core holds only SPY, so B4 is the run itself | |

## Neighbours (at most five)

Each changes one setting of the judged configuration (Hunter, 97%). Each must pass B1 (decision 16, mark B5).

- `vol12`: volatility target 12% instead of 15%
- `vol18`: volatility target 18% instead of 15%
- `lookback63`: volatility over 63 daily returns instead of 21
- `daily`: a decision after every close instead of weekly
- `nocap`: no volatility cap (w = 1 always): the plain index core

## Fill model

Decide after the close of session T (the weekly decision day), send near-close DAY market orders at 15:50 ET of
T + 1, and fill them at the official closing print of T + 1 (`SimBroker` with `near_close`). Cost model: the engine's
print model at 1x, `broker.sim.AuctionImpactCosts` (half a spread `max(1 bp, half a cent)` plus the impact term on
the last minute's volume). Stress fill: the opening print of T + 1 at twice that spread and impact (`fill="stress"`),
reported. Note for Product: `config/gate_v3.yaml` `final_requires` names `cost_model: v2`. The engine's daily runner
prices the prints with this model, not with `broker.costs_v2`. The report states its model as
`engine_daily_print_v1_impact`, which makes the result `provisional` until Product rules whether the daily runner's
print model meets `final_requires` (for SPY it charges at least 1 bp a side, four times the index-fund bucket of
`config/costs_index_bucket.yaml`, so it is the stricter of the two).

## Pass marks

The book test of decision 16 (section 5) and `config/gate_v3.yaml`, as committed together with this file (amended by
decision 16 section 15 when that is merged): B1 to B6, all must hold, on the research window, judged at Hunter.
B6 holds with no sleeves (`sleeves: {}`). B2 may pass on either branch. The hypothesis is the **defensive**
branch. Then the final check of decision 16 section 7, under one opening of the hold-out committed to
`config/holdout.yaml` before it is read. Nothing is loosened here. Printed beside the marks and not judged:
Balanced (57%) and Careful (27%), the stress fill, 2x and 3x costs, the break-even cost multiple, the
Treasury-bill cash version, the bootstrap intervals of B2, the share of sessions the volatility cap bound, the mean
index weight, turnover, and the SPY orders a year.

**Trials.** Six configurations count (n_trials 1 each): the judged run and its five neighbours. Cost multiples, the
stress fill and the two other levels are reports of the same configuration (n_trials 0, decision 15 section 10).
Every run writes one row to `data/trials.jsonl` with this file's sha256.

**What a pass would allow, and what not.** A pass lets Product propose the index core for paper trading, with two
keys and the stakeholder's confirmation of the level caps (row 2). A fail is reported as found. The book then does
not trade, and nothing in it is re-run with changed settings: a changed construction is a new pre-registration.

## Amendment 1 (22 Nov 2026, QA-Ops, before any run on real data)

The pull request is squash-merged, so on main this file arrives in the same commit as the code that runs it, not
in an earlier one (the preamble's "later commits of the same pull request"). What the rule asks still holds: no run
of the index core on real data happened before that commit, no report and no `trial` row of it exists, and every
fixture run (CI) ran on a checkout with this file committed. `momentum_limits` (engine/daily.py) now sets
`max_index_pct` to 0, so only `risk/index_core.book_limits` gives a book an index cap. Nothing in the frozen
settings, the neighbours or the pass marks changes.

## Amendment 2 (23 Nov 2026, quant research, before any run on real data)

The cost model, following Product's ruling of 22 Nov 2026 (#559: gate v3's final verdicts use cost model v2) and
PR #572, which put v2 into the engine's daily path. Row 11 and "Fill model" change only in the model that prices the
prints. The judged run and its neighbours use `engine/daily.run_daily(cost_model="v2")`, `broker.sim.AuctionCostsV2`:
the ADV bucket of the session before the fill (SPY's index-fund bucket of `config/costs_index_bucket.yaml`), the
closing auction venue, capped impact, and the SEC and FINRA fees. 2x and 3x are v2's multiples, 0x is still the
break-even, and the stress fill is v2's opening venue (no second doubling, as #572 defines it). The report and every
trial row record `cost_model: v2`, which `final_requires` needs, so the result is no longer `provisional` on this
count. The decision rule, the sessions, the volatility cap, the neighbours, the pass marks and the trial count are
unchanged. The v1 print model is not run.

## Amendment 3 (23 Nov 2026, quant research, before any run on real data)

The stakeholder set the level values (issue #600, 23 Nov 2026). The index fund's cap `max_index_pct` is **27% at
every level** (Careful, Balanced and Hunter), not the 27 / 57 / 97% proposal of row 2. As the introduction says, this
is a new configuration, written down before it runs: `config/index_core.yaml` carries 27 for each level. Row 2,
row 4 (Hunter: a 27% index cap under the 100% exposure cap) and the neighbours (each changes one setting of Hunter
at 27%) are read with that value. The hard ceiling `BOOK_HARD_CEILINGS.max_index_pct` is lowered from 97% to 27%
too (Product, 23 Nov 2026), so a config edit can never raise the cap above 27%. Every other row is unchanged, and so
are the live profiles (no index fund) and `enabled: false`.

**What this does to the expectation.** At most 27% of the book is in SPY at Hunter. The rest is cash, which earns 0%
in the judged run. The hypothesis's range (SPY -3 to -1 points a year) was written for 97%, and it no longer holds:
the book is expected to trail SPY by about two-thirds of SPY's return, with roughly a quarter of its drawdown. It
is not expected to meet B2's return branch. A fail is reported as found. Any later change of the value is the
stakeholder's, as a new configuration with its own amendment.

## Amendment 4 (25 Nov 2026, quant research, before any run at this cap)

**Why, and what is known.** The 24 Nov 2026 runs at the 27% cap (job `index-core-2026-11-24`, #458) failed: CAGR
3.38% and a 5.13% maximum drawdown at Hunter, with about three-quarters of the book in cash at 0%. As Amendment 3
expected, that configuration cannot answer the question this file asks (an index core near SPY's return with a
shallower drawdown). Product decided on 25 Nov 2026, under the stakeholder's permission to run backtests, to test
the core once more with the index fund allowed up to **80% of the book**. This amendment is written **after** the
27% results were seen, and the 80% value was chosen because of them. That is stated here and counted below. It is
not a blind choice.

**Research only. No setting changes.** The live index cap stays 27% at every level, the hard ceiling
`BOOK_HARD_CEILINGS.max_index_pct` stays 27%, and `config/index_core.yaml` (27 / 27 / 27, `enabled: false`) and the
live profiles do not change. A loader still refuses a config value above 27. The 80% cap exists only as a
script argument, `scripts/index_core.py --index-cap 80`. That argument goes through `risk.index_core.builder(index_cap_pct=80)`,
which validates the book against the hard ceilings with only `max_index_pct` raised (`PortfolioBuilder.index_ceiling_pct`,
never above 97%). No live module sets it. This amendment is not a level limit: those stay the stakeholder's
(decision 15 section 6). A pass here would not put 80% anywhere live. That would still need the stakeholder to set
the level value and the hard ceiling, a new pull request with two keys, and the final check below.

**The configuration (a new one, next to the 27% one).**

| Row | 27% configuration (Amendment 3) | This amendment |
|---|---|---|
| 2, 4: the index cap at Hunter (judged) | 27% | **80%** under the 100% exposure cap |
| Balanced, Careful (reported, not judged) | 27%, 27% | min(80, the level's exposure cap less the 3% reserve): **57%** and **27%** |
| Neighbours | each at Hunter, 27% | the same five (`vol12`, `vol18`, `lookback63`, `daily`, `nocap`), each at Hunter, **80%** |

Every other row is unchanged. That covers the instrument, the 15% volatility target on 21 returns, the weekly decision,
the cuts of row 8 and the 10% band. It also covers near-close orders at the closing print, cost model v2 (Amendment 2),
0% cash in the judged run, dividends, the $100,000 account and the point-in-time universe. The window stays the
research window, **2017-05-01 to 2024-12-31**. The hold-out (from 2025-01-01) is not read, and `holdout.enforce`
refuses any later `--end`. The pass marks (B1 to B6 of decision 16 section 5, judged at Hunter) are unchanged and
nothing is loosened.

**Expectation, before the run (scaled from the known 27% run, so not independent of it).** Roughly three times the
27% exposure: CAGR about 8 to 11% a year, **SPY -6 to -3 points**, with a maximum drawdown of about 0.35 to 0.55 times
SPY's. If that holds, B2's defensive branch (CAGR at least SPY -2 and drawdown at most 0.75 times SPY's) fails on
return, and the growth branch fails too. A fail is reported as found. `nocap` is expected nearer SPY's return with
about 0.8 times its drawdown.

**Trials.** Six new configurations count (n_trials 1 each): the judged run at 80% and its five neighbours. Cost
multiples, the stress fill and the two other levels are reports (n_trials 0). The six configurations of 24 Nov 2026
(27%) stay counted. The rule `index_core` has then tried **12 configurations**, and every later reading of either
verdict names both. Each row records `params.index_cap_pct` (80) and the row's `max_index_pct`, and the report is
named `index_core_aggressive_<universe>_cap80_<ts>`.

**What a result allows.** The 27% verdict (fail) stands, and this run does not replace it. If the 80% book passes
B1 to B6, Product may propose a level value to the stakeholder. Nothing is opened, changed or re-run with other
settings because of this run: another cap is a new amendment written before its run. The final check of decision 16
section 7 needs its own opening committed to `config/holdout.yaml` before the hold-out is read.

## Amendment 5 (26 Nov 2026, backend-quant, issue #705, after the runs of 24 and 25 Nov 2026): the design saw the hold-out

Gate v3.1 (decision 18 section 9) asks every pre-registration whether anything that shaped its design read the
hold-out. For this book it did. The frozen 15% volatility target came from the audit's own monthly sums over 2017
to 2026, and that window includes the hold-out (from 2025-01-01). This amendment declares it:

- **design_saw_holdout:** true (the volatility target was set from 2017-2026 sums that include the hold-out)

This changes no setting, neighbour, mark or run. Its only effect is the label of the final check on the hold-out
(decision 16 section 7). If that check is ever opened, it is labelled a consistency check, never an out-of-sample
test. It is written after results were seen, and gate v3.1 prints it as hindsight. That is correct, and it makes
nothing easier to pass.

## Amendment 6 (26 Nov 2026, backend-quant, issue #710, after the runs of 24 and 25 Nov 2026): the original 97% core, once, as a consistency check

**Why, and what is known.** The original design of this file (rows 2 and 4: a 97% index cap at Hunter) was never
run. Amendment 3 replaced it with the stakeholder's 27% before any run, and Amendment 4 added 80% after the 27%
result. The second engine audit lists that gap as ticket 6.10 (M17). Product decided on 26 Nov 2026, under the
stakeholder's full permission of 25 Nov 2026 (#600), to run the 97% configuration once. This amendment is written
**after** the 27% results (CAGR 3.38%, maximum drawdown 5.13%, fail; job `index-core-2026-11-24`) and the 80%
results (CAGR 10.09%, maximum drawdown 15.33%, fail; job `index-core-80-2026-11-25`) were seen. The value 97 is
the one this file froze on 22 Nov 2026 before any run, so it was not chosen from those results. The decision to
run it now was taken after them, though, so the run is **hindsight**, and gate v3.1 prints it that way (decision
18). It is a **consistency check** of the original design. It is not a clean test and must never be called one.

- **design_saw_holdout:** true (as Amendment 5: the 15% volatility target was set from 2017-2026 sums that include the hold-out; this amendment adds that the run was decided after the 27% and 80% results)

**Research only. No setting changes.** The live index cap stays 27% at every level. So do the hard ceiling
`BOOK_HARD_CEILINGS.max_index_pct`, `config/index_core.yaml` (27 / 27 / 27, `enabled: false`) and the live
profiles. The 97% cap exists only as a script argument, `scripts/index_core.py --index-cap 97`. That argument goes
through `risk.index_core.builder(index_cap_pct=97)` and the research ceiling of Amendment 4, which is at most 97%:
100% exposure less the 3% reserve. No live module sets it. Nothing here is a level limit, and those stay the
stakeholder's (decision 15 section 6).

**The configuration.** It is the judged configuration of the 22 Nov 2026 text, with the amendments since then.

| Row | This amendment |
|---|---|
| 2, 4: the index cap at Hunter (judged) | **97%** under the 100% exposure cap (the original row 2) |
| Balanced, Careful (reported, not judged) | min(97, the level's exposure cap less the 3% reserve): **57%** and **27%** |
| Neighbours | the same five (`vol12`, `vol18`, `lookback63`, `daily`, `nocap`), each at Hunter, **97%** |

Every other row is unchanged: the instrument, the 15% volatility target on 21 returns, the weekly decision, the cuts
of row 8, the 10% band, near-close orders at the closing print, cost model v2 (Amendment 2), 0% cash in the judged
run with the Treasury-bill version printed beside it, dividends, the $100,000 account and the point-in-time
universe. The window stays the research window, **2017-05-01 to 2024-12-31** (`--end 2024-12-31`). The hold-out
(from 2025-01-01) is **not read** by this run, and `holdout.enforce` refuses any later `--end`. Opening it needs its
own entry in `config/holdout.yaml`, committed before it is read. Even then it is labelled a consistency check
(Amendment 5). The pass marks (B1 to B6 of decision 16 section 5 and gate v3.1's chance test on B2, judged at
Hunter) are unchanged, and nothing is loosened. B2's goal metric is the owner's to fix (#731). This run is judged
on today's B2, and it does not take that decision.

**Expectation, before the run.** This is scaled from the 80% run, so it is not independent of it. At w = 1 the
core holds 97/80 of the 80% core. Expect a CAGR of about **11.5 to 12.5%**, which is about **SPY -3 to -2 points**
(SPY total return 14.39% a year in this window, `docs/research/results/gate_v3_rescore.md`). Expect a maximum
drawdown of about **0.5 to 0.6 times SPY's** (SPY 33.78%). B2's defensive branch (CAGR at least SPY -2 and
drawdown at most 0.75 times SPY's) is borderline on return, and a fail is more likely than a pass. The chance
test of decision 18 then counts N = 18 (below). B3 failed at 27% and 80% and is expected to fail again. `nocap` is expected nearest SPY's return, with about 0.95 times its
drawdown. A fail is reported as found.

**Trials.** Six new configurations count (n_trials 1 each): the judged run at 97% and its five neighbours. Cost
multiples, the stress fill and the two other levels are reports (n_trials 0). The twelve configurations of
24 and 25 Nov 2026 (27% and 80%) stay counted, so the book idea `index_core` has then tried **18 configurations**.
B2's chance test uses that N. Each row records `params.index_cap_pct` (97) and the row's `max_index_pct`, and the
report is named `index_core_aggressive_<universe>_cap97_<ts>`. Job: `index-core-97-2026-11-26`, posting to #710.

**What a result allows.** The 27% and 80% verdicts (fail) stand, and this run replaces neither. A pass would
still be a hindsight consistency check. It would let Product put the 97% value to the stakeholder as information,
nothing more. A fail is the end of the index-cap question for this file. No further cap is tried under this file.
A changed construction (a T-bill ETF for idle cash, two tranches, another sleeve) is a new pre-registration.
