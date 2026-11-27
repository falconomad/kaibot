#!/usr/bin/env python
"""Does the daily store hold the point-in-time keys the live daily path reads (#812)? Read-only.

    ./scripts/check_pit_store.sh                 # the store at $KAIBOT_DAILY_DATA (default data/)
    .venv/bin/python scripts/check_pit_store.py --data data --json

Loads the view as engine/live_daily.StoreLoader does (mode "pit", the fixed keys of sp500_pit.STORE_DAY) for the
store's last session and prints companies/keys expected, matched, missing (up to 20) and a last line
"OK: live path would trade N stocks" or "FAIL: ...". No network, no writes, no broker.
Exit 0: OK; 1: FAIL. Logic: src/kaibot/ingest/pit_store_check.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.common import switches  # noqa: E402
from kaibot.ingest import pit_store_check as psc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="", help="the daily store's root (default: KAIBOT_DAILY_DATA, else data)")
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--json", action="store_true", help="print the report as JSON before the verdict")
    a = ap.parse_args(argv)
    root = Path(a.data) if a.data else Path(switches.get("KAIBOT_DAILY_DATA") or ROOT / "data")
    if not root.is_absolute():
        root = ROOT / root
    rep = psc.check(root, Path(a.universe_file))
    if a.json:
        print(json.dumps(rep, indent=1, default=str))
    print(psc.render(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
