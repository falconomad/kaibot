# A tier-one screen so only survivors get an engine run (design; M11 ticket 2.11, issue #462)

- **Status:** design, for Product. **Revised 22 Nov 2026 (#568, QA on #557):** the level inside a batch is now the
  one the budget sets (section 4), and screen candidates do not count in S4 (section 6; Product's ruling, 22 Nov 2026). It changes no code, no gate mark, no config and nothing live. The code and the
  config come in a later pull request, written against this document. Any change to the marks below after that
  pull request needs a decision record (decision 16 section 13).
- **Written by:** Quant research, 22 Nov 2026.
- **Why:** with about a dozen rule families tested and no correction, a chance "pass" was to be expected
  (`docs/research/engine_audit/report.md`, Stage 2, row 2.11). Decision 16 fixed the correction for verdicts. It left
  open the cost of testing in sequence: every verdict already given stands when the count grows (decision 16
  section 6, "ticket 2.11 adds a budget for it"). This screen is that budget. It also keeps the expensive part, a
  full engine run with a 2,000-run control, for ideas that showed something first.
- **Not a second simulator** (CLAUDE.md, decision 15): the screen never produces a go/no-go number. It only decides
  which candidates are worth a pre-registration and an engine run. Every verdict still comes from the engine with
  `SimBroker` and gate v3.

## 1. What is screened

A **candidate** is one signal: a function of point-in-time daily data giving every stock in the universe a score
on each decision day (for example "the 90-session regression slope times R-squared", "the return from t-252 to
t-21", "a 10-day fall of more than 2 ATR"). It has no sizing, no exits beyond its holding period, no market filter
and no costs beyond the screen's fixed charge below. Each candidate belongs to one **family** of
`config/gate_v3.yaml` (a new family is added there in the same pull request, as decision 16 section 6 requires) and
arrives in a **batch**: the candidates screened together in one run, declared in one file before the run.

## 2. The panel

- The point-in-time S&P 500 daily store (`--universe pit`, the panel of `replay/multiday_core.Market.from_panel`),
  the same price and volume filter as the rules ($5, $50M 20-day dollar volume, point-in-time membership).
- The **research window only** (2017-05-01 to 2024-12-31). The screen calls `holdout.enforce` like every research
  script and never reads 2025 on. The hold-out is not for screening.
- Features read rows at or before the decision day only (CLAUDE.md, point in time).

## 3. The statistic

For each candidate and each monthly decision day (the first session of the month), the **rank information
coefficient**: the Spearman correlation across the stocks between the score and the next 21 sessions' return,
minus SPY's, after a fixed charge of 10 bp a side on the turnover the top quintile implies. The candidate's
statistic is the mean of those monthly coefficients. Its t is computed with Newey-West errors (lag
floor(4 (T/100)^(2/9)), as `mtm.nw_lag`), and its p-value is one-sided (the coefficient is above zero).

Why this statistic: it uses every stock every month, so it has more power than a top-20 portfolio. It needs no
sizing, so the screen cannot tune any. And it is cheap: one panel pass for a whole batch. A candidate that ranks
stocks the wrong way gets a negative coefficient and does not survive.

## 4. The threshold, adjusted for the batch

One level for the whole programme of screening: **a marginal false discovery rate of 0.10** (the expected number of
false survivors over the expected number of survivors, across every batch ever screened). It is reached by
alpha-investing over batches (Foster and Stine, 2008; the batch form of Zrnic, Jiang, Ramdas and Jordan, 2020; both
cited from memory). Inside each batch Benjamini-Hochberg runs at the batch's own level `q_b`, which the budget sets.
**There is no fixed in-batch level of 0.10.**

| Setting | Value | Why |
|---|---|---|
| Programme-wide level | mFDR 0.10 | the one-in-ten of decision 16, applied to screening |
| Initial wealth W_0 | **0.09** = 0.10 x (1 - 0.10) | the starting wealth Foster and Stine's bound allows at this level (W_0 at most alpha x (1 - alpha)) |
| Level of batch b | `q_b` = half of the wealth left before it, never above 0.10 | so the budget can never be spent in one batch |
| Cost of a batch | `q_b / (1 - q_b)` is deducted from the wealth when the batch runs, whatever it finds | Foster and Stine's cost of a test at level `q_b` |
| Payout | **0.10** to the wealth for each survivor of batch b that the engine later **confirms** (a gate v3 verdict of `pass` or `evidence_based`), credited when that verdict is merged | only confirmed discoveries refill the budget |
| Out of budget | below 0.005 of wealth no batch may run | Product resets the budget only by a decision record |

In numbers: the first batch runs Benjamini-Hochberg at **q_1 = 0.045** and costs 0.047. The second, without a
confirmed survivor in between, runs at 0.0214, and every later batch at about half the one before. A programme that screens many ideas and confirms none therefore
runs out of budget. That is the bound for testing in sequence that decision 16 section 6 asked for. The ledger is one
file committed in the repository, `docs/research/tier_one_screen/budget.md`, append-only, one line per batch (date,
batch id, wealth before, q_b, survivors, wealth after) and one line per payout. A batch whose line is not committed
before its run does not count as screened.

## 5. What a survivor gets, and what a non-survivor gets

- A **survivor** becomes a candidate rule. It needs a pre-registration in `docs/prereg/` before its engine run, as
  every rule does. The pre-registration names its batch and its screen statistic, and its settings are the ones
  screened, unchanged. Its engine run and its gate v3 verdict are the only results that count.
- A **non-survivor** is recorded and is not run through the engine. It may come back only as a new candidate in a
  later batch, with its own budget cost, and never with changed settings in the same batch.

## 6. Every candidate is a trial, and the screen changes no mark

Each candidate is logged in the trial registry (`data/trials.jsonl`, `evaluation/trials.append`) under its own
candidate name, with n_trials 1, `variant: screen`, its family ID, its batch ID, the screen's statistic and
p-value, the batch's `q_b` and the code and config hashes. The registry's whole count therefore includes it.

**It does not enter S4. Product's ruling, 22 Nov 2026 (on #569):** screen candidates do not count in S4. S4 counts the
configurations of a pre-registered rule, the configurations logged for the rules its family lists in
`config/gate_v3.yaml` (decision 16 section 4), and a screen candidate is not such a rule. The screen's own
multiplicity is handled by its own budget: Benjamini-Hochberg inside each batch at the level the alpha-investing
budget sets (section 4). Candidates are still logged as trials (above), so the registry's whole count includes them.
A survivor that becomes a rule is listed in its family with its pre-registration, and from then on its engine runs
count in S4 like any rule's. No mark changes, so no decision record is needed.

## 7. What the code will be (the next pull request; not written yet)

| File | What |
|---|---|
| `config/screen.yaml` | the statistic, the horizon (21 sessions), the charge (10 bp), the mFDR level (0.10), the alpha-investing settings (W_0 0.09, half spent per batch, payout 0.10, floor 0.005) |
| `src/kaibot/evaluation/screen.py` | pure: the rank IC per decision day, its Newey-West t and p, Benjamini-Hochberg at q_b, the alpha-investing ledger |
| `scripts/screen_batch.py` | reads a batch file (`docs/research/tier_one_screen/batches/<id>.yaml`, committed before the run), the store, `holdout.enforce`, writes the batch report and one trial row per candidate |
| `tests/unit/test_screen.py` | the rank IC by hand; Benjamini-Hochberg by hand at q_b; the batch levels 0.045, 0.0214, ... with no payout; on synthetic batches with no signal, false survivors over survivors at most about 0.10 across many batches; a planted signal survives; the ledger runs out with no confirmed survivor; a batch file changed after its run is refused; a candidate's trial row does not change S4's count |

## 8. Mac command (once the code exists)

```
PYTHONPATH=src python scripts/screen_batch.py --batch docs/research/tier_one_screen/batches/<id>.yaml --data data
```

## 9. Decisions for Product

1. The programme-wide mFDR level (0.10) and the alpha-investing settings (W_0 0.09, half spent per batch, payout
   0.10 per confirmed survivor, floor 0.005).
2. Whether the screen is required before every new rule's engine run, or only for batches of three or more
   candidates. The proposal: required for every new **signal**, and not for a re-scaled or re-packaged rule that was
   already judged (such as `trend_momentum_sleeve`, #550).
3. Who may reset the budget (proposal: Product, by decision record).

Settled: screen candidates do not count in S4 (Product, 22 Nov 2026; section 6).
