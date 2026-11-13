#!/usr/bin/env bash
# Start the trend_momentum PAPER experiment on this Mac with one command (decision 15 section 11; docs/ops/paper_start.md).
#   ./scripts/go_live_paper.sh
# Run it OUTSIDE 09:30-16:00 New York time (15:30-22:00 Berlin). Safe to run again: every step checks first.
#   1. git pull --ff-only (the latest code)
#   2. ./scripts/alpaca_no_shorting.sh (shorting off on the paper account; stops here if Alpaca does not confirm)
#   3. KAIBOT_MULTIDAY=on in the engine's .env (the one line added or replaced, a timestamped backup kept)
#   4. stops an engine already running on this Mac (never two engines at once)
#   5. ./scripts/run_engine.sh --check (must end with ALL GOOD)
#   6. ./scripts/install_service.sh (launchd: the engine at login and boot, kept alive; the nightly daily-bar refresh)
#   7. a short summary: shorting, multi-day, overnight, level and cap, the stop line, the next decision, how to stop
set -uo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
export KAIBOT_ENV_FILE="$ENV_FILE"
STOP_WAIT="${GO_LIVE_STOP_WAIT:-60}"
PY="$(command -v .venv/bin/python 2>/dev/null || command -v python3.13 || command -v python3.12 || command -v python3)"
say() { echo "== $*"; }
stop_here() { echo; echo "STOPPED: $*"; exit 1; }

# 0. outside the session (weekday 09:30-16:00 New York): GO_LIVE_NOW="<weekday 1-7> <HHMM>" overrides (tests)
read -r WD HM <<<"${GO_LIVE_NOW:-$(TZ=America/New_York date '+%u %H%M')}"
if [ "$WD" -le 5 ] && [ "$((10#$HM))" -ge 930 ] && [ "$((10#$HM))" -lt 1600 ]; then
  stop_here "the US market is open now (09:30-16:00 New York, 15:30-22:00 Berlin). Run this again after the close."
fi
[ -f "$ENV_FILE" ] || stop_here "no .env at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)."

say "1/7 latest code (git pull --ff-only)"
git pull --ff-only -q || stop_here "could not pull the latest code. Run 'git status': local changes? Tell Product."

say "2/7 shorting off on the Alpaca paper account"
if ! ./scripts/alpaca_no_shorting.sh; then
  stop_here "Alpaca did not confirm that shorting is off (keys rejected, no network, or the setting did not
apply). Check the paper keys in $ENV_FILE, or switch Shorting off in the Alpaca dashboard (paper account,
Account Configurations), then run this again."
fi

say "3/7 KAIBOT_MULTIDAY=on in $ENV_FILE"
"$PY" scripts/go_live_paper.py set-flag "$ENV_FILE" KAIBOT_MULTIDAY on || stop_here "could not update the .env."

say "4/7 no other engine on this Mac"
service_running() { command -v launchctl >/dev/null 2>&1 &&
  launchctl print "gui/$(id -u)/com.kaibot.engine" 2>/dev/null | grep -q 'state = running'; }
if service_running; then
  ./scripts/service.sh stop || stop_here "the service engine did not stop: ./scripts/service.sh status"
fi
while read -r kind id rest; do
  [ -n "${kind:-}" ] || continue
  case "$kind" in
    python)
      echo "Stopping the engine started by hand (pid $id) cleanly…"
      kill -INT "$id" 2>/dev/null || true
      for _ in $(seq "$STOP_WAIT"); do kill -0 "$id" 2>/dev/null || break; sleep 1; done
      if kill -0 "$id" 2>/dev/null; then
        stop_here "an engine started by hand is still running (pid $id). Press Ctrl-C in its terminal window,
wait until it says it stopped, then run this again."
      fi ;;
    docker|compose)
      echo "Stopping the Docker engine ($rest)…"
      docker stop -t 60 "$id" >/dev/null 2>&1 || stop_here "could not stop the Docker engine: docker stop $id" ;;
  esac
done < <(./scripts/engine_guard.sh --list 2>/dev/null)
./scripts/engine_guard.sh >/dev/null 2>&1 || stop_here "an engine is still running on this Mac: ./scripts/engine_guard.sh --list"

say "5/7 check (keys, connections, the multi-day switches)"
CHECK_OUT="$(mktemp)"; trap 'rm -f "$CHECK_OUT"' EXIT
./scripts/run_engine.sh --check 2>&1 | tee "$CHECK_OUT"
grep -q "ALL GOOD" "$CHECK_OUT" || stop_here "the check did not end with ALL GOOD. Fix the FAILED lines, then run this again."

say "6/7 always-on service (engine at login and boot, nightly daily-bar refresh)"
./scripts/install_service.sh || stop_here "the service did not install: see the message above."

say "7/7 summary"
"$PY" scripts/go_live_paper.py summary "$CHECK_OUT"
echo
echo "The Mac may be off at weekends: on Monday it starts the engine by itself at login, and the daily-bar refresh"
echo "runs at login too, so Friday's bars are in before the rule needs them (it decides on Tuesday evenings)."
