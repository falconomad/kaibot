#!/usr/bin/env bash
# One locked dependency set (#122). pyproject.toml lists what Kaibot needs; uv.lock pins every package (all platforms,
# Python 3.12+); the two requirements files are hash-pinned exports of uv.lock for pip (Dockerfile, Mac scripts).
#   ./scripts/lock_deps.sh            re-export the requirements files from uv.lock (after `uv lock` or `uv lock --upgrade`)
#   ./scripts/lock_deps.sh --check    exit 1 if uv.lock or either export is out of date (CI runs this)
# Needs uv (https://docs.astral.sh/uv/). Never edit the requirements files by hand.
set -euo pipefail
cd "$(dirname "$0")/.."
export_to() {  # $1 = output file, rest = uv export flags
  local out="$1"; shift
  uv export --frozen --no-dev --no-emit-project --format requirements-txt --quiet "$@" > "$out"
}
if [ "${1:-}" = --check ]; then
  uv lock --check
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  export_to "$tmp/requirements-engine.txt"
  export_to "$tmp/requirements-research.txt" --extra research
  for f in requirements-engine.txt requirements-research.txt; do
    diff -q "$tmp/$f" "$f" >/dev/null || { echo "$f is not the export of uv.lock: run ./scripts/lock_deps.sh"; exit 1; }
  done
  echo "uv.lock and the requirements exports agree"
else
  uv lock
  export_to requirements-engine.txt           # engine: Dockerfile, run_engine.sh, fetch_*.sh
  export_to requirements-research.txt --extra research   # engine + research: run_experiments.sh, replay scripts
fi
