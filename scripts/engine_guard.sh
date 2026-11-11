#!/usr/bin/env bash
# Is a Kaibot engine already running on THIS machine? (CLAUDE.md: never run two engines at once.)
#   scripts/engine_guard.sh           exit 0 if none, 1 if one or more (prints a warning)
#   scripts/engine_guard.sh --list    print one line per engine: "docker <id> <name>", "compose <id> <name>",
#                                     "python <pid>"; same exit codes
# It sees: the run_engine.sh container (name kaibot-engine), a docker compose "engine" service built from the
# kaibot-engine image, and a Python-mode engine ("python -m kaibot.engine" with no arguments). The watchdog and
# "--check" runs are not engines. It cannot see an engine on ANOTHER machine (e.g. a cloud VM): see
# docs/ops/outage_drill.md, "Two engines".
set -uo pipefail
LIST=0; [ "${1:-}" = --list ] && LIST=1
found=()

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  while IFS=$'\t' read -r id name svc; do
    [ -n "${id:-}" ] || continue
    if [ "$name" = kaibot-engine ]; then found+=("docker $id $name")
    elif [ "${svc:-}" = engine ]; then found+=("compose $id $name"); fi
  done < <(docker ps --filter ancestor=kaibot-engine \
             --format '{{.ID}}\t{{.Names}}\t{{.Label "com.docker.compose.service"}}' 2>/dev/null)
fi

# The interpreter itself must be the first word (skips the caffeinate wrapper and the docker CLI) and the
# command must end in "-m kaibot.engine" (skips the watchdog and --check).
while read -r pid; do
  [ -n "$pid" ] && found+=("python $pid")
done < <(ps -axo pid=,command= 2>/dev/null |
           awk '$2 ~ /(^|\/)python[0-9.]*$/ && $0 ~ / -m kaibot\.engine[[:space:]]*$/ {print $1}')

if [ "${#found[@]}" -eq 0 ]; then exit 0; fi
if [ "$LIST" = 1 ]; then printf '%s\n' "${found[@]}"
else
  echo "A Kaibot engine is already running on this machine:" >&2
  printf '  %s\n' "${found[@]}" >&2
  echo "Two engines on one paper account would trade twice. Stop it first (docker stop kaibot-engine," >&2
  echo "docker compose stop engine, or Ctrl-C in its terminal)." >&2
fi
exit 1
