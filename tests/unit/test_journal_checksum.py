"""#496: every journal entry carries a chained checksum, written on append and verified when the file is opened
and on every read. A record changed into other valid JSON is refused by name; an older journal stays readable."""

from __future__ import annotations

import sqlite3

import pytest
from test_journal_kind_index import OLD_SCHEMA, old_journal

from kaibot.engine import failsafe
from kaibot.engine.journal import Journal, JournalCorrupt

FILL = {"run_id": 1, "activity": "x::1", "strategy": "s1", "symbol": "A", "side": "buy", "qty": 5, "px": 100.25}


def _raw(path, sql, *args):
    """Change the file behind the journal's back, as a damaged disk or a bad copy would (no trigger, no checksum)."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("drop trigger if exists entry_no_update")
    db.execute("drop trigger if exists entry_no_delete")
    db.execute(sql, args)
    db.execute("pragma wal_checkpoint(truncate)")
    db.close()


def _journal(tmp_path, n=3):
    path = tmp_path / "journal.sqlite"
    j = Journal(path)
    for i in range(n):
        j.append("note", {"i": i})
    j.append("fill", FILL)
    j.append("note", {"i": "last"})
    return path, j


def test_every_entry_of_a_new_journal_carries_a_checksum_chained_to_the_one_before(tmp_path):
    path, j = _journal(tmp_path)
    sums = [s for (s,) in j.db.execute("select sum from entry order by seq")]
    assert all(s and len(s) == 64 for s in sums) and len(set(sums)) == len(sums)
    assert j.meta("checksum_from") == "1"
    assert j.verify() == 5
    j.close()
    again = Journal(path)
    assert [e[3] for e in again.entries(kinds=("fill",))] == [FILL]
    assert again.append("note", {"i": "after reopen"}) == 6 and again.verify() == 6


def test_a_record_changed_into_other_valid_json_stops_the_open_and_names_the_entry(tmp_path):
    path, j = _journal(tmp_path)
    j.close()
    raw = bytearray(path.read_bytes())
    at = raw.rindex(b'"qty":5') + len(b'"qty":')
    raw[at] = ord("7")
    path.write_bytes(bytes(raw))
    with pytest.raises(JournalCorrupt, match=r"entry 4 .*checksum"):
        Journal(path)


@pytest.mark.parametrize("sql", [
    "update entry set body = replace(body, '100.25', '100.26') where seq = 4",
    "update entry set kind = 'mark' where seq = 4",
    "update entry set ts = '2020-01-01T00:00:00+00:00' where seq = 4",
    "update entry set sum = null where seq = 4",
])
def test_a_change_to_any_part_of_an_entry_is_noticed_on_the_next_read_too(tmp_path, sql):
    """The engine runs for days on one open journal: a read after the damage refuses it, not only the next start."""
    path, j = _journal(tmp_path)
    assert j.entries()
    _raw(path, sql)
    with pytest.raises(JournalCorrupt, match="entry 4"):
        j.entries(kinds=("fill", "mark"))
    with pytest.raises(JournalCorrupt, match="entry 4"):
        j.entries()
    assert [e[0] for e in j.entries(limit=3)] == [1, 2, 3]  # the entries before it still read


def test_an_entry_that_went_missing_breaks_the_chain(tmp_path):
    path, j = _journal(tmp_path)
    j.close()
    _raw(path, "delete from entry where seq = 2")
    with pytest.raises(JournalCorrupt, match="entry 3"):
        Journal(path)


def test_an_older_journal_opens_as_it_is_and_new_entries_are_protected_from_the_cut_over(tmp_path):
    path = tmp_path / "journal.sqlite"
    old_journal(path, 120)
    before = sqlite3.connect(path).execute("select seq, ts, kind, body from entry order by seq").fetchall()
    j = Journal(path)
    assert j.meta("checksum_from") == "121"
    assert j.db.execute("select seq, ts, kind, body from entry order by seq").fetchall() == before
    assert len(j.entries()) == 120 and j.verify() == 0  # accepted without a checksum: none to verify
    assert j.append("fill", FILL) == 121
    j.close()
    j = Journal(path)  # a second open changes nothing
    assert j.meta("checksum_from") == "121" and j.verify() == 1
    _raw(path, "update entry set body = replace(body, '\"qty\":5', '\"qty\":7') where seq = 121")
    with pytest.raises(JournalCorrupt, match="entry 121"):
        j.entries(kinds=("fill",))


def test_an_entry_without_a_checksum_after_the_cut_over_is_refused_until_the_owner_accepts_it(tmp_path):
    """What an older release writes after a roll-back: entries with no checksum. Not accepted silently."""
    path, j = _journal(tmp_path)
    j.close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("insert into entry (ts, kind, body) values ('2026-11-22T15:00:00+00:00', 'note', '{\"old\":1}')")
    db.close()
    with pytest.raises(JournalCorrupt, match=r"entry 6 has no checksum"):
        Journal(path)
    j = Journal(path, verify=False)
    assert j.reseal() == {"accepted": [6, 6], "checksum_from": 7}
    assert j.append("note", {"i": "new"}) == 7
    j.close()
    j = Journal(path)
    assert j.meta("checksum_from") == "7" and len(j.entries()) == 7
    _raw(path, "update entry set body = '{\"i\":9}' where seq = 2")  # entries that have a checksum keep it
    with pytest.raises(JournalCorrupt, match="entry 2"):
        j.entries()


def test_reseal_never_accepts_an_entry_whose_checksum_is_wrong(tmp_path):
    path, j = _journal(tmp_path)
    j.close()
    _raw(path, "update entry set body = '{\"i\":9}' where seq = 2")
    with pytest.raises(JournalCorrupt, match="entry 2"):
        Journal(path, verify=False).reseal()


def test_skipping_ahead_after_an_adopt_keeps_the_chain(tmp_path):
    path, j = _journal(tmp_path)
    j.skip_to(40)
    assert j.append("note", {"i": "after the gap"}) == 41
    assert j.verify() == 6
    j.close()
    assert Journal(path).last_seq() == 41


def test_the_in_memory_journal_of_research_is_checked_the_same_way():
    j = Journal()
    j.append("note", {"i": 1})
    assert j.verify() == 1 and j.entries()[0][3] == {"i": 1}


def test_the_watchdogs_read_only_journal_refuses_a_changed_entry_and_reads_an_older_file(tmp_path):
    path, j = _journal(tmp_path)
    j.close()
    ro = failsafe.ReadOnlyJournal(path)
    assert [e[3] for e in ro.entries(kinds=("fill",))] == [FILL]
    _raw(path, "update entry set body = replace(body, '\"qty\":5', '\"qty\":7') where seq = 4")
    with pytest.raises(JournalCorrupt, match="entry 4"):
        ro.entries(kinds=("fill",))
    old = tmp_path / "old.sqlite"
    db = sqlite3.connect(old, isolation_level=None)
    db.executescript(OLD_SCHEMA)
    db.execute("insert into entry (ts, kind, body) values ('t', 'note', '{\"i\":1}')")
    db.close()
    assert failsafe.ReadOnlyJournal(old).entries() == [(1, "t", "note", {"i": 1})]


def test_the_engine_exits_with_the_journal_code_on_a_corrupted_journal(tmp_path, caplog):
    from kaibot.engine import __main__ as M

    path, j = _journal(tmp_path)
    j.close()
    _raw(path, "update entry set body = replace(body, '\"qty\":5', '\"qty\":7') where seq = 4")
    with pytest.raises(SystemExit) as e:
        M.open_journal(path)
    assert e.value.code == M.EXIT_JOURNAL
    assert "entry 4" in caplog.text and "checksum" in caplog.text


def _schema(path, old, new):
    """#770: one damaged byte in the file's schema (`sqlite_master`), as the nightly fuzzing found it."""
    raw = path.read_bytes()
    assert raw.count(old) == 1
    path.write_bytes(raw.replace(old, new))


def test_a_damaged_schema_that_hides_the_checksums_is_refused_not_taken_for_an_older_journal_770(tmp_path, caplog):
    """#770: `body text not null,\\n  sum  text` with the newline damaged into `U` read as a column `U` and no `sum`:
    the journal took the file for one from before checksums, added an empty `sum` and accepted every entry
    unchecked (a changed time read back as written). Refused at open, exit code 4, the file left as it was."""
    from kaibot.engine import __main__ as M

    path, j = _journal(tmp_path)
    j.close()
    _raw(path, "update entry set ts = '2020-01-01T00:00:00+00:00' where seq = 2")
    _schema(path, b"body text not null,\n  sum  text", b"body text not null,U  sum  text")
    before = path.read_bytes()
    with pytest.raises(JournalCorrupt, match="entry table is not the journal's"):
        Journal(path)
    with pytest.raises(JournalCorrupt, match="entry table is not the journal's"):
        failsafe.ReadOnlyJournal(path).entries()
    with pytest.raises(SystemExit) as e:
        M.open_journal(path)
    assert e.value.code == M.EXIT_JOURNAL and "cut-over" in caplog.text
    assert path.read_bytes() == before  # nothing written to a refused file (no `alter table`, no schema)


def test_a_journal_whose_entry_table_lost_its_name_is_not_started_as_an_empty_one_770(tmp_path, caplog):
    """#770: with its triggers and index the damaged name is SQLite's own refusal (a malformed schema); without
    them the journal's (it has an identity). Either way exit code 4 at start, the file left as it was."""
    from kaibot.engine import __main__ as M

    for bare in (False, True):
        path, j = _journal(tmp_path / str(bare))
        j.close()
        db = sqlite3.connect(path, isolation_level=None)
        if bare:
            for name in ("entry_no_update", "entry_no_delete"):
                db.execute(f"drop trigger {name}")
            db.execute("drop index entry_kind_seq")
        db.execute("pragma writable_schema = 1")  # the table's name damaged in the schema
        db.execute("update sqlite_master set name = 'entrz', tbl_name = 'entrz', sql = replace(sql, 'TABLE entry', "
                   "'TABLE entrz') where name = 'entry'")
        db.close()
        before = path.read_bytes()
        with pytest.raises((JournalCorrupt, sqlite3.DatabaseError), match="no entry table|malformed"):
            Journal(path)
        with pytest.raises(SystemExit) as e:
            M.open_journal(path)
        assert e.value.code == M.EXIT_JOURNAL
        assert path.read_bytes() == before


def test_an_older_journal_is_still_taken_for_one_and_a_new_file_still_opens_770(tmp_path):
    path = tmp_path / "old.sqlite"
    old_journal(path, 5)
    assert Journal(path).meta("checksum_from") == "6"
    assert Journal(tmp_path / "new.sqlite").append("note", {"i": 1}) == 1
