"""The local journal (S77, #117; decision 15: "local journal first, Supabase asynchronously"; audit D5, H2).

One SQLite file (`data/live/journal.sqlite` for the paper engine) with one append-only table. Everything the engine
records goes into it first and is durable (WAL, synchronous=FULL) before the call that follows it returns:

- `row` entries: every write the engine used to send to Supabase (insert, update, upsert), in order;
- ledger entries (engine/ledger.py: run, fill, mark, dividend, split, position): the accounting;
- `intent` / `ack` entries: every broker call, written BEFORE the call and answered after it;
- `import` / `bootstrap`: rows copied from Supabase the first time a journal is started on an existing run.

State is a fold over it: `JournalStore` materialises the engine's tables from the `row` and `import` entries when it
opens, and the engine folds the ledger from the ledger entries. A restart reads the file and is where it was.

Supabase is a replica. `Replicator` sends the `row` entries in journal order, from a cursor, on its own thread; an
outage only makes the replica late. Every send is idempotent (engine-assigned ids for the four tables other rows
point to, a `jseq` column with the journal sequence number elsewhere, natural keys for the rest; "duplicate key"
counts as done), so a retry after an unknown outcome and a full `rebuild()` never duplicate a row. Migration
0018 makes the ids and `jseq` acceptable to the database.

Before 0018 is applied the replica degrades instead of stopping (the dashboard keeps updating): the replicator
looks once, at its first contact with Supabase, for `ledger_day` and `segment.jseq`. Without them it writes the
pre-0018 tables the way the engine always did: the database assigns the ids of run / signal / order / trade (the
journal keeps a map from the engine's id to the database's and translates every reference), no `jseq`, and the
ledger's own tables stay in the journal. A send whose answer was lost may then be duplicated on retry, as before
S77, and `rebuild()` is refused. Apply 0018 and restart the engine to switch; references to rows sent before the
switch still go through the map.

Reads of the engine's own tables are answered locally. Only tables other writers own (the dashboard's commands
and configs, the trial registry, the news desk) are read from Supabase, with a short timeout and a breaker.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("kaibot.journal")

# Tables only the engine writes: answered from the journal. Everything else is read from (and written to) Supabase.
LOCAL_TABLES = frozenset({"run", "segment", "signal", "gate_run", "order", "fill", "trade", "ledger", "daily_result",
                          "halt", "event", "gate_random", "gate_report", "room_session", "ledger_day",
                          "ledger_position"})
ID_TABLES = frozenset({"run", "signal", "order", "trade"})  # other rows point at these: replicated with our id
JSEQ_TABLES = frozenset({"segment", "gate_run", "fill", "halt", "event", "gate_report"})  # deduplicated on jseq
DEFAULT_TS = {"halt": "ts", "gate_report": "ts", "gate_random": "created_at"}  # set when written, not replicated
LIVE_ID_BASE = 1_000_000_000  # engine-assigned ids live above every id the database's own sequences hand out
LEDGER_TABLES = frozenset({"ledger_day", "ledger_position"})  # created by 0018: not replicated before it
FK_COLUMNS = {"run_id": "run", "signal_id": "signal", "order_id": "order", "trade_id": "trade"}
SCHEMA_0018, SCHEMA_PRE_0018 = "0018", "pre-0018"

SCHEMA = """
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
create table if not exists idmap (name text not null, tbl text not null, engine_id integer not null,
  db_id integer not null, primary key (name, tbl, engine_id));
"""


def _json(o):
    if isinstance(o, datetime):
        return o.astimezone(UTC).isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    raise TypeError(type(o))


def canon(x):
    """What the row looks like after a trip through JSON (and through Supabase): datetimes as ISO text."""
    return json.loads(json.dumps(x, default=_json))


class Journal:
    def __init__(self, path: str | Path = ":memory:", now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        with self.lock:
            if self.path != ":memory:":
                self.db.execute("pragma journal_mode=wal")
            self.db.execute("pragma synchronous=full")
            self.db.executescript(SCHEMA)

    def append(self, kind: str, body: dict) -> int:
        """Durable when it returns (autocommit, synchronous=FULL)."""
        text = json.dumps(body, default=_json, sort_keys=True, separators=(",", ":"))
        with self.lock:
            return int(self.db.execute("insert into entry (ts, kind, body) values (?, ?, ?)",
                                       (self.now().astimezone(UTC).isoformat(), kind, text)).lastrowid)

    def entries(self, after: int = 0, kinds: tuple[str, ...] | None = None, limit: int | None = None) -> list:
        q, args = "select seq, ts, kind, body from entry where seq > ?", [after]
        if kinds:
            q += f" and kind in ({','.join('?' * len(kinds))})"
            args += list(kinds)
        q += " order by seq"
        if limit:
            q += f" limit {int(limit)}"
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [(s, ts, k, json.loads(b)) for s, ts, k, b in rows]

    def last_seq(self) -> int:
        with self.lock:
            return int(self.db.execute("select coalesce(max(seq), 0) from entry").fetchone()[0])

    def count(self, kinds: tuple[str, ...], after: int = 0) -> int:
        q = f"select count(*) from entry where seq > ? and kind in ({','.join('?' * len(kinds))})"
        with self.lock:
            return int(self.db.execute(q, (after, *kinds)).fetchone()[0])

    def cursor(self, name: str) -> int:
        with self.lock:
            r = self.db.execute("select seq from cursor where name = ?", (name,)).fetchone()
        return int(r[0]) if r else 0

    def set_cursor(self, name: str, seq: int) -> None:
        with self.lock:
            self.db.execute("insert into cursor (name, seq) values (?, ?) on conflict(name) do update set seq = ?",
                            (name, seq, seq))

    def id_map(self, name: str) -> dict[tuple[str, int], int]:
        """Engine id -> database id, for rows a replica received before migration 0018 (it assigned the id)."""
        with self.lock:
            rows = self.db.execute("select tbl, engine_id, db_id from idmap where name = ?", (name,)).fetchall()
        return {(t, int(e)): int(d) for t, e, d in rows}

    def map_id(self, name: str, table: str, engine_id: int, db_id: int) -> None:
        with self.lock:
            self.db.execute("insert or replace into idmap (name, tbl, engine_id, db_id) values (?, ?, ?, ?)",
                            (name, table, int(engine_id), int(db_id)))

    def close(self) -> None:
        with self.lock:
            self.db.close()


class BootstrapError(RuntimeError):
    pass


class JournalStore:
    """The engine's store (the interface of engine/store.Store) on top of the journal. `remote` is the Supabase
    store (None in research: then every table is local)."""

    def __init__(self, journal: Journal, remote=None, *, id_base: int = 0, breaker_s: float = 30.0,
                 mono: Callable[[], float] = time.monotonic):
        self.journal, self.remote, self.id_base = journal, remote, id_base
        self.breaker_s, self.mono = breaker_s, mono
        self._open_until = -1.0  # remote reads are skipped until then (after a failure)
        self.tables: dict[str, list[dict]] = {}
        self.applied: set = set()  # command ids this engine has applied (the update may not have reached Supabase)
        self.beats: dict = {}
        self._max_id = id_base
        self._prev_hash: str | None = None
        for _, _, kind, op in journal.entries(kinds=("row", "import", "bootstrap")):
            if kind == "bootstrap":
                self._prev_hash = op.get("prev_hash") or self._prev_hash
                continue
            self._apply(op)

    # ---------------------------------------------------------------- the fold
    def _apply(self, op: dict) -> None:
        t, kind = op["table"], op["op"]
        rows = self.tables.setdefault(t, [])
        if kind == "insert":
            row = dict(op["row"])
            rows.append(row)
            if isinstance(row.get("id"), int):
                self._max_id = max(self._max_id, row["id"])
            if t == "event" and row.get("hash"):
                self._prev_hash = row["hash"]
        elif kind == "update":
            for r in rows:
                if all(str(r.get(k)) == str(v) for k, v in op["match"].items()):
                    r.update(op["values"])
            if t == "command" and "applied_at" in op["values"]:
                self.applied.add(str(op["match"].get("id")))
        elif kind == "upsert":
            keys = op["on_conflict"].split(",")
            row = op["row"]
            for r in rows:
                if all(str(r.get(k)) == str(row.get(k)) for k in keys):
                    r.update(row)
                    break
            else:
                rows.append(dict(row))

    def _write(self, op: dict) -> None:
        op = canon(op)
        self.journal.append("row", op)  # durable first
        self._apply(op)

    def _local(self, table: str) -> bool:
        return self.remote is None or table in LOCAL_TABLES

    # ---------------------------------------------------------------- remote, guarded
    def remote_ok(self) -> bool:
        return self.remote is not None and self.mono() >= self._open_until

    def _remote(self, fn: Callable[[], Any]) -> Any:
        from kaibot.engine.store import StoreError

        if not self.remote_ok():
            raise StoreError("supabase skipped: unreachable a moment ago (breaker open)")
        try:
            return fn()
        except Exception:
            self._open_until = self.mono() + self.breaker_s
            raise

    def _quick(self):
        q = getattr(self.remote, "quick", None)
        return q() if q is not None else self.remote

    # ---------------------------------------------------------------- the Store interface
    def insert(self, table: str, row, returning: bool = True):
        if not self._local(table):
            return self.remote.insert(table, row, returning=returning)
        rows = row if isinstance(row, list) else [row]
        out = []
        for r in rows:
            r = dict(r)
            r.setdefault("id", self._new_id())
            if table in DEFAULT_TS:
                r.setdefault(DEFAULT_TS[table], datetime.now(UTC))
            if table == "run":
                r.setdefault("kind", "paper")
            self._write({"op": "insert", "table": table, "row": r})
            out.append(self.tables[table][-1])
        return out[0] if isinstance(row, dict) else out

    def upsert(self, table: str, row, on_conflict: str):
        if not self._local(table):
            return self.remote.upsert(table, row, on_conflict)
        for r in row if isinstance(row, list) else [row]:
            self._write({"op": "upsert", "table": table, "row": dict(r), "on_conflict": on_conflict})
        return None

    def update(self, table: str, match: dict, values: dict):
        if not self._local(table):
            return self.remote.update(table, match, values)
        self._write({"op": "update", "table": table, "match": dict(match), "values": dict(values)})
        return None

    def select(self, table: str, params: dict) -> list[dict]:
        if not self._local(table):
            return self._remote(lambda: self._quick().select(table, params))
        return select_rows(self.tables.get(table, []), params)

    def rpc(self, fn: str, args: dict) -> Any:
        return self.remote.rpc(fn, args)

    def heartbeat(self, process: str, detail: dict) -> None:
        """Straight to Supabase (the watchdog and the dead-man read it there), quickly or not at all."""
        self.beats[process] = detail
        if self.remote is None:
            return
        try:
            self._remote(lambda: self._quick().heartbeat(process, detail))
        except Exception as e:  # Supabase down: the watchdog will see a stale heartbeat; trading carries on
            log.warning("heartbeat not sent: %s", str(e)[:120])

    def event(self, kind: str, detail: dict, run_id: int | None = None) -> None:
        """The hash-chained event log, chained in the journal (the chain is this engine's own)."""
        payload = canon(detail)
        ts = datetime.now(UTC)
        h = hashlib.sha256(
            f"{self._prev_hash or ''}|{ts.isoformat()}|{kind}|{json.dumps(payload, sort_keys=True)}".encode()
        ).hexdigest()
        self.insert("event", {"ts": ts, "run_id": run_id, "type": kind, "payload": payload,
                              "prev_hash": self._prev_hash, "hash": h}, returning=False)
        self._prev_hash = h

    def pending_commands(self) -> list[dict]:
        """The dashboard's commands. Supabase down: none (they wait there), and the loop goes on."""
        if self.remote is None:
            rows = [c for c in self.tables.get("command", []) if c.get("applied_at") is None]
        else:
            try:
                rows = self._remote(lambda: self._quick().pending_commands())
            except Exception as e:
                log.warning("commands not read (%s): trading and stops carry on", str(e)[:120])
                return []
        return [c for c in rows if str(c.get("id")) not in self.applied]

    def mark_applied(self, cmd_id: int, payload_update: dict | None = None) -> None:
        vals: dict = {"applied_at": datetime.now(UTC)}
        if payload_update is not None:
            vals["payload"] = payload_update
        self.applied.add(str(cmd_id))
        self._write({"op": "update", "table": "command", "match": {"id": cmd_id}, "values": vals})

    def active_run(self) -> dict | None:
        rows = [r for r in self.tables.get("run", []) if r.get("kind", "paper") == "paper"
                and r.get("status") in ("scheduled", "running", "paused")]
        return max(rows, key=lambda r: r["id"]) if rows else None

    def open_trades(self, run_id: int) -> list[dict]:
        return sorted((t for t in self.tables.get("trade", []) if str(t.get("run_id")) == str(run_id)
                       and t.get("exit_ts") is None), key=lambda t: t["id"])

    def run_realized(self, run_id: int) -> float:
        return float(sum(min(float(r["pnl_venue"]), float(r["pnl_shadow"]))
                         for r in self.tables.get("daily_result", []) if str(r.get("run_id")) == str(run_id)))

    def __getattr__(self, name: str):  # lease_acquire, lease_renew, lease_release, lease_row, ...: Supabase's
        if name.startswith("_") or self.__dict__.get("remote") is None:
            raise AttributeError(name)
        return getattr(self.remote, name)

    # ---------------------------------------------------------------- ids and bootstrap
    def _new_id(self) -> int:
        self._max_id += 1
        return self._max_id

    def bootstrap(self) -> dict:
        """First start of a journal on a machine: copy the active run's rows from Supabase, so the engine reads the
        same state it would have read there. Done once per journal. An empty journal with Supabase unreachable
        refuses to start (fail closed): the engine would not know its own open positions."""
        if self.remote is None or self.journal.count(("bootstrap",)):
            return {"imported": 0}
        try:
            run = self.remote.active_run()
            rows: dict[str, list[dict]] = {}
            if run:
                rid = run["id"]
                q = {"run_id": f"eq.{rid}"}
                rows["run"] = [run]
                for t in ("segment", "trade", "daily_result", "halt", "gate_random"):
                    rows[t] = self.remote.select(t, dict(q))
                rows["event"] = self.remote.select("event", {**q, "type": "eq.engine_start"})
                rows["signal"] = self.remote.select("signal", {**q, "mode": "eq.live", "order": "id.desc",
                                                               "limit": "200"})
                ids = ",".join(str(r["id"]) for r in rows["signal"])
                if ids:
                    for t in ("gate_run", "order"):
                        rows[t] = self.remote.select(t, {"signal_id": f"in.({ids})"})
            last = self.remote.select("event", {"select": "hash", "order": "id.desc", "limit": "1"})
        except Exception as e:
            raise BootstrapError(
                f"The journal is new and Supabase cannot be read ({str(e)[:160]}). The engine needs Supabase once, "
                "to copy the active run into the journal. Not starting.") from None
        n = 0
        for t, rs in rows.items():
            for r in sorted(rs, key=lambda r: (r.get("id") or 0)) if t != "signal" else reversed(rs):
                op = canon({"op": "insert", "table": t, "row": r})
                self.journal.append("import", op)
                self._apply(op)
                n += 1
        prev = last[0]["hash"] if last else None
        self.journal.append("bootstrap", {"run_id": run and run["id"], "rows": n, "prev_hash": prev})
        self._prev_hash = self._prev_hash or prev
        return {"imported": n, "run_id": run and run["id"]}


def select_rows(rows: list[dict], params: dict) -> list[dict]:
    """The PostgREST filters the engine uses: eq, neq, lt, lte, gt, gte, is.null, in.(...); order by any columns
    (asc / desc); limit. `select` is ignored (whole rows come back)."""
    out = list(rows)
    for k, v in params.items():
        if k in ("order", "limit", "select", "offset"):
            continue
        op, _, val = str(v).partition(".")
        if op == "eq":
            out = [r for r in out if _s(r.get(k)) == val]
        elif op == "neq":
            out = [r for r in out if _s(r.get(k)) != val]
        elif op in ("lt", "lte", "gt", "gte"):
            out = [r for r in out if r.get(k) is not None and _cmp(op, _s(r.get(k)), val)]
        elif op == "is" and val == "null":
            out = [r for r in out if r.get(k) is None]
        elif op == "in":
            opts = set(val.strip("()").split(","))
            out = [r for r in out if _s(r.get(k)) in opts]
    for part in reversed([p for p in str(params.get("order") or "").split(",") if p]):
        col, _, how = part.partition(".")
        out.sort(key=lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0),
                 reverse=how.startswith("desc"))
    if "limit" in params:
        out = out[: int(params["limit"])]
    return out


def _cmp(op: str, a: str, b: str) -> bool:
    return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]


def _s(v) -> str:
    return "true" if v is True else "false" if v is False else str(v)


# ---------------------------------------------------------------- replication
class Replicator:
    """Sends the journal's `row` entries to Supabase in order. Never raises out of `pump`: a failure stops this
    pass, is remembered (`last_error`) and retried by the next one."""

    def __init__(self, journal: Journal, remote, name: str = "supabase", batch: int = 500,
                 schema: str | None = None):
        self.journal, self.remote, self.name, self.batch = journal, remote, name, batch
        self.schema = schema  # SCHEMA_0018 / SCHEMA_PRE_0018; None: not known yet (detect() at the first contact)
        self.ids = journal.id_map(name)
        self.last_error: str | None = None
        self.sent = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def pending(self) -> int:
        return self.journal.count(("row",), after=self.journal.cursor(self.name))

    def detect(self) -> str | None:
        """Is migration 0018 applied? Asked once, at the first answer from Supabase; the answer holds until the
        engine restarts. None while Supabase cannot be reached (then nothing is sent and the next pass asks)."""
        if self.schema is not None:
            return self.schema
        q = getattr(self.remote, "quick", None)
        r = q() if q is not None else self.remote
        try:
            r.select("ledger_day", {"select": "run_id", "limit": "1"})
            r.select("segment", {"select": "jseq", "limit": "1"})
            self.schema = SCHEMA_0018
        except Exception as e:
            if not _schema_missing(e):
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                return None
            self.schema = SCHEMA_PRE_0018
            log.warning("migration 0018 is not applied: replicating to the pre-0018 tables (database ids, no "
                        "ledger_day / ledger_position) so the dashboard keeps updating. Apply "
                        "migrations/0018_m7_ledger.sql, then restart the engine to switch.")
        return self.schema

    def pump(self, max_n: int | None = None) -> int:
        if self.detect() is None:
            return 0
        done = 0
        while max_n is None or done < max_n:
            cur = self.journal.cursor(self.name)
            todo = self.journal.entries(after=cur, kinds=("row",), limit=self.batch)
            if not todo:
                break
            for seq, _, _, op in todo:
                try:
                    self._send(seq, op)
                except Exception as e:
                    if self.last_error is None:
                        log.warning("replication to %s paused at entry %d: %s", self.name, seq, str(e)[:200])
                    self.last_error = f"{type(e).__name__}: {e}"[:300]
                    return done
                self.journal.set_cursor(self.name, seq)
                done += 1
                self.sent += 1
                if max_n is not None and done >= max_n:
                    break
        if self.last_error is not None and done:
            log.info("replication to %s resumed", self.name)
            self.last_error = None
        return done

    def rebuild(self) -> int:
        """Send the whole journal again (into an empty or a partly filled database): idempotent. Needs 0018."""
        if self.detect() == SCHEMA_PRE_0018:
            raise RuntimeError("rebuild needs migration 0018 (without jseq a second send duplicates rows): "
                               "apply migrations/0018_m7_ledger.sql first")
        self.journal.set_cursor(self.name, 0)
        return self.pump()

    def start(self, every_s: float = 2.0) -> None:
        def loop():
            while not self._stop.is_set():
                with contextlib.suppress(Exception):
                    self.pump()
                self._stop.wait(every_s)

        self._thread = threading.Thread(target=loop, name="journal-replicator", daemon=True)
        self._thread.start()

    def stop(self, flush_s: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(flush_s)
        t0 = time.monotonic()
        while time.monotonic() - t0 < flush_s and self.pump(self.batch):
            pass

    def status(self) -> dict:
        return {"cursor": self.journal.cursor(self.name), "last_seq": self.journal.last_seq(),
                "pending": self.pending(), "error": self.last_error, "schema": self.schema}

    def _db_id(self, table: str, v):
        return self.ids.get((table, v), v) if isinstance(v, int) else v

    def _translate(self, op: dict) -> dict:
        """References to rows the database numbered itself (sent before 0018) take the database's id."""
        if not self.ids:
            return op
        op = dict(op)

        def fks(d: dict) -> dict:
            return {k: self._db_id(FK_COLUMNS[k], v) if k in FK_COLUMNS else v for k, v in d.items()}

        if op["op"] in ("insert", "upsert"):
            op["row"] = fks(op["row"])
        else:
            m = fks(op["match"])
            if op["table"] in ID_TABLES and "id" in m:
                m["id"] = self._db_id(op["table"], m["id"])
            op["match"], op["values"] = m, fks(op["values"])
        return op

    def _send(self, seq: int, op: dict) -> None:
        t = op["table"]
        op = self._translate(op)
        if self.schema != SCHEMA_PRE_0018:
            return send(self.remote, seq, op)
        if t in LEDGER_TABLES:  # no such tables before 0018: the ledger stays in the journal
            return None
        if op["op"] != "insert":
            return send(self.remote, seq, op)
        row = dict(op["row"])
        eid = row.pop("id", None)
        if t not in ID_TABLES:
            self.remote.insert(t, row, returning=False)
            return None
        if (t, eid) in self.ids:  # sent already
            return None
        out = self.remote.insert(t, row, returning=True)
        out = out[0] if isinstance(out, list) else out
        if not out or out.get("id") is None:
            raise RuntimeError(f"insert into {t} returned no id")
        self.journal.map_id(self.name, t, eid, out["id"])
        self.ids[(t, eid)] = int(out["id"])
        return None


def _schema_missing(e: Exception) -> bool:
    """PostgREST's answers for a table or column that does not exist (as opposed to Supabase being down)."""
    text = str(e)
    return any(c in text for c in ("PGRST205", "PGRST204", "42P01", "42703", "does not exist", "Could not find"))


def _duplicate(e: Exception) -> bool:
    text = str(e)
    return "409" in text or "23505" in text or "duplicate key" in text


def send(remote, seq: int, op: dict) -> None:
    t, kind = op["table"], op["op"]
    if kind == "insert":
        row = dict(op["row"])
        if t not in ID_TABLES:
            row.pop("id", None)
        if t in JSEQ_TABLES:
            row["jseq"] = seq
        try:
            remote.insert(t, row, returning=False)
        except Exception as e:
            if not _duplicate(e):  # sent before, answer lost: it is there
                raise
    elif kind == "update":
        remote.update(t, op["match"], op["values"])
    elif kind == "upsert":
        remote.upsert(t, op["row"], op["on_conflict"])


# ---------------------------------------------------------------- command line
def main(argv: list[str] | None = None) -> int:
    """`python -m kaibot.engine.journal --status | --rebuild` (never while an engine runs on the same file)."""
    ap = argparse.ArgumentParser(description="the engine's local journal")
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="send every row to Supabase again (idempotent)")
    a = ap.parse_args(argv)
    from kaibot.engine.config import Settings
    from kaibot.engine.store import Store

    s = Settings.from_env()
    j = Journal(a.path or journal_path(s.data_dir))
    rep = Replicator(j, Store(s.supabase_url, s.supabase_service_key))
    if a.rebuild:
        try:
            print(json.dumps({"sent": rep.rebuild(), **rep.status()}))
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
    else:
        print(json.dumps(rep.status()))
    return 0


def journal_path(data_dir: Path) -> Path:
    return Path(data_dir) / "journal.sqlite"


if __name__ == "__main__":
    sys.exit(main())
