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
#       Since #203 the exit-before-earnings variant of core_pit uses the SEC point-in-time dates of
#       data/earnings/announcements.parquet (./scripts/fetch_earnings_dates.sh) when they meet the pre-declared
#       reliability bar (else the headline estimate as before); a core_pit report from before the file existed
#       is run again once it does.
#   ./scripts/run_experiments.sh spy         issue #128: the SPY noise-band rule (long only, 1x) and its overnight
#       carry, each ONE continuous account from 2017-05 on SPY minute bars, with cost scenarios, neighbours, the
#       15:30 check and the live last-30-minutes trade beside it, then ONE table written to
#       data/reports/experiments_spy_summary.md. Pre-registered in docs/prereg/spy_noise_band.md. Needs no .env.
#       A few minutes (see docs/m0/reproduce.md section 8). Since #149 each version also runs Gate B mark 3, the
#       random-timing control of decision 15 section 10 (500 seeded runs by default, about a minute per version on
#       synthetic bars of the real size; KAIBOT_SPY_CONTROL_RUNS changes it, and a report from before #149 or with a
#       different number of runs is run again), printed under the table.
#   ./scripts/run_experiments.sh spy_engine  issue #202: the Gate B go/no-go of spy_noise_band (decision 15): the
#       frozen rule through the ENGINE with SimBroker + SimClock on SPY minute bars, 2017-05-01 to the end, on cost
#       model v2 and at the prereg's 0.5 / 1 bp, plus the five neighbours (11 engine runs, KAIBOT_SPY_ENGINE_JOBS at a
#       time, default half the CPUs), the random-timing control (KAIBOT_SPY_CONTROL_RUNS, 500), then ONE page with
#       decision 15's Gate B marks, the prereg's marks and the harness side by side, written to
#       data/reports/experiments_spy_engine.md. Runs the harness's spy_noise_band first if its report is missing.
#       Needs no .env; uses data/daily (SPY total return) and data/inputs/fred_vixcls.json when present.
#       About 40 minutes of CPU per engine run (measured on synthetic bars of the real size): allow 1 to 1.5
#       hours on an 8-10 core Mac with the default jobs (an estimate), 7-8 hours on one core.
#   ./scripts/run_experiments.sh oneshots    issue #112: the six one-shot tests of decision 15 (candidate 5), each ONE
#       frozen pre-registered run (docs/prereg/): orb_stocks_in_play (keep or delete) and news_continuation on
#       today's list with minute bars, then pullback_uptrend, ath_breakout (a distribution over random signal
#       subsets at 5, 10 and 20 positions), post_earnings_week (needs data/earnings) and weekly_fall_rebound on
#       the point-in-time S&P 500 and the daily store; ONE table with pass / fail per rule written to
#       data/reports/experiments_oneshots_summary.md. A rule whose data is missing is skipped with a message and
#       tried again next time. KAIBOT_ONESHOT_CONTROL_RUNS (500) and KAIBOT_ONESHOT_SEEDS (200) change the control
#       and the subsets (a different number is a new report). Times: docs/m0/reproduce.md section 10. No .env.
#   ./scripts/run_experiments.sh spy_trend   issue #201: the SPY trend filter (hold SPY above its 10-month average,
#       else cash; month-end check, filled at the next close), its five neighbours, cost models v1 and v2, the
#       random-timing control of Gate B mark 3 (500 runs per cost model; KAIBOT_SPY_TREND_CONTROL_RUNS changes it,
#       and a different number is a new report) and Gate B per cost model, ONE continuous account each from
#       2017-05, then ONE table written to data/reports/experiments_spy_trend_summary.md. Pre-registered in
#       docs/prereg/spy_trend_filter.md. Reads only SPY (and BIL, if present, for the T-bill row) from the daily
#       store data/daily: no .env. A few minutes.
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
# The locked research set (engine + scipy, uv.lock export, #122); already-installed pins make this a no-op.
.venv/bin/pip install -q --disable-pip-version-check --require-hashes -r requirements-research.txt
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -m certifi)}"  # macOS python.org builds lack root certs

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
mkdir -p logs data/reports

if [ "${1:-}" = "oneshots" ]; then  # reads data/bars_1m, data/news, data/daily, data/earnings: no .env needed
  RUNS="${KAIBOT_ONESHOT_CONTROL_RUNS:-500}"
  SEEDS="${KAIBOT_ONESHOT_SEEDS:-200}"
  for r in orb_stocks_in_play news_continuation pullback_uptrend ath_breakout post_earnings_week \
      weekly_fall_rebound; do
    if "$PY" scripts/oneshots.py --has "$r" --start "$START" --end "$END" --universe-file "$UNIVERSE" \
        --control-runs "$RUNS" --seeds "$SEEDS"; then
      echo "== $r: report exists, skipped"
      continue
    fi
    echo "== $r: running (log: logs/experiment_oneshot_$r.log)"
    set +e
    awake "$PY" scripts/oneshots.py --rule "$r" --start "$START" --end "$END" --universe-file "$UNIVERSE" \
      --level "$LEVEL" --control-runs "$RUNS" --seeds "$SEEDS" > "logs/experiment_oneshot_$r.log" 2>&1
    rc=$?
    set -e
    if [ "$rc" = 3 ]; then
      tail -n 1 "logs/experiment_oneshot_$r.log"  # data missing: the message says which; tried again next time
      continue
    elif [ "$rc" != 0 ]; then
      echo "$r failed: see logs/experiment_oneshot_$r.log, then run this again (finished ones are skipped)"
      exit 1
    fi
    tail -n 1 "logs/experiment_oneshot_$r.log"
  done
  echo
  "$PY" scripts/oneshots.py --summary
  exit 0
fi
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
if [ "${1:-}" = "spy_trend" ]; then  # reads only data/daily (SPY, and BIL when present): no .env needed
  RUNS="${KAIBOT_SPY_TREND_CONTROL_RUNS:-500}"
  [ -f data/daily/bars/symbol=SPY.parquet ] \
    || { echo "No SPY in the daily store at data/daily: run ./scripts/fetch_daily.sh first (docs/m0/reproduce.md)"; exit 1; }
  if "$PY" scripts/spy_trend.py --has --start "$START" --end "$END" --control-runs "$RUNS"; then
    echo "== spy_trend_filter: report exists, skipped"
  else
    echo "== spy_trend_filter: running (log: logs/experiment_spy_trend.log)"
    awake "$PY" scripts/spy_trend.py --start "$START" --end "$END" --control-runs "$RUNS" \
      > logs/experiment_spy_trend.log 2>&1 \
      || { echo "spy_trend failed: see logs/experiment_spy_trend.log, then run this again"; exit 1; }
    tail -n 1 logs/experiment_spy_trend.log
  fi
  echo
  "$PY" scripts/spy_trend.py --summary
  exit 0
fi
if [ "${1:-}" = "spy" ]; then  # reads only data/bars_1m/symbol=SPY: no .env needed
  RUNS="${KAIBOT_SPY_CONTROL_RUNS:-500}"
  for v in spy_noise_band spy_noise_band_carry; do
    if "$PY" scripts/spy_noise_band.py --has "$v" --level "$LEVEL" --start "$START" --end "$END" \
        --control-runs "$RUNS"; then
      echo "== $v: report exists, skipped"
      continue
    fi
    echo "== $v: running (log: logs/experiment_spy_$v.log)"
    awake "$PY" scripts/spy_noise_band.py --start "$START" --end "$END" --level "$LEVEL" --version "$v" \
      --control-runs "$RUNS" > "logs/experiment_spy_$v.log" 2>&1 \
      || { echo "$v failed: see logs/experiment_spy_$v.log, then run this again (finished ones are skipped)"
           exit 1; }
    tail -n 1 "logs/experiment_spy_$v.log"
  done
  echo
  "$PY" scripts/spy_noise_band.py --summary --level "$LEVEL"
  exit 0
fi
if [ "${1:-}" = "spy_engine" ]; then  # issue #202: reads data/bars_1m/symbol=SPY (+ data/daily, VIX cache): no .env
  RUNS="${KAIBOT_SPY_CONTROL_RUNS:-500}"
  JOBS="${KAIBOT_SPY_ENGINE_JOBS:-$("$PY" -c 'import os; print(max(1, (os.cpu_count() or 2) // 2))')}"
  # the harness numbers for the side-by-side (a few minutes; skipped when its report exists)
  if ! "$PY" scripts/spy_noise_band.py --has spy_noise_band --level "$LEVEL" --start "$START" --end "$END" \
      --control-runs "$RUNS"; then
    echo "== harness spy_noise_band: running (log: logs/experiment_spy_spy_noise_band.log)"
    awake "$PY" scripts/spy_noise_band.py --start "$START" --end "$END" --level "$LEVEL" --version spy_noise_band \
      --control-runs "$RUNS" > logs/experiment_spy_spy_noise_band.log 2>&1 \
      || { echo "harness run failed: see logs/experiment_spy_spy_noise_band.log"; exit 1; }
  fi
  if "$PY" scripts/spy_engine.py --has --level "$LEVEL" --start "$START" --end "$END" --control-runs "$RUNS"; then
    echo "== spy_engine: report exists, skipped"
  else
    echo "== spy_engine: 11 engine runs, $JOBS at a time (log: logs/experiment_spy_engine.log)"
    awake "$PY" scripts/spy_engine.py --start "$START" --end "$END" --level "$LEVEL" --control-runs "$RUNS" \
      --jobs "$JOBS" > logs/experiment_spy_engine.log 2>&1 \
      || { echo "spy_engine failed: see logs/experiment_spy_engine.log, then run this again"; exit 1; }
  fi
  echo
  "$PY" scripts/spy_engine.py --summary --level "$LEVEL"
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
  echo "usage: $0 [multiday|core|core_pit|spy|spy_trend|oneshots]   (no argument: the variant A experiments of issue #109)"; exit 2
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
