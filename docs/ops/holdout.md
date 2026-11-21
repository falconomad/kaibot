# The hold-out window (M11 ticket 2.6, issue #457)

**In one line:** every session from **1 January 2025** on is held back. A research run that would read it fails.

Product decided on 21 Nov 2026 that a hold-out is to be frozen (decision D5,
`docs/research/engine_audit/decisions.md`). This page fixes the length and says how the guard works.

## The window

| | |
|---|---|
| Held back | every session on or after `holdout_from` = 2025-01-01 (`config/holdout.yaml`) |
| Research window | 2017-05-01 (decision 15) to 2024-12-31 |
| Length today | 22.6 months to 20 Nov 2026 (D5 asked for 18 to 24) |
| Why this date | it ends the research window on a whole calendar year |
| New sessions | fall inside the hold-out as they arrive; the hold-out only grows |

Moving `holdout_from` to a later date hands held-back data to research. That needs a decision in
`docs/decisions/` first. An earlier date only holds back more.

## What a research run does now

Every research script takes its window from `--end`. Before it reads anything it calls
`kaibot.evaluation.holdout.enforce`:

- `--end 2024-12-31` or earlier: the run goes ahead. Nothing is logged.
- A later `--end`, or a script that reads to the newest bar: the command fails with
  `hold-out: <script> would read to <date>, inside the hold-out ...`.

`./scripts/run_experiments.sh` now ends on 2024-12-31 by default. Reports made before the freeze (to 2026-11-06)
stay where they are. A run to the new end is a new report, and it is logged as a new trial.

Guarded scripts: `momentum_core.py`, `oneshots.py`, `spy_engine.py`, `spy_noise_band.py`, `spy_trend.py`,
`walk_forward.py`, `walk_forward_multiday.py`, `run_replay.py`, `run_shadow_replay.py`,
`run_spy_momentum_replay.py`, `filing_flags_study.py`, and the comparisons of `market_alarm_report.py` (its flags
and rankings read no outcome and stay open, because the live alarm needs the rankings).

A test (`tests/unit/test_holdout.py`) reads every file in `scripts/`. A script that imports the replay, the
research runner or the daily store must call the guard, or be on the test's exemption list with a reason.

## Opening the hold-out

The hold-out is opened for one purpose at a time, by a pull request that appends an entry to `openings` in
`config/holdout.yaml`:

```yaml
openings:
  - id: 2027-02-01.my_rule.final
    rule: my_rule
    purpose: final_check          # final_check | gate_c_tracking | data_check
    scripts: [momentum_core.py]
    max_runs: 12
    decided_by: Product
    decided_on: 2027-02-01
    reason: "passed gate v3 on the research window (issue #...); the final check its pre-registration declared"
```

Then the run names it: `--open-holdout 2027-02-01.my_rule.final` (or `KAIBOT_HOLDOUT_OPENING=<id>` for
`run_experiments.sh`). The run is refused if the opening is not in the file, is for another rule or another
script, or has used up its reads.

**The opening is logged twice.** The entry is in the repository's history. Each read appends a line to
`data/holdout_access.jsonl` on the machine that ran it: time, opening, rule, script, window, the config's hash,
the code version and the command's arguments. If the line cannot be written the run does not start. The run
also prints `HOLD-OUT OPENED: ...`.

| Purpose | When | What it may be used for |
|---|---|---|
| `final_check` | a rule has passed every research-window mark of gate v3 (decision 16) | the one look at the hold-out; its result is reported whatever it is |
| `gate_c_tracking` | a rule is on paper | the `SimBroker` replay of sessions the paper engine already traded; it judges tracking, never the rule |
| `data_check` | a data repair must be verified on recent sessions | a run whose output holds no rule result |

Rules for an opening: it is append-only (never edited or removed); a failed final check is not repeated with
changed settings (a changed rule is a new pre-registration, and the hold-out has then been seen once for that
idea); `max_runs` is set to the number of script calls the check needs and no more.

## What the hold-out is not

- **It is not untouched data for the rules that exist today.** The window was frozen on 21 Nov 2026. Every rule
  tested before that date was run on data up to November 2026, and anyone designing a new rule has seen those
  results. So for the 17 existing pre-registrations a hold-out result is a consistency check, not an
  out-of-sample test, and a report must say so in those words. The hold-out is clean only for sessions after
  21 Nov 2026 and for ideas that owe nothing to the earlier results; it gets cleaner with every month.
- **It guards scripts, not files.** Reading the parquet files by hand is not prevented. The guard removes the
  accidental read.
- **It does not touch the paper engine.** The live engine trades today's sessions as before.
- **Not guarded, by choice** (for Product to confirm): `run_config_replays.py` (the dashboard's what-if replays of
  the owner's own settings on recent sessions) and `run_ablation.py` (it reads trades already made and alt data
  collected since November 2026, and loads no price history).

## Effect on the gates

Gate v2's mark 1 needs ten counted calendar years. The research window has eight (2017 as a part-year with
eight months, then 2018 to 2024), so no run on the research window alone can pass Gate B of gate v2. Gate v3
(ticket 2.5, decision 16) is written for the research window and uses the hold-out as its final check.

## Commands

```
.venv/bin/python scripts/momentum_core.py --version trend_momentum --end 2026-11-06    # fails: hold-out
.venv/bin/python scripts/momentum_core.py --version trend_momentum --end 2024-12-31    # runs
cat data/holdout_access.jsonl                                                           # every read so far
```
