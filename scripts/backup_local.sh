#!/usr/bin/env bash
# Nightly backup of what the engine keeps on this machine (issue #121). Run by the daily launchd job; safe by hand.
#   ./scripts/backup_local.sh          one archive now, then delete archives older than the retention
# What: data/live (holder id, trial log, running release; any *.sqlite journal copied consistently with sqlite3),
# data/RELEASE and data/release_history.log, the logs (logs/ and ~/Library/Logs/kaibot). Not the price-history
# cache (data/live/history, downloaded again on demand) and never the secrets file.
# Where: $KAIBOT_BACKUP_DIR (default ~/Projects/kaibot-backups), kept $KAIBOT_BACKUP_KEEP_DAYS days (default 30).
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${KAIBOT_BACKUP_DIR:-$HOME/Projects/kaibot-backups}"
KEEP="${KAIBOT_BACKUP_KEEP_DAYS:-30}"
SVC_LOGS="${KAIBOT_LOG_DIR:-$HOME/Library/Logs/kaibot}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/kaibot/data"
if [ -d data/live ]; then
  mkdir -p "$STAGE/kaibot/data/live"
  (cd data/live && find . -path ./history -prune -o -type f ! -name '*.sqlite' ! -name '*.sqlite-*' -print) |
    while read -r f; do mkdir -p "$STAGE/kaibot/data/live/$(dirname "$f")"; cp -p "data/live/$f" "$STAGE/kaibot/data/live/$f"; done
  find data/live -path data/live/history -prune -o -type f -name '*.sqlite' -print | while read -r db; do
    out="$STAGE/kaibot/$db"; mkdir -p "$(dirname "$out")"
    if command -v sqlite3 >/dev/null 2>&1; then sqlite3 "$db" ".backup '$out'"; else cp -p "$db" "$out"; fi
  done
fi
for f in data/RELEASE data/release_history.log; do [ -f "$f" ] && cp -p "$f" "$STAGE/kaibot/$f"; done
[ -d logs ] && cp -pR logs "$STAGE/kaibot/logs"
[ -d "$SVC_LOGS" ] && cp -pR "$SVC_LOGS" "$STAGE/kaibot/service-logs"

ARCHIVE="$DEST/kaibot-$STAMP.tar.gz"
tar -czf "$ARCHIVE.part" -C "$STAGE" kaibot && mv "$ARCHIVE.part" "$ARCHIVE"
find "$DEST" -maxdepth 1 -name 'kaibot-*.tar.gz' -type f -mtime +"$KEEP" -print -delete | sed 's/^/deleted old backup: /'
echo "backup: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1)); $(find "$DEST" -maxdepth 1 -name 'kaibot-*.tar.gz' | wc -l | tr -d ' ') kept"
