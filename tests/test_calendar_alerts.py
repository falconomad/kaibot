"""M9 ticket 1.17 (#442): two calendars are extended by hand every autumn. The engine says so 30 days before
either runs out: the macro-event calendar's `covers_until` (after it every session halts new entries) and the last
row of the dead-man's holiday table `kaibot.market_calendar_exception` (after it the dead-man knows no holiday
and no early close)."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest
from test_failsafe_drills import DAY1, Account

from kaibot.engine.core import CALENDAR_ALERT_DAYS
from kaibot.engine.store import StoreError

TODAY = date.fromisoformat(DAY1)


def _alerts(a) -> list[dict]:
    return [e["payload"] for e in a.store.tables["event"] if e["type"] == "calendar_expiring"]


def _holidays(a, *days: date) -> None:
    for d in days:
        a.store.insert("market_calendar_exception", {"day": d.isoformat(), "close_et": None, "note": "holiday"})


def _session_start(tmp_path, covers: date | None, holidays: tuple[date, ...] = ()) -> Account:
    a = Account(tmp_path)
    a.eng.calendar_until = covers
    _holidays(a, *holidays)
    a.ticks(a.open1, 0, 3)
    return a


def test_the_window_is_thirty_days():
    assert CALENDAR_ALERT_DAYS == 30


@pytest.mark.parametrize("days_left, alert", [(31, False), (30, True), (29, True), (1, True), (0, True)])
def test_the_macro_calendar_is_announced_thirty_days_before_it_ends(tmp_path, caplog, days_left, alert):
    with caplog.at_level(logging.ERROR):
        a = _session_start(tmp_path, TODAY + timedelta(days=days_left))
    got = _alerts(a)
    assert bool(got) is alert
    if alert:
        (e,) = got  # once per session, not once per tick
        assert e["calendar"] == "macro_calendar" and e["days_left"] == days_left
        assert e["until"] == str(TODAY + timedelta(days=days_left)) and "macro_calendar.yaml" in e["action"]
        assert "calendar expiring" in caplog.text
        assert a.eng.kill is None and a.eng.entry_block() is None, "an alert only: nothing is halted"


def test_a_date_shifted_year_end_raises_the_alert_through_december(tmp_path):
    """What happens on the Mac if nobody extends the file: covers_until 31 Dec, alerts from 1 Dec."""
    a = Account(tmp_path)
    a.eng.calendar_until = date(2026, 12, 31)
    seen = {}
    for today in (date(2026, 11, 30), date(2026, 12, 1), date(2026, 12, 31), date(2027, 1, 4)):
        before = len(_alerts(a))
        a.eng._calendar_alerts(today)
        seen[str(today)] = [e["days_left"] for e in _alerts(a)[before:]]
    assert seen == {"2026-11-30": [], "2026-12-01": [30], "2026-12-31": [0], "2027-01-04": [-4]}


def test_the_dead_mans_holiday_table_is_announced_thirty_days_before_its_last_row(tmp_path):
    last = TODAY + timedelta(days=20)
    a = _session_start(tmp_path, None, holidays=(TODAY - timedelta(days=200), last, TODAY + timedelta(days=5)))
    (e,) = _alerts(a)
    assert e["calendar"] == "deadman_holidays" and e["until"] == str(last) and e["days_left"] == 20
    assert "market_calendar_exception" in e["action"]


def test_a_holiday_table_that_reaches_far_enough_is_silent_and_both_can_alert_together(tmp_path):
    a = _session_start(tmp_path, TODAY + timedelta(days=400), holidays=(TODAY + timedelta(days=31),))
    assert _alerts(a) == []
    b = _session_start(tmp_path / "b", TODAY + timedelta(days=3), holidays=(TODAY + timedelta(days=4),))
    assert sorted(e["calendar"] for e in _alerts(b)) == ["deadman_holidays", "macro_calendar"]


def test_a_holiday_table_that_cannot_be_read_stops_nothing_and_is_tried_again_next_session(tmp_path, caplog):
    a = Account(tmp_path)
    real = a.store.select

    def select(table, params):
        if table == "market_calendar_exception":
            raise StoreError("GET market_calendar_exception: timed out")
        return real(table, params)

    a.store.select = select
    with caplog.at_level(logging.WARNING):
        a.ticks(a.open1, 0, 3)
    assert _alerts(a) == [] and a.eng.kill is None and a.eng.day is not None
    assert "holiday table" in caplog.text
    a.store.select = real
    _holidays(a, TODAY + timedelta(days=2))
    a.eng._calendar_alerts(TODAY + timedelta(days=1))
    assert [e["days_left"] for e in _alerts(a)] == [1]


def test_an_empty_or_missing_table_is_not_an_expiry(tmp_path):
    a = _session_start(tmp_path, None)
    assert _alerts(a) == []
