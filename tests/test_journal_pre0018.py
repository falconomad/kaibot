"""S77 (#117): the engine on new code while migration 0018 is not applied yet. The replica keeps writing the
pre-0018 tables (database ids, no jseq, no ledger tables), so the dashboard keeps updating; a restart after 0018
is applied switches to the new tables and keeps the references to the rows sent before.

With KAIBOT_TEST_PG_PRE / KAIBOT_TEST_PG_POST (psql-style connection options of scratch databases in production's
state, without and with 0018) the same replication also runs against real Postgres."""

from __future__ import annotations

import json
import os
import shlex

import pytest
from test_engine import synth_frames
from test_journal import Remote
from test_ledger_engine import D2, build, calm, offer, session

from kaibot.engine.journal import (
    ID_TABLES,
    LEDGER_TABLES,
    LIVE_ID_BASE,
    SCHEMA_0018,
    SCHEMA_PRE_0018,
    Journal,
    JournalStore,
    Replicator,
)
from kaibot.engine.store import StoreError


class PreRemote(Remote):
    """Supabase before 0018, answering as PostgREST does. `migrated = True`: 0018 has been applied."""

    def __init__(self):
        super().__init__()
        self.migrated = False

    def _schema(self, table, row=None, params=None):
        if self.migrated:
            return
        if table in LEDGER_TABLES:
            raise StoreError(f"{table} -> 404: {{\"code\":\"PGRST205\",\"message\":\"Could not find the table "
                             f"'kaibot.{table}' in the schema cache\"}}")
        if params and "jseq" in str(params.get("select", "")):
            raise StoreError(f'GET {table} -> 400: {{"code":"42703","message":"column {table}.jseq does not exist"}}')
        if row is not None and "jseq" in row:
            raise StoreError(f"POST {table} -> 400: {{\"code\":\"PGRST204\",\"message\":\"Could not find the 'jseq' "
                             f"column of '{table}' in the schema cache\"}}")
        if row is not None and table in ID_TABLES and "id" in row:
            raise StoreError(f'POST {table} -> 400: {{"code":"428C9","message":"cannot insert a non-DEFAULT value '
                             f'into column \\"id\\""}}')

    def insert(self, table, row, returning=True):
        self._gate()
        self._schema(table, row=row)
        return super().insert(table, row, returning)

    def upsert(self, table, row, on_conflict):
        self._gate()
        self._schema(table, row=row)
        return super().upsert(table, row, on_conflict)

    def update(self, table, match, values):
        self._gate()
        self._schema(table)
        return super().update(table, match, values)

    def select(self, table, params):
        self._gate()
        self._schema(table, params=params)
        return super().select(table, params)


def by_id(remote, t):
    return {r["id"]: r for r in remote.tables.get(t, [])}


def test_before_0018_the_old_tables_keep_updating_with_the_database_ids(tmp_path):
    remote = PreRemote()
    path = tmp_path / "journal.sqlite"
    st = JournalStore(Journal(path), remote, id_base=LIVE_ID_BASE)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    sig = st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    st.insert("gate_run", {"signal_id": sig["id"], "approved": True}, returning=False)
    order = st.insert("order", {"signal_id": sig["id"], "symbol": "A", "side": "buy"})
    st.insert("fill", {"order_id": order["id"], "qty": 3, "price": 10.0}, returning=False)
    trade = st.insert("trade", {"run_id": run["id"], "signal_id": sig["id"], "symbol": "A", "qty": 3})
    st.event("engine_start", {"positions": []}, run["id"])
    st.upsert("daily_result", {"run_id": run["id"], "session": D2, "pnl_venue": 4.3, "pnl_shadow": 4.2},
              "run_id,session")
    st.upsert("ledger_day", {"run_id": run["id"], "session": D2, "close_equity": 5004.2}, "run_id,session")
    rep = Replicator(st.journal, remote)
    assert rep.pump() == 9 and rep.pending() == 0 and rep.last_error is None
    assert rep.status()["schema"] == SCHEMA_PRE_0018

    (rrun,) = remote.tables["run"]
    (rsig,) = remote.tables["signal"]
    (rorder,) = remote.tables["order"]
    (rtrade,) = remote.tables["trade"]
    assert rrun["id"] < LIVE_ID_BASE and rsig["run_id"] == rrun["id"], "the database's id, and references to it"
    assert remote.tables["gate_run"][0]["signal_id"] == rsig["id"] and rorder["signal_id"] == rsig["id"]
    assert remote.tables["fill"][0]["order_id"] == rorder["id"]
    assert rtrade["run_id"] == rrun["id"] and rtrade["signal_id"] == rsig["id"]
    assert remote.tables["event"][0]["run_id"] == rrun["id"]
    assert remote.tables["daily_result"][0]["run_id"] == rrun["id"]
    assert not any("jseq" in r for rows in remote.tables.values() for r in rows)
    assert "ledger_day" not in remote.tables, "the ledger's tables do not exist yet: they stay in the journal"
    assert st.tables["ledger_day"][0]["close_equity"] == 5004.2

    # the engine restarts (still no 0018): updates find the rows by their database ids
    st2 = JournalStore(Journal(path), remote, id_base=LIVE_ID_BASE)
    st2.update("trade", {"id": trade["id"]}, {"exit_reason": "time", "pnl_usd": 4.2})
    st2.update("run", {"id": run["id"]}, {"status": "paused"})
    st2.update("gate_run", {"signal_id": sig["id"]}, {"approved": False})
    rep2 = Replicator(st2.journal, remote)
    assert rep2.pump() == 3
    assert by_id(remote, "trade")[rtrade["id"]]["exit_reason"] == "time"
    assert by_id(remote, "run")[rrun["id"]]["status"] == "paused"
    assert remote.tables["gate_run"][0]["approved"] is False
    with pytest.raises(RuntimeError, match="0018"):
        rep2.rebuild()


def test_the_schema_is_asked_once_and_waits_for_supabase(tmp_path):
    remote = PreRemote()
    st = JournalStore(Journal(), remote, id_base=LIVE_ID_BASE)
    st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    rep = Replicator(st.journal, remote)
    remote.down = True
    assert rep.detect() is None and rep.pump() == 0 and rep.last_error and remote.tables.get("run") is None
    remote.down = False
    assert rep.pump() == 1 and rep.schema == SCHEMA_PRE_0018 and rep.last_error is None
    remote.migrated = True  # applied while the engine runs: nothing changes until a restart
    st.insert("signal", {"run_id": st.active_run()["id"], "symbol": "A", "mode": "live"})
    assert rep.pump() == 1 and remote.tables["signal"][0]["id"] < LIVE_ID_BASE
    assert Replicator(st.journal, remote).detect() == SCHEMA_0018


def test_after_0018_a_restart_switches_and_keeps_the_references_to_rows_sent_before(tmp_path):
    remote = PreRemote()
    path = tmp_path / "journal.sqlite"
    st = JournalStore(Journal(path), remote, id_base=LIVE_ID_BASE)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    trade = st.insert("trade", {"run_id": run["id"], "symbol": "A", "qty": 3})
    assert Replicator(st.journal, remote).pump() == 2
    db_run = remote.tables["run"][0]["id"]

    remote.migrated = True  # the stakeholder applies 0018; the engine restarts
    st2 = JournalStore(Journal(path), remote, id_base=LIVE_ID_BASE)
    sig = st2.insert("signal", {"run_id": run["id"], "symbol": "B", "mode": "live"})
    st2.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "x"}, returning=False)
    st2.update("trade", {"id": trade["id"]}, {"exit_reason": "stop"})
    st2.upsert("ledger_day", {"run_id": run["id"], "session": D2, "close_equity": 4990.0}, "run_id,session")
    rep = Replicator(st2.journal, remote)
    assert rep.pump() == 4 and rep.schema == SCHEMA_0018
    rsig = remote.tables["signal"][0]
    assert rsig["id"] == sig["id"] > LIVE_ID_BASE, "new rows: the engine's id"
    assert rsig["run_id"] == db_run, "a reference to a row sent before the switch: the database's id"
    assert remote.tables["halt"][0]["run_id"] == db_run and "jseq" in remote.tables["halt"][0]
    assert remote.tables["trade"][0]["exit_reason"] == "stop"
    assert remote.tables["ledger_day"][0]["run_id"] == db_run


def test_a_whole_session_before_0018_reaches_the_tables_the_dashboard_reads(tmp_path):
    remote = PreRemote()
    store = JournalStore(Journal(tmp_path / "journal.sqlite"), remote, id_base=LIVE_ID_BASE)
    remote.insert("command", {"kind": "new_run", "payload": {"amount_usd": 5000.0, "level": "balanced"},
                              "idempotency_key": "k1", "applied_at": None})
    eng, broker, clock, _ = build(tmp_path, calm(synth_frames()), D2, store=store, new_run=False)
    rep = Replicator(store.journal, remote)
    offer(eng, D2)

    def each(k):
        eng.apply_commands()
        eng.tick()
        rep.pump()

    session(eng, broker, clock, D2, each=each)
    rep.pump()
    assert rep.pending() == 0 and rep.last_error is None and rep.schema == SCHEMA_PRE_0018
    (t,) = store.tables["trade"]
    (rt,) = remote.tables["trade"]
    db_run = remote.tables["run"][0]["id"]
    assert rt["exit_reason"] == t["exit_reason"] == "time" and rt["run_id"] == db_run < LIVE_ID_BASE
    assert rt["signal_id"] in by_id(remote, "signal")
    orders = by_id(remote, "order")
    assert len(remote.tables["fill"]) == 2 and all(f["order_id"] in orders for f in remote.tables["fill"])
    assert remote.tables["daily_result"][0]["run_id"] == db_run and remote.tables["daily_result"][0]["session"] == D2
    assert remote.tables["command"][0]["applied_at"] is not None
    assert "ledger_day" not in remote.tables and store.tables["ledger_day"]


# ---------------------------------------------------------------- against real Postgres (optional)
PG_PRE, PG_POST = os.environ.get("KAIBOT_TEST_PG_PRE"), os.environ.get("KAIBOT_TEST_PG_POST")


class PgRemote:
    """Just enough of PostgREST over psycopg: inserts, upserts, updates and probing selects on schema kaibot,
    with Postgres's own errors (code in the text) as StoreError."""

    def __init__(self, opts: str):
        import psycopg

        a = shlex.split(opts)
        kw = {{"-h": "host", "-p": "port", "-U": "user", "-d": "dbname"}[a[i]]: a[i + 1] for i in range(0, len(a), 2)}
        self.db = psycopg.connect(**kw, autocommit=True)

    def _run(self, sql, args=()):
        import psycopg

        try:
            with self.db.cursor() as c:
                c.execute(sql, args)
                return c.fetchall() if c.description else None
        except psycopg.Error as e:
            raise StoreError(f"{e.sqlstate}: {e}") from None

    def _types(self, table):
        if not hasattr(self, "_t"):
            self._t = {}
        if table not in self._t:
            self._t[table] = dict(self._run("select column_name, data_type from information_schema.columns "
                                            "where table_schema = 'kaibot' and table_name = %s", (table,)))
        return self._t[table]

    def _vals(self, table, row):
        """As PostgREST: JSON objects and arrays go to jsonb, arrays to Postgres arrays where the column is one."""
        from psycopg.types.json import Jsonb

        types = self._types(table)
        return [v if not isinstance(v, dict | list) or types.get(k) == "ARRAY" else Jsonb(v) for k, v in row.items()]

    def insert(self, table, row, returning=True):
        cols = ", ".join(f'"{k}"' for k in row)
        vals = ", ".join(["%s"] * len(row))
        ret = " returning id" if returning else ""
        out = self._run(f'insert into kaibot."{table}" ({cols}) values ({vals}){ret}', self._vals(table, row))
        return {**row, "id": out[0][0]} if returning else None

    def upsert(self, table, row, on_conflict):
        cols = ", ".join(f'"{k}"' for k in row)
        sets = ", ".join(f'"{k}" = excluded."{k}"' for k in row)
        self._run(f'insert into kaibot."{table}" ({cols}) values ({", ".join(["%s"] * len(row))}) '
                  f"on conflict ({on_conflict}) do update set {sets}", self._vals(table, row))

    def update(self, table, match, values):
        sets = ", ".join(f'"{k}" = %s' for k in values)
        where = " and ".join(f'"{k}"::text = %s' for k in match)
        self._run(f'update kaibot."{table}" set {sets} where {where}',
                  self._vals(table, values) + [str(v) for v in match.values()])

    def select(self, table, params):
        cols = params.get("select", "*")
        return self._run(f'select {cols} from kaibot."{table}" limit {int(params.get("limit", 1))}')

    def count(self, table, where="true"):
        return self._run(f'select count(*) from kaibot."{table}" where {where}')[0][0]


def _engine_session_into(tmp_path, remote):
    """One engine session on fakes (the command comes from a fake Supabase), replicated into `remote`."""
    fake = Remote()
    fake.insert("command", {"kind": "new_run", "payload": {"amount_usd": 5000.0, "level": "balanced"},
                            "idempotency_key": "k1", "applied_at": None})
    store = JournalStore(Journal(tmp_path / "journal.sqlite"), fake, id_base=LIVE_ID_BASE)
    eng, broker, clock, _ = build(tmp_path, calm(synth_frames()), D2, store=store, new_run=False)
    offer(eng, D2)
    session(eng, broker, clock, D2)
    journal = store.journal
    rows = [op for _, _, _, op in journal.entries(kinds=("row",)) if op["table"] != "command"]
    for _, _, _, op in journal.entries(kinds=("row",)):
        assert op["table"] == "command" or op["table"] in {r["table"] for r in rows}
    j2 = Journal(tmp_path / "replica.sqlite")
    for op in rows:
        j2.append("row", op)
    rep = Replicator(j2, remote)
    sent = rep.pump()
    return rep, sent, len(rows), store


@pytest.mark.skipif(not PG_PRE, reason="set KAIBOT_TEST_PG_PRE to a scratch Postgres in production's state, no 0018")
def test_pg_before_0018_the_engine_rows_land_in_the_old_tables(tmp_path):
    remote = PgRemote(PG_PRE)
    before = {t: remote.count(t) for t in ("run", "trade", "fill", "daily_result")}
    rep, sent, n, store = _engine_session_into(tmp_path, remote)
    assert rep.schema == SCHEMA_PRE_0018 and rep.last_error is None, rep.last_error
    assert sent == n
    assert remote.count("run") == before["run"] + 1 and remote.count("trade") == before["trade"] + 1
    assert remote.count("fill") == before["fill"] + 2
    run_id = rep.ids[("run", store.tables["run"][0]["id"])]
    assert remote.count("trade", f"run_id = {run_id} and exit_reason = 'time'") == 1
    assert remote.count("daily_result", f"run_id = {run_id} and session = '{D2}'") == 1
    assert json.dumps(sorted(t for t, _ in rep.ids)) and run_id < LIVE_ID_BASE


@pytest.mark.skipif(not PG_POST, reason="set KAIBOT_TEST_PG_POST to a scratch Postgres with 0018 applied")
def test_pg_after_0018_the_engine_rows_and_the_ledger_land_with_engine_ids(tmp_path):
    remote = PgRemote(PG_POST)
    rep, sent, n, store = _engine_session_into(tmp_path, remote)
    assert rep.schema == SCHEMA_0018 and rep.last_error is None, rep.last_error
    assert sent == n and not rep.ids
    rid = store.tables["run"][0]["id"]
    assert remote.count("trade", f"run_id = {rid} and exit_reason = 'time'") == 1
    assert remote.count("ledger_day", f"run_id = {rid} and session = '{D2}'") == 1
    assert remote.count("fill", f"jseq is not null and order_id in (select id from kaibot.\"order\" "
                                f"where signal_id in (select id from kaibot.signal where run_id = {rid}))") == 2
    assert rep.rebuild() == n, "a second send of everything"
    assert remote.count("trade", f"run_id = {rid}") == 1 and remote.count("ledger_day", f"run_id = {rid}") == 1
