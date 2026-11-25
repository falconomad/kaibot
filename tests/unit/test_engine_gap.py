"""QA-Ops (the 24-25 Nov 2026 incident): the Mac mini slept for about 17 hours with the engine's heartbeat stopped,
and nothing said so afterwards. engine/gap.py writes one `engine_gap` event (and an `engine gap:` WARNING, a
Healthchecks.io fail ping) when the engine was not running for more than an hour while it should have been: at a
start after a last heartbeat that was not a clean stop's, or on waking with the process still running."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fakes import FakeStore

from kaibot.engine import __main__ as engine_main
from kaibot.engine import event_names
from kaibot.engine.gap import GAP_S, GapWatch

T0 = datetime(2026, 11, 24, 22, 45, tzinfo=UTC)  # 23:45 Berlin


class Remote:
    """Supabase's heartbeat table as the engine reads it (one row per process), or unreachable."""

    def __init__(self, rows=(), fail=False):
        self.rows, self.fail, self.asked = list(rows), fail, []

    def select(self, table, params):
        self.asked.append((table, params))
        if self.fail:
            raise OSError("connection refused")
        return [r for r in self.rows if f"eq.{r['process']}" == params.get("process")]


def beat(ts, state="managing", process="engine", as_text=True):
    return {"process": process, "ts": ts.isoformat().replace("+00:00", "Z") if as_text else ts,
            "detail": {"state": state, "host": "mini"}}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def watch(remote, now):
    store = FakeStore()
    w = GapWatch(store, lambda: 7, "mini", process="engine", now=now)
    w.before_start(remote)
    return w, store


def gaps(store):
    return [r for r in store.tables.get("event", []) if r["type"] == "engine_gap"]


def test_a_start_17_hours_after_a_running_heartbeat_writes_one_engine_gap(caplog):
    now = Clock(T0 + timedelta(hours=17, minutes=5))
    w, store = watch(Remote([beat(T0)]), now)
    with caplog.at_level(logging.WARNING, "kaibot.engine"):
        out = w.after_start()
    (ev,) = gaps(store)
    assert ev["run_id"] == 7 and ev["payload"] == out
    assert out["gap_s"] == 17 * 3600 + 300 and out["gap_h"] == 17.08 and out["seen"] == "start"
    assert out["since"].startswith("2026-11-24T22:45") and out["last_state"] == "managing"
    assert out["last_host"] == "mini" and out["host"] == "mini"
    (line,) = [r.getMessage() for r in caplog.records if "engine gap" in r.getMessage()]
    assert line.startswith("engine gap: no engine heartbeat for 17 h 05 min")
    assert w.tick() is None and len(gaps(store)) == 1  # reported once, not again at the first pass


def test_a_heartbeat_ts_already_a_datetime_counts_too():
    w, store = watch(Remote([beat(T0, as_text=False)]), Clock(T0 + timedelta(hours=2)))
    assert w.after_start()["gap_s"] == 7200 and len(gaps(store)) == 1


def test_a_clean_stop_before_the_start_is_no_gap_however_long():
    """service.sh stop / hold, Ctrl-C: the last heartbeat says `stopped`; the engine was not meant to run."""
    w, store = watch(Remote([beat(T0, state="stopped")]), Clock(T0 + timedelta(days=3)))
    assert w.after_start() is None and not gaps(store)


def test_a_restart_within_the_hour_is_no_gap():
    """A crash that launchd restarts in about a minute, or a restart for a release."""
    w, store = watch(Remote([beat(T0)]), Clock(T0 + timedelta(seconds=GAP_S)))
    assert w.after_start() is None and not gaps(store)


def test_no_heartbeat_row_or_an_unreadable_one_is_no_check_and_never_raises(caplog):
    caplog.set_level(logging.INFO, "kaibot.engine")
    for remote in (Remote(), Remote([beat(T0, process="watchdog")]), Remote(fail=True), None):
        w, store = watch(remote, Clock(T0 + timedelta(hours=9)))
        assert w.after_start() is None and not gaps(store)
    assert "last heartbeat unreadable at start" in caplog.text


def test_the_read_uses_the_quick_client_when_the_store_has_one():
    fast = Remote([beat(T0)])

    class Slow(Remote):
        def quick(self):
            return fast

    w, _ = watch(Slow(fail=True), Clock(T0 + timedelta(hours=2)))
    assert fast.asked == [("heartbeat", {"process": "eq.engine", "limit": "1"})]
    assert w.prior["ts"] == T0


def test_waking_with_the_engine_still_running_writes_one_engine_gap():
    """The incident itself: the process does not restart when the Mac wakes; two loop passes 17 hours apart."""
    now = Clock(T0)
    w, store = watch(Remote(), now)
    w.after_start()
    for _ in range(3):  # normal passes, about 10 s apart
        now.t += timedelta(seconds=10)
        assert w.tick() is None
    now.t += timedelta(hours=17)
    out = w.tick()
    assert out["seen"] == "running" and out["gap_s"] == 17 * 3600 and "last_state" not in out
    now.t += timedelta(seconds=10)
    assert w.tick() is None and len(gaps(store)) == 1


def test_a_failing_event_write_never_reaches_the_engine():
    class Broken(FakeStore):
        def event(self, *a, **k):
            raise OSError("disk full")

    w = GapWatch(Broken(), lambda: None, "mini", now=Clock(T0 + timedelta(hours=3)))
    w.before_start(Remote([beat(T0)]))
    assert w.after_start()["gap_s"] == 3 * 3600


def test_engine_gap_is_a_registered_pending_health_event_with_a_severity():
    assert "engine_gap" in {e.name for e in event_names.PENDING}
    assert event_names.severity("engine_gap") == "warn"


def test_the_engine_reads_the_heartbeat_before_it_starts_and_watches_every_pass():
    """Wiring in engine/__main__.main: before start_or_refuse (before its own first heartbeat), after it, and at
    the top of every loop pass, never able to stop the loop."""
    src = Path(engine_main.__file__).read_text()
    body = src.split("def main() -> None:", 1)[1]
    guard = "with contextlib.suppress(Exception):"
    assert f"{guard}  # a report only: never able to stop a start\n        gaps.before_start(" in body
    assert f"{guard}\n        gaps.after_start()" in body
    i_before, i_start = body.index("gaps.before_start("), body.index("mode = start_or_refuse(")
    i_after, i_loop = body.index("gaps.after_start()"), body.index('while not stop["now"]:')
    assert i_before < i_start < i_after < i_loop < body.index("gaps.tick()") < body.index("beat(status)")
    assert "with contextlib.suppress(Exception):\n            gaps.tick()" in body
