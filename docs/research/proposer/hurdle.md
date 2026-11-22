# The counted proposer: the hurdle, published before the first proposal (M12 ticket 3.10, issue #472)

- **Status:** published 22 Nov 2026 by Quant research, **before any proposal exists**. The ledger
  `docs/research/proposer/proposals.jsonl` is empty in the commit that adds this file. Changing the hurdle after the
  first proposal needs a decision record.
- **Why** (`docs/research/engine_audit/report.md`, "Honest uses of AI"; the audit's sixth innovation): research
  systems that let a model try ideas report best-of-N numbers with no correction for the number of tries. Here a
  model may only **propose**. Deterministic code parses, counts, tests and judges, and every machine idea raises the
  bar for every idea, human ones included.
- **Live trading:** none. No proposal trades, and nothing here is imported by the engine.

## 1. What a proposal is

One formula in the restricted language of `src/kaibot/evaluation/proposals.py`: returns over a window (with a skip),
volatility, the distance to a moving average or to a recent high, cross-sectional rank and z-score, sign, constants,
sums and products. It has at most 12 nodes and reads at most 504 sessions back. Nothing a model writes is run as
code; the formula is parsed into a tree of those functions. A proposal names its family in `config/gate_v3.yaml` and
a one-line reason.

## 2. Limits

| Limit | Value |
|---|---|
| Machine proposals (`proposer: model:<pinned model id>`) | **at most 3 per ISO week**; a fourth is refused and written nowhere |
| Human proposals | no weekly cap, the same count and the same hurdle |
| Duplicates | recognised by the canonical form (sums and products sorted), **accepted and counted again**: a model that repeats itself spends its tries |
| A refused formula (unknown function, too deep, too big, reads no price) | not counted: it never became a hypothesis |

## 3. The hurdle

1. **Every accepted proposal is a trial.** At intake, one row goes to the proposals ledger and one to the trial
   registry (`variant: proposal`, n_trials 1, its `proposal_id`). The registry now refuses any unregistered run
   (`trials.registration`): every row carries a pre-registration hash, a screen batch or a proposal id.
2. **Then the tier-one screen** (ticket 2.11, `docs/research/tier_one_screen/design.md`). Proposals enter batches
   like any candidate, and Benjamini-Hochberg runs at the batch's level from the alpha-investing budget (0.045 for
   the first batch, about half of that for each later batch without a confirmed survivor). Proposals and human
   candidates share that one budget. A screened proposal's screen row carries n_trials 0, because its proposal row
   already counted it. That needs a one-line follow-up in `scripts/screen_batch.py` once #574 merges.
3. **A survivor needs a pre-registration** in `docs/prereg/`, written by a person, and an engine run with
   `SimBroker`. Its verdict is gate v3's, with its family's count and correction, like every rule's.
4. **What is published with every batch:** the number of proposals so far (machine and human), the duplicates, the
   batch levels, and the survivors. A survivor's report states how many proposals preceded it.

## 4. What the proposer job is, and is not

The job that asks a model for formulas belongs to Agents-AI (it needs a pinned model and its spend cap). It calls
`scripts/propose.py` with each formula. It never writes the ledger itself, never sees credentials, and never touches
`docs/prereg/` or any money-path code.
