# Pre-registration: dip_reversal

- **Rule:** `dip_reversal`
- **Status:** retroactive (written 22 Nov 2026, long after this rule's first runs; it can never pass a gate)
- **Date:** 2026-11-22
- **Author:** quant research
- **Issue:** #472 (M12 ticket 3.10: the registry refuses unregistered runs)

**Why a retroactive file.** The intraday dip rule traded on paper from M1 and was retired by decision 15 (it now
runs only as the plumbing soak test, and decision D4 removes it from the live loop). Its runs predate pre-registration.
Since #472 the trial registry and every research entry point refuse a run without a pre-registration. This file
registers the rule **as it is in the code**, so its remaining research runs (`scripts/run_replay.py`,
`scripts/walk_forward.py`, `scripts/run_ablation.py`) are counted under one hash. `Status: retroactive` means gate v2
and gate v3 never let it pass (`evaluation/prereg.py`). Nothing here changes the rule or any trading.

## Hypothesis

A liquid stock that falls sharply against its sector during the session, without company news in the last 24 hours,
and then prints a five-minute higher low, rebounds by more than the costs before the close. The ten-year replay
rejected it (decision 15 section 1: the rule lost 59% while SPY rose 209%).

## Who is on the other side of the trade

Sellers who need liquidity during the session (forced or hurried selling) without new information. The replay found
no edge after costs against them.

## Published evidence

Intraday reversal after liquidity shocks is documented for less liquid stocks, and it weakens sharply in large caps
and after costs. The project's own write-ups are `docs/m0/replay_001.md` to `replay_003.md`,
`docs/m0/walk_forward_10y.md` and decision 15 section 1.

## Frozen settings

As in the code at the date of this file: `src/kaibot/strategies/dip.py` (Variant A, with variants A/B of
`scripts/run_replay.py` as pre-registered in `docs/m0/replay_002.md` and `replay_003.md`), the risk gate's sizing
(`risk/gate.py`), and the levels of `config/profiles.yaml`. Candidates come from the point-in-time dip scan
(`replay/features.candidates`), the news veto covers 24 hours, the stop is 1.5 x ATR(5-minute), the target 2R, and
the trailing stop follows the highest close minus 1.5 ATR once the trade is 1R up. Flat by 15:55.

## Neighbours (at most five)

- None: the walk-forward's tuning grid (`scripts/walk_forward.py`) is counted as configurations in each of its rows.

## Fill model

Cost model v1 of the replay (`replay/sim.py`: half a spread of at least 1 bp plus impact), minute bars.

## Pass marks

None that can be met: a retroactive pre-registration never passes a gate (decision 15 section 7; decision 16).
