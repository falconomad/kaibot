"""#746 (audit 2, 5.16 finding): with the broker's calendar unreadable, a restarted engine sent nothing in the window
and one started during the outage never started its day.

#733 gave the send window a local close (`LOCAL_EARLY_CLOSES`). The two other reads of the same calendar had no
fallback: `LiveDaily._plan`'s `prev_session(session)` (`sessions_from_calendar`, a per-process cache that a restarted
process does not have) and `Engine._start_day`'s open and session length. Both now fall back to the local NYSE
calendar (`live_daily.local_session` / `local_hours`: kaibot.ingest.nyse_sessions' days, the local table's closes,
LOCAL_CLOSE_YEARS only) when the read raises, journal it once (`calendar_local`), and never overrule an answer of the
broker. Outside the local calendar's years, or on a day it has no session for, nothing changes: the read raises and
nothing is sent.

The DST world runs the live process's engine (`Engine(intraday=False)`, the book loop) with the broker's calendar
down on D2 (Tue 4 Aug 2026) from 09:31 ET (right after the session's start) or 09:00 ET (before it) to 16:30 ET.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "dst"))

import world  # noqa: E402
from scenarios import quiet  # noqa: E402
from world import D1, D2, NY, Outage, World, et  # noqa: E402

from kaibot.broker.protocol import BrokerError  # noqa: E402
from kaibot.engine import live_daily as ld  # noqa: E402
from kaibot.engine.core import Engine  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

DAY2 = D2.replace("-", "")


def book_loop(monkeypatch) -> None:
    none = {level: [] for level in load_profiles()}
    monkeypatch.setattr(world, "Engine", lambda *a, **k: Engine(*a, intraday=False, strategies=none, **k))


def calendar_down(w: World, start: str, end: str = "16:30", day: str = D2) -> list[str]:
    """The broker's calendar answers 503 to every read from `start` to `end` ET on `day`, whatever day it asks for.
    Returns the days asked while it was down."""
    real, asked = w.sim.calendar, []

    def calendar(d: str):
        if et(day, start) <= w.clock() < et(day, end):
            asked.append(d)
            raise BrokerError(503, '{"message":"calendar unavailable"}')
        return real(d)

    w.broker.calendar = calendar  # FaultyBroker passes the calendar through; an instance attribute replaces it
    return asked


def venue_orders(w: World, day: str) -> list[dict]:
    return [o for o in w.sim.orders.values() if f"-{day.replace('-', '')}-" in str(o.get("client_order_id"))]


def events(w: World, kind: str) -> list[dict]:
    return [r for r in w.replica.tables.get("event", []) if r.get("type") == kind]


# ---------------------------------------------------------------- the local calendar
def test_the_local_calendar_knows_the_sessions_and_hours_of_its_years_only():
    assert ld.local_hours(date(2026, 11, 27)) == (datetime(2026, 11, 27, 14, 30, tzinfo=ld.UTC), 210)
    assert ld.local_hours(date(2026, 11, 25)) == (datetime(2026, 11, 25, 14, 30, tzinfo=ld.UTC), 390)
    assert ld.local_hours(date(2026, 8, 4)) == (datetime(2026, 8, 4, 13, 30, tzinfo=ld.UTC), 390)  # EDT
    assert ld.local_session(date(2026, 11, 26)) is False and ld.local_hours(date(2026, 11, 26)) is None
    assert ld.local_session(date(2026, 11, 28)) is False  # a Saturday
    assert ld.local_session(date(2030, 7, 1)) is None and ld.local_hours(date(2030, 7, 1)) is None  # no local table


def test_sessions_fall_back_to_the_local_calendar_only_when_the_read_raises():
    up = {"on": False}
    reads: list[str] = []
    noted: list = []

    def calendar(k: str):
        reads.append(k)
        if not up["on"]:
            raise BrokerError(503, "down")
        # the broker (here) says 25 Nov is closed too: its answer wins over the local calendar's
        return None if k in ("2026-11-25", "2026-11-26") else {"date": k}

    nxt, prev = ld.sessions_from_calendar(calendar, on_local=lambda d, why: noted.append((d, why)))
    assert prev(date(2026, 11, 27)) == date(2026, 11, 25)  # 26 Nov (Thanksgiving) skipped by the local calendar
    assert nxt(date(2026, 11, 25)) == date(2026, 11, 27)
    assert [d for d, _ in noted] == [date(2026, 11, 26), date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)]
    assert "503" in noted[0][1]
    up["on"] = True
    reads.clear()
    assert prev(date(2026, 11, 27)) == date(2026, 11, 24)  # local answers were not cached: the broker read again
    assert reads == ["2026-11-26", "2026-11-25", "2026-11-24"]
    reads.clear()
    up["on"] = False
    assert prev(date(2026, 11, 27)) == date(2026, 11, 24)  # the broker's own answers stay cached
    assert reads == []


def test_outside_the_local_years_a_failed_read_still_raises():
    def calendar(k):
        raise BrokerError(503, "down")

    _, prev = ld.sessions_from_calendar(calendar)
    with pytest.raises(BrokerError):
        prev(date(2030, 7, 2))


def test_a_note_that_fails_never_stops_the_answer():
    def calendar(k):
        raise BrokerError(503, "down")

    def broken(d, why):
        raise RuntimeError("store down")

    _, prev = ld.sessions_from_calendar(calendar, on_local=broken)
    assert prev(date(2026, 8, 4)) == date(2026, 8, 3)


# ---------------------------------------------------------------- the engine, in the DST world
def test_a_restart_before_the_calendar_goes_down_still_sends_in_the_window(monkeypatch):
    """The issue's first case: killed on the evening of D1 after the decision (sell B, buy C), back before D2's
    open; the calendar goes down at 09:31, right after the session's start. The decision goes out at 15:50 on D2."""
    book_loop(monkeypatch)
    w = World(quiet({D1: ("B",), D2: ("C",)}, outages=(Outage(et(D1, "20:00"), 600),)))
    asked = calendar_down(w, "09:31")
    res = w.run()
    assert res.ok, res.report()
    assert not w.eng.intraday and res.stats["kills"] >= 1 and asked
    sent = venue_orders(w, D2)
    assert {(o["symbol"], o["side"]) for o in sent} == {("B", "sell"), ("C", "buy")}, sent
    assert {datetime.fromisoformat(o["submitted_at"]).astimezone(NY).strftime("%H:%M") for o in sent} == {"15:50"}
    said = events(w, "calendar_local")
    assert len(said) == 1 and said[0]["payload"]["day"] == D2  # once for the session, not per read or tick


def test_without_the_fallback_the_restart_sends_nothing_in_the_window(monkeypatch):
    """The bug as it was (no local calendar): the same scenario sends nothing on D2."""
    book_loop(monkeypatch)
    monkeypatch.setattr(ld, "local_session", lambda d: None)
    w = World(quiet({D1: ("B",), D2: ("C",)}, outages=(Outage(et(D1, "20:00"), 600),)))
    calendar_down(w, "09:31")
    w.run()
    assert not venue_orders(w, D2)


def test_a_process_started_during_the_outage_starts_its_day_and_sends(monkeypatch):
    """The issue's second case: the calendar is down from 09:00; the engine was down from D1's evening and comes back
    at 10:00. The day starts from the local calendar (09:30 open, 390 minutes) and the decision goes out at 15:50."""
    book_loop(monkeypatch)
    w = World(quiet({D1: ("B",), D2: ("C",)}, outages=(Outage(et(D1, "20:00"), 840),)))
    calendar_down(w, "09:00")
    res = w.run()
    assert res.ok, res.report()
    sent = venue_orders(w, D2)
    assert {(o["symbol"], o["side"]) for o in sent} == {("B", "sell"), ("C", "buy")}, sent
    assert {datetime.fromisoformat(o["submitted_at"]).astimezone(NY).strftime("%H:%M") for o in sent} == {"15:50"}
    starts = [r for r in events(w, "session_start") if r["payload"]["day"] == D2]
    assert len(starts) == 1 and starts[0]["payload"]["minutes"] == 390
    said = events(w, "calendar_local")
    assert len(said) == 1 and said[0]["payload"]["day"] == D2


def test_a_day_the_local_calendar_has_no_session_for_is_never_started(monkeypatch):
    """Never outside the local calendar: with the calendar down and the local calendar saying D2 is no session (the
    broker's clock says open), the engine does not start the day and sends nothing on D2."""
    from kaibot.ingest import nyse_sessions

    book_loop(monkeypatch)
    real = nyse_sessions.is_session
    monkeypatch.setattr(nyse_sessions, "is_session", lambda d: real(d) and str(d) != D2)
    w = World(quiet({D1: ("B",), D2: ("C",)}, outages=(Outage(et(D1, "20:00"), 840),)))
    calendar_down(w, "09:00")
    w.run()
    assert not venue_orders(w, D2)
    assert not [r for r in events(w, "session_start") if r["payload"]["day"] == D2]


def test_the_send_stays_inside_the_local_window_on_an_early_close():
    """The window with the calendar down comes from the same local calendar (#733): 12:50-12:57 on 27 Nov 2026."""
    def close(d):
        raise BrokerError(503, "down")

    lv = ld.LiveDaily(None, None, lambda: None, [], None, None, next_session=lambda d: d, prev_session=lambda d: d,
                      session_close=close)
    d = date(2026, 11, 27)
    assert (lv.send_from(d), lv.send_until(d)) == (time(12, 50), time(12, 57))
    assert ld.local_hours(d)[1] == 210  # the day's start agrees: a 13:00 close
