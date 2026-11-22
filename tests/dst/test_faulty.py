"""M12 ticket 3.2 (#464): each fault of kaibot.broker.faulty does what its name says, on the simulator alone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from world import D1, Replica, Venue, et, minute_frames

from kaibot.broker.faulty import (
    DEFAULT_FAULTS,
    NO_FAULTS,
    Dice,
    FaultProfile,
    FaultyBroker,
    FaultyData,
    FaultyStore,
    Forced,
)
from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.broker.sim import SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.store import StoreError
from kaibot.replay.history import HistoricalData

FRAMES = minute_frames(1)
OPEN = et(D1, "09:30")


def venue(*forced: Forced, profile: FaultProfile = NO_FAULTS):
    clock = SimClock(OPEN + timedelta(minutes=10, seconds=7))
    data = HistoricalData(FRAMES, clock)
    sim = Venue(data.history, clock)
    return FaultyBroker(sim, 1, profile, forced=forced), sim, clock


def step(b: FaultyBroker, clock: SimClock, minutes: int = 1) -> None:
    for _ in range(minutes):
        b.advance(pd.Timestamp(clock().replace(second=0, microsecond=0)))
        clock.advance(minutes=1)


def buy(coid: str = "kb1-s-B-20260803-buy-1", qty: int = 10) -> OrderRequest:
    return OrderRequest("B", "buy", qty, "market", "day", coid)


def test_without_faults_the_wrapper_is_the_simulator():
    b, sim, clock = venue()
    o = b.submit(buy())
    step(b, clock)
    assert b.order(o["id"])["status"] == "filled" and b.positions()["B"]["qty"] == sim.pos["B"]["qty"] == 10
    assert [a["id"] for a in b.activities()] == [f["id"] for f in sim.fills] and b.log == []
    assert b.calendar(D1) == sim.calendar(D1) and b.name == sim.name


def test_it_wraps_the_simulator_only():
    with pytest.raises(TypeError, match="simulator only"):
        FaultyBroker(object(), 1)
    assert isinstance(venue()[1], SimBroker)


def test_a_submit_can_time_out_with_the_order_in_and_be_invisible_to_the_lookup_for_a_while():
    b, sim, clock = venue(Forced("submit_timeout_accepted"), Forced("lookup_lag", "", 2))
    with pytest.raises(BrokerError) as e:
        b.submit(buy())
    assert e.value.status == 0 and len(sim.orders) == 1
    assert b.order_by_client_id("kb1-s-B-20260803-buy-1") is None  # the lookup lags ...
    step(b, clock)
    assert [a["side"] for a in b.activities()] == ["buy"]  # ... while the fill is already reported
    assert b.open_orders() == [] and b.order_by_client_id("kb1-s-B-20260803-buy-1") is None
    assert b.order_by_client_id("kb1-s-B-20260803-buy-1")["status"] == "filled"  # the third read finds it


def test_a_listing_lag_hides_the_order_from_the_open_orders_too():
    b, sim, _ = venue(Forced("submit_timeout_accepted"), Forced("lookup_lag", "", 3), Forced("hold", "", "never"),
                      Forced("listing_lag"))
    with pytest.raises(BrokerError):
        b.submit(buy())
    assert len(sim.open_orders()) == 1 and b.open_orders() == []
    b.heal()
    assert len(b.open_orders()) == 1


def test_a_submit_can_be_lost_before_the_venue():
    b, sim, _ = venue(Forced("submit_timeout_lost"))
    with pytest.raises(BrokerError) as e:
        b.submit(buy())
    assert e.value.status == 0 and sim.orders == {}


def test_a_lost_cancel_leaves_the_order_working_and_the_next_request_arrives():
    b, sim, _ = venue(Forced("hold", "", "never"), Forced("cancel_lost", "", 1))
    o = b.submit(buy())
    with pytest.raises(BrokerError):
        b.cancel(o["id"])
    assert sim.orders[o["id"]]["status"] == "new"
    b.cancel(o["id"])
    assert sim.orders[o["id"]]["status"] == "canceled"


def test_a_late_cancel_shows_pending_cancel_and_the_order_can_still_fill():
    b, sim, clock = venue(Forced("cancel_late"))
    o = b.submit(buy())
    b.cancel(o["id"])
    assert b.order(o["id"])["status"] == "pending_cancel" and sim.orders[o["id"]]["status"] == "new"
    step(b, clock)  # the bar trades first: the fill after the cancel request
    assert b.order(o["id"])["status"] == "filled"
    b2, sim2, clock2 = venue(Forced("cancel_late"), Forced("hold", "", "never"))
    o2 = b2.submit(buy())
    b2.cancel(o2["id"])
    step(b2, clock2)
    assert b2.order(o2["id"])["status"] == "canceled"  # nothing filled: the cancel takes effect a bar later


def test_a_fill_can_be_reported_twice_and_a_dropped_one_appears_on_a_later_poll():
    b, _, clock = venue(Forced("fill_duplicate"))
    b.submit(buy())
    step(b, clock)
    rows = b.activities()
    assert len(rows) == 2 and rows[0]["id"] == rows[1]["id"]
    b, _, clock = venue(Forced("fill_dropped", "", 3))
    b.submit(buy())
    step(b, clock)
    assert b.activities() == []
    step(b, clock, 2)
    assert b.activities() == []
    step(b, clock)
    assert len(b.activities()) == 1


def test_a_partial_fill_and_a_held_order():
    b, _, clock = venue(Forced("partial_fill", "", 0.5))
    o = b.submit(buy())
    step(b, clock, 3)
    assert float(b.order(o["id"])["filled_qty"]) == 5 and b.order(o["id"])["status"] == "partially_filled"
    b, _, clock = venue(Forced("hold", "", 2))
    o = b.submit(buy())
    step(b, clock, 2)
    assert b.order(o["id"])["status"] == "new"
    step(b, clock)
    assert b.order(o["id"])["status"] == "filled"


def test_rate_limits_clock_skew_and_bad_marks_come_from_the_profile():
    b, _, clock = venue(profile=FaultProfile(rate_limit=1.0))
    for call in (b.positions, b.account, b.clock, b.open_orders, lambda: b.submit(buy())):
        with pytest.raises(BrokerError) as e:
            call()
        assert e.value.status == 429
    b, sim, clock = venue(profile=FaultProfile(clock_skew=1.0))
    off = datetime.fromisoformat(b.clock()["timestamp"]) - datetime.fromisoformat(sim.clock()["timestamp"])
    assert abs(off.total_seconds()) >= 30
    b.heal()
    assert b.clock()["timestamp"] == sim.clock()["timestamp"]
    b, sim, clock = venue(profile=FaultProfile(mark_bad=1.0))
    b.submit(buy())
    step(b, clock)
    good = float(sim.positions()["B"]["current_price"])
    bad = b.positions()["B"]["current_price"]
    assert bad is None or float(bad) == 0 or float(bad) < good * 0.7


def test_quotes_can_be_stale_missing_zero_or_crossed():
    clock = SimClock(OPEN + timedelta(minutes=10, seconds=7))
    data = HistoricalData(FRAMES, clock)
    good = data.latest_quotes(["B"])["B"]
    for fault in ("quote_stale", "quote_missing", "quote_zero", "quote_crossed"):
        q = FaultyData(data, 1, FaultProfile(**{fault: 1.0})).quotes_within(["B"], 8.0).get("B")
        if fault == "quote_missing":
            assert q is None
        elif fault == "quote_stale":
            assert datetime.fromisoformat(q["t"]) < clock() - timedelta(minutes=30)
        elif fault == "quote_zero":
            assert q["bp"] == q["ap"] == 0
        else:
            assert q["bp"] > q["ap"]
    quiet = FaultyData(data, 1, NO_FAULTS)
    assert quiet.latest_quotes(["B"])["B"] == good and quiet.bars(["B"], OPEN, clock(), "iex")["B"].equals(
        data.bars(["B"], OPEN, clock(), "iex")["B"])


def test_a_store_write_can_fail_time_out_after_it_was_applied_or_arrive_twice():
    now = lambda: datetime(2026, 8, 3, 15, tzinfo=UTC)  # noqa: E731
    r = Replica(now)
    with pytest.raises(StoreError, match="503"):
        FaultyStore(r, 1, FaultProfile(write_fail=1.0), now=now).insert("fill", {"jseq": 1, "x": 1})
    assert r.tables.get("fill") is None
    with pytest.raises(StoreError, match="timed out"):
        FaultyStore(r, 1, FaultProfile(write_timeout=1.0), now=now).insert("fill", {"jseq": 1, "x": 1})
    assert len(r.tables["fill"]) == 1  # the write is there, the client was not told
    twice = FaultyStore(r, 1, FaultProfile(write_twice=1.0), now=now)
    twice.insert("fill", {"jseq": 2, "x": 2})  # a unique key: the second arrival is refused, silently
    twice.insert("note", {"x": 3}, returning=False)  # no unique key: two rows
    assert len(r.tables["fill"]) == 2 and len(r.tables["note"]) == 2
    with pytest.raises(StoreError, match="503"):
        FaultyStore(r, 1, FaultProfile(read_fail=1.0), now=now).select("fill", {})
    calm = FaultyStore(r, 1, NO_FAULTS, now=now)
    assert len(calm.select("fill", {})) == 2 and calm.quick() is calm and calm.log == []


def test_the_dice_do_not_depend_on_the_order_of_the_questions():
    a, b = Dice(5), Dice(5)
    keys = [("hold", f"coid{i}") for i in range(50)]
    assert [a.u(*k) for k in keys] == [b.u(*k) for k in reversed(keys)][::-1]
    assert Dice(6).u("hold", "coid1") != a.u("hold", "coid1")
    hits = sum(a.hit(0.3, "x", i) for i in range(2000))
    assert 500 < hits < 700 and not a.hit(0.0, "x", 1)


def test_a_profile_can_be_scaled_and_cut_down():
    p = DEFAULT_FAULTS.scaled(2.0)
    assert p.hold == pytest.approx(DEFAULT_FAULTS.hold * 2) and DEFAULT_FAULTS.scaled(100).hold == 1.0
    only = DEFAULT_FAULTS.only("hold", "write_fail")
    assert only.hold == DEFAULT_FAULTS.hold and only.cancel_lost == 0 and only.write_fail > 0
    with pytest.raises(ValueError, match="unknown faults"):
        DEFAULT_FAULTS.only("gremlins")
    assert DEFAULT_FAULTS.listing_lag == 0  # engine bug #535: off in the random profile while it is open
