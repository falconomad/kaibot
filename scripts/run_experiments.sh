#!/usr/bin/env bash
# Issue #109: the control (variant A) and the four pre-declared experiments, each its own ten-year walk-forward,
# one after another, then ONE comparison table (also written to data/reports/experiments_summary.md).
#   ./scripts/run_experiments.sh
#   ./scripts/run_experiments.sh multiday    issue #113: the four multi-day rules (trend_highs, rel_strength,
#       overnight_spy, winners_run), then ONE table with SPY buy-and-hold and the equal-weight universe, written
#       to data/reports/experiments_multiday_summary.md. Allow about 25 minutes for the first run (estimated,
#       not measured on the real data): 5 to 10 to build the daily bars from the minute bars and about as long
#       again for winners_run's one pass over the minute bars. Both are cached in data/multiday/, so a re-run
#       takes a few minutes.
#   ./scripts/run_experiments.sh core        issue #127: the momentum core of decision 15 (trend_momentum,
#       mom_12_1_band with and without the index filter), each frozen configuration ONE continuous account from
#       2017-05, then ONE table with SPY, the equal-weight universe and matched random entries, written to
#       data/reports/experiments_core_summary.md. Pre-registered in docs/prereg/. Reuses the daily-bar cache of
#       the multiday set (builds it if missing: 5 to 10 minutes more). Measured on synthetic bars of the real size
#       (busy 2-CPU cloud machine: trend_momentum 4.5 min), not on the real data: about 15 minutes with the
#       cache and the default 200 random runs per exposure cap, about 35 with KAIBOT_CORE_CONTROL_RUNS=500
#       (what Gate B mark 3 asks for; a different number of runs is a new report, not a resume). Needs no .env.
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

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
mkdir -p logs data/reports

if [ "${1:-}" = "core" ]; then  # reads only data/bars_1m, data/news and the daily-bar cache: no .env needed
  RUNS="${KAIBOT_CORE_CONTROL_RUNS:-200}"
  for v in trend_momentum mom_12_1_band mom_12_1_band_nofilter; do
    if "$PY" scripts/momentum_core.py --has "$v" --level "$LEVEL" --start "$START" --end "$END" \
        --universe "$UNIVERSE" --control-runs "$RUNS"; then
      echo "== $v: report exists, skipped"
      continue
    fi
    echo "== $v: running (log: logs/experiment_core_$v.log)"
    awake "$PY" scripts/momentum_core.py --universe "$UNIVERSE" --start "$START" --end "$END" --level "$LEVEL" \
      --version "$v" --control-runs "$RUNS" > "logs/experiment_core_$v.log" 2>&1 \
      || { echo "$v failed: see logs/experiment_core_$v.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_core_$v.log"
  done
  echo
  "$PY" scripts/momentum_core.py --summary --level "$LEVEL"
  exit 0
fi
[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }

if [ "${1:-}" = "multiday" ]; then
  for exp in trend_highs rel_strength overnight_spy winners_run; do
    if "$PY" scripts/experiments_summary.py --has "$exp" --level "$LEVEL" --start "$START" --end "$END" \
        --universe "$UNIVERSE"; then
      echo "== $exp: report exists, skipped"
      continue
    fi
    echo "== $exp: running (log: logs/experiment_multiday_$exp.log)"
    awake "$PY" scripts/walk_forward_multiday.py --env "$ENV_FILE" --universe "$UNIVERSE" --start "$START" \
      --end "$END" --level "$LEVEL" --rule "$exp" > "logs/experiment_multiday_$exp.log" 2>&1 \
      || { echo "$exp failed: see logs/experiment_multiday_$exp.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_multiday_$exp.log"
  done
  echo
  "$PY" scripts/experiments_summary.py --set multiday --level "$LEVEL"
  exit 0
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [multiday|core]   (no argument: the variant A experiments of issue #109)"; exit 2
fi

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
