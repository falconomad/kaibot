# Pre-registration: winners_run

**Retroactive, first look, cannot pass Gate B.** Written on 12 November 2026, after the rule was built and first
run in issue #113. It records what was built so that the run can be logged with a hash. It is not evidence that
the settings were chosen before the data was seen.

- **Rule:** `winners_run`
- **Status:** retroactive, first look, cannot pass Gate B
- **Date:** 2026-11-12
- **Author:** qa-ops, from backend-quant's definition in `src/kaibot/replay/multiday_rules.py` (issue #113)
- **Issue:** #113 (rule), #126 (this file)

## Hypothesis

The intraday dip rule (variant A) gives up its best trades by selling everything at 15:55. Keeping the trades
that are already well in profit for a few more sessions should add return, because stock gains are earned
mostly overnight. Decision 15 expects a first look only: "not a verdict".

## Who is on the other side of the trade

No counterparty can be named with confidence. The carried positions are bought from whoever sells a recovering
stock before the close. This is an exit variation of the retired dip rule, not a rule with its own reason.

## Published evidence

None for this construction. The nearest findings are that momentum and the market's premium are earned
overnight and that carrying an intraday long to the next open helped a published SPY rule before costs. See
`docs/research/engine_v3/notes_intraday_overnight_strategies.md`, section 3. The entry is the dip rule, which
has zero gross edge over ten years (`docs/m0/experiments_001.md`).

## Frozen settings

As built in #113; the fixed account uses these values:

- Entry: variant A's intraday dip entry, unchanged, at its pre-registered defaults.
- Carry: a trade still open at 15:55 and up at least 0.5R is kept overnight, as far as it fits the gap sizing
  (a 3-ATR gap costs at most the level's risk per trade); the rest is sold at 15:55 as before.
- Exit of a carried position: trailing stop 2 daily ATRs under the highest close; out after 5 sessions.
- Limits: at most 3 carried positions and 25% of equity (the intraday leg can use the other 75%); long only, no
  leverage. Level: Balanced.
- Not frozen in #113: the "tuned" account re-chooses the holding period among three settings every month. Only
  the fixed account matches these settings.

## Neighbours (at most five)

The two other settings of the #113 grid:

- `winners_run_hold3`: out after 3 sessions
- `winners_run_hold10`: out after 10 sessions

## Fill model

The research harness of #113, not the engine with `SimBroker`. The intraday leg is the intraday replay of
variant A (next-bar open at the modelled bid or ask plus impact, cost model v1), scaled to this account. A
carried position is taken over at its intraday fill, sold at the stop when the day's low reaches it, at the open
when the open is already below it, or at the close print after 5 sessions.

## Pass marks

The Gate B marks of `config/gate_v2.yaml` are computed and shown, but this rule cannot pass: the
pre-registration is retroactive, and the run is on the research harness, cost model v1, today's stock list and
price-only SPY, which decision 15 labels provisional. Its intraday leg stays under the M4 gate
(`config/gate.yaml`).
