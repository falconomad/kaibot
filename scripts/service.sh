#!/usr/bin/env bash
# Day-to-day control of the always-on engine (issue #121; install first with ./scripts/install_service.sh).
#   ./scripts/service.sh status    is it running, which release, last log lines
#   ./scripts/service.sh stop      stop cleanly for today (starts again next weekday 15:00 or at login)
#   ./scripts/service.sh start     start now
#   ./scripts/service.sh restart   stop cleanly, then start (e.g. after ./scripts/release.sh)
#   ./scripts/service.sh hold      stop and stay stopped until "unhold" (survives reboots and 15:00 starts)
#   ./scripts/service.sh unhold    remove the hold and start
#   ./scripts/service.sh logs      follow the engine log
set -uo pipefail
cd "$(dirname "$0")/.."
DOMAIN="gui/$(id -u)"; JOB="$DOMAIN/com.kaibot.engine"
LOG_DIR="${KAIBOT_LOG_DIR:-$HOME/Library/Logs/kaibot}"
running() { launchctl print "$JOB" 2>/dev/null | grep -q 'state = running'; }
loaded() { launchctl print "$JOB" >/dev/null 2>&1 || { echo "Not installed: run ./scripts/install_service.sh"; exit 1; }; }
stop() {
  running || { echo "The service engine is not running."; return 0; }
  echo "Stopping cleanly (up to 90 s)…"; launchctl kill SIGINT "$JOB"
  for _ in $(seq 90); do running || { echo "Stopped."; return 0; }; sleep 1; done
  echo "Still running after 90 s: see ./scripts/service.sh logs"; return 1
}
case "${1:-status}" in
  status)
    loaded
    if running; then echo "engine service: running (pid $(launchctl print "$JOB" | awk '/^\tpid =/ {print $3}'))"
    else echo "engine service: not running$( [ -f data/HOLD ] && echo ' (on hold: ./scripts/service.sh unhold)')"; fi
    echo "engines on this machine: $(./scripts/engine_guard.sh --list | tr '\n' ' ')"
    bash scripts/release.sh --show | sed -n '1,3p'
    echo "--- last lines of $LOG_DIR/engine.log"; tail -n 8 "$LOG_DIR/engine.log" 2>/dev/null ;;
  stop) loaded; stop ;;
  start) loaded; rm -f data/live/service.note; launchctl kickstart "$JOB" && echo "Started: ./scripts/service.sh logs" ;;
  restart) loaded; stop && launchctl kickstart "$JOB" && echo "Restarted." ;;
  hold) loaded; mkdir -p data; date -u +%FT%TZ > data/HOLD; stop; echo "On hold until ./scripts/service.sh unhold" ;;
  unhold) loaded; rm -f data/HOLD data/live/service.note; launchctl kickstart "$JOB" && echo "Hold removed, started." ;;
  logs) tail -n 50 -F "$LOG_DIR/engine.log" ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
