"""Command line of the test-writing agent (M12 3.8, #470). See `kaibot.agents.testwriter`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from kaibot.agents.testwriter import core, guard

FLAG_ENV = "KAIBOT_TESTWRITER"


def _survivors(path: str | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(Path(path).read_text())
    return list(data.get("survivors") or []) if isinstance(data, dict) else list(data)


def _gateway(env_file: str | None):
    """The real gateway (real client, real Postgres budget). Only with KAIBOT_TESTWRITER=on."""
    from kaibot.agents.gateway import AnthropicClient, Gateway
    from kaibot.engine.config import DEFAULT_SUPABASE_URL, load_env_file
    from kaibot.engine.store import Store

    load_env_file(env_file)
    if (os.environ.get(FLAG_ENV) or "").strip().lower() not in ("1", "on", "true", "yes"):
        raise SystemExit(f"the test-writing agent is off: set {FLAG_ENV}=on (it spends API credit)")
    key = os.environ.get("ANTHROPIC_API_KEY")
    sb = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key or not sb:
        raise SystemExit("propose needs ANTHROPIC_API_KEY and SUPABASE_SERVICE_KEY (see --env)")
    return Gateway(Store(os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/"), sb), AnthropicClient(key))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kaibot.agents.testwriter", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose", help="one model call for one target (spends API credit)")
    p.add_argument("target", help="a name in tests/mutation/targets.toml, e.g. ledger")
    p.add_argument("--survivors", help="the mutation run's <target>.json (its survivors and diffs)")
    p.add_argument("--env", help="env file with ANTHROPIC_API_KEY and SUPABASE_*")
    t = sub.add_parser("triage", help="run undecided proposals in the sandbox (no key, no network)")
    t.add_argument("--survivors", help="the mutation run's <target>.json, to check mutant kills")
    sub.add_parser("list", help="proposals waiting for a human")
    d = sub.add_parser("decide", help="a human accepts or rejects a proposal")
    d.add_argument("id")
    d.add_argument("verdict", choices=("accept", "reject"))
    d.add_argument("--by", required=True)
    d.add_argument("--note", default="")
    d.add_argument("--bug", default=None, help="issue of the bug a failing test shows (accept only)")
    r = sub.add_parser("report", help="the month's counts and spend against the cap")
    r.add_argument("--month", default=date.today().strftime("%Y-%m"))
    a = ap.parse_args(argv)

    if a.cmd == "propose":
        gw = _gateway(a.env)
        rows = core.propose(gw, a.target, today=date.today(), survivors=_survivors(a.survivors),
                            env_values=guard.secret_env_values(os.environ))
        for row in rows:
            print(f"{row['event']:9} {row['id']}  {'; '.join(row.get('reasons') or [])[:160]}")
        return 0
    if a.cmd == "triage":
        for row in core.triage(survivors=_survivors(a.survivors), env_values=guard.secret_env_values(os.environ)):
            print(f"{row['result']:16} {row['id']}")
        return 0
    if a.cmd == "list":
        for cid, c in sorted(core.state(core.read_ledger()).items()):
            if c.get("event") == "proposed" and not c.get("decision"):
                print(f"{cid}  {c.get('triage') or 'not triaged':14} {c.get('kind'):8} {c.get('claim', '')[:90]}")
        return 0
    if a.cmd == "decide":
        row = core.decide(a.id, a.verdict, a.by, a.note, bug=a.bug)
        print(f"{row['event']} {row['id']} by {row['by']}")
        return 0
    from kaibot.agents.gateway import LLMConfig

    cap = core.limits(LLMConfig.load()).monthly_usd
    rep = core.monthly(core.read_ledger(), a.month, cap)
    print(json.dumps(rep, indent=1))
    return 0 if rep["under_cap"] else 1


if __name__ == "__main__":
    sys.exit(main())
