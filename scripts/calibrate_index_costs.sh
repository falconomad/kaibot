#!/usr/bin/env bash
# Measure an index fund's quoted spread at the moments it is traded, for cost model v2's index-fund bucket
# (M11 ticket 2.3, issue #454). Read-only: GET requests to the PAPER account's activities and to market-data quotes.
#   ./scripts/calibrate_index_costs.sh                 the paper account's SPY fills against the quotes
#   ./scripts/calibrate_index_costs.sh --quotes-only   quotes at the rule's check times, no fills needed
# Reads the engine's .env (KAIBOT_ENV_FILE, default ~/Projects/kaibot-secrets/.env); never prints a key.
# Writes data/reports/index_cost_calibration.md and .json and prints the table. A few hundred requests at most,
# paced at 40 a minute (about seven minutes for the quote sample), so it can run while the engine runs.
set -euo pipefail
cd "$(dirname "$0")/.."
export KAIBOT_ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
[ -f "$KAIBOT_ENV_FILE" ] || { echo "No .env found at $KAIBOT_ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3.13 || command -v python3.12 || command -v python3)"
if [ -x .venv/bin/python ]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi 2>/dev/null || true)}"
fi
exec "$PY" scripts/calibrate_index_costs.py "$@"
