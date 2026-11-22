"""M9 ticket 1.8 (issue #433): the multi-day flatten sales go through the order manager.

The audit's findings (M6 confirmed, M7's double-send sequence suspected), each reproduced here before the change on
main @ 453bb87:
- M6: `_sync_md_exits` booked only a completely filled sale; a sale cancelled after a partial fill was forgotten
  with its filled shares unbooked (`test_a_partial_fill_then_a_cancel_books_the_filled_shares` failed: the ledger
  kept 40 shares where the venue held 25, a reconciliation difference);
- M7: the sale's client id carried a timestamp and the call bypassed the manager; a call that raised after the
  venue accepted the order left no record, and the next flatten sent a second sale under a new id
  (`test_a_sale_whose_answer_was_lost_is_one_sale` failed: two market sales reached the venue; the second was not
  refused there because the fake venue, like Alpaca for a market order, accepted it).

Now: one sale per sleeve through `OrderManager.submit` under `kb<run>-<rule>-<symbol>-<day>-flatten-<n>`; the
manager books every fill from the account activities at its own quantity and price.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

from test_failsafe_drills import DAY2, MD, QTY
from test_live_daily_engine import DailyAccount

from kaibot.broker.protocol import BrokerError
from kaibot.engine.orders import St


def _held(tmp_path, monkeypatch) -> DailyAccount:
    """The engine WITH the daily path (as live: KAIBOT_MULTIDAY=on) and no daily view, so the rule decides
    nothing; the sleeve is booked as in the fail-safe drills."""
    a = DailyAccount(tmp_path, monkeypatch, view=False)
    a.hold_b()
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 5)
    return a


def _sales(a):
    return [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"
            and o["type"] == "market"]


def _sold(a):
    return [b for *_, b in a.eng.journal.entries(kinds=("fill",)) if b.get("symbol") == "B" and b["side"] == "sell"]


def _flatten(a):
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())


def _coid(a, n=1):
    return f"kb{a.rid}-{MD}-B-{DAY2.replace('-', '')}-flatten-{n}"


def test_the_sale_goes_through_the_order_manager_under_a_deterministic_id(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    seen = []
    real = a.broker.submit

    def submit(order):
        seen.append([b["coid"] for *_, b in a.eng.journal.entries(kinds=("om_intent",))])
        return real(order)

    a.broker.submit = submit
    _flatten(a)
    (o,) = _sales(a)
    assert o["client_order_id"] == _coid(a) and _coid(a) in seen[-1]  # the manager's intent, before the venue call
    r = a.eng.daily.om.get(_coid(a))
    assert r is not None and (r.strategy, r.purpose, r.side, r.qty) == (MD, "flatten", "sell", QTY)
    _flatten(a)  # a second flatten while the sale works: nothing new, and the working sale is not cancelled
    assert len(_sales(a)) == 1 and _sales(a)[0]["status"] != "canceled"
    a.ticks(a.open2, 5, 8)
    (f,) = _sold(a)
    assert f["qty"] == QTY and f["coid"] == _coid(a) and f["purpose"] == "flatten"
    assert not a.eng._md_sleeves() and a.eng._md_exits == {} and a.eng.recon.check("test").diffs == []
    assert any(e["type"] == "multi_day_sold" and e["payload"]["reason"] == "book_drawdown"
               and e["payload"]["keys"] == [f"{MD}|B"] for e in a.store.tables["event"])
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["coid"] == _coid(a) and done["state"] == "booked"


def test_a_partial_fill_then_a_cancel_books_the_filled_shares(tmp_path, monkeypatch):
    """The ticket's first check (audit M6)."""
    a = _held(tmp_path, monkeypatch)
    a.broker.script.fills("B", [15.0])  # 15 of 40 trade, the rest never does
    _flatten(a)
    (o,) = _sales(a)
    a.ticks(a.open2, 5, 7)
    assert float(a.broker.orders[o["id"]]["filled_qty"]) == 15
    a.broker.cancel(o["id"])  # the venue cancels the rest
    a.ticks(a.open2, 7, 9)
    assert [f["qty"] for f in _sold(a)] == [15.0]
    b = a.eng.ledger.book(a.rid)
    assert b.sleeves[(MD, "B")].qty == QTY - 15 == float(a.broker.pos["B"]["qty"])
    assert a.eng.recon.check("test").diffs == [] and a.eng._md_exits == {}
    (done,) = [x for *_, x in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["state"] == "canceled" and done["filled"] == 15
    _flatten(a)  # the next flatten sells what is left, under the next attempt number
    a.ticks(a.open2, 9, 12)
    assert sorted(f["qty"] for f in _sold(a)) == [15.0, QTY - 15.0]
    assert {o["client_order_id"] for o in _sales(a)} == {_coid(a, 1), _coid(a, 2)}
    assert not a.eng._md_sleeves() and "B" not in a.broker.pos and a.eng.recon.check("test").diffs == []


def test_a_kill_between_the_submit_and_its_record_is_one_sale(tmp_path, monkeypatch):
    """The ticket's second check: the venue has the order, the process dies before it hears back."""
    a = _held(tmp_path, monkeypatch)
    real = a.broker.submit

    def submit_then_die(order):
        real(order)
        raise SystemExit("killed")

    a.broker.submit = submit_then_die
    with contextlib.suppress(SystemExit):
        _flatten(a)
    a.broker.submit = real
    assert len(_sales(a)) == 1
    a.restart()
    _flatten(a)  # the restarted engine flattens again at once
    assert len(_sales(a)) == 1 and _sales(a)[0]["client_order_id"] == _coid(a)
    a.ticks(a.open2, 5, 8)
    _flatten(a)
    assert len(_sales(a)) == 1 and [f["qty"] for f in _sold(a)] == [QTY]
    assert not a.eng._md_sleeves() and a.eng.recon.check("test").diffs == []
    a.restart()
    a.ticks(a.open2, 8, 10)
    assert len(_sold(a)) == 1 and a.eng._md_exits == {}


def test_a_sale_whose_answer_was_lost_is_one_sale(tmp_path, monkeypatch):
    """Audit M7: the call raises after the venue accepted the order; the same process flattens again."""
    a = _held(tmp_path, monkeypatch)
    real = a.broker.submit

    def lost(order):
        real(order)
        raise BrokerError(0, "no answer")

    a.broker.submit = lost
    _flatten(a)
    a.broker.submit = real
    assert a.eng.daily.om.get(_coid(a)).state is St.UNKNOWN
    _flatten(a)
    assert len(_sales(a)) == 1
    a.ticks(a.open2, 5, 8)  # the lookup by client id finds it; its fill is booked
    assert [f["qty"] for f in _sold(a)] == [QTY] and len(_sales(a)) == 1
    assert not a.eng._md_sleeves() and a.eng.recon.check("test").diffs == []


def test_a_refused_sale_is_reported_as_not_flattened_and_tried_again(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    a.broker.script.reject("B", 403, "insufficient qty available")
    failed: dict = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    assert "B" in failed and "refused" in str(failed["B"]) and not _sales(a)
    a.ticks(a.open2, 5, 6)
    failed = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    assert not failed and [o["client_order_id"] for o in _sales(a)] == [_coid(a, 2)]
    a.ticks(a.open2, 6, 9)
    assert [f["qty"] for f in _sold(a)] == [QTY]


def test_the_daily_paths_cancel_of_working_sales_leaves_the_flatten_sale(tmp_path, monkeypatch):
    """A full flatten first cancels the daily path's working orders of both sides for the day (`md_cancel`); the
    flatten's own sale is not one of them."""
    a = _held(tmp_path, monkeypatch)
    a.broker.script.hold("B", bars=2)
    a.eng.daily.cancel_working(("buy", "sell"), "book_drawdown")
    _flatten(a)
    a.ticks(a.open2, 5, 6)
    (o,) = _sales(a)
    assert o["status"] not in ("canceled", "pending_cancel")
    a.ticks(a.open2, 6, 10)
    assert [f["qty"] for f in _sold(a)] == [QTY]


def test_two_sleeves_of_one_symbol_are_two_sales_each_booked_to_its_own_sleeve(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    px = float(a.eng.market.last_close("B"))
    a.broker.pos["B"]["qty"] = QTY + 10
    a.eng._book("fill", {"run_id": a.rid, "strategy": "mom_12_1_band", "symbol": "B", "side": "buy", "qty": 10,
                         "px": px, "fees": 0.0, "coid": f"kb{a.rid}-mom_12_1_band-B-20260803-target-1"})
    _flatten(a)
    assert sorted(float(o["qty"]) for o in _sales(a)) == [10.0, float(QTY)]
    a.ticks(a.open2, 5, 8)
    assert sorted((f["strategy"], f["qty"]) for f in _sold(a)) == sorted([(MD, float(QTY)), ("mom_12_1_band", 10.0)])
    assert not a.eng._md_sleeves() and "B" not in a.broker.pos and a.eng.recon.check("test").diffs == []


def test_a_sale_in_flight_when_the_run_has_ended_is_booked_after_a_restart(tmp_path, monkeypatch):
    """Flatten and stop, then a restart before the fill: no run, so no manager by itself; the sale's own record
    brings the manager of its run back and the fill is booked once."""
    a = _held(tmp_path, monkeypatch)
    a.broker.script.hold("B", bars=3)
    a.store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k9",
                               "applied_at": None})
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped" and len(_sales(a)) == 1 and not _sold(a)
    a.restart()
    assert a.eng.run is None and list(a.eng._md_exits) == [_coid(a)]
    for k in range(5, 12):  # the venue trades; the engine has no run (reaching this step from an idle tick is
        a.broker.advance(a.open2 + timedelta(minutes=k))  # ticket 1.9, PR #482: here it is called directly)
        a.clock.t = a.open2 + timedelta(minutes=k + 1, seconds=7)
        a.eng._sync_md_exits()
    assert [f["qty"] for f in _sold(a)] == [QTY] and a.eng._md_exits == {} and len(_sales(a)) == 1
    assert _sold(a)[0]["run_id"] == a.rid
