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
