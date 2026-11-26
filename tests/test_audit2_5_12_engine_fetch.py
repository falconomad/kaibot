"""Audit 2, ticket 5.12 (#692): the engine starts the daily-bars fetch itself (engine/fetch.py).

The drill: the Mac sleeps from Tuesday 19:00 ET to Wednesday 10:00 ET, through both runs of the night job (03:15
and 07:45 Berlin, 21:15 and 01:45 ET). On waking the store has no bars for Tuesday, Wednesday's send window has not
opened, so the engine starts the fetch itself, and the store is complete long before 15:50 ET. The fetch here is a
stand-in command (no network in the tests) run the way the real script is run: a background process the loop only
polls. The lock the night job shares is checked on the real fetch_daily.sh and scripts/fetch_lock.py."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from kaibot.engine import fetch as ef

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
TUE = date(2026, 8, 4)


def prev(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def nxt(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


class World:
    """A clock, a daily store (one marker file per session's bars) and the events written."""

    def __init__(self, tmp_path, at: datetime):
        self.t, self.store, self.events = at, tmp_path / "store", []
        self.store.mkdir()

    def now(self):
        return self.t

    def missing(self, k, now, full=True):
        self.full = full
        return None if (self.store / str(k)).exists() else f"the daily store has no SPY bar for {k}"

    def fetch(self, tmp_path, cmd=None, **kw):
        script = tmp_path / "fake_fetch.py"
        script.write_text("import sys, time, pathlib\ntime.sleep(0.3)\n"
                          "pathlib.Path(sys.argv[1], sys.argv[2]).write_text('bars')\n")
        return ef.EngineFetch(self.missing, prev, nxt, lambda d: time(15, 50),
                              event=lambda k, b: self.events.append((k, b)), now=self.now,
                              cmd=cmd or [sys.executable, str(script), str(self.store), str(TUE)],
                              cwd=tmp_path, log_path=tmp_path / "logs" / "fetch_daily.log", **kw)


def _run(f, w, minutes=30):
    """The engine's loop: one pass a minute of the simulated clock until the fetch has ended."""
    for _ in range(minutes):
        f.step()
        if f.proc is None and w.events and w.events[-1][1].get("state") != "started":
            return
        if f.proc is not None:
            f.proc.wait(timeout=10)
        w.t += timedelta(minutes=1)
    raise AssertionError("the fetch never ended")


def test_drill_the_mac_asleep_through_both_night_fetches_has_the_store_complete_before_1550(tmp_path):
    w = World(tmp_path, datetime(2026, 8, 4, 19, 0, tzinfo=ET))
    f = w.fetch(tmp_path)
    f.step()  # the last loop pass before the Mac sleeps, Tuesday 19:00 ET
    w.t = datetime(2026, 8, 5, 10, 0, tzinfo=ET)  # it wakes at 10:00 ET on Wednesday: both night runs missed
    assert w.missing(TUE, w.now())  # no Tuesday bars
    _run(f, w)
    assert [b["state"] for k, b in w.events if k == "daily_fetch"] == ["started", "finished"]
    assert w.events[0][1]["session"] == str(TUE)
    assert w.missing(TUE, w.now()) is None and w.now() < datetime(2026, 8, 5, 15, 50, tzinfo=ET)
    assert w.events[0][1]["asked"] == "waking"
    assert "started by the engine (waking)" in (tmp_path / "logs" / "fetch_daily.log").read_text()
    f.wake("waking")  # a second wake with the store complete starts nothing
    f.step()
    assert f.proc is None and len(w.events) == 2


def test_nothing_is_started_without_a_wake_or_with_the_store_complete(tmp_path):
    w = World(tmp_path, datetime(2026, 8, 5, 10, 0, tzinfo=ET))
    f = w.fetch(tmp_path)
    f.step()
    assert f.proc is None and not w.events  # nobody asked: the loop's ordinary passes never fetch
    (w.store / str(TUE)).write_text("bars")
    f.wake("start")
    f.step()
    assert f.proc is None and not w.events


def test_once_the_send_window_has_opened_the_engine_does_not_fetch(tmp_path):
    w = World(tmp_path, datetime(2026, 8, 5, 15, 51, tzinfo=ET))
    f = w.fetch(tmp_path)
    f.wake("start")
    f.step()
    assert f.proc is None and not w.events


def test_before_the_deadline_the_bars_are_not_yet_due_so_nothing_is_fetched(tmp_path):
    # Tuesday 22:00 ET: Monday's bars are the due ones (the night job has until 03:30 ET for Tuesday's)
    w = World(tmp_path, datetime(2026, 8, 4, 22, 0, tzinfo=ET))
    (w.store / str(prev(TUE))).write_text("bars")
    f = w.fetch(tmp_path)
    f.wake("start")
    f.step()
    assert f.proc is None and not w.events


def test_a_failed_fetch_is_one_warning_event_and_the_next_wake_tries_again(tmp_path):
    w = World(tmp_path, datetime(2026, 8, 5, 9, 0, tzinfo=ET))
    f = w.fetch(tmp_path, cmd=[sys.executable, "-c", "raise SystemExit(3)"])
    f.wake("start")
    _run(f, w)
    assert w.events[-1][0] == "daily_fetch_failed" and w.events[-1][1]["exit"] == 3
    f.wake("waking")
    f.step()
    assert f.proc is not None
    f.proc.wait(timeout=10)


def test_a_fetch_past_its_bound_is_killed_with_its_whole_group(tmp_path):
    w = World(tmp_path, datetime(2026, 8, 5, 9, 0, tzinfo=ET))
    ticks = iter(range(0, 10_000, 600))
    f = w.fetch(tmp_path, cmd=["/bin/sh", "-c", "sleep 60 & sleep 60"], timeout_s=1200,
                clock=lambda: float(next(ticks)))
    f.wake("start")
    f.step()
    pid = f.proc.pid
    for _ in range(5):
        f.step()
    assert f.proc is None and w.events[-1][0] == "daily_fetch_failed" and w.events[-1][1]["exit"] == "timeout"
    assert w.events[0][1]["pid"] == pid


def test_the_engine_wires_it_only_with_the_daily_path_and_the_switch(monkeypatch):
    assert ef.for_engine(SimpleNamespace(daily=None)) is None
    daily = SimpleNamespace(load=lambda k, now: None, prev_session=prev, next_session=nxt,
                            send_from=lambda d: time(15, 50))
    eng = SimpleNamespace(daily=daily, store=SimpleNamespace(event=lambda *a: None), _rid=lambda: None,
                          now=lambda: datetime(2026, 8, 5, 10, tzinfo=ET))
    assert isinstance(ef.for_engine(eng), ef.EngineFetch)
    monkeypatch.setenv("KAIBOT_ENGINE_FETCH", "0")
    assert ef.for_engine(eng) is None
    src = (ROOT / "src" / "kaibot" / "engine" / "__main__.py").read_text()
    assert 'fetch.wake("start")' in src and "fetch.step()" in src


def test_a_loop_pass_more_than_an_hour_after_the_last_is_a_wake(tmp_path):
    """Waking is seen by the fetch itself: the wall clock jumped by more than engine/gap.py's GAP_S between passes."""
    w = World(tmp_path, datetime(2026, 8, 4, 19, 0, tzinfo=ET))  # Tuesday evening: nothing is due yet
    f = w.fetch(tmp_path)
    f.step()
    w.t += timedelta(minutes=1)
    f.step()
    assert f.proc is None and not w.events  # an ordinary pass is no wake
    w.t = datetime(2026, 8, 5, 10, 0, tzinfo=ET)  # the Mac slept through both night runs
    f.step()
    assert f.proc is not None and w.events[0][1]["asked"] == "waking"
    f.proc.wait(timeout=10)


# ---------------------------------------------------------------- the lock shared with the night job
def _hold(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_the_lock_runs_one_fetch_and_turns_a_second_away(tmp_path):
    lock, mark = tmp_path / "fetch_daily.lock", tmp_path / "ran"
    cmd = [sys.executable, str(ROOT / "scripts" / "fetch_lock.py"), str(lock), "--", sys.executable, "-c",
           f"open({str(mark)!r}, 'w').write('x')"]
    fd = _hold(lock)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "Another daily fetch is running" in out.stdout and not mark.exists()
    os.close(fd)  # the holder ends: the lock goes with it (no stale file to clean up)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and mark.exists()


def test_fetch_daily_sh_takes_the_lock_before_anything_else(tmp_path):
    """The real script, in a copy of the repository's layout: with the lock held (the night job downloading), the
    engine's run of fetch_daily.sh says so and ends at once, before it touches the .venv or the network."""
    (tmp_path / "scripts").mkdir()
    for n in ("fetch_daily.sh", "fetch_lock.py"):
        shutil.copy(ROOT / "scripts" / n, tmp_path / "scripts" / n)
    fd = _hold(tmp_path / "data" / "fetch_daily.lock")
    t0 = _time.monotonic()
    env = {k: v for k, v in os.environ.items() if k != "KAIBOT_FETCH_LOCKED"}
    out = subprocess.run(["/bin/bash", str(tmp_path / "scripts" / "fetch_daily.sh")], capture_output=True, text=True,
                         timeout=60, env=env)
    os.close(fd)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Another daily fetch is running" in out.stdout
    assert not (tmp_path / ".venv").exists() and _time.monotonic() - t0 < 30
    text = (ROOT / "scripts" / "fetch_daily.sh").read_text()
    assert text.index("fetch_lock.py") < text.index("[ -d .venv ]")


def test_close_to_the_window_only_the_quick_look_is_made(tmp_path):
    """Second-key review: within 20 minutes of the window the whole view is not read (it holds the loop)."""
    w = World(tmp_path, datetime(2026, 8, 5, 15, 40, tzinfo=ET))
    f = w.fetch(tmp_path)
    f.wake("waking")
    _run(f, w)
    assert w.full is False and w.events[0][1]["state"] == "started"
    (tmp_path / "b").mkdir()
    w2 = World(tmp_path / "b", datetime(2026, 8, 5, 10, 0, tzinfo=ET))
    f2 = w2.fetch(tmp_path / "b")
    f2.wake("start")
    _run(f2, w2)
    assert w2.full is True


def test_the_real_missing_check_reads_the_whole_view_only_when_asked():
    from kaibot.engine.live_daily import DataNotReady

    calls = []

    class Load:
        def available(self, k, now):
            calls.append("quick")
            return None

        def __call__(self, k, now):
            calls.append("full")
            raise DataNotReady("the daily store holds 2026-08-04 for only 10 of 300 stocks")

    daily = SimpleNamespace(load=Load(), prev_session=prev, next_session=nxt, send_from=lambda d: time(15, 50))
    eng = SimpleNamespace(daily=daily, store=SimpleNamespace(event=lambda *a: None), _rid=lambda: None,
                          now=lambda: datetime(2026, 8, 5, 10, tzinfo=ET))
    f = ef.for_engine(eng)
    assert "only 10 of 300" in f.missing(TUE, eng.now()) and calls == ["quick", "full"]
    calls.clear()
    assert f.missing(TUE, eng.now(), full=False) is None and calls == ["quick"]
