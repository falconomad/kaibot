#!/usr/bin/env bash
# Earnings announcement dates from SEC EDGAR (story S85, issue #125). Research data only; the engine does not read it.
# For every company in the S&P 500 since 2016 (current and former members): its 8-K filings with Item 2.02 since
# 2016 and their EDGAR acceptance times -> data/earnings/announcements.parquet, then today's forward-calendar
# snapshot and the coverage report. Resumable: stop with Ctrl-C and run it again, finished companies are skipped.
#   ./scripts/fetch_earnings_dates.sh                  first run: about 10-15 minutes, about 2,000 SEC requests
#   ./scripts/fetch_earnings_dates.sh --refresh        later: re-read every company's newest filings (~3 minutes)
#   ./scripts/fetch_earnings_dates.sh --snapshot       only today's forward-calendar snapshot (10 Finnhub requests)
#   ./scripts/fetch_earnings_dates.sh --install-daily  schedule --snapshot every night at 01:10 local time (launchd)
#   ./scripts/fetch_earnings_dates.sh --status         what is stored (offline)
# SEC fair access is 10 requests/s with a declared User-Agent; this uses 5/s and SEC_USER_AGENT from the .env.
# How to read the result and how the research harness uses it: docs/ops/earnings_dates.md
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
mkdir -p logs data/earnings

case "${1:-}" in
  --status) exec .venv/bin/python scripts/fetch_earnings_dates.py --status ;;
  --install-daily)
    PLIST="$HOME/Library/LaunchAgents/com.kaibot.earnings-calendar.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.kaibot.earnings-calendar</string>
  <key>ProgramArguments</key><array>
    <string>$PWD/scripts/fetch_earnings_dates.sh</string><string>--snapshot</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>KAIBOT_ENV_FILE</key><string>$ENV_FILE</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>1</integer><key>Minute</key><integer>10</integer></dict>
  <key>StandardOutPath</key><string>$PWD/logs/earnings_calendar.log</string>
  <key>StandardErrorPath</key><string>$PWD/logs/earnings_calendar.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Scheduled: the forward-calendar snapshot runs every night at 01:10 (log: logs/earnings_calendar.log)."
    exit 0 ;;
  --snapshot|--refresh|"") ;;
  *) echo "Usage: ./scripts/fetch_earnings_dates.sh [--refresh|--snapshot|--install-daily|--status]"; exit 2 ;;
esac

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
grep -q "^SEC_USER_AGENT=" "$ENV_FILE" || { echo "Missing SEC_USER_AGENT in $ENV_FILE ('Name email@domain')"; exit 1; }

if [ "${1:-}" = "--snapshot" ]; then
  exec .venv/bin/python scripts/fetch_earnings_dates.py --env "$ENV_FILE" --snapshot
fi

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
trap 'echo; echo "Stopped. Run again to resume."; exit 130' INT TERM
awake .venv/bin/python scripts/fetch_earnings_dates.py --env "$ENV_FILE" ${1:+"$1"} 2>&1 | tee logs/fetch_earnings_dates.log
