# Pre-registration: overnight_spy

**Retroactive, first look, cannot pass Gate B.** Written on 12 November 2026, after the rule was built and first
run in issue #113. It records what was built so that the run can be logged with a hash. It is not evidence that
the settings were chosen before the data was seen.

- **Rule:** `overnight_spy`
- **Status:** retroactive, first look, cannot pass Gate B
- **Date:** 2026-11-12
- **Author:** qa-ops, from backend-quant's definition in `src/kaibot/replay/multiday_rules.py` (issue #113)
- **Issue:** #113 (rule), #126 (this file)

## Hypothesis

SPY earns most of its return between the close and the next open. Holding it only overnight, and only when the
previous day's VIX close is under 30, should capture that return with half the time in the market. Decision 15
expects a first look only: "not a verdict".

## Who is on the other side of the trade

Traders who sell at the close so as not to carry risk overnight, and buyers at the open. The premium is about
the size of a realistic round-trip cost, so they may be giving up nothing after costs.

## Published evidence

The overnight premium in SPY is real and has persisted, at about 4 bp a night, but it is roughly the size of a
round-trip cost, the one live product lost money, and unlevered it earns less than holding. The research report
concludes that an unconditional overnight trade deserves no slot. See
`docs/research/engine_v3/notes_intraday_overnight_strategies.md`, section 3.

## Frozen settings

As built in #113; the fixed account uses these values:

- Instrument: SPY only.
- Entry: buy at the close when the previous day's VIX close (FRED VIXCLS) is under 30. Exit: sell at the next
  session's open.
- Sizing: one position, sized like every other (a 3-ATR gap costs at most the level's risk per trade) and never
  more than the level's position cap, which is 15% of equity at Balanced. Long only, no leverage.
- Not frozen in #113: the "tuned" account re-chooses the VIX limit among three settings every month. Only the
  fixed account matches these settings.

## Neighbours (at most five)

The two other settings of the #113 grid:

- `overnight_spy_vix_max0`: no VIX filter
- `overnight_spy_vix_max25`: VIX under 25

## Fill model

The research harness of #113, not the engine with `SimBroker`: buy at the close print of the last regular-hours
minute (not the closing auction) and sell at the open print of the first minute, each with half a spread and
impact (cost model v1).

## Pass marks

The Gate B marks of `config/gate_v2.yaml` are computed and shown, but this rule cannot pass: the
pre-registration is retroactive, and the run is on the research harness, cost model v1 and price-only SPY, which
decision 15 labels provisional. Mark 3 cannot be computed for a single-instrument rule with the random-name
control: it needs a random-night control that has not been specified.
