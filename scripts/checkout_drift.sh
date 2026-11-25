#!/usr/bin/env bash
# One informational WARNING line when the engine's checkout differs from what was reviewed: uncommitted changes
# (tracked or untracked; ignored files such as data/ and .venv are not changes), or HEAD ahead of or behind
# origin/main (as of the last fetch: nothing is fetched here). Prints nothing when it matches. Never fails: exit 0
# always, and it changes nothing (QA-Ops audit, 25 Nov 2026). run_engine.sh calls it once at every engine start,
# so the line lands in engine.log.
#   scripts/checkout_drift.sh [DIR]     DIR: the checkout (default: this script's)
cd "${1:-$(dirname "$0")/..}" 2>/dev/null || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
notes=()
changed="$(git status --porcelain --untracked-files=normal 2>/dev/null | wc -l | tr -d ' ')"
[ "${changed:-0}" -gt 0 ] && notes+=("$changed uncommitted change(s) (git status)")
if counts="$(git rev-list --left-right --count HEAD...refs/remotes/origin/main 2>/dev/null)"; then
  read -r ahead behind <<<"$counts"
  [ "${ahead:-0}" -gt 0 ] && notes+=("$ahead commit(s) ahead of origin/main")
  [ "${behind:-0}" -gt 0 ] && notes+=("$behind commit(s) behind origin/main")
else
  notes+=("no origin/main to compare with")
fi
[ "${#notes[@]}" -eq 0 ] && exit 0
where="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; [ "$where" = HEAD ] && where="detached at $(git rev-parse --short HEAD)"
msg="${notes[0]}"; for n in "${notes[@]:1}"; do msg="$msg; $n"; done
echo "WARNING: the engine's checkout $(pwd -P) ($where) is not origin/main as reviewed: $msg." \
     "Informational only: the engine starts as before (a pinned release is behind main by design)."
exit 0
