#!/usr/bin/env bash
# Does the daily store hold the point-in-time keys the live daily path reads (#812)? Read-only: no network, no
# writes, no broker. Loads the view as the live daily path does and ends with
#   "OK: live path would trade N stocks"  or  "FAIL: live path would stop (no orders) — missing keys" (or why).
#   ./scripts/check_pit_store.sh            the store at $KAIBOT_DAILY_DATA (default data/)
#   ./scripts/check_pit_store.sh --json     the full report first
# Exit 0: OK; 1: FAIL.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "No .venv here: run ./scripts/run_engine.sh --check once (it builds .venv)"; exit 1; }
exec .venv/bin/python scripts/check_pit_store.py "$@"
