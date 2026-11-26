#!/usr/bin/env bash
# The research runner's own GitHub token (audit 2 7.1, #711; docs/ops/research_runner.md, "Its own GitHub token").
#   ./scripts/runner_token.sh --install   paste the fine-grained token (not shown, not in the shell history); writes it to
#                                         ~/Projects/kaibot-secrets/runner_gh_token, mode 600, then runs --check
#   ./scripts/runner_token.sh --check     the file (a regular file of yours, mode 600, a fine-grained github_pat_ token),
#                                         who it signs in as, that it reaches falconomad/kaibot, its expiry, and whether
#                                         main's rules would refuse a direct push from it
#   ./scripts/runner_token.sh --remove    delete the file (the runner then pushes and posts nothing)
# The runner (scripts/research_runner.py) uses ONLY this file, as GH_TOKEN with an empty gh config folder of its own,
# never your `gh auth login`. KAIBOT_RUNNER_GH_TOKEN_FILE names another file. The token is never printed.
set -euo pipefail
FILE="${KAIBOT_RUNNER_GH_TOKEN_FILE:-$HOME/Projects/kaibot-secrets/runner_gh_token}"
REPO=falconomad/kaibot
PAT_RE='^github_pat_[A-Za-z0-9_]{20,250}$'

kind_of() {  # what a token string is, in words
  case "$1" in
    ghp_*) echo "a classic token (it reaches every repository of the account)" ;;
    gho_*) echo "gh's own login token (the owner's full login)" ;;
    ghs_*|ghu_*) echo "an app token" ;;
    *) echo "not a GitHub token" ;;
  esac
}

mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }  # GNU, then macOS (GNU stat -f is another thing)
owner_of() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1"; }

check() {
  local rc=0 tok mode login cfg rules exp
  echo "token file: $FILE"
  if [ -L "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "  FAILED: missing (or a link). Make it: ./scripts/runner_token.sh --install"; return 1
  fi
  [ "$(owner_of "$FILE")" = "$(id -u)" ] || { echo "  FAILED: it belongs to another user"; rc=1; }
  mode="$(mode_of "$FILE")"
  if [ $((8#$mode & 8#077)) -ne 0 ]; then echo "  FAILED: mode $mode, others can read it: chmod 600 \"$FILE\""; rc=1
  else echo "  mode $mode: ok"; fi
  tok="$(tr -d '[:space:]' < "$FILE")"
  if ! [[ "$tok" =~ $PAT_RE ]]; then
    echo "  FAILED: it holds $(kind_of "$tok"), not a fine-grained token (github_pat_...)"; return 1
  fi
  echo "  a fine-grained token: ok"
  command -v gh >/dev/null 2>&1 || { echo "  FAILED: gh is not installed (brew install gh)"; return 1; }
  cfg="$(mktemp -d)"; trap 'rm -rf "$cfg"' RETURN
  ghx() { GH_TOKEN="$tok" GH_CONFIG_DIR="$cfg" GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1 gh "$@"; }
  if ! login="$(ghx api user --jq .login 2>/dev/null)"; then
    echo "  FAILED: GitHub refuses it (expired, revoked or mistyped)"; return 1
  fi
  echo "  signs in as: $login"
  if ghx api "repos/$REPO" --jq .full_name >/dev/null 2>&1; then echo "  reaches $REPO: ok"
  else echo "  FAILED: it cannot reach $REPO (Repository access: Only select repositories > $REPO)"; rc=1; fi
  exp="$(ghx api -i "repos/$REPO" 2>/dev/null | tr -d '\r' \
           | sed -n 's/^[Gg]ithub-[Aa]uthentication-[Tt]oken-[Ee]xpiration: //p' | head -n 1)"
  echo "  expires: ${exp:-unknown (set an expiry; renew with --install before it passes)}"
  rules="$(ghx api "repos/$REPO/rules/branches/main" --jq '[.[].type] | join(" ")' 2>/dev/null || true)"
  case " $rules " in
    *" pull_request "*) echo "  main's rules: $rules: a direct push to main is refused (unless the ruleset lets admins bypass it)" ;;
    *) echo "  WARNING: main has no pull-request rule (rules: ${rules:-none}): this token CAN push to main."
       echo "  Add the ruleset of audit 2 5.1 (docs/ops/research_runner.md, 'Its own GitHub token', step 4)." ;;
  esac
  return "$rc"
}

case "${1:-}" in
  --install)
    printf 'Paste the fine-grained token (github_pat_...), then Enter (it is not shown): '
    IFS= read -rs tok; echo
    tok="$(printf '%s' "$tok" | tr -d '[:space:]')"
    [[ "$tok" =~ $PAT_RE ]] || { echo "Refused: that is $(kind_of "$tok"), not a fine-grained token (github_pat_...)."
                                 echo "Nothing was written."; exit 1; }
    umask 077
    mkdir -p "$(dirname "$FILE")"
    tmp="$(mktemp "$(dirname "$FILE")/.runner_gh_token.XXXXXX")"
    printf '%s\n' "$tok" > "$tmp"; chmod 600 "$tmp"; mv -f "$tmp" "$FILE"
    unset tok
    echo "Wrote $FILE (mode 600)."
    check ;;
  --check) check ;;
  --remove) rm -f "$FILE"; echo "Removed $FILE: the runner pushes and posts nothing until a new one is installed." ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
