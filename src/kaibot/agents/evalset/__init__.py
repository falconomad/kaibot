"""Offline evaluation set for the agent room (issue #82). Nothing here is imported by the engine.

- cases.py   the versioned case format (JSONL), the bundled SYNTHETIC set and the training-cutoff filter
- stub.py    a deterministic stand-in for the model (no network), used by default and in CI
- runner.py  replays the real Room over a set and reports veto/downgrade rates, agreement, violations and cost
- export.py  builds real cases from the Supabase room_session / signal / trade tables (documented, run by hand)

How to run it: docs/ops/agent_room.md, "Offline evaluation".
"""

from kaibot.agents.evalset.cases import (
    FORMAT_VERSION,
    SYNTHETIC_SET,
    EvalCase,
    after_cutoff,
    dump_cases,
    load_cases,
)
from kaibot.agents.evalset.runner import contract_violations, replay, report

__all__ = ["FORMAT_VERSION", "SYNTHETIC_SET", "EvalCase", "after_cutoff", "contract_violations", "dump_cases",
           "load_cases", "replay", "report"]
