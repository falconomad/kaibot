"""M10 1.24 (#449): the engine's journal upkeep (engine/__main__.py): with the market closed, once a day, every
checksum is checked and a snapshot is written when one is due; a snapshot that would not match is an event."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fakes import FakeStore

from kaibot.engine import __main__ as m
from kaibot.engine.journal import Journal, JournalCorrupt, live_store

NOW = datetime(2026, 3, 2, 21, 5, tzinfo=UTC)
CLOSED, OPEN = {"state": "market_closed"}, {"state": "running"}


def engine(tmp_path, clock=None):
    store = live_store(Journal(tmp_path / "j.sqlite", now=lambda: (clock or [NOW])[0]), FakeStore())
    store.insert("run", {"amount_usd": 1000.0, "status": "running"})
    return SimpleNamespace(store=store, _rid=lambda: store.active_run()["id"])


def test_a_snapshot_is_written_once_with_the_market_closed(tmp_path):
    clock = [NOW]
    eng, done = engine(tmp_path, clock), {}

    def at(t):
        clock[0] = t
        m.journal_upkeep(eng, CLOSED, t, done)

    m.journal_upkeep(eng, OPEN, NOW, done)
    m.journal_upkeep(eng, "error", NOW, done)
    assert eng.store.journal.last("snapshot") is None and not done  # never during a session
    m.journal_upkeep(eng, CLOSED, NOW, done)
    first = eng.store.journal.last("snapshot")
    assert first is not None and first[3]["reason"] == "first"
    n = eng.store.journal.last_seq()
    for k in range(1, 4):  # later the same day and the next days of the month: nothing more
        at(NOW + timedelta(minutes=k))
        at(NOW + timedelta(days=k))
    assert eng.store.journal.last_seq() == n
    at(NOW + timedelta(days=31))  # a new month
    assert eng.store.journal.last("snapshot")[3]["reason"] == "month"
    assert live_store(Journal(eng.store.journal.path), FakeStore()).state() == eng.store.state()


def test_a_snapshot_that_would_not_match_is_an_event_and_the_engine_goes_on(tmp_path, caplog):
    eng = engine(tmp_path)
    eng.store.tables["run"][0]["amount_usd"] = 5.0  # memory the journal does not hold
    m.journal_upkeep(eng, CLOSED, NOW, {})
    assert eng.store.journal.last("snapshot") is None
    ev = eng.store.tables["event"][-1]
    assert ev["type"] == "journal_snapshot_mismatch" and "tables: run" in ev["payload"]["error"]
    assert "no snapshot written" in caplog.text


def test_the_daily_check_stops_on_a_damaged_entry(tmp_path):
    import sqlite3

    eng = engine(tmp_path)
    db = sqlite3.connect(eng.store.journal.path, isolation_level=None)
    db.execute("drop trigger if exists entry_no_update")
    db.execute("update entry set body = replace(body, '1000.0', '9000.0') where seq = 1")
    db.close()
    with pytest.raises(JournalCorrupt, match="entry 1 "):  # the main loop stops the engine on it (#496)
        m.journal_upkeep(eng, CLOSED, NOW, {})
