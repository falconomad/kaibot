#!/usr/bin/env bash
# Download the M0 replay history (story S03): 1-minute SIP bars (09:30-16:00 ET only) and Benzinga news headlines,
# 2016 -> now, for the 300-symbol universe in config/universe_history.yaml, plus SEC fundamentals. Resumable: stop with Ctrl-C and
# run it again, finished months are skipped. Keeps the Mac awake while it runs.
#   ./scripts/fetch_history.sh            download (3 parallel workers, roughly 12-18 hours, ~5 GB)
#   ./scripts/fetch_history.sh --status   show progress and exit
# Alpaca allows 200 requests/min per account. This uses 135/min, or 60/min while the engine is running.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
UNIVERSE=config/universe_history.yaml
START="${KAIBOT_HISTORY_START:-2016-01-01}"
WORKERS=3

PY="$(command -v python3.13 || command -v python3.12 || command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Needs Python 3.12+:  brew install python@3.13"; exit 1
fi
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements-engine.txt

if [ "${1:-}" = "--status" ]; then
  .venv/bin/python - "$UNIVERSE" "$START" <<'EOF'
import sys
from datetime import date
from pathlib import Path

import yaml

cfg = yaml.safe_load(Path(sys.argv[1]).read_text())
syms = [cfg["market"], *cfg["sectors"], *[s for v in cfg["sectors"].values() for s in v]]
y, m = map(int, sys.argv[2][:7].split("-"))
t = date.today()
months = (t.year - y) * 12 + t.month - m + 1
root = Path("data/bars_1m")
done = sum(len(list((root / f"symbol={s}").glob("*.parquet"))) + len(list((root / f"symbol={s}").glob("*.empty")))
           for s in syms)
full = sum(1 for s in syms if (root / f"symbol={s}").exists()
           and len(list((root / f"symbol={s}").glob("*.*"))) >= months)
size = sum(f.stat().st_size for f in root.rglob("*.parquet")) / 1e9 if root.exists() else 0
print(f"{done}/{len(syms) * months} symbol-months ({done / (len(syms) * months):.0%}), "
      f"{full}/{len(syms)} symbols complete, {size:.1f} GB")
EOF
  exit 0
fi

[ -f "$ENV_FILE" ] || { echo "No .env found at $ENV_FILE (set KAIBOT_ENV_FILE=/path/to/.env)"; exit 1; }
for k in ALPACA_API_KEY ALPACA_SECRET SEC_USER_AGENT; do
  grep -q "^$k=" "$ENV_FILE" || { echo "Missing $k in $ENV_FILE"; exit 1; }
done
PER_MIN=45
if pgrep -f "kaibot.engine" >/dev/null 2>&1; then
  PER_MIN=20; echo "The engine is running: downloading slowly (60 requests/min) to leave it room."
fi

awake() { if command -v caffeinate >/dev/null 2>&1; then caffeinate -is "$@"; else "$@"; fi; }
mkdir -p logs data
echo "Downloading $START -> now, $WORKERS workers. Logs: logs/fetch_*.log. Progress: ./scripts/fetch_history.sh --status"
pids=()
for i in $(seq 0 $((WORKERS - 1))); do
  awake .venv/bin/python scripts/fetch_history.py --env "$ENV_FILE" --universe "$UNIVERSE" --start "$START" \
    --rth-only --news --shard "$i/$WORKERS" --per-min "$PER_MIN" > "logs/fetch_$i.log" 2>&1 &
  pids+=($!)
done
trap 'kill "${pids[@]}" 2>/dev/null; echo; echo "Stopped. Run again to resume."; exit 130' INT TERM
fail=0
for p in "${pids[@]}"; do wait "$p" || fail=1; done
[ "$fail" = 0 ] || { echo "A worker failed: see logs/fetch_*.log, then run again to resume."; exit 1; }
echo "Bars done. Fetching SEC fundamentals…"
awake .venv/bin/python scripts/fetch_fundamentals.py --env "$ENV_FILE" --universe "$UNIVERSE" > logs/fetch_fundamentals.log 2>&1 \
  || echo "Fundamentals failed: see logs/fetch_fundamentals.log"
./scripts/fetch_history.sh --status
