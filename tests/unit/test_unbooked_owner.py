"""#529: a fill whose activity never arrives was never booked, and the owner could not resolve it: the
reconciliation's "unbooked" difference could not be accepted, and the target loop waits on it for good (a buy of
the sleeve always waits, trend_momentum Amendment 24). Now, once the order's New York day is over, the owner can
book it (from the venue's own view of the order: filled quantity and average price) or accept it, by the rule of
#493: the command names exactly what the screen showed, and the venue is read again before anything is done.
Written before the fix: on main the order manager has neither `owner_check`, `book_missing` nor `waive`, and the
reconciliation has no `unbooked_plan`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.journal import Journal
from kaibot.engine.ledger import LEDGER_KINDS, Ledger
from kaibot.engine.orders import OrderManager
from kaibot.engine.reconcile import apply_unbooked, compare, unbooked_plan

D1 = datetime(2026, 8, 3, 19, 50, 7, tzinfo=UTC)  # 15:50 ET
D2 = D1 + timedelta(days=1, hours=-5)  # 10:50 ET the next day


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Venue:
    """Fills the buy at once (the order view says so) but never lists its FILL activity, until `report()`."""

    def __init__(self):
        self.view = {"id": "o1", "status": "filled", "qty": "10", "filled_qty": "10", "filled_avg_price": "100.0"}
        self.acts: list = []
        self.down = False

    def submit(self, req):
        return {**self.view, "client_order_id": req.client_order_id}

    def order(self, oid):
        if self.down:
            raise BrokerError(503, "busy")
        return dict(self.view)

    def order_by_client_id(self, coid):
        return dict(self.view)

    def open_orders(self):
        return []

    def activities(self, after=None, types=("FILL",)):
        return list(self.acts)

    def report(self, px="100.05"):
        self.acts.append({"id": "a1", "order_id": "o1", "symbol": "B", "side": "buy", "qty": "10", "price": px,
                          "transaction_time": D1.isoformat()})


def _setup():
    clock, v = Clock(D1), Venue()
    j = Journal(":memory:", now=clock)
    j.append("run", {"run_id": 1, "amount": 10_000.0})
    booked = []
    om = OrderManager(v, j, clock, run_id=1, on_fill=lambda f, r: booked.append(f))
    r = om.submit(OrderRequest("B", "buy", 10, client_order_id=om.next_coid(1, "s", "B", "2026-08-03", "buy")),
                  strategy="s", purpose="buy", session="2026-08-03")
    om.sync()
    assert r.cum == 10 and r.booked == 0 and r.coid in om.settling
    return clock, v, j, om, r, booked


def _book(j):
    return Ledger.fold(e for e in j.entries() if e[2] in LEDGER_KINDS).book(1)


def _last(om, now):
    return compare({}, {"B": {"qty": "10"}}, om=om, now=now).diffs


def _row(r, action="book", **kw):
    return {"kind": "unbooked", "symbol": "B", "coid": r.coid, "venue": r.cum, "booked": r.booked, "action": action,
            **kw}


def test_the_difference_says_when_the_owner_may_decide():
    clock, _, _, om, r, _ = _setup()
    (d,) = [d for d in _last(om, D1) if d.kind == "unbooked"]
    assert d.detail["aged"] is False  # the day of the order: its activity is on its way
    (d,) = [d for d in _last(om, D2) if d.kind == "unbooked"]
    assert d.detail["aged"] is True


def test_book_it_books_the_venues_fill_once_and_the_late_activity_is_not_booked_again():
    clock, v, j, om, r, booked = _setup()
    clock.t = D2
    plan = unbooked_plan(om, _last(om, D2), [_row(r)], D2)
    done = apply_unbooked(om, plan, by="owner", reason="Alpaca never listed the fill")
    assert done == [{"coid": r.coid, "action": "book", "qty": 10.0, "px": 100.0, "symbol": "B"}]
    b = _book(j)
    assert b.qty_by_symbol() == {"B": 10.0} and b.cash == 9_000.0 and r.coid not in om.settling
    assert [f.qty for f in booked] == [10.0]
    assert not [d for d in _last(om, D2) if d.kind == "unbooked"]
    v.report()  # the activity comes after all
    om.sync()
    assert _book(j).qty_by_symbol() == {"B": 10.0} and len(booked) == 1 and not om.foreign
    again = OrderManager(v, j, clock, run_id=1)  # a restart folds the same
    assert again.read_fills() == [] and not again.settling and "a1" in again.seen


def test_accept_it_stops_counting_the_shares_as_unbooked_and_a_late_activity_is_still_booked():
    clock, v, j, om, r, booked = _setup()
    clock.t = D2
    apply_unbooked(om, unbooked_plan(om, _last(om, D2), [_row(r, "accept")], D2), by="owner", reason="sold by hand")
    assert r.coid not in om.settling and om.unbooked("B") == 0 and not _book(j).qty_by_symbol()
    assert not [d for d in _last(om, D2) if d.kind == "unbooked"]
    again = OrderManager(v, j, clock, run_id=1)
    assert not again.settling
    v.report()
    om.sync()
    assert _book(j).qty_by_symbol() == {"B": 10.0} and len(booked) == 1  # the venue's report is the fact


@pytest.mark.parametrize("change, why", [
    (lambda om, r, v: setattr(v, "view", {**v.view, "filled_qty": "9"}), "now shows 9"),
    (lambda om, r, v: setattr(v, "view", {**v.view, "filled_avg_price": "NaN"}), "cannot be read"),
    (lambda om, r, v: setattr(v, "down", True), "could not be read"),
])
def test_nothing_is_done_when_the_venue_no_longer_shows_exactly_that(change, why):
    clock, v, j, om, r, booked = _setup()
    clock.t = D2
    change(om, r, v)
    with pytest.raises(ValueError, match=why):
        unbooked_plan(om, _last(om, D2), [_row(r)], D2)
    assert not booked and r.coid in om.settling


def test_the_rows_must_be_exactly_what_the_screen_showed():
    clock, v, j, om, r, booked = _setup()
    clock.t = D2
    last = _last(om, D2)
    for rows, why in (([_row(r, venue=9)], "changed since it was shown"),
                      ([_row(r, booked=1)], "changed since it was shown"),
                      ([_row(r, action="sell")], "book or accept"),
                      ([], "changed since they were shown"),
                      ([_row(r), {**_row(r), "coid": "other"}], "changed since they were shown")):
        with pytest.raises(ValueError, match=why):
            unbooked_plan(om, last, rows, D2)
    with pytest.raises(ValueError, match="changed since they were shown"):  # same day: not aged, not shown
        unbooked_plan(om, _last(om, D1), [_row(r)], D1)
    assert not booked


def test_the_owner_cannot_book_on_the_orders_own_day():
    clock, v, j, om, r, booked = _setup()
    with pytest.raises(ValueError, match="of today"):
        om.owner_check(r.coid, r.cum, r.booked, D1)
