#!/usr/bin/env bash
# What the launchd agents run (issue #121). Not for hand use: start by hand with ./scripts/run_engine.sh.
#   service_run.sh engine   one engine run through run_engine.sh, with the alarm pinger and the ticker-check worker
#                           (scripts/run_ticker_checks.py, read-only, log ticker_checks.log) beside it, and the price
#                           relay (scripts/price_stream.py) when KAIBOT_PRICE_STREAM=on
#   service_run.sh daily    nightly job: rotate logs, back up local files, refresh the daily bar store (a second run
#                           the same day only refreshes the store)
# Settings (written into the plist by install_service.sh): KAIBOT_SERVICE_MODE (docker|python), KAIBOT_ENV_FILE,
# KAIBOT_LOG_DIR. KAIBOT_PRICE_STREAM=on adds the price relay (scripts/price_stream.py, log price_stream.log; off by
# default; read like the others, on/off or 1/0). KAIBOT_TICKER_CHECKS=0 turns the worker off and KAIBOT_DAILY_FETCH=0 skips the nightly bar refresh,
# each read from the service's environment or the .env (`switch`: an explicit 0 or 1). Exit codes for launchd
# (KeepAlive restarts only after a non-zero exit; the worker never changes them):
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

# A setting as the engine reads its settings (engine/config.py): the service's own environment when the variable is
# set there, else the .env file (install_service.sh writes only KAIBOT_ENV_FILE, KAIBOT_LOG_DIR and
# KAIBOT_SERVICE_MODE into the plist), else the default. Same parsing: KEY=VALUE, " #" starts a comment, quotes off.
setting() {  # setting NAME DEFAULT
  local name="$1" def="$2" line v
  if [ -n "${!name+x}" ]; then
    v="${!name}"
  else
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$name=" "$ENV_FILE" 2>/dev/null | tail -n 1)"
    [ -n "$line" ] || { echo "$def"; return 0; }
    v="${line#*=}"
    v="${v%% \#*}"
    v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  fi
  [ -n "$v" ] || v="$def"
  echo "$v"
}
switch() {  # switch NAME DEFAULT: true for an explicit 1; any value but 0 or 1 keeps the default and says so
  local v; v="$(setting "$1" "$2")"
  case "$v" in
    0|1) ;;
    *) say "$1='$v' is not 0 or 1: the default ($2) is used"; v="$2" ;;
  esac
  [ "$v" = 1 ]
}

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

# ---- the ticker-check worker (M5 S68, #96): answers the dashboard's ticker checks (My lists) within seconds while
# the engine service is up, market hours or not. A separate, read-only process (asset record, daily bars, Finnhub
# industry under the config/sources.yaml budgets; it never trades). Restarted when it dies (5 s, doubling to 5 min);
# stopped with the engine. It never blocks the engine or changes the service's exit code.
CHECKS_PID=data/live/ticker_checks.pid
CHECKS_LOG="$LOG_DIR/ticker_checks.log"
CHECKS_NOTE=data/live/ticker_checks.note
CHECKS_BOX=kaibot-ticker-checks
W_PID=""; W_KIND=""; W_CMD=()

checks_cmd() {  # W_CMD and W_KIND for this machine; 1 when it cannot run yet (no .venv with the deps, no image)
  local py="$REPO/.venv/bin/python" cert
  if [ -x "$py" ] && PYTHONPATH="$REPO/src" "$py" -c \
      'import kaibot.common.http, kaibot.engine.alpaca, kaibot.engine.store, kaibot.engine.tickercheck' 2>/dev/null; then
    # python.org's macOS Python has no root certificates: certifi's, as run_engine.sh does
    cert="${SSL_CERT_FILE:-$("$py" -m certifi 2>/dev/null)}"
    W_KIND=venv; W_CMD=(env PYTHONUNBUFFERED=1 ${cert:+SSL_CERT_FILE="$cert"}
                        "$py" -u scripts/run_ticker_checks.py --env "$ENV_FILE")
  elif [ "$MODE" = docker ] && docker image inspect kaibot-engine >/dev/null 2>&1; then
    # no .venv on a Docker Mac: the engine's image (built by run_engine.sh), with today's worker script mounted
    W_KIND=docker; W_CMD=(docker run --rm --init --name "$CHECKS_BOX" -e KAIBOT_ENV_FILE=/secrets/.env
      -v "$ENV_FILE:/secrets/.env:ro" -v "$REPO/scripts/run_ticker_checks.py:/app/scripts/run_ticker_checks.py:ro"
      kaibot-engine python -u scripts/run_ticker_checks.py --env /secrets/.env)
  else
    return 1
  fi
}

checks_stop_worker() {
  if [ -n "$W_PID" ]; then
    kill -TERM "$W_PID" 2>/dev/null
    for _ in $(seq 10); do kill -0 "$W_PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$W_PID" 2>/dev/null
    wait "$W_PID" 2>/dev/null
  fi
  [ "$W_KIND" = docker ] && docker rm -f "$CHECKS_BOX" >/dev/null 2>&1
  W_PID=""; rm -f "$CHECKS_PID"
}

checks_loop() {  # checks_loop ENGINE_PID WRAPPER_PID: in the background, output to the worker's log only
  local engine="$1" parent="$2" delay=5 started code
  alive() { kill -0 "$engine" 2>/dev/null && kill -0 "$parent" 2>/dev/null; }
  nap() { local i; for ((i = 0; i < $1; i++)); do alive || return 1; sleep 1; done; }
  trap 'checks_stop_worker; say "ticker-check worker stopped with the engine"; exit 0' TERM INT HUP
  nap "${KAIBOT_CHECKS_START_DELAY:-10}" || exit 0  # let run_engine.sh check out the release and build first
  while alive; do
    if ! checks_cmd; then
      local why="cannot start the ticker-check worker yet: no .venv with the engine's packages"
      [ "$MODE" = docker ] && why="$why and no kaibot-engine image"
      [ "$(cat "$CHECKS_NOTE" 2>/dev/null)" = "$why" ] || { say "$why (retrying every minute)"; echo "$why" > "$CHECKS_NOTE"; }
      nap 60 || break; continue
    fi
    rm -f "$CHECKS_NOTE"; started=$SECONDS
    "${W_CMD[@]}" </dev/null &
    W_PID=$!; echo "$W_PID" > "$CHECKS_PID"
    say "ticker-check worker started (pid $W_PID, $W_KIND)"
    while alive && kill -0 "$W_PID" 2>/dev/null; do sleep 1; done
    alive || break
    wait "$W_PID"; code=$?; W_PID=""; rm -f "$CHECKS_PID"
    [ $((SECONDS - started)) -ge 300 ] && delay=5
    say "ticker-check worker exited with code $code: starting it again in $delay s"
    nap "$delay" || break
    delay=$((delay * 2 > 300 ? 300 : delay * 2))
  done
  checks_stop_worker; say "ticker-check worker stopped with the engine"
}

checks_reap() {  # a worker left by a killed service run: stop it, so two workers never answer the same rows
  local p; p="$(cat "$CHECKS_PID" 2>/dev/null)"
  if [ -n "$p" ] && ps -p "$p" -o command= 2>/dev/null | grep -q run_ticker_checks; then
    kill "$p" 2>/dev/null; say "stopped a ticker-check worker left from an earlier run (pid $p)"
  fi
  rm -f "$CHECKS_PID"
  [ "$MODE" = docker ] && docker rm -f "$CHECKS_BOX" >/dev/null 2>&1
  return 0
}

checks_end() {  # $1 = the loop's pid: give it 15 s to stop the worker, then end its process group
  kill -TERM "$1" 2>/dev/null || return 0
  for _ in $(seq 15); do kill -0 "$1" 2>/dev/null || return 0; sleep 1; done
  kill -KILL -- "-$1" 2>/dev/null; kill -KILL "$1" 2>/dev/null
  [ "$MODE" = docker ] && docker rm -f "$CHECKS_BOX" >/dev/null 2>&1
  rm -f "$CHECKS_PID"; return 0
}

# ---- the price relay (20 Nov 2026): scripts/price_stream.py streams the held stocks' IEX prices to the dashboard
# (Supabase Realtime, at most one message a second, market hours only). Off unless KAIBOT_PRICE_STREAM=on. A separate
# process in its own process group: it sends no order and writes no trading table. Whatever it does (fails, hangs,
# exits) never reaches the engine: nothing here signals, waits on or restarts the engine, and the service's exit
# code is the engine's alone. Restarted when it dies (5 s, doubling to 5 min); stopped with the engine.
STREAM_PID=data/live/price_stream.pid
STREAM_LOG="$LOG_DIR/price_stream.log"
STREAM_NOTE=data/live/price_stream.note
STREAM_BOX=kaibot-price-stream
S_PID=""; S_KIND=""; S_CMD=()

stream_on() {  # KAIBOT_PRICE_STREAM: on/1 starts the relay; off/0/unset does not; anything else is off, said once
  local v; v="$(setting KAIBOT_PRICE_STREAM off | tr '[:upper:]' '[:lower:]')"
  case "$v" in
    on|1|true|yes) return 0 ;;
    off|0|false|no) return 1 ;;
    *) say "KAIBOT_PRICE_STREAM='$v' is not on or off: the price relay stays off"; return 1 ;;
  esac
}

stream_cmd() {  # S_CMD and S_KIND; 1 when it cannot run yet. Needs Python 3.11+ only (no package beyond the stdlib).
  local py cert
  for py in "$REPO/.venv/bin/python" "$(command -v python3 || true)"; do
    if [ -n "$py" ] && [ -x "$py" ] && PYTHONPATH="$REPO/src" "$py" -c \
        'import sys; assert sys.version_info >= (3, 11); import kaibot.engine.config' 2>/dev/null; then
      cert="${SSL_CERT_FILE:-$("$py" -m certifi 2>/dev/null)}"
      S_KIND=python; S_CMD=(env PYTHONUNBUFFERED=1 ${cert:+SSL_CERT_FILE="$cert"}
                            "$py" -u scripts/price_stream.py --env "$ENV_FILE")
      return 0
    fi
  done
  if [ "$MODE" = docker ] && docker image inspect kaibot-engine >/dev/null 2>&1; then
    S_KIND=docker; S_CMD=(docker run --rm --init --name "$STREAM_BOX"
      -v "$ENV_FILE:/secrets/.env:ro" -v "$REPO/scripts/price_stream.py:/app/scripts/price_stream.py:ro"
      kaibot-engine python -u scripts/price_stream.py --env /secrets/.env)
    return 0
  fi
  return 1
}

stream_stop_relay() {
  if [ -n "$S_PID" ]; then
    kill -TERM "$S_PID" 2>/dev/null
    for _ in $(seq 10); do kill -0 "$S_PID" 2>/dev/null || break; sleep 1; done
    kill -KILL "$S_PID" 2>/dev/null
    wait "$S_PID" 2>/dev/null
  fi
  [ "$S_KIND" = docker ] && docker rm -f "$STREAM_BOX" >/dev/null 2>&1
  S_PID=""; rm -f "$STREAM_PID"
}

stream_loop() {  # stream_loop ENGINE_PID WRAPPER_PID: in the background, output to the relay's log only
  local engine="$1" parent="$2" delay=5 started code
  alive() { kill -0 "$engine" 2>/dev/null && kill -0 "$parent" 2>/dev/null; }
  nap() { local i; for ((i = 0; i < $1; i++)); do alive || return 1; sleep 1; done; }
  trap 'stream_stop_relay; say "price relay stopped with the engine"; exit 0' TERM INT HUP
  nap "${KAIBOT_STREAM_START_DELAY:-15}" || exit 0  # after run_engine.sh has checked out the release
  while alive; do
    if ! stream_cmd; then
      local why="cannot start the price relay yet: no Python 3.11+ with the repo's src"
      [ "$MODE" = docker ] && why="$why and no kaibot-engine image"
      [ "$(cat "$STREAM_NOTE" 2>/dev/null)" = "$why" ] || { say "$why (retrying every minute)"; echo "$why" > "$STREAM_NOTE"; }
      nap 60 || break; continue
    fi
    rm -f "$STREAM_NOTE"; started=$SECONDS
    "${S_CMD[@]}" </dev/null &
    S_PID=$!; echo "$S_PID" > "$STREAM_PID"
    say "price relay started (pid $S_PID, $S_KIND)"
    while alive && kill -0 "$S_PID" 2>/dev/null; do sleep 1; done
    alive || break
    wait "$S_PID"; code=$?; S_PID=""; rm -f "$STREAM_PID"
    [ $((SECONDS - started)) -ge 300 ] && delay=5
    say "price relay exited with code $code: starting it again in $delay s (the engine is not affected)"
    nap "$delay" || break
    delay=$((delay * 2 > 300 ? 300 : delay * 2))
  done
  stream_stop_relay; say "price relay stopped with the engine"
}

stream_reap() {  # a relay left by a killed service run: stop it, so two never hold the one IEX connection
  local p; p="$(cat "$STREAM_PID" 2>/dev/null)"
  if [ -n "$p" ] && ps -p "$p" -o command= 2>/dev/null | grep -q price_stream; then
    kill "$p" 2>/dev/null; say "stopped a price relay left from an earlier run (pid $p)"
  fi
  rm -f "$STREAM_PID"
  [ "$MODE" = docker ] && docker rm -f "$STREAM_BOX" >/dev/null 2>&1
  return 0
}

stream_end() {  # $1 = the loop's pid: give it 15 s to stop the relay, then end its process group
  kill -TERM "$1" 2>/dev/null || return 0
  for _ in $(seq 15); do kill -0 "$1" 2>/dev/null || return 0; sleep 1; done
  kill -KILL -- "-$1" 2>/dev/null; kill -KILL "$1" 2>/dev/null
  [ "$MODE" = docker ] && docker rm -f "$STREAM_BOX" >/dev/null 2>&1
  rm -f "$STREAM_PID"; return 0
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
  rotate "$CHECKS_LOG" 10000000; rotate "$STREAM_LOG" 10000000

  local flag=() child pinger="" checks="" streamer="" code
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
  if switch KAIBOT_TICKER_CHECKS 1; then  # its own process group: the engine's stop signal does not reach it
    checks_reap
    checks_loop "$child" "$$" </dev/null >>"$CHECKS_LOG" 2>&1 &
    checks=$!
    say "ticker-check worker on (log $CHECKS_LOG)"
  fi
  if stream_on; then  # its own process group too; off by default
    stream_reap
    stream_loop "$child" "$$" </dev/null >>"$STREAM_LOG" 2>&1 &
    streamer=$!
    say "price relay on (log $STREAM_LOG)"
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
  [ -n "$checks" ] && checks_end "$checks"
  [ -n "$streamer" ] && stream_end "$streamer"
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
  if switch KAIBOT_DAILY_FETCH 1; then  # the service's environment or the .env; an explicit 0 or 1
    # #206: the multi-day rule decides on these bars (docs/ops/paper_start.md); resumable, skips what it has
    say "daily job: refresh the daily bar store (scripts/fetch_daily.sh; log logs/fetch_daily.log)"
    { echo "== $(date '+%Y-%m-%d %H:%M:%S %Z')"; ./scripts/fetch_daily.sh </dev/null; } >>logs/fetch_daily.log 2>&1 ||
      failed+=(fetch_daily)
  fi
  if switch KAIBOT_NOTICES 0; then  # #378: the dashboard's market notices; off unless 1. Never fails the daily job
    local py=.venv/bin/python; [ -x "$py" ] || py=python3
    say "daily job: market notices (scripts/publish_notices.py; log logs/notices.log)"
    { echo "== $(date '+%Y-%m-%d %H:%M:%S %Z')"; "$py" scripts/publish_notices.py --write --env "$ENV_FILE" </dev/null; } \
      >>logs/notices.log 2>&1 || say "market notices not published (see logs/notices.log); the daily job goes on"
  fi
  if [ "${#failed[@]}" -eq 0 ]; then say "daily job finished"; hc log "kaibot daily job finished on $(hostname -s)"
  else say "daily job FAILED: ${failed[*]}"; hc fail "kaibot daily job failed: ${failed[*]}"; exit 1; fi
}

case "${1:-}" in
  engine) run_engine ;;
  daily) run_daily ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
