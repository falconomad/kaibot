# Decision 18: Gate v3.1 (the yardstick counts books, fills and hindsight)

- **Status:** proposed by Backend-quant for Product, 25 Nov 2026. It takes effect when the pull request that adds
  it is merged. Until then gate v3 (decision 16) is in force as written.
- **Who may make this change.** Decision 16 section 13 gives Product "every later change to a mark, a level, the
  window or a family (by decision record)". The stakeholder gave Product that work without asking first, on #600
  (25 Nov 2026, 17:11 CET): "full permission for Product to keep working without asking. Merge, deploy, fix, improve
  and backtest; finish M8 and M10–M12, then start the next audit." This record acts under those two texts. The
  permission names M8 and M10 to M12 and "the next audit", not M17. Product's reading is that the second audit's
  staged fixes are part of the next audit. **Product should confirm that reading when it merges.**
- **What stays the owner's.** The second audit lists "Fix the book test's goal metric (B2) before any further book
  run" among the decisions only the owner can take. **This record does not take it.** B2's branches (SPY − 2 points
  at 0.75 times SPY's drawdown, or SPY + 1 point at 1.1 times) are unchanged, number for number. This record only
  adds a test of chance that a branch must also pass, so B2 can only become harder to meet. If the owner later
  changes the goal metric, that is a separate record, and it applies only to books pre-registered after it.
- **Evidence:** the second engine audit (claude.ai project doc `claude/kaibot-second-engine-audit.md`), Stage 6,
  ticket 6.1. Its research notes, Q2, say: "Books sit outside the correction … B2 is a point estimate", and "The
  book loophole is open. index_core has had 12 configurations in two days." Milestone M17, issue #701.
- **Changes:** how gate v3 scores books, and how a verdict is printed. **Does not change:** any rule, any setting,
  the engine, the risk gate, live paper trading, the live 27% index cap, gate v2, or the sleeve marks. A verdict
  given before this record is not reopened. The re-score (`scripts/gate_v3_rescore.py`) prints every existing
  report under the new marks as information.
- **Files:** this record; `config/gate_v3.yaml` (`version: "3.1"`, `book.b2_chance`, `books.<book>.idea`);
  `src/kaibot/evaluation/gate_v3.py`; `src/kaibot/evaluation/prereg.py` (the commit time of each amendment);
  `scripts/gate_v3_rescore.py`, `scripts/index_core.py`, `scripts/index_core_book.py`;
  `tests/unit/test_gate_v3_1_books.py`. Section 6 (#702): `src/kaibot/evaluation/trials.py`,
  `src/kaibot/replay/config_replay.py`, `scripts/trend_momentum_gbm.py`, `scripts/trend_momentum_band.py`;
  `tests/unit/test_gate_v3_1_trial_sharpes.py`. Section 7 (#703): `src/kaibot/evaluation/results.py`,
  `src/kaibot/replay/spy_noise_band.py`; `tests/unit/test_gate_v3_1_timing_exposure.py`. Section 8 (#704):
  `tests/unit/test_gate_v3_1_holm_inputs.py`.

## 1. What changes (ticket 6.1)

| # | What | Before (gate v3) | After (gate v3.1) | Why |
|---|---|---|---|---|
| 1 | Book configurations | counted in the registry, used by nothing | counted per **book idea**: every book listed under `books:` with the same `idea` shares N; N is printed with every book verdict and in the re-score | A book was in no family and in no correction (decision 16 section 15, item 5), so a book could be amended until one configuration crossed B2 by chance |
| 2 | B2 | a point estimate: either branch, on one path | either branch **and** the chance test of section 2 (`book.b2_chance`, DSR at least 0.90) | The audit: B2 judged one path after twelve configurations |
| 3 | B3 | B1 and B2 at doubled costs | the same, with B2 as in row 2 (same N) | B3 repeats B2 |
| 4 | Hindsight | the pre-registration's prose could say "written after the results"; the verdict did not | every amendment of the pre-registration **committed after the first logged run** of the rule or book idea is listed, and the verdict is printed as "`<verdict>`, hindsight (Amendment n written after the first run)" | The audit: "label any amendment written after results as 'hindsight' in the verdict itself, not only in the prereg text" |

## 2. The chance test of B2

The statistic is the book's daily Sharpe minus SPY's, d = SR(book) − SR(SPY), with idle cash at 0% as judged. The
null hypothesis is "no Sharpe gain over SPY". A book that only holds less SPY has SPY's Sharpe, so this is what a
book with no timing or selection skill shows.

- V is the variance of d over the gate's own paired stationary-bootstrap resamples (2,000, mean block 20, seed
  16; book and SPY resampled with the same rows). It is the sampling variance of this statistic. It plays the part
  that the 1/(T−1) floor plays in S4.
- The hurdle SR0 is the expected best of N unskilled configurations (Bailey and López de Prado,
  `metrics.expected_max_sharpe(N, V)`). N is the configurations of the book's idea (section 3).
- DSR = Φ((d − SR0) / √V). The test holds when DSR ≥ 0.90 (= 1 − the level, as S4). With N = 1 there is no
  hurdle and this is the probabilistic Sharpe of d.
- No registry, or no configuration of the idea logged: N is unknown, so the test is unknown and B2 is unknown.
  Unknown never passes. A run that is judged before it appends its own rows (`scripts/index_core.py`,
  `scripts/index_core_book.py`) adds its own configurations to N (`unlogged_configurations`).

The audit offered two forms: a bootstrap share, or a deflated test with N = book configurations. The deflated
test is used because only it counts the configurations tried. The bootstrap share of resamples that meet B2's
branches is still printed and still not judged.

## 3. Book ideas

`books.<book>.idea` names the idea a book tests. The default is the book's own name. Today:

| Book | Idea | Configurations in the registry (re-score of 25 Nov 2026) |
|---|---|---|
| `index_core` | `index_core` | 12: the 27% and 80% caps, each the judged run and 5 neighbours |
| `index_core_trend_momentum` | `index_core_trend_momentum` | 12 |

A new book that changes a cap, a volatility target or a sleeve of an existing book is the same idea and names that
idea, so its configurations add to the same N. Keeping the core and the core with a sleeve as two ideas is a
choice, and it is the lighter one. Counting both under one idea would give N = 24 for each. Product can merge
them by changing one line, before the next book run.

## 4. Hindsight

`prereg.load` records, for each "## Amendment n" heading of the pre-registration, the time of the first commit
that carried it (`Prereg.amendment_log`). The gate compares that time with the first logged run: the earlier of
the run being judged and the first registry row of the rule, or of any book of the idea. An amendment committed
later is hindsight. So is one whose commit time is not known, because it is not shown to come before the run. The
test is the commit time, not the amendment's words. Amendments say "before any run at this cap" or "no real
result had been seen", and both can be true while results of another kind were already in.

The label is a label. It does not by itself fail a mark. The marks stay as they are, and the hindsight is shown
where the verdict is read.

## 5. Effect on the published re-score

- No book or sleeve verdict can turn from fail into pass: B2 and B3 only gain a condition.
- `index_core` (27% and 80%) and `index_core_trend_momentum` (27% and 80%) already fail B2 on its branches. They
  now show N = 12, the chance test's DSR, and "hindsight" for Amendment 4 of `docs/prereg/index_core.md` (committed
  25 Nov 2026 18:48 CET, after the first run on 24 Nov) and Amendment 3 of
  `docs/prereg/index_core_trend_momentum.md`.
- The published table is regenerated by the runner job of #701 (`ops/runner/queue.yaml`), once every Stage 6
  change is on main.

## 6. Ticket 6.2: S4's trial variance comes from active-return Sharpes (#702)

S4 is the deflated Sharpe of the sleeve's **active** return (decision 16 section 4). Its V, the variance of the
family's trial Sharpes, was read from `metrics.daily_sharpe_ann`, the Sharpe of each trial's **absolute** daily
return (`replay/config_replay.py`, every research script). The units matched (daily), but the series did not.
The audit marked this [confirmed] and noted that the 1/(T−1) floor usually binds anyway.

| What | Before | After |
|---|---|---|
| The trial Sharpe S4 reads | `daily_sharpe_ann` (absolute return) | `daily_active_sharpe_ann`: the annualised Sharpe of the trial's active return on the research window, computed by `gate_v3.active_sharpe_ann` exactly as S4 computes the series it tests (`s4_deflated_sharpe.trial_sharpes: active_return`) |
| Old rows | used in V | still counted in N, **flagged** (`trial_sharpes_absolute_only_flagged` in S4's numbers), never used in V. The registry is append-only, so they are never rewritten |
| Fewer than two active Sharpes in the family | not possible: V from the absolute ones | V unknown, so the 1/(T−1) floor of amendment 1 is the V used, the strict default of the deflated Sharpe |
| Writers | `daily_sharpe_ann` only | `scripts/trend_momentum_gbm.py`, `scripts/trend_momentum_band.py` and the sleeve rows of `scripts/index_core_book.py` also log `daily_active_sharpe_ann`. The config-panel replay (`replay/config_replay.py`) has no SPY-at-exposure benchmark, so it logs none and says `daily_sharpe_series: absolute_return`. Its rows are flagged |

Gate v2 and the M4 gate judge absolute returns, so they keep reading `daily_sharpe_ann`.

**Effect.** Every row in today's registry is absolute-only, so every family's V becomes the 1/(T−1) floor until
active Sharpes are logged. Where the absolute Sharpes varied more than the floor, the hurdle is lower than before.
No verdict can pass because of this. S4 is one of six marks, and every rule in the re-score either predates
decision 16, and so cannot pass (decision 16 section 8), or fails S1: `trend_momentum_gbm`, −0.46% a year
(#474). `trend_momentum_sleeve` takes the evidence-based tier, where S4 is printed and not judged. A rule run
from now on logs active Sharpes, and its V is their spread.

## 7. Ticket 6.3: the timing benchmark sees an intraday rule's exposure (#703)

Decision 16 benchmarks a timing sleeve with "SPY at the sleeve's average exposure". The exposure it read was the
share of equity invested **at each close**. An intraday rule such as `spy_noise_band` is flat at every close, so
its benchmark held no SPY and S1 measured "beats cash" (re-score of 25 Nov 2026: "Exposure 0%"; #454: "mean position
13.16% of equity" intraday).

| What | Before | After |
|---|---|---|
| `sleeve.benchmark.timing` | `average_matched`: SPY at the mean close exposure | `average_matched_time_weighted`: SPY at the mean of each session's exposure, the **larger of its close exposure and its time-weighted intraday exposure** (the share of the session's minutes the rule was invested) |
| Where the intraday exposure comes from | nowhere | `Account.intraday_exposure`. `replay/spy_noise_band.run_account` writes `exposure_intraday` to the daily file. For a daily file written before this change, `results.intraday_exposure` derives it from the trades file beside it (entry and exit minutes; an early close counted as 390 minutes, a slight understatement). Nothing is re-run |
| A timing sleeve with no intraday series | — | unchanged: the close exposure, as before |
| The sleeve's own idle cash in bills | `1 −` the close exposure going into the session | `1 −` the larger of that and the session's intraday exposure: money in SPY during the session earns no bill rate (so an intraday rule is not credited a full day of bills) |
| Selection sleeves | — | unchanged: `path_matched` |

The audit offered two readings: time-weighted intraday exposure, or open-to-close SPY at the mean exposure. The
re-score reads only reports, and those hold SPY close-to-close, not its open. So the first reading is used, with
SPY close-to-close. This charges an intraday rule SPY's overnight drift as well, which makes the benchmark the
stricter of the two. An open-to-close benchmark needs SPY's daily opens in the reports, and is a later change.

**Effect.** `spy_noise_band` and `spy_noise_band_carry` now show their intraday exposure and are compared with that
much SPY. Their active return falls, because SPY rose over the window. No verdict can pass because of this. The
benchmark's exposure is never below the old one, SPY rose over the research window (14.4% a year), and every
intraday SPY rule predates decision 16, so it cannot pass (decision 16 section 8).

## 8. Ticket 6.4: what enters Holm, a control tolerance, and the mislabelled row (#704)

| # | What | Before | After |
|---|---|---|---|
| 1 | P-values in Holm | the best-evidenced run's control p of each rule, from any run with 500 or more runs and a matched control. So `spy_intraday_momentum` "cleared" the level on the 0.5 bp-cost run's p (0.0020), which is cost-optimistic, from a 500-run control, and whose window ran into the hold-out (†) | only a p-value from a run that meets `sleeve.final_requires` (engine with SimBroker, cost model v2, data repairs, SPY total return), with the control runs its family needs (`control_runs_for`: 2,000 for several rules), a matched control (row 2) drawn from the rule's own pool or random times, and no session after 2024-12-31 (`s3_control.holm_inputs: final_requires`). Every other p-value is **shown greyed out** (in italics, with its reasons) in the sleeve table and the families table, and counts as 1 in Holm |
| 2 | The control's match | one unfilled slot in any of 2,000 runs made the control unmatched and S3 unknown (#474: 1 of 946) | **pre-registered tolerance**: the control is matched when, over **all** its runs together, at most **0.5%** of its slots are unfilled (`floor(0.005 × slots)`, so 4 of 946), and its committed exposure stays within its own tolerance (2 points) in every run (`s3_control.unfilled_slots_max_share: 0.005`; the config may only lower it) |
| 3 | The `trend_momentum (?, point_in_time)` row | the reference run of `trend_momentum` inside `trend_momentum_gbm`'s report was read as a base run of `trend_momentum`, given the GBM's control (p 0.1644, "the control did not fill every slot") and the kind "?" | a row labelled `reference|…` has the role `reference`: it is not a run of that rule's own and gets no report-level control. A report's `source` (engine, cost model) is read, so the GBM report is `engine_core` with its cost model, not "?" and "not stated" |

**Hindsight, by this record's own rule (section 4).** Row 2 is written after #474's result was seen. It is
labelled so here. Its effect on that run is to make S3 judged: the GBM control (p 0.164, times 7 rules in the
family) fails Holm, so S3 turns from unknown to fail. It cannot make anything pass. Row 1 only removes p-values
from the correction, so it can only raise the adjusted p-values of the families that keep theirs.

**Effect on the published table.** `spy_intraday_momentum`'s family p is greyed out (cost not v2, 500 runs where
2,000 are needed, hold-out sessions), so the family counts as 1 and no family clears the level. The other rows
that fed Holm until now (`trend_momentum`, `mom_12_1_band`, `mom_12_1_band_nofilter`, `pullback_uptrend`,
`weekly_fall_rebound`, `spy_trend_filter`, `spy_noise_band_carry`) are greyed out with their reasons, except where
a run meets every condition.

## The configuration this record adopts

Every gate report records the sha256 of `config/gate_v3.yaml` as `gate_sha`. A test
(`tests/unit/test_gate_v3_1_books.py`) checks the line below against the file. Any later edit of the file must
update this line in the same pull request, together with the section that explains the edit.

config/gate_v3.yaml sha256: `dcc1b6de4150f9902299210bd79ec1feb5fca47c45e19b57866194d889bcde8a`
