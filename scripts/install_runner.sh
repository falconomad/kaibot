#!/usr/bin/env bash
# The research runner's launchd agent on the Mac (docs/ops/research_runner.md), in its OWN checkout.
#   ./scripts/install_runner.sh                      set up the runner's checkout and install (or reinstall)
#                                                    com.kaibot.research-runner, every 15 min; an install that ran in
#                                                    the engine's checkout is moved (its state is copied over)
#   ./scripts/install_runner.sh --uninstall          stop it and remove the plist (data/runner is kept)
#   ./scripts/install_runner.sh --status             loaded? which checkout? the runner's token? the last log lines
#   ./scripts/install_runner.sh --prepare            set up the runner's checkout only (no launchd; works on Linux)
#   ./scripts/install_runner.sh --dry-run --out DIR  write the plist to DIR only (no launchctl; works on Linux)
# QA-Ops audit (25 Nov 2026): the runner never works in the engine's checkout. Its checkout is
# KAIBOT_RUNNER_CHECKOUT (default ~/Projects/kaibot-runner), a second clone of the same repository; the engine's is
# KAIBOT_ENGINE_CHECKOUT (default: the WorkingDirectory of com.kaibot.engine's plist, else ~/Projects/kaibot).
# In the engine's checkout this script only reads: its origin URL, .venv (linked, never installed into), data/
# (its folders linked, except data/live and data/runner, which are never linked; data/runner is copied once).
# Logs: <runner checkout>/data/runner/runner.log. Never touches com.kaibot.engine, com.kaibot.daily or
# scripts/service.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd -P)"
LABEL=com.kaibot.research-runner
ACTION=install; DRY=0; OUT="$HOME/Library/LaunchAgents"
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION=uninstall;; --status) ACTION=status;; --prepare) ACTION=prepare;;
    --dry-run) DRY=1;; --out) OUT="$2"; shift;;
    *) sed -n '2,10p' "$0"; exit 2;;
  esac; shift
done
PLIST="$OUT/$LABEL.plist"
ENGINE_PLIST="$HOME/Library/LaunchAgents/com.kaibot.engine.plist"
# data/ of the engine never linked into the runner's data/: the engine's state, the old runner's state, the pins
PRIVATE="live runner RELEASE HOLD release_history.log"
# what the allowed jobs write under data/ (reports, trial log, alarm files, the SEC store): the runner's own copies,
# cloned once from the engine's (APFS clones: no extra space), never links
OWN="reports trials.jsonl alarm fundamentals"

plist_value() {  # plist_value FILE KEY: a string of a plist, or nothing
  [ -f "$1" ] || return 0
  if command -v plutil >/dev/null 2>&1; then plutil -extract "$2" raw -o - "$1" 2>/dev/null || true
  else python3 -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb")).get(sys.argv[2], ""))' \
         "$1" "$2" 2>/dev/null || true; fi
}
canon() {  # an absolute path without links (the folder need not exist yet; its parent is made)
  local p="${1%/}"
  if [ -d "$p" ]; then (cd "$p" && pwd -P); else mkdir -p "$(dirname "$p")"; echo "$(cd "$(dirname "$p")" && pwd -P)/$(basename "$p")"; fi
}
ENGINE="${KAIBOT_ENGINE_CHECKOUT:-$(plist_value "$ENGINE_PLIST" WorkingDirectory)}"
ENGINE="$(canon "${ENGINE:-$HOME/Projects/kaibot}")"
RUNNER="$(canon "${KAIBOT_RUNNER_CHECKOUT:-$HOME/Projects/kaibot-runner}")"
LOG="$RUNNER/data/runner/runner.log"
say() {
  local line; line="$(date -u +%Y-%m-%dT%H:%M:%SZ) install_runner: $*"
  echo "$line"; if [ -d "$RUNNER/data/runner" ] && [ ! -L "$RUNNER/data" ]; then echo "$line" >> "$LOG"; fi
}
die() { echo "install_runner: $*" >&2; exit 1; }

within() {  # within A B: A is B or inside it (by device and inode: macOS folders are case-insensitive)
  local d="$1"
  while :; do
    { [ "$d" = "$2" ] || { [ -e "$d" ] && [ "$d" -ef "$2" ]; }; } && return 0
    [ "$d" = / ] || [ "$d" = . ] && return 1
    d="$(dirname "$d")"
  done
}
within "$RUNNER" "$ENGINE" && die "the runner's checkout ($RUNNER) is or is inside the engine's ($ENGINE): set KAIBOT_RUNNER_CHECKOUT"
within "$ENGINE" "$RUNNER" && die "the engine's checkout ($ENGINE) is inside the runner's ($RUNNER)"
if [ "$DRY" = 0 ] && [ "$ACTION" != prepare ] && [ "$(uname -s)" != Darwin ]; then
  echo "launchd is macOS only (use --prepare, or --dry-run --out DIR)"; exit 1
fi

py() {  # a Python for the small checks here: the engine's .venv (python.org's), else python3
  if [ -x "$ENGINE/.venv/bin/python" ]; then "$ENGINE/.venv/bin/python" "$@"; else python3 "$@"; fi
}
lock_free() {  # lock_free FILE: no runner tick holds it (a non-blocking flock, released at once)
  [ -e "$1" ] || return 0
  py -c 'import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(1)' "$1"
}

prepare() {  # the runner's own checkout: clone (or update when clean on main), .venv, data/ links, state copied
  if [ ! -e "$RUNNER" ]; then
    local url; url="$(git -C "$ENGINE" remote get-url origin 2>/dev/null || echo https://github.com/falconomad/kaibot.git)"
    git clone --quiet --branch main "$url" "$RUNNER"
    RUNNER="$(canon "$RUNNER")"
    echo "install_runner: cloned $url into $RUNNER (main @ $(git -C "$RUNNER" rev-parse --short HEAD))"
  elif ! git -C "$RUNNER" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
       || [ "$(cd "$RUNNER" && git rev-parse --show-toplevel)" != "$RUNNER" ]; then
    die "$RUNNER exists and is not a git checkout of its own: move it away or set KAIBOT_RUNNER_CHECKOUT"
  elif [ "$(git -C "$RUNNER" rev-parse --abbrev-ref HEAD)" = main ] && [ -z "$(git -C "$RUNNER" status --porcelain)" ]; then
    git -C "$RUNNER" pull --ff-only --quiet || echo "install_runner: could not pull $RUNNER; using what is there"
  else
    echo "install_runner: $RUNNER is not clean main: not pulled (the runner pauses until it is clean)"
  fi
  mkdir -p "$RUNNER/data"
  [ -L "$RUNNER/data" ] && die "$RUNNER/data is a link: the runner keeps a data/ of its own (remove the link)"
  if [ ! -e "$RUNNER/.venv" ] && [ ! -L "$RUNNER/.venv" ]; then
    if [ -x "$ENGINE/.venv/bin/python" ]; then
      ln -s "$ENGINE/.venv" "$RUNNER/.venv"
      echo "install_runner: .venv -> $ENGINE/.venv (shared; the runner never installs into it)"
    else
      echo "install_runner: no $ENGINE/.venv to share: make $RUNNER/.venv (python3 -m venv .venv &&" \
           ".venv/bin/pip install --require-hashes -r requirements-research.txt)"
    fi
  fi
  # the old install's state (done jobs, logs, pause) moves with the runner, once; never while a tick runs there
  local old="$ENGINE/data/runner"
  if [ -f "$old/done.json" ] && [ ! -f "$RUNNER/data/runner/done.json" ]; then
    lock_free "$old/runner.lock" || die "a runner tick is running in $ENGINE right now: try again once it ends" \
                                        "(tail $old/runner.log)"
    mkdir -p "$RUNNER/data/runner"
    cp -Rp "$old/." "$RUNNER/data/runner/"
    rm -f "$RUNNER/data/runner/runner.lock" "$RUNNER/data/runner/sec.lock"
    echo "install_runner: copied the runner's state from $old ($(py -c 'import json, sys
print(len(json.load(open(sys.argv[1]))))' "$RUNNER/data/runner/done.json") done jobs); the old copy is left as it was"
  fi
  mkdir -p "$RUNNER/data/runner"
  local n src
  for n in $OWN; do
    src="$ENGINE/data/$n"
    if [ ! -e "$RUNNER/data/$n" ] && [ ! -L "$RUNNER/data/$n" ] && [ -e "$src" ]; then
      cp -Rpc "$src" "$RUNNER/data/$n" 2>/dev/null || { rm -rf "$RUNNER/data/$n"; cp -Rp "$src" "$RUNNER/data/$n"; }
      echo "install_runner: data/$n copied from the engine's checkout (the runner's own from now on)"
    fi
  done
  local linked=0
  for src in "$ENGINE"/data/*; do
    [ -e "$src" ] || continue
    n="$(basename "$src")"
    case " $PRIVATE $OWN " in *" $n "*) continue;; esac
    if [ ! -e "$RUNNER/data/$n" ] && [ ! -L "$RUNNER/data/$n" ]; then
      ln -s "$src" "$RUNNER/data/$n"; linked=$((linked + 1))
    fi
  done
  [ "$linked" = 0 ] || echo "install_runner: linked $linked folder(s) of $ENGINE/data into $RUNNER/data (read by the jobs)"
}

case "$ACTION" in
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    say "uninstalled $LABEL (data/runner kept)"; exit 0;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state|last exit|run interval' || echo "$LABEL is not loaded"
    used="$(plist_value "$PLIST" WorkingDirectory)"
    echo "runner checkout: ${used:-none (no plist at $PLIST)}"
    echo "engine checkout: $ENGINE"
    if [ -n "$used" ] && within "$used" "$ENGINE"; then
      echo "WARNING: the runner still runs in the ENGINE's checkout (it refuses every tick): ./scripts/install_runner.sh"
    elif [ -n "$used" ]; then
      echo "runner main @ $(git -C "$used" rev-parse --short HEAD 2>/dev/null || echo '?')" \
           "on $(git -C "$used" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?');" \
           ".venv -> $(readlink "$used/.venv" 2>/dev/null || echo 'its own')"
      LOG="$used/data/runner/runner.log"
    fi
    # #711: the runner's own token. Product (26 Nov 2026): with no token file at all (and none ever used), the runner
    # still uses this user's gh login, with a WARNING each tick; once a token was used, never again
    TOKEN_FILE="${KAIBOT_RUNNER_GH_TOKEN_FILE:-$HOME/Projects/kaibot-secrets/runner_gh_token}"
    if [ ! -e "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ]; then
      if [ -f "$RUNNER/data/runner/runner_token_in_use" ]; then
        echo "runner token: MISSING after it was in use: nothing is pushed or posted (./scripts/runner_token.sh --install)"
      else
        echo "runner token: WARNING owner login in use; install runner token (./scripts/runner_token.sh --install)"
      fi
    else
      bash "$HERE/scripts/runner_token.sh" --check 2>&1 | sed 's/^/runner token: /' ||
        echo "runner token: REFUSED: nothing is pushed or posted until it is fixed (./scripts/runner_token.sh --check)"
    fi
    tail -n 20 "$LOG" 2>/dev/null || true; exit 0;;
esac

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/[|\\]/\\&/g'; }
write_plist() {
  local template="$RUNNER/ops/runner/$LABEL.plist"
  [ -f "$template" ] || template="$HERE/ops/runner/$LABEL.plist"
  local run_path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  mkdir -p "$OUT"
  sed -e "s|__REPO__|$(xml "$RUNNER")|g" -e "s|__ENGINE__|$(xml "$ENGINE")|g" -e "s|__HOME__|$(xml "$HOME")|g" \
      -e "s|__PATH__|$(xml "$run_path")|g" "$template" > "$PLIST.tmp"
  mv "$PLIST.tmp" "$PLIST"
}

if [ "$DRY" = 1 ]; then write_plist; echo "wrote $PLIST for $RUNNER (dry run, nothing loaded)"; exit 0; fi
if [ "$ACTION" = prepare ]; then prepare; say "prepared $RUNNER (engine checkout $ENGINE; nothing loaded)"; exit 0; fi

# install: stop the old job first (no tick starts while its state is copied), then set up, then load the new one
lock_free "$ENGINE/data/runner/runner.lock" || die "a runner tick is running in $ENGINE right now: try again once it" \
                                                   "ends (tail $ENGINE/data/runner/runner.log)"
was="$(plist_value "$PLIST" WorkingDirectory)"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
prepare
[ -x "$RUNNER/.venv/bin/python" ] || die "No $RUNNER/.venv/bin/python (see above); the runner is not loaded"
"$RUNNER/.venv/bin/python" -c 'import yaml' 2>/dev/null || die "$RUNNER/.venv lacks pyyaml; the runner is not loaded"
write_plist
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
if [ -n "$was" ] && ! within "$was" "$RUNNER"; then say "moved $LABEL from $was to $RUNNER"; fi
say "installed $LABEL: every 15 minutes from $RUNNER (the engine's checkout $ENGINE is never pulled or run in)"
TOKEN_FILE="${KAIBOT_RUNNER_GH_TOKEN_FILE:-$HOME/Projects/kaibot-secrets/runner_gh_token}"
if bash "$HERE/scripts/runner_token.sh" --check >/dev/null 2>&1; then
  say "the runner's own GitHub token works: results are pushed and posted with it (#711)"
elif [ ! -e "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ]; then
  say "WARNING owner login in use; install runner token: ./scripts/runner_token.sh --install (#711)"
else
  say "the runner token is refused: nothing is pushed or posted until it is fixed (./scripts/runner_token.sh --check)"
fi
"$RUNNER/.venv/bin/python" "$RUNNER/scripts/research_runner.py" --check --no-pull || say "queue.yaml has jobs that will be rejected (see above)"
