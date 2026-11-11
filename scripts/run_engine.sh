#!/usr/bin/env bash
# Start the Kaibot engine on this machine and keep it (and the Mac) awake while it runs.
#   ./scripts/run_engine.sh            run (Docker if it's running, otherwise Python 3.12+)
#   ./scripts/run_engine.sh --check    test keys and connections, then exit
#   ./scripts/run_engine.sh --docker | --python   force one way
# Stop with Ctrl-C. Open trades keep their stop-loss at Alpaca either way.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
MODE=auto; ARGS=()
for a in "$@"; do case "$a" in --docker) MODE=docker;; --python) MODE=python;; *) ARGS+=("$a");; esac; done

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
for k in ALPACA_API_KEY ALPACA_SECRET FRED_API_KEY; do
  grep -q "^$k=" "$ENV_FILE" || { echo "Missing $k in $ENV_FILE"; exit 1; }
done
grep -qE "^SUPABASE_SERVICE(_ROLE)?_KEY=" "$ENV_FILE" || {
  echo "Missing SUPABASE_SERVICE_KEY in $ENV_FILE (Supabase → Project Settings → API keys → Secret key, sb_secret_…)"; exit 1; }
git pull --ff-only -q 2>/dev/null || echo "(could not pull the latest code; running what is here)"
mkdir -p data/live

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
CHECK=0; for a in ${ARGS[@]+"${ARGS[@]}"}; do [ "$a" = --check ] && CHECK=1; done
# The watchdog (separate process, own broker client) is left running when the engine stops or crashes:
# that is its job. It cancels entries if the engine goes quiet and flattens at 15:55. One copy at a time.

if [ "$MODE" = docker ] || { [ "$MODE" = auto ] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }; then
  echo "Starting with Docker…"
  docker build -q -t kaibot-engine --build-arg CODE_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" . >/dev/null
  if [ "$CHECK" = 0 ] && ! docker ps -q -f name=^kaibot-watchdog$ | grep -q .; then
    docker run -d --rm --name kaibot-watchdog -e KAIBOT_ENV_FILE=/secrets/.env -v "$ENV_FILE:/secrets/.env:ro" \
      kaibot-engine python -m kaibot.engine.watchdog >/dev/null && echo "Watchdog started (container kaibot-watchdog)."
  fi
  awake docker run --rm --name kaibot-engine -e KAIBOT_ENV_FILE=/secrets/.env \
    -v "$ENV_FILE:/secrets/.env:ro" -v "$PWD/data/live:/data" kaibot-engine python -m kaibot.engine ${ARGS[@]+"${ARGS[@]}"}
else
  PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
  if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Needs Python 3.12+ or Docker Desktop. Install one:  brew install python@3.13   (or Docker Desktop)"; exit 1
  fi
  echo "Starting with $("$PY" --version)…"
  [ -d .venv ] || "$PY" -m venv .venv
  .venv/bin/pip install -q --disable-pip-version-check -r requirements-engine.txt
  # python.org's macOS Python has no root certificates until "Install Certificates.command" is run: use certifi's.
  export SSL_CERT_FILE="${SSL_CERT_FILE:-$(.venv/bin/python -m certifi)}"
  WD_PID=data/live/watchdog.pid
  # Restart a watchdog left over from an earlier start, so it runs today's code and settings.
  if [ "$CHECK" = 0 ] && [ -f "$WD_PID" ] && ps -p "$(cat "$WD_PID")" -o command= 2>/dev/null | grep -q kaibot.engine.watchdog; then
    kill "$(cat "$WD_PID")" 2>/dev/null || true; sleep 1
  fi
  if [ "$CHECK" = 0 ]; then
    mkdir -p logs
    KAIBOT_ENV_FILE="$ENV_FILE" PYTHONPATH="$PWD/src" nohup .venv/bin/python -m kaibot.engine.watchdog \
      >> logs/watchdog.log 2>&1 &
    echo $! > "$WD_PID"; echo "Watchdog started (pid $(cat "$WD_PID"), log: logs/watchdog.log)."
  fi
  KAIBOT_ENV_FILE="$ENV_FILE" KAIBOT_DATA_DIR="$PWD/data/live" PYTHONPATH="$PWD/src" \
    KAIBOT_CODE_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
    awake .venv/bin/python -m kaibot.engine ${ARGS[@]+"${ARGS[@]}"}
fi
