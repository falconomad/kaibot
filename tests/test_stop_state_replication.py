"""QA-Ops on #339: before migration 0028 the production path still works. The engine writes `ledger_position` into
its local journal (JournalStore: a local table), so Supabase's refusal of `none_by_design` reaches the replicator,
never the engine's upsert. Replication is strictly in order: one refused row must not stop every later row (the
dead-man reads today's `ledger_position` rows in Supabase)."""

from __future__ import annotations

from types import SimpleNamespace

from test_journal import Remote

from kaibot.engine import __main__ as engine_main
from kaibot.engine.journal import Journal, JournalStore, Replicator
from kaibot.engine.store import StoreError

KEY = "run_id,session,strategy,symbol"


class Pre0028(Remote):
    """Supabase before 0028: the check constraint refuses the new value, as PostgREST answers it."""

    def upsert(self, table, row, on_conflict):
        if table == "ledger_position" and row.get("stop_state") == "none_by_design":
            self._gate()
            raise StoreError('POST ledger_position -> 400: {"code":"23514","details":null,"hint":null,"message":'
                             '"new row for relation \\"ledger_position\\" violates check constraint '
                             '\\"ledger_position_stop_state_check\\""}')
        return super().upsert(table, row, on_conflict)


def _row(sym, state="none_by_design"):
    return {"run_id": 7, "session": "2026-11-19", "strategy": "trend_momentum", "symbol": sym, "qty": 5.0,
            "avg_cost": 10.0, "mark": 10.0, "priced": False, "opened_session": "2026-11-19", "dividends": 0.0,
            "stop_px": None, "stop_state": state}


def test_before_0028_a_refused_stop_state_is_sent_as_missing_and_replication_goes_on():
    remote = Pre0028()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    st.upsert("ledger_position", _row("A"), KEY)
    st.upsert("ledger_position", _row("B"), KEY)
    st.event("engine_start", {"positions": []}, 7)
    assert st.select("ledger_position", {"symbol": "eq.A"})[0]["stop_state"] == "none_by_design"  # local: as written
    assert rep.pump() == 3 and rep.pending() == 0 and rep.last_error is None
    assert [(r["symbol"], r["qty"], r["stop_state"]) for r in remote.tables["ledger_position"]] == [
        ("A", 5.0, "missing"), ("B", 5.0, "missing")]  # never lost: the dead-man reads these rows
    assert [r["type"] for r in remote.tables["event"]] == ["engine_start"]
    assert rep.status()["stop_state_fallback"] is True
    assert [e for e in st.journal.entries(kinds=("replica_fallback",))], "recorded in the journal"
    eng = SimpleNamespace(store=st, _rid=lambda: 7)
    engine_main.report_conflicts(eng, rep)  # reported once, as a `stop_state_fallback` event; no halt row
    ev = [e for e in st.tables["event"] if e["type"] == "stop_state_fallback"]
    assert len(ev) == 1 and "0028" in ev[0]["payload"]["action"] and not st.tables.get("halt")
    engine_main.report_conflicts(eng, rep)
    assert rep.take_fallbacks() == []
    assert len([e for e in st.tables["event"] if e["type"] == "stop_state_fallback"]) == 1
    assert rep.pump() == 1 and remote.tables["event"][-1]["type"] == "stop_state_fallback"


def test_after_0028_none_by_design_is_sent_as_it_is():
    remote = Remote()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    st.upsert("ledger_position", _row("A"), KEY)
    assert rep.pump() == 1 and remote.tables["ledger_position"][0]["stop_state"] == "none_by_design"
    assert rep.take_fallbacks() == [] and rep.status()["stop_state_fallback"] is False


def test_another_refusal_still_pauses_replication():
    """Only the stop_state check of a `none_by_design` row is translated; any other refusal stays an error."""

    class Refuses(Remote):
        def upsert(self, table, row, on_conflict):
            raise StoreError('POST ledger_position -> 400: {"code":"23502","message":"null value in column"}')

    remote = Refuses()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    st.upsert("ledger_position", _row("A"), KEY)
    assert rep.pump() == 0 and rep.last_error and rep.pending() == 1
