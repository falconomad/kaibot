#!/usr/bin/env bash
# The research runner's launchd agent on the Mac (docs/ops/research_runner.md).
#   ./scripts/install_runner.sh                      install (or reinstall) com.kaibot.research-runner, every 15 min
#   ./scripts/install_runner.sh --uninstall          stop it and remove the plist (data/runner is kept)
#   ./scripts/install_runner.sh --status             is it loaded, is gh logged in, the last lines of the log
#   ./scripts/install_runner.sh --dry-run --out DIR  write the plist to DIR only (no launchctl; works on Linux)
# Logs: data/runner/runner.log. Never touches com.kaibot.engine, com.kaibot.daily or scripts/service.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
LABEL=com.kaibot.research-runner
ACTION=install; DRY=0; OUT="$HOME/Library/LaunchAgents"
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION=uninstall;; --status) ACTION=status;;
    --dry-run) DRY=1;; --out) OUT="$2"; shift;;
    *) sed -n '2,7p' "$0"; exit 2;;
  esac; shift
done
PLIST="$OUT/$LABEL.plist"
mkdir -p "$REPO/data/runner"
LOG="$REPO/data/runner/runner.log"
say() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) install_runner: $*" | tee -a "$LOG"; }

if [ "$DRY" = 0 ] && [ "$(uname -s)" != Darwin ]; then echo "launchd is macOS only (use --dry-run --out DIR)"; exit 1; fi

case "$ACTION" in
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    say "uninstalled $LABEL (data/runner kept)"; exit 0;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state|last exit|run interval' || echo "$LABEL is not loaded"
    if command -v gh >/dev/null 2>&1 && gh auth status --hostname github.com >/dev/null 2>&1; then
      echo "gh: logged in (results are posted as issue comments)"
    else
      echo "gh: missing or not logged in (results go to data/runner/out/<id>.md)"
    fi
    tail -n 20 "$LOG" 2>/dev/null || true; exit 0;;
esac

if [ "$DRY" = 0 ]; then
  [ -x "$REPO/.venv/bin/python" ] || { echo "No $REPO/.venv/bin/python (the research .venv the scripts already use; see docs/ops/research_runner.md)"; exit 1; }
  "$REPO/.venv/bin/python" -c 'import yaml' 2>/dev/null || { echo ".venv lacks pyyaml: .venv/bin/pip install --require-hashes -r requirements-research.txt"; exit 1; }
fi
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/[|\\]/\\&/g'; }
RUN_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$OUT"
sed -e "s|__REPO__|$(xml "$REPO")|g" -e "s|__HOME__|$(xml "$HOME")|g" -e "s|__PATH__|$(xml "$RUN_PATH")|g" \
  "$REPO/ops/runner/$LABEL.plist" > "$PLIST.tmp"
mv "$PLIST.tmp" "$PLIST"
if [ "$DRY" = 1 ]; then say "wrote $PLIST (dry run, nothing loaded)"; exit 0; fi
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
say "installed $LABEL: every 15 minutes from $REPO"
if command -v gh >/dev/null 2>&1 && gh auth status --hostname github.com >/dev/null 2>&1; then
  say "gh is logged in: results are posted as issue comments"
else
  say "gh missing or not logged in: results go to data/runner/out/<id>.md (brew install gh && gh auth login)"
fi
"$REPO/.venv/bin/python" "$REPO/scripts/research_runner.py" --check --no-pull || say "queue.yaml has jobs that will be rejected (see above)"
