#!/usr/bin/env bash
# Spin-off share ratios from the parents' SEC 8-K filings (issue #298). Shadow / research data only: the engine does
# not read it. One row per spin-off -> data/spinoffs/spinoffs.parquet, with the EDGAR acceptance time of the filing
# that first stated the ratio. Resumable: stop with Ctrl-C and run it again, filings already read are skipped.
#   ./scripts/fetch_spinoffs.sh               the spin-offs in the daily store's corporate-actions table
#   ./scripts/fetch_spinoffs.sh --check262    the spin-offs named in #262, then the check against Alpaca
#   ./scripts/fetch_spinoffs.sh --refresh     also re-read the parents' filing lists
#   ./scripts/fetch_spinoffs.sh --build       offline: rebuild from what is cached
#   ./scripts/fetch_spinoffs.sh --status      what is stored (offline)
# SEC fair access is 10 requests/s with a declared User-Agent; this uses 5/s and SEC_USER_AGENT from the .env.
# Do not run it together with fetch_earnings_dates.sh, fetch_fundamentals.py or collect_alt.py (each process has
# its own SEC budget). How to read the result: docs/ops/spinoff_ratios.md
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"

PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Needs Python 3.12+:  brew install python@3.13"; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-engine.txt  # locked (uv.lock export, #122)
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"  # macOS python.org builds lack root certs
mkdir -p logs data/spinoffs

case "${1:-}" in
  --status) exec .venv/bin/python scripts/fetch_spinoffs.py --status ;;
  --build) exec .venv/bin/python scripts/fetch_spinoffs.py --build ${2:+"$2"} ;;
  --check262|--refresh|"") ;;
  *) echo "Usage: ./scripts/fetch_spinoffs.sh [--check262|--refresh|--build [--check262]|--status]"; exit 2 ;;
esac

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
grep -q "^SEC_USER_AGENT=" "$ENV_FILE" || { echo "Missing SEC_USER_AGENT in $ENV_FILE ('Name email@domain')"; exit 1; }

trap 'echo; echo "Stopped. Run again to resume."; exit 130' INT TERM
.venv/bin/python scripts/fetch_spinoffs.py --env "$ENV_FILE" ${1:+"$1"} 2>&1 | tee logs/fetch_spinoffs.log
