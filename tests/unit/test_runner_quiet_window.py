"""Audit 2 ticket 7.8 (#718): heavy runner jobs keep out of the engine's daily send.

- The launchd job runs as `ProcessType Background` with `LowPriorityIO` (macOS throttles it whenever the engine
  wants the CPU or the disk).
- No heavy job runs from 30 minutes before a session's close to 10 minutes after (15:30-16:10 ET; 12:30-13:10 on a
  13:00 close; nothing on a holiday): one due then is deferred to a later tick, and one already running is paused
  (SIGSTOP to its process group) for the window and resumed after it, the pause not counted against its timeout.

The soak of the ticket (a 240-minute job, no engine tick more than 10 s late in the window) needs the Mac; here the
pause is checked on a real process: it writes nothing while it is stopped.
"""

from __future__ import annotations

import plistlib
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import research_runner as rr  # noqa: E402


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


# ---------------------------------------------------------------- the plist


def test_the_plist_runs_the_runner_in_the_background_with_low_priority_io():
    p = plistlib.loads((ROOT / "ops" / "runner" / "com.kaibot.research-runner.plist").read_bytes())
    assert p["ProcessType"] == "Background" and p["LowPriorityIO"] is True
    assert p["StartInterval"] == 900  # unchanged


# ---------------------------------------------------------------- the window


@pytest.mark.parametrize(("at", "window"), [
    (utc(2026, 11, 25, 15, 0), (utc(2026, 11, 25, 20, 30), utc(2026, 11, 25, 21, 10))),  # EST: 15:30-16:10 ET
    (utc(2026, 7, 15, 12, 0), (utc(2026, 7, 15, 19, 30), utc(2026, 7, 15, 20, 10))),     # EDT
    (utc(2026, 11, 27, 12, 0), (utc(2026, 11, 27, 17, 30), utc(2026, 11, 27, 18, 10))),  # 13:00 close: 12:30-13:10
    (utc(2026, 11, 26, 20, 45), None),  # Thanksgiving: no session, no window
    (utc(2026, 11, 28, 20, 45), None),  # Saturday
    (utc(2026, 11, 25, 21, 10), None),  # over
])
def test_the_quiet_window_follows_the_nyse_close(at, window):
    assert rr.quiet_window(at) == window


def test_in_quiet_only_inside():
    assert rr.in_quiet(utc(2026, 11, 25, 20, 29, 59)) is None
    assert rr.in_quiet(utc(2026, 11, 25, 20, 30)) is not None
    assert rr.in_quiet(utc(2026, 11, 25, 21, 9, 59)) is not None
    assert rr.in_quiet(utc(2026, 11, 25, 21, 10)) is None


# ---------------------------------------------------------------- deferring and pausing


class FakeRun:
    def __call__(self, cmd, **kw):
        import subprocess

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


class FakeProc:
    pid, returncode = 4242, 0

    def wait(self, timeout=None):
        return 0


class FakePopen:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        kw["stdout"].write("done\n")
        return FakeProc()


def _tick(tmp_path, monkeypatch, at, jobs):
    monkeypatch.setenv(rr.ENGINE_CHECKOUT_ENV, str(tmp_path / "engine"))
    monkeypatch.setattr(rr, "ENGINE_SERVICE_PLIST", str(tmp_path / "no.plist"))
    monkeypatch.setattr(rr, "wall_now", lambda: at)
    monkeypatch.setattr(rr, "post", lambda *a, **k: False)
    q = tmp_path / "queue.yaml"
    q.write_text(yaml.safe_dump({"jobs": jobs}))
    popen = FakePopen()
    res = rr.tick(state=tmp_path / "state", queue=q, pull=False, run=FakeRun(), popen=popen, python="/venv/python")
    return res, popen


HEAVY = {"id": "heavy-1", "script": "scripts/index_core.py", "args": ["--fixture"], "posts_to_issue": 600}


def test_the_heavy_jobs_of_the_allow_list_are_marked():
    cfg = rr.load_allowed()
    assert cfg.specs["scripts/index_core.py"].heavy


def test_a_heavy_job_due_in_the_window_is_deferred_not_done(tmp_path, monkeypatch):
    res, popen = _tick(tmp_path, monkeypatch, utc(2026, 11, 25, 20, 45), [HEAVY])
    assert res == {"heavy-1": "deferred"} and popen.cmds == []
    assert "quiet window" in (tmp_path / "state" / "runner.log").read_text()
    assert "heavy-1" not in rr.load_done(tmp_path / "state")  # it runs at a later tick


def test_the_same_job_runs_before_or_after_the_window_and_on_a_holiday(tmp_path, monkeypatch):
    for i, at in enumerate([utc(2026, 11, 25, 20, 29), utc(2026, 11, 25, 21, 10), utc(2026, 11, 26, 20, 45)]):
        d = tmp_path / str(i)
        d.mkdir()
        res, popen = _tick(d, monkeypatch, at, [HEAVY])
        assert res == {"heavy-1": "ok"} and len(popen.cmds) == 1, at


def test_a_running_heavy_job_is_stopped_for_the_window_and_resumed(tmp_path, monkeypatch):
    """A real process writes a line every 50 ms; the window starts 0.6 s after it does and lasts 1.2 s."""
    import subprocess

    t0 = datetime.now(UTC)
    lo, hi = t0 + timedelta(seconds=0.6), t0 + timedelta(seconds=1.8)
    monkeypatch.setattr(rr, "wall_now", lambda: datetime.now(UTC))
    monkeypatch.setattr(rr, "quiet_window", lambda at: (lo, hi) if at < hi else None)
    out = tmp_path / "ticks.txt"
    code = (f"import time\nend = time.time() + 2.6\nwith open({str(out)!r}, 'a') as f:\n"
            "    while time.time() < end:\n        f.write(f'{time.time()}\\n'); f.flush(); time.sleep(0.05)\n")
    spec = rr.Spec("scripts/x.py", {}, False, True, False, timeout_min=1)
    job = rr.Job("q", spec, (), 600)
    res = rr.run_job(job, [sys.executable, "-c", code], tmp_path / "job.log", popen=subprocess.Popen, env={})
    assert res["status"] == "ok" and res["rc"] == 0 and "paused_minutes" in res
    ticks = [float(x) for x in out.read_text().split()]
    quiet = [t for t in ticks if lo.timestamp() + 0.2 < t < hi.timestamp() - 0.05]
    assert quiet == []  # nothing ran while it was stopped
    assert any(t > hi.timestamp() for t in ticks)  # and it went on after
    log = (tmp_path / "job.log").read_text()
    assert "paused for the quiet window" in log and "resumed" in log


def test_a_light_job_is_never_paused(tmp_path, monkeypatch):
    import subprocess

    t0 = datetime.now(UTC)
    monkeypatch.setattr(rr, "wall_now", lambda: datetime.now(UTC))
    monkeypatch.setattr(rr, "quiet_window", lambda at: (t0, t0 + timedelta(hours=1)))
    spec = rr.Spec("scripts/x.py", {}, False, False, False, timeout_min=1)
    start = time.time()
    res = rr.run_job(rr.Job("l", spec, (), 600), [sys.executable, "-c", "print(1)"], tmp_path / "l.log",
                     popen=subprocess.Popen, env={})
    assert res["status"] == "ok" and "paused_minutes" not in res and time.time() - start < 10
