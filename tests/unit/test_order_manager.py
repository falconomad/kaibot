"""Issue #118 (S78): the order manager's state machine, its journal and reconciliation, against `SimBroker` scripts:
partial fill, reject, timeout then late fill, duplicate events, the auction window, replace and cancel, a
replaced bracket leg in both shapes Alpaca may list it in (issue #11), and the outage drills (process killed
mid-order, network cut) with zero duplicate orders at the venue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.broker.sim import NY, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.journal import Journal
from kaibot.engine.orders import (
    COID_MAX,
    OM_KINDS,
    OrderBlocked,
    OrderManager,
    St,
    client_order_id,
)
from kaibot.engine.reconcile import Reconciler, compare
from kaibot.replay.history import BarHistory

DAY, NEXT = "2026-09-15", "2026-09-16"


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def bar(m: int, day: str = DAY) -> datetime:
    return et(day, "09:30") + timedelta(minutes=m)


def flat(day: str, px: float = 100.0, edits: dict | None = None) -> pd.DataFrame:
    ts = pd.Timestamp(f"{day} 09:30", tz=NY).tz_convert(UTC) + pd.to_timedelta(np.arange(390), unit="m")
    f = pd.DataFrame({"ts": ts, "o": px, "h": px + 0.05, "l": px - 0.05, "c": px, "v": 10_000.0, "vw": px})
    for m, kv in (edits or {}).items():
        for k, v in kv.items():
            f.loc[m, k] = v
    return f


def venue(edits: dict | None = None, **kw) -> tuple[SimBroker, SimClock]:
    clock = SimClock(et(DAY, "10:00", 7))
    frames = {s: pd.concat([flat(DAY, edits=edits if s == "A" else None), flat(NEXT)], ignore_index=True)
              for s in ("A", "B")}
    kw.setdefault("costs", None)
    return SimBroker(BarHistory(frames), clock, **kw), clock


def run(b: SimBroker, clock: SimClock, m0: int, m1: int, day: str = DAY) -> None:
    for m in range(m0, m1 + 1):
        b.advance(bar(m, day))
        clock.set(max(clock(), bar(m, day) + timedelta(minutes=1, seconds=7)))


def manager(b, clock, journal=None, **kw) -> tuple[OrderManager, Journal, list]:
    journal = journal or Journal(now=clock)
    booked: list = []
    om = OrderManager(b, journal, clock, on_fill=lambda f, r: booked.append(f), **kw)
    return om, journal, booked


def mkt(symbol="A", side="buy", qty=100, purpose="buy", attempt=1, tif="day", **kw) -> OrderRequest:
    return OrderRequest(symbol, side, qty, "market", tif, client_order_id("1", "rule", symbol, DAY, purpose, attempt),
                        **kw)


# ---------------------------------------------------------------- ids and the journal


def test_client_order_ids_are_deterministic_short_and_per_attempt():
    a = client_order_id(7, "trend_momentum", "BRK.B", "2026-09-15", "buy")
    assert a == client_order_id(7, "trend_momentum", "BRK.B", "2026-09-15", "buy") == "kb7-trend_momentum-BRK.B-" \
        "20260915-buy-1"
    assert client_order_id(7, "x" * 300, "A", DAY, "buy", 2).endswith("-2")
    assert len(client_order_id(7, "x" * 300, "Y" * 50, DAY, "p" * 50, 99)) <= COID_MAX


def test_the_intent_is_journalled_before_the_venue_is_called():
    b, clock = venue()
    journal = Journal(now=clock)
    seen = []
    real = b.submit

    def spy(req):
        seen.append([k for _, _, k, _ in journal.entries(kinds=OM_KINDS)])
        return real(req)

    b.submit = spy
    om, _, _ = manager(b, clock, journal)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    assert seen == [["om_intent"]] and r.state is St.ACCEPTED and r.order_id


def test_the_same_decision_twice_is_one_order_at_the_venue():
    b, clock = venue()
    om, _, _ = manager(b, clock)
    r1 = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    r2 = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    assert r1 is r2 and len(b.orders) == 1 and b.calls.count(f"submit {r1.coid}") == 1
    assert om.next_coid("1", "rule", "A", DAY, "buy") == r1.coid  # an open order of the decision is reused


def test_a_restart_folds_the_journal_back_to_the_same_book():
    b, clock = venue()
    b.script.fills("A", [30, 20])
    om, journal, _ = manager(b, clock)
    om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 32)
    om.sync()
    again = OrderManager(b, journal, clock)
    assert again.snapshot() == om.snapshot() and again.seen == om.seen


# ---------------------------------------------------------------- SimBroker scripts


def test_a_partial_fill_is_booked_at_the_filled_quantity_and_kept_when_the_rest_ends():
    b, clock = venue()
    b.script.fills("A", [30, 20])
    om, _, booked = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 30)
    om.sync()
    assert r.state is St.PARTIAL and r.booked == 30 and [f.qty for f in booked] == [30]
    run(b, clock, 31, 40)
    om.sync()
    assert r.booked == 50 and r.left == 50 and [f.qty for f in booked] == [30, 20]
    clock.set(et(DAY, "16:01"))
    om.sync()  # the day order expires at the close: the 50 shares stay booked
    assert r.state is St.EXPIRED and r.booked == 50 and r.left == 0 and b.pos["A"]["qty"] == 50


def test_a_reject_ends_that_order_only():
    b, clock = venue()
    b.script.reject("A", status=403, message='{"message":"insufficient buying power"}')
    om, _, _ = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    other = om.submit(mkt("B"), strategy="rule", purpose="buy", session=DAY)
    assert r.state is St.REJECTED and "buying power" in r.reason and other.state is St.ACCEPTED
    assert om.blocked("A") is None  # a refusal is an answer: nothing is unknown
    nxt = om.next_coid("1", "rule", "A", DAY, "buy")
    assert nxt.endswith("-2") and om.submit(mkt(attempt=2), strategy="rule", purpose="buy",
                                            session=DAY).state is St.ACCEPTED


def test_a_timeout_then_a_late_fill_blocks_the_symbol_until_the_lookup_and_sends_nothing_twice():
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    b.script.hold("A", bars=2)
    om, _, booked = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    assert r.state is St.UNKNOWN and om.blocked("A") and om.blocked("B") is None
    with pytest.raises(OrderBlocked):
        om.submit(mkt(attempt=2), strategy="rule", purpose="buy", session=DAY)
    assert om.submit(mkt(), strategy="rule", purpose="buy", session=DAY) is r  # same id: not re-sent
    om.sync()  # the lookup by client order id finds it
    assert r.state is St.ACCEPTED and om.blocked("A") is None
    run(b, clock, 30, 33)
    om.sync()
    assert r.state is St.FILLED and r.booked == 100 and len(booked) == 1
    assert len(b.orders) == 1 and sum(c.startswith("submit") for c in b.calls) == 1


def test_an_order_that_never_arrived_is_lost_only_after_two_lookups_and_a_minute():
    b, clock = venue()
    b.script.timeout("A", accepted=False)
    om, _, _ = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    om.resolve()
    om.resolve()
    assert r.state is St.UNKNOWN and r.misses == 2  # not yet a minute old
    clock.advance(seconds=61)
    om.resolve()
    assert r.state is St.REJECTED and r.reason.startswith("lost") and om.blocked("A") is None
    coid = om.next_coid("1", "rule", "A", DAY, "buy")
    assert coid.endswith("-2") and not b.orders


def test_a_duplicate_fill_event_is_booked_once():
    b, clock = venue()
    b.script.duplicate("A")
    om, _, booked = manager(b, clock)
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    run(b, clock, 30, 31)
    assert len(b.activities()) == 2 and b.activities()[0]["id"] == b.activities()[1]["id"]
    om.sync()
    om.sync()
    assert len(booked) == 1 and r.booked == 100 and not om.foreign


def test_a_closing_auction_order_after_1550_is_rejected_by_the_venue():
    b, clock = venue()
    clock.set(et(DAY, "15:51"))
    om, _, _ = manager(b, clock)
    r = om.submit(mkt(tif="cls"), strategy="rule", purpose="buy", session=DAY)
    assert r.state is St.REJECTED and "cls orders" in r.reason
    clock.set(et(DAY, "19:00"))  # queued from 19:00 for the next session's close: accepted
    ok = om.submit(mkt(tif="cls", attempt=2), strategy="rule", purpose="buy", session=DAY)
    assert ok.state is St.ACCEPTED


def test_cancel_and_replace_are_state_transitions_and_follow_a_new_order_id():
    b, clock = venue(replace_new_id=True)
    om, _, _ = manager(b, clock)
    req = OrderRequest("A", "buy", 10, "limit", "day", client_order_id("1", "rule", "A", DAY, "buy"), limit_price=90)
    r = om.submit(req, strategy="rule", purpose="buy", session=DAY)
    first = r.order_id
    om.replace(r.coid, limit=95.0)
    assert r.order_id != first and first in r.ids and r.limit == 95.0 and r.state is St.ACCEPTED
    assert b.orders[first]["status"] == "replaced"
    om.cancel(r.coid)
    assert r.state is St.CANCELED and b.orders[r.order_id]["status"] == "canceled"
    again = OrderManager(b, om.journal, clock)
    assert again.get(r.coid).limit == 95.0 and again.get(r.coid).state is St.CANCELED


def test_a_cancel_the_venue_has_not_confirmed_stays_pending():
    b, clock = venue()
    om, _, _ = manager(b, clock)
    req = OrderRequest("A", "buy", 10, "limit", "day", client_order_id("1", "rule", "A", DAY, "buy"), limit_price=90)
    r = om.submit(req, strategy="rule", purpose="buy", session=DAY)
    b.cancel = lambda oid: None  # accepted (204), not done yet
    om.cancel(r.coid)
    assert r.state is St.PENDING_CANCEL
    b.orders[r.order_id]["status"] = "canceled"
    om.sync()
    assert r.state is St.CANCELED


@pytest.mark.parametrize("shape", ["nested", "detached"])
def test_a_fill_on_a_replaced_stop_leg_is_found_whichever_way_alpaca_lists_it(shape):
    """Issue #11: does a replaced stop leg appear under its parent? Alpaca's documentation does not say and no
    recorded paper response exists yet. The manager books fills from the account activities by every id an
    order ever had, so it finds the stop's fill in both shapes; the old engine looked only at the parent's legs,
    which in the detached shape never shows the fill (audit D11)."""
    b, clock = venue(edits={m: {"l": 97.0} for m in range(40, 45)}, replace_new_id=True, replaced_leg=shape)
    om, _, booked = manager(b, clock)
    req = OrderRequest("A", "buy", 10, "market", "gtc", client_order_id("1", "dip", "A", DAY, "entry"),
                       order_class="bracket", take_profit=110.0, stop_loss=95.0)
    r = om.submit(req, strategy="dip", purpose="entry", session=DAY)
    run(b, clock, 30, 31)
    om.sync()
    leg = next(x["id"] for x in b.order(r.order_id)["legs"] if x["type"] == "stop")
    new = om.replace_leg(r.coid, leg, stop=98.0)
    assert new and new != leg and new in r.ids
    run(b, clock, 32, 41)
    om.sync()
    assert [(f.side, f.qty, f.px) for f in booked] == [("buy", 10, 100.0), ("sell", 10, 98.0)]
    assert "A" not in b.pos and not om.foreign
    parent_legs = b.order(r.order_id)["legs"]
    assert any(x["status"] == "filled" for x in parent_legs) is (shape == "nested")


# ---------------------------------------------------------------- outage drills: zero duplicate orders


class Killed(BaseException):
    """The process dies (not an Exception: nothing in the manager catches it)."""


def test_drill_process_killed_after_the_intent_before_the_venue_call():
    b, clock = venue()
    journal = Journal(now=clock)
    real = b.submit
    b.submit = lambda req: (_ for _ in ()).throw(Killed())
    om, _, _ = manager(b, clock, journal)
    with pytest.raises(Killed):
        om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    b.submit = real
    om2, _, _ = manager(b, clock, journal)  # restart from the journal
    om2.recover()
    assert om2.blocked("A") and not b.orders  # the intent is unresolved: nothing new in A
    clock.advance(seconds=61)
    om2.recover()
    r = om2.get(client_order_id("1", "rule", "A", DAY, "buy"))
    assert r.state is St.REJECTED and r.reason.startswith("lost")
    coid = om2.next_coid("1", "rule", "A", DAY, "buy")
    om2.submit(OrderRequest("A", "buy", 100, "market", "day", coid), strategy="rule", purpose="buy", session=DAY)
    assert len(b.orders) == 1


def test_drill_process_killed_after_the_venue_accepted_before_the_answer_was_written():
    b, clock = venue()
    journal = Journal(now=clock)
    real = b.submit

    def accept_then_die(req):
        real(req)
        raise Killed()

    b.submit = accept_then_die
    om, _, _ = manager(b, clock, journal)
    with pytest.raises(Killed):
        om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    b.submit = real
    om2, _, booked = manager(b, clock, journal)
    om2.recover()
    r = om2.get(client_order_id("1", "rule", "A", DAY, "buy"))
    assert r.state is St.ACCEPTED and om2.blocked("A") is None
    assert om2.submit(mkt(), strategy="rule", purpose="buy", session=DAY) is r
    run(b, clock, 30, 31)
    om2.sync()
    assert len(b.orders) == 1 and r.booked == 100 and len(booked) == 1


def test_drill_network_cut_mid_order_and_through_the_sync():
    b, clock = venue()
    om, _, booked = manager(b, clock)
    real = b.submit

    def cut_after_send(req):
        real(req)
        b.down = True  # the answer is lost on the way back
        raise BrokerError(0, "connection reset")

    b.submit = cut_after_send
    r = om.submit(mkt(), strategy="rule", purpose="buy", session=DAY)
    b.submit = real
    assert r.state is St.UNKNOWN
    run(b, clock, 30, 31)  # the venue fills it while we cannot see
    om.sync()  # every read fails: nothing changes, nothing is sent, the errors are kept
    assert r.state is St.UNKNOWN and om.errors and om.blocked("A")
    b.down = False
    om.sync()
    om.sync()
    assert r.state is St.FILLED and r.booked == 100 and len(booked) == 1 and len(b.orders) == 1


# ---------------------------------------------------------------- reconciliation


def test_reconciliation_finds_quantity_foreign_fill_unbooked_and_cash_differences():
    b, clock = venue()
    om, _, _ = manager(b, clock)
    b.submit(OrderRequest("A", "buy", 5, "market", "day", "someone-else"))  # not through the manager
    run(b, clock, 30, 31)
    om.sync()
    rec = compare({"A": 0.0}, b.positions(), om=om, ledger_cash=100_000.0, ledger_cash0=100_000.0,
                  broker_cash=float(b.cash), broker_cash0=100_000.0, equity=100_000.0)
    assert {d.kind for d in rec.diffs} == {"quantity", "foreign_fill", "cash"}
    assert compare({"A": 5.0}, b.positions(), ledger_cash=b.cash, ledger_cash0=1, broker_cash=b.cash,
                   broker_cash0=1).ok


def test_the_reconciler_refuses_trades_on_differences_but_never_protective_stops():
    state = {"ledger": {"A": 10.0}, "broker": {"A": {"qty": "12"}}}
    rc = Reconciler(lambda: {"ledger_qty": state["ledger"], "broker_positions": state["broker"]})
    assert rc.allows("A", "buy") == "not reconciled yet"
    rc.check("startup")
    assert "quantity" in rc.allows("A", "sell") and rc.allows("B", "buy") is None
    assert rc.allows("A", "sell", purpose="stop") is None
    state["ledger"]["A"] = 12.0  # the late activity arrived
    rc.check()
    assert rc.allows("A", "buy") is None
    cash = Reconciler(lambda: {"ledger_qty": {}, "broker_positions": {}, "ledger_cash": 90.0, "ledger_cash0": 100.0,
                               "broker_cash": 100.0, "broker_cash0": 100.0, "receivable": 0.0})
    cash.check()
    assert "cash" in cash.allows("B", "buy") and cash.allows("B", "sell") is None


def test_the_paper_adapter_sends_qty_only_when_a_replace_asks_for_it():
    from kaibot.broker.alpaca import AlpacaPaper

    a = AlpacaPaper("k", "s")
    sent = []
    a._t = lambda method, path, params=None, body=None: sent.append((method, path, body)) or {"id": "n1"}
    a.replace_stop("o1s", 98.5)
    a.replace("o2", stop_price=90.0, qty=7)
    assert sent == [("PATCH", "/v2/orders/o1s", {"stop_price": "98.50"}),
                    ("PATCH", "/v2/orders/o2", {"qty": "7", "stop_price": "90.00"})]
