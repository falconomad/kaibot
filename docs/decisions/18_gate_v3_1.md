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
  `tests/unit/test_gate_v3_1_books.py`.

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

## 6. The configuration this record adopts

Every gate report records the sha256 of `config/gate_v3.yaml` as `gate_sha`. A test
(`tests/unit/test_gate_v3_1_books.py`) checks the line below against the file. Any later edit of the file must
update this line in the same pull request, together with the section that explains the edit.

config/gate_v3.yaml sha256: `59b004e149012fb6736637952269eee3705d7231f916d7a2121abaf2883ddcf4`
