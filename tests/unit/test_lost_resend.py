"""M9 ticket 1.11 (issue #436): a "lost" order is checked against the venue before a new attempt may go out.

The audit's finding (M10, "suspected"): an unconfirmed order is declared `rejected: lost` after two lookups BY CLIENT
ORDER ID that find nothing, and the live send then makes a new attempt under a new client id. If the venue accepted
the order and only its by-client-id lookup lags, two orders exist. The first two tests below fail on main @ 453bb87
(two orders at the venue): the code path is as the audit read it. Whether Alpaca's lookup can lag like this is not
known; scripts/probe_alpaca_idempotency.py measures it on the paper account.

Before "lost" the manager now reads the open orders and the fills since the intent, and compares the venue's
position with the ledger plus the fills its other orders still have to book. An order found is adopted; evidence it
may have traded, or a venue that cannot be read, keeps it `unknown` (the symbol stays blocked, nothing is re-sent).

QA-Ops on #495: the hold is bounded. An `unknown` order blocks every order in its symbol, sales included, so from
the New York day after the intent on only the order itself counts (found: adopted; the venue unreadable: still
unknown; otherwise lost) and the symbol is free again.
"""

from __future__ import annotations

import pytest
from test_order_manager import DAY, manager, mkt, run, venue

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.orders import St
from kaibot.engine.targets import Target, reconcile_targets


def lagging(b, *, open_orders=False, fills=False):
    """The venue has the order, but its lookup by client id finds nothing (and, if asked, neither do the open
    orders or the account activities)."""
    b.order_by_client_id = lambda coid: None
    if open_orders:
        b.open_orders = lambda: []
    if fills:
        b.activities = lambda after=None, types=("FILL",): []


def two_misses(om, clock):
    om.resolve()
    clock.advance(seconds=31)
    om.resolve()
    clock.advance(seconds=31)
    om.resolve()


def test_an_accepted_order_whose_lookup_lags_is_found_in_the_open_orders_and_is_one_order():
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    b.script.hold("A", bars=None)
    om, _, _ = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    assert r.state is St.UNKNOWN and len(b.orders) == 1
    lagging(b)
    two_misses(om, clock)
    assert r.state is St.ACCEPTED and r.order_id and om.blocked("A") is None
    # the live send's next step: the decision's open order is reused, nothing new goes out
    coid = om.next_coid("1", "rule", "A", DAY, "buy")
    assert coid == r.coid
    om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    assert len(b.orders) == 1 and sum(c.startswith("submit") for c in b.calls) == 1


def test_a_filled_order_whose_lookup_lags_is_found_by_its_fill_and_booked_once():
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    om, _, booked = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 32)  # it fills at the venue: no longer an open order
    assert not b.open_orders() and float(b.positions()["A"]["qty"]) == 100
    lagging(b)
    two_misses(om, clock)
    om.sync()
    assert r.state is St.FILLED and r.booked == 100 and len(booked) == 1
    assert len(b.orders) == 1 and om.next_coid("1", "rule", "A", DAY, "buy").endswith("-2")
    assert om.submit(mkt(), strategy="rule", purpose="buy", session=DAY) is r  # the same id: never sent again
    assert sum(c.startswith("submit") for c in b.calls) == 1


def test_shares_at_the_venue_that_the_ledger_does_not_have_keep_the_order_unknown():
    """Every read lags except the position: the venue holds 100 shares the ledger does not. Not lost; nothing new."""
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    om, _, _ = manager(b, clock, position=lambda sym: 0.0)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 32)
    real_lookup = b.order_by_client_id
    lagging(b, open_orders=True, fills=True)
    two_misses(om, clock)
    assert r.state is St.UNKNOWN and "not lost" in r.reason and "position" in r.reason
    assert om.blocked("A") and len(b.orders) == 1
    assert any(c == r.coid and "not lost" in why for c, why in om.errors)
    b.order_by_client_id = real_lookup  # the lookup catches up
    clock.advance(seconds=31)
    om.resolve()
    assert r.state is St.FILLED and len(b.orders) == 1


def test_a_position_that_matches_the_ledger_lets_a_lost_order_be_lost():
    b, clock = venue()
    b.script.timeout("A", accepted=False)
    om, _, _ = manager(b, clock, position=lambda sym: 0.0)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    two_misses(om, clock)
    assert r.state is St.REJECTED and r.reason.startswith("lost") and not b.orders
    assert om.next_coid("1", "rule", "A", DAY, "buy").endswith("-2")


def test_fills_the_managers_other_orders_have_not_booked_yet_are_not_evidence():
    """An earlier order of the symbol filled at the venue and its activity is not booked yet: those shares are
    explained, so a later order that never arrived is still lost."""
    b, clock = venue()
    om, _, _ = manager(b, clock, position=lambda sym: 0.0)
    first = om.submit(mkt(qty=40), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 32)
    real = b.activities
    b.activities = lambda after=None, types=("FILL",): []  # the activity is late
    om.sync()
    assert first.state is St.FILLED and first.cum == 40 and first.booked == 0
    b.script.timeout("A", accepted=False)
    r = om.submit(mkt(attempt=2, qty=60), strategy="rule", purpose="buy", session=DAY)
    two_misses(om, clock)
    assert r.state is St.REJECTED and r.reason.startswith("lost")
    b.activities = real


def _held_by_a_difference(b, clock, side="buy"):
    """An order that never arrived, in a stock whose venue position differs from the ledger for another reason
    (in the order's direction): held `unknown` through its own day. Buy: the venue holds 100 shares the ledger
    does not have. Sale: the venue holds 100 of the ledger's 200."""
    ledger = {"A": 0.0}
    om, _, _ = manager(b, clock, position=lambda sym: ledger[sym])
    om.submit(mkt(purpose="seed"), strategy="rule", purpose="seed", session=DAY)
    run(b, clock, 30, 32)
    om.sync()
    assert float(b.positions()["A"]["qty"]) == 100 and len(b.orders) == 1
    ledger["A"] = 0.0 if side == "buy" else 200.0
    b.script.timeout("A", accepted=False)
    r = om.submit(mkt(side=side, purpose=side), strategy="rule", purpose=side, session=DAY)
    two_misses(om, clock)
    for _ in range(5):  # and it stays held for the rest of the day, lookup after lookup
        clock.advance(minutes=30)
        om.resolve()
    assert r.state is St.UNKNOWN and "not lost" in r.reason and "position" in r.reason and om.blocked("A")
    assert len(b.orders) == 1
    return om, r


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_hold_ends_with_the_orders_day_and_the_symbol_is_free_again(side):
    """QA-Ops on #495: a difference that stands for days must not keep the symbol blocked for days (a sale of it
    would be locked in). The day after, the order that is nowhere at the venue is lost; a new order may go out."""
    b, clock = venue()
    om, r = _held_by_a_difference(b, clock, side)
    clock.advance(hours=18)  # the next New York day, before its session
    om.resolve()
    assert r.state is St.REJECTED and r.reason.startswith("lost") and om.blocked("A") is None
    assert len(b.orders) == 1 and om.next_coid("1", "rule", "A", DAY, side).endswith("-2")


def test_after_its_day_an_order_the_venue_has_is_still_adopted_and_an_unreadable_venue_is_still_not_lost():
    b, clock = venue()
    om, r = _held_by_a_difference(b, clock)
    clock.advance(hours=18)

    def down(*a, **k):
        raise BrokerError(0, "no answer")

    real = b.open_orders
    b.open_orders = down
    om.resolve()
    assert r.state is St.UNKNOWN and "could not be read" in r.reason and om.blocked("A")
    b.open_orders = lambda: [{"id": "v1", "client_order_id": r.coid, "symbol": "A", "side": "buy", "qty": "100",
                              "filled_qty": "0", "status": "accepted", "type": "market", "time_in_force": "day"}]
    clock.advance(seconds=31)
    om.resolve()
    assert r.state is St.ACCEPTED and r.order_id == "v1"  # found: this record's order, never a second one
    b.open_orders = real


@pytest.mark.parametrize("what", ["open_orders", "activities", "positions"])
def test_a_venue_that_cannot_be_read_is_never_lost(what):
    b, clock = venue()
    b.script.timeout("A", accepted=False)
    om, _, _ = manager(b, clock, position=lambda sym: 0.0)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)

    def down(*a, **k):
        raise BrokerError(0, "no answer")

    real = getattr(b, what)
    setattr(b, what, down)
    two_misses(om, clock)
    assert r.state is St.UNKNOWN and "not lost" in r.reason and om.blocked("A")
    setattr(b, what, real)
    clock.advance(seconds=31)
    om.resolve()
    assert r.state is St.REJECTED and r.reason.startswith("lost")  # read again at the next miss


def test_a_fill_on_another_known_order_of_the_symbol_is_not_evidence():
    """A fill since the intent on an order the venue names with ANOTHER client id is that order's, not this one's."""
    b, clock = venue()
    om, _, _ = manager(b, clock)
    b.script.timeout("A", accepted=False)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    b.submit(OrderRequest("A", "buy", 5, "market", "day", "manual-1"))  # someone else's order, same symbol and side
    run(b, clock, 30, 32)
    two_misses(om, clock)
    assert r.state is St.REJECTED and r.reason.startswith("lost")


def test_the_live_send_makes_one_order_when_the_lookup_lags(tmp_path):
    """The audit's sequence end to end on the target loop: unknown, two blind lookups, the next loop run."""
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    b.script.hold("A", bars=None)
    om, _, _ = manager(b, clock)
    want = [Target("rule", "A", 30, "buy")]
    pol = ExecutionPolicy("marketable_limit")
    q = {"A": (99.9, 100.0)}
    reconcile_targets(om, pol, want, {}, now=clock(), session=DAY, quotes=q)
    assert len(b.orders) == 1
    lagging(b)
    two_misses(om, clock)
    res = reconcile_targets(om, pol, want, {}, now=clock(), session=DAY, quotes=q)
    assert not res.sent and len(b.orders) == 1
