#!/usr/bin/env bash
# S40 outage drill on this machine, PAPER ONLY: kill the running engine, watch the fail-safes notice, restart it
# with run_engine.sh and time how long it takes to be healthy again. Runbook: docs/ops/outage_drill.md.
#
#   ./scripts/outage_drill.sh --dry-run        checks + plan only; stops nothing (read-only heartbeat select)
#   ./scripts/outage_drill.sh                  the real drill (outside 09:30-16:00 ET)
#   ./scripts/outage_drill.sh --during-session the real drill while the market is open (loud warning)
# Options: --graceful   stop with SIGTERM instead of a crash (SIGKILL)
#          --no-wait    restart at once instead of waiting for the watchdog / a stale heartbeat
#          --timeout S  give up waiting for a healthy heartbeat after S seconds (default 600)
# PASS: restart -> fresh heartbeat (<= 30 s old, newer than the restart) with account_clean true, in < 300 s.
# Exit codes: 0 PASS (or dry run OK), 1 FAIL, 2 refused.
set -euo pipefail
cd "$(dirname "$0")/.."

PAPER_URL="https://paper-api.alpaca.markets"
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
DRY=0; DURING=0; SIG=KILL; WAIT=1; TIMEOUT=600; PASS_S=300; FRESH_S=30; STALE_S=90
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1;; --during-session) DURING=1;; --graceful) SIG=TERM;; --no-wait) WAIT=0;;
    --timeout) TIMEOUT="${2:?--timeout needs seconds}"; shift;;
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
  shift
done

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
refuse() { echo "REFUSED: $*" >&2; exit 2; }

# Read a setting the way kaibot.engine.config does: the environment wins, then the first KEY= line of the .env.
env_get() {
  local k="$1"
  if [ -n "${!k:-}" ]; then printf '%s' "${!k}"; return 0; fi
  [ -f "$ENV_FILE" ] || return 0
  python3 - "$ENV_FILE" "$k" <<'PY'
import sys
path, key = sys.argv[1:]
for line in open(path, encoding="utf-8"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        if k.strip() == key:
            print(v.split(" #")[0].strip().strip('"').strip("'"), end="")
            break
PY
}

# ---------- 1. paper only ----------
BASE="$(env_get ALPACA_BASE_URL)"; BASE="${BASE:-$PAPER_URL}"; BASE="${BASE%/}"
[ "$BASE" = "$PAPER_URL" ] || refuse "ALPACA_BASE_URL is '$BASE', not the paper endpoint $PAPER_URL. Paper only."
say "broker: $PAPER_URL (paper)"

# ---------- 2. not during the session unless asked ----------
DOW="$(TZ=America/New_York date +%u)"; HM="$(TZ=America/New_York date +%H%M)"
if [ "$DOW" -le 5 ] && [ $((10#$HM)) -ge 930 ] && [ $((10#$HM)) -lt 1600 ]; then
  IN_SESSION=1
  [ "$DURING" = 1 ] || refuse "it is $(TZ=America/New_York date +%H:%M) ET on a weekday (09:30-16:00 is the session). \
Run the drill after the close, or pass --during-session if you really mean it."
  cat <<'EOF'
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!  DRILL DURING THE SESSION (paper account). The engine will be killed while the  !!
!!  market is open: the watchdog will CANCEL unfilled entry orders, open positions   !!
!!  keep only their broker-held stops/targets, and no new entries happen until the  !!
!!  restart. Do not do this after 15:50 ET (15:55 flatten, 15:56 dead-man).        !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
  [ "$((10#$HM))" -lt 1550 ] || refuse "too close to the 15:55 flatten / 15:56 dead-man."
  if [ "$DRY" = 0 ]; then say "starting in 10 s: Ctrl-C to abort"; sleep 10; fi
else
  IN_SESSION=0
fi

# ---------- 3. Supabase heartbeat (read-only select; keys never printed or put on a command line) ----------
SB_URL="$(env_get SUPABASE_URL)"
[ -n "$SB_URL" ] || SB_URL="$(PYTHONPATH=src python3 -c 'from kaibot.engine.config import DEFAULT_SUPABASE_URL as u; print(u)')"
SB_URL="${SB_URL%/}"
SB_KEY="$(env_get SUPABASE_SERVICE_KEY)"; [ -n "$SB_KEY" ] || SB_KEY="$(env_get SUPABASE_SERVICE_ROLE_KEY)"
[ -n "$SB_KEY" ] || refuse "no SUPABASE_SERVICE_KEY in the environment or $ENV_FILE"
PROC="$(env_get KAIBOT_ENGINE_NAME)"; PROC="${PROC:-engine}"

# Prints "<age_s> <ts_epoch> <account_clean> <state>" or "none".
heartbeat() {
  local body
  body="$(printf 'header = "apikey: %s"\nheader = "Authorization: Bearer %s"\n' "$SB_KEY" "$SB_KEY" |
          curl -sS --max-time 10 -K - -H "Accept-Profile: kaibot" \
            "$SB_URL/rest/v1/heartbeat?process=eq.$PROC&select=ts,detail")" || { echo "none"; return 0; }
  printf '%s' "$body" | python3 -c '
import json, sys
from datetime import UTC, datetime
try:
    rows = json.load(sys.stdin)
    r = rows[0]
    ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
    d = r.get("detail") or {}
    clean = {True: "true", False: "false"}.get(d.get("account_clean"), "unknown")
    print(round((datetime.now(UTC) - ts).total_seconds(), 1), int(ts.timestamp()), clean, d.get("state", "?"))
except Exception:
    print("none")'
}

# ---------- 4. exactly one engine on this machine ----------
ENGINES="$(./scripts/engine_guard.sh --list || true)"
KIND=""; ID=""
if grep -q '^compose ' <<<"$ENGINES"; then KIND=compose; ID="$(grep '^compose ' <<<"$ENGINES" | head -1 | cut -d' ' -f2)"
elif grep -q '^docker ' <<<"$ENGINES"; then KIND=docker; ID=kaibot-engine
elif grep -q '^python ' <<<"$ENGINES"; then KIND=python; ID="$(grep '^python ' <<<"$ENGINES" | head -1 | cut -d' ' -f2)"; fi
N_CONTAINERS="$(grep -cE '^(docker|compose) ' <<<"$ENGINES" || true)"
N_PY="$(grep -c '^python ' <<<"$ENGINES" || true)"
# On Linux a container's processes are also visible to ps; on the Mac (Docker Desktop VM) they are not.
if [ "$N_CONTAINERS" -gt 1 ] || { [ "$N_CONTAINERS" = 0 ] && [ "$N_PY" -gt 1 ]; }; then
  printf '%s\n' "$ENGINES" >&2; refuse "more than one engine is running. Stop the extra one first."
fi
if [ -z "$KIND" ]; then
  [ "$DRY" = 1 ] || refuse "no engine is running here. Start it with ./scripts/run_engine.sh, then run the drill."
  say "no engine running (dry run: continuing with the plan for Python mode)"; KIND=python; ID="<pid>"
fi
say "engine: $KIND $ID"

read -r AGE _ CLEAN STATE <<<"$(heartbeat)" || true
if [ "${AGE:-none}" = none ]; then
  [ "$DRY" = 1 ] || refuse "cannot read the $PROC heartbeat from Supabase."
  say "heartbeat: could not read it (dry run: continuing)"
else
  say "heartbeat before: ${AGE} s old, state=$STATE, account_clean=$CLEAN"
  if [ "$DRY" = 0 ] && { [ "${AGE%.*}" -gt "$FRESH_S" ] || [ "$CLEAN" != true ]; }; then
    refuse "the engine is not healthy before the drill (fix that first; see docs/ops/outage_drill.md)."
  fi
fi

mkdir -p logs
case "$KIND" in
  docker)  STOP=(docker kill --signal "$SIG" kaibot-engine); RESTART=(./scripts/run_engine.sh --docker);;
  compose) STOP=(docker compose kill -s "$SIG" engine); RESTART=(docker compose start engine);;
  python)  STOP=(kill "-$SIG" "$ID"); RESTART=(./scripts/run_engine.sh --python);;
esac

if [ "$DRY" = 1 ]; then
  echo "Plan (dry run, nothing is stopped):"
  echo "  T0 stop the engine:        ${STOP[*]}"
  if [ "$IN_SESSION" = 1 ]; then echo "  wait for the watchdog:     'engine heartbeat ... s old' in its log (~105 s)"
  else echo "  wait for a stale beat:     heartbeat older than ${STALE_S} s (watchdog idles outside the session)"; fi
  if [ "$KIND" = compose ]; then echo "  T1 restart:                ${RESTART[*]}"
  else echo "  T1 restart:                nohup ${RESTART[*]} >> logs/engine.log 2>&1 &"; fi
  echo "  poll the heartbeat until:  <= ${FRESH_S} s old, newer than T1, account_clean=true (timeout ${TIMEOUT} s)"
  echo "  PASS if T1 -> healthy < ${PASS_S} s"
  exit 0
fi

# ---------- 5. T0: stop the engine ----------
T0="$(date +%s)"; WD_OFF=0; [ -f logs/watchdog.log ] && WD_OFF="$(wc -c < logs/watchdog.log | tr -d ' ')"
say "T0: stopping the engine (${STOP[*]})"
"${STOP[@]}" >/dev/null
for _ in $(seq 1 30); do ./scripts/engine_guard.sh >/dev/null 2>&1 && break; sleep 1; done
./scripts/engine_guard.sh || { echo "FAIL: the engine did not stop" >&2; exit 1; }
if [ "$KIND" = docker ]; then  # run_engine.sh starts it with --rm: wait until the name is free again
  for _ in $(seq 1 30); do [ -z "$(docker ps -aq -f name=^kaibot-engine$)" ] && break; sleep 1; done
fi
say "engine stopped"

# ---------- 6. wait for the fail-safes to notice ----------
watchdog_saw_it() {
  case "$KIND" in
    python) [ -f logs/watchdog.log ] && tail -c "+$((WD_OFF + 1))" logs/watchdog.log | grep -q "engine heartbeat .* s old";;
    docker) docker logs --since "$T0" kaibot-watchdog 2>&1 | grep -q "engine heartbeat .* s old";;
    compose) docker compose logs --no-log-prefix --since "$T0" watchdog 2>&1 | grep -q "engine heartbeat .* s old";;
  esac
}
if [ "$WAIT" = 1 ]; then
  if [ "$IN_SESSION" = 1 ]; then
    say "waiting for the watchdog to log the stale heartbeat (normally ~105 s)"
    seen=0
    for _ in $(seq 1 48); do watchdog_saw_it && { seen=1; break; }; sleep 5; done
    if [ "$seen" = 1 ]; then say "watchdog: stale heartbeat seen ($(( $(date +%s) - T0 )) s after T0)"
    else say "WARNING: no watchdog stale log within 240 s: is the watchdog running? (see the runbook)"; fi
  else
    say "market closed: the watchdog only acts in the session; waiting for the heartbeat to go stale (> ${STALE_S} s)"
    for _ in $(seq 1 60); do
      read -r AGE _ _ _ <<<"$(heartbeat)" || true
      [ "${AGE:-none}" != none ] && [ "${AGE%.*}" -gt "$STALE_S" ] && break
      sleep 5
    done
    say "heartbeat ${AGE:-?} s old: the dashboard now shows Engine offline"
  fi
fi

# ---------- 7. T1: restart ----------
T1="$(date +%s)"
say "T1: restarting (${RESTART[*]}) after $((T1 - T0)) s down"
if [ "$KIND" = compose ]; then "${RESTART[@]}" >/dev/null
else nohup "${RESTART[@]}" >> logs/engine.log 2>&1 & echo "  engine log: logs/engine.log (pid $!)"; fi

# ---------- 8. poll until healthy ----------
T2=""
while [ $(( $(date +%s) - T1 )) -lt "$TIMEOUT" ]; do
  read -r AGE TS CLEAN STATE <<<"$(heartbeat)" || true
  if [ "${AGE:-none}" != none ]; then
    printf '  +%3ss heartbeat %ss old, state=%s, account_clean=%s\n' "$(( $(date +%s) - T1 ))" "$AGE" "$STATE" "$CLEAN"
    if [ "$TS" -ge "$T1" ] && [ "${AGE%.*}" -le "$FRESH_S" ] && [ "$CLEAN" = true ]; then T2="$(date +%s)"; break; fi
  fi
  sleep 5
done
N_AFTER="$(./scripts/engine_guard.sh --list | grep -cE '^(docker|compose|python) ' || true)"

echo
echo "Outage drill $(date -u +%Y-%m-%dT%H:%M:%SZ) ($KIND mode, SIG$SIG, $([ "$IN_SESSION" = 1 ] && echo in || echo outside) session)"
echo "  down (T0 -> T1):      $((T1 - T0)) s"
if [ -z "$T2" ]; then
  echo "  recovery (T1 -> ok):  none within ${TIMEOUT} s"
  echo "FAIL: no fresh heartbeat with account_clean=true. See docs/ops/outage_drill.md, 'On FAIL'."; exit 1
fi
echo "  recovery (T1 -> ok):  $((T2 - T1)) s   (pass < ${PASS_S} s)"
[ "$N_AFTER" -ge 1 ] || say "WARNING: no engine process seen after the restart"
if [ $((T2 - T1)) -lt "$PASS_S" ]; then echo "PASS"; exit 0; fi
echo "FAIL: recovery took $((T2 - T1)) s. See docs/ops/outage_drill.md, 'On FAIL'."; exit 1
