"""Audit 2, 5.17 (#697): the DST world's wall and monotonic clocks part when the Mac sleeps, and a daily store can come
after the fill session. The 24-25 Nov 2026 incident replays as a scenario (scenarios.mac_sleep_24_25_nov).

What the world models (world.py): an `Outage(mac=True)` stops the engine, the watchdog (it runs on the same Mac) and
the engine's monotonic clock for hours, while the wall clock, the venue, Supabase and the daily store go on; at the
wake the watchdog runs first, then the engine wakes or is started again. With `Scenario.lease` the world runs the
live process's own loop around the engine (engine/__main__.py: the lease and its fence, `before_tick`, the heartbeat
that renews the lease, the gap watch), which is what the new weeks' seeds run.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest
from scenarios import HEARTBEAT_STOPS, MAC_WAKES, STORE_COMPLETE, mac_sleep_24_25_nov, quiet, quiet_in
from world import D1, D2, D3, NY, SUMMER, Outage, Scenario, World, et, run_scenario, week_of


def _run(scenario):
    w = World(scenario)
    w.own_root = False  # kept for the checks below, removed by the test
    try:
        res = w.run()
    except BaseException:
        shutil.rmtree(w.root, ignore_errors=True)
        raise
    return w, res


def _events(w, kind):
    return [r["payload"] for r in w.replica.tables.get("event", []) if r.get("type") == kind]


def _at(line: str) -> datetime:
    """The New York time of a trace line ("11-25 10:48:07 ..." in 2026)."""
    return datetime.strptime(f"2026-{line[:14]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY)


@pytest.fixture
def incident():
    w, res = _run(mac_sleep_24_25_nov())
    yield w, res
    shutil.rmtree(w.root, ignore_errors=True)


def test_the_24_25_nov_timeline_replays(incident):
    """Product's reading of the production database, as a scenario: the heartbeat stops at 22:45 UTC on 24 Nov, the
    watchdog acts at 15:47:55 UTC on 25 Nov on a heartbeat 61325 s old, the engine is up again at 15:48, the store
    has the view of 24 Nov at about 16:00, the late decision follows (sell B, the stand-in for XOM), and the sale goes
    out in that afternoon's window. The engine says how long it was gone (`engine_gap`, seen at the start)."""
    w, res = incident
    assert res.ok, res.report()
    beats = [r for r in w.replica.tables["heartbeat"] if r["process"] == "engine"]
    assert beats and beats[0]["ts"] >= MAC_WAKES  # (one row per process, upserted: its last write)
    asleep = next(t for t in res.trace if "the Mac sleeps" in t)
    assert _at(asleep).astimezone(UTC) == HEARTBEAT_STOPS
    dog = next(t for t in res.trace if t.startswith("11-25") and "watchdog:" in t)
    assert _at(dog).astimezone(UTC) == MAC_WAKES and '"age_s": 61325.0' in dog, dog
    halts = [r for r in w.replica.tables["halt"] if r["kind"] == "watchdog"]
    assert len(halts) == 1 and "engine heartbeat 61325 s old" in halts[0]["reason"]
    up = [t for t in res.trace if t.endswith("engine up")]
    assert len(up) == 2 and _at(up[1]).astimezone(UTC).strftime("%H:%M") == "15:48"
    late = next(t for t in res.trace if "md_decision session=2026-11-24 fill_session=2026-11-25 late=True" in t)
    decided = _at(late).astimezone(UTC)
    # the store is complete at 16:00 UTC; the late decision is tried every LATE_EVERY (5 min) from the start at
    # 15:48:07, so it is taken at 16:03:07 here (16:05 in production, whose loop had its own phase)
    assert STORE_COMPLETE <= decided <= STORE_COMPLETE + timedelta(minutes=6), late
    sale = [t for t in res.trace if " om_intent " in t and "-B-20261125-sell-" in t]
    assert len(sale) == 1 and sale[0][6:11] == "15:50"
    assert any(" fill " in t and "-B-20261125-sell-" in t for t in res.trace)
    gaps = _events(w, "engine_gap")
    assert len(gaps) == 1, gaps  # no other gap: the world runs the live loop through the nights
    g = gaps[0]
    assert g["seen"] == "start" and g["since"].startswith("2026-11-24T22:45:50") and 61_300 < g["gap_s"] < 61_400


def test_the_clocks_part_while_the_mac_sleeps(incident):
    """Over the run the monotonic clock fell behind the wall clock by exactly the sleep."""
    w, _ = incident
    assert w.slept == (MAC_WAKES - HEARTBEAT_STOPS).total_seconds() == 61_325
    assert w.clock().timestamp() - w.mono() == pytest.approx(61_325)


def test_an_engine_frozen_through_the_night_wakes_and_says_so():
    """The same sleep without the restart: the process wakes where it was, one loop pass 17 hours after the last.
    Its lease is older than its TTL by the wall clock (the monotonic one says 0 s): it is renewed before anything is
    sent, the gap watch reports a frozen process, and the late decision and the sale go out as before."""
    w, res = _run(mac_sleep_24_25_nov(outages=(Outage(HEARTBEAT_STOPS, 0, kill=False, mac=True, until=MAC_WAKES),)))
    try:
        assert res.ok, res.report()
        assert sum(t.endswith("engine up") for t in res.trace) == 1
        gaps = _events(w, "engine_gap")
        assert len(gaps) == 1 and gaps[0]["seen"] == "running" and gaps[0]["gap_s"] > 61_300, gaps
        assert any(" om_intent " in t and "-B-20261125-sell-" in t and t[6:11] == "15:50" for t in res.trace)
        assert w.lease is not None and w.lease.ok and res.stats["lease_lost"] == 0
    finally:
        shutil.rmtree(w.root, ignore_errors=True)


def test_a_store_after_the_fill_session_is_decided_at_the_next_session_not_from_an_old_view():
    """The store has the view of 24 Nov only at 17:00 New York on 25 Nov, after the fill session: nothing is sent on
    25 Nov (no decision from an older view); at the next session (Fri 27 Nov, after the holiday) the look-back of
    5.11 (#751) takes the decision late and B is sold in the 12:50-12:57 window of the early close."""
    late = et("2026-11-25", "17:00")
    scenario = mac_sleep_24_25_nov(ready={"2026-11-23": et("2026-11-23", "16:10"), "2026-11-24": late,
                                          "2026-11-25": et("2026-11-25", "17:10"),
                                          "2026-11-27": et("2026-11-27", "13:10")})
    res = run_scenario(scenario)
    assert res.ok, res.report()
    assert not any(" om_intent " in t and ("-20261125-sell-" in t or "-20261125-buy-" in t) for t in res.trace)
    late_decision = [t for t in res.trace if "md_decision session=2026-11-24 fill_session=2026-11-25 late=True" in t]
    assert len(late_decision) == 1 and late_decision[0].startswith("11-27 "), res.report()
    sale = [t for t in res.trace if " om_intent " in t and "-B-20261127-sell-" in t]
    assert len(sale) == 1 and sale[0][6:11] == "12:50", res.report()


def test_a_view_that_comes_after_its_fill_session_is_still_decided_and_sent():
    """#691 (audit 2, 5.11; fixed by #751's look-back), reached by 5.17's late store: the view of D1 comes at 17:30 on
    D2, after D2's close. D2's decision (buy C as well) is taken late on D3 and sent in D3's window, so the book holds
    C at the end."""
    res = run_scenario(quiet({D1: ("B",), D2: ("B", "C")},
                             ready={"2026-07-31": et("2026-07-31", "16:10"), D1: et(D2, "17:30"),
                                    D2: et(D2, "17:40"), D3: et(D3, "16:10")}))
    assert res.ok, res.report()
    assert any(" fill " in t and "-C-" in t for t in res.trace), res.report()


def test_the_generator_draws_mac_sleeps_and_late_stores_in_the_new_weeks():
    """Sleeps of hours (some into the next session, half of them with a restart at the wake) and views after the
    fill session, in the new weeks only; every new week's seed runs the live loop with the lease."""
    seen = {"mac_sleep": 0, "into_session": 0, "restart": 0, "after_fill_session": 0}
    for seed in range(1, 601):
        s = Scenario.from_seed(seed)
        if s.week is SUMMER:
            assert not any(o.mac for o in s.outages) and not s.lease
            continue
        assert s.lease and s.book_loop
        w = s.week
        for o in s.outages:
            if o.mac:
                seen["mac_sleep"] += 1
                seen["restart"] += o.restart
                seen["into_session"] += any(et(d, "09:30") < o.wake < et(d, w.close(d)) for d in w.sessions)
        for ev, nxt in zip(w.evenings, w.days[1:], strict=False):
            seen["after_fill_session"] += s.ready[ev] > et(nxt, w.close(nxt))
    assert all(n >= 10 for n in seen.values()), seen


def test_the_summer_seeds_are_what_they_were():
    """5.17's draws have their own stream and only the new weeks make them."""
    for seed in range(1, 60):
        if week_of(seed) is SUMMER:
            s = Scenario.from_seed(seed)
            assert not s.lease and not any(o.mac for o in s.outages)


# seeds that draw a Mac sleep, one with a restart at the wake and one without (run on every pull request)
MAC_SEEDS = tuple(next(s for s in range(1, 400) if any(o.mac and o.restart is r for o in Scenario.from_seed(s).outages))
                  for r in (True, False))


@pytest.mark.parametrize("seed", MAC_SEEDS)
def test_a_seed_with_a_mac_sleep_holds_the_invariants(seed):
    res = run_scenario(Scenario.from_seed(seed))
    assert res.stats["mac_sleeps"] == 1 and res.scenario.lease
    assert res.ok, res.report()


def test_a_late_store_on_the_live_loop_is_decided_late_the_same_session():
    """The store has the view of the evening before only at 11:00 New York: the late decision (M9 1.3) on the live
    loop, in a gap week."""
    from world import OCTOBER_GAP

    d0, d1 = OCTOBER_GAP.days[0], OCTOBER_GAP.days[1]
    ready = {d: et(d, "16:10") for d in OCTOBER_GAP.evenings}
    ready[d0] = et(d1, "11:00")
    res = run_scenario(quiet_in(OCTOBER_GAP, {d1: ("B",)}, ready=ready, lease=True))
    assert res.ok, res.report()
    assert any(f"fill_session={d1} late=True" in t for t in res.trace)
    assert any(" om_intent " in t and "-B-" in t and t[6:11] == "15:50" for t in res.trace)
