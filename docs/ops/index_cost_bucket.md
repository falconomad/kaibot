# The index-fund cost bucket (M11 ticket 2.3, issue #454)

Cost model v2 charges SPY what it charges every stock that trades over $500M a day: 1.75 bp a side. The SPY
noise-band rule's pre-registration says it needs fills under about half a basis point a side, and its ten-year
test failed on exactly that cost. This ticket adds a way to price an index fund by its own measured spread.
**Nothing is measured yet, and no verdict changes here.**

## What exists after this ticket

| Piece | What it does |
|---|---|
| `kaibot.broker.costs_v2` | An index-fund bucket chosen by symbol. Off by default: `get_model("v2")` is unchanged to the last bit. `get_model("v2_index")` is v2 plus the bucket |
| `config/costs_index_bucket.yaml` | The bucket's symbols (SPY) and its value. The value is empty; while it is empty the model refuses to build |
| `scripts/calibrate_index_costs.sh` | Measures the fund's quoted spread at the moments it is traded. Read-only |
| `scripts/spy_cost_rerun.py` | One engine run of the frozen SPY rule with the bucket. Refuses to run without a value |

The rule that turns the measurement into the value is written in `config/costs_index_bucket.yaml` and was fixed
before any measurement: the mean quoted half-spread, rounded up to the next 0.05 bp; from the paper account's
fills when there are at least 30 on at least 5 sessions, else from quotes at the rule's check times on the last
20 sessions. What the paper fills paid beyond the quote is reported and not used, because the paper venue fills at
the quote with no queue and no impact.

## Steps, in order (on the research Mac)

1. **Measure.** Read-only; about seven minutes; it can run while the engine runs.

   ```
   cd ~/Projects/kaibot && git pull
   ./scripts/calibrate_index_costs.sh
   ```

   It prints a table and a block for `config/costs_index_bucket.yaml`, and writes
   `data/reports/index_cost_calibration.md` and `.json`. If the account has too few SPY fills it samples quotes
   by itself; `--quotes-only` skips the fills. Exit code 3 means there was too little to measure.

2. **Commit the value, by pull request.** Backend-quant puts the printed block into
   `config/costs_index_bucket.yaml` and the table into `docs/research/results/index_cost_calibration.md`. This is
   a parameter change of the cost model.

3. **Re-run the SPY rule's costs.** One engine run, about 40 minutes of CPU.

   ```
   .venv/bin/python scripts/spy_cost_rerun.py --end 2024-12-31
   ```

   It writes `data/reports/experiments_spy_cost_rerun.md` and logs one trial (n_trials 1). The page shows the new
   row beside the earlier run's rows on the same sessions. 2024-12-31 is the last research session once the
   hold-out is frozen (ticket 2.6); a later end is refused then.

4. **Restate the verdict.** Product's step, after reading both, under the gate in force. Not done by any script.

## What this does not settle

- **The size limit.** The risk gate holds any single instrument to about 15% of the account, so even at a lower
  cost the SPY rule moves the account by well under one percent a year. A separate cap for index funds is a level
  limit for the stakeholder (tickets 2.7 and 2.8).
- **Real fills.** The measurement is of quotes. A real marketable order can pay less (price improvement) or more
  (a fast market). The bucket is the quoted half-spread, and v2's impact term still applies on top.
- **The 15:50 re-run.** The ticket also asks for one re-run of a daily rule that prices near-close orders at the
  15:50 price. That run goes through the research runner in `src/kaibot/engine/`, which other teams are changing
  now; it is not in this pull request.
- **The Databento route.** Spending the one-time credit stays with the stakeholder. This ticket uses free data.
