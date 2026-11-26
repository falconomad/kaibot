"""Audit 2, 5.13 (#693, #529's watchdog half): a position close the watchdog made is recorded locally
(failsafe.CLOSES_FILE) and its fill is booked against the sleeves it sold (OrderManager.book_failsafe_sale), once,
also after a restart. The end-to-end case (the buy booked a day late, the watchdog's close before it) is
tests/dst/test_dst.py::test_a_watchdog_flatten_of_unbooked_shares_is_booked_against_the_late_buy."""

from __future__ import annotations

import json

from test_order_manager import DAY, manager, run, venue

from kaibot.broker.protocol import OrderRequest
from kaibot.engine import failsafe
from kaibot.engine.watchdog import Watchdog


def _foreign_fills():
    """A buy and the close of it, both on orders the manager does not know (as the watchdog's close is)."""
    b, clock = venue()
    b.submit(OrderRequest("A", "buy", 100, "market", "day"))
    run(b, clock, 30, 32)
    close = b.close_position("A")
    run(b, clock, 33, 35)
    om, journal, _ = manager(b, clock, run_id=1)
    om.read_fills()
    sale = next(aid for aid, a in om.foreign.items() if a["side"] == "sell")
    return b, clock, om, journal, close, sale


def test_the_watchdog_close_record_is_read_back_and_a_torn_line_is_skipped(tmp_path):
    p = tmp_path / failsafe.CLOSES_FILE
    p.write_text(json.dumps({"order_id": "o9", "symbol": "A", "ts": "t"}) + "\n" + '{"order_id": "o1', encoding="utf-8")
    assert failsafe.watchdog_closes(p) == {"o9": {"symbol": "A", "ts": "t"}}
    assert failsafe.watchdog_closes(tmp_path / "none.jsonl") == {} and failsafe.watchdog_closes(None) == {}


def test_the_watchdog_writes_each_close_it_made_next_to_its_cancel_record(tmp_path):
    b, clock = venue()
    dog = Watchdog(b, store=None, now=clock, cancel_log=tmp_path / failsafe.CANCELS_FILE)
    dog._note_close("A", {"id": "o7", "qty": "5"})
    dog._note_close("B", None)  # nothing to sell: no order, no line
    assert failsafe.watchdog_closes(tmp_path / failsafe.CLOSES_FILE) == {
        "o7": {"symbol": "A", "ts": clock().isoformat()}}
    Watchdog(b, store=None, now=clock)._note_close("A", {"id": "o8"})  # no record wired: nothing written, no error


def test_a_watchdog_sale_is_booked_to_the_sleeves_once_and_not_again_after_a_restart():
    b, clock, om, journal, close, sale = _foreign_fills()
    a = om.foreign[sale]
    assert a["order_id"] == close["id"]
    bodies = om.book_failsafe_sale(sale, [("rule", 60.0), ("other", 40.0)], session=DAY, reason="watchdog close")
    assert [(x["strategy"], x["qty"], x["side"], x["purpose"]) for x in bodies] == [
        ("rule", 60.0, "sell", "flatten"), ("other", 40.0, "sell", "flatten")]
    assert all(x["activity"] == sale and x["run_id"] == 1 and x["order_id"] == close["id"] for x in bodies)
    assert sale not in om.foreign
    om.read_fills()
    assert sale not in om.foreign  # read again: seen, not foreign again
    again, _, _ = manager(b, clock, journal=journal, run_id=1)  # a restart folds the same journal
    again.read_fills()
    assert sale not in again.foreign
    assert again.book_failsafe_sale(sale, [("rule", 100.0)], session=DAY, reason="x") == []


def test_a_sale_is_booked_only_whole_and_never_a_buy_or_without_a_run():
    b, clock, om, journal, close, sale = _foreign_fills()
    buy = next(aid for aid, a in om.foreign.items() if a["side"] == "buy")
    assert om.book_failsafe_sale(sale, [("rule", 60.0)], session=DAY, reason="x") == []  # not the whole sale
    assert om.book_failsafe_sale(buy, [("rule", 100.0)], session=DAY, reason="x") == []
    assert sale in om.foreign and buy in om.foreign
    no_run, _, _ = manager(b, clock)
    no_run.read_fills()
    assert no_run.book_failsafe_sale(sale, [("rule", 100.0)], session=DAY, reason="x") == []


def test_the_daily_path_books_a_watchdog_sale_only_once_the_ledger_holds_its_shares():
    """LiveDaily._book_failsafe_closes: a sale on an order the close record names waits while the ledger holds fewer
    shares than it sold (the buy not booked yet: #529), then is booked to the sleeves, smallest rule name first;
    a fill on an order the record does not name is left for the owner."""
    from types import SimpleNamespace

    from kaibot.engine.live_daily import LiveDaily

    b, clock, om, journal, close, sale = _foreign_fills()
    held: list = []
    booked: list = []
    events: list = []
    me = SimpleNamespace(_watchdog_closes=lambda: {close["id"]: {"symbol": "A"}}, now=clock,
                         account=SimpleNamespace(holders=lambda s: list(held), book_fill=booked.append),
                         event=lambda name, body: events.append((name, body)))
    assert LiveDaily._book_failsafe_closes(me, om) == [] and sale in om.foreign  # nothing in the ledger yet
    held[:] = [("rule", 30.0)]
    assert LiveDaily._book_failsafe_closes(me, om) == [] and sale in om.foreign  # fewer shares than sold: waits
    held[:] = [("zeta", 50.0), ("rule", 70.0)]
    assert LiveDaily._book_failsafe_closes(me, om) == [sale] and sale not in om.foreign
    assert [(x["strategy"], x["qty"]) for x in booked] == [("rule", 70.0), ("zeta", 30.0)]
    assert [n for n, _ in events] == ["watchdog_close_booked"] and events[0][1]["qty"] == 100.0
    other = SimpleNamespace(**{**vars(me), "_watchdog_closes": lambda: {"o-else": {"symbol": "A"}}})
    _, _, om2, _, _, sale2 = _foreign_fills()
    assert LiveDaily._book_failsafe_closes(other, om2) == [] and sale2 in om2.foreign
