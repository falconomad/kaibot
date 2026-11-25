"""#252: a multi-day flatten sale still open at a restart is booked exactly once (written before the code). The sale
is journalled when sent (`md_exit`: order id, run, keys, reason) and when booked or dropped (`md_exit_done`); a
restart rebuilds the open ones from the journal. On the simulated venue of the S80 drills.

M10 1.20 step 4 (#445, Product ruling 24 Nov): the older direct sale (`kbfl-` client id, `md_exit_intent`), which
an engine sent when it had no daily path, is deleted. Step 2 made it unreachable: an engine whose run holds
multi-day sleeves and that has no daily path does not start. No engine sends it now; a journal an older engine wrote
may still hold one that was sent and not booked, and `_rebuild_md_exits` and `_sync_md_exits` still book it.
`_older_sale` below writes that journal, as the deleted code wrote it, on the account WITH the daily path (as live
runs, so the restarts need no lifted check). The sales an engine sends now go through the order manager
(tests/test_flatten_via_om.py)."""

from __future__ import annotations

import contextlib

import pytest
from test_failsafe_drills import MD, QTY, Account, daily_account

from kaibot.broker.protocol import OrderRequest
from kaibot.engine import order_path
from kaibot.engine.core import Engine
from kaibot.engine.failsafe import clean


def _fills(a, side="sell"):
    return [b for *_, b in a.eng.journal.entries(kinds=("fill",)) if b.get("symbol") == "B" and b["side"] == side]


def _held(tmp_path, monkeypatch) -> Account:
    a = daily_account(tmp_path, monkeypatch)
    a.hold_b()
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 5)
    return a


def _older_sale(a, stage: str = "sent", reason: str = "book_drawdown") -> str:
    """The older direct sale of every sleeve in B, journalled as an engine before #445 step 4 journalled it (the
    deleted `_md_exit_submit` branch and `_flatten_md`'s `kbfl-` id): the symbol's stops cancelled, `md_exit_intent`,
    the call under `Engine._order` (its `intent`, then the venue), then `md_exit`. `stage` is where that engine died:
    `lost` (the call never reached the venue), `call` (the venue took the order, the answer was lost), `answer`
    (answered; `md_exit` not written) or `sent` (all of it written). Returns the client id."""
    eng, rid = a.eng, a.rid
    keys = sorted((k, sl.qty) for k, sl in eng._md_sleeves().items() if k[1] == "B")
    coid = f"kbfl-{clean(keys[0][0][0])[:40]}-{clean('B')[:12]}-{eng.now():%Y%m%d%H%M%S}"
    for oo in a.broker.open_orders():
        if oo.get("symbol") == "B":
            eng._order("cancel", lambda oid=oo["id"]: order_path.cancel_order(a.broker, oid), order_id=oo["id"],
                       symbol="B", strategy=",".join(k[0] for k, _ in keys))
    qty = sum(q for _, q in keys)
    eng.journal.append("md_exit_intent", {"coid": coid, "run_id": rid, "symbol": "B", "reason": reason,
                                          "keys": [[list(k), q] for k, q in keys], "ts": eng.now().isoformat()})
    req = OrderRequest("B", "sell", qty, "market", "day", coid)

    def call():
        if stage == "lost":
            raise SystemExit("killed before the call reached the venue")
        o = order_path.submit(a.broker, req, gate=eng.trading_state)
        if stage == "call":
            raise SystemExit("killed after the venue took the order")
        return o

    with contextlib.suppress(SystemExit):
        o = eng._order("sell", call, coid=coid, symbol="B", strategy=",".join(k[0] for k, _ in keys), qty=qty,
                       reason=reason)
        if stage == "sent":
            eng._md_exit_sent(o, rid, keys, reason)
    return coid


def _om_sales(a):
    return [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"
            and o["type"] == "market" and "-flatten-" in str(o.get("client_order_id"))]


# ---------------------------------------------------------------- M10 1.20 step 4: no older sale is sent


def test_a_flatten_sends_no_sale_outside_the_order_manager(tmp_path, monkeypatch):
    """With the daily path every sleeve's sale goes through the order manager: no `kbfl-` id, no `md_exit_intent`."""
    a = _held(tmp_path, monkeypatch)
    failed = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    assert failed == {} and len(_om_sales(a)) == 1
    assert not [o for o in a.broker.orders.values() if str(o.get("client_order_id")).startswith("kbfl-")]
    assert not list(a.eng.journal.entries(kinds=("md_exit_intent", "md_exit")))


def test_an_engine_without_the_daily_path_never_sells_a_sleeve(tmp_path, monkeypatch):
    """The case step 2 keeps from starting (tests/test_445_daily_path_required.py), built here by hand: sleeves and
    no daily path. Should a flatten be reached all the same, it cancels, sends and journals nothing for them and
    counts the symbol as failed (fail closed): the broker-held stop stays."""
    a = Account(tmp_path)  # no daily path
    a.hold_b()
    a.ticks(a.open1, 30, 60)
    assert a.eng.daily is None and a.eng._md_sleeves()
    a.eng.sleep = lambda s: None
    before, seq = set(a.broker.orders), a.eng.journal.last_seq()
    real = a.broker.submit
    a.broker.submit = lambda *x, **k: pytest.fail("a sale was sent without the order manager")
    failed = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    a.broker.submit = real
    assert list(failed) == ["B"] and "no order manager" in str(failed["B"])
    sells = [o for o in a.broker.orders.values() if o["side"] == "sell" and o["type"] == "market"]
    assert sells == [] and not any(b.get("action") == "sell" for *_, b in a.eng.journal.entries(kinds=("intent",)))
    assert not list(a.eng.journal.entries(kinds=("md_exit_intent", "md_exit")))
    assert a.eng._md_exits == {} and a.eng.journal.last_seq() == seq  # nothing journalled
    assert set(a.broker.orders) == before and a.stop_b()  # nothing sent or cancelled: B's stop still rests


def test_md_exit_submit_without_an_order_manager_raises_before_anything(monkeypatch):
    eng = Engine.__new__(Engine, intraday=False)
    eng.daily = None
    with pytest.raises(RuntimeError, match="no order manager"):
        Engine._md_exit_submit(eng, "B", [((MD, "B"), 40.0)], "book_drawdown", 3)


# ---------------------------------------------------------------- an older engine's sale, booked after a restart


def test_a_sale_open_at_a_restart_is_booked_once_after_it(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    _older_sale(a)
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    assert x["keys"] == [[[MD, "B"], QTY]] and x["reason"] == "book_drawdown" and x["run_id"] == a.rid
    assert _fills(a) == []  # sent, not filled yet
    a.restart()  # the engine dies before the fill
    assert x["order_id"] in a.eng._md_exits  # rebuilt from the journal
    a.ticks(a.open2, 5, 8)  # the sale fills at the venue; the new engine books it
    (f,) = _fills(a)
    assert f["qty"] == QTY and f["order_id"] == x["order_id"]
    # #241: the venue's own fee (the engine tests' venue charges none), never the estimate
    assert f["fees"] == sum(float(v["fees"]) for v in a.broker.fills if v["order_id"] == x["order_id"])
    assert (MD, "B") not in a.eng.ledger.book(a.rid).sleeves and not a.eng._md_sleeves()
    assert "B" not in a.broker.pos
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["order_id"] == x["order_id"] and done["state"] == "booked"
    a.restart()  # a second restart books nothing again
    assert a.eng._md_exits == {}
    a.ticks(a.open2, 8, 10)
    assert len(_fills(a)) == 1 and _om_sales(a) == []


def test_a_crash_between_the_booking_and_its_done_entry_books_nothing_twice(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    _older_sale(a)
    a.restart()
    real = a.eng.journal.append

    def dies(kind, body):  # the process dies after the fill entry and before `md_exit_done`
        if kind == "md_exit_done":
            raise SystemExit("killed")
        return real(kind, body)

    a.eng.journal.append = dies
    with contextlib.suppress(SystemExit):
        a.ticks(a.open2, 5, 8)
    assert len(_fills(a)) == 1 and not list(a.eng.journal.entries(kinds=("md_exit_done",)))
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    a.restart()
    assert x["order_id"] in a.eng._md_exits
    a.ticks(a.open2, 8, 10)
    assert len(_fills(a)) == 1 and a.eng._md_exits == {}


def test_a_sale_cancelled_while_down_is_dropped_and_the_next_flatten_goes_through_the_manager(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    _older_sale(a)
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    a.broker.cancel(x["order_id"])  # cancelled at the venue while the engine is down
    a.restart()
    a.ticks(a.open2, 5, 6)
    assert a.eng._md_exits == {}
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["state"] == "canceled" and _fills(a) == []
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())  # the next flatten sells it, through the manager
    assert len(_om_sales(a)) == 1 and len([b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]) == 1
    a.ticks(a.open2, 6, 9)
    (f,) = _fills(a)
    assert f["qty"] == QTY and "-flatten-" in f["coid"]


def test_a_crash_between_two_sleeves_of_one_sale_books_the_second_after_the_restart(tmp_path, monkeypatch):
    """One older sale for two sleeves of B: the process dies after the first sleeve's fill entry. The restart books
    the second sleeve, and the first is not booked again (the check is per sleeve, not per order)."""
    a = _held(tmp_path, monkeypatch)
    px = float(a.eng.market.last_close("B"))
    a.broker.pos["B"]["qty"] = QTY + 10
    a.eng._book("fill", {"run_id": a.rid, "strategy": "mom_12_1_band", "symbol": "B", "side": "buy", "qty": 10,
                         "px": px, "fees": 0.0, "coid": f"kb{a.rid}-mom_12_1_band-B-20260803-target-1"})
    _older_sale(a)
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    assert len(x["keys"]) == 2
    a.restart()
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
    assert not a.eng._md_sleeves() and "B" not in a.broker.pos


def test_an_older_sale_answered_but_not_journalled_is_found_by_client_id(tmp_path, monkeypatch):
    """QA-Ops (#279, #282): the venue took and answered the older sale; the process died before `md_exit`. At the
    restart the sale is found at the venue by the client id of its journalled `intent`, booked once when it fills,
    and nothing is sent again."""
    a = _held(tmp_path, monkeypatch)
    coid = _older_sale(a, stage="answer")
    sent = [o for o in a.broker.orders.values() if o.get("client_order_id") == coid]
    assert len(sent) == 1 and not list(a.eng.journal.entries(kinds=("md_exit",)))
    a.restart()
    assert sent[0]["id"] in a.eng._md_exits  # rebuilt from the intent and the venue's record
    a.ticks(a.open2, 5, 8)
    (f,) = _fills(a)
    assert f["qty"] == QTY and f["order_id"] == sent[0]["id"]
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())  # nothing left to sell
    assert _om_sales(a) == []
    a.restart()
    assert a.eng._md_exits == {} and len(_fills(a)) == 1


def test_an_older_sale_whose_answer_was_lost_is_matched_by_client_id(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    coid = _older_sale(a, stage="call")
    assert not list(a.eng.journal.entries(kinds=("md_exit",)))
    a.restart()
    (x,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit",))]
    assert x["coid"] == coid and x["order_id"] in a.eng._md_exits
    assert any(e["type"] == "multi_day_exit_recovered" for e in a.store.tables["event"])
    a.ticks(a.open2, 5, 8)
    (f,) = _fills(a)
    assert f["qty"] == QTY and f["order_id"] == x["order_id"]
    assert (MD, "B") not in a.eng.ledger.book(a.rid).sleeves


def test_an_older_intent_the_venue_never_got_is_closed_at_start_up(tmp_path, monkeypatch):
    a = _held(tmp_path, monkeypatch)
    _older_sale(a, stage="lost")
    assert not [o for o in a.broker.orders.values() if str(o.get("client_order_id")).startswith("kbfl-")]
    a.restart()
    assert a.eng._md_exits == {}
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["state"] == "never_sent"
    a.eng._flatten_md("book_drawdown", {}, a.eng.monotonic())  # the next flatten sells it, through the manager
    a.ticks(a.open2, 5, 8)
    (f,) = _fills(a)
    assert "-flatten-" in f["coid"]


def test_a_cancel_that_takes_a_moment_does_not_put_the_sale_off_to_the_next_tick(tmp_path, monkeypatch):
    """QA-Ops (#282): Alpaca shows a cancelled stop as open for a moment (pending_cancel). The flatten waits for it
    inside its time budget, as close_position's cancel-and-wait did, and sends the sale in the same call."""
    a = _held(tmp_path, monkeypatch)
    assert a.stop_b()  # the sleeve's protective stop rests at the venue
    real, lag = a.broker.open_orders, {"n": 2}

    def open_orders():
        rows = real()
        gone = [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["type"] == "stop"
                and o["status"] == "canceled"]
        if gone and lag["n"] > 0:  # the venue still lists the cancelled stop, as pending_cancel, twice
            lag["n"] -= 1
            rows = rows + [{**gone[0], "status": "pending_cancel"}]
        return rows

    slept = []
    monkeypatch.setattr(a.broker, "open_orders", open_orders)
    monkeypatch.setattr(a.eng, "sleep", slept.append)
    failed = {}
    a.eng._flatten_md("book_drawdown", failed, a.eng.monotonic())
    assert failed == {} and slept  # waited, then sold
    (o,) = _om_sales(a)
    assert float(o["qty"]) == QTY
