"""#613: one watchdog per data folder (pid file + liveness check).

22 Nov 2026: a hand-started `run_engine.sh` beside the service left a second watchdog (pid 2509) running until it
was killed by hand. A watchdog now refuses to start while another live kaibot watchdog of this host holds
`data/live/watchdog.lock`, and one that lost the file to a live one stops at its next poll.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kaibot.engine import watchdog as wdmod
from kaibot.engine.watchdog import WATCHDOG_LOCK, WatchdogLock

ROOT = Path(__file__).resolve().parents[1]


def lock(tmp_path, pid, live=(), host="mac", sleeps=None):
    return WatchdogLock(tmp_path, host=host, pid=pid, is_watchdog=lambda p: p in live,
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


def held(tmp_path):
    b = json.loads((tmp_path / WATCHDOG_LOCK).read_text())
    return b["pid"], b["host"]


def test_the_first_watchdog_takes_the_file(tmp_path):
    assert lock(tmp_path, 10).claim() is None
    assert held(tmp_path) == (10, "mac")


def test_a_second_watchdog_is_refused_while_the_first_lives(tmp_path):
    assert lock(tmp_path, 10).claim() is None
    sleeps: list[float] = []
    assert lock(tmp_path, 20, live={10}, sleeps=sleeps).claim(wait_s=5) == 10
    assert held(tmp_path) == (10, "mac")  # untouched
    assert sum(sleeps) == 5  # it gave the first one time to go first


def test_a_watchdog_being_stopped_is_waited_for(tmp_path):
    """run_engine.sh stops the previous watchdog just before it starts the next: the next one waits for it."""
    assert lock(tmp_path, 10).claim() is None
    live = {10}
    first = lock(tmp_path, 10)

    def sleep(_s):
        live.discard(10)  # the old one exits during the wait and lets go of the file
        first.release()

    second = WatchdogLock(tmp_path, host="mac", pid=20, is_watchdog=lambda p: p in live, sleep=sleep)
    assert second.claim(wait_s=20) is None and held(tmp_path) == (20, "mac")


def test_a_dead_or_foreign_pid_does_not_hold_the_file(tmp_path):
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 10, "host": "mac"}))
    assert lock(tmp_path, 20, live=set()).claim() is None  # gone, or a pid reused by another program
    assert held(tmp_path) == (20, "mac")


def test_a_file_of_another_host_is_not_checked(tmp_path):
    """Docker: the container's watchdog writes its container's host name; its pid means nothing here."""
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 10, "host": "container"}))
    assert lock(tmp_path, 20, live={10}).claim() is None


@pytest.mark.parametrize("text", ["", "not json", "{}", '{"pid": "x"}'])
def test_an_unreadable_file_holds_nothing(tmp_path, text):
    (tmp_path / WATCHDOG_LOCK).write_text(text)
    assert lock(tmp_path, 20).claim() is None and held(tmp_path) == (20, "mac")


def test_two_started_at_once_the_last_writer_keeps_it(tmp_path):
    """Both find the file free and write; after the settle wait the one whose write was overwritten stops."""
    a = WatchdogLock(tmp_path, host="mac", pid=10, is_watchdog=lambda p: p in {10, 20}, sleep=lambda s: None)
    b = WatchdogLock(tmp_path, host="mac", pid=20, is_watchdog=lambda p: p in {10, 20}, sleep=lambda s: None)
    assert a.in_the_way() is None and b.in_the_way() is None
    a._write()
    b._write()
    assert a.in_the_way() == 20 and b.in_the_way() is None


def test_keep_stops_a_watchdog_that_lost_the_file_and_rewrites_a_missing_one(tmp_path):
    a = lock(tmp_path, 10, live={20})
    assert a.claim() is None
    (tmp_path / WATCHDOG_LOCK).unlink()
    assert a.keep() is None and held(tmp_path) == (10, "mac")  # written again
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 30, "host": "mac"}))  # a dead one's
    assert a.keep() is None and held(tmp_path) == (10, "mac")
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 20, "host": "mac"}))  # a live one took it
    assert a.keep() == 20 and held(tmp_path) == (20, "mac")


def test_release_removes_only_its_own_file(tmp_path):
    a = lock(tmp_path, 10)
    assert a.claim() is None
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 20, "host": "mac"}))
    a.release()
    assert held(tmp_path) == (20, "mac")
    (tmp_path / WATCHDOG_LOCK).write_text(json.dumps({"pid": 10, "host": "mac"}))
    a.release()
    assert not (tmp_path / WATCHDOG_LOCK).exists()


def test_an_unwritable_folder_runs_unguarded(tmp_path):
    assert lock(tmp_path / "missing", 10).claim() is None


# ------------------------------------------------------------------ real processes
SLEEP = shutil.which("sleep")


@pytest.fixture
def proc():
    """A `sleep` whose argv[0] is `argv0`, started with ONE exec and waited for until it shows that exact command line.

    #654: this used to be `bash -c 'exec -a "<argv0>" sleep 30'`, which passes through two images. Bash's own command
    line already contains the watched word, so the wait ended at once, and while bash exec'd `sleep` the command line
    read empty for a moment (Linux clears it until the new image's arguments are set up). A `_is_watchdog()` landing
    in that gap saw no watchdog. Now the only image that can show the expected command line is the final one, and
    `sleep` never changes it, so once the wait has seen it, every later read sees it too."""
    started = []

    def start(argv0):
        p = subprocess.Popen([argv0, "30"], executable=SLEEP)
        started.append(p)
        _wait_cmdline(p.pid, f"{argv0} 30")
        return p

    yield start
    for p in started:
        p.kill()
        p.wait()


def _wait_cmdline(pid, want, timeout_s=30.0):
    """Wait until `pid` runs exactly `want`; fail (not carry on) when it never does."""
    deadline = time.monotonic() + timeout_s
    while (got := wdmod._command_of(pid)) != want:
        if time.monotonic() > deadline:
            pytest.fail(f"pid {pid} never showed {want!r} (last: {got!r})")
        time.sleep(0.01)


def test_liveness_needs_a_live_kaibot_watchdog_process(proc):
    wd = proc("/x/.venv/bin/python -m kaibot.engine.watchdog")
    other = proc("sleep")
    assert wdmod._is_watchdog(wd.pid)
    assert not wdmod._is_watchdog(other.pid)  # a reused pid
    gone = subprocess.Popen(["true"])
    gone.wait()
    assert not wdmod._is_watchdog(gone.pid) and not wdmod._is_watchdog(0)


def test_a_second_watchdog_process_exits_before_it_touches_the_broker(tmp_path, proc):
    """End to end: `python -m kaibot.engine.watchdog` with another live watchdog of this host holding the file
    exits with code 3, says why and leaves the file to the first. No broker or Supabase call is made (the keys are
    fake and the URLs unreachable)."""
    import socket

    first = proc("python -m kaibot.engine.watchdog")
    data = tmp_path / "live"
    data.mkdir()
    (data / WATCHDOG_LOCK).write_text(json.dumps({"pid": first.pid, "host": socket.gethostname()}))
    env_file = tmp_path / "x.env"
    env_file.write_text("ALPACA_API_KEY=a\nALPACA_SECRET=b\nFRED_API_KEY=c\nSUPABASE_URL=http://127.0.0.1:9\n"
                        "SUPABASE_SERVICE_KEY=d\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAIBOT_")}
    env.update({"KAIBOT_ENV_FILE": str(env_file), "KAIBOT_DATA_DIR": str(data),
                "PYTHONPATH": str(ROOT / "src")})
    code = "import kaibot.engine.watchdog as w; w.WatchdogLock.claim.__defaults__ = (0,); w.main()"  # no 20 s wait
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 3, r.stdout + r.stderr
    assert f"another kaibot watchdog (pid {first.pid})" in r.stderr
    assert json.loads((data / WATCHDOG_LOCK).read_text())["pid"] == first.pid
