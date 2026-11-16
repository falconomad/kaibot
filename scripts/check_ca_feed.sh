#!/usr/bin/env bash
# Is a spin-off listed under the PARENT's ticker, a merger under the ACQUIRED company's and a symbol change under the
# OLD one in Alpaca's corporate-actions feed? The #271 buy guard depends on it (#266, QA-Ops). Read-only: GET on
# Alpaca's market-data API only; the keys are read from the .env with the engine's loader and never printed.
#   ./scripts/check_ca_feed.sh     prints each case and "guard sees spin-offs by parent: yes/no"
# Exit 0: every case seen as the guard reads it; 3: one is not; 2: keys missing / not paper; 1: feed unreadable.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 2; }
PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Needs Python 3.12+:  brew install python@3.13"; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-engine.txt
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"
exec .venv/bin/python scripts/check_ca_feed.py --env "$ENV_FILE"
