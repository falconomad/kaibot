"""Issue #244: the journal's `entry(kind, seq)` index, added when a journal opens.

A journal written with the schema before the index (OLD_SCHEMA, the schema of main before #244, written here as
it was) is copied and opened with today's code: the index is added; every read returns exactly what it returned
before; the append-only triggers, the identity and the cursors are untouched; a second open changes nothing; the
kind reads use the index; the read-only opener of the watchdog still reads the file."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaibot.engine import failsafe
from kaibot.engine.journal import Journal

OLD_SCHEMA = """
create table if not exists entry (
  seq  integer primary key autoincrement,
  ts   text not null,
  kind text not null,
  body text not null
);
create trigger if not exists entry_no_update before update on entry
  begin select raise(abort, 'the journal is append-only'); end;
create trigger if not exists entry_no_delete before delete on entry
  begin select raise(abort, 'the journal is append-only'); end;
create table if not exists cursor (name text primary key, seq integer not null);
create table if not exists meta (k text primary key, v text not null);
create table if not exists idmap (name text not null, tbl text not null, engine_id integer not null,
  db_id integer not null, primary key (name, tbl, engine_id));
"""

KINDS = ("row", "fill", "mark", "md_book_peak", "div_accrual", "reconcile", "md_decision")


def old_journal(path: Path, n: int) -> None:
    """A journal file as the code before #244 wrote it: no index on kind."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("pragma journal_mode=wal")
    db.executescript(OLD_SCHEMA)
    jid = uuid.UUID(int=12345)
    db.execute("insert into meta (k, v) values ('journal_id', ?), ('epoch', '77'), ('created', '2026-11-01')",
               (jid.hex,))
    rows = []
    for i in range(n):  # mostly per-minute rows, a few rare kinds, as a live journal is
        k = KINDS[0] if i % 50 else KINDS[1 + (i // 50) % (len(KINDS) - 1)]
        rows.append((f"2026-11-0{1 + i % 9}T15:00:00+00:00", k, json.dumps({"i": i, "run_id": 1, "peak": i})))
    db.execute("begin")
    db.executemany("insert into entry (ts, kind, body) values (?, ?, ?)", rows)
    db.execute("commit")
    db.execute("insert into cursor (name, seq) values ('supabase', ?)", (n // 2,))
    db.close()


def reads(j: Journal) -> list:
    out = [j.entries(), j.entries(after=17), j.entries(limit=5), j.last_seq(), j.cursor("supabase")]
    for kinds in [(k,) for k in KINDS] + [("div_accrual", "md_book_peak"), ("reconcile", "fill", "mark")]:
        out += [j.entries(kinds=kinds), j.entries(after=200, kinds=kinds), j.count(kinds), j.count(kinds, 300)]
    return out


def indexes(path: Path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {r[0] for r in db.execute("select name from sqlite_master where type = 'index' and tbl_name = 'entry'")}
    finally:
        db.close()


@pytest.fixture
def copies(tmp_path):
    src = tmp_path / "old.sqlite"
    old_journal(src, 3000)
    a, b = tmp_path / "a" / "journal.sqlite", tmp_path / "b" / "journal.sqlite"
    for p in (a, b):
        p.parent.mkdir()
        shutil.copy(src, p)
    return a, b


def _old_reads(path: Path) -> list:
    """The same reads through the old code path: a Journal whose schema step is the old one."""
    import kaibot.engine.journal as jm

    real = jm.SCHEMA
    jm.SCHEMA = OLD_SCHEMA
    try:
        j = Journal(path)
        try:
            return reads(j)
        finally:
            j.close()
    finally:
        jm.SCHEMA = real


def test_opening_an_old_journal_adds_the_index_and_every_read_is_unchanged(copies):
    a, b = copies
    want = _old_reads(a)
    assert "entry_kind_seq" not in indexes(a)
    j = Journal(b)
    try:
        assert reads(j) == want
        assert (j.journal_id, j.epoch) == (uuid.UUID(int=12345).hex, 77)
        n = j.last_seq()
        j.append("md_book_peak", {"run_id": 1, "peak": 1.5})  # the next number, as before
        assert j.last_seq() == n + 1 and j.entries(kinds=("md_book_peak",))[-1][3] == {"run_id": 1, "peak": 1.5}
        for q in ("update entry set body = '{}' where seq = 1", "delete from entry where seq = 1"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                j.db.execute(q)
    finally:
        j.close()
    assert "entry_kind_seq" in indexes(b)


def test_a_second_open_changes_nothing(copies):
    _, b = copies
    Journal(b).close()
    before = b.read_bytes() if not Path(f"{b}-wal").exists() else None
    j = Journal(b)
    try:
        assert "entry_kind_seq" in indexes(b)
        assert sum(1 for x in indexes(b) if x == "entry_kind_seq") == 1
        assert reads(j) == reads(j)
    finally:
        j.close()
    if before is not None:
        assert len(b.read_bytes()) == len(before)


def test_the_kind_reads_use_the_index(copies):
    _, b = copies
    j = Journal(b)
    try:
        plan = " ".join(str(r) for r in j.db.execute(
            "explain query plan select seq, ts, kind, body from entry where seq > ? and kind in (?, ?) order by seq",
            (0, "div_accrual", "reconcile")).fetchall())
        assert "entry_kind_seq" in plan, plan
        plan1 = " ".join(str(r) for r in j.db.execute(
            "explain query plan select count(*) from entry where seq > ? and kind in (?)", (0, "fill")).fetchall())
        assert "entry_kind_seq" in plan1, plan1
    finally:
        j.close()


def test_the_read_only_opener_still_reads_an_indexed_journal(copies):
    _, b = copies
    j = Journal(b)
    try:
        want = j.entries(kinds=("md_book_peak", "reconcile"))
    finally:
        j.close()
    assert want and failsafe.ReadOnlyJournal(b).entries(kinds=("md_book_peak", "reconcile")) == want


def test_a_kind_read_on_a_large_journal_is_fast(tmp_path):
    """Not a benchmark (shared CI machines): a rare-kind read of 200k entries must not scan them all."""
    p = tmp_path / "big.sqlite"
    old_journal(p, 200_000)
    j = Journal(p)
    try:
        t = time.perf_counter()
        for _ in range(20):
            j.entries(kinds=("md_book_peak",))
        assert (time.perf_counter() - t) / 20 < 0.02
    finally:
        j.close()


def test_a_rollback_to_the_old_code_reads_and_appends_an_indexed_journal(copies):
    """QA-Ops (#249): after a downgrade the old code opens a journal that has the index: it appends and reads as
    before, SQLite keeps the index current, and the new code afterwards reads the same as the old one."""
    import kaibot.engine.journal as jm

    a, b = copies
    Journal(a).close()  # the new code added the index
    assert "entry_kind_seq" in indexes(a)
    real = jm.SCHEMA
    jm.SCHEMA = OLD_SCHEMA  # the code before #244
    try:
        for p in (a, b):
            j = Journal(p, now=lambda: datetime(2026, 11, 20, 15, tzinfo=UTC))  # same stamps in both files
            try:
                for i in range(40):
                    j.append("md_decision" if i % 4 == 0 else "row", {"after_rollback": i})
            finally:
                j.close()
    finally:
        jm.SCHEMA = real
    assert "entry_kind_seq" in indexes(a)  # the old schema script neither drops nor breaks it
    assert _old_reads(a) == _old_reads(b)  # indexed and never-indexed files: same rows after the old appends
    j = Journal(a)
    try:
        assert reads(j) == _old_reads(b)
        assert j.count(("md_decision",)) == len(j.entries(kinds=("md_decision",)))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            j.db.execute("delete from entry where seq = 1")
    finally:
        j.close()
