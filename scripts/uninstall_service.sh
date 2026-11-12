#!/usr/bin/env bash
# Remove the always-on launchd agents (issue #121). Stops the engine cleanly first (as Ctrl-C would).
#   ./scripts/uninstall_service.sh
# Keeps logs (~/Library/Logs/kaibot), backups, data/ and the release pin. Afterwards start by hand as before:
# ./scripts/run_engine.sh. The watchdog of a Python-mode run keeps running, as after Ctrl-C; stop it with
# kill "$(cat data/live/watchdog.pid)" (Docker mode: docker stop kaibot-watchdog).
set -uo pipefail
cd "$(dirname "$0")/.."
AGENTS="${KAIBOT_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
DOMAIN="gui/$(id -u)"
if launchctl print "$DOMAIN/com.kaibot.engine" >/dev/null 2>&1; then
  echo "Stopping the engine cleanly…"
  launchctl kill SIGINT "$DOMAIN/com.kaibot.engine" 2>/dev/null || true
  for _ in $(seq 75); do
    launchctl print "$DOMAIN/com.kaibot.engine" 2>/dev/null | grep -q 'state = running' || break; sleep 1
  done
fi
for label in com.kaibot.engine com.kaibot.daily; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null && echo "removed $label" || echo "$label was not loaded"
  rm -f "$AGENTS/$label.plist"
done
rm -f data/live/service.pid data/live/service.note
if ./scripts/engine_guard.sh --list | grep -q .; then echo "Still running:"; ./scripts/engine_guard.sh --list; fi
echo "Done. Start by hand with ./scripts/run_engine.sh, or reinstall with ./scripts/install_service.sh."
