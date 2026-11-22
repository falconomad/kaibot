"""M9 ticket 1.9 (audit M8): a run that ended, a restart, and a multi-day flatten sale not booked yet. Written before
the fix. With no run and no session (`Engine.day is None` after the restart) `tick` returned `idle` before
`_sync_orders`, so `_sync_md_exits` never ran: the sale was never booked and a `new_run` command waited forever
(`apply_commands` keeps it pending while `_md_exits` is not empty). On the simulated venue of the S80 drills."""

from __future__ import annotations

from test_failsafe_drills import MD, QTY, Account


def _sells(a):
    return [b for *_, b in a.eng.journal.entries(kinds=("fill",)) if b.get("symbol") == "B" and b["side"] == "sell"]


def _stopped_and_restarted(tmp_path, k: int) -> Account:
    """The book holds B; "Flatten and stop" at minute `k` of DAY2 ends the run and sends B's sale; the engine dies
    before the sale fills and comes back with no run, no session and the sale rebuilt from the journal."""
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, k)
    a.store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k9",
                               "applied_at": None})
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped" and _sells(a) == []
    a.restart()
    assert a.eng.run is None and a.eng.day is None and len(a.eng._md_exits) == 1
    return a


def _booked_once(a):
    (f,) = _sells(a)
    assert f["qty"] == QTY and f["strategy"] == MD and f["run_id"] == a.rid
    (done,) = [b for *_, b in a.eng.journal.entries(kinds=("md_exit_done",))]
    assert done["state"] == "booked" and a.eng._md_exits == {}
    assert (MD, "B") not in a.eng.ledger.book(a.rid).sleeves and "B" not in a.broker.pos


def test_a_sale_open_at_a_restart_after_the_run_ended_is_booked_and_a_new_run_starts(tmp_path):
    a = _stopped_and_restarted(tmp_path, 10)
    a.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": 8000, "level": "balanced"},
                               "idempotency_key": "k8", "applied_at": None})
    for k in range(10, 13):  # the main loop: commands, then the tick
        a.eng.apply_commands()
        assert a.ticks(a.open2, k, k + 1, catch=True) == []
    _booked_once(a)
    a.eng.apply_commands()
    assert a.eng.run is not None and a.eng.run["id"] != a.rid  # the command no longer waits
    assert all(c["applied_at"] is not None for c in a.store.tables["command"])
    a.ticks(a.open2, 13, 15)
    assert len(_sells(a)) == 1  # booked once


def test_a_sale_that_filled_before_the_close_is_booked_by_a_restart_after_the_close(tmp_path):
    a = _stopped_and_restarted(tmp_path, 380)
    a.after_close(a.open2)  # the market is closed: the sale filled at the venue before 16:00
    _booked_once(a)


def test_an_unreadable_order_with_the_market_closed_does_not_stop_the_closed_branch(tmp_path):
    a = _stopped_and_restarted(tmp_path, 380)

    def down(oid):
        raise RuntimeError("venue unreachable")

    real, a.broker.order = a.broker.order, down
    a.after_close(a.open2)  # no raise: the closed branch (the daily decision, the preparation) goes on
    assert _sells(a) == [] and len(a.eng._md_exits) == 1
    a.broker.order = real
    a.after_close(a.open2)
    _booked_once(a)
