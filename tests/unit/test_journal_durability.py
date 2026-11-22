"""M9 ticket 1.18 (#443): the journal's durability settings are asserted at start, and the engine fails closed.

The journal promises "durable when append returns": write-ahead mode with synchronous=FULL, and on macOS
`fullfsync`, without which fsync does not reach the disk. SQLite does not raise when a pragma is not taken (a file
system that cannot do write-ahead mode answers `delete`), so a journal that only sets them can run without them."""

from __future__ import annotations

import sqlite3

import pytest

from kaibot.engine import journal as J
from kaibot.engine.journal import DurabilityError, Journal


def _connection_that_ignores(*pragmas: str, instead: str = "select 1"):
    """A SQLite connection that silently does not take the named pragmas (what a file system or a build that cannot
    honour them does: no error, the old value stays); `instead` is what runs in their place."""

    class Conn(sqlite3.Connection):
        def execute(self, sql, *a):
            if any(sql.strip().lower().startswith(f"pragma {p}=") for p in pragmas):
                return super().execute(instead)
            return super().execute(sql, *a)

    real = sqlite3.connect
    return lambda *a, **k: real(*a, factory=Conn, **k)


def test_a_file_journal_runs_in_wal_mode_with_full_sync(tmp_path):
    j = Journal(tmp_path / "journal.sqlite")
    assert j.durability()["journal_mode"] == "wal"
    assert j.durability()["synchronous"] == 2  # FULL
    j.close()


def test_a_journal_that_did_not_get_write_ahead_mode_refuses_to_open(tmp_path, monkeypatch):
    monkeypatch.setattr(J.sqlite3, "connect", _connection_that_ignores("journal_mode"))
    with pytest.raises(DurabilityError, match="journal_mode"):
        Journal(tmp_path / "journal.sqlite")


def test_a_journal_that_did_not_get_full_sync_refuses_to_open(tmp_path, monkeypatch):
    monkeypatch.setattr(J.sqlite3, "connect",
                        _connection_that_ignores("synchronous", instead="pragma synchronous = normal"))
    with pytest.raises(DurabilityError, match="synchronous"):
        Journal(tmp_path / "journal.sqlite")


def test_the_in_memory_journal_of_research_and_tests_still_opens():
    j = Journal()
    assert j.durability()["synchronous"] == 2
    j.append("x", {"a": 1})


def test_on_macos_fullfsync_is_switched_on_and_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(J.sys, "platform", "darwin")
    j = Journal(tmp_path / "journal.sqlite")
    d = j.durability()
    assert d["fullfsync"] == 1 and d["checkpoint_fullfsync"] == 1
    j.close()
    monkeypatch.setattr(J.sqlite3, "connect", _connection_that_ignores("fullfsync"))
    with pytest.raises(DurabilityError, match="fullfsync"):
        Journal(tmp_path / "other.sqlite")


def test_off_macos_fullfsync_is_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr(J.sys, "platform", "linux")
    monkeypatch.setattr(J.sqlite3, "connect", _connection_that_ignores("fullfsync", "checkpoint_fullfsync"))
    Journal(tmp_path / "journal.sqlite").close()


def test_the_engine_exits_with_the_journal_code_and_builds_nothing(tmp_path, monkeypatch, caplog):
    """Start-up with a wrong setting exits (code 4, the journal's), before a broker or a lease is touched."""
    from kaibot.engine import __main__ as M

    monkeypatch.setattr(J.sqlite3, "connect", _connection_that_ignores("journal_mode"))
    with pytest.raises(SystemExit) as e:
        M.open_journal(tmp_path / "journal.sqlite")
    assert e.value.code == M.EXIT_JOURNAL
    assert "journal_mode" in caplog.text
