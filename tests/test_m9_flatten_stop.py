"""M9 ticket 1.2 (audit R2): "Flatten and stop" was not retried. Written before the fix.

- `flatten_all` raises `FlattenIncomplete` when anything is left; `apply_commands`' generic handler then marked the
  command applied with the error text. The run stayed "running", the position stayed, and no later pass retried.
  (The `new_run` command, which sells the old book first, lost itself the same way.)
- When the flatten did go through, the run was ended at once, with the sales still unfilled.
- Commands are applied while the market is closed too, so the sale went out as DAY market orders that wait for the
  next open.

Now: the command stays pending until the book is flat (every pass tries again), no position is opened while it is
pending, the sales are sent only while the market is open, and the run ends when nothing is left to sell. On the
venue of the S80 drills (a multi-day sleeve of B) and of the soak test (intraday A and B)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from test_engine_soak import refuse_close, step, two_open
from test_failsafe_drills import MD, Account

from kaibot.broker.protocol import BrokerError
from kaibot.engine import core


def _cmd(store, kind, payload, key):
    return store.insert("command", {"kind": kind, "payload": payload, "idempotency_key": key, "applied_at": None})


def _row(store, key):
    (c,) = [c for c in store.tables["command"] if c["idempotency_key"] == key]
    return c


def _events(store, kind):
    return [e for e in store.tables.get("event", []) if e["type"] == kind]


def _refuse_sales(broker, symbol):
    """The venue does not take a market sale of `symbol` (network error) until `state["down"]` is cleared."""
    real, state = broker.submit, {"down": True, "calls": 0}

    def submit(req, *a, **k):
        if state["down"] and req.symbol == symbol and req.side == "sell" and req.type == "market":
            state["calls"] += 1
            raise BrokerError(0, "sim: network down")
        return real(req, *a, **k)

    broker.submit = submit
    return state


def test_an_incomplete_flatten_leaves_the_command_pending_and_the_next_pass_retries(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    assert a.eng.entry_block() is None
    venue = _refuse_sales(a.broker, "B")
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    c = _row(a.store, "k9")
    assert c["applied_at"] is None and "error" not in (c["payload"] or {})  # still pending, not "applied with an error"
    assert venue["calls"] == 1 and a.eng.run["status"] == "running" and "B" in a.broker.pos
    assert a.eng.entry_block() == "flatten and stop pending" and not a.eng._may_enter(60)
    a.eng.apply_commands()  # the next loop pass tries again
    assert venue["calls"] == 2 and _row(a.store, "k9")["applied_at"] is None
    a.ticks(a.open2, 10, 12)  # the engine goes on ticking; nothing is bought
    assert a.eng.run["status"] == "running" and not [o for o in a.broker.orders.values() if o["side"] == "buy"
                                                     and o["symbol"] == "B" and o["status"] == "new"]
    venue["down"] = False
    a.eng.apply_commands()  # the sale goes out; the run is not ended before it is booked
    assert len(a.sells("B")) == 1 and a.eng.run is not None and _row(a.store, "k9")["applied_at"] is None
    a.ticks(a.open2, 12, 14)
    a.eng.apply_commands()
    assert _row(a.store, "k9")["applied_at"] is not None and a.eng.run is None
    assert a.store.tables["run"][0]["status"] == "stopped" and "B" not in a.broker.pos and not a.stop_b()
    assert (MD, "B") not in a.eng.ledger.book(a.rid).sleeves and len(a.sells("B")) == 1  # sold once
    assert len(_events(a.store, "command_flatten_and_stop")) == 1 and len(_events(a.store, "multi_day_sold")) == 1
    a.eng.apply_commands()
    assert len(_events(a.store, "command_flatten_and_stop")) == 1


def test_the_run_ends_only_when_the_sale_is_booked(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    assert a.sells("B") and a.eng._md_exits  # sent, not yet booked
    assert a.store.tables["run"][0]["status"] == "running" and _row(a.store, "k9")["applied_at"] is None
    assert a.eng.entry_block() == "flatten and stop pending"
    a.eng.apply_commands()
    assert len(a.sells("B")) == 1  # a pass in between sends nothing twice
    a.ticks(a.open2, 10, 12)
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped" and _row(a.store, "k9")["applied_at"] is not None
    assert "B" not in a.broker.pos and not a.eng._md_sleeves()


def test_no_sale_is_sent_while_the_market_is_closed_and_it_goes_out_at_the_open(tmp_path):
    a = Account(tmp_path)
    a.day1()  # 16:30 ET on DAY1, the market is closed, B is held overnight
    orders = len(a.broker.orders)
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    a.clock.t += timedelta(hours=3)
    a.eng.tick()
    a.eng.apply_commands()
    assert len(a.broker.orders) == orders and len(a.stop_b()) == 1  # nothing sent, the stop still rests
    assert _row(a.store, "k9")["applied_at"] is None and a.eng.run["status"] == "running"
    assert a.eng.entry_block() == "flatten and stop pending"
    a.broker.advance(pd.Timestamp(a.open2))
    a.clock.t = a.open2 + timedelta(seconds=30)  # the first loop pass after the open
    a.eng.apply_commands()
    assert len(a.sells("B")) == 1 and not a.stop_b()
    a.ticks(a.open2, 0, 3)
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped" and "B" not in a.broker.pos
    assert _row(a.store, "k9")["applied_at"] is not None


def test_an_unreadable_clock_sends_no_sale_and_keeps_the_command(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    real = a.broker.clock

    def down():
        raise BrokerError(0, "sim: network down")

    a.broker.clock = down
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    assert not a.sells("B") and _row(a.store, "k9")["applied_at"] is None
    assert a.eng.entry_block() == "flatten and stop pending"
    a.broker.clock = real
    a.eng.apply_commands()
    assert len(a.sells("B")) == 1


def test_a_new_run_whose_flatten_is_incomplete_also_waits_and_retries(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    venue = _refuse_sales(a.broker, "B")
    _cmd(a.store, "new_run", {"amount_usd": 8000, "level": "balanced"}, "k8")
    a.eng.apply_commands()
    c = _row(a.store, "k8")
    assert c["applied_at"] is None and "error" not in c["payload"] and a.eng.run["id"] == a.rid
    assert a.eng.entry_block() == "new run pending: the old book is being sold"
    venue["down"] = False
    a.eng.apply_commands()
    a.ticks(a.open2, 10, 12)
    a.eng.apply_commands()
    assert a.eng.run["id"] != a.rid and "B" not in a.broker.pos and _row(a.store, "k8")["applied_at"] is not None
    assert a.eng.entry_block() != "new run pending: the old book is being sold"


def test_the_entry_block_survives_a_pass_that_could_not_read_the_commands(tmp_path):
    """JournalStore.pending_commands answers [] while Supabase is unreachable: the block must not lift then."""
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    _refuse_sales(a.broker, "B")
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    real = a.store.pending_commands
    a.store.pending_commands = lambda: []
    a.eng.apply_commands()
    assert a.eng.entry_block() == "flatten and stop pending"
    a.store.pending_commands = real


def test_an_intraday_position_the_venue_refuses_to_close_keeps_the_command_pending(tmp_path):
    eng, broker, clock, store, open_utc = two_open(tmp_path)
    step(eng, broker, clock, open_utc, 0, 57)
    calls = refuse_close(broker, {"A"}, times=core.FLATTEN_TRIES)
    _cmd(store, "flatten_and_stop", {}, "k9")
    eng.apply_commands()
    assert _row(store, "k9")["applied_at"] is None and "error" not in (_row(store, "k9")["payload"] or {})
    assert eng.pos["B"].exit_order_id and eng.pos["A"].exit_order_id is None and eng.run["status"] == "running"
    assert not eng._may_enter(60)
    eng.apply_commands()  # the retry: A is closed now, B is not closed twice
    assert eng.pos["A"].exit_order_id and calls.count("B") == 1
    step(eng, broker, clock, open_utc, 57, 60)
    eng.apply_commands()
    assert broker.pos == {} and eng.run is None and store.tables["run"][0]["status"] == "stopped"
    assert _row(store, "k9")["applied_at"] is not None
    assert sorted(t["exit_reason"] for t in store.tables["trade"]) == ["manual", "manual"]


def test_the_entry_block_survives_a_restart_while_the_commands_cannot_be_read(tmp_path, monkeypatch):
    """QA-Ops second key on #525: the block was in memory only. The engine is restarted with the command still
    pending and Supabase unreachable (`pending_commands` answers none): it must not open a position in a book the
    owner told it to sell. The block is read back from the journal and ends with the command."""
    # #445 step 2: this venue's engine has no daily path, so with B in the book a restart is refused; the check is
    # lifted here only, to keep the older flatten sale covered until step 4 deletes it
    monkeypatch.setattr(core.Engine, "_require_daily_path", lambda self: None)
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    venue = _refuse_sales(a.broker, "B")
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    assert len(_events(a.store, "command_waiting")) == 1
    real = a.store.pending_commands
    a.store.pending_commands = lambda: []
    a.restart()
    assert a.eng.run is not None and a.eng.entry_block() == "flatten and stop pending" and not a.eng._may_enter(60)
    a.eng.apply_commands()
    a.ticks(a.open2, 10, 12)
    assert a.eng.entry_block() == "flatten and stop pending"
    a.store.pending_commands = real  # Supabase is back: the command is read, retried and completed
    venue["down"] = False
    a.eng.apply_commands()
    a.ticks(a.open2, 12, 14)
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped" and "B" not in a.broker.pos
    assert len(_events(a.store, "command_waiting")) == 1  # once per command, not once per process
    kinds = [k for _, _t, k, _b in a.eng.journal.entries(kinds=("command_waiting", "command_done"))]
    assert kinds == ["command_waiting", "command_done"]
    a.restart()
    assert a.eng._cmd_waiting == {}


def test_a_waiting_command_of_an_ended_run_blocks_nothing_after_a_restart(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    _refuse_sales(a.broker, "B")
    _cmd(a.store, "flatten_and_stop", {}, "k9")
    a.eng.apply_commands()
    a.eng._end_run("ended", "drawdown 8% from peak")  # the run ends by another path with the command still waiting
    a.restart()
    assert a.eng.run is None and a.eng._cmd_waiting == {}
