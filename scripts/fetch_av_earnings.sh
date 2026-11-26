#!/usr/bin/env bash
# Alpha Vantage EARNINGS backfill, free tier only, research only (decision 20, issue #797; docs/ops/alphavantage.md).
#   ./scripts/fetch_av_earnings.sh                  one daily batch: the upcoming-earnings calendar (1 request, display
#                                                   only), then at most 19 EARNINGS requests, 13 s apart (~5 minutes)
#   ./scripts/fetch_av_earnings.sh --status         progress and today's request count (offline)
#   ./scripts/fetch_av_earnings.sh --check-key      is ALPHAVANTAGE_API_KEY configured? (never shows it)
#   ./scripts/fetch_av_earnings.sh --install-daily  run the batch every night at 02:00 local time (launchd)
# Key: ALPHAVANTAGE_API_KEY in ~/Projects/kaibot-secrets/data.env. No key: a warning, exit 0.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"

PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Needs Python 3.12+:  brew install python@3.13"; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-engine.txt  # locked (#122)
export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"  # macOS python.org builds lack root certs
mkdir -p logs data/earnings/alphavantage

case "${1:-}" in
  --status|--check-key) exec .venv/bin/python scripts/fetch_av_earnings.py --env "$ENV_FILE" "$1" ;;
  --install-daily)
    PLIST="$HOME/Library/LaunchAgents/com.kaibot.av-earnings.plist"
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.kaibot.av-earnings</string>
  <key>ProgramArguments</key><array><string>$PWD/scripts/fetch_av_earnings.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>KAIBOT_ENV_FILE</key><string>$ENV_FILE</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>StandardOutPath</key><string>$PWD/logs/av_earnings.log</string>
  <key>StandardErrorPath</key><string>$PWD/logs/av_earnings.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Scheduled: the Alpha Vantage batch runs every night at 02:00 local time (log: logs/av_earnings.log)."
    exit 0 ;;
  "") ;;
  *) echo "Usage: ./scripts/fetch_av_earnings.sh [--status|--check-key|--install-daily]"; exit 2 ;;
esac
exec .venv/bin/python scripts/fetch_av_earnings.py --env "$ENV_FILE"
