#!/bin/sh
# The engine image's start (M20 9.3, #727): the engine runs only the build its host promoted.
#   engine_entry.sh [engine args]   check the promotion, then exec python -m kaibot.engine [engine args]
#   engine_entry.sh --verify-only   check the promotion and say so; start nothing (CI, the runbook's drill)
#   engine_entry.sh --check         the engine's read-only check; allowed for any build (no lease, no order)
#
# The image knows the commit it was built from (KAIBOT_BUILD_COMMIT, baked in by scripts/build_image.sh from a rel-*
# tag). The host knows what it promoted: <data dir>/promoted, one line "<tag> <full commit>", written by
# `./scripts/release.sh <tag>` (or --latest) and removed by `--clear` / `main`. With KAIBOT_REQUIRE_PROMOTED=1 (the
# VM's setting; default 0, the Mac, where release.sh and KAIBOT_FOLLOW_MAIN guard the checkout) an image whose commit
# is not the promoted one, or that was not built from a release, does not start: exit code 6 and a REFUSED line.
# Nothing is started, no lease is taken, the broker is not touched. Runbook: docs/ops/always_on.md, "Pinned releases".
# POSIX sh: the image has no bash.
set -eu

EXIT_UNPROMOTED=6
DATA_DIR="${KAIBOT_DATA_DIR:-/data}"
RECORD="$DATA_DIR/promoted"

# KAIBOT_REQUIRE_PROMOTED: the process environment when set there, else the .env the engine reads (KAIBOT_ENV_FILE),
# else 0. Only 0 and 1 count; anything else is refused (a typo must not switch the check off).
require_value() {
  if [ -n "${KAIBOT_REQUIRE_PROMOTED+x}" ]; then v="$KAIBOT_REQUIRE_PROMOTED"
  else
    v=""
    if [ -n "${KAIBOT_ENV_FILE:-}" ] && [ -r "$KAIBOT_ENV_FILE" ]; then
      v="$(grep -E '^[[:space:]]*(export[[:space:]]+)?KAIBOT_REQUIRE_PROMOTED=' "$KAIBOT_ENV_FILE" | tail -n 1 \
           | sed -e 's/^[^=]*=//' -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
                 -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")" || v=""
    fi
  fi
  case "$v" in
    ""|0) echo 0 ;;
    1) echo 1 ;;
    *) echo "bad:$v" ;;
  esac
}

refuse() {
  echo "REFUSED: $1" >&2
  echo "This engine runs only the release its host promoted (KAIBOT_REQUIRE_PROMOTED=1, #727). Promote one on the" >&2
  echo "host with ./scripts/release.sh --latest (or a rel-* tag), build it with ./scripts/build_image.sh <tag> and" >&2
  echo "start again (docs/ops/always_on.md, 'Pinned releases'). Nothing was started." >&2
  exit "$EXIT_UNPROMOTED"
}

VERIFY=0
[ "${1:-}" = --verify-only ] && { VERIFY=1; shift; }
COMMIT="${KAIBOT_BUILD_COMMIT:-}"
LABEL="${KAIBOT_CODE_SHA:-unknown}"
REQ="$(require_value)"
case "$REQ" in
  bad:*) refuse "KAIBOT_REQUIRE_PROMOTED='${REQ#bad:}' is not 0 or 1" ;;
esac

if [ "$REQ" = 1 ] && [ "${1:-}" = --check ]; then
  # `--check` takes no lease and trades nothing (keys, connectivity, the lease's holder): any build may answer it
  echo "--check: the promotion is not required for a read-only check (this image: $LABEL)" >&2
elif [ "$REQ" = 1 ]; then
  case "$COMMIT" in
    ""|unknown) refuse "this image was not built from a release (no build commit; build it with scripts/build_image.sh)" ;;
  esac
  [ -r "$RECORD" ] || refuse "nothing is promoted on this host ($RECORD is missing)"
  read -r PTAG PSHA _ < "$RECORD" || true
  [ -n "${PSHA:-}" ] || refuse "$RECORD does not name a release and a commit"
  [ "$PSHA" = "$COMMIT" ] || refuse "this image is $LABEL (commit $COMMIT); the host promoted $PTAG (commit $PSHA)"
  echo "promoted build: $LABEL (commit $COMMIT) is $PTAG, the release this host promoted" >&2
else
  echo "promotion not required (KAIBOT_REQUIRE_PROMOTED=0): starting $LABEL${COMMIT:+ (commit $COMMIT)}" >&2
fi

if [ "$VERIFY" = 1 ]; then
  echo "verify-only: nothing started" >&2
  exit 0
fi
exec python -m kaibot.engine "$@"
