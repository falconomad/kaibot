"""#533: "Flatten and stop" must not end the run while the run's daily-path orders still hold shares the ledger does
not know: an order still open (it can fill), or a finished one whose fill is not booked while the venue holds those
shares. Written before the fix; the end-to-end cases are tests/dst/test_dst.py
(test_flatten_and_stop_sells_a_filled_buy_whose_activity_has_not_arrived,
test_flatten_and_stop_waits_for_a_buy_whose_cancel_was_lost). These pin `Engine._nothing_to_sell` case by case."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kaibot.engine import core
from kaibot.engine.orders import OrderRecord, St


class _Book:
    def __init__(self, qty: dict[str, float]):
        self._q = qty

    def qty_by_symbol(self):
        return dict(self._q)


class _OM:
    def __init__(self, run_id=7, records=(), fail=False):
        self.run_id, self.fail = run_id, fail
        self.records = list(records)

    def working(self):
        if self.fail:
            raise RuntimeError("journal unreadable")
        return [r for r in self.records if r.open]

    @property
    def orders(self):
        return {r.coid: r for r in self.records}

    @property
    def settling(self):
        return {r.coid: r for r in self.records if not r.open and r.cum > r.booked + 1e-9}


_NOW = datetime(2026, 8, 3, 19, 53, tzinfo=UTC)  # 15:53 ET on the order's day


def _rec(coid, symbol="B", side="buy", state=St.FILLED, cum=23.0, booked=0.0, purpose="target"):
    return OrderRecord(coid=coid, strategy="trend_momentum", symbol=symbol, side=side, qty=23.0, state=state, cum=cum,
                       booked=booked, purpose=purpose, session="2026-08-03")


def _engine(*, records=(), venue=None, ledger=None, om_run=7, om_fail=False, venue_fail=False, sleeves=()):
    def positions():
        if venue_fail:
            raise RuntimeError("venue down")
        return {s: {"qty": q} for s, q in (venue or {}).items()}

    e = SimpleNamespace(pos={}, _md_exits={}, daily=SimpleNamespace(om=_OM(om_run, records, om_fail)),
                        broker=SimpleNamespace(positions=positions), ledger=SimpleNamespace(
                            book=lambda rid: _Book(ledger or {})), _rid=lambda: 7, now=lambda: _NOW,
                        _md_sleeves=lambda: {("trend_momentum", s): None for s in sleeves})
    e._om_unsettled = lambda: core.Engine._om_unsettled(e)
    e._om_unbooked_sale = lambda: core.Engine._om_unbooked_sale(e)
    return e


def flat(e) -> bool:
    return core.Engine._nothing_to_sell(e)


def test_a_filled_buy_whose_activity_has_not_arrived_keeps_the_run_while_the_venue_holds_it():
    """The issue's case: the order is `filled` (cum 23), nothing booked, the venue long 23 B, the ledger flat."""
    assert not flat(_engine(records=[_rec("b1")], venue={"B": 23.0}))


def test_once_the_fill_is_booked_the_sleeve_decides_as_before():
    assert not flat(_engine(records=[_rec("b1", booked=23.0)], venue={"B": 23.0}, ledger={"B": 23.0}, sleeves=("B",)))
    assert flat(_engine(records=[_rec("b1", booked=23.0)], venue={}, ledger={}))  # sold and booked: flat


@pytest.mark.parametrize("state", [St.ACCEPTED, St.PARTIAL, St.PENDING_CANCEL, St.UNKNOWN, St.SUBMITTED])
def test_an_open_order_of_the_run_keeps_the_run_whatever_the_venue_holds(state):
    """The comment's case: a buy still working (or being cancelled, or unconfirmed) can fill after the run ended."""
    assert not flat(_engine(records=[_rec("b1", state=state, cum=0.0)], venue={}))


def test_a_resting_protective_stop_does_not_keep_the_run():
    """Stops are the flatten's to cancel with the sleeve (`_flatten_md`); one left without shares sells nothing."""
    assert flat(_engine(records=[_rec("s1", side="sell", state=St.ACCEPTED, cum=0.0, purpose="stop")], venue={}))


def test_unbooked_shares_the_venue_does_not_hold_leave_nothing_to_sell():
    """A fail-safe sold them meanwhile (or they were never there): no share is left, the run may end. The late
    activity, when it comes, is a reconciliation difference (#529), not a sale."""
    assert flat(_engine(records=[_rec("b1")], venue={}))
    assert flat(_engine(records=[_rec("b1")], venue={"B": 10.0}, ledger={"B": 10.0}))  # the venue holds only booked


def test_a_filled_sale_whose_activity_has_not_arrived_keeps_the_run_591():
    """#591 (seed 796524965): the sale filled at the venue (no B there any more), its activity is late, the ledger
    still holds the 23 shares. The venue holds less than the ledger, so the buy rule above would end the run, and
    with it the sync that books the sale: the run waits for the booking instead."""
    sale = _rec("s1", side="sell")
    assert not flat(_engine(records=[sale], venue={}, ledger={"B": 23.0}, sleeves=("B",)))
    assert not flat(_engine(records=[sale], venue={}))
    sale.booked = 23.0  # booked: the ledger is flat, nothing is left to wait for
    assert flat(_engine(records=[sale], venue={}))


def test_an_unreadable_venue_or_order_manager_never_ends_the_run():
    assert not flat(_engine(records=[_rec("b1")], venue_fail=True))
    assert not flat(_engine(records=[_rec("b1")], venue={}, om_fail=True))


def test_another_runs_order_manager_and_no_daily_path_change_nothing():
    assert flat(_engine(records=[_rec("b1", state=St.ACCEPTED, cum=0.0)], venue={}, om_run=6))
    e = _engine(venue={})
    e.daily = None
    assert flat(e)


# ---------------------------------------------------------------- QA-Ops on #544: the bound of the wait (#524's rule)
def _aged_engine(now, records):
    events = []
    e = _engine(records=records, venue={"B": 23.0})
    e.now = lambda: now
    e._cmd_aged = {}
    e._cmd_note, e._cmd_waiting = {}, {}
    e.journal = SimpleNamespace(append=lambda *a, **k: None)
    e.store = SimpleNamespace(event=lambda kind, body, rid=None: events.append((kind, body)))
    return e, events


def test_an_unbooked_fill_of_an_earlier_day_raises_one_alarm_and_the_command_keeps_waiting():
    """The activity never arrives (#529): the run must not wait silently for ever, and must not be ended with the
    shares at the venue either. From the order's next New York day on, one more `command_waiting` event (with `aged`)
    names the order and the owner's action; the command stays pending (nothing to sell is still False)."""

    r = _rec("b1")
    r.created = "2026-08-03T19:50:00+00:00"  # 15:50 ET on 3 Aug
    same_day = datetime(2026, 8, 3, 20, 30, tzinfo=UTC)  # 16:30 ET: its activity is still on its way
    e, events = _aged_engine(same_day, [r])
    core.Engine._cmd_unbooked_aged(e, "c1", "flatten_and_stop")
    assert events == []
    e.now = lambda: datetime(2026, 8, 4, 14, 0, tzinfo=UTC)  # the next morning
    for _ in range(3):  # every pass comes here: raised once
        core.Engine._cmd_unbooked_aged(e, "c1", "flatten_and_stop")
    assert [k for k, _ in events] == ["command_waiting"]
    body = events[0][1]
    assert body["why"] == "a fill of an earlier day was never booked"
    assert body["aged"] == [{"coid": "b1", "symbol": "B", "side": "buy", "unbooked": 23.0, "session": "2026-08-03"}]
    assert "#529" in body["action"]
    assert not flat(e)  # fail closed: the run is not ended
    core.Engine._cmd_done(e, "c1")
    assert e._cmd_aged == {}


def test_the_aged_check_never_raises():
    e, events = _aged_engine(None, [_rec("b1")])
    e.daily.om.fail = True
    e.now = lambda: (_ for _ in ()).throw(RuntimeError("clock"))
    core.Engine._cmd_unbooked_aged(e, "c1", "flatten_and_stop")
    assert events == []


def test_an_order_called_lost_keeps_the_run_until_its_day_is_over():
    """QA-Ops on #544 with #535: "lost" is a guess; the venue may still fill the day order (and #535 adopts the fill).
    Until the order's New York day is over the run is kept as for an open order; after it, the verdict stands."""
    r = _rec("b1", state=St.REJECTED, cum=0.0)
    r.reason, r.created = "lost: never reached the venue", "2026-08-03T19:50:00+00:00"
    assert not flat(_engine(records=[r], venue={}))
    e = _engine(records=[r], venue={})
    e.now = lambda: datetime(2026, 8, 4, 13, 31, tzinfo=UTC)  # the next session
    assert flat(e)
    refused = _rec("b2", state=St.REJECTED, cum=0.0)
    refused.reason, refused.created = "403: insufficient buying power", "2026-08-03T19:50:00+00:00"
    assert flat(_engine(records=[refused], venue={}))  # a refusal by the venue is not a guess
