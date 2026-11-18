"""#323 (written before the code): RoomFlow reads the LLM budget state off the engine's main loop, in the room's
worker pool; at most one read in flight, at most every 5 minutes; fail closed (rules_only) until the session's
first result and when the last one is stale; no_entries halts entries on the main loop; every session reads again."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace

from kaibot.engine.roomflow import STATE_EVERY_MIN, STATE_STALE_MIN, RoomFlow


class Gw:
    def __init__(self, state="ok", block: threading.Event | None = None, fail=False):
        self.state_value, self.block, self.fail = state, block, fail
        self.calls, self.threads = 0, []

    def state(self):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.block is not None:
            self.block.wait(5)
        if self.fail:
            raise RuntimeError("supabase down")
        return {"state": self.state_value}


class Eng:
    def __init__(self, day=date(2026, 11, 18)):
        self.day = SimpleNamespace(day=day)
        self.halts = []

    def _halt_entries(self, kind, reason, detail=None):
        self.halts.append((kind, threading.current_thread()))


def flow(gw, eng=None):
    rf = RoomFlow(eng or Eng(), SimpleNamespace(gw=gw))
    rf.state_ex = ThreadPoolExecutor(max_workers=1)
    return rf


def settle(rf, m, tries=200):
    """Call mode(m) until the read in flight has been taken (the worker answers within milliseconds)."""
    out = rf.mode(m)
    for _ in range(tries):
        if rf._state_fut is None:
            return rf.mode(m)
        time.sleep(0.005)
        out = rf.mode(m)
    return out


def test_the_read_runs_in_the_worker_pool_and_the_first_answer_is_fail_closed():
    gw = Gw("ok", block=threading.Event())
    rf = flow(gw)
    assert rf.mode(0) == "rules_only"  # nothing known yet this session: fail closed
    gw.block.set()
    assert settle(rf, 0) == "ok"
    assert gw.calls == 1 and gw.threads[0] is not threading.main_thread()


def test_a_hung_read_never_holds_the_caller_and_only_one_is_in_flight():
    gw = Gw("ok", block=threading.Event())
    rf = flow(gw)
    t0 = time.monotonic()
    for m in range(0, 40):
        rf.mode(m)
    assert time.monotonic() - t0 < 0.5  # forty calls while the read hangs: no waiting at all
    assert gw.calls == 1  # never a second read while one is in flight
    gw.block.set()


def test_a_stale_state_is_rules_only():
    gw = Gw("ok")
    rf = flow(gw)
    assert settle(rf, 0) == "ok"
    gw.block = threading.Event()  # the next reads hang
    assert rf.mode(STATE_EVERY_MIN) == "ok"  # a read starts; the last result is still fresh
    assert rf.mode(STATE_STALE_MIN + 1) == "rules_only"  # nothing new for 15 minutes: fail closed
    gw.block.set()


def test_a_failed_read_is_rules_only():
    rf = flow(Gw(fail=True))
    assert settle(rf, 0) == "rules_only"


def test_no_entries_halts_on_the_main_loop():
    eng = Eng()
    rf = flow(Gw("no_entries"), eng)
    assert settle(rf, 0) == "no_entries"
    assert [k for k, _ in eng.halts] == ["llm_budget"] and eng.halts[0][1] is threading.main_thread()


def test_every_session_reads_again():
    gw, eng = Gw("ok"), Eng()
    rf = flow(gw, eng)
    assert settle(rf, 380) == "ok" and gw.calls == 1
    eng.day = SimpleNamespace(day=date(2026, 11, 19))  # the next session: m starts at 0 again
    gw.state_value = "rules_only"
    assert settle(rf, 0) == "rules_only" and gw.calls == 2


def test_reads_at_most_every_five_minutes():
    gw = Gw("ok")
    rf = flow(gw)
    for m in range(0, STATE_EVERY_MIN * 3):
        settle(rf, m)
    assert gw.calls == 3


def test_a_slow_read_is_as_old_as_its_start_not_its_answer():
    """QA-Ops on #326: a read that started at minute 0 and answers at minute 14 says what the budget was at minute 0;
    it must count as stale from minute 16, not from minute 30."""
    gw = Gw("ok", block=threading.Event())
    rf = flow(gw)
    assert rf.mode(0) == "rules_only"  # the read starts at minute 0 and hangs
    gw.block.set()
    for _ in range(200):  # it answers; it is taken at minute 14
        if rf._state_fut is not None and rf._state_fut.done():
            break
        time.sleep(0.005)
    gw.block = threading.Event()  # every later read hangs
    assert rf.mode(14) == "ok"  # 14 minutes old: still inside STATE_STALE_MIN
    assert rf.mode(STATE_STALE_MIN + 1) == "rules_only"  # 16 minutes after the read started: fail closed
    gw.block.set()


def test_a_minute_before_the_last_result_is_rules_only():
    """QA-Ops on #326: if the session minute goes backwards (a session change the day check missed), the last
    result is not taken as fresh."""
    rf = flow(Gw("ok"))
    assert settle(rf, 300) == "ok"
    assert rf.mode(10) == "rules_only"
