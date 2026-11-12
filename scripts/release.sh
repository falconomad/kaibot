#!/usr/bin/env bash
# Pinned releases (issue #121, audit H4): the engine trades a tagged release, not whatever is on main.
#   ./scripts/release.sh v2026.11.13         pin this tag; the engine switches to it at its next start
#   ./scripts/release.sh v2026.11.13 --force  same, even during 09:30-16:00 ET (normally refused)
#   ./scripts/release.sh main                 escape hatch: follow main again (git pull at every start)
#   ./scripts/release.sh --clear              remove the pin: back to the behaviour before #121 (same as main)
#   ./scripts/release.sh --show               print the pin, what is checked out and the history
#   ./scripts/release.sh --apply              used by run_engine.sh: check out the pin, print its label
# The pin lives in data/RELEASE (not tracked by git); KAIBOT_RELEASE=<tag|main> overrides it for one start.
# Every promotion is appended to data/release_history.log. Runbook: docs/ops/always_on.md.
set -euo pipefail
cd "$(dirname "$0")/.."
PIN_FILE=data/RELEASE
HISTORY=data/release_history.log

in_market_hours() {  # weekday 09:30-16:00 New York time (holidays are not known here: use --force)
  local dow hm
  if [ -n "${KAIBOT_FAKE_ET:-}" ]; then read -r dow hm <<<"$KAIBOT_FAKE_ET"  # tests: "<1-7> <HHMM>"
  else dow="$(TZ=America/New_York date +%u)"; hm="$(TZ=America/New_York date +%H%M)"; fi
  [ "$dow" -le 5 ] && [ "$((10#$hm))" -ge 930 ] && [ "$((10#$hm))" -lt 1600 ]
}

pin() {  # the release to trade: KAIBOT_RELEASE, else data/RELEASE, else nothing
  if [ -n "${KAIBOT_RELEASE:-}" ]; then echo "$KAIBOT_RELEASE"
  elif [ -f "$PIN_FILE" ]; then head -n1 "$PIN_FILE" | tr -d '[:space:]'
  fi
}

clean_tree() { git diff --quiet && git diff --cached --quiet; }

apply() {  # stdout: the label to record ("" when nothing is pinned); messages on stderr
  local rel target
  rel="$(pin)"
  [ -n "$rel" ] || return 0
  if [ "$rel" = main ]; then
    if [ "$(git rev-parse --abbrev-ref HEAD)" != main ]; then
      clean_tree || { echo "Local changes in $(pwd): not switching to main. Commit or stash them first." >&2; return 1; }
      git checkout -q main >&2
    fi
    git pull --ff-only -q >&2 2>/dev/null || echo "(could not pull the latest code; running what is here)" >&2
    echo "main@$(git rev-parse --short HEAD)"
    return 0
  fi
  git fetch -q --tags 2>/dev/null || echo "(could not fetch tags; using the tags already here)" >&2
  target="$(git rev-parse -q --verify "refs/tags/$rel^{commit}" 2>/dev/null)" || {
    echo "Release '$rel' does not exist (pinned in ${KAIBOT_RELEASE:+KAIBOT_RELEASE}${KAIBOT_RELEASE:-$PIN_FILE})." \
         "List them with: git tag --list" >&2; return 1; }
  if [ "$(git rev-parse HEAD)" != "$target" ]; then
    clean_tree || { echo "Local changes in $(pwd): not switching to release $rel. Commit or stash them first." >&2
                    return 1; }
    git checkout -q --detach "$target" >&2
    echo "Switched to release $rel ($(git rev-parse --short HEAD))." >&2
  fi
  echo "$rel@$(git rev-parse --short HEAD)"
}

case "${1:-}" in
  --apply) apply; exit ;;
  --show)
    echo "pinned:      $(pin || true)${KAIBOT_RELEASE:+ (from KAIBOT_RELEASE)}"
    [ -n "$(pin)" ] || echo "             (nothing pinned: every start pulls main, as before)"
    echo "checked out: $(git describe --tags --always 2>/dev/null) ($(git rev-parse --short HEAD))"
    [ -f data/live/running_release ] && echo "last start:  $(cat data/live/running_release)"
    [ -f "$HISTORY" ] && { echo "history (last 5):"; tail -n5 "$HISTORY"; }
    exit 0 ;;
  --clear)
    rm -f "$PIN_FILE"; mkdir -p data
    echo "$(date -u +%FT%TZ) cleared by $(id -un)" >> "$HISTORY"
    echo "Pin removed: the engine pulls main at every start, as before."; exit 0 ;;
  ""|-h|--help) sed -n '2,10p' "$0"; exit 2 ;;
esac

REL="$1"; FORCE=0
[ "${2:-}" = --force ] && FORCE=1
if in_market_hours && [ "$FORCE" = 0 ]; then
  echo "Refused: it is 09:30-16:00 in New York. Promote releases outside market hours, or add --force"
  echo "(a running engine keeps its code until its next start either way)."; exit 1
fi
if [ "$REL" != main ]; then
  git fetch -q --tags 2>/dev/null || echo "(could not fetch tags; using the tags already here)"
  git rev-parse -q --verify "refs/tags/$REL^{commit}" >/dev/null || {
    echo "No tag '$REL'. Existing tags (newest first):"; git tag --sort=-creatordate | head -n5; exit 1; }
fi
mkdir -p data
echo "$REL" > "$PIN_FILE"
SHA="$([ "$REL" = main ] && echo main || git rev-parse --short "$REL^{commit}")"
echo "$(date -u +%FT%TZ) $REL $SHA by $(id -un)$([ "$FORCE" = 1 ] && echo ' (forced)')" >> "$HISTORY"
echo "Pinned $REL ($SHA). The engine switches to it at its next start."
echo "To switch now (outside market hours): ./scripts/service.sh restart  (or Ctrl-C and ./scripts/run_engine.sh)"
