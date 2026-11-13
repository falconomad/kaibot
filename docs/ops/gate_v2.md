# Evidence gate v2: how a multi-day rule earns its place (decision 15, section 7)

The M4 gate (`docs/ops/gate.md`) asks for 900 trades and judges each trade on its own. A rule that holds for
weeks would need 4 to 14 years of paper trading to get there, and its trades are not independent: they overlap
and share the market's move. So multi-day rules pass four gates instead. The M4 gate stays in force for
intraday rules. Nothing here trades: these are reports.

The pass marks are fixed in `config/gate_v2.yaml`. Every report records that file's sha256 (`gate_sha`), so a
changed mark is visible. Changing a mark needs a decision in `docs/decisions/` first.

## The four gates

### Gate A: a reason, written down first

1. Copy `docs/prereg/TEMPLATE.md` to `docs/prereg/<rule>.md` and fill in every section: the hypothesis, who is
   on the other side of the trade, the published evidence, the frozen settings, at most five neighbouring
   settings, the fill model, the pass marks, the date.
2. Commit it. Only then run the rule for the first time.
3. The walk-forward calls `prereg.require(rule)` before anything else. That call fails with a plain message
   while the file is missing, unfinished or not committed; otherwise it returns the file's sha256, which is
   logged with the run. Any later edit changes the hash. (The function is built and tested; putting the call
   into `scripts/momentum_core.py` and `scripts/walk_forward_multiday.py` is hand-off 1 below.)

A file written after the first run is marked `Status: retroactive`. The run is still logged, but such a rule
can never pass Gate B. The four first-look rules of #113 are in this state. The same holds when a run was
logged under a different hash than today's file (the file was edited after that run), or when the file was
committed after the rule's first logged run.

### Gate B: the ten-year test

The rule runs in the engine with `SimBroker` from May 2017 onward. All six marks must hold:

| Mark | In plain words |
|---|---|
| 1 | It made money overall, and in at least 6 of the 10 calendar years. A part-year counts only with at least 6 months of data; with more than 10 counted years, 60% of them (rounded up) must be positive. |
| 2 | Either it kept within 2 points a year of SPY while falling at most three quarters as far, or it beat SPY by 1 point a year while falling at most 1.1 times as far. |
| 3 | Its Sharpe is in the top tenth of at least 500 random portfolios that traded on the same dates, held as many names for as long, with the same money at work, drawn from the same stocks. |
| 4 | Marks 1 and 2 still hold when every cost is doubled. Triple costs and the break-even cost are shown. |
| 5 | It holds on the stock list as it was at the time, not only on today's survivors. |
| 6 | Every neighbouring setting named in the pre-registration also passes mark 1. |

`evaluate_gate_b` returns each mark as pass, fail or unknown, with its numbers. Unknown counts as not met: a
missing cost series, random control or point-in-time run can never turn into a pass.

A result is **provisional** (it can fail, it cannot pass) when it comes from the research harness of #113,
cost model v1, data without the repairs of S83 to S85, or price-only SPY. That is every result until those
stories land. Passing Gate B lets Product start the rule on paper.

### Gate C: paper incubation

The rule trades on paper for at least 3 months and 60 sessions. `gate_c_tracking` checks, every day:

- the ledger equals the broker;
- the live daily returns track the `SimBroker` replay of the same days: the mean absolute difference over a
  rolling 20 sessions is at most 10 basis points (not judged before the 20th session);
- the live drawdown is inside the 95% band of drawdowns the ten-year test could have produced over a stretch of
  the same length (block bootstrap);
- the retirement trigger was committed before the start.

Only sessions that ran wholly on the rule's current code count (its fingerprint, see "Frozen code" below): a
change to the rule or to the shared core restarts the incubation.

The rule is **retired** when its drawdown leaves the 99% band, or when it tracks outside the tolerance on 10
sessions in a row. The report says `incubating`, `pass`, `fail`, `incomplete` (something could not be checked) or
`retire`.

### Gate D: real money

The stakeholder's decision. It starts small. Nothing in the code can switch it on.

## What the numbers are

All statistics are on **daily mark-to-market returns** after costs (`kaibot.evaluation.mtm`), not per trade.

| Number | What it tells you |
|---|---|
| Sharpe, with its haircut | Return per unit of risk. Every backtested Sharpe is printed with half of itself beside it: plan with the smaller one. Above about 1.0 for a long-only stock rule the report prints a warning. |
| t-stat (Newey-West) | Whether the Sharpe could be luck, allowing for days that move together. The lag is fixed by the length of the sample: floor(4 x (days / 100)^(2/9)), 8 lags for ten years. |
| Alpha and beta against SPY total return | How much is market exposure (beta) and how much the rule added (alpha, with its own t). A long-only book is mostly beta. |
| Max drawdown | The deepest fall of the account marked at each close, open losses included. |
| Calendar-year table | Return per year beside SPY's. Part-years are marked; one under 6 months is marked "not counted" (it is not a year for mark 1). |
| 1x, 2x, 3x costs and break-even | The same numbers with costs scaled, and the cost multiple at which the rule makes nothing. |
| Deflated Sharpe | The chance that the Sharpe is real after allowing for everything that was tried. It uses the number of configurations in the trial registry. |
| Drawdown bands | The 95% and 99% worst drawdowns from 2,000 resamples of the test in blocks of about 20 sessions. |

For several rules together, `portfolio_view` shows the combined book and how the rules move together once the
market is taken out (`residual` correlation). Two long-only rules always look alike before that step.

## The matched random control (mark 3)

`random_control.matched_random_control` keeps the rule's whole timetable (each entry date, holding period and
position size) and replaces only the names, drawing from the stocks that were eligible that day. It does this
at least 500 times, seeded, and reports the share of random portfolios the rule beat.

It needs one function from the simulator: `simulate(selection) -> daily net returns`. Hand-off 4 below says how
the #113 engine provides it.

## One trial registry

Every replay and walk-forward logs one row per run in `data/trials.jsonl`. A row's `n_trials` is the number of
configurations that run searched. Everything now counts **configurations, not rows**, with one function
(`kaibot.evaluation.trials.count_configurations`): the walk-forward scripts, `trial_report.py`, the M4 gate
report and gate v2. A run logged twice counts once. A line that cannot be read counts as one.

```
.venv/bin/python scripts/trial_registry.py count            # the number everything deflates by
```

**Inside the engine container.** The image holds no `data/` folder, so the gate used to see no research trials
at all. It now looks for the file at `$KAIBOT_TRIALS_FILE`, then `/app/data/trials.jsonl`, then
`$KAIBOT_DATA_DIR/trials.jsonl`. `scripts/run_engine.sh` and `compose.yaml` already mount `data/live` as `/data`,
so the registry is read from `data/live/trials.jsonl`:

```
cp data/trials.jsonl data/live/trials.jsonl      # after a research session, before the engine's next start
```

A stale copy only misses the newest runs. Each gate report lists what it read under `trial_registry.sources`,
so a missing file shows as `not found`. Two tidier options:

- a read-only mount of the file itself: add `-v "$PWD/data/trials.jsonl:/registry/trials.jsonl:ro" -e
  KAIBOT_TRIALS_FILE=/registry/trials.jsonl` to the `docker run` line (run `touch data/trials.jsonl` first:
  Docker creates a folder when the file is missing). This needs a small change to `scripts/run_engine.sh` and
  `compose.yaml`, which this story did not touch;
- the database: migration `0015_m7_trial_registry.sql` adds the append-only table `kaibot.trial_registry`.
  Once the stakeholder has applied it, `scripts/trial_registry.py push --env <.env> --yes` copies new rows
  there and the container needs no file. Until then the table is skipped.

## Frozen code, per rule

The fingerprint in `config/gate.yaml` now has a shared `core` and a list of files per rule. A rule's evidence
clock restarts when the core or its own files change, not when another rule changes. `strategies/`, `broker/`
and `evaluation/` are covered. A new file in `strategies/` that no rule claims counts as core, so add the rule
to `fingerprint.strategies` when it gets its own file. The engine still logs one string at start-up
(`<all>;core=<hash>;<rule>=<hash>;...`); the dashboard shows its first eight characters as before.

## Commands

```
.venv/bin/python scripts/gate_v2_report.py --rule <rule> --daily <daily.csv> --spy <spy.csv> \
    [--trades <trades.csv>] [--random <control.json>] [--neighbour <name>=<daily.csv>]   # Gate B report
.venv/bin/python scripts/trial_registry.py count
.venv/bin/python -m pytest -q tests/unit/test_mtm_stats.py tests/unit/test_random_control.py \
    tests/unit/test_gate_v2.py tests/unit/test_trial_registry.py tests/unit/test_prereg.py
```

## Hand-offs for Backend-quant

1. **Pre-registration in the runners.** `scripts/momentum_core.py` (#127) has its own `prereg(rule)` that hashes
   the file but does not check that it is committed. Take the hash from `prereg.require(rule)` instead (the same
   sha256, plus the refusal while the file is unfinished or has uncommitted edits), and exit with the
   `PreregError` message. Do the same in
   `scripts/walk_forward_multiday.py`, one line after `a = ap.parse_args()`. Add `"gate_v2_sha256":
   GateV2.load(ROOT).sha` to the report and to every trial row. Both #127 files load with the checker as they
   are (`tests/unit/test_prereg.py::test_every_committed_pre_registration_is_readable`).
2. **Daily costs.** Add to each `book.daily` row the dollars of spread, impact and fees charged that session
   (`cost`) and the dollars bought plus sold (`traded`). Until then `gate_v2_report.py` spreads each trade's
   costs over its entry and exit day, which is exact in total and approximate in timing.
3. **SPY per session** as a CSV (`date`, `ret`) for the test window. Done (#185): `walk_forward_multiday.py`
   writes `spy_daily_<tag>.csv` beside the daily CSVs (`ret_kind`: total return when the daily store has SPY,
   else price only) and `momentum_core.py` writes `<tag>/spy_daily.csv`.
4. **Random control: switch `multiday_core.matched_random_control` to the exposure-matched control.** Today it
   keeps the entry dates and the number of entries per date, draws each holding period independently from the
   rule's distribution, and sizes each random name with the rule's own ATR formula. So the random books carry a
   different exposure from the rule's on most days, and their Sharpe is not comparable (amendment 1 of both
   #127 pre-registrations: the Sharpe percentile, 73 to 90 on real data against the 90 mark, is provisional).
   The switch, inside that one function (its docstring already names it):
   - **Schedule.** One `random_control.Slot` per position the rule opened (resize rows are not slots):
     `entry` = the session index of the entry fill, `hold` = `t.hold_days`, `weight` = the position's value
     at entry over the account's equity at that close. The k-th random name inherits slot k whole: same entry
     day, same exit day, same money at work.
   - **Resizes** (`trend_momentum` resizes weekly). Keep, per slot, the rule's own resize prints as
     `(session, value / equity)`. The control's replay rule sets the random name to that same share of equity
     on those sessions. `random_control` never sees this: it lives in the `simulate` closure.
   - **`eligible(i)`**: the harness's universe filter on row `i - 1` (price >= $5, 20-day dollar volume >=
     $50M). Not the rule's own score or trend filter: those are the skill being tested.
   - **`simulate(selection)`**: run `Engine` with a replay rule that enters `selection[k]` at the slot's fill
     (same fill model, costs and caps as the rule's run), resizes as above and exits on the slot's exit day;
     return `pnl / start` per session.
   - Call `random_control.matched_random_control(rule_returns, schedule, eligible, simulate, runs=500,
     seed=127, strategy_selection=<the rule's own name ids per slot>)`. The rule's names through `simulate`
     must reproduce its Sharpe closely; if not, the replay rule is wrong. `evaluate_gate_b` counts the lower of
     the two percentiles. `result.matched` is false when a slot found no free eligible name; then mark 3 is
     not judged.
   - Keep today's entries-matched numbers in the report as a diagnostic, labelled as such; feed the new
     `ControlResult.to_dict()` to `gate_v2_report.py --random`.
5. **A point-in-time run** (S83) labelled `universe="point_in_time"`, for mark 5.
6. **Gate C inputs** (S93): the rule's live daily mark-to-market returns from the ledger, the `SimBroker`
   replay of the same days, and one ledger-equals-broker flag per session.

## Open points for Product

Each was implemented in its strictest reading. None can let a rule pass that should not.

Points 1 to 3 and 7 were settled by decision 15 section 10 (Product, 12 Nov 2026) and are in
`config/gate_v2.yaml` (#183, #184). Every report made before that change carries the old `gate_sha` and is stale.

1. **Tracking tolerance (settled).** Mean absolute difference between the live daily returns and the replay,
   over a rolling 20 sessions, at most 10 bp per session. Before the 20th session the mark is not judged.
2. **"10 sessions" outside tolerance (settled):** 10 sessions in a row.
3. **"6 of 10 calendar years" (settled):** a part-year counts only with at least 6 months of out-of-sample data
   (6 calendar months with a session); the threshold is then 60% of the counted years, rounded up. With the
   window from May 2017, 2017 (8 months) counts.
4. **Mark 3 for one-instrument rules** (`overnight_spy`, the SPY noise band): there is no other name to draw,
   so the mark stays unknown. They need a random-timing control, which is not specified.
5. **Mark 5** is read as marks 1 to 4 on the point-in-time run. **Mark 6:** a rule with no neighbour has had
   no plateau check and does not meet the mark. **Mark 3:** when the rule's own names are also run through
   the control's simulator, the lower of its two percentiles counts.
6. **Costs in mark 4** are spread, impact and fees. Gap slippage is a price outcome and is not doubled.
   Doubling keeps the positions as they were.
7. **Bootstrap (settled):** stationary, blocks of 20 sessions on average, 2,000 resamples, a fixed seed (15), band
   for a stretch as long as the live record. `GateV2.bootstrap_kw` is the one reader of these settings.
8. **Trial count:** all rules together, a tuned walk-forward counting grid size times folds (the reading
   behind the decision's 8,286). The deflated Sharpe is shown, it is not a pass mark.
9. **Reports of one configuration.** A row with `n_trials: 0` counts nothing: `momentum_core.py` logs the 2x
   and 3x cost runs, the stress fill and the 0x break-even input that way, as reports of the base run. The
   exposure caps and the earnings versions do count, one configuration each.
10. **"Same universe, liquidity filter" in mark 3** is read as the harness's price and dollar-volume filter,
   not the rule's own trend or score filters. Exposure is matched per position, resizes included.
