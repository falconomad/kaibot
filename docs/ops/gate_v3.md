# Evidence gate v3: how to use it (decision 16)

The marks and the reasons are in `docs/decisions/16_gate_v3.md`. The numbers are in `config/gate_v3.yaml`. This
page is the how-to.

## Before a rule's first run

1. Write the pre-registration (`docs/prereg/<rule>.md`) as before, and add the gate v3 declarations: role
   (sleeve or book), kind (selection or timing), family, and the candidate pool the control draws from.
2. In the same pull request add the rule to its family in `config/gate_v3.yaml` with `v3_declared: true`, or add
   a new family. A new family raises the count that every later verdict is corrected for.
3. Run it on the research window (`--end 2024-12-31`). A later end is refused (`docs/ops/holdout.md`).

## Scoring

```python
from kaibot.evaluation import gate_v3 as g3
gate = g3.GateV3.load()
report = g3.evaluate_sleeve(gate, rule="my_rule", account=g3.Account(net, spy, exposure, costs, control,
                            "point_in_time"), neighbours={...}, prereg=..., source={...}, registry=..., tbill=...)
print(g3.render_sleeve(report))
```

- `net`, `exposure`, `costs`: the account's daily file (`ret` = `pnl / start`, `exposure`, `cost / start`).
- `control`: `random_control.matched_random_control(..., pool="candidate_pool")` with `eligible` set to the
  rule's own candidate pool. It now also returns `p_value` and `drawdown_percentile`.
- `tbill`: `kaibot.evaluation.tbill.find(data)` (ticket 2.4).
- A book: `g3.evaluate_book(gate, book=..., account=..., neighbours=..., sleeves={"rule": "pass", ...})`.
- The final check, after an opening of the hold-out: `g3.holdout_check(...)`.

## Re-scoring what already exists (no rule is re-run)

On the research Mac, where `data/reports` is:

```
cd ~/Projects/kaibot && git pull
.venv/bin/python scripts/fetch_tbill.py --env ~/Projects/kaibot-secrets/.env    # once; without it cash is at 0%
.venv/bin/python scripts/gate_v3_rescore.py
```

It reads the report files, the daily CSVs, `data/trials.jsonl` and the bill-rate file. It writes
`docs/research/results/gate_v3_rescore.md` and `.json` and prints the table. It runs no simulator, reads no
price bar and logs no trial. Sessions in the hold-out are dropped before any number is computed.

How to read the table:

- `provisional` is the best a re-score can show: every mark that could be scored holds, and the rule's
  pre-registration predates decision 16.
- `?` is a mark the existing report cannot support. The reason is listed under the table. The common ones: the
  control drew from the whole universe for an event rule; fewer than 500 control runs; no exposure column.
- A run listed under "could not be scored" has no daily file, or no SPY series covers its sessions.

## What gate v3 does not replace

Gate C (paper incubation) and Gate D are still read from `config/gate_v2.yaml`. Gate v2's verdicts stand. The M4
gate still judges intraday rules.
