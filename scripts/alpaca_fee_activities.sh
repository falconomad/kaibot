#!/usr/bin/env bash
# Show the FEE activities of the Alpaca PAPER account, last 90 days (issue #238). Read-only: one kind of GET.
#   ./scripts/alpaca_fee_activities.sh
# Reads the engine's .env (KAIBOT_ENV_FILE, default ~/Projects/kaibot-secrets/.env); never prints a key.
set -euo pipefail
cd "$(dirname "$0")/.."
export KAIBOT_ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
[ -f "$KAIBOT_ENV_FILE" ] || { echo "No .env found at $KAIBOT_ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3.13 || command -v python3.12 || command -v python3)"
if [ -x .venv/bin/python ]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi 2>/dev/null || true)}"
fi
exec "$PY" scripts/alpaca_fee_activities.py
