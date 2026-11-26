"""#546 (weekly mutation testing), second pass on `engine/journal.py` (26 Nov 2026, main @ 2a05008): tests for the
survivors outside the pure read helpers of `test_journal_survivors.py`.

- `_same` / `same_row`: the replicator's "is the stored row the one sent?" (a conflict is dropped only when it is);
- `_clash_columns`, `_eq_filter`, `_duplicate`, `_stop_state_missing`, `_row_0018`: how a refused or duplicate
  write is read and sent again;
- the store's reads the engine acts on: pending commands, the active run, open trades, realized P&L, the breaker
  around Supabase and the quick (short-timeout) client;
- what a bootstrap remembers of other journals (`_know`, `_seen_now`);
- `skip_to` on a fresh file, `reseal` putting the cut-over back when it refuses;
- the replicator's status, its backlog alarm and its id translation.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from kaibot.engine import journal as J
from kaibot.engine.journal import Journal, JournalCorrupt, JournalStore, Replicator
from kaibot.engine.store import StoreError

# ---------------------------------------------------------------- _same / same_row


def test_none_is_the_same_only_as_none():
    assert J._same(None, None)
    assert not J._same(None, 5) and not J._same(5, None)
    assert not J._same(None, "None") and not J._same("None", None)  # never by its text


def test_floats_match_to_the_cent_plus_a_relative_hair_for_large_values():
    assert J._same(10.0, 10.004) and not J._same(10.0, 10.006)
    # 1e9 + 0.5: within 0.005 + 1e-9 x 1e9 = 1.005, so the same; not with `-` or `/` in the relative part
    assert J._same(1e9, 1e9 + 0.5)
    assert not J._same(1e9, 1e9 + 1.5)


@pytest.mark.parametrize("a, b", [(1.5, "abc"), (5, "abc"), ("a", "b"), ("2026-11-26", "not a date")])
def test_values_that_cannot_be_compared_are_not_the_same(a, b):
    assert J._same(a, b) is False


def test_dicts_compare_every_key_of_either_side():
    assert J._same({"a": 1}, {"a": 1})
    assert not J._same({"a": 1}, {"a": 1, "b": 2}) and not J._same({"a": 1, "b": 2}, {"a": 1})
    assert J._same({"a": 1}, [1]) is False  # a dict against a list is not compared key by key


def test_lists_compare_only_with_lists():
    assert J._same([1, 2.0], (1, 2.001)) and not J._same([1, 2], [1])
    assert J._same([1], "1") is False  # not element by element against text


def test_same_row_skips_the_id_only_when_the_stored_row_has_none():
    assert J.same_row({"id": 5, "x": 1}, {"x": 1})  # the database numbered it: not compared
    assert not J.same_row({"id": 5, "x": 1}, {"id": 6, "x": 1})  # it has one: compared
    assert J.same_row({"id": 5, "x": 1}, {"id": 5, "x": 1})


# ---------------------------------------------------------------- refused and duplicate writes


def test_clash_columns_are_read_from_postgres_key_text():
    e = StoreError('POST trade -> 409: Key (run_id, "ts")=(1, 2) already exists.')
    assert J._clash_columns(e) == ("run_id", "ts")
    assert J._clash_columns(StoreError("Key (id)=(7) already exists")) == ("id",)
    assert J._clash_columns(StoreError("duplicate key value violates unique constraint")) == ()
    assert J._clash_columns(StoreError("key (id)=(7)")) == ()  # Postgres writes "Key"


def test_eq_filter():
    assert J._eq_filter(None) == "is.null"
    assert J._eq_filter(3) == "eq.3"


def test_a_unique_violation_is_known_by_its_code_alone():
    assert J._duplicate(StoreError('-> 409: {"code":"23505"}'))
    assert J._duplicate(StoreError("duplicate key value"))
    assert not J._duplicate(StoreError('-> 409: {"code":"23503"}'))


def _ss_op(table="ledger_position"):
    return {"table": table, "op": "insert", "row": {"run_id": 1, "stop_state": "none_by_design"}}


def test_stop_state_fallback_only_for_ledger_position():
    err = StoreError('-> 400: {"code":"23514","message":"violates check constraint stop_state"}')
    got = J._stop_state_missing(_ss_op(), err)
    assert got == {**_ss_op(), "row": {"run_id": 1, "stop_state": "missing"}}
    assert J._stop_state_missing(_ss_op("trade"), err) is None


def test_row_0018_without_an_id_does_not_fail():
    assert J._row_0018("segment", {"x": 1}, 7) == {"x": 1, "jseq": 7}
    assert J._row_0018("segment", {"id": 3, "x": 1}, 7) == {"x": 1, "jseq": 7}
    assert J._row_0018("trade", {"id": 3}, 7) == {"id": 3}


# ---------------------------------------------------------------- the store's reads


def store(**kw):
    return JournalStore(Journal(":memory:"), **kw)


def test_reads_of_an_empty_store_are_empty():
    js = store()
    assert js.pending_commands() == [] and js.open_trades(1) == [] and js.run_realized(1) == 0.0
    assert js.active_run() is None


def test_an_applied_command_is_not_pending():
    js = store()
    js.insert("command", {"kind": "halt", "applied_at": "2026-11-26T15:00:00+00:00"})
    new = js.insert("command", {"kind": "resume"})
    assert [c["id"] for c in js.pending_commands()] == [new["id"]]


def test_mark_applied_writes_the_payload_only_when_given():
    js = store()
    js.mark_applied(7)
    js.mark_applied(8, {"done": True})
    ops = [op for _, _, _, op in js.journal.entries(kinds=("row",))]
    assert "payload" not in ops[0]["values"] and ops[1]["values"]["payload"] == {"done": True}
    assert datetime.fromisoformat(ops[0]["values"]["applied_at"]).utcoffset() is not None
    assert {"7", "8"} <= js.applied


def test_the_active_run_is_a_paper_run_and_a_run_without_a_kind_is_paper():
    js = store()
    js.tables["run"] = [{"id": 1, "status": "running"}, {"id": 3, "kind": "backtest", "status": "running"}]
    assert js.active_run()["id"] == 1


def test_the_active_run_is_the_newest_scheduled_running_or_paused_one():
    js = store()
    js.tables["run"] = [{"id": 1, "kind": "paper", "status": "running"},
                        {"id": 2, "kind": "paper", "status": "scheduled"},
                        {"id": 4, "kind": "paper", "status": "done"}]
    assert js.active_run()["id"] == 2


def test_open_trades_come_in_id_order():
    js = store()
    js.tables["trade"] = [{"id": 5, "run_id": 1}, {"id": 3, "run_id": "1"}, {"id": 4, "run_id": 1, "exit_ts": "x"},
                          {"id": 2, "run_id": 2}]
    assert [t["id"] for t in js.open_trades(1)] == [3, 5]


class Quick:
    def __init__(self, tag, fail=False):
        self.tag, self.fail, self.calls = tag, fail, 0

    def select(self, table, params):
        self.calls += 1
        if self.fail:
            raise StoreError("down")
        return [{"via": self.tag}]


class Remote(Quick):
    def __init__(self, fail=False):
        super().__init__("remote", fail)
        self.q = Quick("quick", fail)

    def quick(self):
        return self.q


def test_remote_reads_go_through_the_quick_client():
    js = store(remote=Remote())
    assert js.select("command", {}) == [{"via": "quick"}]
    plain = Quick("plain")  # a remote without quick() is used as it is
    assert store(remote=plain).select("command", {}) == [{"via": "plain"}]


def test_the_breaker_opens_after_a_failure_and_closes_exactly_breaker_s_later():
    t = [100.0]
    js = store(remote=Remote(fail=True), breaker_s=30.0, mono=lambda: t[0])
    assert js.remote_ok()
    with pytest.raises(StoreError, match="down"):
        js.select("command", {})
    assert not js.remote_ok()
    with pytest.raises(StoreError, match="breaker open"):
        js.select("command", {})
    t[0] = 129.9
    assert not js.remote_ok()
    t[0] = 130.0
    assert js.remote_ok()


def test_a_table_not_kept_in_memory_is_read_from_the_journal():
    js = store(keep=lambda table: table != "ledger")
    js.insert("ledger", {"run_id": 1, "ts": "2026-11-26T15:00:00+00:00", "equity": 100.0})
    assert "ledger" not in js.tables
    assert [r["equity"] for r in js.select("ledger", {})] == [100.0]


# ---------------------------------------------------------------- other journals


def test_know_keeps_the_highest_number_seen_of_each_journal_and_epoch():
    js = store()
    js._know({"known": {"a": 5}, "epochs": {"3": 9}})
    js._know({"known": {"a": 3, "b": 0}, "epochs": {"3": 4, "4": 0}})
    assert js.known_journals == {"a": 5, "b": 0}
    assert js.known_epochs == {3: 9, 4: 0}


def test_seen_now_keeps_the_highest_number_of_each_epoch():
    obs = {"latest": {"x": (3, 10), "y": (3, 7), "z": (4, 0)}, "heartbeat": {"id": "j1", "seq": 12}}
    assert JournalStore._seen_now(obs) == {"known": {"j1": 12}, "epochs": {"3": 10, "4": 0}}


# ---------------------------------------------------------------- the journal file


def test_skip_to_on_a_fresh_journal():
    j = Journal(":memory:")
    j.skip_to(40)
    assert j.append("note", {}) == 41


def test_reseal_with_nothing_to_accept_says_none(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.append("note", {"i": 1})
    assert j.reseal()["accepted"] is None


def test_a_refused_reseal_puts_the_cut_over_back(tmp_path):
    import sqlite3

    path = tmp_path / "j.sqlite"
    j = Journal(path)
    for i in range(3):
        j.append("note", {"i": i})
    j.close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("drop trigger if exists entry_no_update")
    db.execute("update entry set body = '{\"i\":9}' where seq = 2")  # a checksummed entry changed
    db.execute("insert into entry (ts, kind, body) values ('2026-11-22T15:00:00+00:00', 'note', '{\"old\":1}')")
    db.close()
    j = Journal(path, verify=False)
    before = j._sum_from
    with pytest.raises(JournalCorrupt):
        j.reseal()
    assert j._sum_from == before is not None


# ---------------------------------------------------------------- the replicator


def _rep(n=3, cursor=1):
    j = Journal(":memory:")
    for i in range(n):
        j.append("row", {"op": "insert", "table": "halt", "row": {"id": i + 1}})
    j.set_cursor("supabase", cursor)
    return j, Replicator(j, remote=None)


def test_status_names_every_field():
    j, rep = _rep()
    assert rep.status() == {"cursor": 1, "last_seq": 3, "pending": 2, "error": None, "schema": None, "conflicts": 0,
                            "poisons": 0, "backlog_alarm": False, "journal": j.journal_id,
                            "stop_state_fallback": False, "multi_day_fallback": False}


def test_the_backlog_alarm_says_how_long_and_where():
    _, rep = _rep()
    t = [1000.0]
    rep.clock = lambda: t[0]
    rep.last_error = "StoreError: down"
    assert rep.backlog_alarm() is None
    t[0] = 1610.0  # 10.17 minutes
    assert rep.backlog_alarm() == {
        "pending": 2, "minutes": 10.2, "cursor": 1, "error": "StoreError: down",
        "action": "Supabase is behind the journal: the dashboard and the dead-man's book are stale; "
                  "trading goes on from the journal"}


def test_db_id_translates_known_ids_only():
    _, rep = _rep()
    rep.ids = {("run", 5): 99}
    assert rep._db_id("run", 5) == 99
    assert rep._db_id("run", 6) == 6  # not one the database numbered: kept
    assert rep._db_id("run", None) is None
    assert rep._db_id("run", ["x"]) == ["x"]  # not an id at all: never looked up
