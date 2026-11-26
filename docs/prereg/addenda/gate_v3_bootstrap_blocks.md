# Pre-registered sensitivity addendum: gate v3's bootstrap block length at 60 and 120 sessions

- **Ticket:** M17 6.9 of the second engine audit's staged plan, issue #709. Backend-quant, 26 Nov 2026.
- **Status:** frozen when committed. Append-only: a later change is a new section `## Amendment n` at the end, and
  an amendment written after any result computed under this addendum is labelled hindsight, as in decision 18
  section 4.
- **Committed before any verdict uses it.** No report, re-score or verdict has been computed at a mean block of 60
  or 120 sessions when this file is committed. `tests/unit/test_gate_v3_block_sensitivity.py` checks that every
  results file that reports these blocks was first committed after this file.
- **What it does not change:** `config/gate_v3.yaml`, its sha256 (pinned in docs/decisions/18_gate_v3_1.md), any
  mark, level or threshold, any rule, the engine, live paper trading. The judged bootstrap stays the stationary
  bootstrap with a **mean block of 20 sessions**, 2,000 runs, seed 16 (`bootstrap:` of `config/gate_v3.yaml`).

## Why

The audit marked it [suspected]: "A 20-session bootstrap block is short for year-long drawdowns". A stationary
bootstrap with a mean block of 20 sessions (about a month) keeps dependence only within about a month. The
drawdowns that decide B2 are long: SPY's 2022 fall ran for most of a year (about 200 sessions), and a vol-capped
core's drawdown is shaped by how volatility clusters over months. Resampled decades built from month-long pieces
can understate how deep and how long a drawdown gets, and how far a long-run CAGR difference can move. The
sensitivity asks whether any bootstrap-based number would read differently with pieces of about a quarter (60) and
about half a year (120).

## Frozen settings

| # | Setting | Value |
|---|---|---|
| 1 | Method | stationary bootstrap (Politis and Romano, 1994), as `bootstrap.method` |
| 2 | Mean block lengths of the sensitivity | **60 sessions** and **120 sessions** (the judged run stays at 20) |
| 3 | Runs per block length | 2,000, as `bootstrap.runs` |
| 4 | Seed | 16 for each block length, as `bootstrap.seed` (the same seed, so only the block length differs) |
| 5 | Interval | two-sided 90%, as `bootstrap.interval` |
| 6 | Window | the research window only, 2017-05-01 to 2024-12-31; hold-out sessions are dropped first, as for the judged numbers. The hold-out is not opened for this addendum |
| 7 | Pairing | book and SPY (and a sleeve and its benchmark) resampled with the same rows, as the judged numbers |

## What is recomputed at 60 and at 120

Every number that gate v3.1 takes from the bootstrap, and nothing else:

1. **S1:** the 90% interval of the sleeve's annualised active return.
2. **B2, intervals (printed, not judged):** the CAGR-minus-SPY interval, the drawdown-over-SPY interval, the share
   of resamples meeting either branch, and the share with a shallower drawdown than SPY.
3. **B2, chance test (judged at 20):** V, the variance of the Sharpe difference over the paired resamples; the
   hurdle SR0 = `expected_max_sharpe(N, V)` with the same N; and the DSR.
4. **The final check on the hold-out:** the 5th-percentile band of the research-window stretches. Computed only if
   and when the hold-out is opened for a final check under decision 16 section 7; this addendum never opens it.

Point estimates (CAGR, drawdown, Sharpe, the branches of B2, B1, B3's costs, B5, B6, B7) do not use the bootstrap
and are not recomputed.

## How it is read

- The verdict is the gate's verdict at a mean block of 20 sessions. This addendum **never turns a fail into a
  pass**, and it does not by itself turn a pass into a fail: that would change a mark, which needs a decision
  record (decision 16 section 13).
- A **pass** (or an evidence-based verdict) whose judged bootstrap-based condition (B2's chance test at DSR 0.90,
  or the hold-out band when it is opened) does not also hold at **both** 60 and 120 sessions is printed as
  "`<verdict>`, **block-sensitive** (fails at a mean block of <n> sessions)". A block-sensitive pass may not be
  proposed to the stakeholder without that label beside it.
- A **fail** stays a fail. Its numbers at 60 and 120 are printed as information.
- The printed sensitivity always shows all three block lengths side by side, never only the more favourable one.

## Expectation, before any computation

- Intervals (S1, B2) at 60 and 120 are expected to be **wider** than at 20, the drawdown-over-SPY interval most of
  all, because longer pieces keep more of the clustering that makes drawdowns deep.
- The share of resamples meeting B2's defensive branch for the 80% index core (21.4% at a block of 20, re-score of
  25 Nov 2026) is expected to move by a few points, direction not predicted.
- B2's chance test: a larger V raises the hurdle and also the denominator, so the direction of the DSR is **not
  predicted**. No book passes B2's branches today, so no verdict is expected to change.

## Trials

This addendum runs no rule and logs no trial row. It re-reads reports already written (as
`scripts/gate_v3_rescore.py` does) and recomputes bootstrap numbers from their daily files. It adds no
configuration to any family or book idea.
