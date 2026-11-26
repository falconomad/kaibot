#!/usr/bin/env bash
# Nightly backup of what exists only on this Mac (issue #121; audit 2 ticket 7.5, #715). Run by the daily launchd
# job; safe by hand.
#   ./scripts/backup_local.sh          one archive now, its encrypted off-box copy if one is set up, then prune
#   ./scripts/backup_local.sh --setup  print the one-time commands for the encrypted off-box copy (changes nothing)
# What (docs/ops/backup_restore.md):
# - the engine: data/live (holder id, trial log, running release; any *.sqlite journal copied consistently with
#   sqlite3), data/RELEASE and data/release_history.log;
# - the research stores in data/: fundamentals/ (without raw/, the download cache that --build re-reads and a fetch
#   downloads again), listings/, reports/, alarm/ and trials.jsonl (the gate's trial count reads it);
# - the research runner's state: <runner checkout>/data/runner/done.json (lost, every queued job runs again);
# - the logs (logs/ and ~/Library/Logs/kaibot);
# - MANIFEST.sha256, one line per file, which scripts/restore_drill.sh checks.
# Not the price-history cache (data/live/history, data/daily: downloaded again) and never a secrets file.
# Where: $KAIBOT_BACKUP_DIR (default ~/Projects/kaibot-backups), kept $KAIBOT_BACKUP_KEEP_DAYS days (default 30).
# Off-box (optional, encrypted, never in plain): $KAIBOT_BACKUP_OFFBOX_DIR (iCloud Drive, an external disk), with
# an age recipients file ($KAIBOT_BACKUP_AGE_RECIPIENTS, default ~/Projects/kaibot-secrets/backup_age_recipients.txt;
# a public key only) or, without one, an openssl passphrase file ($KAIBOT_BACKUP_PASSFILE, default
# ~/Projects/kaibot-secrets/backup.pass); kept $KAIBOT_BACKUP_OFFBOX_KEEP_DAYS days (default 14). The off-box
# settings come from the environment, else the .env ($KAIBOT_ENV_FILE), as the service reads its settings. Neither
# key may sit inside a checkout. When the off-box copy is set up and cannot be written, this exits 1 (the daily job
# then sends a Healthchecks fail); the local archive is kept either way.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd -P)"
ENV_FILE="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
RUNNER="${KAIBOT_RUNNER_CHECKOUT:-$HOME/Projects/kaibot-runner}"

# A setting: the environment when set there, else the .env, else the default (service_run.sh's `setting`); a
# leading ~/ is the home folder.
setting() {  # setting NAME DEFAULT
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
KEEP="$(setting KAIBOT_BACKUP_KEEP_DAYS 30)"
OFFBOX="$(setting KAIBOT_BACKUP_OFFBOX_DIR "")"
OFFBOX_KEEP="$(setting KAIBOT_BACKUP_OFFBOX_KEEP_DAYS 14)"
AGE_RCPT="$(setting KAIBOT_BACKUP_AGE_RECIPIENTS "$HOME/Projects/kaibot-secrets/backup_age_recipients.txt")"
PASSFILE="$(setting KAIBOT_BACKUP_PASSFILE "$HOME/Projects/kaibot-secrets/backup.pass")"
SVC_LOGS="${KAIBOT_LOG_DIR:-$HOME/Library/Logs/kaibot}"

if [ "${1:-}" = "--setup" ]; then
  cat <<EOF
One-time setup of the encrypted off-box copy (docs/ops/backup_restore.md). Nothing here was run.

1. A key pair with age (recommended: only the PUBLIC key stays on this Mac):
     brew install age
     mkdir -p ~/Projects/kaibot-secrets && chmod 700 ~/Projects/kaibot-secrets
     age-keygen -o /tmp/kaibot-backup.agekey        # prints "Public key: age1..."
     age-keygen -y /tmp/kaibot-backup.agekey > ~/Projects/kaibot-secrets/backup_age_recipients.txt
   Put the whole of /tmp/kaibot-backup.agekey (the line AGE-SECRET-KEY-1...) in your password manager AND on a USB
   stick kept away from the Mac, then delete it here:
     rm -P /tmp/kaibot-backup.agekey
   Without age: an openssl passphrase (then the key does stay on the Mac; keep a second copy off it too):
     openssl rand -base64 48 > ~/Projects/kaibot-secrets/backup.pass && chmod 600 ~/Projects/kaibot-secrets/backup.pass
2. Choose the folder and add it to $ENV_FILE (one line; quotes because of the spaces):
     iCloud Drive:  KAIBOT_BACKUP_OFFBOX_DIR="~/Library/Mobile Documents/com~apple~CloudDocs/kaibot-backups"
     external disk: KAIBOT_BACKUP_OFFBOX_DIR="/Volumes/<disk name>/kaibot-backups"
   Optional: KAIBOT_BACKUP_OFFBOX_KEEP_DAYS=14 (iCloud's free 5 GB fills up at about 30 copies of a large data/).
3. Once by hand in Terminal (macOS may ask to allow access to iCloud Drive or the disk; allow it):
     ./scripts/backup_local.sh
4. The restore drill, with the key from the USB stick (the encrypted copy, into a temporary folder):
     ./scripts/restore_drill.sh --offbox --identity /Volumes/<usb>/kaibot-backup.agekey
5. A second copy of the secrets off the Mac (they are never in a backup): docs/ops/backup_restore.md, "Secrets".

Now: off-box folder ${OFFBOX:-(not set)}; age recipients ${AGE_RCPT} ($( [ -f "$AGE_RCPT" ] && echo present || echo missing));
passphrase file ${PASSFILE} ($( [ -f "$PASSFILE" ] && echo present || echo missing)); age $(command -v age >/dev/null 2>&1 && echo installed || echo "not installed").
EOF
  exit 0
fi

inside() {  # inside PATH DIR: PATH is DIR or below it (both made absolute; a missing PATH uses its folder)
  local p="$1" d="$2" pd
  [ -d "$d" ] || return 1
  d="$(cd "$d" && pwd -P)"
  pd="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)" || return 1
  case "$pd/" in "$d"/*) return 0 ;; esac
  return 1
}

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
B="$STAGE/kaibot"
mkdir -p "$B/data"

# the engine
if [ -d data/live ]; then
  mkdir -p "$B/data/live"
  (cd data/live && find . -path ./history -prune -o -type f ! -name '*.sqlite' ! -name '*.sqlite-*' -print) |
    while read -r f; do mkdir -p "$B/data/live/$(dirname "$f")"; cp -p "data/live/$f" "$B/data/live/$f"; done
  find data/live -path data/live/history -prune -o -type f -name '*.sqlite' -print | while read -r db; do
    out="$B/$db"; mkdir -p "$(dirname "$out")"
    if command -v sqlite3 >/dev/null 2>&1; then sqlite3 "$db" ".backup '$out'"; else cp -p "$db" "$out"; fi
  done
fi
for f in data/RELEASE data/release_history.log; do [ -f "$f" ] && cp -p "$f" "$B/$f"; done

# the research stores (audit 2 7.5)
for d in fundamentals listings reports alarm; do
  [ -d "data/$d" ] || continue
  (cd data && find "$d" -path "fundamentals/raw" -prune -o -type f -print) | while read -r f; do
    mkdir -p "$B/data/$(dirname "$f")"; cp -p "data/$f" "$B/data/$f"
  done
done
[ -f data/trials.jsonl ] && cp -p data/trials.jsonl "$B/data/trials.jsonl"

# the research runner's state
if [ -f "$RUNNER/data/runner/done.json" ]; then
  mkdir -p "$B/runner/data/runner"; cp -p "$RUNNER/data/runner/done.json" "$B/runner/data/runner/done.json"
fi

[ -d logs ] && cp -pR logs "$B/logs"
[ -d "$SVC_LOGS" ] && cp -pR "$SVC_LOGS" "$B/service-logs"

# the manifest: sha256 of every file, as `shasum -a 256` / `sha256sum` print it
if command -v shasum >/dev/null 2>&1; then SHA=(shasum -a 256); else SHA=(sha256sum); fi
(cd "$B" && find . -type f ! -name MANIFEST.sha256 | LC_ALL=C sort | sed 's|^\./||' |
  while read -r f; do "${SHA[@]}" "$f"; done) > "$STAGE/MANIFEST.sha256"
mv "$STAGE/MANIFEST.sha256" "$B/MANIFEST.sha256"

ARCHIVE="$DEST/kaibot-$STAMP.tar.gz"
# COPYFILE_DISABLE: macOS tar would add ._* files for extended attributes, which the manifest does not list
COPYFILE_DISABLE=1 tar -czf "$ARCHIVE.part" -C "$STAGE" kaibot && mv "$ARCHIVE.part" "$ARCHIVE"
find "$DEST" -maxdepth 1 -name 'kaibot-*.tar.gz' -type f -mtime +"$KEEP" -print -delete | sed 's/^/deleted old backup: /'
echo "backup: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1), $(($(wc -l < "$B/MANIFEST.sha256"))) files);" \
     "$(find "$DEST" -maxdepth 1 -name 'kaibot-*.tar.gz' | wc -l | tr -d ' ') kept"

# the encrypted off-box copy
if [ -z "$OFFBOX" ]; then
  echo "off-box copy: not set up (./scripts/backup_local.sh --setup)"
  exit 0
fi
offbox_fail() { echo "off-box copy FAILED: $*" >&2; exit 1; }
for k in "$AGE_RCPT" "$PASSFILE"; do
  for c in "$REPO" "$RUNNER"; do
    if [ -f "$k" ] && inside "$k" "$c"; then offbox_fail "the key file $k is inside the checkout $c: move it out"; fi
  done
done
for c in "$REPO" "$RUNNER"; do
  if inside "$OFFBOX/x" "$c"; then offbox_fail "the off-box folder $OFFBOX is inside the checkout $c"; fi
done
mkdir -p "$OFFBOX" 2>/dev/null || offbox_fail "cannot make $OFFBOX (disk not mounted, or no access: docs/ops/backup_restore.md)"
if [ -f "$AGE_RCPT" ]; then
  command -v age >/dev/null 2>&1 || offbox_fail "$AGE_RCPT is set up but age is not installed (brew install age)"
  OUT="$OFFBOX/kaibot-$STAMP.tar.gz.age"
  age -R "$AGE_RCPT" -o "$OUT.part" "$ARCHIVE" || { rm -f "$OUT.part"; offbox_fail "age could not encrypt"; }
elif [ -f "$PASSFILE" ]; then
  OUT="$OFFBOX/kaibot-$STAMP.tar.gz.enc"
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -md sha256 -pass "file:$PASSFILE" -in "$ARCHIVE" \
    -out "$OUT.part" || { rm -f "$OUT.part"; offbox_fail "openssl could not encrypt"; }
else
  offbox_fail "no key: neither $AGE_RCPT nor $PASSFILE exists (./scripts/backup_local.sh --setup); nothing is copied in plain"
fi
mv "$OUT.part" "$OUT" || offbox_fail "cannot write $OUT"
find "$OFFBOX" -maxdepth 1 -type f \( -name 'kaibot-*.tar.gz.age' -o -name 'kaibot-*.tar.gz.enc' \) \
  -mtime +"$OFFBOX_KEEP" -print -delete | sed 's/^/deleted old off-box copy: /'
echo "off-box copy: $OUT ($(du -h "$OUT" | cut -f1))"
