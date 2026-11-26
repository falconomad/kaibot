#!/usr/bin/env bash
# Pinned releases (issue #121, audit H4; audit 2 5.2, #682): the engine trades a tagged release, not whatever is on main.
#   ./scripts/release.sh --latest             pin the newest rel-YYYYMMDD-N tag (made by CI, .github/workflows/release.yml)
#   ./scripts/release.sh rel-20261126-1       pin this tag; the engine switches to it at its next start
#   ./scripts/release.sh rel-20261126-1 --force  same, even during 09:30-16:00 ET (normally refused)
#   ./scripts/release.sh main                 follow main again (git pull at every start; needs KAIBOT_FOLLOW_MAIN)
#   ./scripts/release.sh --clear              remove the pin: follow main (same as main)
#   ./scripts/release.sh --show               print the pin, what is checked out and the history
#   ./scripts/release.sh --apply              used by run_engine.sh: check out the pin, print its label
#   ./scripts/release.sh --verify <tag>       is <tag> a release (annotated, on main)? exit 0, or 1 and the reason
# The pin lives in data/RELEASE (not tracked by git); KAIBOT_RELEASE=<tag|main> overrides it for one start.
# A tag is accepted only when it is annotated (or signed) and its commit is on main (origin/main when there is one).
# Unpinned (no pin, or `main`) the engine follows main only while KAIBOT_FOLLOW_MAIN=1 (the service's environment,
# else the .env; default 1 for now, #600) and says so loudly at every start; KAIBOT_FOLLOW_MAIN=0 refuses to start
# an unpinned engine. Every promotion is appended to data/release_history.log. Runbook: docs/ops/always_on.md.
# M20 9.3 (#727): pinning a tag also writes data/live/promoted ("<tag> <full commit>"), the record an engine image
# checks before it starts (scripts/engine_entry.sh, KAIBOT_REQUIRE_PROMOTED=1 on the VM); `main` and --clear remove it.
set -euo pipefail
cd "$(dirname "$0")/.."
PIN_FILE=data/RELEASE
HISTORY=data/release_history.log
PROMOTED=data/live/promoted  # the engine container's /data/promoted

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

# KAIBOT_FOLLOW_MAIN (audit 2 5.2, #682): may an unpinned start follow main? The process environment when set there,
# else the .env (KAIBOT_ENV_FILE, as the engine reads its settings), else the default. Product decision (26 Nov 2026,
# the stakeholder's fast deploys of #600): default 1 for now. Only an explicit 0 or 1 counts; anything else keeps the
# default and says so (as service_run.sh's switches).
FOLLOW_MAIN_DEFAULT=1
follow_main_value() {
  local v line env_file="${KAIBOT_ENV_FILE:-$HOME/Projects/kaibot-secrets/.env}"
  if [ -n "${KAIBOT_FOLLOW_MAIN+x}" ]; then v="$KAIBOT_FOLLOW_MAIN"
  else
    line="$(grep -E '^[[:space:]]*(export[[:space:]]+)?KAIBOT_FOLLOW_MAIN=' "$env_file" 2>/dev/null | tail -n 1)"
    v="${line#*=}"; v="${v%% \#*}"
    v="$(printf '%s' "$v" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  fi
  case "$v" in
    0|1) echo "$v" ;;
    "") echo "$FOLLOW_MAIN_DEFAULT" ;;
    *) echo "KAIBOT_FOLLOW_MAIN='$v' is not 0 or 1: the default ($FOLLOW_MAIN_DEFAULT) is used" >&2
       echo "$FOLLOW_MAIN_DEFAULT" ;;
  esac
}

unpinned() {  # $1 = the pin ("" or main). 0: go on (following main, said loudly); 1: refused (KAIBOT_FOLLOW_MAIN=0)
  local why="${1:+data/RELEASE or KAIBOT_RELEASE says 'main'}"
  why="${why:-nothing is pinned (no data/RELEASE)}"
  if [ "$(follow_main_value)" = 0 ]; then
    echo "REFUSED: $why and KAIBOT_FOLLOW_MAIN=0: this engine trades only a pinned release." >&2
    echo "Pin the newest CI-made release (one command): ./scripts/release.sh --latest" >&2
    echo "(or follow main for now: KAIBOT_FOLLOW_MAIN=1 in the .env). Nothing was started." >&2
    return 1
  fi
  echo "WARNING: UNPINNED ENGINE: $why, so this start runs whatever is on main now (KAIBOT_FOLLOW_MAIN=1)." >&2
  echo "WARNING: UNPINNED ENGINE: code nobody promoted can start here at any restart, also mid-session." >&2
  echo "WARNING: UNPINNED ENGINE: pin with ./scripts/release.sh --latest (docs/ops/always_on.md, #682)." >&2
  return 0
}

main_ref() {  # the ref a release must be on: origin/main when this clone has it, else the local main
  if git rev-parse -q --verify refs/remotes/origin/main >/dev/null; then echo refs/remotes/origin/main
  else echo refs/heads/main; fi
}

check_tag() {  # check_tag TAG: 0 when TAG is an annotated (or signed) tag whose commit is on main; else a reason
  local rel="$1" target ref
  target="$(git rev-parse -q --verify "refs/tags/$rel^{commit}" 2>/dev/null)" || { echo "no tag '$rel'"; return 1; }
  if [ "$(git cat-file -t "refs/tags/$rel" 2>/dev/null)" != tag ]; then
    echo "'$rel' is a lightweight tag: a release must be an annotated or signed tag (CI's release workflow makes them)"
    return 1
  fi
  ref="$(main_ref)"
  if ! git merge-base --is-ancestor "$target" "$ref" 2>/dev/null; then
    echo "'$rel' ($(git rev-parse --short "$target")) is not on main (${ref#refs/}): only a merged commit can be a release"
    return 1
  fi
}

latest_tag() {  # the newest rel-YYYYMMDD-N tag ("" when there is none)
  git tag --list 'rel-[0-9]*' --sort=-v:refname 2>/dev/null | head -n 1
}

apply() {  # stdout: the label to record ("" when nothing is pinned); messages on stderr
  local rel target why
  rel="$(pin)"
  if [ -z "$rel" ] || [ "$rel" = main ]; then unpinned "$rel" || return 1; fi
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
         "List them with: git tag --list 'rel-*'" >&2; return 1; }
  why="$(check_tag "$rel")" || { echo "Release refused: $why. Nothing was started." >&2; return 1; }
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
  --verify)
    [ -n "${2:-}" ] || { echo "usage: release.sh --verify <tag>"; exit 2; }
    git rev-parse -q --verify "refs/tags/$2^{commit}" >/dev/null 2>&1 \
      || git fetch -q --tags 2>/dev/null || true
    check_tag "$2"; exit ;;
  --show)
    echo "pinned:      $(pin || true)${KAIBOT_RELEASE:+ (from KAIBOT_RELEASE)}"
    [ -n "$(pin)" ] || echo "             (nothing pinned: every start pulls main while KAIBOT_FOLLOW_MAIN=1)"
    echo "follow main: KAIBOT_FOLLOW_MAIN=$(follow_main_value 2>/dev/null) (1: an unpinned start follows main; 0: refused)"
    echo "newest rel:  $(latest_tag)"
    echo "checked out: $(git describe --tags --always 2>/dev/null) ($(git rev-parse --short HEAD))"
    echo "promoted:    $(cat "$PROMOTED" 2>/dev/null || echo 'nothing (an image with KAIBOT_REQUIRE_PROMOTED=1 refuses to start)')"
    [ -f data/live/running_release ] && echo "last start:  $(cat data/live/running_release)"
    [ -f "$HISTORY" ] && { echo "history (last 5):"; tail -n5 "$HISTORY"; }
    exit 0 ;;
  --clear)
    rm -f "$PIN_FILE" "$PROMOTED"; mkdir -p data
    echo "$(date -u +%FT%TZ) cleared by $(id -un)" >> "$HISTORY"
    echo "Pin removed: the engine follows main at every start (only while KAIBOT_FOLLOW_MAIN=1)."; exit 0 ;;
  ""|-h|--help) sed -n '2,17p' "$0"; exit 2 ;;
esac

REL="$1"; FORCE=0
[ "${2:-}" = --force ] && FORCE=1
if [ "$REL" = --latest ]; then
  git fetch -q --tags 2>/dev/null || echo "(could not fetch tags; using the tags already here)"
  REL="$(latest_tag)"
  [ -n "$REL" ] || { echo "No rel-* tag yet: run the release workflow first (GitHub > Actions > release > Run workflow)."
                     exit 1; }
  echo "Newest release: $REL"
fi
if in_market_hours && [ "$FORCE" = 0 ]; then
  echo "Refused: it is 09:30-16:00 in New York. Promote releases outside market hours, or add --force"
  echo "(a running engine keeps its code until its next start either way)."; exit 1
fi
if [ "$REL" != main ]; then
  git fetch -q --tags 2>/dev/null || echo "(could not fetch tags; using the tags already here)"
  git rev-parse -q --verify "refs/tags/$REL^{commit}" >/dev/null || {
    echo "No tag '$REL'. Existing tags (newest first):"; git tag --sort=-creatordate | head -n5; exit 1; }
  WHY="$(check_tag "$REL")" || { echo "Refused: $WHY. Nothing was changed."; exit 1; }
else
  echo "Note: 'main' is not a release: every start follows main (only while KAIBOT_FOLLOW_MAIN=1; with 0 it refuses)."
fi
mkdir -p data/live
echo "$REL" > "$PIN_FILE"
SHA="$([ "$REL" = main ] && echo main || git rev-parse --short "$REL^{commit}")"
if [ "$REL" = main ]; then rm -f "$PROMOTED"
else echo "$REL $(git rev-parse "refs/tags/$REL^{commit}")" > "$PROMOTED.tmp" && mv "$PROMOTED.tmp" "$PROMOTED"; fi
echo "$(date -u +%FT%TZ) $REL $SHA by $(id -un)$([ "$FORCE" = 1 ] && echo ' (forced)')" >> "$HISTORY"
echo "Pinned $REL ($SHA). The engine switches to it at its next start."
echo "To switch now (outside market hours): ./scripts/service.sh restart  (or Ctrl-C and ./scripts/run_engine.sh)"
