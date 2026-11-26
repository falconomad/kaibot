"""Audit 2, 5.16 (#696): the deterministic simulation in weeks with a holiday, an early close, New York's change of
clocks, the US/EU gap weeks and an unreadable calendar (world.Week), on the live process's engine (the book loop).

Each new week is one kind of session the four summer sessions never had:

    thanksgiving              Wed 25 Nov 2026 regular, Thu 26 Nov closed, Fri 27 Nov closes at 13:00 (window
                              12:50-12:57)
    thanksgiving_no_calendar  the same, and on 27 Nov the broker's calendar is down from just after the session's
                              start (09:31 ET) to 16:30: the day's close comes from the local table (#733)
    march_gap                 New York moves to summer time on 8 Mar 2026 inside the run, Berlin only on 29 Mar
    october_gap               Berlin moved back on 25 Oct, New York moves on 1 Nov 2026 inside the run

A seed draws its week (`world.week_of`); the `dst` workflow's 200 seeds put about half of them in the new weeks.
"""

from __future__ import annotations

import shutil

import pytest
from scenarios import FINDINGS, calendar_fallback_regular_close, quiet_in
from world import (
    KNOWN,
    MARCH_GAP,
    NY,
    OCTOBER_GAP,
    SUMMER,
    THANKSGIVING,
    THANKSGIVING_NO_CALENDAR,
    WEEKS,
    Outage,
    Scenario,
    World,
    et,
    minute_frames,
    run_scenario,
    run_seed,
    trading_days,
    week_of,
)

NEW_WEEKS = (THANKSGIVING, THANKSGIVING_NO_CALENDAR, MARCH_GAP, OCTOBER_GAP)
# a seed of each new week (its own draw: `week_of`), run on every pull request beside the numbered ones
WEEK_SEEDS = {w.name: next(s for s in range(1, 200) if week_of(s) is w) for w in NEW_WEEKS}
# audit 2 5.3 / #733 from a seed: seeds that draw the unreadable-calendar week and send on 27 Nov. Before #733 (at
# eb3c7bd, and here with `calendar_fallback_regular_close` planted) each breaks invariant 11: the 27 Nov decision is
# not sent in the 12:50-12:57 window, the engine waits for 15:50, the venue has closed, it goes out late on 30 Nov
SEEDS_733 = (25, 35, 40, 60)


def _sends(res, day: str) -> list[str]:
    return [t for t in res.trace if " om_intent " in t and f"-{day.replace('-', '')}-" in t]


def test_every_session_type_has_a_week_and_the_seeds_draw_each():
    """Each kind of session is in a week, and the seeds reach every week (cheap: no scenario is run)."""
    kinds = {w.name: w.kinds() for w in WEEKS}
    assert kinds["summer"] == {"regular"}
    assert kinds["thanksgiving"] == {"holiday", "early_close"}
    assert kinds["thanksgiving_no_calendar"] == {"holiday", "early_close", "calendar_unreadable"}
    assert kinds["march_gap"] == kinds["october_gap"] == {"dst_change", "dst_gap"}
    drawn: dict[str, int] = {}
    for seed in range(1, 401):
        s = Scenario.from_seed(seed)
        drawn[s.week.name] = drawn.get(s.week.name, 0) + 1
        assert s.book_loop is (s.week is not SUMMER)  # the new weeks run the live process's engine
        assert set(s.plan) == set(s.week.sessions) and set(s.ready) == set(s.week.evenings)
    assert set(drawn) == {w.name for w in WEEKS} and min(drawn.values()) >= 30, drawn
    # the summer seeds draw what they drew before 5.16 (the week has its own stream)
    assert Scenario.from_seed(1).week is SUMMER and Scenario.from_seed(1, week=SUMMER) == Scenario.from_seed(1)


def test_the_generator_puts_its_events_on_the_new_sessions():
    """Outages around the 12:50 window of the early close, decisions whose fill session is the early close, views
    that come by the Mac's own night job (Berlin time: in a gap week 02:45 New York, not 01:45)."""
    seen = {"early_close_window_outage": 0, "early_close_fill": 0, "mac_view": 0, "gap_mac_view": 0}
    for seed in range(1, 801):
        s = Scenario.from_seed(seed)
        w = s.week
        if w.closes:
            day = w.closes[0][0]
            seen["early_close_fill"] += s.plan[day] is not None
            seen["early_close_window_outage"] += any(
                et(day, "12:40") <= o.arm <= et(day, "13:03") for o in s.outages)
        for ev, nxt in zip(w.evenings, w.days[1:], strict=False):
            t = s.ready[ev].astimezone(NY)
            if str(t.date()) == nxt and t.hour < 9:
                seen["mac_view"] += 1
                seen["gap_mac_view"] += "dst_gap" in w.kinds()
    assert all(n >= 10 for n in seen.values()), seen


def test_the_venue_knows_the_holiday_and_the_early_close():
    w = World(quiet_in(THANKSGIVING, {}))
    try:
        assert w.sim.calendar("2026-11-26") is None
        assert w.sim.calendar("2026-11-27")["close"] == "13:00" and w.sim.calendar("2026-11-25")["close"] == "16:00"
        days = trading_days(THANKSGIVING)
        assert "2026-11-26" not in days and days[-5:] == ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30",
                                                          "2026-12-01"]
        bars = minute_frames(0, THANKSGIVING)["B"]
        on27 = bars[bars["ts"].dt.tz_convert(NY).dt.date.astype(str) == "2026-11-27"]
        assert len(on27) == 210 and on27["ts"].iloc[-1].tz_convert(NY).strftime("%H:%M") == "12:59"
        assert "2026-11-26" not in {str(d) for d in w.daily_all.dates[-8:]}
    finally:
        shutil.rmtree(w.root, ignore_errors=True)


def test_thanksgiving_week_sends_at_12_50_on_the_early_close_and_nothing_on_the_holiday():
    """The decision taken on the evening of 25 Nov is for Fri 27 Nov (the next session, not the holiday); it goes out
    in the 12:50-12:57 window. The engine runs through Thursday and trades nothing that day."""
    res = run_scenario(quiet_in(THANKSGIVING, {"2026-11-25": ("B",), "2026-11-27": ("B", "C")}))
    assert res.ok, res.report()
    assert any("fill_session=2026-11-27" in t and "md_decision session=2026-11-25" in t for t in res.trace)
    sent = _sends(res, "2026-11-27")
    assert len(sent) == 1 and "-C-" in sent[0] and sent[0][6:11] == "12:50", sent
    assert not _sends(res, "2026-11-26") and not any(t.startswith("11-26") and " journal " in t for t in res.trace)


def test_a_kill_through_the_early_close_window_is_sent_late_the_next_session():
    """The engine is down from 12:45 to 13:05 on 27 Nov: the buy goes out late on Mon 30 Nov at 15:50 (M9 1.4)."""
    res = run_scenario(quiet_in(THANKSGIVING, {"2026-11-25": ("B",), "2026-11-27": ("B", "C")},
                                outages=(Outage(et("2026-11-27", "12:45"), 20),)))
    assert res.ok, res.report()
    assert not _sends(res, "2026-11-27")
    late = _sends(res, "2026-11-30")
    assert len(late) == 1 and "-C-" in late[0] and late[0][6:11] == "15:50"


@pytest.mark.parametrize("week", [MARCH_GAP, OCTOBER_GAP], ids=lambda w: w.name)
def test_the_send_stays_at_15_50_new_york_across_new_yorks_clock_change(week):
    """Buys on D1, a sale on D2, across New York's change of clocks (in March between D0 and D1, in October between
    D1 and D2): every order leaves at 15:50 New York time, whatever the UTC hour."""
    d1, d2 = week.sessions[0], week.sessions[1]
    res = run_scenario(quiet_in(week, {d1: ("B", "C"), d2: ("C",)}))
    assert res.ok, res.report()
    for day in (d1, d2):
        sent = _sends(res, day)
        assert sent and all(t[6:11] == "15:50" for t in sent), sent
    utc = {et(d, "15:50").hour for d in week.days}
    assert len(utc) == 2  # 19:50 and 20:50 UTC: the run crosses the change


@pytest.mark.parametrize("name", sorted(WEEK_SEEDS))
def test_a_seed_of_each_new_week_holds_the_invariants(name):
    res = run_seed(WEEK_SEEDS[name])
    assert res.scenario.week.name == name and res.scenario.book_loop
    assert res.ok, res.report()


def test_the_unreadable_calendar_falls_back_to_the_local_early_close():
    """#733 at work: the calendar is down from 09:31 ET, the window comes from LOCAL_EARLY_CLOSES (12:50-12:57)."""
    scenario = FINDINGS["calendar unreadable on an early close"][0]
    res = run_scenario(scenario)
    assert res.ok, res.report()
    assert any("calendar goes down" in t for t in res.trace) and any("fault calendar_down" in t for t in res.trace)
    sent = _sends(res, "2026-11-27")
    assert len(sent) == 1 and sent[0][6:11] == "12:50", sent


@pytest.mark.parametrize("seed", SEEDS_733)
def test_733s_bug_reproduces_from_a_seed_without_the_fix(seed, monkeypatch):
    """Audit 2 5.16's "5.3's bug reproduces from a seed before its fix": each seed draws the unreadable-calendar
    week. As the engine is, it holds the invariants; with #733's fallback taken away (the 16:00 close the code
    used before it) the 27 Nov decision misses its 12:50-12:57 window (invariant 11)."""
    assert week_of(seed) is THANKSGIVING_NO_CALENDAR
    res = run_seed(seed)
    assert res.ok, res.report()
    assert any("md_send session=2026-11-27" in t and t[6:11] < "12:57" for t in res.trace), res.report()
    calendar_fallback_regular_close(monkeypatch)
    bad = run_seed(seed)
    assert any("invariant 11: the decision for 2026-11-27 was not sent in its window" in v
               for v in bad.violations), bad.report()


@pytest.mark.xfail(strict=True, reason="engine bug #746: with the calendar down, a restarted process cannot find the "
                                       "previous session and sends nothing in the early close's window")
def test_a_restart_before_the_calendar_goes_down_still_sends_in_the_window():
    """#746: the engine is killed on the evening of 25 Nov and starts again on the holiday; the calendar goes down at
    09:31 on 27 Nov, right after the session's start. The decision of 25 Nov (sell B, buy C) is due at 12:50."""
    assert 746 in KNOWN
    res = run_scenario(quiet_in(THANKSGIVING_NO_CALENDAR, {"2026-11-25": ("B",), "2026-11-27": ("C",)},
                                outages=(Outage(et("2026-11-25", "20:00"), 600),)))
    assert res.ok, res.report()


def test_a_send_outside_the_early_close_window_is_a_violation():
    """Invariant 9 knows the session's real close: an order of 27 Nov at 15:51 (after the 13:00 close) breaks it, one
    at 12:51 does not. (The engine never sends after the venue's close: its book loop does not run the send then.)"""
    w = World(quiet_in(THANKSGIVING, {}))
    try:
        w.check.intents |= {"kb1-trend_momentum-C-20261127-buy-1", "kb1-trend_momentum-C-20261127-buy-2"}
        w.clock.set(et("2026-11-27", "12:51"))
        w.check.on_venue_write("venue.submit C buy kb1-trend_momentum-C-20261127-buy-1")
        assert not w.violations
        w.clock.set(et("2026-11-27", "15:51"))
        w.check.on_venue_write("venue.submit C buy kb1-trend_momentum-C-20261127-buy-2")
        assert any("outside its window" in v and "closes at 13:00" in v for v in w.violations), w.violations
    finally:
        shutil.rmtree(w.root, ignore_errors=True)
