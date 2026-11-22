"""#543: one damaged byte in the journal's kind index (`entry_kind_seq`) hid an entry from every read by kind while
the journal still opened and verified: `verify()` read the table, the reads by kind read the index. Written before
the fix. Now the file's structure is checked (`pragma integrity_check`, which compares every index with its table)
whenever the journal is opened, and a finding stops the engine at start with exit code 4, as a failed checksum does.
The watchdog's read-only reader does not use the index at all. The owner repairs a damaged index with
`python -m kaibot.engine.journal --reindex`, with the engine stopped (docs/ops/ledger.md, "Checksums").
The fuzz target's case is tests/fuzz/test_fuzz_targets.py::test_a_damaged_kind_index_does_not_hide_entries_543."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kaibot.engine import __main__ as engine_main
from kaibot.engine import journal as jmod
from kaibot.engine.failsafe import ReadOnlyJournal
from kaibot.engine.journal import Journal, JournalCorrupt


def _journal(tmp_path: Path, n: int = 3) -> Path:
    p = tmp_path / "journal.sqlite"
    j = Journal(p)
    for i in range(n):
        j.append("fill", {"coid": f"c{i}"})
    j.append("minute", {"m": 1})
    j.close()
    return p


def _damage_index(p: Path) -> None:
    """'fill' -> 'fxll' in one entry of the kind index only (the issue's minimal case)."""
    db = sqlite3.connect(p)
    root = db.execute("select rootpage from sqlite_master where name='entry_kind_seq'").fetchone()[0]
    psize = db.execute("pragma page_size").fetchone()[0]
    db.close()
    raw = bytearray(p.read_bytes())
    off = (root - 1) * psize
    raw[off + raw[off:off + psize].find(b"fill") + 1] = ord("x")
    p.write_bytes(bytes(raw))


def test_a_damaged_kind_index_refuses_the_journal_at_open(tmp_path):
    p = _journal(tmp_path)
    _damage_index(p)
    with pytest.raises(JournalCorrupt, match="entry_kind_seq"):
        Journal(p)


def test_the_engine_exits_with_code_4_on_a_damaged_index(tmp_path):
    p = _journal(tmp_path)
    _damage_index(p)
    with pytest.raises(SystemExit) as e:
        engine_main.open_journal(p)
    assert e.value.code == engine_main.EXIT_JOURNAL == 4


def test_a_sound_journal_still_opens_and_reads_by_kind(tmp_path):
    p = _journal(tmp_path)
    j = Journal(p)
    try:
        assert len(j.entries(kinds=("fill",))) == j.count(("fill",)) == 3
    finally:
        j.close()


def test_the_watchdogs_reader_does_not_trust_the_index(tmp_path):
    """The watchdog reads the book every poll without opening a `Journal` (read-only, no check at open): it must
    not lose the entry the index hides."""
    p = _journal(tmp_path)
    _damage_index(p)
    assert len(ReadOnlyJournal(p).entries(kinds=("fill",))) == 3


def test_reindex_repairs_a_damaged_index_and_the_journal_opens_again(tmp_path, capsys, monkeypatch):
    p = _journal(tmp_path)
    _damage_index(p)
    assert jmod.main(["--path", str(p), "--reindex"]) == 0
    assert '"reindexed"' in capsys.readouterr().out
    j = Journal(p)
    try:
        assert len(j.entries(kinds=("fill",))) == j.count(("fill",)) == 3
    finally:
        j.close()


def test_reindex_never_accepts_a_changed_entry(tmp_path, capsys):
    """REINDEX rebuilds an index from the table; an entry whose checksum fails is still refused."""
    p = _journal(tmp_path)
    db = sqlite3.connect(p)
    db.execute("drop trigger entry_no_update")
    db.execute("update entry set body = '{\"coid\": \"forged\"}' where seq = 2")
    db.commit()
    db.close()
    assert jmod.main(["--path", str(p), "--reindex"]) == 2
    assert "checksum" in capsys.readouterr().err
    with pytest.raises(JournalCorrupt):
        Journal(p)
