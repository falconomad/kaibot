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
#       (busy 2-CPU cloud machine: trend_momentum 4.5 min), not on the real data. Since #143 the random control
#       is the exposure-matched one of decision 15 section 10, 500 runs per exposure cap by default (what Gate B
#       mark 3 asks for; KAIBOT_CORE_CONTROL_RUNS changes it, and a different number of runs is a new report, not
#       a resume); SPY total return is read from the daily store (data/daily) when it holds SPY. Needs no .env.
#   ./scripts/run_experiments.sh core_pit    issue #143: the same three versions on the point-in-time S&P 500
#       (#123) with every symbol read from the daily store (#124: raw prices, dividends, SPY total return), then
#       ONE table plus the comparison "today's list against the point-in-time universe" of the base rows, written
#       to data/reports/experiments_core_pit_summary.md. Needs data/daily from ./scripts/fetch_daily.sh after
#       scripts/fetch_sp500_history.py (docs/m0/reproduce.md section 9 has the sequence and the times). No .env.
#       KAIBOT_CORE_COSTS=v2 runs core and core_pit on cost model v2 (#111) instead of v1.
#   ./scripts/run_experiments.sh spy         issue #128: the SPY noise-band rule (long only, 1x) and its overnight
#       carry, each ONE continuous account from 2017-05 on SPY minute bars, with cost scenarios, neighbours, the
#       15:30 check and the live last-30-minutes trade beside it, then ONE table written to
#       data/reports/experiments_spy_summary.md. Pre-registered in docs/prereg/spy_noise_band.md. Needs no .env.
#       A few minutes (see docs/m0/reproduce.md section 8).
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

if [ "${1:-}" = "core_pit" ]; then  # reads only data/daily, data/news and the committed membership log
  RUNS="${KAIBOT_CORE_CONTROL_RUNS:-500}"
  COSTS="${KAIBOT_CORE_COSTS:-v1}"
  [ -f data/daily/bars/symbol=SPY.parquet ] \
    || { echo "No daily store at data/daily: run ./scripts/fetch_daily.sh first (docs/m0/reproduce.md)"; exit 1; }
  [ -f data/universe/former_members.csv ] \
    || echo "WARNING: no data/universe/former_members.csv: former index members are probably missing from the store"
  for v in trend_momentum mom_12_1_band mom_12_1_band_nofilter; do
    if "$PY" scripts/momentum_core.py --has "$v" --level "$LEVEL" --start "$START" --end "$END" \
        --universe pit --universe-file "$UNIVERSE" --control-runs "$RUNS" --costs "$COSTS"; then
      echo "== $v (point-in-time): report exists, skipped"
      continue
    fi
    echo "== $v (point-in-time): running (log: logs/experiment_core_pit_$v.log)"
    awake "$PY" scripts/momentum_core.py --universe pit --universe-file "$UNIVERSE" --start "$START" --end "$END" \
      --level "$LEVEL" --version "$v" --control-runs "$RUNS" --costs "$COSTS" > "logs/experiment_core_pit_$v.log" 2>&1 \
      || { echo "$v failed: see logs/experiment_core_pit_$v.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_core_pit_$v.log"
  done
  echo
  "$PY" scripts/momentum_core.py --summary --set core_pit --level "$LEVEL" --costs "$COSTS"
  exit 0
fi
if [ "${1:-}" = "core" ]; then  # reads only data/bars_1m, data/news and the daily-bar cache: no .env needed
  RUNS="${KAIBOT_CORE_CONTROL_RUNS:-500}"
  COSTS="${KAIBOT_CORE_COSTS:-v1}"
  for v in trend_momentum mom_12_1_band mom_12_1_band_nofilter; do
    if "$PY" scripts/momentum_core.py --has "$v" --level "$LEVEL" --start "$START" --end "$END" \
        --universe "$UNIVERSE" --control-runs "$RUNS" --costs "$COSTS"; then
      echo "== $v: report exists, skipped"
      continue
    fi
    echo "== $v: running (log: logs/experiment_core_$v.log)"
    awake "$PY" scripts/momentum_core.py --universe "$UNIVERSE" --start "$START" --end "$END" --level "$LEVEL" \
      --version "$v" --control-runs "$RUNS" --costs "$COSTS" > "logs/experiment_core_$v.log" 2>&1 \
      || { echo "$v failed: see logs/experiment_core_$v.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_core_$v.log"
  done
  echo
  "$PY" scripts/momentum_core.py --summary --level "$LEVEL" --costs "$COSTS"
  exit 0
fi
if [ "${1:-}" = "spy" ]; then  # reads only data/bars_1m/symbol=SPY: no .env needed
  for v in spy_noise_band spy_noise_band_carry; do
    if "$PY" scripts/spy_noise_band.py --has "$v" --level "$LEVEL" --start "$START" --end "$END"; then
      echo "== $v: report exists, skipped"
      continue
    fi
    echo "== $v: running (log: logs/experiment_spy_$v.log)"
    awake "$PY" scripts/spy_noise_band.py --start "$START" --end "$END" --level "$LEVEL" --version "$v" \
      > "logs/experiment_spy_$v.log" 2>&1 \
      || { echo "$v failed: see logs/experiment_spy_$v.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_spy_$v.log"
  done
  echo
  "$PY" scripts/spy_noise_band.py --summary --level "$LEVEL"
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
  echo "usage: $0 [multiday|core|core_pit|spy]   (no argument: the variant A experiments of issue #109)"; exit 2
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
