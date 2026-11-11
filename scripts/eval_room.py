"""Replay the agent room over an offline evaluation set (issue #82).

Reports veto rate, downgrade rate, agreement with the outcome labels, contract violations (must be zero: the room
may only veto or halve) and cost per decision. Exit code 1 if there is any contract violation.

Default: a deterministic stub model, no network, no key, no real budget touched. The cost is then an estimate
from the prompt size at the prices in config/llm.yaml.

    python scripts/eval_room.py                                  # bundled synthetic set, stub model
    python scripts/eval_room.py --set data/room_eval/real_v1.jsonl --cutoff 2026-08-31
    python scripts/eval_room.py --live --max-usd 0.50 --cutoff 2026-08-31 --env ~/Projects/kaibot-secrets/.env

--live calls the real model through the LLM budget gateway (agents/gateway.py): every call is reserved and
settled in Postgres against the same daily and monthly caps as the engine. It needs ANTHROPIC_API_KEY and the
Supabase service key in the env file, spends real money, and stops at --max-usd. Run it outside market hours so
it cannot use up the engine's day. It does not need or change KAIBOT_ROOM: the room in the engine stays off.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.agents.evalset import SYNTHETIC_SET, after_cutoff, load_cases, replay, report  # noqa: E402
from kaibot.agents.evalset.runner import format_report, offline_gateway  # noqa: E402


def live_gateway(env_file: str | None):
    """The real gateway: real client, real Postgres budget. Only reached with --live."""
    from kaibot.agents.gateway import AnthropicClient, Gateway
    from kaibot.engine.config import DEFAULT_SUPABASE_URL, load_env_file
    from kaibot.engine.store import Store

    load_env_file(env_file)
    key = os.environ.get("ANTHROPIC_API_KEY")
    sb = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key or not sb:
        raise SystemExit("--live needs ANTHROPIC_API_KEY and SUPABASE_SERVICE_KEY in the env file (see --env)")
    return Gateway(Store(os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/"), sb), AnthropicClient(key))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", default=str(SYNTHETIC_SET), help="evaluation set (JSONL); default: bundled synthetic")
    ap.add_argument("--cutoff", type=date.fromisoformat, default=None,
                    help="training cutoff of the model (YYYY-MM-DD): real cases decided on or before it are dropped")
    ap.add_argument("--margin-days", type=int, default=0, help="extra days after the cutoff to drop as well")
    ap.add_argument("--limit", type=int, default=None, help="only the first N cases")
    ap.add_argument("--live", action="store_true", help="call the real model through the budget gateway (costs money)")
    ap.add_argument("--max-usd", type=float, default=1.0, help="--live only: stop once this much is spent")
    ap.add_argument("--env", default=None, help="--live only: env file with ANTHROPIC_API_KEY and SUPABASE_*")
    ap.add_argument("--json", default=None, help="also write the full report to this file")
    a = ap.parse_args(argv)

    cases = load_cases(a.set)
    dropped = []
    if a.cutoff is not None:
        cases, dropped = after_cutoff(cases, a.cutoff, a.margin_days)
    elif a.live and any(not c.synthetic for c in cases):
        raise SystemExit("--live on real cases needs --cutoff: the model must not have seen how they ended")
    cases = cases[: a.limit] if a.limit else cases
    if not cases:
        raise SystemExit("no cases left to run")

    if a.live:
        results, not_run = replay(cases, live_gateway(a.env), max_usd=a.max_usd)
    else:
        results, not_run = replay(cases, offline_gateway())
    rep = report(results, "actual, settled in the LLM budget" if a.live else "stub estimate at config/llm.yaml prices")
    rep |= {"set": str(a.set), "mode": "live" if a.live else "stub", "dropped_by_cutoff": len(dropped),
            "not_run": len(not_run), "cutoff": a.cutoff.isoformat() if a.cutoff else None}
    print(f"set   {a.set}\nmode  {rep['mode']}" + (f"   cutoff {a.cutoff} (+{a.margin_days} d): "
                                                  f"{len(dropped)} real cases dropped" if a.cutoff else ""))
    if not_run:
        print(f"STOPPED EARLY: {len(not_run)} cases not run (--max-usd reached or the LLM budget is not ok)")
    print(format_report(rep))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1))
    return 1 if rep["contract_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
