#!/usr/bin/env bash
# Restore drill (audit 2 ticket 7.5, #715): rebuild the engine and the research runner's state from a backup in a
# temporary folder and check them (scripts/restore_drill.py; docs/ops/backup_restore.md). Read-only towards the
# live install: no engine, watchdog or runner is started, nothing in this checkout's data/ is written.
#   ./scripts/restore_drill.sh                                   the newest local archive
#   ./scripts/restore_drill.sh --offbox --identity <age key>     the newest encrypted off-box copy
#   ./scripts/restore_drill.sh --archive <file> [--passfile F] [--keep] [--into DIR] [--no-code]
# The folders come from KAIBOT_BACKUP_DIR and KAIBOT_BACKUP_OFFBOX_DIR (the environment, else the .env), as
# backup_local.sh reads them. Exit 0: every check passed; 1: one failed; 2: usage.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
setting() {  # as backup_local.sh: the environment, else the .env, else the default; ~/ is the home folder
  local name="$1" def="$2" line v
  if [ -n "${!name+x}" ]; then
    v="${!name}"
  else
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$name=" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
    v="${line#*=}"
    v="${v%% \#*}"
    v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  fi
  [ -n "$v" ] || v="$def"
  case "$v" in "~/"*) v="$HOME/${v#\~/}" ;; esac
  printf '%s\n' "$v"
}
DEST="$(setting KAIBOT_BACKUP_DIR "$HOME/Projects/kaibot-backups")"
OFFBOX="$(setting KAIBOT_BACKUP_OFFBOX_DIR "")"
PASSFILE="$(setting KAIBOT_BACKUP_PASSFILE "$HOME/Projects/kaibot-secrets/backup.pass")"
py=.venv/bin/python; [ -x "$py" ] || py=python3
args=(--backup-dir "$DEST" --passfile "$PASSFILE")
[ -n "$OFFBOX" ] && args+=(--offbox-dir "$OFFBOX")
exec "$py" scripts/restore_drill.py "${args[@]}" "$@"
