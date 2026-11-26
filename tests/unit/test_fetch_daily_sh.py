"""scripts/fetch_daily.sh (#230, PR A): the nightly refresh writes the integrity summary into logs/fetch_daily.log
once, keeps earlier nights in that log, and passes the check's exit code on. Python and pip are stubs: no network,
no keys (the .env holds placeholders), nothing installed."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUB = """#!/bin/sh
case "$*" in
  *"-m certifi"*) echo /dev/null; exit 0 ;;
  *--check*) mkdir -p data/daily/check
             echo "Bar integrity: 2 keys: 2 clean, 0 warn, 0 error." > data/daily/check/integrity.txt
             cat data/daily/check/integrity.txt; exit "${CHECK_RC:-0}" ;;
  *) echo "fetched 2 keys"; exit 0 ;;
esac
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    for name in ("fetch_daily.sh", "fetch_lock.py"):  # audit 2, 5.12: the script runs itself under the lock
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    bin_ = tmp_path / ".venv" / "bin"
    bin_.mkdir(parents=True)
    for name, text in (("python", STUB), ("pip", "#!/bin/sh\nexit 0\n")):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    (tmp_path / ".env").write_text("ALPACA_API_KEY=placeholder\nALPACA_SECRET=placeholder\n")
    return tmp_path


def sh(repo: Path, *args: str, rc: int = 0, stdout=None) -> subprocess.CompletedProcess:
    env = {**os.environ, "KAIBOT_ENV_FILE": str(repo / ".env"), "CHECK_RC": str(rc),
           "KAIBOT_FORMER_MEMBERS": str(repo / "none.csv")}
    return subprocess.run(["bash", str(repo / "scripts" / "fetch_daily.sh"), *args], cwd=repo, env=env, text=True,
                          stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, timeout=60)


def test_a_manual_run_writes_the_summary_into_the_log(repo):
    p = sh(repo)
    assert p.returncode == 0, p.stdout
    log = (repo / "logs" / "fetch_daily.log").read_text()
    assert "fetched 2 keys" in log and "(exit 0)" in log
    assert log.count("Bar integrity: 2 keys") == 1


@pytest.mark.parametrize("rc", [3, 1])
def test_the_check_exit_code_is_passed_on(repo, rc):
    p = sh(repo, rc=rc)
    assert p.returncode == rc
    assert f"(exit {rc})" in (repo / "logs" / "fetch_daily.log").read_text()
    assert ("600-day window" in p.stdout) == (rc == 3)
    assert sh(repo, "--check", rc=rc).returncode == rc  # --check alone (no .env needed)


def test_the_nightly_job_keeps_earlier_nights_and_logs_the_summary_once(repo):
    (repo / "logs").mkdir()
    log = repo / "logs" / "fetch_daily.log"
    log.write_text("== last night\nold line\n")
    with log.open("a") as f:  # what scripts/service_run.sh does: >>logs/fetch_daily.log 2>&1
        f.write("== tonight\n")
        f.flush()
        p = sh(repo, rc=3, stdout=f)
    assert p.returncode == 3
    text = log.read_text()
    assert text.startswith("== last night\nold line\n== tonight\n")
    assert text.count("fetched 2 keys") == 1 and text.count("Bar integrity: 2 keys") == 1


def test_check_runs_without_an_env_file(repo):
    (repo / ".env").unlink()
    assert sh(repo, "--check").returncode == 0
