"""M10 1.24 (#449): the journal's read cache and keyed index, the store's snapshot, and the minute rows that the
live store no longer keeps in memory. Decisions do not change: every read gives what a read of the file gives,
and a journal written with snapshots holds the same entries as one written without, the snapshots apart."""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakeStore

from kaibot.engine import journal as J
from kaibot.engine.journal import (
    Journal,
    JournalCorrupt,
    JournalStore,
    Replicator,
    SnapshotMismatch,
    live_store,
    state_digest,
)

ROOT = Path(__file__).resolve().parents[1]


class Tick:
    """A clock that moves a second per reading: two journals fed the same calls get the same times."""

    def __init__(self):
        self.t = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def disk(j: Journal, kinds=None, after=0, limit=None) -> list:
    """What the file says, read past the cache (a second connection, every checksum checked)."""
    q, args = f"select {J.SUM_COLUMNS} from entry where seq > ?", [after]
    if kinds:
        q += f" and kind in ({','.join('?' * len(kinds))})"
        args += list(kinds)
    q += " order by seq" + (f" limit {limit}" if limit else "")
    return J.checked(j.db.execute(q, args).fetchall(), 1)


def feed(j: Journal, n: int = 40) -> None:
    for i in range(n):
        j.append("md_decision", {"strategy": "a" if i % 2 else "b", "session": f"2026-03-{i % 9 + 1:02d}", "i": i})
        j.append("md_book_peak", {"peak": 100.0 + i, "run_id": 1})
        j.append("row", {"op": "insert", "table": "ledger", "row": {"id": i + 1, "cash": 1.5 * i}})


# ---------------------------------------------------------------- the read cache
@pytest.mark.parametrize("path", [":memory:", "file"])
def test_cached_reads_equal_the_file_for_every_shape_of_read(tmp_path, path):
    j = Journal(tmp_path / "j.sqlite" if path == "file" else ":memory:")
    feed(j)
    shapes = [(("md_decision",), 0, None), (("md_book_peak", "md_decision"), 0, None), (("md_decision",), 57, None),
              (("md_decision", "md_book_peak"), 31, 7), (("md_book_peak",), 0, 3), (("nothing",), 0, None),
              (("md_decision", "row"), 10, None), (("md_decision", "md_decision"), 0, None)]
    for _ in range(3):  # the first read of a kind goes to the file, the later ones to the cache
        for kinds, after, limit in shapes:
            assert j.entries(after=after, kinds=kinds, limit=limit) == disk(j, kinds, after, limit)
        j.append("md_decision", {"strategy": "a", "session": "2026-03-31", "i": "new"})
        j.append("md_book_peak", {"peak": 1.0, "when": datetime(2026, 3, 2, tzinfo=UTC)})  # a datetime, as JSON
    assert "md_decision" in j._cache and "row" not in j._cache and "snapshot" not in j._cache


def test_a_read_is_the_callers_own_copy(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    feed(j, 3)
    j.entries(kinds=("md_decision",))
    got = j.entries(kinds=("md_decision",))
    got[0][3]["strategy"] = "changed"
    got[0][3].setdefault("list", []).append(1)
    j.find("md_decision", strategy="b")[0]["i"] = "changed"
    assert j.entries(kinds=("md_decision",)) == disk(j, ("md_decision",))
    assert j.find("md_decision", strategy="b")[0]["i"] == 0


def test_a_kind_read_once_is_not_kept(tmp_path):
    """The folds a start makes once (ten years of research orders) do not stay in memory."""
    j = Journal(tmp_path / "j.sqlite")
    feed(j, 3)
    j.entries(kinds=("md_decision", "md_book_peak"))
    assert not j._cache
    j.entries(kinds=("md_decision",))
    assert set(j._cache) == {"md_decision"}


def test_an_entry_another_process_appends_is_seen_at_the_next_read(tmp_path):
    """The owner's `reconcile --accept` writes to the journal of a running engine."""
    path = tmp_path / "j.sqlite"
    eng = Journal(path)
    feed(eng, 3)
    for _ in range(2):
        assert len(eng.entries(kinds=("md_decision",))) == 3
    assert len(eng.find("md_decision", strategy="b")) == 2
    other = Journal(path)
    other.append("md_decision", {"strategy": "b", "session": "2026-03-09", "by": "owner"})
    other.close()
    assert [b.get("by") for _, _, _, b in eng.entries(kinds=("md_decision",))][-1] == "owner"
    assert eng.find("md_decision", strategy="b")[-1]["by"] == "owner"
    eng.append("md_decision", {"strategy": "b", "session": "2026-03-10", "by": "engine"})  # the chain goes on
    assert [b.get("by") for b in eng.find("md_decision", strategy="b")][-2:] == ["owner", "engine"]
    assert eng.entries(kinds=("md_decision",)) == disk(eng, ("md_decision",))
    eng.close()
    assert Journal(path).verify() == 11


def test_an_append_right_after_another_process_wrote_does_not_hide_its_entry(tmp_path):
    path = tmp_path / "j.sqlite"
    eng = Journal(path)
    feed(eng, 2)
    eng.entries(kinds=("md_decision",))
    eng.entries(kinds=("md_decision",))
    other = Journal(path)
    other.append("md_decision", {"by": "owner"})
    other.close()
    eng.append("md_decision", {"by": "engine"})  # no read in between
    assert [b.get("by") for _, _, _, b in eng.entries(kinds=("md_decision",))][-2:] == ["owner", "engine"]


@pytest.mark.parametrize("read", ["entries", "find"])
def test_a_cached_entry_changed_in_the_file_is_refused_at_the_next_read(tmp_path, read):
    """#496 stays: a change made to the file while the engine runs is noticed by the next read, cache or not."""
    path = tmp_path / "j.sqlite"
    j = Journal(path)
    feed(j, 3)
    for _ in range(2):
        j.entries(kinds=("md_decision",))
        j.find("md_decision", strategy="b")
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("drop trigger if exists entry_no_update")
    db.execute("update entry set body = replace(body, '\"i\":0', '\"i\":5') where seq = 1")
    db.close()
    with pytest.raises(JournalCorrupt, match="entry 1 "):
        j.entries(kinds=("md_decision",)) if read == "entries" else j.find("md_decision", strategy="b")


def test_find_answers_as_a_filter_of_the_kind_and_follows_appends(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    feed(j)
    j.append("md_decision", {"strategy": ["a", "list"], "session": None})
    j.append("md_decision", {"session": "2026-03-01"})  # no strategy at all

    def scan(**eq):
        return [b for _, _, _, b in disk(j, ("md_decision",)) if all(b.get(k) == v for k, v in eq.items())]

    for eq in ({"strategy": "a"}, {"strategy": "a", "session": "2026-03-02"}, {"session": "2026-03-01"},
               {"strategy": None}, {"strategy": "zz"}, {"i": 7}, {"i": 7.0}, {"strategy": ["a", "list"]},
               {"session": None, "strategy": ["a", "list"]}):
        assert j.find("md_decision", **eq) == scan(**eq), eq
    j.append("md_decision", {"strategy": "a", "session": "2026-03-02", "i": "late"})
    assert j.find("md_decision", strategy="a", session="2026-03-02")[-1]["i"] == "late"
    assert j.find("md_decision", strategy="a", session="2026-03-02") == scan(strategy="a", session="2026-03-02")
    assert j.find("row", table="ledger") == [b for _, _, _, b in disk(j, ("row",))]  # a bulk kind: a plain read
    assert j.last("md_decision")[3]["i"] == "late" and j.last("nothing") is None


# ---------------------------------------------------------------- the store's snapshot
def run(store: JournalStore, snapshots: bool) -> None:
    """A stretch of an engine's writes: inserts, updates, upserts, a command applied, minute rows."""
    r = store.insert("run", {"amount_usd": 1000.0, "status": "running"})
    for d in range(1, 7):
        day = f"2026-03-{d:02d}"
        for m in range(5):
            store.insert("ledger", {"run_id": r["id"], "ts": f"{day}T14:{30 + m}:07+00:00", "cash": 10.0 * m,
                                    "multi_day": {"positions": [{"symbol": "A", "mark": 1.0 + m}]}})
        t = store.insert("trade", {"run_id": r["id"], "symbol": "A", "qty": d, "exit_ts": None})
        store.update("trade", {"id": t["id"]}, {"exit_ts": f"{day}T20:00:00+00:00"})
        store.upsert("ledger_position", {"run_id": r["id"], "session": day, "strategy": "s", "symbol": "A",
                                         "qty": d}, "run_id,session,strategy,symbol")
        store.upsert("ledger_position", {"run_id": r["id"], "session": day, "strategy": "s", "symbol": "A",
                                         "qty": d + 0.5}, "run_id,session,strategy,symbol")
        store.insert("daily_result", {"run_id": r["id"], "session": day, "pnl_venue": 1.0, "pnl_shadow": 0.5})
        store.mark_applied(100 + d)
        store.update("run", {"id": r["id"]}, {"peak_equity": 1000.0 + d})
        if snapshots and d in (2, 4):
            store.snapshot("test")


def fresh(tmp_path, name, live=True):
    j = Journal(tmp_path / name, now=Tick())
    j.db.execute("update meta set v = '0000000000000000000000000000abcd' where k = 'journal_id'")
    j.db.execute("update meta set v = '9' where k = 'epoch'")
    j.close()
    j = Journal(tmp_path / name, now=Tick())
    return live_store(j, FakeStore()) if live else JournalStore(j)


@pytest.fixture
def fixed_now(monkeypatch):
    """`mark_applied` reads the wall clock; two runs of the same inputs must write the same text."""
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 3, 2, 15, 0, tzinfo=tz)

    monkeypatch.setattr(J, "datetime", _DT)


def test_a_journal_with_snapshots_holds_the_same_entries_as_one_without(tmp_path, fixed_now):
    a, b = fresh(tmp_path, "a.sqlite"), fresh(tmp_path, "b.sqlite")
    run(a, snapshots=False)
    run(b, snapshots=True)
    ea = [(k, body) for _, _, k, body in a.journal.db.execute("select seq, ts, kind, body from entry order by seq")]
    eb = [(k, body) for _, _, k, body in b.journal.db.execute("select seq, ts, kind, body from entry order by seq")]
    assert [e for e in eb if e[0] != "snapshot"] == ea  # byte for byte, the snapshot entries apart
    assert sum(k == "snapshot" for k, _ in eb) == 2 and b.journal.verify() == len(eb)
    assert a.state() == b.state()


@pytest.mark.parametrize("live", [True, False])
def test_a_start_from_a_snapshot_gives_the_state_of_a_full_fold(tmp_path, fixed_now, live):
    st = fresh(tmp_path, "j.sqlite", live)
    run(st, snapshots=True)
    want, path = st.state(), st.journal.path
    st.journal.close()
    kw = {"keep": st._keep, "live": live}
    j = Journal(path)
    seen = []
    orig = j.stream
    j.stream = lambda after=0, kinds=None: (seen.append(after), orig(after, kinds))[1]
    snap = JournalStore(j, None, **kw)
    full = JournalStore(Journal(path), None, snapshots=False, **kw)
    assert snap.snapshot_from is not None and full.snapshot_from is None and snap.snapshot_problem is None
    assert seen == [j.last("snapshot")[3]["upto"]] and seen[0] > 0  # only the entries after it were folded
    assert snap.state() == full.state() == want
    assert state_digest(J.canon(snap.state())) == state_digest(J.canon(full.state()))
    # and both go on the same way: the same ids, the same rows
    for s in (snap, full):
        assert s.insert("trade", {"run_id": 1, "symbol": "B", "qty": 1, "exit_ts": None})["id"] == want["max_id"] + 1
        assert s.applied == {str(100 + d) for d in range(1, 7)}
    assert snap.state() == full.state()


def test_no_snapshot_is_written_when_memory_differs_from_the_journal(tmp_path, fixed_now):
    st = fresh(tmp_path, "j.sqlite")
    run(st, snapshots=False)
    n = st.journal.last_seq()
    st.tables["trade"][0]["qty"] = 99  # state in memory that the journal does not hold
    with pytest.raises(SnapshotMismatch, match=r"entry \d+ in tables \(tables: trade\)"):
        st.snapshot("test")
    assert st.journal.last_seq() == n and st.journal.last("snapshot") is None


@pytest.mark.parametrize("damage", ["digest", "version", "upto", "settings"])
def test_a_snapshot_that_cannot_be_trusted_is_not_used_and_everything_is_folded(tmp_path, fixed_now, damage):
    st = fresh(tmp_path, "j.sqlite")
    run(st, snapshots=False)
    want = st.state()
    got = st.snapshot("test")
    body = st.journal.last("snapshot")[3]
    if damage == "digest":
        body["state"]["tables"]["trade"][0]["qty"] = 99
    elif damage == "version":
        body["v"] = 99
    elif damage == "upto":
        body["upto"] = got["seq"] + 5
    st.journal.append("snapshot", body)  # a later snapshot entry with a valid checksum and a bad content
    path = st.journal.path
    st.journal.close()
    again = live_store(Journal(path), FakeStore()) if damage != "settings" else JournalStore(Journal(path), live=True)
    assert again.snapshot_from is None and "not used" in again.snapshot_problem
    want["dropped"] = again.state()["dropped"]
    if damage == "settings":  # this store keeps the minute rows: they come from the journal, all of them
        assert len(again.tables["ledger"]) == 30
        want["tables"]["ledger"] = again.tables["ledger"]
    assert again.state() == want


def test_when_a_snapshot_is_due(tmp_path, fixed_now):
    st = fresh(tmp_path, "j.sqlite")
    t = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
    assert st.snapshot_due(t) is None  # an empty journal
    r = st.insert("run", {"amount_usd": 1.0, "status": "running"})
    assert st.snapshot_due(t) == "first"
    st.snapshot("first")
    assert st.snapshot_due(t) is None
    assert st.snapshot_due(datetime(2026, 4, 1, 0, 30, tzinfo=UTC)) == "month"
    st.update("run", {"id": r["id"]}, {"status": "ended"})
    assert st.snapshot_due(t) is None  # no active run: nothing new to cover
    st.insert("run", {"amount_usd": 2.0, "status": "running"})
    assert st.snapshot_due(t) == "run"
    st.snapshot("run")
    assert st.snapshot_due(t) is None


# ---------------------------------------------------------------- minute rows
def test_the_live_store_keeps_no_minute_rows_and_loses_none(tmp_path, fixed_now):
    remote = FakeStore()
    j = Journal(tmp_path / "j.sqlite")
    st = live_store(j, remote)
    keeps = JournalStore(Journal(), FakeStore(), live=True)
    for s in (st, keeps):
        run(s, snapshots=False)
    assert "ledger" not in st.tables and len(keeps.tables["ledger"]) == 30
    row = st.insert("ledger", {"run_id": 1, "ts": "2026-03-09T14:30:07+00:00", "cash": 1.0})
    assert row["cash"] == 1.0 and row["id"] == st._max_id  # the caller gets its row and its id back
    # a read of the table still answers, from the journal
    assert len(st.select("ledger", {})) == 31
    assert st.select("ledger", {"ts": "eq.2026-03-09T14:30:07+00:00"}) == [row]
    assert [r["cash"] for r in st.select("ledger", {"order": "id.desc", "limit": "2"})] == [1.0, 40.0]
    # Supabase gets every one of them, from the journal
    Replicator(j, remote, schema=J.SCHEMA_0018).pump()
    assert len(remote.tables["ledger"]) == 31 and remote.tables["ledger"][-1]["cash"] == 1.0
    # ids are never reused after a restart, with or without a snapshot
    top = st._max_id
    st.snapshot("test")
    for snaps in (True, False):
        again = live_store(Journal(j.path), FakeStore(), snapshots=snaps)
        assert again._max_id == top and "ledger" not in again.tables
        again.journal.close()
    # everything else is what a store that keeps them has
    a, b = st.state(), keeps.state()
    assert {t: len(r) for t, r in a["tables"].items()} == {t: len(r) for t, r in b["tables"].items() if t != "ledger"}


# ---------------------------------------------------------------- the benchmark's journal
def bench():
    spec = importlib.util.spec_from_file_location("bench_journal", ROOT / "scripts" / "bench_journal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_synthetic_journal_is_a_valid_journal_and_a_snapshot_start_folds_only_its_tail(tmp_path):
    b = bench()
    path = tmp_path / "journal.sqlite"
    size = b.build(path, n_sessions=3, positions=4)
    first = path.read_bytes()
    b.build(path, n_sessions=3, positions=4)
    assert path.read_bytes() == first  # the same arguments: the same file
    j = Journal(path)
    assert j.verify() == size["entries"] == j.last_seq()
    st = live_store(j, FakeStore())
    assert "ledger" not in st.tables and len(st.tables["ledger_position"]) == 12
    assert len(JournalStore(j, FakeStore(), live=True).tables["ledger"]) == 3 * 390
    st.snapshot("bench")
    j.append("row", {"op": "insert", "table": "halt", "row": {"id": st._max_id + 1, "kind": "x"}})
    again = live_store(j, FakeStore())
    assert again.snapshot_from == size["entries"] + 1 and len(again.tables["halt"]) == 1
    assert again.state() == live_store(j, FakeStore(), snapshots=False).state()
    out = b.measure(path, ticks=2)
    assert out["rows_in_memory"].get("ledger") is None and out["tick_send_reads"] == 17
    assert out["snapshot"]["written"] is None  # one is there already, of this month


def test_a_fold_reads_the_journal_a_chunk_at_a_time(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    feed(j, 25)
    for chunk in (1, 7, 75, 76, 500):
        assert list(j.stream(chunk=chunk)) == j.entries()
        assert list(j.stream(after=9, kinds=("row", "md_decision"), chunk=chunk)) == \
            j.entries(after=9, kinds=("row", "md_decision"))
