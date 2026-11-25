#!/usr/bin/env bash
# Start the Kaibot engine on this machine and keep it (and the Mac) awake while it runs.
#   ./scripts/run_engine.sh            run (Docker if it's running, otherwise Python 3.12+)
#   ./scripts/run_engine.sh --check    test keys and connections and the Mac's sleep setting, then exit
#   ./scripts/run_engine.sh --docker | --python   force one way
# Trades the release pinned with scripts/release.sh (data/RELEASE, or KAIBOT_RELEASE=<tag|main>); nothing pinned:
# pulls main as before. Always-on (launchd, alarms, backups): scripts/install_service.sh, docs/ops/always_on.md.
# Stop with Ctrl-C. Open trades keep their stop-loss at Alpaca either way.
# With the always-on service running it refuses (but --check): use ./scripts/service.sh restart (#613).
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
MODE=auto; ARGS=()
for a in "$@"; do case "$a" in --docker) MODE=docker;; --python) MODE=python;; *) ARGS+=("$a");; esac; done
CHECK=0; for a in ${ARGS[@]+"${ARGS[@]}"}; do [ "$a" = --check ] && CHECK=1; done

# #613: while the always-on service runs (scripts/install_service.sh), it already runs the engine and the watchdog:
# refuse a second start by hand, before touching the checkout. The service's own run (scripts/service_run.sh) passes
# KAIBOT_SERVICE_RUN=<its pid>, the pid in data/live/service.pid. --check never starts an engine: always allowed.
service_pid() {  # the pid of a live service run (from its pid lock), else nothing
  local p; p="$(cat data/live/service.pid 2>/dev/null)"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && ps -p "$p" -o command= 2>/dev/null | grep -q service_run && echo "$p"
  return 0
}
service_job_running() {  # launchd (macOS) says the installed com.kaibot.engine job is running
  command -v launchctl >/dev/null 2>&1 &&
    launchctl print "gui/$(id -u)/com.kaibot.engine" 2>/dev/null | grep -q 'state = running'
}
if [ "$CHECK" = 0 ]; then
  SVC_PID="$(service_pid)"
  if [ -z "${KAIBOT_SERVICE_RUN:-}" ] || [ "${KAIBOT_SERVICE_RUN}" != "$SVC_PID" ]; then
    if [ -n "$SVC_PID" ] || service_job_running; then
      echo "The always-on service is running${SVC_PID:+ (pid $SVC_PID)}: it already runs the engine and the watchdog." >&2
      echo "Not starting a second one. Use ./scripts/service.sh restart (or status, logs, stop)." >&2
      exit 1
    fi
  fi
fi

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
for k in ALPACA_API_KEY ALPACA_SECRET FRED_API_KEY; do
  grep -q "^$k=" "$ENV_FILE" || { echo "Missing $k in $ENV_FILE"; exit 1; }
done
grep -qE "^SUPABASE_SERVICE(_ROLE)?_KEY=" "$ENV_FILE" || {
  echo "Missing SUPABASE_SERVICE_KEY in $ENV_FILE (Supabase → Project Settings → API keys → Secret key, sb_secret_…)"; exit 1; }
# Pinned release (#121, docs/ops/always_on.md): with data/RELEASE or KAIBOT_RELEASE set, trade that tag (or main).
# Nothing pinned: pull main, as before.
HEAD_BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
RELEASE_LABEL="$(bash scripts/release.sh --apply)" || exit 1
if [ -z "$RELEASE_LABEL" ]; then
  git pull --ff-only -q 2>/dev/null || echo "(could not pull the latest code; running what is here)"
elif [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] && [ -z "${KAIBOT_REEXEC:-}" ]; then
  KAIBOT_REEXEC=1 exec bash scripts/run_engine.sh "$@"  # this script may have changed with the checkout
fi
CODE_SHA="${RELEASE_LABEL:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"  # recorded with every decision
mkdir -p data/live
[ -n "$RELEASE_LABEL" ] && echo "Release: $RELEASE_LABEL"
echo "$CODE_SHA $(date -u +%FT%TZ)" > data/live/running_release

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
# --check (QA-Ops, the 24-25 Nov 2026 incident: the Mac mini slept 17 hours): the Mac's sleep settings (pmset -g).
# System sleep must be 0 (never) on AC power, else a FAILED line and --check exits 1. Not a Mac: skipped.
mac_sleep() {
  echo "mac_sleep (pmset -g):"
  if ! command -v pmset >/dev/null 2>&1; then echo "  no pmset (not a Mac): skipped"; return 0; fi
  local all ac v
  all="$(pmset -g 2>/dev/null || true)"
  printf '%s\n' "$all" | grep -E '^[[:space:]]*(sleep|disksleep|displaysleep|standby|powernap|autorestart)[[:space:]]' \
    | sed -e 's/^[[:space:]]*/  /' || true
  # the AC profile's own value (pmset -g custom, "AC Power:"); a Mac without that section: the current settings
  ac="$(pmset -g custom 2>/dev/null | awk '/^AC Power:/ {on = 1; next} /^[^[:space:]].*:[[:space:]]*$/ {on = 0} on')"
  [ -n "$ac" ] || ac="$all"
  v="$(printf '%s\n' "$ac" | awk '$1 == "sleep" {print $2; exit}')"
  if [ "$v" = 0 ]; then
    echo "  system sleep on AC power: 0 (never): ok"; return 0
  fi
  echo "  FAILED: system sleep on AC power is ${v:-unknown}, not 0: the Mac can fall asleep and stop the engine"
  echo "  (heartbeat, nightly bar refresh). Fix: sudo pmset -c sleep 0 disksleep 0 (docs/ops/always_on.md)."
  return 1
}
finish() {  # the engine through caffeinate; with --check, then the Mac's sleep settings
  [ "$CHECK" = 1 ] || { awake "$@"; return; }
  local rc=0
  awake "$@" || rc=$?
  if ! mac_sleep; then
    echo "Fix the FAILED line above (the Mac's sleep setting), then run --check again."
    [ "$rc" != 0 ] || rc=1
  fi
  exit "$rc"
}
# Never two engines at once (one paper account would trade twice): refuse if one already runs on this machine.
if [ "$CHECK" = 0 ]; then ./scripts/engine_guard.sh || exit 1; fi
# The watchdog (separate process, own broker client) is left running when the engine stops or crashes:
# that is its job. It cancels entries if the engine goes quiet, keeps every multi-day stop and sells the intraday
# shares at 15:55 (S80). One copy at a time.

if [ "$MODE" = docker ] || { [ "$MODE" = auto ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }; then
  echo "Starting with Docker…"
  docker build -q -t kaibot-engine --build-arg CODE_SHA="$CODE_SHA" . >/dev/null
  if [ "$CHECK" = 0 ] && ! docker ps -q -f name=^kaibot-watchdog$ | grep -q .; then
    docker run -d --rm --name kaibot-watchdog -e KAIBOT_ENV_FILE=/secrets/.env -v "$ENV_FILE:/secrets/.env:ro" \
      -v "$PWD/data/live:/data" kaibot-engine python -m kaibot.engine.watchdog >/dev/null && echo "Watchdog started (container kaibot-watchdog)."
  fi
  # #206: the live daily path reads the daily store (written on this Mac by ./scripts/fetch_daily.sh). The image has
  # no data/ folder, so the store is mounted read-only (KAIBOT_DAILY_DATA from the environment or the .env, else
  # ./data: the folder that holds daily/). Without it the daily path finds no store and decides nothing.
  DAILY_HOST="${KAIBOT_DAILY_DATA:-$(sed -n 's/^KAIBOT_DAILY_DATA=//p' "$ENV_FILE" | tail -1 | tr -d "\"'")}"
  DAILY_HOST="${DAILY_HOST:-$PWD/data}"
  DAILY_MOUNT=()
  [ -d "$DAILY_HOST/daily" ] && DAILY_MOUNT=(-v "$DAILY_HOST/daily:/kaibot-daily/daily:ro" -e KAIBOT_DAILY_DATA=/kaibot-daily)
  finish docker run --rm --name kaibot-engine -e KAIBOT_ENV_FILE=/secrets/.env ${DAILY_MOUNT[@]+"${DAILY_MOUNT[@]}"} \
    -v "$ENV_FILE:/secrets/.env:ro" -v "$PWD/data/live:/data" kaibot-engine python -m kaibot.engine ${ARGS[@]+"${ARGS[@]}"}
else
  PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
  if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Needs Python 3.12+ or Docker Desktop. Install one:  brew install python@3.13   (or Docker Desktop)"; exit 1
  fi
  echo "Starting with $("$PY" --version)…"
  [ -d .venv ] || "$PY" -m venv .venv
  .venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-engine.txt  # locked (uv.lock export, #122)
  # python.org's macOS Python has no root certificates until "Install Certificates.command" is run: use certifi's.
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"
  WD_PID=data/live/watchdog.pid
  # Restart a watchdog left over from an earlier start, so it runs today's code and settings.
  if [ "$CHECK" = 0 ] && [ -f "$WD_PID" ] && ps -p "$(cat "$WD_PID")" -o command= 2>/dev/null | grep -q kaibot.engine.watchdog; then
    OLD_WD="$(cat "$WD_PID")"; kill "$OLD_WD" 2>/dev/null || true
    for _ in $(seq 30); do kill -0 "$OLD_WD" 2>/dev/null || break; sleep 1; done  # #613: let it release its lock
  fi
  if [ "$CHECK" = 0 ]; then
    LOG_DIR="${KAIBOT_LOG_DIR:-logs}"; mkdir -p "$LOG_DIR"  # the service sets ~/Library/Logs/kaibot
    KAIBOT_ENV_FILE="$ENV_FILE" KAIBOT_DATA_DIR="$PWD/data/live" PYTHONPATH="$PWD/src" \
      nohup .venv/bin/python -m kaibot.engine.watchdog \
      >> "$LOG_DIR/watchdog.log" 2>&1 &
    echo $! > "$WD_PID"; echo "Watchdog started (pid $(cat "$WD_PID"), log: $LOG_DIR/watchdog.log)."
  fi
  KAIBOT_ENV_FILE="$ENV_FILE" KAIBOT_DATA_DIR="$PWD/data/live" PYTHONPATH="$PWD/src" \
    KAIBOT_CODE_SHA="$CODE_SHA" \
    finish .venv/bin/python -m kaibot.engine ${ARGS[@]+"${ARGS[@]}"}
fi
