"""The one trial registry (issue #126, audit F5): count it, or copy it into the database.

python scripts/trial_registry.py count [--data data] [--env .env]
    Configurations tried so far: the number the walk-forward scripts, the trial report and both gates deflate
    by. Without --env only the file is read; with --env the database tables are merged in (a run that is in the
    file and in a table counts once).

python scripts/trial_registry.py push --env ~/Projects/kaibot-secrets/.env [--data data] [--yes]
    Copy the trials that kaibot.trial_registry does not have yet from data/trials.jsonl into it, so an engine
    in a container or in the cloud reads the same registry without a mounted file. Needs migration 0015
    (applied by the stakeholder). Insert only; without --yes it only says how many rows it would add.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import trials  # noqa: E402


def make_store(env_file: str):
    """The engine's database client from a .env file (the key is read, never printed)."""
    from kaibot.engine.config import DEFAULT_SUPABASE_URL
    from kaibot.engine.store import Store

    env = {}
    for line in Path(env_file).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    env = {**env, **os.environ}
    key = env.get("SUPABASE_SERVICE_KEY") or env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        sys.exit(f"no SUPABASE_SERVICE_KEY in {env_file}")
    return Store(env.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/"), key)


def main(argv: list[str] | None = None, store_factory=make_store) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["count", "push"])
    ap.add_argument("--data", default=str(ROOT / "data"), help="folder with trials.jsonl")
    ap.add_argument("--env", default="", help=".env with the Supabase service key (count: optional)")
    ap.add_argument("--yes", action="store_true", help="push: really insert the rows")
    a = ap.parse_args(argv)
    f = Path(a.data) / "trials.jsonl"
    if a.command == "count":
        out = trials.load(store_factory(a.env) if a.env else None, f).summary()
    else:
        if not a.env:
            sys.exit("push needs --env (the database to copy into)")
        rows, unreadable = trials.read_jsonl(f)
        store = store_factory(a.env)
        try:
            have = {r.get("trial_id") for r in trials._select_all(store, trials.TABLE, "id,trial_id")}
        except Exception as e:  # noqa: BLE001
            sys.exit(f"kaibot.{trials.TABLE} cannot be read ({type(e).__name__}): has migration 0015 been applied?")
        new = [r for r in trials.merge(rows) if r["trial_id"] not in have]
        out = {"file": str(f), "trials_in_file": len(trials.merge(rows)), "unreadable_lines": unreadable,
               "already_in_database": len(have), "to_add": len(new), "added": 0}
        if a.yes:
            out["added"] = trials.push(store, rows)
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
