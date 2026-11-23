"""#551: a damaged journal entry read on the daily path while the engine runs. Written before the fix: the daily
path's broad handlers (`Engine._daily_step`, `_daily_status`) caught `JournalCorrupt`, so `tick()` returned
normally, the near-close send failed silently on every tick (one `multi_day_error`), the heartbeat stayed healthy
and the watchdog never took over. Now the journal remembers the first damage it found, and `tick()` (and
`apply_commands`) raise it whatever handler caught it on the way, so the main loop writes `engine_error`, stops
and exits with code 4 (docs/ops/ledger.md, "Checksums")."""

from __future__ import annotations

import contextlib
import os
import sqlite3

import pytest
from world import D1, EVENINGS, Scenario, World, et

from kaibot.broker.faulty import NO_FAULTS
from kaibot.engine.journal import JournalCorrupt


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    w = World(Scenario(5, {D1: ("B",), "2026-08-04": None, "2026-08-05": None},
                       {d: et(d, "16:10") for d in EVENINGS}, profile=NO_FAULTS), tmp_path / "w")
    w.evening("2026-07-31")
    for t in ("09:00", "09:25", "09:31", "10:00", "11:00"):
        w.at(et(D1, t, 7))
    yield w
    os.environ.pop("KAIBOT_MULTIDAY", None)


def _damage(w, kind: str) -> int:
    seq = [q for q, _, k, _ in w.eng.journal.entries() if k == kind][-1]
    db = sqlite3.connect(w.jpath)
    db.execute("drop trigger entry_no_update")  # a damaged disk, not SQL
    db.execute("update entry set body = replace(body, '\"', '''') where seq = ?", (seq,))
    db.commit()
    db.close()
    return seq


def test_a_damaged_decision_read_by_the_send_stops_the_tick(world):
    """The issue's case: the session's md_decision is damaged; the near-close tick must not return normally."""
    seq = _damage(world, "md_decision")
    world.clock.set(et(D1, "15:50", 7))
    with pytest.raises(JournalCorrupt, match=str(seq)):
        world.eng.tick()
    assert not [o for o in world.sim.orders.values() if o["symbol"] == "B"]  # nothing sent on the damaged book


def test_the_damage_stays_raised_on_every_later_tick(world):
    _damage(world, "md_decision")
    world.clock.set(et(D1, "15:50", 7))
    for _ in range(2):
        with pytest.raises(JournalCorrupt):
            world.eng.tick()


def test_a_command_pass_that_read_the_damage_raises_it(world):
    """apply_commands catches every command's failure (a bad command must never stop the engine): not this one."""
    world.eng.journal.damage = JournalCorrupt("journal entry 9 fails its checksum")
    with pytest.raises(JournalCorrupt, match="entry 9"):
        world.eng.apply_commands()


def test_the_tick_stops_at_the_step_that_read_the_damage(world, monkeypatch):
    """QA-Ops on #564: `_daily_step` caught the damage and the rest of the tick ran (the market update, the checks,
    the intraday entries) before the end-of-tick check raised. Nothing may run after the damage is read."""
    _damage(world, "md_decision")
    world.clock.set(et(D1, "15:50", 7))
    eng, after = world.eng, []
    for name in ("_market_update", "_sync_orders", "_check_drift", "_risk_halts", "_manage_stops", "_may_enter",
                 "_recheck_reconcile", "_late_actions"):
        real = getattr(eng, name)

        def spy(*a, _real=real, _name=name, **k):
            if eng.journal.damage is not None:
                after.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(eng, name, spy)
    with pytest.raises(JournalCorrupt):
        eng.tick()
    assert after == []


def test_a_command_that_read_the_damage_stays_unapplied_and_nothing_after_it_runs(world, monkeypatch):
    """QA-Ops on #564: a command whose handling read a damaged entry was marked applied (with the error) and lost,
    and the commands queued behind it still ran. It stays pending, nothing after it runs, the pass raises."""
    seq = _damage(world, "md_decision")
    eng, store = world.eng, world.eng.store

    def reads_the_journal(self, pl):  # any handler that reads the journal (here: the damaged decision)
        self.journal.entries(kinds=("md_decision",))

    monkeypatch.setattr(type(eng), "_accept_reconcile", reads_the_journal)
    for kind, key in (("accept_reconcile", "k-first"), ("pause", "k-second")):
        store.insert("command", {"kind": kind, "payload": {"reason": "x", "shown": []} if kind != "pause" else {},
                                 "idempotency_key": key, "applied_at": None})
    with pytest.raises(JournalCorrupt, match=str(seq)):
        eng.apply_commands()
    assert [c["idempotency_key"] for c in store.pending_commands()] == ["k-first", "k-second"]
    assert eng.run["status"] == "running"


def test_every_checked_read_of_the_journal_keeps_the_damage():
    """#551: `Journal.damage` is set by `Journal._check` / `_checked` only. A read path of the class that calls the
    module's `checked` or `check_entry` itself (a new cache, a snapshot read: #534) would let a damage be caught
    and forgotten; it must go through those two."""
    import inspect
    import re

    from kaibot.engine import journal

    src = inspect.getsource(journal.Journal)
    calls = [m.group(0) for m in re.finditer(r"(?<![._\w])(checked|check_entry)\(", src)]
    assert calls == ["check_entry(", "checked("], calls  # the two inside _check and _checked, nothing else


def test_a_daily_step_that_catches_the_damage_itself_still_stops_the_tick(world, monkeypatch):
    """QA-Ops second key on #564: a daily-path step that catches the error itself and returns normally (no raise
    reaches `_daily_step`) must stop the tick at that step too: nothing after it runs."""
    _damage(world, "md_decision")
    world.clock.set(et(D1, "15:50", 7))
    eng, after = world.eng, []

    def send_that_swallows(day):
        try:
            eng.journal.entries(kinds=("md_decision",))
        except JournalCorrupt:
            return None

    monkeypatch.setattr(eng.daily, "send", send_that_swallows)
    for name in ("_market_update", "_sync_orders", "_check_drift", "_risk_halts", "_may_enter"):
        real = getattr(eng, name)

        def spy(*a, _real=real, _name=name, **k):
            if eng.journal.damage is not None:
                after.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(eng, name, spy)
    with pytest.raises(JournalCorrupt):
        eng.tick()
    assert after == []


def test_a_command_whose_handler_catches_the_damage_itself_stays_pending(world, monkeypatch):
    """QA-Ops second key on #564: a command handler that catches the error itself and returns normally must not
    have its command marked applied, and the command behind it must not run."""
    seq = _damage(world, "md_decision")
    eng, store = world.eng, world.eng.store

    def swallows(self, pl):
        with contextlib.suppress(JournalCorrupt):
            self.journal.entries(kinds=("md_decision",))

    monkeypatch.setattr(type(eng), "_accept_reconcile", swallows)
    for kind, key in (("accept_reconcile", "k-first"), ("pause", "k-second")):
        store.insert("command", {"kind": kind, "payload": {"reason": "x", "shown": []} if kind != "pause" else {},
                                 "idempotency_key": key, "applied_at": None})
    with pytest.raises(JournalCorrupt, match=str(seq)):
        eng.apply_commands()
    assert [c["idempotency_key"] for c in store.pending_commands()] == ["k-first", "k-second"]
    assert eng.run["status"] == "running"


def test_verify_keeps_the_damage_it_finds(tmp_path):
    """QA-Ops on #564: the full check (`verify`, at open and on demand) goes through `Journal._check` too, so a
    damage it finds while the engine runs is kept like a read's."""
    from kaibot.engine.journal import Journal

    j = Journal(tmp_path / "j.sqlite")
    for i in range(3):
        j.append("fill", {"coid": f"c{i}"})
    db = sqlite3.connect(tmp_path / "j.sqlite")
    db.execute("drop trigger entry_no_update")
    db.execute("update entry set body = '{\"coid\": \"forged\"}' where seq = 2")
    db.commit()
    db.close()
    assert j.damage is None
    with pytest.raises(JournalCorrupt, match="entry 2"):
        j.verify()
    assert j.damage is not None and "entry 2" in str(j.damage)
    j.close()
