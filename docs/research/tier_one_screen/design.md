# A tier-one screen so only survivors get an engine run (design; M11 ticket 2.11, issue #462)

- **Status:** design, for Product. It changes no code, no gate mark, no config and nothing live. The code and the
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

- Inside a batch: **Benjamini-Hochberg at q = 0.10** over the batch's p-values. This is the procedure decision 16
  section 4 names for the screen: it bounds the expected share of false survivors, and it pays off when many
  candidates are screened.
- Across batches, the budget for testing in sequence: **alpha-investing** (Foster and Stine, 2008, cited from memory)
  with an initial wealth of 0.05, a payout of 0.025 per survivor, and each batch spending at most half of the wealth
  left. Batch b runs Benjamini-Hochberg at the level it is allowed to spend, `q_b`. Only survivors that are later
  **confirmed by the engine** (a gate v3 verdict of `pass` or `evidence_based`) earn the payout. A programme that
  screens many ideas with no confirmed survivor therefore runs out of budget and must stop screening until Product
  resets it by decision record. That is the bound decision 16 section 6 asked for.
- The wealth ledger is one file committed in the repository, `docs/research/tier_one_screen/budget.md`, append-only,
  one line per batch (date, batch id, wealth before, q spent, survivors, wealth after). A batch whose line is not
  committed before its run does not count as screened.

## 5. What a survivor gets, and what a non-survivor gets

- A **survivor** becomes a candidate rule. It needs a pre-registration in `docs/prereg/` before its engine run, as
  every rule does. The pre-registration names its batch and its screen statistic, and its settings are the ones
  screened, unchanged. Its engine run and its gate v3 verdict are the only results that count.
- A **non-survivor** is recorded and is not run through the engine. It may come back only as a new candidate in a
  later batch, with its own budget cost, and never with changed settings in the same batch.

## 6. Every candidate is a trial

Each candidate is logged in the trial registry (`data/trials.jsonl`, `evaluation/trials.append`) with n_trials 1,
its family ID, its batch ID, the screen's statistic and p-value, the batch's q and the code and config hashes.
Survivor or not, it counts toward its family's configurations in S4 (decision 16: the deflated Sharpe counts the
family's own configurations). The screen therefore makes later verdicts in a family stricter, as it should.

## 7. What the code will be (the next pull request; not written yet)

| File | What |
|---|---|
| `config/screen.yaml` | the statistic, the horizon (21 sessions), the charge (10 bp), q (0.10), the alpha-investing settings (0.05, 0.025, at most half spent) |
| `src/kaibot/evaluation/screen.py` | pure: the rank IC per decision day, its Newey-West t and p, Benjamini-Hochberg at q_b, the alpha-investing ledger |
| `scripts/screen_batch.py` | reads a batch file (`docs/research/tier_one_screen/batches/<id>.yaml`, committed before the run), the store, `holdout.enforce`, writes the batch report and one trial row per candidate |
| `tests/unit/test_screen.py` | the rank IC by hand; Benjamini-Hochberg by hand; on synthetic panels with no signal, the share of batches with any survivor at most about q; a planted signal survives; the ledger runs out with no confirmed survivor; a batch file changed after its run is refused |

## 8. Mac command (once the code exists)

```
PYTHONPATH=src python scripts/screen_batch.py --batch docs/research/tier_one_screen/batches/<id>.yaml --data data
```

## 9. Decisions for Product

1. q = 0.10 inside a batch, and the alpha-investing settings (0.05 initial, 0.025 payout, at most half spent per
   batch).
2. Whether the screen is required before every new rule's engine run, or only for batches of three or more
   candidates. The proposal: required for every new **signal**, and not for a re-scaled or re-packaged rule that
   was already judged (such as `trend_momentum_sleeve`, #550).
3. Who may reset the budget (proposal: Product, by decision record).
