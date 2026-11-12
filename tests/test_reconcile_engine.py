"""Issue #118, QA-Ops review of #173 (B1): the live start-up reconciliation blocks entries until it succeeds; the
first open tick does not reset it; a cash difference is caught; exits keep running; the owner's command (with a
reason) accepts the difference, journalled."""

from __future__ import annotations

from test_ledger_engine import D2, carried, offer, rows, session

from kaibot.engine.reconcile import ACCEPT_COMMAND


def drift(eng, cash=-500.0):
    eng._recon_inputs = lambda: {"ledger_qty": {}, "broker_positions": {}, "ledger_cash": 0.0, "ledger_cash0": 0.0,
                                 "broker_cash": cash, "broker_cash0": 0.0, "equity": 5000.0}


def test_a_start_up_cash_difference_survives_the_first_open_tick_and_blocks_entries_only(tmp_path):
    eng, broker, clock, store = carried(tmp_path)  # D1: a dip position held over the close
    drift(eng)
    eng.startup()  # a restart: the ledger and the broker disagree on cash
    assert eng.recon.last.diffs and not eng._may_enter(60)
    offer(eng, D2)  # a dip candidate at 10:24 on D2
    session(eng, broker, clock, D2)
    trades = rows(store, "trade")
    assert len(trades) == 1, "no entry while the difference is open"
    assert trades[0]["exit_reason"] == "carried_over" and broker.positions() == {}, "exits still run"
    assert any(e["type"] == "reconcile_blocked" for e in rows(store, "event"))


def test_a_read_failure_at_start_up_blocks_entries_until_a_check_succeeds(tmp_path):
    eng, broker, clock, store = carried(tmp_path)

    def down():
        raise RuntimeError("broker unreachable")

    eng._recon_inputs = down
    eng.startup()
    assert eng.recon.last.diffs and eng.recon.last.diffs[0].kind == "unavailable"
    drift(eng, cash=0.0)  # reachable again, books agree
    eng._recheck_reconcile(force=True)
    assert not eng.recon.last.diffs


def test_the_owner_accepts_the_difference_with_a_reason_and_it_holds_until_it_changes(tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    drift(eng)
    eng.startup()
    store.insert("command", {"kind": ACCEPT_COMMAND, "payload": {"reason": ""}, "idempotency_key": "a0",
                             "applied_at": None})
    eng.apply_commands()
    assert eng.recon.last.diffs, "a resolution needs a reason"
    store.insert("command", {"kind": ACCEPT_COMMAND, "payload": {"reason": "fees the ledger does not model"},
                             "idempotency_key": "a1", "applied_at": None})
    eng.apply_commands()
    assert not eng.recon.last.diffs and eng._may_enter(60)
    eng.startup()  # a restart keeps the resolution (folded from the journal)
    assert not eng.recon.last.diffs
    drift(eng, cash=-900.0)  # the difference changed: blocked again at the next check (the next session start)
    eng._startup_reconcile("session_start")
    assert eng.recon.last.diffs and not eng._may_enter(60)
