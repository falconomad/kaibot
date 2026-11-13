"""#165: follow-ups from the QA-Ops re-review of PR #160 (N1, N2). Ids and integers compare exactly in `same_row`;
only a unique violation (23505) is a duplicate, a foreign-key 409 (23503) is a real error."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_journal import Remote
from test_journal_pre0018 import PreRemote

from kaibot.engine.journal import EPOCHS, ID_SPAN, Journal, JournalStore, Replicator, _duplicate, same_row
from kaibot.engine.store import StoreError

FK_409 = ('POST {t} -> 409: {{"code":"23503","details":"Key (order_id)=(4294967306) is not present in table '
          '\\"order\\".","message":"insert or update on table \\"{t}\\" violates foreign key constraint"}}')


# ---------------------------------------------------------------- N1: ids and integers exactly
def test_reviewer_repro_two_ids_in_one_epoch_are_different_rows():
    base = 1_000_000 * ID_SPAN
    assert not same_row({"jseq": base + 1, "order_id": base + 10}, {"jseq": base + 1, "order_id": base + 11})


@given(epoch=st.integers(1, EPOCHS), n=st.integers(0, ID_SPAN - 2), d=st.integers(1, 1000))
def test_ints_never_match_a_different_int(epoch, n, d):
    a = epoch * ID_SPAN + n
    assert same_row({"id": a, "x": a}, {"id": a, "x": a})
    assert not same_row({"x": a}, {"x": a + d})
    assert not same_row({"x": a}, {"x": str(a + d)}), "PostgREST may hand a bigint back as text"
    assert same_row({"x": a}, {"x": str(a)})


def test_small_ints_are_exact_too():
    assert not same_row({"qty": 3}, {"qty": 4})
    assert not same_row({"qty": 0}, {"qty": 0.004}), "an int is not rounded, so no tolerance"


def test_an_int_still_matches_the_same_number_as_a_numeric_column_returns_it():
    assert same_row({"qty": 3}, {"qty": 3.0})
    assert same_row({"qty": 3}, {"qty": "3.00"})
    assert not same_row({"qty": 3}, {"qty": "3.01"})
    assert not same_row({"qty": 3}, {"qty": "abc"})


def test_floats_keep_the_cent_tolerance():
    assert same_row({"px": 10.004}, {"px": 10.0})
    assert same_row({"px": 10.004}, {"px": "10.00"})
    assert not same_row({"px": 10.01}, {"px": 10.0})
    assert same_row({"pos": {"A": [1, 2.001]}}, {"pos": {"A": [1, 2]}})
    assert not same_row({"pos": {"A": [base := 5 * ID_SPAN + 1]}}, {"pos": {"A": [base + 1]}})


def test_bools_are_not_numbers():
    assert same_row({"ok": True}, {"ok": True})
    assert same_row({"ok": False}, {"ok": "false"})
    assert not same_row({"ok": True}, {"ok": False})


# ---------------------------------------------------------------- N2: only unique violations are duplicates
@pytest.mark.parametrize("text,dup", [
    ('POST x -> 409: {"code":"23505","message":"duplicate key value violates unique constraint"}', True),
    ("duplicate key value violates unique constraint \"order_client_order_id_key\"", True),
    (FK_409.format(t="fill"), False),
    ("POST x -> 409: conflict", False),
    ("POST x: <urlopen error [Errno 111] Connection refused>", False),
])
def test_duplicate_means_a_unique_violation_only(text, dup):
    assert _duplicate(StoreError(text)) is dup


class FkRemote(Remote):
    """Refuses inserts into `fk_tables` with PostgREST's 409 for a foreign-key violation."""

    def __init__(self):
        super().__init__()
        self.fk_tables: set[str] = set()

    def insert(self, table, row, returning=True):
        if table in self.fk_tables:
            self._gate()
            raise StoreError(FK_409.format(t=table))
        return super().insert(table, row, returning)


class FkPreRemote(PreRemote):
    def __init__(self):
        super().__init__()
        self.fk_tables: set[str] = set()

    def insert(self, table, row, returning=True):
        if table in self.fk_tables:
            self._gate()
            self._schema(table, row=row)
            raise StoreError(FK_409.format(t=table))
        return super().insert(table, row, returning)


@pytest.mark.parametrize("schema", ["pre-0018", "0018"])
def test_a_foreign_key_409_pauses_replication_and_is_not_a_conflict(tmp_path, schema):
    remote = FkPreRemote() if schema == "pre-0018" else FkRemote()
    js = JournalStore(Journal(tmp_path / "j.sqlite"), remote, live=True)
    run = js.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    rep = Replicator(js.journal, remote)
    assert rep.pump() == 1
    remote.fk_tables.add("halt")
    js.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "fk"}, returning=False)
    js.insert("event", {"run_id": run["id"], "type": "after", "detail": {}}, returning=False)
    assert rep.pump() == 0, "the row is not dropped: replication waits on it"
    assert "23503" in rep.last_error and rep.pending() == 2
    assert rep.status()["conflicts"] == 0 and rep.take_conflicts() == []
    assert not js.journal.entries(kinds=("replica_conflict",)), "an FK error is not recorded as a conflict"

    remote.fk_tables.clear()  # the parent row is there now
    assert rep.pump() == 2 and rep.last_error is None and rep.pending() == 0
    assert [h["reason"] for h in remote.tables["halt"]] == ["fk"]
