# Go-live gate (M4: S38 report, S41 review)

The gate answers one question: has a strategy earned a go-live *discussion*? It never switches anything. Kaibot
trades paper only, and there is no live path in the code.

This is the gate for **intraday** rules. Rules that hold for days are judged by evidence gate v2
(`docs/ops/gate_v2.md`, decision 15).

## The bar (pre-registered)
`config/gate.yaml` holds the PRD v2 §1 bar. Each strategy is judged separately, on frozen code:

| Criterion | Bar |
|---|---|
| Trades | ≥ 900 |
| Forward paper | ≥ 3 months |
| Expectancy after shadow costs | t > 3 (per-trade P&L) |
| Deflated Sharpe | > 0.5 as a probability (= z > 0), deflated by every configuration in the trial registry |
| Random entries | ≥ 95th percentile of 1,000 runs (same days, entry counts, sizing and exits) |
| Sharpe | above SPY's, same sessions, annualised |
| Drawdown | within the level's cap (`run_drawdown_end_pct`) |

**Kill** ("50 trades can only kill"). From 50 trades on, a strategy is killed in any of these cases:
- its t-stat is ≤ −2;
- it is at or below the 5th percentile of random entries;
- at any trade count, its drawdown exceeds the cap.

Below 900 trades or 3 months a strategy is **provisional**. It can be killed, but it can never pass, and the
page shows unmet rows in slate ("not yet"), not red.

Every report records the sha256 of `gate.yaml`. Changing the bar needs a decision in `docs/decisions/` first.

## Frozen code
At start-up the engine hashes the files listed in `gate.yaml` and logs the fingerprint in the `engine_start`
event. Since decision 15 there is one fingerprint per rule plus a shared core:

- the **core** (engine, risk, broker, evaluation, replay, features, agents, news, shared strategy files,
  profiles, LLM config, universe): a change restarts every rule's clock;
- a **rule's own files** (for example `strategies/spy_momentum.py` for SPY momentum): a change restarts that
  rule's clock only.

Only a rule's trades since the first start of the latest unbroken stretch with its current fingerprint count.
Dashboard, docs and test changes don't reset the clock. The report shows how many older trades it excluded, and
each rule's own `fingerprint` and `frozen_since`. The header's "frozen since" is for the whole code.

**Trial count.** The deflated Sharpe is deflated by the number of configurations in the one trial registry
(`docs/ops/gate_v2.md`), the same count the walk-forward uses, not by the number of logged rows. The report
lists what it read under `trial_registry`. With fewer than two logged Sharpes for a rule the spread of trial
Sharpes is unknown and the hurdle stays zero; `n_trial_sharpes` shows when that is the case.

## How it is computed
After each session (`_finalize_day`) the engine does two things:
1. Simulates 1,000 random-entry strategies on that day's bars, with the same number of dip entries, sizing and
   exits as replay, seeded by (run, session). It stores the totals in `kaibot.gate_random`. The first write wins,
   so a restart never recomputes a day.
2. Recomputes the whole report from stored trades, sessions, the random baseline and the trial log, then appends
   it to `kaibot.gate_report`.

Run k of the period is the sum of run k of each day, so months of minute bars never have to be kept. A trading
day without a baseline (the engine was down at the close) leaves the random row **unknown**. Unknown counts as not
met, so the gate fails closed.

Both tables are append-only. Triggers block update, delete and truncate for every role (migration 0010). Nobody
can edit a verdict. A failure in this hook is logged and never affects trading.

The random baseline currently exists for the dip reversal. SPY momentum reports that row as unknown until a
baseline is pre-registered for it.

## Reading it
Open the dashboard and go to **Go / no-go**. It shows:
- the overall verdict: *keep collecting*, *discuss* (a strategy cleared the bar) or *kill*;
- one card per strategy, with every criterion: its value now, the bar, and its state;
- the verdict history.

The page has no buttons; a vitest test enforces that.
