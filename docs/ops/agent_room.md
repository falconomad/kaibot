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
