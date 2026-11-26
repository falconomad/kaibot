"""Audit 2 ticket 7.6 (#716): one SEC request window for every process on the machine.

SEC counts 10 requests a second per IP. Each process had its own `sec_edgar` budget (5 a second), so the engine's
8-K feed, the nightly collector and a research runner job together could pass 10. Now `sec_edgar` also takes a slot
from `SharedWindow`: a file under a flock in data/ratelimit (KAIBOT_RATELIMIT_DIR), 8 a second in total.

Verified by: three concurrent processes, each asking as fast as its own budget allows, stay under 10 requests in any
second together (and the same three processes without the shared file do not, so the test can fail).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import threading
from bisect import bisect_left
from pathlib import Path

import pytest

from kaibot.common import http, ratelimit
from kaibot.common.ratelimit import Budget, BudgetSpec, Registry, SharedWindow, load_specs

ROOT = Path(__file__).resolve().parents[2]


class Wall:
    """A shared fake wall clock: sleep moves it on. Thread-safe."""

    def __init__(self, t: float = 1_000.0):
        self.t, self.lock = t, threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.t

    def sleep(self, s: float) -> None:
        with self.lock:
            self.t += s


def most_in_a_second(times: list[float], per: float = 1.0) -> int:
    ts = sorted(times)
    return max((bisect_left(ts, t + per) - i for i, t in enumerate(ts)), default=0)


# ---------------------------------------------------------------- the configuration


def test_sec_edgar_is_capped_for_the_whole_machine_below_secs_10_a_second():
    spec = load_specs()["sec_edgar"]
    assert spec.windows == ((5, 1.0),)  # per process, as before
    assert spec.shared_per_s is not None and spec.shared_per_s < 10


def test_every_sec_caller_named_in_the_ticket_uses_the_sec_edgar_budget_and_no_private_one():
    """fetch_sec_fundamentals, fetch_listings, collect_alt (form4, listings), fetch_earnings_dates (earnings_sec,
    spinoff_sec) and the engine's 8-K feed (edgar_8k_feed) all ask through common.http with source "sec_edgar", so
    they all get the registry's budget and its shared window; none passes a `budget=` of its own to an SEC call."""
    from kaibot.ingest import earnings_sec, edgar_8k_feed, sec_fundamentals, spinoff_sec
    from kaibot.ingest.alt import form4, listings

    assert {earnings_sec.BUDGET, edgar_8k_feed.BUDGET, sec_fundamentals.BUDGET, spinoff_sec.BUDGET, form4.BUDGET,
            listings.BUDGET_SEC} == {"sec_edgar"}
    files = ["src/kaibot/ingest/earnings_sec.py", "src/kaibot/ingest/edgar_8k_feed.py",
             "src/kaibot/ingest/spinoff_sec.py",
             "src/kaibot/ingest/alt/form4.py", "src/kaibot/ingest/alt/listings.py", "scripts/fetch_sec_fundamentals.py",
             "scripts/fetch_fundamentals.py"]
    for f in files:
        text = (ROOT / f).read_text()
        for call in re.findall(r"http\.get(?:_json)?\((?:[^()]|\([^()]*\))*\)", text, flags=re.S):
            if re.search(r"source=(BUDGET|BUDGET_SEC|sf\.BUDGET|\"sec_edgar\")", call):
                assert "budget=" not in call, f"{f}: an SEC call with a private budget: {call[:120]}"


def test_the_registry_gives_sec_edgar_a_shared_window_in_the_configured_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIBOT_RATELIMIT_DIR", str(tmp_path))
    b = Registry().get("sec_edgar")
    assert b.shared is not None and b.shared.path == tmp_path / "sec_edgar.slots" and b.shared.capacity < 10


def test_a_fake_clock_never_touches_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIBOT_RATELIMIT_DIR", str(tmp_path))

    class Clock:
        def monotonic(self):
            return 0.0

        def sleep(self, s):
            pass

    assert Registry(clock=Clock()).get("sec_edgar").shared is None
    assert Registry().get("alpaca").shared is None  # only sources with shared_budget_per_s


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, "8"])
def test_a_bad_shared_value_is_refused(bad):
    with pytest.raises(ValueError, match="shared_budget_per_s"):
        BudgetSpec.from_mapping({"budget_per_s": 5, "shared_budget_per_s": bad})


# ---------------------------------------------------------------- the window itself (fake wall clock)


def test_many_windows_on_one_file_never_grant_more_than_capacity_in_a_second(tmp_path):
    wall = Wall()
    path = tmp_path / "x.slots"
    wins = [SharedWindow(path, 8, wall=wall, sleep=lambda s: None) for _ in range(3)]  # three "processes"
    slots = [wins[i % 3].reserve() for i in range(100)]
    assert slots == sorted(slots)
    assert most_in_a_second(slots) == 8
    assert min(b - a for a, b in zip(slots, slots[1:], strict=False)) >= 1 / 8 - 1e-9  # spaced, never in bursts
    assert slots[-1] - slots[0] >= (100 // 8) - 1


def test_threads_on_one_file_keep_the_window(tmp_path):
    wall = Wall()
    path = tmp_path / "x.slots"
    got, lock = [], threading.Lock()

    def worker():
        w = SharedWindow(path, 8, wall=wall, sleep=lambda s: None)
        for _ in range(20):
            t = w.reserve()
            with lock:
                got.append(t)

    ts = [threading.Thread(target=worker) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(got) == 120 and most_in_a_second(got) <= 8


def test_backoff_holds_every_process_and_is_capped(tmp_path):
    wall = Wall()
    a = SharedWindow(tmp_path / "x.slots", 8, wall=wall, sleep=lambda s: None)
    b = SharedWindow(tmp_path / "x.slots", 8, wall=wall, sleep=lambda s: None)
    a.backoff(20)
    assert b.reserve() == pytest.approx(1_020.0)
    a.backoff(3600)  # a Retry-After of an hour is held to 60 s, as common.http does
    assert json.loads((tmp_path / "x.slots").read_text())["blocked_until"] <= wall() + 60


def test_old_slots_expire_and_a_clock_jump_does_not_hold_anyone(tmp_path):
    wall = Wall()
    p = tmp_path / "x.slots"
    p.write_text(json.dumps({"slots": [wall() + 10_000.0] * 8, "blocked_until": wall() + 10_000.0}))
    w = SharedWindow(p, 8, wall=wall, sleep=lambda s: None)
    assert w.reserve() == wall()  # slots and backoff hours ahead are a clock that went back: dropped


def test_a_damaged_file_starts_again(tmp_path):
    p = tmp_path / "x.slots"
    p.write_text("not json{")
    assert SharedWindow(p, 8, wall=Wall()).reserve() == 1_000.0
    assert json.loads(p.read_text())["slots"] == [1_000.0]


def test_an_unusable_folder_falls_back_to_the_process_budget_and_says_so_once(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("")
    w = SharedWindow(blocker / "sub" / "x.slots", 8)  # its folder cannot be made: a file is in the way
    with caplog.at_level("WARNING", logger="kaibot.common.ratelimit"):
        assert w.acquire() is None and w.acquire() is None
    assert w.broken and sum("unusable" in r.message for r in caplog.records) == 1
    b = Budget("sec_edgar", BudgetSpec(((5, 1.0),)), shared=w)
    b.acquire()  # still works: only the process's own budget holds


def test_a_429_through_common_http_backs_off_the_shared_window(tmp_path):
    import io
    import urllib.error

    wall = Wall()
    w = SharedWindow(tmp_path / "sec_edgar.slots", 8, wall=wall, sleep=wall.sleep)

    class Clock:
        def monotonic(self):
            return wall()

        def sleep(self, s):
            wall.sleep(s)

    b = Budget("sec_edgar", BudgetSpec(((5, 1.0),)), Clock(), shared=w)
    calls = []

    def opener(req, timeout):
        calls.append(wall())
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {"Retry-After": "7"}, io.BytesIO(b""))

        class R:
            status, headers = 200, {}

            def read(self, n=-1):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    http.get("https://data.sec.gov/x", source="sec_edgar", budget=b, opener=opener)
    assert calls[1] - calls[0] >= 7  # and any other process waits the same
    assert json.loads((tmp_path / "sec_edgar.slots").read_text())["blocked_until"] >= calls[0] + 7


# ---------------------------------------------------------------- three real processes


CALLER = textwrap.dedent("""
    import json, sys, time
    from kaibot.common.ratelimit import get_budget
    b = get_budget("sec_edgar")
    start = float(sys.argv[1])
    while time.time() < start:
        time.sleep(0.005)
    out = []
    for _ in range(int(sys.argv[2])):
        b.acquire()
        out.append(time.time())  # the moment this process would send its request
    print(json.dumps(out))
""")


def run_three(dirs: list[Path], n: int) -> list[float]:
    import time

    start = time.time() + 1.5  # all three begin together
    procs = []
    for d in dirs:
        env = {**os.environ, "KAIBOT_RATELIMIT_DIR": str(d), "PYTHONPATH": str(ROOT / "src")}
        procs.append(subprocess.Popen([sys.executable, "-c", CALLER, str(start), str(n)], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    times: list[float] = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        times += json.loads(out)
    return times


def test_three_concurrent_processes_stay_under_10_requests_a_second_together(tmp_path):
    n = 24  # each process alone would send these at 5 a second
    times = run_three([tmp_path] * 3, n)
    assert len(times) == 3 * n
    # the shared cap is 8 a second; a process wakes a little after its slot, so allow one for that jitter, and
    # never SEC's 10
    assert most_in_a_second(times) <= ratelimit.load_specs()["sec_edgar"].shared_per_s + 1 < 10
    span = max(times) - min(times)
    assert (len(times) - 8) / span <= 8.5  # the long-run rate is the shared cap, not 3 x 5


def test_without_the_shared_file_the_same_three_processes_pass_10_a_second(tmp_path):
    """The control: one folder each (no sharing) is the old behaviour, 3 x 5 a second. So the test above can fail."""
    times = run_three([tmp_path / "a", tmp_path / "b", tmp_path / "c"], 15)
    assert most_in_a_second(times) > 10
