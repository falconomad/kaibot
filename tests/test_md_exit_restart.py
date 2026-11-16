"""#252: a multi-day flatten sale still open at a restart is booked exactly once (written before the code). The sale
is journalled when sent (`md_exit`: order id, run, keys, reason) and when booked or dropped (`md_exit_done`); a
restart rebuilds the open ones from the journal. On the simulated venue of the S80 drills."""

from __future__ import annotations

import contextlib

from test_failsafe_drills import MD, QTY, Account


def _fills(a, side="sell"):
    return [b for *_, b in a.eng.journal.entries(kinds=("fill",)) if b.get("symbol") == "B" and b["side"] == side]


def _held(tmp_path, monkeypatch) -> Account:
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    a = Account(tmp_path)
    a.hold_b()
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 5)
    return a


def _flatten(a):
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    return x


def test_a_sale_open_at_a_restart_is_booked_once_after_it(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    x = _flatten(a)
    assert x["keys"] == [[[MD, "B"], QTY]] and x["reason"] == "book_drawdown" and x["run_id"] == a.rid
    assert _fills(a) == []  # sent, not filled yet
    a.restart()  # the engine dies before the fill
    assert x["order_id"] in a.eng._md_exits  # rebuilt from the journal
    a.ticks(a.open2, 5, 8)  # the sale fills at the venue; the new engine books it
    (f,) = _fills(a)
    assert f["qty"] == QTY and f["fees"] > 0 and f["order_id"] == x["order_id"]
    assert (MD, "B") not in a.eng.ledger.book(a.rid).sleeves and not a.eng._md_sleeves()
    assert "B" not in a.broker.pos and a.eng.recon.check("test").diffs == []
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["order_id"] == x["order_id"] and done["state"] == "booked"
    a.restart()  # a second restart books nothing again
    assert a.eng._md_exits == {}
    a.ticks(a.open2, 8, 10)
    assert len(_fills(a)) == 1


def test_a_crash_between_the_booking_and_its_done_entry_books_nothing_twice(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    x = _flatten(a)
    real = a.eng.journal.append

    def dies(kind, body):  # the process dies after the fill entry and before `md_exit_done`
        if kind == "md_exit_done":
            raise SystemExit("killed")
        return real(kind, body)

    a.eng.journal.append = dies
    with contextlib.suppress(SystemExit):
        a.ticks(a.open2, 5, 8)
    assert len(_fills(a)) == 1 and not list(a.eng.journal.entries(kinds=("md_exit_done",)))
    a.restart()
    assert x["order_id"] in a.eng._md_exits
    a.ticks(a.open2, 8, 10)
    assert len(_fills(a)) == 1 and a.eng._md_exits == {}


def test_a_sale_cancelled_while_down_is_dropped_and_tried_again(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    x = _flatten(a)
    a.broker.cancel(x["order_id"])  # cancelled at the venue while the engine is down
    a.restart()
    a.ticks(a.open2, 5, 6)
    assert a.eng._md_exits == {}
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["state"] == "canceled" and _fills(a) == []
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())  # the next flatten sends it again
    assert len([b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]) == 2
    a.ticks(a.open2, 6, 9)
    assert len(_fills(a)) == 1


# ---------------------------------------------------------------- QA-Ops second key (#279)


def test_a_crash_between_two_sleeves_of_one_sale_books_the_second_after_the_restart(tmp_path, monkeypatch):
    """One close_position sale for two sleeves of B: the process dies after the first sleeve's fill entry. The
    restart books the second sleeve, and the first is not booked again (the check is per sleeve, not per order)."""
    a = _held(tmp_path, monkeypatch)
    px = float(a.eng.market.last_close("B"))
    a.broker.pos["B"]["qty"] = QTY + 10
    a.eng._book("fill", {"run_id": a.rid, "strategy": "mom_12_1_band", "symbol": "B", "side": "buy", "qty": 10,
                         "px": px, "fees": 0.0, "coid": f"kb{a.rid}-mom_12_1_band-B-20260803-target-1"})
    x = _flatten(a)
    assert len(x["keys"]) == 2
    real, n = a.eng._book, [0]

    def dies(kind, body):
        if kind == "fill" and body.get("side") == "sell":
            n[0] += 1
            if n[0] == 2:
                raise SystemExit("killed between the two sleeves")
        return real(kind, body)

    a.eng._book = dies
    with contextlib.suppress(SystemExit):
        a.ticks(a.open2, 5, 8)
    assert len(_fills(a)) == 1
    a.restart()
    a.ticks(a.open2, 8, 10)
    got = sorted((f["strategy"], f["qty"]) for f in _fills(a))
    assert got == sorted([(MD, QTY), ("mom_12_1_band", 10)])
    assert not a.eng._md_sleeves() and a.eng.recon.check("test").diffs == []


def test_a_crash_between_the_sleeves_own_sale_and_its_entry_is_found_by_client_id(tmp_path, monkeypatch):
    """The sleeve's own market sale (a symbol an intraday position also holds: `kbfl-` client id). The process dies
    after the venue took the order and before `md_exit` was written. At the restart the sale is found at the venue
    by the client id of its journalled intent, booked once when it fills, and not sent a second time."""
    a = _held(tmp_path, monkeypatch)
    monkeypatch.setattr(a.eng.pos, "on", lambda sym: [object()] if sym == "B" else [])  # B: the submit path

    def dies(*args, **kw):
        raise SystemExit("killed after the broker call")

    monkeypatch.setattr(a.eng, "_md_exit_sent", dies)
    with contextlib.suppress(SystemExit):
        a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())
    sent = [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"
            and str(o.get("client_order_id")).startswith("kbfl-")]
    assert len(sent) == 1 and not list(a.eng.journal.entries(kinds=("md_exit",)))
    a.restart()
    assert sent[0]["id"] in a.eng._md_exits  # rebuilt from the intent and the venue's record
    a.ticks(a.open2, 5, 8)
    (f,) = _fills(a)
    assert f["qty"] == QTY and f["order_id"] == sent[0]["id"]
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())  # nothing left to sell
    assert len([o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"
                and o["type"] == "market"]) == 1
    a.restart()
    assert a.eng._md_exits == {} and len(_fills(a)) == 1


def test_a_crash_before_the_entry_of_a_close_position_shows_in_the_reconciliation(tmp_path, monkeypatch):
    """close_position has no client id: if the process dies between the broker call and `md_exit`, the sale cannot
    be rebuilt. It is not booked silently either: once it fills, the reconciliation reports B (ledger 40, venue 0)
    and entries are blocked."""
    a = _held(tmp_path, monkeypatch)

    def dies(*args, **kw):
        raise SystemExit("killed after the broker call")

    monkeypatch.setattr(a.eng, "_md_exit_sent", dies)
    with contextlib.suppress(SystemExit):
        a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())
    a.restart()
    assert a.eng._md_exits == {}
    a.ticks(a.open2, 5, 8)
    assert _fills(a) == [] and "B" not in a.broker.pos
    diffs = a.eng.recon.check("test").diffs
    assert diffs and "B" in str(diffs)
    assert a.eng.entry_block() is not None
