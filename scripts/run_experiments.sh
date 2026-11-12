#!/usr/bin/env bash
# Issue #109: the control (variant A) and the four pre-declared experiments, each its own ten-year walk-forward,
# one after another, then ONE comparison table (also written to data/reports/experiments_summary.md).
#   ./scripts/run_experiments.sh
# Resumable: an experiment whose report already exists for the same dates and universe is skipped, so after
# Ctrl-C just run it again. Research only: reads the history under data/, never touches the engine or the broker.
# Each run takes about as long as the baseline walk-forward (about 12 minutes), so allow about an hour in all.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
UNIVERSE="${KAIBOT_EXP_UNIVERSE:-config/universe_history.yaml}"
START="${KAIBOT_EXP_START:-2016-01-01}"
END="${KAIBOT_EXP_END:-2026-11-06}"
LEVEL=balanced
PY=.venv/bin/python

[ -x "$PY" ] || { echo "No .venv here: run ./scripts/fetch_history.sh --status once (it creates it)"; exit 1; }
"$PY" -c 'import scipy, certifi, pyarrow' 2>/dev/null \
  || .venv/bin/pip install -q --disable-pip-version-check -r requirements-engine.txt -r requirements-research.txt
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -m certifi)}"  # macOS python.org builds lack root certs
[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
mkdir -p logs data/reports
for exp in control mornings wide_stop high_vix mornings_wide_stop; do
  if "$PY" scripts/experiments_summary.py --has "$exp" --level "$LEVEL" --start "$START" --end "$END" \
      --universe "$UNIVERSE"; then
    echo "== $exp: report exists, skipped"
    continue
  fi
  echo "== $exp: running (log: logs/experiment_$exp.log)"
  awake "$PY" scripts/walk_forward.py --env "$ENV_FILE" --universe "$UNIVERSE" --start "$START" --end "$END" \
    --variant A --level "$LEVEL" --experiment "$exp" > "logs/experiment_$exp.log" 2>&1 \
    || { echo "$exp failed: see logs/experiment_$exp.log, then run this again (finished ones are skipped)"; exit 1; }
  tail -n 1 "logs/experiment_$exp.log"
done
echo
"$PY" scripts/experiments_summary.py --level "$LEVEL"
