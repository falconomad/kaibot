"""Open orders unreadable in the send window: the due sales still go out, the buys wait (audit 2 5.10's shape, #737).

Before: `LiveDaily.send` read the venue's open orders (for the wash-trade check, `opposite_orders`) and, when that
read failed, skipped the whole rule for the tick, sales included; `_drop_failsafe_stops` did the same when the
fail-safe stops' adoption could not read them. An open-orders endpoint down all window cost the session's sales.

Now an unreadable read sets `_Gate.open_unread`: the target loop skips every buy (the send stays open, the next
tick reads again, a buy not sent by the window's end is carried), and the sales go out.

Why a sale is safe without that read (the condition the change rests on, checked here on `OrderManager`): its
client order id is deterministic (`client_order_id`), an open or unconfirmed order of the same decision is reused
and never doubled (`next_coid`, `blocked`, the target loop's unconfirmed skip), the same id submitted again is the
same record and nothing is sent (`submit`), and the venue refuses a reused id (Alpaca 422; the simulator returns the
order already there). A new attempt number follows only a refusal, a confirmed cancel or "lost", and "lost" needs
the open orders read: unreadable is "not lost" (`_lost_evidence`). So while the read fails, a sale whose answer was
lost stays unconfirmed and blocks its symbol; it is never sent under a second id.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "dst"))

import world  # noqa: E402
from scenarios import quiet  # noqa: E402
from world import D1, D2, D3, NY, World  # noqa: E402

from kaibot.broker.faulty import Forced  # noqa: E402
from kaibot.broker.protocol import BrokerError, OrderRequest  # noqa: E402
from kaibot.engine import live_daily as ld  # noqa: E402
from kaibot.engine.core import Engine  # noqa: E402
from kaibot.engine.execution import ExecutionPolicy  # noqa: E402
from kaibot.engine.journal import Journal  # noqa: E402
from kaibot.engine.orders import LOOKUP_GAP_S, LOST_AFTER_S, OrderManager, St, client_order_id  # noqa: E402
from kaibot.engine.targets import Target, reconcile_targets  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

DAY2, DAY3 = D2.replace("-", ""), D3.replace("-", "")
OPEN_DOWN = Forced("rate_limit", f"open_orders|{DAY2}T19")  # 15:00-15:59 ET on D2 (EDT): every open-orders read


def book_loop(monkeypatch) -> None:
    none = {level: [] for level in load_profiles()}
    monkeypatch.setattr(world, "Engine", lambda *a, **k: Engine(*a, intraday=False, strategies=none, **k))


def venue_orders(w, symbol, side):
    return [o for o in w.sim.orders.values() if o["symbol"] == symbol and o["side"] == side]


# ---------------------------------------------------------------- the engine, in the DST world (book loop)
def test_open_orders_unreadable_all_window_the_sale_goes_out_and_the_buy_waits(monkeypatch):
    book_loop(monkeypatch)
    w = World(quiet({D1: ("B",), D2: ("C",)}, forced=(OPEN_DOWN,)))
    res = w.run()
    assert res.ok, res.report()
    assert not w.eng.intraday and any("fault rate_limit open_orders" in t for t in res.trace)
    sold = venue_orders(w, "B", "sell")
    assert len(sold) == 1 and f"-B-{DAY2}-sell-1" in sold[0]["client_order_id"] and sold[0]["status"] == "filled"
    assert datetime.fromisoformat(sold[0]["submitted_at"]).astimezone(NY).strftime("%H:%M") == "15:50"
    bought = venue_orders(w, "C", "buy")
    assert len(bought) == 1 and f"-C-{DAY3}-" in bought[0]["client_order_id"]  # not on D2: it waited, then carried
    waits = [r for r in w.replica.tables.get("event", []) if r.get("type") == "multi_day_waiting"
             and (r.get("payload") or {}).get("why") == ld.OPEN_UNREAD]
    assert len(waits) == 1  # one for the spell, not one a tick


def test_before_the_change_the_sale_waited_too(monkeypatch):
    """The old behaviour (the whole rule skipped while the read fails), planted: no sale of B in D2's window."""
    book_loop(monkeypatch)
    send = ld.LiveDaily.send

    def skip_all(self, session):
        try:
            self.broker.open_orders()
        except BrokerError:
            return []
        return send(self, session)

    monkeypatch.setattr(ld.LiveDaily, "send", skip_all)
    w = World(quiet({D1: ("B",), D2: ("C",)}, forced=(OPEN_DOWN,)))
    w.run()
    assert not [o for o in venue_orders(w, "B", "sell") if f"-{DAY2}-" in o["client_order_id"]]


def test_a_sale_whose_answer_is_lost_while_open_orders_are_unreadable_is_never_sent_twice(monkeypatch):
    """The safety review's case: the sale reaches the venue, its answer is lost, the lookup by client id lags, and
    the open orders cannot be read all window. The order stays unconfirmed ("not lost": the venue cannot be read),
    no second attempt is made, and the venue holds exactly one sale of B for D2."""
    book_loop(monkeypatch)
    w = World(quiet({D1: ("B",), D2: ("C",)}, forced=(
        OPEN_DOWN, Forced("submit_timeout_accepted", f"-B-{DAY2}-sell"),
        Forced("lookup_lag", f"-B-{DAY2}-sell", param=6), Forced("listing_lag", f"-B-{DAY2}-sell"))))
    res = w.run()
    assert res.ok, res.report()
    assert any("fault submit_timeout_accepted" in t for t in res.trace)
    sold = [o for o in venue_orders(w, "B", "sell") if f"-{DAY2}-" in o["client_order_id"]]
    assert len(sold) == 1 and sold[0]["client_order_id"].endswith("-sell-1"), sold
    assert not [o for o in venue_orders(w, "C", "buy") if f"-{DAY2}-" in o["client_order_id"]]


def test_a_fail_safe_stop_the_engine_cannot_see_makes_the_venue_refuse_the_sale_never_sell_twice(monkeypatch):
    """The fail-safe stop's adoption needs the open orders too (#188). A watchdog stop on all of B's shares is placed
    at 15:00 on D2, behind the engine's back, while the open orders cannot be read: the sale goes out at 15:50 and
    the venue refuses it (the stop holds the shares: insufficient qty). Nothing is sold twice or short; on D3 the
    stop is adopted and cancelled and the sale goes out."""
    from kaibot.engine.watchdog import failsafe_coid

    book_loop(monkeypatch)
    w = World(quiet({D1: ("B",), D2: ("C",)}, forced=(OPEN_DOWN,)))
    tick, placed = w.one_pass, []

    def watched():
        if not placed and w.clock() >= world.et(D2, "15:00"):
            qty = float(w.sim.positions()["B"]["qty"])
            placed.append(w.sim.submit(OrderRequest("B", "sell", qty, "stop", "gtc",
                                                    failsafe_coid(world.MD, "B", w.clock()), stop_price=1.0)))
        tick()

    w.one_pass = watched
    res = w.run()
    assert placed and res.ok, res.report()
    sells = venue_orders(w, "B", "sell")
    filled = [o for o in sells if o["status"] == "filled"]
    assert len(filled) == 1 and f"-B-{DAY3}-sell-" in filled[0]["client_order_id"], sells
    assert float(filled[0]["filled_qty"]) == float(placed[0]["qty"])
    assert "B" not in w.sim.positions()  # flat, never short
    refused = [t for t in res.trace if f"-B-{DAY2}-sell-" in t and "state=rejected" in t]
    assert refused and all("insufficient qty available" in t for t in refused)  # every D2 try refused by the venue


# ---------------------------------------------------------------- the condition, on OrderManager
class _Venue:
    """A sale's answer is lost (status 0); the lookup by client id finds nothing; the open orders cannot be read."""

    name = "stub"

    def __init__(self) -> None:
        self.submits: list[str] = []

    def submit(self, req):
        self.submits.append(req.client_order_id)
        raise BrokerError(0, "no answer")

    def order_by_client_id(self, coid):
        return None

    def open_orders(self):
        raise BrokerError(429, "rate limit")

    def activities(self, after=None, types=("FILL",)):
        return []

    def positions(self):
        return {}


def test_without_the_open_orders_a_lost_answer_never_becomes_a_new_attempt():
    t = {"now": datetime(2026, 8, 4, 19, 50, tzinfo=UTC)}
    venue = _Venue()
    om = OrderManager(venue, Journal(), lambda: t["now"], run_id=7)
    coid = om.next_coid(7, "trend_momentum", "B", "2026-08-04", "sell")
    assert coid == client_order_id(7, "trend_momentum", "B", "2026-08-04", "sell", 1)
    r = om.submit(OrderRequest("B", "sell", 10, "market", "cls", coid), strategy="trend_momentum", purpose="sell",
                  session="2026-08-04")
    assert r.state is St.UNKNOWN and venue.submits == [coid]
    for _ in range(6):  # well past the lookups and the age that would call it lost
        t["now"] += timedelta(seconds=max(LOOKUP_GAP_S, LOST_AFTER_S) + 1)
        om.resolve()
    r = om.get(coid)
    assert r.state is St.UNKNOWN and "not lost" in str(r.reason) and "could not be read" in str(r.reason)
    # the same decision gets the same id; the same id is the same record and nothing is sent
    assert om.next_coid(7, "trend_momentum", "B", "2026-08-04", "sell") == coid
    again = om.submit(OrderRequest("B", "sell", 10, "market", "cls", coid), strategy="trend_momentum",
                      purpose="sell", session="2026-08-04")
    assert again is r and venue.submits == [coid]
    # the target loop does not send another order in the symbol while it is unconfirmed
    res = reconcile_targets(om, ExecutionPolicy("near_close"), [Target("trend_momentum", "B", 0, "sell")],
                            {("trend_momentum", "B"): 10.0}, now=t["now"], session="2026-08-04", run=7)
    assert not res.sent and venue.submits == [coid]  # it counts as working: nothing more to sell


def test_the_venue_refuses_a_reused_client_order_id():
    """The simulator, as Alpaca (422, the paper adapter then returns the order there): one order per client id."""
    w = World(quiet({}))
    try:
        w.clock.set(world.et(D1, "15:50"))
        coid = client_order_id(7, "trend_momentum", "B", D1, "buy", 1)
        a = w.sim.submit(OrderRequest("B", "buy", 1, "market", "day", coid))
        b = w.sim.submit(OrderRequest("B", "buy", 1, "market", "day", coid))
        assert a["id"] == b["id"] and len([o for o in w.sim.orders.values() if o["client_order_id"] == coid]) == 1
    finally:
        import shutil

        shutil.rmtree(w.root, ignore_errors=True)


def test_the_gate_holds_buys_only_while_the_open_orders_are_unreadable():
    class Acc:
        def entry_block(self):
            return None

        def allows(self, symbol, side, purpose, qty):
            return None

    g = ld._Gate(Acc())
    g.open_unread = True
    assert g.allows("C", "buy") == ld.OPEN_UNREAD
    assert g.allows("B", "sell") is None
    g.open_unread = False
    assert g.allows("C", "buy") is None
