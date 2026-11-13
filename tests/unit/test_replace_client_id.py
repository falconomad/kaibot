"""Issue #197 (S78d; QA-Ops review of #196, item 4): every replace sends a client id the fail-safes recognise.

Alpaca gives a replaced order a NEW client id unless one is sent (PATCH /v2/orders/{id}: "Automatically generated if
not sent"). A re-priced multi-day stop then no longer reads as a multi-day stop to the watchdog and the dead-man,
which recognise one by its client id alone (engine/failsafe.py, migration 0019): a false `unprotected` alarm, the
stop cancelled at the close minus 5 as an intraday sell, and with the book unknown the whole position sold.

The order manager now sends, journalled before the call, a deterministic id of the same family:
- its own `kb<run>-<strategy>-<symbol>-<day>-<purpose>-<n>`: the next attempt number of that decision (counted
  with the intents, so a later new stop never reuses it);
- an adopted fail-safe `kbwd-`/`kbdm-...-<suffix>`: `<root>.r<generation>` (2, 3, ...).
The record keeps every id it had (`client_ids`; `OrderManager.owner`), so the adoption of fail-safe stops never
takes a replaced one for a new stop. The review gaps of #196 are here too: a partly filled `kbwd` stop at restart
and the price of a fail-safe stop that filled while the engine was down."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_adopt_failsafe_stops import MD, K, _Store, failsafe_stop, kinds, resting_stops, setup
from test_order_manager import DAY, et, manager, run, venue

from kaibot.broker.protocol import BrokerError
from kaibot.engine import failsafe
from kaibot.engine.failsafe import COID, FAILSAFE_COID, BookView, SleeveView
from kaibot.engine.journal import Journal
from kaibot.engine.orders import OrderManager, St, client_order_id
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.reconcile import compare
from kaibot.engine.watchdog import Watchdog

OWN = client_order_id("1", MD, "A", DAY, "stop", 1)


def spy_replace(b, om):
    """Record every replace the venue receives, with the client ids the journal held at that moment."""
    sent = []
    real = b.replace

    def replace(order_id, **kw):
        journalled = [e["client_id"] for e in kinds(om, "om_state") if e.get("client_id")]
        sent.append((order_id, kw, journalled))
        return real(order_id, **kw)

    b.replace = replace
    return sent


def own_stop(qty=30, stop=90.0, **kw):
    b, clock, om, ps, booked = setup(qty=qty, **kw)
    ps.ensure({K: float(qty)}, {K: stop}, clock(), DAY)
    (r,) = ps.stops_of(K)
    assert r.coid == OWN
    return b, clock, om, ps, booked


def test_the_paper_adapter_sends_a_client_id_on_replace_only_when_asked():
    from kaibot.broker.alpaca import AlpacaPaper

    a = AlpacaPaper("k", "s")
    sent = []
    a._t = lambda method, path, params=None, body=None: sent.append((method, path, body)) or {"id": "n1"}
    a.replace_stop("o1s", 98.5)  # the intraday engine's call: the same bytes as before
    a.replace("o2", stop_price=90.0, qty=7, client_order_id="kb1-x-A-20260915-stop-2")
    assert sent == [("PATCH", "/v2/orders/o1s", {"stop_price": "98.50"}),
                    ("PATCH", "/v2/orders/o2", {"qty": "7", "stop_price": "90.00",
                                                "client_order_id": "kb1-x-A-20260915-stop-2"})]


def test_a_re_priced_engine_stop_gets_the_next_attempt_id_journalled_before_the_call():
    b, clock, om, ps, _ = own_stop()
    sent = spy_replace(b, om)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    ps.ensure({K: 25.0}, {K: 91.0}, clock(), DAY)
    two, three = OWN[:-1] + "2", OWN[:-1] + "3"
    assert [kw["client_order_id"] for _, kw, _ in sent] == [two, three]
    assert [j for _, _, j in sent] == [[two], [two, three]]  # each journalled before its call
    (s,) = resting_stops(b)
    assert s["client_order_id"] == three and COID.match(three)
    (r,) = ps.stops_of(K)
    assert r.coid == OWN and r.client_ids == [two, three] and om.owner(three) is r and om.owner(two) is r
    # the stop goes, a new one comes: its attempt number is past every id the venue has seen
    ps.ensure({K: 0.0}, {}, clock(), DAY)
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (n,) = ps.stops_of(K)
    assert n.coid == OWN[:-1] + "4"
    ids = [o["client_order_id"] for o in b.orders.values()]
    assert len(ids) == len(set(ids))


def test_the_ids_are_folded_from_the_journal_after_a_restart(tmp_path):
    path = tmp_path / "journal.sqlite"
    b, clock = venue(replace_new_id=True)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    j = Journal(path, now=clock)
    ps = ProtectiveStops(OrderManager(b, j, clock), run="1")
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    j.close()
    om2 = OrderManager(b, Journal(path, now=clock), clock)
    assert om2.owner(OWN[:-1] + "2") is om2.get(OWN) and om2.attempts(OWN[:-2]) == 2
    ps2 = ProtectiveStops(om2, run="1")
    ps2.startup({K: 30.0}, DAY)
    sent = spy_replace(b, om2)
    ps2.ensure({K: 30.0}, {K: 92.0}, clock(), DAY)
    assert [kw["client_order_id"] for _, kw, _ in sent] == [OWN[:-1] + "3"]


@pytest.mark.parametrize("who", ["wd", "dm"])
def test_a_re_priced_fail_safe_stop_keeps_its_family_and_is_never_adopted_twice(tmp_path, who):
    path = tmp_path / "journal.sqlite"
    b, clock = venue(replace_new_id=True)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    j = Journal(path, now=clock)
    om = OrderManager(b, j, clock)
    ps = ProtectiveStops(om, run="1")
    o = failsafe_stop(b, clock, stop=88.5, who=who)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)  # adopted, then re-priced
    (s,) = resting_stops(b)
    assert s["client_order_id"] == o["client_order_id"] + ".r2" and FAILSAFE_COID.match(s["client_order_id"])
    assert failsafe.parse_coid(s["client_order_id"])[:2] == (MD, "A")
    assert ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY) == []  # the replaced stop is not a new fail-safe stop
    ps.ensure({K: 30.0}, {K: 92.0}, clock(), DAY)
    assert resting_stops(b)[0]["client_order_id"] == o["client_order_id"] + ".r3"
    assert len(kinds(om, "stop_adopted")) == 1 and len(ps.stops_of(K)) == 1
    j.close()
    om2 = OrderManager(b, Journal(path, now=clock), clock)
    ps2 = ProtectiveStops(om2, run="1")
    assert ps2.startup({K: 30.0}, DAY) == [] and ps2.ensure({K: 30.0}, {K: 92.0}, clock(), DAY) == []
    assert len(kinds(om2, "stop_adopted")) == 1 and len(resting_stops(b)) == 1


def test_a_replace_whose_answer_was_lost_is_followed_and_not_adopted_again_after_a_restart(tmp_path):
    path = tmp_path / "journal.sqlite"
    b, clock = venue(replace_new_id=True)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    j = Journal(path, now=clock)
    om = OrderManager(b, j, clock)
    ps = ProtectiveStops(om, run="1")
    o = failsafe_stop(b, clock, stop=88.5)
    ps.adopt({K}, DAY)
    real = b.replace

    def crash(order_id, **kw):
        real(order_id, **kw)  # the venue replaced it ...
        raise KeyboardInterrupt  # ... and the engine died before the answer was journalled

    b.replace = crash
    with pytest.raises(KeyboardInterrupt):
        ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    j.close()
    b.replace = real
    om2 = OrderManager(b, Journal(path, now=clock), clock)
    ps2 = ProtectiveStops(om2, run="1")
    assert ps2.startup({K: 30.0}, DAY) == []
    (r,) = ps2.stops_of(K)
    (s,) = resting_stops(b)
    assert r.coid == o["client_order_id"] and r.order_id == s["id"] and r.state is St.ACCEPTED
    assert om2.owner(s["client_order_id"]) is r and r.stop == 91.0
    assert len(kinds(om2, "stop_adopted")) == 1


def test_an_id_the_venue_chose_itself_is_recorded_as_the_orders_too():
    """A venue that ignores the id sent (the sim's own `sim-<id>` stands in for it): the record still knows the id
    the order works under, so the adoption never takes it for somebody else's stop."""
    b, clock, om, ps, _ = setup()
    failsafe_stop(b, clock, stop=88.5)
    ps.adopt({K}, DAY)
    real = b.replace
    b.replace = lambda order_id, client_order_id=None, **kw: real(order_id, **kw)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    (s,) = resting_stops(b)
    (r,) = ps.stops_of(K)
    assert s["client_order_id"].startswith("sim-") and om.owner(s["client_order_id"]) is r


# ---------------------------------------------------------------- the fail-safes see a replaced stop


def replaced(who):
    """The venue after the engine re-priced its multi-day stop (who='own') or an adopted fail-safe one."""
    b, clock, om, ps, _ = setup(qty=50)
    if who == "own":
        ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    else:
        failsafe_stop(b, clock, qty=30, stop=88.5, who=who)
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    (s,) = resting_stops(b)
    assert float(s["qty"]) == 30 and float(s["stop_price"]) == 91.0
    return b, clock, s


WHO = ["own", "wd", "dm"]


@pytest.mark.parametrize("who", WHO)
def test_the_plan_counts_a_replaced_stop_as_multi_day_with_no_alarm_and_never_cancels_it(who):
    b, _, s = replaced(who)
    pos = {"A": {"qty": "50", "current_price": "100"}}
    book = BookView([SleeveView(MD, "A", 30.0, 91.0)], "journal", 1)
    p = failsafe.plan(pos, b.open_orders(), book, flatten=True)
    assert p.multi_day == {"A": 30.0} and p.stops == [] and p.unprotected == {} and p.alarms == []
    assert p.sell == [("A", 20, [])] and p.close == []  # the 20 intraday shares only; the stop stays
    # the book unknown (Supabase down, journal unreadable): the stop alone explains the 30 shares
    p = failsafe.plan(pos, b.open_orders(), None, flatten=True)
    assert p.multi_day == {"A": 30.0} and p.sell == [("A", 20, [])] and p.close == [] and p.unprotected == {}


@pytest.mark.parametrize("who", WHO)
def test_the_watchdog_keeps_a_replaced_stop_and_its_shares_at_the_close_with_the_book_unknown(who):
    b, clock, s = replaced(who)
    clock.set(et(DAY, "15:55", 10))
    wd = Watchdog(b, _Store(), now=clock, book=lambda: BookView([], "none", None))
    out = wd.step()
    assert out["state"] == "engine_stale" and out["stops"] == [] and out["kept"] == {"A": 30.0}
    assert out["closed"] == ["A"]  # the intraday 20 are sold ...
    (left,) = resting_stops(b)
    assert left["id"] == s["id"] and left["status"] == "new"  # ... the multi-day stop is never cancelled
    assert f"cancel {s['id']}" not in b.calls and "close_position" not in " ".join(b.calls)
    sells = [o for o in b.orders.values() if o["side"] == "sell" and o["type"] == "market"]
    assert [float(o["qty"]) for o in sells] == [20]


@pytest.mark.parametrize("who", WHO)
def test_the_watchdog_raises_no_unprotected_alarm_for_a_replaced_stop(who):
    b, clock, _ = replaced(who)
    clock.set(et(DAY, "11:00", 10))
    wd = Watchdog(b, _Store(), now=clock, book=lambda: BookView([SleeveView(MD, "A", 30.0, 91.0)], "journal", 1))
    out = wd.step()
    assert out["stops"] == [] and len(resting_stops(b)) == 1
    assert not [r for r in wd.store.rows if r[:2] == ("event", "watchdog_protect") and r[2]["unprotected"]]


# ---------------------------------------------------------------- reconciliation


@pytest.mark.parametrize("restart", [False, True])
def test_a_replaced_stop_that_fills_is_booked_to_its_sleeve_once_and_reconciles(tmp_path, restart):
    path = tmp_path / "journal.sqlite"
    b, clock = venue(edits={m: {"l": 95.0, "o": 96.0} for m in range(40, 45)}, replace_new_id=True)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    j = Journal(path, now=clock)
    booked = []
    om = OrderManager(b, j, clock, on_fill=lambda f, r: booked.append(f))
    ps = ProtectiveStops(om, run="1")
    ps.ensure({K: 30.0}, {K: 96.0}, clock(), DAY)
    ps.ensure({K: 30.0}, {K: 96.5}, clock(), DAY)  # re-priced: a new order id and a new client id
    if restart:
        j.close()
        om = OrderManager(b, Journal(path, now=clock), clock, on_fill=lambda f, r: booked.append(f))
        ps = ProtectiveStops(om, run="1")
    run(b, clock, 30, 45)
    om.sync()
    om.sync()
    assert [(f.coid, f.strategy, f.side, f.qty, f.purpose, f.px) for f in booked] == [
        (OWN, MD, "sell", 30, "stop", 96.0)]
    assert om.get(OWN).state is St.FILLED and not om.foreign
    assert compare({}, b.positions(), om=om).ok


# ---------------------------------------------------------------- QA-Ops review gaps on #196


@pytest.mark.parametrize("new_id", [True, False])
def test_a_partly_filled_watchdog_stop_at_restart_keeps_its_rest_books_the_part_once_and_adds_no_stop(tmp_path,
                                                                                                       new_id):
    """Review item 1: the watchdog's stop of 30 sold 10 at 95 while the engine was down. At restart the stop is
    adopted with its fill, the 10 are booked once, the 20 left keep resting (never resized to 20 in all, which
    would leave 10 of them bare), and no second stop is placed."""
    b, clock = venue(replace_new_id=new_id)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    o = failsafe_stop(b, clock, stop=95.0)
    root = o["client_order_id"]
    b._fill(o, o, 10, 95.0, clock())  # the venue filled a part
    assert b.orders[o["id"]]["status"] == "partially_filled" and b.pos["A"]["qty"] == 20
    path = tmp_path / "journal.sqlite"
    j = Journal(path, now=clock)
    booked = []
    om = OrderManager(b, j, clock, on_fill=lambda f, r: booked.append(f))
    ps = ProtectiveStops(om, run="1")
    ps.startup({K: 30.0}, DAY)
    (r,) = ps.stops_of(K)
    assert (r.coid, r.state, r.cum, r.booked) == (root, St.PARTIAL, 10.0, 10.0)
    assert [(f.strategy, f.side, f.qty, f.px, f.purpose) for f in booked] == [(MD, "sell", 10, 95.0, "stop")]
    calls = len(b.calls)
    did = ps.ensure({K: 20.0}, {K: 95.0}, clock(), DAY)  # the sleeve after the booked part
    assert did == [] and om.errors == []  # 20 rest for a sleeve of 20: nothing to send
    (s,) = resting_stops(b)
    assert s["id"] == o["id"] and float(s["qty"]) - float(s["filled_qty"]) == 20
    assert not [c for c in b.calls[calls:] if c.startswith("submit")]
    om.sync()
    j.close()  # and again after a restart: nothing booked twice, nothing new sent
    om2 = OrderManager(b, Journal(path, now=clock), clock, on_fill=lambda f, r: booked.append(f))
    ps2 = ProtectiveStops(om2, run="1")
    ps2.startup({K: 20.0}, DAY)
    if not new_id:  # a sale frees down to the shares kept, counted on what rests (the sim models this in place)
        assert ps2.release(K, 15)
        (s,) = resting_stops(b)
        assert float(s["qty"]) - float(s["filled_qty"]) == 15
    assert len(booked) == 1 and len(resting_stops(b)) == 1 and om2.get(root).booked == 10.0


def test_a_fail_safe_stop_that_filled_while_the_engine_was_down_is_booked_at_its_fill_price():
    """Review item 2: the sale is booked at the activity's own price (the stop at 96 fills at the bar's open, 95.5,
    below it: a gap through the stop)."""
    b, clock = venue(edits={m: {"o": 95.5, "l": 95.0} for m in range(35, 40)}, replace_new_id=True)
    om, _, booked = manager(b, clock)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    ps = ProtectiveStops(om, run="1")
    failsafe_stop(b, clock, stop=96.0)
    run(b, clock, 30, 36)
    om.sync()
    ps.adopt({K}, DAY)
    om.sync()
    (f,) = booked
    (a,) = [x for x in b.fills if x["side"] == "sell"]
    assert f.px == float(a["price"]) == 95.5 and (f.qty, f.strategy) == (30, MD)


def test_a_refused_replace_keeps_the_order_and_its_id_and_the_next_one_moves_on():
    b, clock, om, ps, _ = own_stop()
    real = b.replace
    b.replace = lambda *a, **kw: (_ for _ in ()).throw(BrokerError(422, "refused"))
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    (s,) = resting_stops(b)
    assert s["client_order_id"] == OWN and om.get(OWN).state is St.ACCEPTED
    b.replace = real
    clock.set(clock() + timedelta(minutes=1))
    ps.ensure({K: 30.0}, {K: 91.0}, clock(), DAY)
    assert resting_stops(b)[0]["client_order_id"] == OWN[:-1] + "3"  # 2 was journalled, never used: not reused
