"""M9 ticket 1.10 (#435, audit M9): the host clock is compared with the broker's at every tick. Beyond five
seconds the engine sends no new order (the near-close send, intraday entries) and raises an event; sales that take
risk off, stops and the flatten go on. Every window is computed from the host clock, so a Mac a few minutes slow
would send the day's market orders after the close."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from test_engine import fake_candidate
from test_live_daily_engine import SEND, DailyAccount

from kaibot.engine.core import CLOCK_SKEW_MAX_S


def skewed(a, seconds: float | None, slow_call_s: float = 0.0):
    """The broker's clock reads `seconds` ahead of the host's (the host is slow); None: it sends no timestamp.
    `slow_call_s`: the call itself takes that long on the host's clock."""
    real = type(a.broker).clock

    def clock():
        c = real(a.broker)
        if slow_call_s:
            a.clock.t += timedelta(seconds=slow_call_s)
        if seconds is None:
            return {k: v for k, v in c.items() if k != "timestamp"}
        return {**c, "timestamp": (pd.Timestamp(c["timestamp"]) + pd.Timedelta(seconds=seconds)).isoformat()}

    a.broker.clock = clock


def _decided(tmp_path, monkeypatch) -> DailyAccount:
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, SEND)
    assert a.md_orders() == [] and a.eng.entry_block() is None
    return a


def _events(a, kind="clock_skew"):
    return [e["payload"] for e in a.store.tables["event"] if e["type"] == kind]


def test_a_host_clock_four_minutes_slow_sends_no_order_and_raises_one_event(tmp_path, monkeypatch):
    a = _decided(tmp_path, monkeypatch)
    skewed(a, 240)
    a.ticks(a.open2, SEND, SEND + 3)  # 15:50-15:52 by the host: the send window (15:54-15:56 by the broker)
    assert a.md_orders() == [], "no order while the clocks disagree"
    (ev,) = _events(a)
    assert ev["state"] == "skewed" and ev["seconds"] == pytest.approx(-240, abs=1) and ev["limit_s"] == 5
    assert "clock" in a.eng.entry_block() and not a.eng._may_enter(SEND)
    assert a.eng.kill is None, "not a halt: it clears by itself"
    assert not [e for e in a.store.tables["event"] if e["type"] == "multi_day_error"]


def test_the_send_goes_out_once_the_clocks_agree_again(tmp_path, monkeypatch):
    a = _decided(tmp_path, monkeypatch)
    skewed(a, 240)
    a.ticks(a.open2, SEND, SEND + 2)
    assert a.md_orders() == []
    del a.broker.clock  # the Mac's clock was set
    a.ticks(a.open2, SEND + 2, SEND + 3)
    assert len(a.md_orders()) == 1 and a.eng.entry_block() is None
    assert [e["state"] for e in _events(a)] == ["skewed", "ok"]


@pytest.mark.parametrize("seconds, blocked", [(4.0, False), (-4.0, False), (6.0, True), (-6.0, True)])
def test_the_limit_is_five_seconds_either_way(tmp_path, monkeypatch, seconds, blocked):
    a = _decided(tmp_path, monkeypatch)
    skewed(a, seconds)
    a.ticks(a.open2, SEND, SEND + 1)
    assert (a.md_orders() == []) is blocked and bool(_events(a)) is blocked
    assert CLOCK_SKEW_MAX_S == 5.0


def test_a_slow_answer_from_the_broker_is_not_a_skew(tmp_path, monkeypatch):
    """The broker's timestamp is taken while the call is under way: it may lie anywhere between the host's time
    before and after the call. A 15-second call (retries) with agreeing clocks blocks nothing."""
    a = _decided(tmp_path, monkeypatch)
    skewed(a, 7.0, slow_call_s=15.0)
    a.ticks(a.open2, SEND, SEND + 1)
    assert len(a.md_orders()) == 1 and not _events(a)


def test_a_clock_answer_without_a_timestamp_fails_closed(tmp_path, monkeypatch):
    a = _decided(tmp_path, monkeypatch)
    skewed(a, None)
    a.ticks(a.open2, SEND, SEND + 1)
    assert a.md_orders() == []
    (ev,) = _events(a)
    assert ev["state"] == "unreadable" and "clock" in a.eng.entry_block()


def test_intraday_entries_wait_and_exits_and_the_flatten_go_on(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    fake_candidate(a.eng, "A", at=(54,))
    a.ticks(a.open1, 0, 70)
    assert [t["symbol"] for t in a.store.tables["trade"]] == ["A"]  # an intraday position, opened before the skew
    skewed(a, -240)
    fake_candidate(a.eng, "A", at=(120,))
    a.ticks(a.open1, 70, 390)
    assert len(a.store.tables["trade"]) == 1, "no new entry while the clocks disagree"
    assert a.store.tables["trade"][0]["exit_ts"] is not None and "A" not in a.broker.positions(), "the exit went out"
    assert a.eng.day.flattened and len(_events(a)) == 1
