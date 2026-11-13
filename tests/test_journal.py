"""S77 (#117): the local journal, its store and the asynchronous replication to Supabase (audit D5, H2)."""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeStore

from kaibot.engine.journal import (
    LIVE_ID_BASE,
    BootstrapError,
    Journal,
    JournalStore,
    Replicator,
    select_rows,
)
from kaibot.engine.store import StoreError


class Remote(FakeStore):
    """Supabase that can be switched off. Inserts that clash on a unique key (id, jseq, the natural keys of
    migrations 0001 / 0008) fail as PostgREST does. `lose` = n: the next n writes commit, then the answer is lost."""

    UNIQUE = {"ledger": ("run_id", "ts"), "order": ("client_order_id",), "room_session": ("signal_id",),
              "daily_result": ("run_id", "session")}

    def __init__(self):
        super().__init__()
        self.down = False
        self.calls = 0
        self.lose = 0

    def _gate(self):
        self.calls += 1
        if self.down:
            raise StoreError("POST: <urlopen error [Errno 111] Connection refused>")

    def _lost(self):
        if self.lose:
            self.lose -= 1
            raise StoreError("POST: The read operation timed out")  # committed; the answer never came

    def insert(self, table, row, returning=True):
        self._gate()
        for key in [("id",), ("jseq",), self.UNIQUE.get(table, ())]:
            if key and all(k in row for k in key) and any(
                    all(r.get(k) == row[k] for k in key) for r in self.tables.get(table, [])):
                raise StoreError(f'POST {table} -> 409: {{"code":"23505","details":"Key ({", ".join(key)})=(...) '
                                 f'already exists.","message":"duplicate key value violates unique constraint"}}')
        out = super().insert(table, row, returning)
        self._lost()
        return out

    def heartbeat(self, process, detail):
        self._gate()
        super().upsert("heartbeat", {"process": process, "detail": detail}, "process")
        return super().heartbeat(process, detail)

    def update(self, table, match, values):
        self._gate()
        return super().update(table, match, values)

    def upsert(self, table, row, on_conflict):
        self._gate()
        return super().upsert(table, row, on_conflict)

    def select(self, table, params):
        self._gate()
        return select_rows(self.tables.get(table, []), params)

    def pending_commands(self):
        self._gate()
        return super().pending_commands()



def test_the_journal_is_append_only(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    j.append("row", {"x": 1})
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        j.db.execute("update entry set body = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        j.db.execute("delete from entry")
    assert [e[3] for e in j.entries()] == [{"x": 1}]


def test_state_is_a_fold_over_the_journal_and_survives_a_restart(tmp_path):
    path = tmp_path / "j.sqlite"
    st = JournalStore(Journal(path), live=True)
    run = st.insert("run", {"amount_usd": 5000, "status": "scheduled", "level": "balanced"})
    assert run["id"] == st.id_base + 1 > LIVE_ID_BASE
    sig = st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    t = st.insert("trade", {"run_id": run["id"], "signal_id": sig["id"], "symbol": "A", "qty": 3})
    st.update("trade", {"id": t["id"]}, {"exit_ts": "2026-11-12T20:00:00+00:00", "pnl_usd": 4.2})
    st.upsert("daily_result", {"run_id": run["id"], "session": "2026-11-12", "pnl_venue": 4.3, "pnl_shadow": 4.2},
              "run_id,session")
    st.update("run", {"id": run["id"]}, {"status": "running"})
    st.journal.close()

    again = JournalStore(Journal(path), live=True)
    assert again.active_run()["status"] == "running"
    assert again.open_trades(run["id"]) == [] and again.run_realized(run["id"]) == pytest.approx(4.2)
    assert again.insert("signal", {"run_id": run["id"], "symbol": "B"})["id"] == st.id_base + 4, "ids go on"
    assert again.tables == {k: v for k, v in again.tables.items()}  # one copy, rebuilt from the file


def test_select_supports_the_filters_the_engine_uses():
    rows = [{"id": i, "s": f"2026-11-0{i}", "x": i % 2 or None, "k": "a" if i < 3 else "b"} for i in range(1, 6)]
    assert [r["id"] for r in select_rows(rows, {"s": "lte.2026-11-03", "order": "id.desc", "limit": "2"})] == [3, 2]
    assert [r["id"] for r in select_rows(rows, {"x": "is.null"})] == [2, 4]
    assert [r["id"] for r in select_rows(rows, {"id": "in.(1,5)", "k": "eq.b"})] == [5]
    assert [r["id"] for r in select_rows(rows, {"order": "k.desc,id.asc"})] == [3, 4, 5, 1, 2]


def test_replication_is_in_order_idempotent_and_survives_an_outage():
    remote = Remote()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    st.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "x"}, returning=False)
    st.event("engine_start", {"positions": []}, run["id"])
    assert rep.pump() == 3
    assert remote.tables["run"][0]["id"] == run["id"], "the four referenced tables keep the engine's id"
    assert "jseq" in remote.tables["halt"][0] and remote.tables["halt"][0]["id"] != run["id"] + 1
    assert remote.tables["halt"][0]["ts"], "the time is the engine's, not the replication's"

    remote.down = True
    st.update("run", {"id": run["id"]}, {"status": "paused"})
    st.insert("halt", {"run_id": run["id"], "kind": "daily_stop", "reason": "y"}, returning=False)
    assert rep.pump() == 0 and rep.last_error and rep.pending() == 2
    assert st.active_run()["status"] == "paused", "the engine reads its own state locally while Supabase is down"
    remote.down = False
    assert rep.pump() == 2 and rep.pending() == 0 and rep.last_error is None
    assert remote.tables["run"][0]["status"] == "paused" and len(remote.tables["halt"]) == 2

    assert rep.rebuild() == 5  # everything again: nothing duplicated
    assert len(remote.tables["run"]) == 1 and len(remote.tables["halt"]) == 2 and len(remote.tables["event"]) == 1


def test_rebuild_into_an_empty_database_gives_the_same_rows():
    remote = Remote()
    st = JournalStore(Journal(), remote, live=True)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    sig = st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    st.insert("gate_run", {"signal_id": sig["id"], "approved": True}, returning=False)
    st.upsert("ledger_day", {"run_id": run["id"], "session": "2026-11-12", "close_equity": 5001.0}, "run_id,session")
    Replicator(st.journal, remote).pump()
    fresh = Remote()
    Replicator(st.journal, fresh, name="fresh").rebuild()
    strip = {t: [{k: v for k, v in r.items() if k != "id" or t in ("run", "signal")} for r in rows]
             for t, rows in remote.tables.items()}
    assert strip == {t: [{k: v for k, v in r.items() if k != "id" or t in ("run", "signal")} for r in rows]
                     for t, rows in fresh.tables.items()}


def test_commands_wait_while_supabase_is_down_and_are_never_applied_twice():
    remote = Remote()
    remote.insert("command", {"kind": "pause", "payload": {}, "applied_at": None})
    clock = [0.0]
    st = JournalStore(Journal(), remote, live=True, mono=lambda: clock[0])
    cmds = st.pending_commands()
    assert [c["kind"] for c in cmds] == ["pause"]
    remote.down = True
    st.mark_applied(cmds[0]["id"])  # journaled; Supabase hears of it later
    assert st.pending_commands() == []
    calls = remote.calls
    assert st.pending_commands() == [] and remote.calls == calls, "breaker open: no call for a while"
    clock[0] += 31
    remote.down = False
    assert st.pending_commands() == [], "applied here, not yet there: not applied again"
    Replicator(st.journal, remote).pump()
    assert remote.tables["command"][0]["applied_at"] is not None


def test_dashboard_tables_are_read_from_supabase_and_engine_tables_locally():
    remote = Remote()
    remote.insert("config", {"run_id": 1, "status": "applied", "version": 2})
    st = JournalStore(Journal(), remote, live=True)
    assert st.select("config", {"run_id": "eq.1"})[0]["version"] == 2
    st.insert("trade", {"run_id": 1, "symbol": "A"})
    remote.down = True
    assert len(st.select("trade", {"run_id": "eq.1"})) == 1
    with pytest.raises(StoreError):
        st.select("config", {"run_id": "eq.1"})


def test_bootstrap_copies_the_active_run_once_and_needs_supabase_the_first_time():
    remote = Remote()
    run = remote.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced", "kind": "paper"})
    sig = remote.insert("signal", {"run_id": run["id"], "mode": "live", "symbol": "A"})
    remote.insert("trade", {"run_id": run["id"], "signal_id": sig["id"], "symbol": "A", "qty": 3, "exit_ts": None})
    remote.insert("daily_result", {"run_id": run["id"], "session": "2026-11-11", "pnl_venue": 5, "pnl_shadow": 4})
    remote.insert("event", {"type": "x", "hash": "abc"})
    st = JournalStore(Journal(), remote, live=True)
    out = st.bootstrap()
    assert out["run_id"] == run["id"] and out["imported"] >= 4
    assert st.active_run()["id"] == run["id"] and len(st.open_trades(run["id"])) == 1
    assert st.run_realized(run["id"]) == 4
    assert st.bootstrap() == {"imported": 0}, "once per journal"
    assert Replicator(st.journal, remote).pending() == 0, "imported rows are not sent back"
    assert st.insert("signal", {"run_id": run["id"]})["id"] > LIVE_ID_BASE

    remote.down = True
    with pytest.raises(BootstrapError, match="Not starting"):
        JournalStore(Journal(), remote, live=True).bootstrap()


def test_heartbeat_never_raises_when_supabase_is_down():
    remote = Remote()
    remote.down = True
    st = JournalStore(Journal(), remote)
    st.heartbeat("engine", {"state": "trading"})
    assert st.beats["engine"]["state"] == "trading"


def test_bootstrap_copies_every_multi_day_retirement_whichever_run_wrote_it():
    """QA-Ops second key on #207: a rule retired in an earlier run (its book hit its drawdown end) must stay retired on
    a new journal (a new machine) with a later run; LiveDaily.retirement reads the halt table of this store."""
    from datetime import UTC, datetime

    from kaibot.engine import live_daily as ld

    remote = Remote()
    old = remote.insert("run", {"amount_usd": 5000, "status": "ended", "level": "balanced", "kind": "paper"})
    remote.insert("halt", {"run_id": old["id"], "kind": "multi_day_retired", "session": "2026-11-20",
                           "reason": "rule=trend_momentum; trend_momentum retired: book fell 21.3% ..."})
    remote.insert("halt", {"run_id": old["id"], "kind": "daily_stop", "session": "2026-11-19", "reason": "x"})
    run = remote.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced", "kind": "paper"})
    remote.insert("halt", {"run_id": run["id"], "kind": "daily_stop", "session": "2026-11-23", "reason": "y"})
    st = JournalStore(Journal(), remote, live=True)
    st.bootstrap()
    assert sorted(h["kind"] for h in st.select("halt", {})) == ["daily_stop", "multi_day_retired"]  # old run's stop: no
    lv = ld.LiveDaily(None, st.journal, lambda: datetime(2026, 11, 24, 15, tzinfo=UTC), [], None, None,
                      next_session=lambda d: d, prev_session=lambda d: d, store=st)
    assert lv.retirement("trend_momentum")["session"] == "2026-11-20"
    assert lv.retirement("mom_12_1_band") is None
