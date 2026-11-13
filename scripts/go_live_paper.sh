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
# QA-Ops second key on #218: the helper is run from a copy, so a release checkout by the check (step 5) cannot take
# it away; the check runs as the service will (no KAIBOT_RELEASE, no KAIBOT_MULTIDAY from this shell).
HELPER_DIR="$(mktemp -d)"; trap 'rm -rf "$HELPER_DIR"' EXIT
cp scripts/go_live_paper.py "$HELPER_DIR/go_live_paper.py"
HELPER="$HELPER_DIR/go_live_paper.py"

# 0. outside the session (weekday 09:30-16:00 New York): GO_LIVE_NOW="<weekday 1-7> <HHMM>" overrides (tests)
read -r WD HM <<<"${GO_LIVE_NOW:-$(TZ=America/New_York date '+%u %H%M')}"
if [ "$WD" -le 5 ] && [ "$((10#$HM))" -ge 930 ] && [ "$((10#$HM))" -lt 1600 ]; then
  stop_here "the US market is open now (09:30-16:00 New York, 15:30-22:00 Berlin). Run this again after the close."
fi
[ -f "$ENV_FILE" ] || stop_here "no .env at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)."

# The engine trades the release pinned in data/RELEASE (scripts/release.sh), not what this pull brings: a pinned tag
# must already contain the go-live (allow_overnight: true), or the experiment would never run.
PIN="$(head -n1 data/RELEASE 2>/dev/null | tr -d '[:space:]')"
if [ -n "$PIN" ] && [ "$PIN" != main ]; then
  say "1/7 the engine is pinned to release $PIN (data/RELEASE): no pull"
  git fetch -q --tags 2>/dev/null || true
  git show "refs/tags/$PIN:config/profiles.yaml" 2>/dev/null | grep -Eq '^[[:space:]]*allow_overnight:[[:space:]]*true' ||
    stop_here "the engine is pinned to release $PIN, which does not contain the go-live. Follow main with
'./scripts/release.sh main' (or pin a newer release), then run this again. Nothing was changed."
else
  say "1/7 latest code (git pull --ff-only)"
  git symbolic-ref -q HEAD >/dev/null ||
    stop_here "this checkout is not on a branch. Run 'git checkout main', then run this again. Nothing was changed."
  git pull --ff-only -q || stop_here "could not pull the latest code. Run 'git status': local changes? Tell Product."
fi

say "2/7 shorting off on the Alpaca paper account"
if ! ./scripts/alpaca_no_shorting.sh; then
  stop_here "Alpaca did not confirm that shorting is off (keys rejected, no network, or the setting did not
apply). Check the paper keys in $ENV_FILE, or switch Shorting off in the Alpaca dashboard (paper account,
Account Configurations), then run this again."
fi

say "3/7 KAIBOT_MULTIDAY=on in $ENV_FILE"
"$PY" "$HELPER" set-flag "$ENV_FILE" KAIBOT_MULTIDAY on || stop_here "could not update the .env."

say "4/7 no other engine on this Mac"
service_running() { command -v launchctl >/dev/null 2>&1 &&
  launchctl print "gui/$(id -u)/com.kaibot.engine" 2>/dev/null | grep -q 'state = running'; }
service_loaded() { command -v launchctl >/dev/null 2>&1 && launchctl print "gui/$(id -u)/com.kaibot.engine" >/dev/null 2>&1; }
if service_running; then
  ./scripts/service.sh stop || stop_here "the service engine did not stop: ./scripts/service.sh status"
fi
STOPPED_NOTE=""
if service_loaded; then
  # A service that stands by (another engine running) would start by itself a minute after the hand-started engine
  # stops: it is unloaded until step 6 installs it again, so it can never race the steps below.
  launchctl bootout "gui/$(id -u)/com.kaibot.engine" 2>/dev/null || true
  STOPPED_NOTE=" The engine service is stopped until this command completes (or ./scripts/install_service.sh)."
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
./scripts/engine_guard.sh >/dev/null 2>&1 ||
  stop_here "an engine is still running on this Mac: ./scripts/engine_guard.sh --list.$STOPPED_NOTE"

say "5/7 check (keys, connections, the multi-day switches)"
CHECK_OUT="$HELPER_DIR/check.out"
env -u KAIBOT_RELEASE -u KAIBOT_MULTIDAY ./scripts/run_engine.sh --check 2>&1 | tee "$CHECK_OUT"
CHECK_CODE="${PIPESTATUS[0]}"
{ [ "$CHECK_CODE" = 0 ] && grep -qx "ALL GOOD" "$CHECK_OUT"; } ||
  stop_here "the check did not end with ALL GOOD. Fix the FAILED lines, then run this again.$STOPPED_NOTE"
"$PY" "$HELPER" gate "$CHECK_OUT" || stop_here "the check does not show the experiment switched on (see the line
above). Nothing was installed.$STOPPED_NOTE"

say "6/7 always-on service (engine at login and boot, nightly daily-bar refresh)"
./scripts/install_service.sh || stop_here "the service did not install: see the message above.$STOPPED_NOTE"

say "7/7 summary"
"$PY" "$HELPER" summary "$CHECK_OUT"
if [ -f data/HOLD ]; then
  echo
  echo "ON HOLD: data/HOLD exists, so the service does not start the engine. Start it with: ./scripts/service.sh unhold"
fi
echo
echo "The Mac may be off at weekends: on Monday it starts the engine by itself at login, and the daily-bar refresh"
echo "runs at login too, so Friday's bars are in before the rule needs them (it decides on Tuesday evenings)."
