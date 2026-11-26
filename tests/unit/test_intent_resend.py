"""#775: a process killed between an order's journalled intent and the venue's answer leaves an `intent` record. The
restarted process looks it up by its client order id; when that finds nothing, the live send sends the same order
again, in the same window, under the SAME client order id (`OrderManager.resend` through the target loop's
`resend`), never a new attempt. If the dead process's call had reached the venue, the venue refuses the duplicate
(Alpaca: client order ids are unique per account; the adapters read back the order that is there) and the record
adopts it: one order at the venue, whatever the crash point. Anything that would stop a new order now stops the
resend too; then the order is waited on (skipped, so the caller's send stays open) until its lookup resolves it.
Without `resend` (research, the default) the loop is as before."""

from __future__ import annotations

import pytest
from test_order_manager import DAY, NEXT, et, manager, run, venue

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.orders import St, client_order_id
from kaibot.engine.targets import Target, reconcile_targets

POL = ExecutionPolicy("near_close")
NEAR = ("15:51", 7)  # inside the near-close window (15:50-16:00 ET)


class Kill(BaseException):
    """The process dies (not an Exception: nothing in the engine catches it)."""


def died(b, clock, *, reached: bool, side: str = "sell", qty: int = 30):
    """A process that sells (or buys) `qty` A near the close and dies in `submit`: before the venue call
    (`reached` False) or after the venue took the order and before its answer was journalled (`reached` True).
    Returns the journal it left and the client order id."""
    om, journal, _ = manager(b, clock)
    coid = client_order_id("1", "rule", "A", DAY, side, 1)
    real = b.submit

    def submit(req):
        if reached:
            real(req)
        raise Kill

    b.submit = submit
    with pytest.raises(Kill):
        om.submit(POL.request("A", side, qty, clock(), coid), strategy="rule", purpose=side, session=DAY)
    b.submit = real
    return journal, coid


def at_close():
    b, clock = venue()
    run(b, clock, 0, 380)  # 15:50
    clock.set(et(DAY, *NEAR))
    b.pos["A"] = {"qty": 30.0, "avg_entry_price": 100.0}  # the sleeve's 30 shares, held since an earlier day
    return b, clock


def restarted(b, clock, journal):
    om, _, _ = manager(b, clock, journal=journal)
    om.recover()  # the start-up: lookups by client id, before any send
    return om


class Lagging:
    """What the manager reads of the venue while its lookup by client id lags behind an order it has (and so do its
    open orders and fills, so that nothing finds it): the venue itself still refuses a reused client id."""

    def __init__(self, b) -> None:
        self.b = b

    def __getattr__(self, name):
        return getattr(self.b, name)

    def order_by_client_id(self, coid):
        return None

    def open_orders(self):
        return []

    def activities(self, after=None, types=("FILL",)):
        return []


def ours(b, coid):
    return [o for o in b.orders.values() if o.get("client_order_id") == coid]


def send(om, clock, *, shares=0, held=30.0, resend=True, **kw):
    return reconcile_targets(om, POL, [Target("rule", "A", shares, "sell")], {("rule", "A"): held}, now=clock(),
                             session=DAY, run="1", resend=resend, **kw)


def test_a_kill_before_the_venue_call_is_sent_again_in_the_window_under_its_own_client_id():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False)
    assert ours(b, coid) == []  # nothing reached the venue
    om = restarted(b, clock, journal)
    r = om.get(coid)
    assert r.state is St.INTENT and om.orphan(r, DAY) and om.resendable(r)
    res = send(om, clock)
    assert [x.coid for x in res.sent] == [coid] and r.state is St.ACCEPTED and not res.skipped
    (o,) = ours(b, coid)
    assert (o["side"], float(o["qty"])) == ("sell", 30.0) and len(b.orders) == 1
    assert om.attempts(coid.rsplit("-", 1)[0]) == 1  # no new attempt was made
    # the next pass: the order is working, nothing more goes out
    assert send(om, clock).sent == [] and len(b.orders) == 1


def test_a_kill_after_the_venue_call_is_found_by_its_lookup_and_never_sent_again():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=True)
    assert len(ours(b, coid)) == 1
    om = restarted(b, clock, journal)
    assert om.get(coid).state is St.ACCEPTED  # the lookup by client id found it: adopted
    res = send(om, clock)
    assert res.sent == [] and len(b.orders) == 1


def test_a_kill_after_the_venue_call_whose_lookup_lags_is_refused_as_a_duplicate_and_adopted():
    """The worst case for a double send: the venue has the order and its lookup by client id does not show it yet.
    The resend goes out under the same client id; the venue answers with the order it already has."""
    b, clock = at_close()
    journal, coid = died(b, clock, reached=True)
    (first,) = ours(b, coid)
    om = restarted(Lagging(b), clock, journal)
    assert om.orphan(om.get(coid), DAY)
    res = send(om, clock)
    assert [x.coid for x in res.sent] == [coid]
    assert ours(b, coid) == [first] and len(b.orders) == 1  # one order: the one the dead process placed
    assert om.get(coid).order_id == first["id"] and om.get(coid).state is St.ACCEPTED


def test_a_venue_that_refuses_the_duplicate_without_reading_it_back_leaves_the_order_unknown():
    """Alpaca's 422 for a reused client id, without the order read back: `unknown`, the symbol blocked, nothing new."""
    b, clock = at_close()
    journal, coid = died(b, clock, reached=True)
    real = b.submit

    def refuse(req):
        if any(o.get("client_order_id") == req.client_order_id for o in b.orders.values()):
            raise BrokerError(422, '{"code":40010001,"message":"client_order_id must be unique"}')
        return real(req)

    b.submit = refuse
    om = restarted(Lagging(b), clock, journal)
    res = send(om, clock)
    r = om.get(coid)
    assert r.state is St.UNKNOWN and om.blocked("A") and len(b.orders) == 1
    assert res.sent == [r]  # the caller sees an unconfirmed order: its send stays open
    assert send(om, clock).sent == [] and len(b.orders) == 1  # blocked: no new attempt while it is unknown


def test_a_kill_during_the_resend_itself_is_sent_once_more_under_the_same_id_and_still_one_order():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False)
    om = restarted(b, clock, journal)
    real = b.submit

    def submit(req):
        real(req)
        raise Kill

    b.submit = submit
    with pytest.raises(Kill):
        send(om, clock)
    b.submit = real
    clock.advance(seconds=31)
    om = restarted(Lagging(b), clock, journal)  # and the next start's lookup lags too
    res = send(om, clock)
    assert [x.coid for x in res.sent] == [coid] and len(ours(b, coid)) == 1 and len(b.orders) == 1


@pytest.mark.parametrize("side", ["sell", "buy"])
def test_without_resend_the_loop_is_as_before_the_orphan_counts_as_working(side):
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False, side=side)
    om = restarted(b, clock, journal)
    res = send(om, clock, shares=0 if side == "sell" else 60, resend=False)
    assert res.sent == [] and res.skipped == [] and b.orders == {}


def test_an_orphan_whose_target_moved_is_not_sent_again_and_is_waited_on():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False)
    om = restarted(b, clock, journal)
    res = send(om, clock, shares=10)  # the target now sells 20, the orphan 30
    assert res.sent == [] and [k for k, _ in res.skipped] == [("rule", "A")] and b.orders == {}
    assert om.get(coid).state is St.INTENT


def test_an_orphan_the_reconciliation_or_the_presend_gate_would_stop_is_not_sent():
    class No:
        def allows(self, symbol, side, purpose, qty):
            return "reconciliation difference"

    b, clock = at_close()
    journal, coid = died(b, clock, reached=False, side="buy", qty=30)
    om = restarted(b, clock, journal)
    res = reconcile_targets(om, POL, [Target("rule", "A", 30, "buy")], {}, now=clock(), session=DAY, run="1",
                            resend=True, reconciler=No())
    assert res.sent == [] and res.skipped == [(("rule", "A"), "reconciliation difference")] and b.orders == {}
    res = reconcile_targets(om, POL, [Target("rule", "A", 30, "buy")], {}, now=clock(), session=DAY, run="1",
                            resend=True, presend=lambda t, side, qty: "the price moved")
    assert res.sent == [] and res.withheld == [(("rule", "A"), "buy", "the price moved")] and b.orders == {}


def test_an_orphan_whose_lookup_never_answered_is_not_sent_and_keeps_the_send_open():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False)
    real = b.order_by_client_id

    def down(coid):
        raise BrokerError(503, "unavailable")

    b.order_by_client_id = down
    om = restarted(b, clock, journal)
    assert om.orphan(om.get(coid), DAY) and not om.resendable(om.get(coid))
    res = send(om, clock)  # skipped (the caller's send stays open), not sent, not counted as covering the target
    assert res.sent == [] and [k for k, _ in res.skipped] == [("rule", "A")] and b.orders == {}
    b.order_by_client_id = real


def test_an_intent_of_another_session_is_not_an_orphan():
    b, clock = at_close()
    journal, coid = died(b, clock, reached=False)
    om = restarted(b, clock, journal)
    assert om.orphan(om.get(coid), DAY) and not om.orphan(om.get(coid), NEXT)
    res = reconcile_targets(om, POL, [Target("rule", "A", 0, "sell")], {("rule", "A"): 30.0}, now=clock(),
                            session=NEXT, run="1", resend=True)
    assert res.sent == [] and b.orders == {}


def test_resend_of_a_record_that_is_not_an_orphan_sends_nothing():
    b, clock = at_close()
    om, _, _ = manager(b, clock)
    r = om.submit(OrderRequest("A", "sell", 5, "market", "day", client_order_id("1", "rule", "A", DAY, "sell", 1)),
                  strategy="rule", purpose="sell", session=DAY)
    assert r.state is St.ACCEPTED and om.resend(r.coid) is r and len(b.orders) == 1
