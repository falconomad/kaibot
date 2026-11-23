"""#535: an order the manager called lost ("never reached the venue", state `rejected`) that the venue had after
all and fills later. Written before the fix: the fill stayed `foreign` for good (a quantity, a cash and a
foreign_fill difference, the shares in no sleeve). Now a lost order stays under watch for LOST_WATCH: a fill in its
symbol and side on an order the manager cannot name is looked up once by its venue id, and when that order carries
the lost record's client order id, the record adopts it and the fill is booked to it. The end-to-end case is
tests/dst/test_dst.py::test_an_order_called_lost_that_fills_later_is_booked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine import orders as om_mod
from kaibot.engine.journal import Journal
from kaibot.engine.orders import OrderManager, St, client_order_id

T0 = datetime(2026, 8, 3, 19, 50, 7, tzinfo=UTC)  # 15:50 ET
DAY = "2026-08-03"


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


class Venue:
    """The order reached the venue (`o1`), but the submit's answer was lost and neither the lookup by client id nor
    the open-orders list shows it (both lag). `fill()` trades it: an activity on `o1`."""

    def __init__(self, clock: Clock, coid: str):
        self.clock, self.coid = clock, coid
        self.acts: list[dict] = []
        self.order_calls: list[str] = []
        self.order_error: BrokerError | None = None
        self.status = "new"
        self.pos: dict[str, dict] = {}

    def submit(self, req):
        raise BrokerError(0, "no answer")

    def order_by_client_id(self, coid):
        return None  # lags

    def open_orders(self):
        return []  # lags too

    def order(self, oid):
        self.order_calls.append(oid)
        if self.order_error is not None:
            raise self.order_error
        if oid == "o1":
            return {"id": "o1", "client_order_id": self.coid, "symbol": "B", "side": "buy", "qty": "23",
                    "filled_qty": "23" if self.status == "filled" else "0", "status": self.status}
        if oid == "x9":
            return {"id": "x9", "client_order_id": "someone-else", "symbol": "B", "side": "buy", "qty": "5",
                    "filled_qty": "5", "status": "filled"}
        raise BrokerError(404, "not found")

    def activities(self, after=None, types=("FILL",)):
        return [a for a in self.acts if after is None or datetime.fromisoformat(a["transaction_time"]) > after]

    def positions(self):
        return dict(self.pos)

    def fill(self, oid="o1", qty=23.0, aid="a1"):
        self.status = "filled"
        self.acts.append({"id": aid, "order_id": oid, "symbol": "B", "side": "buy", "qty": str(qty), "price": "98.0",
                          "transaction_time": self.clock().isoformat()})
        self.pos["B"] = {"qty": str(qty)}


def _lost(tmp_path=None):
    clock = Clock(T0)
    coid = client_order_id(7, "trend_momentum", "B", DAY, "buy", 1)
    v = Venue(clock, coid)
    j = Journal(now=clock)
    booked: list = []
    om = OrderManager(v, j, clock, on_fill=lambda f, r: booked.append(f), position=lambda s: 0.0)
    r = om.submit(OrderRequest("B", "buy", 23, "market", "day", coid), strategy="trend_momentum", purpose="buy",
                  session=DAY)
    assert r.state is St.UNKNOWN
    for _ in range(3):  # 15:50:07, 15:50:38, 15:51:09: two misses 30 s apart, a minute after the intent
        om.sync()
        clock.advance(seconds=31)
    assert r.state is St.REJECTED and r.reason.startswith("lost"), r
    return clock, v, j, om, r, booked


def test_a_fill_on_the_lost_order_is_adopted_and_booked_not_foreign():
    clock, v, _, om, r, booked = _lost()
    clock.advance(minutes=2)
    v.fill()
    om.sync()
    assert not om.foreign, om.foreign
    assert r.state is St.FILLED and r.order_id == "o1" and r.booked == 23.0 and r.cum == 23.0
    assert [f.coid for f in booked] == [r.coid] and booked[0].qty == 23.0
    assert "adopted" in (r.reason or "")
    # the live reconciliation reads an entry in `errors` during read_fills as "the activities were not read"
    assert om.errors == []
    om.sync()
    assert len(booked) == 1  # booked once


def test_the_adoption_survives_a_restart():
    clock, v, j, om, r, _ = _lost()
    clock.advance(minutes=2)
    v.fill()
    om.sync()
    again = OrderManager(v, j, clock)
    r2 = again.get(r.coid)
    assert r2.state is St.FILLED and r2.booked == 23.0 and again.by_id["o1"] == r.coid and not again.settling
    assert again.read_fills() == [] and not again.foreign


def test_a_fill_on_someone_elses_order_stays_foreign_and_is_looked_up_once():
    clock, v, _, om, r, booked = _lost()
    v.acts.append({"id": "a9", "order_id": "x9", "symbol": "B", "side": "buy", "qty": "5", "price": "98.0",
                   "transaction_time": clock().isoformat()})
    om.sync()
    om.sync()
    assert "a9" in om.foreign and r.state is St.REJECTED and not booked
    assert v.order_calls.count("x9") == 1


def test_a_fill_in_another_symbol_or_side_is_not_looked_up():
    clock, v, _, om, r, _ = _lost()
    v.acts.append({"id": "a8", "order_id": "x8", "symbol": "C", "side": "buy", "qty": "5", "price": "9.0",
                   "transaction_time": clock().isoformat()})
    v.acts.append({"id": "a7", "order_id": "x7", "symbol": "B", "side": "sell", "qty": "5", "price": "9.0",
                   "transaction_time": clock().isoformat()})
    om.sync()
    assert {"a8", "a7"} <= set(om.foreign) and v.order_calls == []


def test_a_lookup_that_fails_is_tried_again_at_the_next_read():
    clock, v, _, om, r, booked = _lost()
    clock.advance(minutes=2)
    v.fill()
    v.order_error = BrokerError(503, "busy")
    om.errors.clear()
    om.read_fills()
    assert "a1" in om.foreign and r.state is St.REJECTED and not booked
    assert om.errors == []  # the activities WERE read: the reconciliation must not count a failed read
    v.order_error = None
    om.sync()
    assert not om.foreign and r.state is St.FILLED and len(booked) == 1


def test_the_watch_ends_after_lost_watch():
    clock, v, _, om, r, booked = _lost()
    clock.advance(seconds=om_mod.LOST_WATCH.total_seconds() + 60)
    v.fill()
    om.sync()
    assert "a1" in om.foreign and r.state is St.REJECTED and not booked and v.order_calls == []


def test_the_manager_says_it_is_watching_only_inside_the_watch():
    """The live path reads the fills while `watching()` (every POLL_EVERY, live_daily.LiveDaily._sync), also when
    no order is open: else the late fill waits for the next reconciliation's read."""
    clock, _, _, om, _, _ = _lost()
    assert om.watching() and not om.live
    clock.advance(seconds=om_mod.LOST_WATCH.total_seconds() + 60)
    assert not om.watching()
