# Working on Kaibot

- Paper trading only. Never add live broker keys, live base URLs or code paths that bypass `BrokerAdapter`.
- No LLM output may set price, size, stop or exit. Agents return `agents/contracts.py` models and can only veto/downgrade.
- `risk/gate.py` is pure and property-tested. Any change to it, sizing or the broker needs tests first.
- Profiles may only tighten `HARD_CEILINGS`. Every strategy/parameter change runs a replay that logs a `trial` row.
- Features read only rows with `available_ts <= decision_ts` (point-in-time). SIP data only when >= 15 min old.
- Respect `config/sources.yaml` budgets; free tiers only; never scrape X, Reddit or Yahoo in the live path.
- The engine (`python -m kaibot.engine`, `scripts/run_engine.sh`) runs on the user's Mac for now and must stay portable:
  env-only settings, outgoing connections only, same Docker image anywhere (decision 11). Never run two engines at once.
- Run `ruff check .` and `pytest -q` before committing. Deploy only outside 09:30-16:00 ET.
- Decisions live in `docs/decisions/`; the backlog in GitHub issues (milestones M0-M5, labels team:* and epic:*).
- Architecture board (Whiteboard, slug `b099f04b-1c74-46fe-b635-7b9b30348e4f`, https://claude.ai/artifact/NoqNs89Sbz3h5UPRQUZNFY):
  when a change adds, removes or renames an engine step in `src/kaibot/engine/` or `risk/gate.py`, or a milestone's status changes,
  update the matching blocks and the "Synced with main @ <sha>" line. Read the board first and edit only what changed.
- Teams and delegation: `docs/teams.md`. The stakeholder talks to Product only; Product delegates with `/delegate <issue>`
  to the team agents in `.claude/agents/`. Stay inside your team's folders.
