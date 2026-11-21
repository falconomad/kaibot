#!/usr/bin/env python3
"""Check the helpers' key path after its one-time setup (M10 ticket 1.26, issue #451; docs/ops/helper_role.md).

    PYTHONPATH=src python3 scripts/helper_check.py            # reads ~/Projects/kaibot-secrets/helper.env

It signs in as the helper and reports, line by line: the sign-in works; the database knows this user as a helper;
the helper's heartbeat write is accepted; a test broadcast on kb:prices is accepted; and three things the helper
must NOT be able to do are refused (insert a command, read the engine lease, write the engine's heartbeat). It
prints no key, password or token. Exit 0 only when every line is as it should be. The broadcast it sends has an
empty price list, which the dashboard ignores."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.common.helper_session import HelperError, HelperSession, helper_env  # noqa: E402

DEFAULT_URL = "https://dosdkdlxejqynbgkjhup.supabase.co"  # the project's public URL, not a secret


def table_call(s: HelperSession, method: str, table: str, body=None) -> int:
    """A direct table request as the helper (what the service key could do). Returns the HTTP status."""
    req = urllib.request.Request(f"{s.url}/rest/v1/{table}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"apikey": s._key, "Authorization": f"Bearer {s._token}",
                                          "Content-Type": "application/json", "Accept-Profile": "kaibot",
                                          "Content-Profile": "kaibot"})
    try:
        with s._open(req, timeout=s.timeout_s) as r:
            r.read(65536)
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def check(s: HelperSession) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def step(name: str, fn, want=lambda v: True) -> None:
        try:
            v = fn()
            out.append((name, bool(want(v)), "ok" if want(v) else f"unexpected answer: {str(v)[:80]}"))
        except HelperError as e:
            out.append((name, False, f"HTTP {e.status}"))

    step("sign-in", s.sign_in)
    if not out[0][1]:
        return out
    step("the database lists this user as a helper", lambda: s.rpc("kb_helper_whoami", {}),
         lambda v: isinstance(v, dict) and v.get("helper") is True)
    step("heartbeat 'price_stream' accepted",
         lambda: s.rpc("kb_helper_heartbeat", {"p_process": "price_stream", "p_detail": {"state": "check"}}))
    step("broadcast on kb:prices accepted", lambda: s.broadcast("kb:prices", "prices", {"feed": "iex", "prices": {}}))
    for name, method, table, body in (
            # QA-Ops on #497: probes that do no harm even if a right were wrongly open: a command kind the table's
            # check refuses and the engine ignores, and a heartbeat row no process reads (never the engine's own)
            ("insert a command: refused", "POST", "command", {"kind": "helper_check_probe", "payload": {}}),
            ("read the engine lease: refused", "GET", "engine_lease", None),
            ("write a heartbeat row directly: refused", "POST", "heartbeat",
             {"process": "helper_check_probe", "ts": "2000-01-01"})):
        code = table_call(s, method, table, body)
        out.append((name, code in (401, 403, 404), f"HTTP {code}"))
    try:
        s.rpc("kb_helper_heartbeat", {"p_process": "engine", "p_detail": {}})
        out.append(("heartbeat 'engine' through the helper function: refused", False, "accepted"))
    except HelperError as e:
        out.append(("heartbeat 'engine' through the helper function: refused", e.status in (400, 403),
                    f"HTTP {e.status}"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--helper-env", default=None)
    a = ap.parse_args(argv)
    env = helper_env(a.helper_env)
    if env is None:
        print("no helpers' env file yet (docs/ops/helper_role.md, step 3)", file=sys.stderr)
        return 2
    rows = check(HelperSession.from_env(env, DEFAULT_URL))
    for name, ok, note in rows:
        print(f"{'OK  ' if ok else 'FAIL'} {name} ({note})")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
