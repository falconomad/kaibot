# Decision 16: Evidence gate v3 (sleeve test, book test, one correction for every idea tried)

- **Status:** adopted by Product on 21 Nov 2026 (decision D2 of `docs/research/engine_audit/decisions.md`:
  "Gate v3 will be introduced, by decision record"). This file is that record. It takes effect when the pull
  request that adds it is merged; until then gate v2 (decision 15, section 7) is the gate in force.
- **Written by:** Quant research, for Product. Product confirms the marks by merging. The marks are proposals
  until then and are listed in one table in section 11 so they can be changed in review.
- **Stakeholder's confirmations, 21 Nov 2026** (recorded in `docs/research/engine_audit/decisions.md`, section
  "Stakeholder decisions, 21 Nov 2026", pull request #486):
  - the goal is an SPY-like return with smaller drawdowns, not a higher raw return (also decision 15, section 1);
  - index funds get their own cap (tickets 2.7 and 2.8);
  - no gold or other non-stock diversifiers for now: the product stays US stocks and index funds;
  - the permanent book stop is to be replaced (ticket 2.10, by its own record).
- **Milestone:** M11 Yardstick and portfolio. Ticket 2.5, issue #456.
- **Evidence behind it:** `docs/research/engine_audit/report.md` ("The algorithm and its test"; Stage 2).
- **Changes:** how a multi-day rule is judged before it may trade on paper. **Does not change:** any rule, any
  setting, the engine, the risk gate, live paper trading, gate v2's file or any verdict already given.
- **Files:** `config/gate_v3.yaml` (the marks; every report records its sha256),
  `src/kaibot/evaluation/gate_v3.py`, `scripts/gate_v3_rescore.py`, `tests/unit/test_gate_v3.py`.

## 1. Why gate v2 is replaced

Gate v2 had two faults that point in opposite directions.

| Fault | What happened | Effect |
|---|---|---|
| It measured the wrong thing | Each rule ran alone in a cash account and had to come within two points of a **fully invested** SPY | A rule that is 36% invested needs about 34% a year on its invested money. It fails by arithmetic, whatever its skill. Marks 4 and 5 re-test mark 2, so one miss shows as three failures |
| It did not count how many ideas were tried | Each rule alone had to beat 90% of random portfolios | With twelve ideas tried at a one-in-ten level, the chance that at least one passes with no skill is 1 - 0.9^12 = 72% |

Two smaller faults: idle cash earned 0%, and the deflated Sharpe counted the 8,286 near-identical
configurations of the retired intraday rule against every new rule, so it could not be passed and was not judged.

Gate v3 changes the unit that is judged. It does not make passing easier: section 9 shows it is harder to pass
by luck and still hard to pass with a small real edge.

## 2. The three parts

| Part | Question | Judged on |
|---|---|---|
| **Sleeve test** | Did this rule earn more than the same amount of SPY would have? | The rule's account against SPY held at the rule's own exposure, the rest in Treasury bills |
| **Book test** | Does the whole account that would trade meet the goal? | The combined account (index core, overlays, sleeves) against SPY, with gate v2's absolute marks |
| **Correction across families** | Could this be luck, given everything that was tried? | One test of chance for the whole programme, not one per rule |

A rule may start on paper only inside a book that has passed the book test, and a book passes only if every
return sleeve in it has passed the sleeve test. An index core and a risk overlay (a volatility cap, a trend
filter on the core) are part of the book's construction. They are judged by the book test, not as sleeves: their
purpose is a smaller drawdown, not a return above SPY.

Gates A, C and D keep their meaning from decision 15. Gate B is now "sleeve test, then book test, then the final
check on the hold-out".

## 3. What is measured, and on which sessions

- **Window.** Every mark is judged on the research window, **2017-05-01 to 2024-12-31**. Sessions from
  2025-01-01 on are the hold-out (ticket 2.6, `config/holdout.yaml`). They are dropped before any number is
  computed and are used once, for the final check of section 7.
- **Series.** Daily mark-to-market returns after costs, from the engine with `SimBroker`, cost model v2, on the
  point-in-time universe, against SPY total return. Unchanged from gate v2.
- **Cash.** In the sleeve test idle cash earns the 3-month Treasury-bill rate on both sides (ticket 2.4). For a
  stock-picking sleeve the bills cancel exactly, because the benchmark holds the same cash. In the book test idle
  cash earns 0%, which is what the paper account shows; the bill version is printed beside it.
- **Active return.** The sleeve's daily return minus its benchmark's.

| Kind of sleeve | Benchmark | Why |
|---|---|---|
| `selection` (picks stocks) | SPY at the sleeve's own exposure, session by session (`path_matched`) | The sleeve's timetable of being invested is copied, so only the choice of names is judged |
| `timing` (trades SPY only) | SPY at the sleeve's **average** exposure, held all the time (`average_matched`) | For an SPY-only rule the timetable is the skill, so it must not be copied |

The kind is written in the pre-registration before the first run.

## 4. The sleeve test: six marks, all must hold

| Mark | Pass mark | Printed beside it, not judged |
|---|---|---|
| **S1** Return above the same amount of SPY | Annualised mean active return above zero | Its Newey-West t; a 90% bootstrap interval; tracking error and information ratio |
| **S2** Costs | S1 still holds when every cost is doubled | The active return at 1x, 2x and 3x; the break-even multiple |
| **S3** Skill against chance | The control's p-value clears Holm's correction across all rule families at a family-wise level of 0.10 | The Benjamini-Hochberg-adjusted p-value; the drawdown percentile; alpha and its t |
| **S4** Deflated Sharpe | The deflated Sharpe of the active return, counted against the configurations of the rule's own family, is at least 0.90 | The family's configuration count beside the whole registry's |
| **S5** Universe | S1 to S4 hold on the point-in-time universe | |
| **S6** Neighbours | Every pre-declared neighbour has an active return above zero | Each neighbour's figure |

A run passes only when its source says engine `simbroker`, cost model `v2`, data repairs in, SPY total return
(as gate v2), and when Gate A holds.

### S3 in detail

- **The control.** At least 500 runs. Each random portfolio keeps the rule's positions slot by slot (entry day,
  holding period, size) and replaces only the name. For a timing sleeve the control is random decision times with
  the rule's holding periods (decision 15, section 10).
- **Drawn from the rule's own candidate pool.** The pool is the names the rule could have bought on that day
  before its own signal is applied. For a rule whose signal is the price alone (momentum, a pullback, a new high)
  that is every index member that passes the price and volume filter, as before. For a rule that acts on an event
  it is the names with that event: for a headline rule the companies with a headline that day, for an earnings
  rule the companies that reported. A control that drew from the whole universe for an event rule answers a
  different question ("are companies with news different?") and S3 is then unknown, never passed.
- **The p-value** is (1 + random Sharpes at or above the rule's) / (runs + 1). With 500 runs the smallest
  possible value is 1/501 = 0.002.
  That floor matters (QA-Ops, 21 Nov 2026): a family of four rules needs 4 x p at or below 0.10 / 12, which 500
  runs reach only when the rule beats every random portfolio (4 / 501 = 0.00798), and not at all once there are
  thirteen families (0.10 / 13 = 0.00769) or five rules in the family. "500 runs or more" is a minimum: a rule
  in a family of several rules should be run with at least 2,000, or S3 cannot be passed for a reason that has
  nothing to do with the rule.
- **Inside a family** the family's p-value is its smallest member p-value times the number of rules the family
  lists (Bonferroni).
- **Across families** Holm's step-down is applied to the family p-values. A family with no usable control counts
  as 1: it is in the count and can never pass. With no other family passing, a rule in a one-rule family needs
  p at or below 0.10 / 12 = 0.0083.
- **The drawdown percentile** is the share of random portfolios with a deeper maximum drawdown than the rule's.

**Why Holm and not Benjamini-Hochberg.** Holm bounds the chance that **any** family passes with no skill, and
it holds whatever the dependence between the tests. Benjamini-Hochberg bounds the expected share of false
passes among the passes, needs independent or positively dependent tests, and pays off when hundreds of
candidates are screened. The programme has twelve families and one account, so one false pass is the event to
avoid. Benjamini-Hochberg is printed beside Holm, and it is the natural choice for the tier-one screen of
ticket 2.11, where candidates come in batches.

**Why 0.10.** Gate v2 asked each rule to reach the 90th percentile, a chance of one in ten of passing with no
skill. Gate v3 keeps one in ten and applies it to the whole programme. S4 uses the same level (0.90 = 1 - 0.10).

### S4 in detail

The deflated Sharpe ratio (Bailey and López de Prado) asks whether a Sharpe is above the best that N unskilled
configurations would be expected to show. Gate v3 computes it on the active return and sets N to the
configurations logged for the rules of the same family. The spread of the family's logged trial Sharpes is used
when the registry has at least two; otherwise the strict default of gate v2. The whole registry's count is
printed beside it. Every run is still logged: nothing is removed from the registry.

## 5. The book test: six marks, all must hold

The book is run through the engine as one account (shared cash, one cap per name), not as a weighted sum of
sleeve results. Its construction and its sleeve weights are fixed in a pre-registration.

| Mark | Pass mark | From |
|---|---|---|
| **B1** Return | Net return positive overall and in at least 60% of the counted calendar years, rounded up: **5 of 8** on the research window (2017 counts with eight months) | Gate v2 mark 1 (6 of 10) |
| **B2** Against SPY | **Either** CAGR at least SPY's minus 2 points with a maximum drawdown of at most 0.75 times SPY's, **or** CAGR at least SPY's plus 1 point with at most 1.1 times | Gate v2 mark 2, unchanged |
| **B3** Costs | B1 and B2 still hold at doubled costs | Gate v2 mark 4 |
| **B4** Universe | B1 to B3 hold on the point-in-time universe | Gate v2 mark 5 |
| **B5** Neighbours | Every pre-declared neighbour of the book passes B1 | Gate v2 mark 6 |
| **B6** Sleeves | Every return sleeve inside the book has passed the sleeve test | New |

The first branch of B2 is the stakeholder's goal in numbers: SPY-like return, smaller drawdowns.

Printed beside B2 and not judged: 90% bootstrap intervals for the CAGR difference and for the drawdown ratio
(the book and SPY resampled together, session for session), the share of resampled decades in which B2 would
hold, and the share in which the book's drawdown is shallower than SPY's. One decade holds only a few deep falls,
so a drawdown ratio measured on it is uncertain; the interval says how uncertain.

Gate v2's mark 3 (the random control) is not asked of the book. Each return sleeve answers it in S3.

## 6. Rule families

One family per idea. Every pre-registration to date is in exactly one, which gives twelve families.

| Family | Rules | Kind |
|---|---|---|
| `intraday_dip_reversal` | dip_reversal (retired), winners_run | selection |
| `cross_sectional_momentum` | trend_momentum, mom_12_1_band, mom_12_1_band_nofilter, rel_strength | selection |
| `new_highs_breakout` | trend_highs, ath_breakout | selection |
| `spy_intraday_momentum` | spy_noise_band, spy_noise_band_carry, spy_late_momentum | timing |
| `overnight_premium` | overnight_spy | timing |
| `index_trend_timing` | spy_trend_filter | timing |
| `opening_range_breakout` | orb_stocks_in_play | selection |
| `news_drift` | news_continuation, news_gap_week | selection |
| `earnings_drift` | post_earnings_week | selection |
| `short_term_reversal` | pullback_uptrend, weekly_fall_rebound | selection |
| `filing_flags` | filing_flags_study (an event study) | selection |
| `market_event_alarm` | market_alarm_study (an event study) | timing |

| Families counted in the correction |
|---|
| 12 |

- The two event studies and the retired intraday rule are counted. They were tests of an idea, and leaving
  them out would make the correction lighter than the work done.
- A new pre-registration names an existing family or adds one to `config/gate_v3.yaml` in the same pull request,
  before its first run. The count then grows for every later verdict. A verdict already given is not reopened
  when the count grows; this is a known weakness of testing in sequence, and ticket 2.11 adds a budget for it.
- Moving a rule between families, or merging families, needs a decision record.

## 7. The final check on the hold-out

After every research-window mark holds, the hold-out is opened once for that rule or book (an opening in
`config/holdout.yaml`, purpose `final_check`; the read is logged).

- **Sleeve.** The hold-out's annualised mean active return must be at or above the **5th percentile** of
  stationary-bootstrap stretches of the same length drawn from the research window.
- **Book.** The same for CAGR minus SPY's, and the hold-out's maximum drawdown must be at or below the 95th
  percentile of the resampled drawdowns.

The check asks whether the hold-out looks like the research window. It does not ask for significance: 23 months
cannot show an edge. A failed final check is a fail. The rule is not re-run with changed settings; a changed rule
is a new pre-registration.

**The hold-out is not untouched data for the rules that exist today.** It was frozen on 21 Nov 2026, after every
existing rule had been run on data to November 2026. For a rule first run before that date the report labels
the result a consistency check, never an out-of-sample test.

## 8. Gate A additions

The pre-registration, or an amendment appended before the run, declares for gate v3:

1. the role: sleeve or book;
2. the kind: selection or timing;
3. the family;
4. the candidate pool the control draws from;
5. for a book: its components and the fixed weight or budget of each sleeve.

None of the 17 existing pre-registrations declares these. A re-score of their existing reports can therefore
show `fail`, `incomplete` or `provisional`, and never `pass`.

## 9. What this gate can and cannot show

Stated before any rule is judged under it.

**It holds the chance of a lucky pass at one in ten for the whole programme.** A test checks this on twelve
families of no skill: judged one by one at 0.10 as in gate v2, at least one passes 72% of the time; under Holm,
10%.

**It cannot confirm a small real edge on this much data.** The research window has 7.7 years. The table gives
the chance that a sleeve with a true information ratio (active return divided by tracking error) passes S3 and
S4. It is arithmetic, not a simulation of the engine, and it assumes normal active returns and that no other
family passes.

| True information ratio | One rule in its family, 6 configurations | Four rules in its family, 20 configurations |
|---|---|---|
| 0 (no skill) | 0.5% | 0.1% |
| 0.25 | 3% | 1% |
| 0.5 | 12% | 4% |
| 0.75 | 31% | 13% |
| 1.0 | 57% | 34% |
| 1.5 | 94% | 83% |

The audit's range for a long-only stock-picking sleeve is an information ratio of about 0.25 to 0.4. Such a
sleeve will usually **not** pass. That is not a defect that tuning the marks can remove: ten years of daily
data do not hold enough information to tell a small edge from none, and a gate that passed such sleeves would
pass about as many with no edge. What follows from it:

- A sleeve that does not pass is **not shown to be worthless**. It is not shown to be worth its tracking error.
- The goal can be met without any sleeve passing: the book test judges an index core with a risk overlay on its
  own, and the first branch of B2 is the goal.
- Rules should be chosen on decades of published evidence and low turnover, then tested once, not chosen by
  which had the best decade.

**The open question, answered.** Whether a sleeve with strong published evidence that meets S1, S2, S5 and S6
but not S3 and S4 may be admitted to a book at a small fixed budget was left open for Product when this record
was written. Product answered it on 21 Nov 2026: yes, with limits. The answer is section 14.

**Checked by simulation (QA-Ops, 21 Nov 2026).** 20,000 simulated sleeves per row, 1,930 sessions of normal active
returns, the gate's own `holm` and `deflated_sharpe` code, a 500-run control, eleven other families not passing,
S4 on the strict default spread. Share passing S3 and S4 together, one rule and 6 configurations / four rules
and 20 configurations: information ratio 0: 0.5% / 0.1%; 0.25: 2.9% / 0.6%; 0.5: 10.9% / 3.2%; 0.75: 30.1% /
11.8%; 1.0: 56.1% / 31.4%; 1.5: 93.6% / 80.2%. The table above is S4's pass rate alone (at 0.5: 11.4% and 3.7%
in the simulation); passing both marks is one to three points rarer. Twelve no-skill families judged one by
one at 0.10: at least one passes 71% of the time; under Holm 9%. The table's conclusion stands.

## 10. What happens to gate v2 and to the verdicts already given

- `config/gate_v2.yaml` and `gate_v2.py` stay. Gate C (paper incubation) and Gate D are read from them as before.
  The M4 gate still judges intraday rules.
- **Every gate v2 verdict stands as history.** No rule that failed gate v2 passes by this decision.
- **`trend_momentum`:** decision 15, section 11 is unchanged. It trades on paper as a labelled experiment that
  failed its ten-year test, and real money stays barred for it whatever a re-score shows.
- **Re-score.** `scripts/gate_v3_rescore.py` scores every existing report under gate v3 without re-running any
  rule and writes `docs/research/results/gate_v3_rescore.md`. It is information for choosing what to test next.
  Its numbers from the daily files are cut to the research window. Its control p-values are the earlier
  reports' own, over their whole windows: for a run that went past 2024-12-31 they have seen hold-out sessions,
  and the table marks them. A verdict takes its control from a run that ends inside the research window.
- **A new verdict needs a new run** on the research window, with a pre-registration that declares the fields of
  section 8, logged as a trial.

## 11. The marks in one table

| Setting | Value | In `config/gate_v3.yaml` |
|---|---|---|
| Research window | 2017-05-01 to 2024-12-31 | `window` |
| Level for every test of chance | 0.10 (one in ten), family-wise | `level` |
| S1 active return | above 0 a year | `sleeve.s1_active_return` |
| S2 cost multiple | 2 | `sleeve.s2_costs` |
| S3 control | 500 runs or more, from the candidate pool; Bonferroni inside a family, Holm across families | `sleeve.s3_control` |
| S4 deflated Sharpe | at least 0.90, family configurations | `sleeve.s4_deflated_sharpe` |
| B1 | positive overall; 60% of counted years (5 of 8); at least 8 counted years | `book.b1_return` |
| B2 | SPY - 2 points and 0.75 x drawdown, or SPY + 1 point and 1.1 x drawdown | `book.b2_spy` |
| B3 cost multiple | 2 | `book.b3_costs` |
| Hold-out band | 5th percentile (95th for drawdown) | `holdout_check` |
| Bootstrap | stationary, mean block 20 sessions, 2,000 resamples, seed 16, 90% intervals | `bootstrap` |
| Families | 12 | `families` |

## 12. How the stakeholder's other confirmations bear on this record

- **Index funds get their own cap.** The book test assumes a book may hold an index core. The size of the cap
  is a level limit for multi-day books and stays the stakeholder's to confirm with ticket 2.7. Nothing here sets
  it.
- **No gold or other non-stock diversifiers for now.** The benchmark stays SPY total return and no multi-asset
  benchmark is defined.
- **The permanent book stop is to be replaced.** That is ticket 2.10: a risk rule, with its own decision record,
  tests first and two keys. Until it is merged the stop of decision 15, section 11 is in force. Gate C's
  bootstrap band, which the audit proposes as the replacement, is unchanged by this record.

## 13. Who decides what

- **Product:** merging this record (which confirms the marks), every later change to a mark, a level, the
  window or a family (by decision record), every opening of the hold-out, and the paper start of a book that has
  passed, with both keys.
- **Only the stakeholder:** real money, the level limits for multi-day books (the index cap included), and
  anything decision 15, section 9 already reserves.
- **Unchanged:** paper only; a pre-registration before the first run; every run logged as a trial; go/no-go
  numbers only from the engine with `SimBroker`.

## 14. Evidence-based sleeves: the open question of section 9, answered

**Product, 21 Nov 2026, delegated by the stakeholder.** The question was whether a sleeve whose signal has strong
published evidence may be admitted to a book without passing the two chance marks, S3 (the control) and S4 (the
deflated Sharpe). The answer is **yes, with limits**:

- **Only with strong published, replicated evidence** for the signal.
- **Labelled.** Such a sleeve is an "evidence-based, not proven here" sleeve, in those words, in its
  pre-registration, in every report and wherever the book shows it. It has not passed gate v3 and no report may
  say that it has.
- **At most 10% of the book in total**, across all such sleeves together, not each.
- **Pre-registered** before its first run, as every rule is (section 8).
- **Every other mark holds:** S1, S2, S5 and S6. The book it joins is judged by the book test as any book is.
- **Reviewed against the hold-out once:** the final check of section 7, under one opening of the hold-out.
- **Everything else needs the full gate.** A sleeve without that evidence, or one that wants more than its share
  of the 10%, passes S1 to S6 or does not trade.

What this does not change: the families and the correction (such a sleeve's runs are logged as trials and its
family stays in the count), paper only, and real money, which stays the stakeholder's decision.

In practice the pre-registration is where the evidence is cited and the label first appears.

Not built yet: `gate_v3.py` has no such tier. `evaluate_sleeve` reports S3 and S4 as they are and returns `fail`
or `unknown` for such a sleeve; the label, the 10% budget and the check that the other marks hold are applied by
Product by record until a ticket adds them to the code and to `config/gate_v3.yaml`.
Superseded by amendment 1 (section 15, item 4), which builds the tier into the code and the config.

## 15. Amendment 1 (22 Nov 2026): QA's follow-ups on #499, the evidence-based tier, books

- **Written by:** Quant research, ticket 2.12 (issue #514). **Status:** proposed. It takes effect when the pull
  request that adds it is merged (Product confirms by merging, as for section 11). Until then the marks above are
  in force as written. **Changes:** how gate v3 scores; nothing trades, and no verdict given before it is reopened.
- **Files:** `config/gate_v3.yaml` (its sha256 changes, so every report written after the merge carries the new
  hash), `src/kaibot/evaluation/gate_v3.py`, `tests/unit/test_gate_v3_amendment1.py`, `tests/conftest.py`.

| # | What changes | Before | After | Why |
|---|---|---|---|---|
| 1 | S4: the variance of the trial Sharpes | the variance of the family's logged Sharpes when there are at least two | never below **1/(T-1)**, the sampling variance of a no-skill Sharpe over the T sessions judged (`s4_deflated_sharpe.variance_floor`) | QA (#499): with near-identical configurations the spread is almost zero, the hurdle almost vanishes, and a no-skill sleeve passed S4 7.5% of the time instead of about 0.5% |
| 2 | S3: runs of the control | at least 500 | at least 500 for a family of one rule; at least **2,000** for a family of two or more rules (`random_runs_min_several_rules`) | Section 4 already said a multi-rule family "should be run with at least 2,000"; with 500 runs the floor of p (1/501) times the family size can hardly clear Holm. Now it is a mark, not advice |
| 3 | The selection benchmark's first session | SPY at 0% on the first session of the window (the exposure series was lagged after the cut) | SPY at the exposure of the **close before the window**, when the account was already invested then; 0% only for an account that starts on that session. The bill side uses the same lag | A sleeve invested going into 2017-05-01 was compared with an empty benchmark for one session |
| 4 | The evidence-based tier of section 14 | applied by Product by record | in the code: a rule's entry under `families` says `tier: evidence_based` and `evidence: <citation>`, written with its pre-registration before its first run. S1, S2, S5 (on S1 and S2) and S6 are judged; S3 and S4 are computed and printed, not judged. The verdict is `evidence_based`, labelled **"evidence-based, not proven here"**, never `pass` unless all six marks hold. In a book, B6 admits such sleeves only with a budget in the book's entry, and all of them together at most **10%** of the book. The pre-registration itself must cite the evidence (its published-evidence section) and carry the label, or the verdict is provisional; B6 admits a sleeve only when its entry takes the tier, reads a listed book's own budgets and leaves B6 unknown while a listed sleeve has no verdict (QA on #542). The final check on the hold-out stays required | Section 14 asked for exactly this; until now `evaluate_sleeve` returned `fail` or `incomplete` for such a sleeve |
| 5 | Where a book is listed | nowhere: `evaluate_book` looked for the book in a family, so every book was `provisional` | a new section **`books:`** of `config/gate_v3.yaml`: the book's components, each return sleeve's budget (% of the book) and `v3_declared`. A book is in no family and does not change the count of the correction. Its pre-registration adds it in the same pull request, before its first run (section 8, item 5) | A book is the account's construction, not an idea tested for skill; putting it in a family would have made the correction stricter for every rule without reason |
| 6 | Tests | `load_env_file` in one test left `ALPACA_BASE_URL` (and the other keys of a test's `.env`) in the process for every later test | `tests/conftest.py` removes, after each test, every environment variable the test added | Main's tests leaked a broker URL from one test into another (QA on #499) |

What does not change: the level (0.10), the families and their count (twelve), Holm across families and
Bonferroni inside one, S1, S2, S5, S6, the book marks B1 to B5, the window, the hold-out and its check, the
bootstrap, gate v2.

Effect on the re-score of section 10: the rows whose control ran 500 times in a family of several rules
(`trend_momentum`, `mom_12_1_band`, `mom_12_1_band_nofilter`, `ath_breakout`, `pullback_uptrend`,
`weekly_fall_rebound`) now show S3 as unknown ("500 runs, 2000 needed") instead of failed. None of them could pass
before (section 8), and none can now. A new verdict for one of these rules needs a run with 2,000 or more control
runs.

The simulation of section 9 was run with the strict default spread (1/(T-1)); its table is unchanged by item 1.

## 16. Amendment note (22 Nov 2026): the family table of section 6 as pre-registrations add rules

- **Written by:** Quant research, at Product's request. **Status:** a note, not a change of any mark. Section 6 is
  left as written on 21 Nov 2026. It records the twelve families and their rules **as of that date**.
- **What is authoritative:** `config/gate_v3.yaml` `families`. A new pre-registration adds its rule there in the same
  pull request, before its first run (section 6), and the gate scores from the config, not from this text. The
  number of families, twelve, is unchanged by the rules below. Holm still counts twelve.
- **`cross_sectional_momentum`, five rules** once #550 (ticket 2.8) is merged:

| Family | Rules | Kind | Added by |
|---|---|---|---|
| `cross_sectional_momentum` | trend_momentum, mom_12_1_band, mom_12_1_band_nofilter, rel_strength, trend_momentum_sleeve | selection | the first four: section 6. `trend_momentum_sleeve`: docs/prereg/trend_momentum_sleeve.md (#459, PR #550), trend_momentum frozen and declared for gate v3 under the evidence-based tier |

- **Effect:** inside the family the Bonferroni factor is the number of rules it lists, so it becomes 5 for every later
  verdict in it (section 4, "Inside a family"). Verdicts already given are not reopened (section 6).
- **Pending:** `trend_momentum_band` (docs/prereg/trend_momentum_band.md, #460, PR #556) joins the same family
  when it is merged. The family then lists six rules, and the config says so. This note will not be edited for it:
  the next addition is recorded by the next note, in the same way.
