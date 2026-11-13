"""Issue #188 (S80b): the order manager adopts the protective stops the watchdog (`kbwd-...-wdstop`) and the database
dead-man (`kbdm-...-dmstop`, migration 0019) create while the engine is silent, as the sleeve's own stop.

At start-up and at every `ensure` (each session's reconciliation of the stops), a resting fail-safe stop of a known
sleeve is journalled into the order manager (`om_intent` + `om_state`, plus `stop_adopted`): never cancelled and
re-armed (#179), never doubled, re-priced and resized through `replace` (a resize up never above the venue's position
less the symbol's other stops: #187, #192), and `release` frees its shares for a sale. A fail-safe stop that filled
while the engine was down is adopted from its fill, so the sale is booked to the sleeve instead of staying a foreign
fill. The drill at the end: the engine is down, the watchdog re-creates the missing stop, the engine restarts, one
stop rests per sleeve, then a sale and a resize go through."""

from __future__ import annotations

from datetime import timedelta

from test_order_manager import DAY, NEXT, et, manager, run, venue

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.failsafe import BookView, SleeveView
from kaibot.engine.journal import Journal
from kaibot.engine.orders import OrderManager, St
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.reconcile import compare
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.engine.watchdog import Watchdog, failsafe_coid
from kaibot.strategies import registry

MD = "trend_momentum"
K = (MD, "A")


def setup(qty=30, journal=None, **kw):
    b, clock = venue(replace_new_id=True, **kw)
    om, journal, booked = manager(b, clock, journal)
    b.pos["A"] = {"qty": qty, "avg_entry_price": 100.0}
    return b, clock, om, ProtectiveStops(om, run="1"), booked


def failsafe_stop(b, clock, qty=30, stop=90.0, who="wd", strategy=MD, symbol="A"):
    """A stop the watchdog (or the dead-man) sent straight to the venue, behind the engine's back."""
    coid = failsafe_coid(strategy, symbol, clock())
    if who == "dm":
        coid = "kbdm-" + coid[len("kbwd-"):].replace("-wdstop", "-dmstop")
    return b.submit(OrderRequest(symbol, "sell", qty, "stop", "gtc", coid, stop_price=stop))


def resting_stops(b, symbol="A"):
    return [o for o in b.open_orders() if o["symbol"] == symbol and o["type"] == "stop"]


def kinds(om, kind):
    return [body for _, _, k, body in om.journal.entries(kinds=(kind,))]


def test_a_resting_watchdog_stop_is_adopted_as_the_sleeves_own_and_no_second_stop_is_sent():
    b, clock, om, ps, _ = setup()
    o = failsafe_stop(b, clock)
    did = ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(K)
    assert r.coid == o["client_order_id"] and r.order_id == o["id"] and r.purpose == "stop"
    assert (r.strategy, r.symbol, int(r.qty), r.stop, r.tif) == (MD, "A", 30, 90.0, "gtc")
    assert did == [f"adopt {r.coid}"]
    assert [x["client_order_id"] for x in resting_stops(b)] == [o["client_order_id"]]  # no duplicate
    (a,) = kinds(om, "stop_adopted")
    assert (a["coid"], a["by"], a["strategy"], a["symbol"]) == (r.coid, "watchdog", MD, "A")
    assert ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY) == []  # adopted once; nothing more to do
    assert len(kinds(om, "stop_adopted")) == 1


def test_a_dead_man_stop_is_adopted_the_same_way():
    b, clock, om, ps, _ = setup()
    o = failsafe_stop(b, clock, who="dm")
    assert o["client_order_id"].startswith("kbdm-") and o["client_order_id"].endswith("-dmstop")
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(K)
    assert r.coid == o["client_order_id"] and kinds(om, "stop_adopted")[0]["by"] == "deadman"
    assert len(resting_stops(b)) == 1


def test_an_adopted_stop_survives_a_restart_from_the_journal_and_is_not_adopted_twice(tmp_path):
    j = Journal(tmp_path / "journal.sqlite")
    b, clock, om, ps, _ = setup(journal=j)
    o = failsafe_stop(b, clock)
    ps.adopt({K}, DAY)
    j.close()
    j2 = Journal(tmp_path / "journal.sqlite", now=clock)
    om2 = OrderManager(b, j2, clock)
    om2.recover()
    ps2 = ProtectiveStops(om2, run="1")
    assert [r.coid for r in ps2.stops_of(K)] == [o["client_order_id"]]  # folded from the journal
    assert ps2.ensure({K: 30.0}, {K: 90.0}, clock(), DAY) == []
    assert len(kinds(om2, "stop_adopted")) == 1 and len(resting_stops(b)) == 1


def test_ensure_re_prices_and_resizes_an_adopted_stop_through_replace_never_cancelling_it():
    b, clock, om, ps, _ = setup()
    failsafe_stop(b, clock, stop=88.5)  # the watchdog's price (an emergency one, say)
    calls = []
    real_cancel = b.cancel
    b.cancel = lambda oid: calls.append(oid) or real_cancel(oid)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)  # the engine's own stop price moves the frozen one
    (r,) = ps.stops_of(K)
    assert r.stop == 91.0 and int(r.qty) == 30 and not calls
    ps.ensure({K: 20.0}, {K: 91.0}, clock(), DAY)  # a downsize
    assert int(ps.stops_of(K)[0].qty) == 20
    b.pos["A"]["qty"] = 35  # the sleeve says 40, the venue holds 35: the resize up stops at the venue
    ps.ensure({K: 40.0}, {K: 91.0}, clock(), DAY)
    assert int(ps.stops_of(K)[0].qty) == 35 and not calls
    (s,) = resting_stops(b)
    assert float(s["qty"]) == 35 and float(s["stop_price"]) == 91.0


def test_release_frees_an_adopted_stops_shares_for_a_sale():
    b, clock, om, ps, _ = setup()
    failsafe_stop(b, clock)
    ps.adopt({K}, DAY)
    res = reconcile_targets(om, ExecutionPolicy("close"), [Target(MD, "A", 10, "sell")], {K: 30.0}, now=clock(),
                            session=DAY, stops=ps)
    assert [(r.side, r.qty, r.state) for r in res.sent] == [("sell", 20, St.ACCEPTED)]
    assert int(ps.stops_of(K)[0].qty) == 10


def test_release_adopts_first_so_a_sale_is_never_sent_against_a_fail_safe_stop_it_does_not_know():
    b, clock, om, ps, _ = setup()
    failsafe_stop(b, clock)  # not adopted yet: the sale's release must find it
    res = reconcile_targets(om, ExecutionPolicy("close"), [Target(MD, "A", 0, "sell")], {K: 30.0}, now=clock(),
                            session=DAY, stops=ps)
    (a,) = kinds(om, "stop_adopted")  # adopted, then cancelled (it held the shares), then the sale
    assert om.get(a["coid"]).state is St.CANCELED and not resting_stops(b)
    assert [(r.side, r.qty, r.state) for r in res.sent] == [("sell", 30, St.ACCEPTED)]


def test_with_an_engine_stop_and_a_fail_safe_stop_on_one_sleeve_one_stop_remains_never_above_the_position():
    b, clock, om, ps, _ = setup(qty=40)
    ps.ensure({K: 20.0}, {K: 90.0}, clock(), DAY)  # the engine's own stop on the 20 shares it had booked
    failsafe_stop(b, clock, qty=20)  # the watchdog covered the other 20 (the book said 40)
    ps.ensure({K: 40.0}, {K: 90.0}, clock(), DAY)
    ps.ensure({K: 40.0}, {K: 90.0}, clock(), DAY)  # the duplicate's cancel is confirmed: the kept one grows
    stops = resting_stops(b)
    assert len(stops) == 1 and float(stops[0]["qty"]) == 40
    assert len(ps.stops_of(K)) == 1


def test_a_resize_up_leaves_room_for_another_sleeves_stop_in_the_same_symbol():
    """#192: the stops of one symbol never sum to more than the venue holds."""
    b, clock, om, ps, _ = setup(qty=40)
    other = ("other_rule", "A")
    ps.ensure({other: 15.0}, {other: 85.0}, clock(), DAY)
    failsafe_stop(b, clock, qty=20)
    ps.ensure({K: 30.0, other: 15.0}, {K: 90.0, other: 85.0}, clock(), DAY)
    assert int(ps.stops_of(K)[0].qty) == 25  # 40 held, 15 under the other sleeve's stop
    assert sum(float(o["qty"]) for o in resting_stops(b)) <= 40


def test_a_neighbour_name_is_adopted_under_the_sleeves_own_name_and_neighbours_are_never_live():
    b, clock, om, ps, _ = setup()
    key = ("trend_momentum~reg60", "A")
    o = failsafe_stop(b, clock, strategy=key[0])
    assert "-trend_momentumreg60-" in o["client_order_id"]  # the client id drops the "~"
    ps.ensure({key: 30.0}, {key: 90.0}, clock(), DAY)
    assert [r.coid for r in ps.stops_of(key)] == [o["client_order_id"]] and len(resting_stops(b)) == 1
    assert registry.live([key[0]]) == ()  # a neighbour is a research name: the live engine never loads it


def test_a_fail_safe_stop_of_no_known_sleeve_is_left_resting_and_noted_once():
    b, clock, om, ps, _ = setup()
    o = failsafe_stop(b, clock, strategy="momentum_other")
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    assert o["client_order_id"] not in om.orders and b.order(o["id"])["status"] == "new"
    (s,) = kinds(om, "stop_adopt_skipped")
    assert s["coid"] == o["client_order_id"] and "no sleeve" in s["why"]


def test_open_orders_unreadable_no_new_stop_is_sent():
    b, clock, om, ps, _ = setup()
    failsafe_stop(b, clock)

    def down():
        raise BrokerError(503, "unavailable")

    b.open_orders = down
    did = ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    assert not ps.stops_of(K) and len([o for o in b.orders.values() if o["type"] == "stop"]) == 1
    assert any("open orders unreadable" in d for d in did)


def test_a_fail_safe_stop_that_filled_while_the_engine_was_down_is_booked_to_the_sleeve():
    b, clock = venue(edits={m: {"l": 95.0} for m in range(35, 40)}, replace_new_id=True)
    om, _, booked = manager(b, clock)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    ps = ProtectiveStops(om, run="1")
    failsafe_stop(b, clock, stop=96.0)
    run(b, clock, 30, 36)  # the stop fills while nobody adopted it
    om.sync()
    assert om.foreign and compare({"A": 30.0}, b.positions(), om=om).diffs
    ps.adopt({K}, DAY)
    om.sync()
    assert [(f.strategy, f.side, f.qty, f.purpose) for f in booked] == [(MD, "sell", 30, "stop")]
    assert not om.foreign and not compare({}, b.positions(), om=om).diffs


# ---------------------------------------------------------------- the drill


class _Store:
    """The watchdog's Supabase, silent engine: no heartbeat row; halts and events are kept."""

    def __init__(self):
        self.rows: list = []

    def select(self, table, params):
        return []

    def heartbeat(self, process, detail):
        self.rows.append(("heartbeat", process, detail))

    def event(self, kind, detail, run_id=None):
        self.rows.append(("event", kind, detail))

    def insert(self, table, row, returning=True):
        self.rows.append((table, row))


def test_drill_engine_down_the_watchdog_re_creates_the_stop_the_engine_restarts_with_one_stop_then_sells_and_resizes(
        tmp_path):
    """Engine on DAY: the sleeve holds 30 shares of A with its own GTC stop. The engine is killed. Overnight the stop
    is lost (cancelled by hand at the broker). NEXT 09:31: the watchdog sees the silent engine and re-creates the
    stop (`kbwd-`). NEXT 10:00: the engine restarts on the same journal: one stop rests for the sleeve (the
    watchdog's, adopted), then a sale (30 -> 10) and a resize (back to 25 after a purchase) go through."""
    path = tmp_path / "journal.sqlite"
    b, clock = venue(replace_new_id=True)
    j = Journal(path, now=clock)
    om = OrderManager(b, j, clock)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    ProtectiveStops(om, run="1").ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (own,) = resting_stops(b)
    j.close()  # the engine is killed

    b.cancel(own["id"])  # overnight, the engine's stop is gone
    clock.set(et(NEXT, "09:31", 7))
    wd = Watchdog(b, _Store(), now=clock,
                  book=lambda: BookView([SleeveView(MD, "A", 30.0, 90.0)], "journal", 1))
    out = wd.step()
    assert out["state"] == "engine_stale" and [(s["symbol"], s["qty"], s["stop"]) for s in out["stops"]] == [
        ("A", 30, 90.0)]
    (wdstop,) = resting_stops(b)
    assert wdstop["client_order_id"].startswith("kbwd-")

    clock.set(et(NEXT, "10:00", 7))  # the engine starts again
    j2 = Journal(path, now=clock)
    om2 = OrderManager(b, j2, clock)
    ps2 = ProtectiveStops(om2, run="1")
    ps2.startup({K: 30.0}, NEXT)
    assert om2.get(own["client_order_id"]).state is St.CANCELED
    assert [r.coid for r in ps2.stops_of(K)] == [wdstop["client_order_id"]]
    ps2.ensure({K: 30.0}, {K: 90.0}, clock(), NEXT)
    assert len(resting_stops(b)) == 1  # one stop per sleeve, never two

    # a sale: the adopted stop shrinks to what is kept and the sale goes out
    res = reconcile_targets(om2, ExecutionPolicy("close"), [Target(MD, "A", 10, "sell")], {K: 30.0},
                            now=clock(), session=NEXT, stops=ps2)
    assert [(r.side, r.qty, r.state) for r in res.sent] == [("sell", 20, St.ACCEPTED)]
    (s,) = resting_stops(b)
    assert float(s["qty"]) == 10
    # the sale fills; a later purchase brings the venue to 25: the stop is resized, still one
    b.orders[res.sent[0].order_id].update(status="filled", filled_qty=20)
    b.pos["A"]["qty"] = 25
    ps2.ensure({K: 25.0}, {K: 92.0}, clock() + timedelta(minutes=1), NEXT)
    (s,) = resting_stops(b)
    assert (float(s["qty"]), float(s["stop_price"])) == (25, 92.0)
    assert len(ps2.stops_of(K)) == 1
