#!/usr/bin/env bash
# What the launchd agents run (issue #121). Not for hand use: start by hand with ./scripts/run_engine.sh.
#   service_run.sh engine   one engine run through run_engine.sh, with the alarm pinger beside it
#   service_run.sh daily    nightly job: rotate logs, back up local files, refresh the daily bar store (a second run
#                           the same day only refreshes the store)
# Settings (written into the plist by install_service.sh): KAIBOT_SERVICE_MODE (docker|python), KAIBOT_ENV_FILE,
# KAIBOT_LOG_DIR. Exit codes for launchd (KeepAlive restarts only after a non-zero exit):
#   0  clean stop (service.sh stop, logout) or on hold (data/HOLD): launchd leaves it stopped
#   1  failed, refused or waiting for something (Docker, another engine): launchd tries again after 60 s
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
LOG_DIR="${KAIBOT_LOG_DIR:-$HOME/Library/Logs/kaibot}"
MODE="${KAIBOT_SERVICE_MODE:-auto}"
KEEP_LOGS="${KAIBOT_LOG_KEEP:-14}"
export KAIBOT_ENV_FILE="$ENV_FILE" KAIBOT_LOG_DIR="$LOG_DIR"
mkdir -p "$LOG_DIR" data/live
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') service $*"; }

# Say a waiting reason once, not every 60 s: the marker remembers the last one.
NOTE=data/live/service.note
note_once() { [ "$(cat "$NOTE" 2>/dev/null)" = "$1" ] || { say "$1"; echo "$1" > "$NOTE"; }; }

python_for_alarms() {
  local p
  for p in "$REPO/.venv/bin/python" "$(command -v python3 || true)"; do
    [ -n "$p" ] && [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}
hc() {  # hc start|success|fail|log [message]: never blocks the caller for long, never fails it
  local py; py="$(python_for_alarms)" || return 0
  "$py" scripts/healthcheck.py --env-file "$ENV_FILE" "$@" 2>>"$LOG_DIR/healthcheck.log" || true
}

rotate() {  # copy-truncate (launchd keeps its log files open in append mode); keep $KEEP_LOGS gzipped copies
  local f="$1" max="${2:-0}" size i
  [ -s "$f" ] || return 0
  size="$(wc -c <"$f" | tr -d ' ')"
  [ "$size" -ge "$max" ] || return 0
  for ((i = KEEP_LOGS - 1; i >= 1; i--)); do [ -f "$f.$i.gz" ] && mv -f "$f.$i.gz" "$f.$((i + 1)).gz"; done
  rm -f "$f.$((KEEP_LOGS + 1)).gz"
  cp "$f" "$f.1" && : >"$f" && gzip -f "$f.1"
}

run_engine() {
  if [ -f data/HOLD ]; then note_once "on hold (data/HOLD exists): not starting. ./scripts/service.sh unhold"; exit 0; fi
  # Local pid lock: one service engine per machine, even while run_engine.sh is still installing or building.
  local lock=data/live/service.pid
  if [ -f "$lock" ] && kill -0 "$(cat "$lock")" 2>/dev/null &&
     ps -p "$(cat "$lock")" -o command= 2>/dev/null | grep -q service_run; then
    note_once "another service run is active (pid $(cat "$lock")): not starting"; exit 1
  fi
  if ! ./scripts/engine_guard.sh 2>/dev/null; then
    note_once "an engine is already running on this machine (started by hand?): standing by, retrying every minute"
    exit 1
  fi
  if [ "$MODE" = docker ] && ! docker info >/dev/null 2>&1; then
    note_once "waiting for Docker Desktop (Settings > General > Start Docker Desktop when you sign in)"; exit 1
  fi
  echo $$ > "$lock"; rm -f "$NOTE"
  rotate "$LOG_DIR/engine.log" 10000000; rotate "$LOG_DIR/watchdog.log" 10000000

  local flag=() child pinger="" code
  case "$MODE" in docker) flag=(--docker);; python) flag=(--python);; esac
  say "starting the engine (mode $MODE)"
  set -m  # the engine gets its own process group, so a stop reaches caffeinate, python or docker in one signal
  ./scripts/run_engine.sh ${flag[@]+"${flag[@]}"} </dev/null &
  child=$!
  if py="$(python_for_alarms)"; then
    "$py" scripts/healthcheck.py --env-file "$ENV_FILE" watch --pid "$child" \
      --log "$LOG_DIR/engine.log" --log "$LOG_DIR/watchdog.log" </dev/null 2>>"$LOG_DIR/healthcheck.log" &
    pinger=$!
  else
    say "no python3 found: alarms are off"
  fi
  set +m

  stopping=0
  stop() {  # same as Ctrl-C in a terminal: SIGINT to the engine's group (the engine stops cleanly, lease released)
    stopping=1; say "stop requested: stopping the engine cleanly"
    kill -INT -- "-$child" 2>/dev/null || true
    for _ in $(seq 60); do kill -0 "$child" 2>/dev/null || return 0; sleep 1; done
    say "the engine did not stop within 60 s: sending SIGTERM"
    kill -TERM -- "-$child" 2>/dev/null || true
    [ "$MODE" = docker ] && docker stop -t 20 kaibot-engine >/dev/null 2>&1
  }
  trap stop INT TERM HUP
  while :; do  # a stop signal interrupts `wait`: keep waiting until the engine has really exited
    wait "$child"; code=$?
    kill -0 "$child" 2>/dev/null || break
  done
  trap - INT TERM HUP
  [ -n "$pinger" ] && { sleep 2; kill "$pinger" 2>/dev/null; }
  rm -f "$lock"
  if [ "$stopping" = 1 ]; then say "engine stopped (on request)"; exit 0; fi
  if [ "$code" = 0 ]; then say "engine exited cleanly"; exit 0; fi
  say "engine exited with code $code: launchd starts it again in about a minute"
  hc fail "kaibot engine exited with code $code on $(hostname -s); restarting"
  exit 1
}

run_daily() {
  command -v caffeinate >/dev/null 2>&1 && caffeinate -i -w $$ &
  local failed=() done_mark="data/live/daily_job.$(date +%F)"
  mkdir -p logs
  if [ ! -f "$done_mark" ]; then  # the 07:45 catch-up run does only the refresh
    say "daily job: rotate logs"
    for f in "$LOG_DIR"/*.log logs/*.log; do [ -f "$f" ] && rotate "$f" 1; done
    say "daily job: backup"
    ./scripts/backup_local.sh || failed+=(backup)
    rm -f data/live/daily_job.*; : > "$done_mark"
  fi
  if [ "${KAIBOT_DAILY_FETCH:-1}" = 1 ]; then
    # #206: the multi-day rule decides on these bars (docs/ops/paper_start.md); resumable, skips what it has
    say "daily job: refresh the daily bar store (scripts/fetch_daily.sh; log logs/fetch_daily.log)"
    { echo "== $(date '+%Y-%m-%d %H:%M:%S %Z')"; ./scripts/fetch_daily.sh </dev/null; } >>logs/fetch_daily.log 2>&1 ||
      failed+=(fetch_daily)
  fi
  if [ "${#failed[@]}" -eq 0 ]; then say "daily job finished"; hc log "kaibot daily job finished on $(hostname -s)"
  else say "daily job FAILED: ${failed[*]}"; hc fail "kaibot daily job failed: ${failed[*]}"; exit 1; fi
}

case "${1:-}" in
  engine) run_engine ;;
  daily) run_daily ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
