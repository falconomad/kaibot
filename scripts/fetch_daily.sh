#!/usr/bin/env bash
# Download the daily bar store (story S84, issue #124): RAW daily bars 2016 -> the last complete session for SPY,
# the 11 sector ETFs and the 288 stocks of config/universe_history.yaml, Alpaca's corporate actions (splits and
# dividends), then the offline checks. When data/universe/former_members.csv exists (story S83: `symbol,asof_date`),
# every former index member is downloaded too, as of a date inside its life.
# Resumable: stop with Ctrl-C and run it again, finished symbols are skipped. Keeps the Mac awake while it runs.
#   ./scripts/fetch_daily.sh            download + check: about 7 minutes and 180 MB of transfer for today's 300
#                                       symbols (about 17 minutes while the engine runs); 50 MB on disk
#   ./scripts/fetch_daily.sh --status   show what is stored and exit
#   ./scripts/fetch_daily.sh --check    only the offline checks (no keys, no network, no .env); exit 3 when a bar
#                                       fails a hard integrity check (the live daily path then does not decide)
# Alpaca allows 200 requests/min per account. This uses 120/min, or 40/min while the engine is running.
# How to read the result and how the research harness uses the store: docs/ops/daily_bars.md
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
FORMER="${KAIBOT_FORMER_MEMBERS:-data/universe/former_members.csv}"

PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Needs Python 3.12+:  brew install python@3.13"; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-engine.txt  # locked (uv.lock export, #122)
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"  # macOS python.org builds lack root certs

ARGS=()
[ -f "$FORMER" ] && ARGS+=(--symbols-file "$FORMER")

case "${1:-}" in
  --status) exec .venv/bin/python scripts/fetch_daily.py --status ${ARGS[@]+"${ARGS[@]}"} ;;
  --check)  exec .venv/bin/python scripts/fetch_daily.py --check ;;
  "") ;;
  *) echo "Usage: ./scripts/fetch_daily.sh [--status|--check]"; exit 2 ;;
esac

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
grep -q "^ALPACA_API_KEY=" "$ENV_FILE" || { echo "Missing ALPACA_API_KEY in $ENV_FILE"; exit 1; }
grep -Eq "^(ALPACA_SECRET|ALPACA_API_SECRET)=" "$ENV_FILE" || { echo "Missing ALPACA_SECRET in $ENV_FILE"; exit 1; }
PER_MIN=120
if pgrep -f "kaibot.engine" >/dev/null 2>&1; then
  PER_MIN=40; echo "The engine is running: downloading slowly (40 requests/min) to leave it room."
fi
[ -f "$FORMER" ] && echo "Former index members: $FORMER" \
  || echo "No $FORMER yet (story S83): downloading today's universe only. Run this again once it exists."

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
mkdir -p logs data
trap 'echo; echo "Stopped. Run again to resume."; exit 130' INT TERM
awake .venv/bin/python scripts/fetch_daily.py --env "$ENV_FILE" --per-min "$PER_MIN" ${ARGS[@]+"${ARGS[@]}"} \
  2>&1 | tee logs/fetch_daily.log
echo "Checking (offline)…"
rc=0
.venv/bin/python scripts/fetch_daily.py --check 2>&1 | tee logs/fetch_daily_check.log || rc=$?
# The integrity summary also goes into logs/fetch_daily.log (the nightly job's log, scripts/service_run.sh), unless
# this script's output already goes there. Exit 3: a bar fails a hard check and the live daily path will not decide.
SUMMARY=data/daily/check/integrity.txt
if [ -f "$SUMMARY" ] && [ ! logs/fetch_daily.log -ef /dev/stdout ]; then
  { echo "== integrity check $(date '+%Y-%m-%d %H:%M:%S %Z') (exit $rc)"; cat "$SUMMARY"; } >>logs/fetch_daily.log
fi
[ "$rc" -eq 3 ] && echo "The daily store fails the integrity check: no multi-day decision until it is fixed (see $SUMMARY)."
exit "$rc"
