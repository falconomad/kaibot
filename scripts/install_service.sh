#!/usr/bin/env bash
# Always-on engine on the Mac (issue #121): installs two launchd agents in ~/Library/LaunchAgents.
#   ./scripts/install_service.sh              install (Docker if Docker Desktop runs now, otherwise Python)
#   ./scripts/install_service.sh --docker | --python   choose the way, as with run_engine.sh
#   ./scripts/install_service.sh --dry-run --out DIR    write the plists to DIR only (no launchctl; works on Linux)
# com.kaibot.engine  starts at login and every weekday at 15:00 (Mac time); the engine itself waits for the
#                    broker's session. Restarted about a minute after a crash. Keeps the Mac awake (caffeinate).
# com.kaibot.daily   03:15 Tuesday-Saturday: rotate logs, back up local files, refresh the daily bar store; again
#                    at 07:45 (a catch-up: only the refresh, which skips what it has). Mac time (Europe/Berlin):
#                    03:15 is 21:15 New York (22:15 in the weeks the two clocks change on different dates), after
#                    Alpaca's daily bar is complete (20:15 ET), and 07:45 is still hours before the open (#206).
# Logs: ~/Library/Logs/kaibot/. Remove with ./scripts/uninstall_service.sh. Runbook: docs/ops/always_on.md.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
MODE=auto; DRY=0; OUT="$HOME/Library/LaunchAgents"
while [ $# -gt 0 ]; do
  case "$1" in
    --docker) MODE=docker;; --python) MODE=python;;
    --dry-run) DRY=1;; --out) OUT="$2"; shift;;
    *) sed -n '2,9p' "$0"; exit 2;;
  esac; shift
done
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
LOG_DIR="$HOME/Library/Logs/kaibot"
ENGINE_HOUR="${KAIBOT_START_HOUR:-15}"  # Mac local time; 15:00 Berlin = 09:00 New York (08:00 in two weeks a year)

if [ "$DRY" = 0 ]; then
  [ "$(uname -s)" = Darwin ] || { echo "launchd is macOS only. On Linux use: docker compose up -d (docs/ops/run_engine.md)"; exit 1; }
  [ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
fi
if [ "$MODE" = auto ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then MODE=docker; else MODE=python; fi
fi

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }  # bash 3.2 and 5.2 alike
# launchd starts jobs with a bare PATH: keep today's, plus the usual places of docker, brew and python.org Python.
SVC_PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"

env_block() {
  cat <<EOF
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key><string>$(xml "$SVC_PATH")</string>
		<key>HOME</key><string>$(xml "$HOME")</string>
		<key>KAIBOT_ENV_FILE</key><string>$(xml "$ENV_FILE")</string>
		<key>KAIBOT_LOG_DIR</key><string>$(xml "$LOG_DIR")</string>
		<key>KAIBOT_SERVICE_MODE</key><string>$MODE</string>
	</dict>
EOF
}

engine_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Kaibot engine (issue #121). Written by scripts/install_service.sh; edit that script, not this file. -->
<plist version="1.0">
<dict>
	<key>Label</key><string>com.kaibot.engine</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$(xml "$REPO/scripts/service_run.sh")</string>
		<string>engine</string>
	</array>
	<key>WorkingDirectory</key><string>$(xml "$REPO")</string>
$(env_block)
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key><false/>
	</dict>
	<key>ThrottleInterval</key><integer>60</integer>
	<key>StartCalendarInterval</key>
	<array>
$(for d in 1 2 3 4 5; do printf '\t\t<dict><key>Weekday</key><integer>%d</integer><key>Hour</key><integer>%d</integer><key>Minute</key><integer>0</integer></dict>\n' "$d" "$ENGINE_HOUR"; done)
	</array>
	<key>AbandonProcessGroup</key><true/>
	<key>ExitTimeOut</key><integer>90</integer>
	<key>ProcessType</key><string>Standard</string>
	<key>StandardOutPath</key><string>$(xml "$LOG_DIR/engine.log")</string>
	<key>StandardErrorPath</key><string>$(xml "$LOG_DIR/engine.log")</string>
</dict>
</plist>
EOF
}

daily_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- Kaibot nightly job (issue #121). Written by scripts/install_service.sh; edit that script, not this file. -->
<plist version="1.0">
<dict>
	<key>Label</key><string>com.kaibot.daily</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$(xml "$REPO/scripts/service_run.sh")</string>
		<string>daily</string>
	</array>
	<key>WorkingDirectory</key><string>$(xml "$REPO")</string>
$(env_block)
	<key>RunAtLoad</key><false/>
	<key>StartCalendarInterval</key>
	<array>
$(for d in 2 3 4 5 6; do for hm in "3 15" "7 45"; do set -- $hm; printf '\t\t<dict><key>Weekday</key><integer>%d</integer><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>\n' "$d" "$1" "$2"; done; done)
	</array>
	<key>LowPriorityIO</key><true/>
	<key>Nice</key><integer>5</integer>
	<key>ProcessType</key><string>Background</string>
	<key>StandardOutPath</key><string>$(xml "$LOG_DIR/daily.log")</string>
	<key>StandardErrorPath</key><string>$(xml "$LOG_DIR/daily.log")</string>
</dict>
</plist>
EOF
}

mkdir -p "$OUT"
engine_plist > "$OUT/com.kaibot.engine.plist"
daily_plist > "$OUT/com.kaibot.daily.plist"
if [ "$DRY" = 1 ]; then echo "Wrote $OUT/com.kaibot.engine.plist and $OUT/com.kaibot.daily.plist (mode $MODE)."; exit 0; fi

mkdir -p "$LOG_DIR"
chmod +x scripts/*.sh
for p in "$OUT/com.kaibot.engine.plist" "$OUT/com.kaibot.daily.plist"; do plutil -lint -s "$p"; done
if ./scripts/engine_guard.sh --list | grep -q .; then
  echo "Note: an engine started by hand is running. The service stands by and takes over within a minute of it"
  echo "stopping (no second engine). To switch now: stop it (Ctrl-C), the service starts right after."
fi
DOMAIN="gui/$(id -u)"
for label in com.kaibot.daily com.kaibot.engine; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$OUT/$label.plist"
done
grep -q '^HEALTHCHECKS_PING_URL=.' "$ENV_FILE" && ALARMS=on || ALARMS="off (no HEALTHCHECKS_PING_URL in $ENV_FILE)"
cat <<EOF
Installed (mode $MODE). The engine starts now and at every login, and every weekday at ${ENGINE_HOUR}:00.
  status:   ./scripts/service.sh status
  logs:     ./scripts/service.sh logs      ($LOG_DIR)
  stop:     ./scripts/service.sh stop      (for today; hold = until you say so)
  release:  $(bash scripts/release.sh --show | head -n1)
  alarms:   $ALARMS
For an unattended Mac mini: System Settings > Energy: "Start up automatically after a power failure" on, and
Users & Groups: automatic login for this user (launch agents run only while you are logged in).
EOF
