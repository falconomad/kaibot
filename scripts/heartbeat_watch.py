#!/usr/bin/env python3
"""The engine's heartbeat, watched from outside the engine's host (M20 9.5, #729).

`scripts/healthcheck.py` pings from the machine the engine runs on: when that machine is off, asleep or offline, it
sends nothing and the check turns red only by its own silence. This script asks the question from anywhere else: is
the engine's heartbeat row in Supabase fresh? It reads one row (`kaibot.heartbeat`, process `engine`), compares its
`ts` with the clock, and pings a Healthchecks.io check of its own: success when fresh, `/fail` with the age when
stale. A run that cannot read Supabase pings `/fail` too (the watcher cannot vouch for the engine). Nothing here
knows the host: everything is configuration, read from the environment or from the given env file:

    SUPABASE_URL                    the project (default: the engine's, kaibot.engine.config)
    SUPABASE_SERVICE_KEY            a key that may read kaibot.heartbeat (on the VM later: its own secret)
    HEALTHCHECKS_HEARTBEAT_URL      this watcher's own check (not the engine's HEALTHCHECKS_PING_URL)
    HEARTBEAT_STALE_S               stale after this many seconds (default 180, the dead-man's limit)

    heartbeat_watch.py [--env-file FILE] [--process engine] [--dry-run]

One run, one ping; schedule it every minute or five (cron on any second machine, Cloud Scheduler with a Cloud Run
job in January). Exit 0 fresh, 1 stale, 2 unreadable or not configured. The key and the URLs are never printed.
Stdlib only, Python 3.9+. Design and the January plan: docs/ops/monitoring.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime

DEFAULT_SUPABASE_URL = "https://dosdkdlxejqynbgkjhup.supabase.co"  # public project URL (engine/config.py)
KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "HEALTHCHECKS_HEARTBEAT_URL",
        "HEARTBEAT_STALE_S")
STALE_S = 180
TIMEOUT_S = 10


def settings(env_file: str | None) -> dict:
    """The keys above: the process environment first, then only those lines of the env file."""
    got = {}
    if env_file:
        try:
            with open(env_file, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        for line in lines:
            m = re.match(r"^\s*(?:export\s+)?([A-Z_]+)=(.*)$", line)
            if m and m.group(1) in KEYS:
                got[m.group(1)] = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip("'\"")
    for k in KEYS:
        if os.environ.get(k):
            got[k] = os.environ[k]
    return got


def read_heartbeat(base: str, key: str, process: str) -> datetime:
    q = urllib.parse.urlencode({"process": f"eq.{process}", "select": "ts"})
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/heartbeat?{q}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}",
                                          "Accept-Profile": "kaibot", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        rows = json.loads(r.read().decode())
    if not rows:
        raise LookupError(f"no heartbeat row for process {process!r}")
    ts = datetime.fromisoformat(str(rows[0]["ts"]).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def ping(url: str, fail: bool, msg: str) -> None:
    try:
        req = urllib.request.Request(url.rstrip("/") + ("/fail" if fail else ""), data=msg.encode()[:2000],
                                     method="POST")
        urllib.request.urlopen(req, timeout=TIMEOUT_S).close()
    except Exception as e:  # noqa: BLE001 - a ping that cannot go out is said, never raised
        print(f"ping not sent ({type(e).__name__})", file=sys.stderr)


def main(argv=None, now=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file")
    ap.add_argument("--process", default="engine")
    ap.add_argument("--dry-run", action="store_true", help="say what would be pinged; send nothing")
    a = ap.parse_args(argv)
    s = settings(a.env_file)
    url = s.get("HEALTHCHECKS_HEARTBEAT_URL", "")
    key = s.get("SUPABASE_SERVICE_KEY") or s.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    try:
        stale_s = float(s.get("HEARTBEAT_STALE_S") or STALE_S)
    except ValueError:
        print(f"HEARTBEAT_STALE_S is not a number: the default {STALE_S} s is used", file=sys.stderr)
        stale_s = STALE_S
    if not url.startswith(("https://", "http://127.0.0.1:")) and not a.dry_run:  # (the second: the tests' server)
        print("HEALTHCHECKS_HEARTBEAT_URL is not set: nothing to ping", file=sys.stderr)
        return 2
    now = now or datetime.now(UTC)
    code, fail = 0, False
    try:
        if not key:
            raise LookupError("no Supabase key (SUPABASE_SERVICE_KEY)")
        ts = read_heartbeat(s.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL, key, a.process)
        age = (now - ts).total_seconds()
        if age > stale_s:
            code, fail = 1, True
            msg = (f"kaibot heartbeat stale: {a.process} last beat {age:.0f} s ago ({ts.isoformat()}), "
                   f"limit {stale_s:.0f} s")
        else:
            msg = f"kaibot heartbeat fresh: {a.process} {age:.0f} s ago"
    except Exception as e:  # noqa: BLE001 - unreadable is an alarm: the watcher cannot vouch for the engine
        code, fail = 2, True
        msg = f"kaibot heartbeat unreadable: {type(e).__name__}: {str(e)[:200]}"
    print(msg, file=sys.stderr)
    if a.dry_run:
        print(f"dry run: would ping {'fail' if fail else 'success'}", file=sys.stderr)
    else:
        ping(url, fail, msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
