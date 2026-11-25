"""scripts/checkout_drift.sh (QA-Ops audit, 25 Nov 2026): the engine logs one informational WARNING line at start when
its checkout has uncommitted changes or is ahead of or behind origin/main; nothing when it matches; never fails."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT

SCRIPT = ROOT / "scripts" / "checkout_drift.sh"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _env(tmp_path: Path) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _repo(tmp_path: Path) -> tuple[Path, Path, dict]:
    env = _env(tmp_path)
    bare, co = tmp_path / "origin.git", tmp_path / "kaibot"

    def git(*a, cwd):
        subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)

    git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-q", "-b", "main", str(co), cwd=tmp_path)
    (co / ".gitignore").write_text("data/\n")
    (co / "a.txt").write_text("a\n")
    git("add", "-A", cwd=co)
    git("commit", "-q", "-m", "one", cwd=co)
    git("remote", "add", "origin", str(bare), cwd=co)
    git("push", "-q", "origin", "main", cwd=co)
    git("fetch", "-q", "origin", cwd=co)
    return co, bare, env


def _drift(co: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), str(co)], capture_output=True, text=True, env=env)


def test_a_checkout_equal_to_origin_main_says_nothing(tmp_path):
    co, _, env = _repo(tmp_path)
    (co / "data").mkdir()
    (co / "data" / "journal.sqlite").write_text("ignored")  # ignored files are not changes
    r = _drift(co, env)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_uncommitted_ahead_and_behind_give_one_warning_line(tmp_path):
    co, bare, env = _repo(tmp_path)
    (co / "a.txt").write_text("changed\n")
    (co / "new.txt").write_text("untracked\n")
    r = _drift(co, env)
    assert r.returncode == 0 and r.stdout.count("\n") == 1 and r.stdout.startswith("WARNING:")
    assert "2 uncommitted change(s)" in r.stdout and "Informational only" in r.stdout
    subprocess.run(["git", "commit", "-qam", "local"], cwd=co, env=env, check=True)
    r = _drift(co, env)
    assert "1 commit(s) ahead of origin/main" in r.stdout and "1 uncommitted" in r.stdout
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], env=env, check=True)
    for i in range(2):
        (other / f"b{i}.txt").write_text("b\n")
        subprocess.run(["git", "add", "-A"], cwd=other, env=env, check=True)
        subprocess.run(["git", "commit", "-qm", f"b{i}"], cwd=other, env=env, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=other, env=env, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=co, env=env, check=True)
    r = _drift(co, env)
    assert r.returncode == 0 and "1 commit(s) ahead" in r.stdout and "2 commit(s) behind origin/main" in r.stdout


def test_no_git_or_no_checkout_never_fails(tmp_path):
    r = _drift(tmp_path / "missing", _env(tmp_path))
    assert r.returncode == 0 and r.stdout == ""
    (tmp_path / "plain").mkdir()
    r = _drift(tmp_path / "plain", _env(tmp_path))
    assert r.returncode == 0 and r.stdout == ""


def test_run_engine_calls_it_once_at_every_start_and_never_fails_on_it():
    src = (ROOT / "scripts" / "run_engine.sh").read_text()
    assert src.count("scripts/checkout_drift.sh") == 1
    assert 'bash scripts/checkout_drift.sh "$PWD" || true' in src
    # after the release is checked out (what runs is what is compared), before the engine starts
    assert src.index("release.sh --apply") < src.index("checkout_drift.sh") < src.index("engine_guard.sh ||")
