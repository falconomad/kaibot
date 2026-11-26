"""M9 ticket 1.15 (audit S1): the engine trusted every command row. Written before the fix.

- `new_run` took any amount (`float(pl["amount_usd"])`): the database's own limit ($1,000 to $100,000, migration
  0002) is met only later by the replicator, which a bad row would then stall. And it was checked (level only) AFTER
  the old run's positions had been sold, so a bad command still sold the whole book.
- A kind the engine does not know was marked applied and written as an event named after it (`command_<kind>`).

Now: a command kind outside `COMMAND_KINDS`, or a `new_run` whose amount or level is not valid, is rejected before
anything is done: marked applied with the reason, and one `command_rejected` event. On the venue of the S80 drills."""

from __future__ import annotations

import pytest
from test_failsafe_drills import Account

from kaibot.engine import commands, core


def _cmd(a, kind, payload, key):
    return a.store.insert("command", {"kind": kind, "payload": payload, "idempotency_key": key, "applied_at": None})


def _events(a, kind):
    return [e["payload"] for e in a.store.tables["event"] if e["type"] == kind]


def _row(a, key):
    (c,) = [c for c in a.store.tables["command"] if c["idempotency_key"] == key]
    return c


@pytest.mark.parametrize("amount", [1, 999.99, 100_000.01, 10_000_000, -5000, float("nan"), float("inf"), "lots",
                                    None, True, [5000], 10**400, "1e999", {"usd": 5000}])
def test_a_new_run_outside_the_amount_limits_is_rejected_with_an_event(tmp_path, amount):
    a = Account(tmp_path)
    runs = len(a.store.tables["run"])
    _cmd(a, "new_run", {"amount_usd": amount, "level": "balanced"}, "bad")
    a.eng.apply_commands()
    assert len(a.store.tables["run"]) == runs and a.eng.run["id"] == a.rid  # no new run, the old one goes on
    assert a.eng.run["status"] in ("scheduled", "running")
    c = _row(a, "bad")
    assert c["applied_at"] is not None and "amount" in c["payload"]["error"]
    (e,) = _events(a, "command_rejected")
    assert e["kind"] == "new_run" and "amount" in e["reason"] and e["command_id"] == c["id"]
    assert _events(a, "command_new_run") == [{"amount_usd": 10_000.0, "level": "balanced"}]  # the first, valid one
    a.eng.apply_commands()
    assert len(_events(a, "command_rejected")) == 1  # once: the command is not pending any more


@pytest.mark.parametrize("payload", [{"amount_usd": 5000, "level": "yolo"}, {"amount_usd": 5000},
                                     {"level": "balanced"}, {}, {"amount_usd": 5000, "level": None},
                                     {"amount_usd": 5000, "level": ["balanced"]}])
def test_a_new_run_with_an_unknown_level_or_a_missing_field_is_rejected(tmp_path, payload):
    a = Account(tmp_path)
    _cmd(a, "new_run", payload, "bad")
    a.eng.apply_commands()
    assert a.eng.run["id"] == a.rid and len(a.store.tables["run"]) == 1
    assert len(_events(a, "command_rejected")) == 1 and _row(a, "bad")["applied_at"] is not None


@pytest.mark.parametrize("amount", [core.RUN_AMOUNT_MIN_USD, 5000, "8000", core.RUN_AMOUNT_MAX_USD])
def test_a_new_run_inside_the_limits_still_starts(tmp_path, amount):
    a = Account(tmp_path)
    _cmd(a, "new_run", {"amount_usd": amount, "level": "aggressive"}, "ok")
    a.eng.apply_commands()
    assert a.eng.run["id"] != a.rid and float(a.eng.run["amount_usd"]) == float(amount)
    assert a.eng.run["level"] == "aggressive" and _events(a, "command_rejected") == []
    assert a.eng.ledger.book(a.eng.run["id"]).amount == float(amount)


def test_a_bad_new_run_does_not_sell_the_old_runs_book(tmp_path):
    """The old order of checks: flatten the old run first, then look at the level. A $10 million command (or an
    unknown level) sold everything and then failed."""
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    orders = len(a.broker.orders)
    _cmd(a, "new_run", {"amount_usd": 10_000_000, "level": "balanced"}, "bad1")
    _cmd(a, "new_run", {"amount_usd": 5000, "level": "yolo"}, "bad2")
    a.eng.apply_commands()
    a.ticks(a.open2, 10, 13)
    assert len(a.broker.orders) == orders and a.sells("B") == [] and len(a.stop_b()) == 1  # nothing sold or cancelled
    assert "B" in a.broker.pos and a.eng.run["id"] == a.rid and a.eng.run["status"] == "running"
    assert len(_events(a, "command_rejected")) == 2


def test_an_unknown_command_kind_is_rejected_and_never_named_in_an_event_type(tmp_path):
    a = Account(tmp_path)
    for i, kind in enumerate(["sell_everything", "accept_reconcile; drop table", "", None, 7]):
        _cmd(a, kind, {"x": 1}, f"u{i}")
    a.eng.apply_commands()
    assert all(c["applied_at"] is not None for c in a.store.tables["command"])
    rej = _events(a, "command_rejected")
    assert len(rej) == 5 and all(r["reason"] == "unknown command kind" for r in rej)
    types = {e["type"] for e in a.store.tables["event"]}
    assert {t for t in types if t.startswith("command_")} == {"command_new_run", "command_rejected"}
    assert a.eng.run["id"] == a.rid  # and the engine goes on


def test_every_known_command_kind_is_still_applied(tmp_path):
    a = Account(tmp_path)
    a.ticks(a.open1, 0, 5)
    for i, (kind, pl) in enumerate([("pause", {}), ("resume", {}), ("set_level", {"level": "aggressive"}),
                                    ("accept_reconcile", {"reason": "checked by hand"}), ("flatten_and_stop", {})]):
        assert kind in core.COMMAND_KINDS
        _cmd(a, kind, pl, f"k{i}")
    a.eng.apply_commands()
    types = [e["type"] for e in a.store.tables["event"] if e["type"].startswith("command_")]
    assert types == ["command_new_run", "command_pause", "command_resume", "command_set_level",
                     "command_accept_reconcile", "command_flatten_and_stop"]
    assert a.eng.run is None and a.store.tables["run"][0]["status"] == "stopped"


def test_the_amount_limits_are_the_databases(tmp_path):
    """migrations/0001 and 0002: `amount_usd between 1000 and 100000` for a paper run."""
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations" / "0002_dashboard_access.sql").read_text()
    assert "amount_usd between 1000 and 100000" in sql
    assert (core.RUN_AMOUNT_MIN_USD, core.RUN_AMOUNT_MAX_USD) == (1000.0, 100000.0)


def test_a_command_the_check_cannot_read_is_rejected_and_the_tick_goes_on(tmp_path, monkeypatch):
    """QA-Ops (#513): the check runs outside the command's own try. If it raises, the row would stay pending and
    raise on every tick (three in a row: the engine_errors kill). It is refused instead, once."""
    a = Account(tmp_path)

    def boom(amount, level, policies):
        raise RuntimeError("check broke")

    monkeypatch.setattr(commands, "run_refusal", boom)  # #694: the check is read where the command loop is
    _cmd(a, "new_run", {"amount_usd": 5000, "level": "balanced"}, "odd")
    a.eng.apply_commands()  # does not raise
    assert a.eng.run["id"] == a.rid and len(a.store.tables["run"]) == 1
    (e,) = _events(a, "command_rejected")
    assert "could not be checked" in e["reason"] and _row(a, "odd")["applied_at"] is not None
    a.eng.apply_commands()
    assert len(_events(a, "command_rejected")) == 1
