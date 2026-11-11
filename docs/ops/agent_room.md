# Agent room (M3: stories S32–S37)

The room reviews dip-reversal candidates that the deterministic rules already approved, and can only **allow**,
**downgrade** (half size) or **veto** them. Price, size, stop and exits always come from code. SPY momentum never
uses it.

## Turning it on
In `~/Projects/kaibot-secrets/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...   # never in chat or the repo
KAIBOT_ROOM=on
```
Restart the engine. The log says `agent room: ON` or `off (rules-only)`. Without both settings it stays off and
the engine is exactly the rules-only M1 system.

## What one candidate costs
- Proposer and Critic in parallel, then the Judge (Sonnet 5.5): one round, ≤ 45 s, typically about $0.03–0.07.
- News triage (Haiku 5.5): one call per deduplicated story cluster, a fraction of a cent.

## Budget (config/llm.yaml)
| Cap | What happens |
|---|---|
| $3 / day | the room is skipped; the rules decide alone (`room_session.status = skipped`) |
| $150 / month | alert on Engine health |
| $190 / month | no new entries (`halt: llm_budget`); exits continue |

Every call reserves its worst case in Postgres first (`kaibot.llm_reserve`, under a row lock), so the engine,
the triage job and any other process can never overspend together. The actual cost settles afterwards, even on
failure.

## Fail closed
A timeout, invalid output, a citation not in the evidence pack, an API error or a crash means **no trade** for
that candidate. A candidate with no verdict after two 5-minute cycles is dropped. `admissible()` vetoes company-news
and unknown causes whatever the Judge says.

## The A/B (S36)
Every session stores the rules-only arm (the rules alone would have traded it) and, at day end, the
counterfactual: the same trade at full size with the replay's fill and exit model. The Report page compares
what the room vetoed and downgraded with what it let through. The room keeps its place only if its vetoes
avoid losses that pay for its cost.

## Offline evaluation (issue #82)
A fixed set of past signals, each with the evidence pack the room would see, the decision date and what the
rules-only trade went on to do. It replays the real room (`agents/room.py`) outside the engine; it never trades
and does not need `KAIBOT_ROOM`.

```
python scripts/eval_room.py                      # 26 synthetic cases, stub model: no network, no key, no cost
python scripts/eval_room.py --json out.json      # the same, with the per-case report written to a file
```
It prints the veto rate, downgrade rate, allow rate, sessions that failed closed, agreement with the outcome
labels, **contract violations** and the cost per decision, then a table per category. Any contract violation
(a size above 1x, a trade on a company-news or unknown cause, a verdict from a failed session, price or size
fields in the output, evidence that is not in the pack) makes the script exit 1. The count must be zero: the room
may only veto or halve.

- **The set** is `src/kaibot/agents/evalset/synthetic_v1.jsonl`, format version 1 (described in
  `evalset/cases.py`). Every case is marked `"synthetic": true`: the tickers (`SYN01`…), headlines and outcomes
  are invented. They cover a clear veto, a clear pass, a downgrade, missing evidence, stale evidence and pump risk.
- **The stub model** (`evalset/stub.py`) applies the room's written rules mechanically, so the same run gives
  the same numbers every time. It checks the plumbing and the contract, and estimates cost from the prompt size
  at the prices in `config/llm.yaml`. It says nothing about how good the real model is. CI runs only this.
- **Agreement**: blocking a loser or allowing a winner counts 1, the opposite 0, a downgrade 0.5. "Lift" is the
  R saved or given up against trading every case at full size.

### Real cases, later
`kaibot.agents.evalset.export.export_real_cases(store, cutoff)` builds cases from `room_session` (the stored
pack and the day-end counterfactual), `signal` and `trade`; the query is in the module's docstring. It only
reads, and nothing runs it automatically. It keeps only decisions dated **after the model's training cutoff**
(pass the cutoff of `models.room` from Anthropic's model documentation, and a `margin_days` if in doubt), so the
model cannot have memorised how a case ended. Write the result outside the repo, for example
`data/room_eval/real_v1.jsonl`, and run `python scripts/eval_room.py --set <file> --cutoff YYYY-MM-DD`.

### With the real model
```
python scripts/eval_room.py --live --max-usd 0.50 --cutoff YYYY-MM-DD --env ~/Projects/kaibot-secrets/.env
```
Only with `--live`. Every call goes through the budget gateway and is reserved and settled in Postgres against
the same $3/day and $190/month caps as the engine, so run it outside market hours. It stops at `--max-usd`
(default $1) or as soon as the budget state is not `ok`, and says how many cases were not run. On real cases
`--live` refuses to start without `--cutoff`. This spends real money and is the stakeholder's call.
