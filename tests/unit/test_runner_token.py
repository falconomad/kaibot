"""Audit 2 7.1 (#711): scripts/runner_token.sh installs and checks the research runner's own fine-grained token, and
.github/CODEOWNERS makes the runner's files and CI owner-reviewed. No network: gh is a fake on PATH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "runner_token.sh"
TOKEN = "github_pat_" + "Z9y8X7w6V5" * 8

FAKE_GH = """#!/bin/bash
# records the token it was given (never the real one) and answers like the REST API
echo "GH_TOKEN=${{GH_TOKEN:0:11}} CFG=${{GH_CONFIG_DIR:-none}} $*" >> {tmp}/gh.calls
[ "$GH_TOKEN" = "{token}" ] || exit 1
case "$*" in
  "api user --jq .login") echo runner-owner ;;
  "api repos/falconomad/kaibot --jq .full_name") echo falconomad/kaibot ;;
  "api -i repos/falconomad/kaibot")
    printf 'HTTP/2.0 200 OK\\r\\nGithub-Authentication-Token-Expiration: 2027-02-24 10:00:00 UTC\\r\\n\\r\\n' ;;
  "api repos/falconomad/kaibot/rules/branches/main --jq"*) echo "${{FAKE_RULES:-}}" ;;
esac
"""


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text(FAKE_GH.format(tmp=tmp_path, token=TOKEN))
    (bin_ / "gh").chmod(0o755)
    return {"PATH": f"{bin_}:{os.environ['PATH']}", "HOME": str(tmp_path),
            "KAIBOT_RUNNER_GH_TOKEN_FILE": str(tmp_path / "secrets" / "runner_gh_token")}


def run(args, env, stdin=""):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, input=stdin, capture_output=True, text=True,
                          timeout=30)


def test_install_writes_the_token_mode_600_and_checks_it(env, tmp_path):
    x = run(["--install"], {**env, "FAKE_RULES": "deletion pull_request non_fast_forward"}, TOKEN + "\n")
    f = tmp_path / "secrets" / "runner_gh_token"
    assert x.returncode == 0, x.stdout + x.stderr
    assert f.read_text() == TOKEN + "\n" and f.stat().st_mode & 0o777 == 0o600
    assert "signs in as: runner-owner" in x.stdout and "reaches falconomad/kaibot: ok" in x.stdout
    assert "expires: 2027-02-24" in x.stdout and "a direct push to main is refused" in x.stdout
    assert TOKEN not in x.stdout + x.stderr  # never printed
    calls = (tmp_path / "gh.calls").read_text()
    assert "CFG=none" not in calls and str(tmp_path / ".config") not in calls  # an empty gh config of its own


@pytest.mark.parametrize("bad", ["ghp_" + "a" * 36, "gho_" + "b" * 36, "hello"])
def test_install_refuses_anything_but_a_fine_grained_token(env, tmp_path, bad):
    x = run(["--install"], env, bad + "\n")
    assert x.returncode == 1 and "Nothing was written" in x.stdout and bad not in x.stdout
    assert not (tmp_path / "secrets" / "runner_gh_token").exists()


def test_check_warns_when_main_would_take_a_direct_push(env, tmp_path):
    run(["--install"], env, TOKEN + "\n")
    x = run(["--check"], {**env, "FAKE_RULES": "deletion"})
    assert "WARNING: main has no pull-request rule" in x.stdout and "this token CAN push to main" in x.stdout


def test_check_fails_on_a_readable_file_and_on_a_classic_token(env, tmp_path):
    f = tmp_path / "secrets" / "runner_gh_token"
    f.parent.mkdir()
    f.write_text(TOKEN + "\n")
    f.chmod(0o644)
    x = run(["--check"], env)
    assert x.returncode == 1 and "others can read it" in x.stdout
    f.write_text("ghp_" + "c" * 36 + "\n")
    f.chmod(0o600)
    x = run(["--check"], env)
    assert x.returncode == 1 and "a classic token" in x.stdout
    f.unlink()
    assert run(["--check"], env).returncode == 1


def test_codeowners_makes_the_runner_and_ci_owner_reviewed():
    lines = [ln.split() for ln in (ROOT / ".github" / "CODEOWNERS").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    owned = {p: o for p, *o in lines}
    for path in ("/ops/runner/", "/scripts/research_runner.py", "/.github/"):
        assert owned.get(path) == ["@falconomad"], path


def test_install_runner_status_says_which_identity_the_runner_uses(tmp_path):
    """Product (26 Nov 2026): no token file yet means the owner's login, said on --status; a token that was in use
    and is gone, or a bad one, means nothing is pushed (never the owner's login)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("launchctl", "exit 1"), ("uname", "echo Darwin")):  # --status without macOS
        (bin_ / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_ / name).chmod(0o755)
    env = {"HOME": str(tmp_path), "PATH": f"{bin_}:{os.environ['PATH']}",
           "KAIBOT_RUNNER_CHECKOUT": str(tmp_path / "runner"), "KAIBOT_ENGINE_CHECKOUT": str(tmp_path / "engine")}

    def status():
        x = subprocess.run(["bash", str(ROOT / "scripts" / "install_runner.sh"), "--status"], env=env,
                           capture_output=True, text=True, timeout=60)
        return [ln for ln in (x.stdout + x.stderr).splitlines() if ln.startswith("runner token")]

    assert status() == ["runner token: WARNING owner login in use; install runner token "
                        "(./scripts/runner_token.sh --install)"]
    (tmp_path / "runner/data/runner").mkdir(parents=True)
    (tmp_path / "runner/data/runner/runner_token_in_use").write_text("x\n")
    assert "MISSING after it was in use" in status()[0]
    f = tmp_path / "Projects/kaibot-secrets/runner_gh_token"
    f.parent.mkdir(parents=True)
    f.write_text("ghp_" + "d" * 36 + "\n")
    f.chmod(0o600)
    assert status()[-1].startswith("runner token: REFUSED")
