"""Audit 2 ticket 7.4 (#714): a disk-free alarm, the research runner's own Healthchecks check, rotated runner logs.

- scripts/healthcheck.py: under 10 GB free on the repo's volume, `watch` sends /fail (at most every 30 minutes) and no
  success ping while it stays low; `healthcheck.py disk` is the same check once (exit 1, never a success ping).
- scripts/research_runner.py: each tick rotates data/runner/*.log at 10 MB (5 kept) and pings the runner check:
  success at the end of a good tick and while a long job runs, /fail when refused, paused, unreadable or the disk low.

The drill of the ticket ("fill a test volume and the alarm fires") is played with a limit above the free space of the
test's own volume: the same comparison, without filling a real disk.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import healthcheck as hc  # noqa: E402
import research_runner as rr  # noqa: E402

GB = 10**9


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakePinger:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, kind, body=""):
        self.sent.append((kind, body))

    def wait(self, timeout=0):
        pass


# ---------------------------------------------------------------- healthcheck.py: the disk


def test_the_limit_is_10_gb_as_macos_counts():
    assert hc.DISK_MIN_GB == 10
    assert hc.disk_problem(10 * GB) is None
    assert hc.disk_problem(10 * GB - 1).startswith("disk low: 10.0 GB free")
    assert hc.disk_problem(3_200_000_000, path="/Users/x") == "disk low: 3.2 GB free on /Users/x (limit 10 GB)"
    assert hc.disk_problem(None) is None  # unreadable is not an alarm


def test_disk_free_reads_the_volume_and_never_raises(tmp_path):
    assert hc.disk_free(str(tmp_path)) > 0
    assert hc.disk_free(str(tmp_path / "missing" / "deeper")) is None


def test_watch_fails_on_a_low_disk_withholds_success_and_repeats_at_most_every_30_minutes():
    now, pings, free = Clock(), [], [50 * GB]
    mon = hc.Monitor(lambda k, b="": pings.append((k, b)), now=now, disk=lambda: free[0])
    now.t += 301
    mon.periodic(True)
    assert pings == [("success", "")]
    free[0] = 4 * GB  # the disk fills
    for _ in range(30):  # 30 minutes of polls, once a minute
        now.t += 61
        mon.periodic(True)
    fails = [b for k, b in pings if k == "fail"]
    assert len(fails) == 1 and fails[0].startswith("kaibot disk_low: disk low: 4.0 GB free")
    assert [k for k, _ in pings].count("success") == 1  # none while low: the check stays red
    now.t += 61 * 5
    mon.periodic(True)
    assert [b for k, b in pings if k == "fail"][1:] != []  # after 30 minutes, the alarm again
    free[0] = 40 * GB  # room again
    now.t += 61
    mon.periodic(True)
    assert pings[-1] == ("success", "")


def test_watch_reads_the_disk_once_a_minute_not_every_poll():
    now, calls = Clock(), []
    mon = hc.Monitor(lambda *a: None, now=now, disk=lambda: calls.append(1) or 50 * GB)
    for _ in range(59):
        now.t += 1
        mon.periodic(True)
    assert len(calls) == 1


@pytest.fixture
def server():
    got: list[tuple[str, str]] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append((self.path, self.rfile.read(n).decode()))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/uuid", got
    srv.shutdown()


def _cli(args, url, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KAIBOT_", "HEALTHCHECKS_"))}
    env["HEALTHCHECKS_PING_URL"] = url
    return subprocess.run([sys.executable, str(SCRIPTS / "healthcheck.py"), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


def test_drill_a_full_volume_sends_fail(server, tmp_path):
    """The drill: a volume with less free space than the limit (here: a limit above what the test volume has)."""
    url, got = server
    free_gb = hc.disk_free(str(tmp_path)) / GB
    r = _cli(["disk", "--disk", str(tmp_path), "--min-gb", str(free_gb * 2 + 1)], url, tmp_path)
    assert r.returncode == 1 and "disk low:" in r.stderr and url not in r.stdout + r.stderr
    assert got[-1][0] == "/uuid/fail" and got[-1][1].startswith("kaibot disk_low: disk low:")


def test_a_disk_with_room_sends_nothing_not_even_success(server, tmp_path):
    url, got = server
    r = _cli(["disk", "--disk", str(tmp_path), "--min-gb", "0.000001"], url, tmp_path)
    assert r.returncode == 0 and got == []


def test_watch_on_a_full_volume_sends_fail_after_start(server, tmp_path):
    url, got = server
    logf = tmp_path / "engine.log"
    logf.write_text("")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    free_gb = hc.disk_free(str(tmp_path)) / GB
    w = _cli_popen(["watch", "--pid", str(proc.pid), "--log", str(logf), "--disk", str(tmp_path),
                    "--min-gb", str(free_gb * 2 + 1)], url, tmp_path)
    try:
        deadline = time.time() + 20
        while "/uuid/fail" not in [p for p, _ in got] and time.time() < deadline:
            time.sleep(0.2)
    finally:
        proc.kill()
        proc.wait()  # reaped: the watcher sees it gone and stops
    w.wait(timeout=20)
    paths = [p for p, _ in got]
    assert "/uuid/start" in paths and "/uuid/fail" in paths and "/uuid" not in paths  # pings are sent in parallel


def _cli_popen(args, url, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KAIBOT_", "HEALTHCHECKS_"))}
    env["HEALTHCHECKS_PING_URL"] = url
    return subprocess.Popen([sys.executable, str(SCRIPTS / "healthcheck.py"), *args], cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- research_runner.py: log rotation


def test_runner_logs_rotate_at_10_mb_keeping_5(tmp_path):
    assert rr.LOG_MAX_BYTES == 10 * 1024 * 1024 and rr.LOG_KEEP == 5
    log = tmp_path / "runner.log"
    small = tmp_path / "gh.log"
    small.write_text("x")
    for gen in range(7):
        log.write_bytes(bytes([65 + gen]) * 1000)
        assert rr.rotate_logs(tmp_path, max_bytes=1000) == ["runner.log"]
        assert log.stat().st_size == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["gh.log", "runner.log", *(f"runner.log.{i}" for i in range(1, 6))]
    assert (tmp_path / "runner.log.1").read_bytes()[:1] == b"G"  # the newest
    assert (tmp_path / "runner.log.5").read_bytes()[:1] == b"C"  # the oldest kept; A and B are gone
    assert small.read_text() == "x"


def test_rotation_truncates_in_place_so_an_open_append_handle_keeps_working(tmp_path):
    log = tmp_path / "runner.log"
    log.write_bytes(b"a" * 2000)
    with open(log, "ab") as f:  # launchd's stdout for this tick
        rr.rotate_logs(tmp_path, max_bytes=1000)
        f.write(b"after\n")
    assert log.read_bytes() == b"after\n"
    assert (tmp_path / "runner.log.1").stat().st_size == 2000


def test_job_logs_and_links_are_left_alone(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "job.log").write_bytes(b"x" * 2000)
    (tmp_path / "elsewhere.txt").write_bytes(b"y" * 2000)
    (tmp_path / "link.log").symlink_to(tmp_path / "elsewhere.txt")
    assert rr.rotate_logs(tmp_path, max_bytes=1000) == []


def test_a_tick_rotates_under_its_lock(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "runner.log").write_bytes(b"z" * (rr.LOG_MAX_BYTES + 1))
    root = tmp_path / "repo"
    root.mkdir()
    q = tmp_path / "queue.yaml"
    q.write_text("jobs: []\n")
    rr.tick(root=root, state=state, queue=q, pull=False, run=_no_git, python="/p", engines=[])
    assert (state / "runner.log.1").stat().st_size == rr.LOG_MAX_BYTES + 1
    assert "rotated (5 kept)" in (state / "runner.log").read_text()


def _no_git(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


# ---------------------------------------------------------------- research_runner.py: the runner's check


def test_the_ping_url_comes_from_the_env_or_the_state_file_never_the_secrets(tmp_path):
    assert rr.ping_url(tmp_path, env={}) == ""
    (tmp_path / rr.PING_FILE).write_text("https://hc-ping.com/abc/\nignored\n")
    assert rr.ping_url(tmp_path, env={}) == "https://hc-ping.com/abc"
    assert rr.ping_url(tmp_path, env={rr.PING_ENV: "https://hc-ping.com/env"}) == "https://hc-ping.com/env"
    (tmp_path / rr.PING_FILE).write_text("http://plain.example/x\n")
    assert rr.ping_url(tmp_path, env={}) == ""  # https only
    assert ".env" not in rr.PING_FILE and "kaibot-secrets" not in rr.ping_url.__doc__


@pytest.mark.parametrize(("res", "expect"), [
    ({"job-a": "ok"}, ("success", "research runner tick: job-a ok")),
    ({}, ("success", "research runner tick: nothing to run")),
    ({"_paused": "the checkout holds changes: docs/x.md"}, ("fail", "kaibot runner paused: the checkout holds")),
    ({"_refused": "/r is the engine's checkout"}, ("fail", "kaibot runner refused: /r is the engine's")),
    ({"_error": "config"}, ("fail", "kaibot runner cannot read queue or allow-list: config")),
])
def test_each_tick_pings_success_or_fail(res, expect):
    p = FakePinger()
    rr.report(p, res, 100 * GB)
    assert len(p.sent) == 1 and p.sent[0][0] == expect[0] and p.sent[0][1].startswith(expect[1])


def test_a_low_disk_fails_the_runner_check_too():
    p = FakePinger()
    assert rr.report(p, {"job-a": "ok"}, 2 * GB).startswith("disk low: 2.0 GB free")
    assert p.sent == [("fail", "kaibot disk low: 2.0 GB free (limit 10 GB)")]


def test_a_busy_lock_pings_nothing():
    p = FakePinger()
    assert rr.report(p, {"_locked": "busy"}, 100 * GB) is None and p.sent == []


def test_a_long_job_keeps_the_check_up():
    p = FakePinger()
    with rr.keepalive(p, every_s=0.05):
        time.sleep(0.3)
    n = len(p.sent)
    assert n >= 3 and all(k == "success" for k, _ in p.sent)
    time.sleep(0.15)
    assert len(p.sent) == n  # stops with the tick


def test_main_without_a_check_set_up_pings_nothing_and_needs_no_network(tmp_path, monkeypatch):
    monkeypatch.delenv(rr.PING_ENV, raising=False)
    monkeypatch.setattr(rr, "STATE", tmp_path)
    monkeypatch.setattr(rr, "tick", lambda pull: {"job-a": "ok"})
    assert isinstance(rr.Pinger, type)
    sent = []
    monkeypatch.setattr(rr, "Pinger", lambda url: sent.append(url))
    assert rr.main(["--no-pull"]) == 0 and sent == []
