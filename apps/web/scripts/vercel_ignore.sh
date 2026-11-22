#!/usr/bin/env bash
# Vercel's "ignored build step" for the dashboard (vercel.json: ignoreCommand). Vercel runs it in the project's Root
# Directory (apps/web) before the build. Exit 0 = skip this build, exit 1 = build.
#
# Skip only when it is certain that nothing under apps/web (the app, api/, vercel.json, package files) differs from
# the last deployment that was built for this branch. Anything unknown builds:
#   - VERCEL_GIT_PREVIOUS_SHA is the commit of the branch's last successful deployment. Vercel sets it only when an
#     ignored build step exists; it is empty on a branch's first deployment.
#   - Vercel clones with --depth=10, so that commit can be missing from the clone (more than ten commits back, or
#     the branch was rebased). Then there is nothing to compare with, and the build runs.
# The comparison is between two trees, not "the last commit": a push of several commits is judged as a whole.
set -u

prev="${VERCEL_GIT_PREVIOUS_SHA:-}"

if [ -z "$prev" ]; then
  echo "vercel_ignore: no earlier deployment of this branch to compare with: build"
  exit 1
fi
if ! git cat-file -e "${prev}^{commit}" 2>/dev/null; then
  echo "vercel_ignore: the last deployed commit ${prev} is not in this clone: build"
  exit 1
fi

git diff --quiet "$prev" HEAD -- .
case $? in
  0) echo "vercel_ignore: nothing under apps/web changed since ${prev}: skip"; exit 0 ;;
  1) echo "vercel_ignore: apps/web changed since ${prev}: build"; exit 1 ;;
  *) echo "vercel_ignore: git diff failed: build"; exit 1 ;;
esac
