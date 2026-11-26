#!/usr/bin/env bash
# Build the engine image from a release (M20 9.3, #727): the image is the tag's tree, never the working directory.
#   ./scripts/build_image.sh rel-20261126-1     build kaibot-engine:rel-20261126-1 from that tag
#   ./scripts/build_image.sh --latest           the same for the newest rel-* tag
#   ./scripts/build_image.sh --test             build HEAD as kaibot-engine:test (CI's check of the image; not a
#                                               release: a host with KAIBOT_REQUIRE_PROMOTED=1 refuses it unless
#                                               HEAD is the commit it promoted)
# A release must be an annotated (or signed) tag whose commit is on main (scripts/release.sh's rule, the same code).
# The image carries the release in KAIBOT_CODE_SHA ("<tag>@<short>": every heartbeat and decision records it) and its
# full commit in KAIBOT_BUILD_COMMIT and the OCI revision label; scripts/engine_entry.sh compares that commit with the
# host's promotion (data/live/promoted) before the engine starts. Also tagged kaibot-engine:latest, the name compose
# and run_engine.sh run. Prints the image id last. Pushes nothing (a registry is the cloud move's, January).
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-}" in
  ""|-h|--help) sed -n '2,14p' "$0"; exit 2 ;;
  --test)
    COMMIT="$(git rev-parse HEAD)"; NAME=test; LABEL="unreleased@$(git rev-parse --short HEAD)"; TREE=HEAD ;;
  *)
    REL="$1"
    if [ "$REL" = --latest ]; then
      git fetch -q --tags 2>/dev/null || echo "(could not fetch tags; using the tags already here)" >&2
      REL="$(git tag --list 'rel-[0-9]*' --sort=-v:refname | head -n 1)"
      [ -n "$REL" ] || { echo "No rel-* tag yet: run the release workflow first." >&2; exit 1; }
    fi
    WHY="$(bash scripts/release.sh --verify "$REL")" || { echo "Refused: $WHY. No image was built." >&2; exit 1; }
    COMMIT="$(git rev-parse "refs/tags/$REL^{commit}")"; NAME="$REL"; LABEL="$REL@$(git rev-parse --short "$COMMIT")"
    TREE="refs/tags/$REL" ;;
esac

echo "Building kaibot-engine:$NAME from $TREE (commit $COMMIT)…" >&2
# git archive: exactly the commit's tracked files, so local edits and untracked files never reach the image
git archive --format=tar "$TREE" | docker build -q -t "kaibot-engine:$NAME" -t kaibot-engine:latest \
  --build-arg CODE_SHA="$LABEL" --build-arg BUILD_COMMIT="$COMMIT" \
  --label org.opencontainers.image.revision="$COMMIT" --label org.opencontainers.image.version="$NAME" - >&2
echo "Built kaibot-engine:$NAME ($LABEL). Start it only after promoting it: ./scripts/release.sh $NAME" >&2
docker image inspect --format '{{.Id}}' "kaibot-engine:$NAME"
