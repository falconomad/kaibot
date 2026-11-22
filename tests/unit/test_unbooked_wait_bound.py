"""QA-Ops second key on #524 (issue #507): the target loop's wait for an unbooked fill is bounded for sales.

#524 makes the loop wait for a finished order's unbooked fill whatever session the order is of. A fill whose
account activity never arrives would then freeze every order of that sleeve, its sales included, for good. The
bound: a buy always waits (it is the double buy of #507, and the reconciliation's "unbooked" difference blocks it
too); a sale waits while the unbooked order is of today (New York) and goes out from the next day on, for the
shares the ledger holds less the shares the unbooked SALES already took at the venue."""

from __future__ import annotations

from test_order_manager import DAY, NEXT, et, manager, mkt, run, venue

from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.orders import St
from kaibot.engine.targets import Target, reconcile_targets

POL, Q = ExecutionPolicy("marketable_limit"), {"A": (99.9, 100.0)}


def _held_100_and_one_order_unbooked(side: str, qty: int):
    """The sleeve holds 100 A, booked. Then one more order of `side` fills at the venue and its account activity
    never arrives: the order's status says filled, the ledger does not have the fill."""
    b, clock = venue()
    om, _, booked = manager(b, clock)
    om.submit(mkt("A", qty=100), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 31)
    om.sync()
    assert [f.qty for f in booked] == [100]
    late = om.submit(mkt("A", side=side, qty=qty, purpose=side, attempt=2), strategy="rule", purpose=side,
                     session=DAY)
    run(b, clock, 40, 41)
    real = b.activities
    b.activities = lambda after=None, types=("FILL",): [a for a in real(after, types)
                                                         if a.get("order_id") != late.order_id]
    om.sync()
    assert late.state is St.FILLED and om.unbooked("A", "rule") == qty and len(booked) == 1
    return b, clock, om


def _loop(om, clock, shares, day=DAY):
    return reconcile_targets(om, POL, [Target("rule", "A", shares, "sell")], {("rule", "A"): 100.0}, now=clock(),
                             session=day, quotes=Q)


def test_on_the_day_of_the_unbooked_fill_the_sleeve_waits_buy_and_sale():
    b, clock, om = _held_100_and_one_order_unbooked("buy", 10)
    n = len(b.orders)
    for shares in (0, 150):
        res = _loop(om, clock, shares)
        assert not res.sent and not res.aged and "not booked yet" in res.skipped[0][1]
    assert len(b.orders) == n and om.unbooked_sold("A", "rule", clock()) is None


def test_from_the_next_day_a_sale_goes_out_for_the_ledgers_shares_and_a_buy_still_waits():
    """An unbooked BUY: the venue holds 110, the ledger 100. The rule wants out: the 100 the ledger holds are sold
    (the 10 stay an open difference for the owner). A higher target still buys nothing."""
    b, clock, om = _held_100_and_one_order_unbooked("buy", 10)
    clock.set(et(NEXT, "10:00", 7))
    n = len(b.orders)
    res = _loop(om, clock, 150, NEXT)
    assert not res.sent and not res.aged and len(b.orders) == n  # never the double buy of #507
    res = _loop(om, clock, 0, NEXT)
    (r,) = res.sent
    assert (r.side, r.qty) == ("sell", 100) and res.aged and res.aged[0][0] == ("rule", "A")
    assert "earlier day" in res.aged[0][1] and not res.skipped


def test_from_the_next_day_a_sale_leaves_out_the_shares_an_unbooked_sale_already_took():
    """An unbooked SALE of 40: the ledger still says 100, the venue holds 60. Selling "everything" must be 60, not
    100 (the venue would refuse 100, or go short where shorting is on)."""
    b, clock, om = _held_100_and_one_order_unbooked("sell", 40)
    clock.set(et(NEXT, "10:00", 7))
    assert om.unbooked_sold("A", "rule", clock()) == 40
    res = _loop(om, clock, 60, NEXT)  # the ledger's 100 less the 40 already sold is the target: nothing to do
    assert not res.sent and not res.aged and "not booked yet" in res.skipped[0][1]
    res = _loop(om, clock, 0, NEXT)
    (r,) = res.sent
    assert (r.side, r.qty) == ("sell", 60) and "40" in res.aged[0][1]
    assert float(b.pos["A"]["qty"]) == 60  # and the venue has exactly these
