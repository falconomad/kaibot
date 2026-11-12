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
point to, a `jseq` column with the journal sequence number elsewhere, natural keys for the rest), so a retry after
an unknown outcome and a full `rebuild()` never duplicate a row. A "duplicate key" answer is checked against the
stored row: the same row is done; another row is a conflict, recorded and reported, and replication goes on.
Migration 0018 makes the ids and `jseq` acceptable to the database.

Every journal has an identity (a uuid and an epoch, in its `meta` table); its ids and `jseq` values live in its own
range (`epoch << 32`), so two journals never share a key. At every start `JournalStore.verify_identity` refuses a
journal that is not the newest one Supabase has seen (a stale standby, a restored backup) unless adopted
(`--adopt-journal`; docs/ops/ledger.md, "Taking over").

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
import re
import sqlite3
import sys
import threading
import time
import uuid
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
LIVE_ID_BASE = 1_000_000_000  # every engine-assigned id is above this (the database's own sequences stay below)
# Journal identity (B1 of the #117 review): every journal file has a uuid and an epoch drawn from it. Its ids and its
# `jseq` values are `epoch << 32 | n`, so two journals (a standby, a recreated volume, a new machine) never share a
# key in Supabase. Epochs stay below 2**21 so every key is below 2**53 (exact in JavaScript, i.e. the dashboard).
ID_SPAN = 1 << 32
EPOCHS = (1 << 21) - 1
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
create table if not exists meta (k text primary key, v text not null);
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
            if self.meta("journal_id") is None:  # a new file: a new identity, written once
                jid = uuid.uuid4()
                self.db.execute("insert or ignore into meta (k, v) values ('journal_id', ?), ('epoch', ?), "
                                "('created', ?)", (jid.hex, str(jid.int % EPOCHS + 1),
                                                   self.now().astimezone(UTC).isoformat()))
        self.journal_id: str = str(self.meta("journal_id"))
        self.epoch: int = int(str(self.meta("epoch")))
        self.id_base: int = self.epoch * ID_SPAN

    def meta(self, k: str) -> str | None:
        with self.lock:
            r = self.db.execute("select v from meta where k = ?", (k,)).fetchone()
        return r[0] if r else None

    def jseq(self, seq: int) -> int:
        """The key a row takes in Supabase: unique across journals."""
        return self.id_base + int(seq)

    def skip_to(self, seq: int) -> None:
        """The next entry gets a number above `seq` (adopting after a restore: keys Supabase holds are not reused).
        The entries themselves are untouched."""
        with self.lock:
            if seq > self.last_seq():
                self.db.execute("insert or ignore into sqlite_sequence (name, seq) values ('entry', 0)")
                self.db.execute("update sqlite_sequence set seq = ? where name = 'entry' and seq < ?", (seq, seq))

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


class IdentityError(RuntimeError):
    """This journal is not the newest one Supabase has seen (a stale standby or a restored backup)."""


class JournalStore:
    """The engine's store (the interface of engine/store.Store) on top of the journal. `remote` is the Supabase
    store (None in research: then every table is local)."""

    def __init__(self, journal: Journal, remote=None, *, live: bool = False, id_base: int = 0,
                 breaker_s: float = 30.0, mono: Callable[[], float] = time.monotonic):
        """`live`: ids come from this journal's own range (`epoch << 32`), unique across journals; else from
        `id_base` up (research: 1, 2, 3, ...)."""
        self.journal, self.remote = journal, remote
        self.id_base = journal.id_base if live else id_base
        self._id_hi = self.id_base + ID_SPAN if live else None
        self.breaker_s, self.mono = breaker_s, mono
        self._open_until = -1.0  # remote reads are skipped until then (after a failure)
        self.tables: dict[str, list[dict]] = {}
        self.applied: set = set()  # command ids this engine has applied (the update may not have reached Supabase)
        self.beats: dict = {}
        self._max_id = self.id_base
        self._prev_hash: str | None = None
        # other journals this one has seen in Supabase and accepted (at its bootstrap, or adopted): id -> seq,
        # epoch -> the highest journal number of theirs it knew of
        self.known_journals: dict[str, int] = {}
        self.known_epochs: dict[int, int] = {}
        for _, _, kind, op in journal.entries(kinds=("row", "import", "bootstrap", "adopt")):
            if kind in ("bootstrap", "adopt"):
                self._prev_hash = op.get("prev_hash") or self._prev_hash
                self._know(op)
                continue
            self._apply(op)

    def _know(self, op: dict) -> None:
        for k, v in (op.get("known") or {}).items():
            self.known_journals[k] = max(int(v), self.known_journals.get(k, 0))
        for k, v in (op.get("epochs") or {}).items():
            self.known_epochs[int(k)] = max(int(v), self.known_epochs.get(int(k), 0))
        if op.get("id_floor"):
            self._max_id = max(self._max_id, int(op["id_floor"]))

    # ---------------------------------------------------------------- the fold
    def _apply(self, op: dict) -> None:
        t, kind = op["table"], op["op"]
        rows = self.tables.setdefault(t, [])
        if kind == "insert":
            row = dict(op["row"])
            rows.append(row)
            if isinstance(row.get("id"), int) and self._ours(row["id"]):
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

    def _ours(self, i: int) -> bool:
        """An id from this journal's range (imported rows of another journal keep theirs)."""
        return i >= self.id_base and (self._id_hi is None or i < self._id_hi)

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
        if self.remote is None:
            self.beats[process] = detail
            return
        # which journal runs this engine, and how far it is: what the next start checks (verify_identity)
        detail = {**detail, "journal": {"id": self.journal.journal_id, "seq": self.journal.last_seq()}}
        self.beats[process] = detail
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

    def observe(self, process: str = "engine") -> dict:
        """What Supabase says about the journals that have written to it (read at start, fail closed):

        - `heartbeat`: the journal the engine's last heartbeat came from, and how far it was (id, seq);
        - `latest`: with 0018, the epoch and number of the newest row of each jseq table (None before 0018);
        - `ours`: with 0018, the highest journal number and id of this journal's own range found there."""
        hb = self.remote.select("heartbeat", {"process": f"eq.{process}"})
        detail = (hb[0].get("detail") or {}) if hb else {}
        j = detail.get("journal") if isinstance(detail, dict) else None
        out: dict = {"heartbeat": {"id": j.get("id"), "seq": int(j.get("seq") or 0), "host": detail.get("host")}
                     if isinstance(j, dict) and j.get("id") else None, "latest": None, "ours": None}
        lo, hi = self.journal.id_base, self.journal.id_base + ID_SPAN
        try:
            latest, seq, ids = {}, 0, 0
            for t in sorted(JSEQ_TABLES):
                r = self.remote.select(t, {"select": "jseq", "jseq": "not.is.null", "order": "id.desc", "limit": "1"})
                if r and r[0].get("jseq") is not None:
                    latest[t] = divmod(int(r[0]["jseq"]), ID_SPAN)
                r = self.remote.select(t, {"select": "jseq", "and": f"(jseq.gte.{lo},jseq.lt.{hi})",
                                           "order": "jseq.desc", "limit": "1"})
                if r:
                    seq = max(seq, int(r[0]["jseq"]) - lo)
            for t in sorted(ID_TABLES):
                r = self.remote.select(t, {"select": "id", "and": f"(id.gte.{lo},id.lt.{hi})", "order": "id.desc",
                                           "limit": "1"})
                if r:
                    ids = max(ids, int(r[0]["id"]))
        except Exception as e:
            if not _schema_missing(e):
                raise
            return out  # before 0018: no jseq; the heartbeat is all there is
        out["latest"], out["ours"] = latest, {"seq": seq, "id": ids}
        return out

    @staticmethod
    def _seen_now(obs: dict) -> dict:
        """The other journals in an observation, as a bootstrap or adopt entry records them."""
        hb = obs.get("heartbeat")
        epochs: dict[str, int] = {}
        for ep, n in (obs.get("latest") or {}).values():
            epochs[str(ep)] = max(n, epochs.get(str(ep), 0))
        return {"known": {hb["id"]: hb["seq"]} if hb else {}, "epochs": epochs}

    def verify_identity(self, process: str = "engine", adopt: bool = False) -> dict:
        """At every start, after the lease and the bootstrap: is this journal the newest one Supabase has seen?
        A stale standby (another journal ran the engine since this one last did) or a restored backup (Supabase
        holds rows of this journal beyond the end of the file) would replay old state and reuse keys, so the
        engine refuses to start (IdentityError) unless `adopt` (`--adopt-journal`, docs/ops/ledger.md): then the
        journal accepts what it sees, skips past its own keys found there, and says so in an event."""
        if self.remote is None:
            return {}
        try:
            obs = self.observe(process)
        except Exception as e:
            raise IdentityError(f"Supabase cannot be read to check the journal ({str(e)[:160]}). Not starting.") \
                from None
        me, last = self.journal.journal_id, self.journal.last_seq()
        problems = []
        hb = obs["heartbeat"]
        if hb and hb["id"] == me and hb["seq"] > last:
            problems.append(f"restored backup: the engine's last heartbeat came from this journal at entry "
                            f"{hb['seq']}, but the file ends at entry {last}")
        elif hb and hb["id"] != me and hb["seq"] > self.known_journals.get(hb["id"], -1):
            problems.append(f"stale standby: another journal ({hb['id'][:8]}, host {hb.get('host')}) ran the engine "
                            f"after this one last did (its entry {hb['seq']})")
        ours = obs.get("ours") or {}
        if ours.get("seq", 0) > last or ours.get("id", 0) > self._max_id:
            problems.append(f"restored backup: Supabase holds rows of this journal up to entry {ours.get('seq')} / "
                            f"id {ours.get('id')}, beyond the end of the file (entry {last}, id {self._max_id})")
        for t, (ep, n) in sorted((obs.get("latest") or {}).items()):
            if ep != self.journal.epoch and n > self.known_epochs.get(ep, -1):
                problems.append(f"stale standby: the newest {t} row in Supabase comes from another journal "
                                f"(epoch {ep}, entry {n}) this one has never seen")
                break
        if not problems:
            return {"ok": True, **obs}
        if not adopt:
            raise IdentityError(
                "This journal is not the newest one Supabase has seen: " + "; ".join(problems) + ". Not starting "
                "(it would trade on old state and its rows would collide in Supabase). See docs/ops/ledger.md "
                "'Taking over': move the file aside to start a fresh journal from Supabase, or, if this file is "
                "the true state, start once with --adopt-journal.")
        floor_seq = max(ours.get("seq", 0), last)
        self.journal.skip_to(floor_seq)
        body = {**self._seen_now(obs), "id_floor": max(ours.get("id", 0), self._max_id), "problems": problems,
                "skipped_to": floor_seq}
        self.journal.append("adopt", body)
        self._know(body)
        log.warning("journal adopted with --adopt-journal: %s", "; ".join(problems))
        self.event("journal_adopted", {"journal": me, "problems": problems, "skipped_to": floor_seq},
                   (self.active_run() or {}).get("id"))
        return {"ok": True, "adopted": problems, **obs}

    def bootstrap(self, process: str = "engine") -> dict:
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
            seen = self._seen_now(self.observe(process))  # the journals before this one: known, not newer
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
        body = {"run_id": run and run["id"], "rows": n, "prev_hash": prev, **seen}
        self.journal.append("bootstrap", body)
        self._know(body)
        self._prev_hash = self._prev_hash or prev
        return {"imported": n, "run_id": run and run["id"]}


def select_rows(rows: list[dict], params: dict) -> list[dict]:
    """The PostgREST filters the engine uses: eq, neq, lt, lte, gt, gte, is.null, in.(...); order by any columns
    (asc / desc); limit. `select` is ignored (whole rows come back)."""
    out = list(rows)
    conds = [(k, v) for k, v in params.items() if k not in ("order", "limit", "select", "offset", "and")]
    for part in str(params.get("and") or "").strip("()").split(","):  # and=(col.op.val,...)
        if part:
            col, _, rest = part.partition(".")
            conds.append((col, rest))
    for k, v in conds:
        op, _, val = str(v).partition(".")
        if op == "not" and val == "is.null":
            out = [r for r in out if r.get(k) is not None]
            continue
        if op == "eq":
            out = [r for r in out if _s(r.get(k)) == val]
        elif op == "neq":
            out = [r for r in out if _s(r.get(k)) != val]
        elif op in ("lt", "lte", "gt", "gte"):
            out = [r for r in out if r.get(k) is not None and _cmp(op, r.get(k), val)]
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


def _cmp(op: str, v, b: str) -> bool:
    """Numbers as numbers (ids, jseq), the rest as text (ISO dates and times sort as text)."""
    a: Any = _s(v)
    c: Any = b
    if isinstance(v, int | float) and not isinstance(v, bool):
        with contextlib.suppress(ValueError):
            a, c = float(v), float(b)
    return {"lt": a < c, "lte": a <= c, "gt": a > c, "gte": a >= c}[op]


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
        self.conflicts = 0
        self._conflicts: list[dict] = []
        self._lock = threading.Lock()
        self._pump_lock = threading.Lock()
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
        with self._pump_lock:  # one pass at a time (stop() may pump while the thread finishes its pass)
            return self._pump(max_n)

    def _pump(self, max_n: int | None) -> int:
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
                "pending": self.pending(), "error": self.last_error, "schema": self.schema,
                "conflicts": self.conflicts, "journal": self.journal.journal_id}

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
            return self._send_0018(seq, op)
        if t in LEDGER_TABLES:  # no such tables before 0018: the ledger stays in the journal
            return None
        if op["op"] != "insert":
            return send(self.remote, self.journal.jseq(seq), op)
        row = dict(op["row"])
        eid = row.pop("id", None)
        if t in ID_TABLES and (t, eid) in self.ids:  # sent already
            return None
        try:
            out = self.remote.insert(t, row, returning=t in ID_TABLES)
        except Exception as e:
            if not _duplicate(e):
                raise
            # sent before, the answer was lost (B2): the row with that natural key is ours if it matches
            out = self._settle(seq, t, row, e, NATURAL_KEYS.get(t))
            if out is None:
                return None
        if t not in ID_TABLES:
            return None
        out = out[0] if isinstance(out, list) else out
        if not out or out.get("id") is None:
            raise RuntimeError(f"insert into {t} returned no id")
        self.journal.map_id(self.name, t, eid, out["id"])
        self.ids[(t, eid)] = int(out["id"])
        return None

    def _send_0018(self, seq: int, op: dict) -> None:
        t = op["table"]
        if op["op"] != "insert":
            return send(self.remote, self.journal.jseq(seq), op)
        row = _row_0018(t, op["row"], self.journal.jseq(seq))
        try:
            self.remote.insert(t, row, returning=False)
        except Exception as e:
            if not _duplicate(e):
                raise
            keys = ("id",) if t in ID_TABLES else ("jseq",) if t in JSEQ_TABLES else NATURAL_KEYS.get(t)
            self._settle(seq, t, row, e, keys)
        return None

    def _settle(self, seq: int, t: str, row: dict, err: Exception, keys) -> dict | None:
        """A "duplicate key" answer: read the stored row by the key that clashed. The same row: sent before, done.
        Another row (or none to be found): a conflict, recorded and reported (`take_conflicts`), and replication
        goes on, never stuck on one entry. Returns the stored row (an ID table maps its id to it either way: other
        rows point at the row the broker knows by that key)."""
        cols = _clash_columns(err) or tuple(keys or ())
        stored = None
        if cols and all(c in row for c in cols):
            got = self.remote.select(t, {**{c: _eq_filter(row[c]) for c in cols}, "limit": "1"})
            stored = got[0] if got else None
        if stored is not None and same_row(row, stored):
            return stored
        c = {"seq": seq, "table": t, "key": {c: row.get(c) for c in cols},
             "reason": "a different row holds this key" if stored is not None else "duplicate key, row not found",
             "sent": row, "stored": stored, "error": str(err)[:200]}
        self.journal.append("replica_conflict", c)
        with self._lock:
            self._conflicts.append(c)
            self.conflicts += 1
        log.error("replica conflict in %s at entry %d (%s): kept going; the journal holds the engine's row",
                  t, seq, c["reason"])
        return stored

    def take_conflicts(self) -> list[dict]:
        """Conflicts since the last call (the engine turns each into a `replica_conflict` event and a halt row)."""
        with self._lock:
            out, self._conflicts[:] = list(self._conflicts), []
        return out


NATURAL_KEYS = {  # the unique keys of tables without an engine id or jseq (migrations 0001, 0008, 0010, 0018)
    "ledger": ("run_id", "ts"), "order": ("client_order_id",), "room_session": ("signal_id",),
    "gate_random": ("run_id", "session", "strategy"), "daily_result": ("run_id", "session"),
    "command": ("idempotency_key",), "ledger_day": ("run_id", "session"),
    "ledger_position": ("run_id", "session", "strategy", "symbol"),
}


def _row_0018(t: str, row: dict, jseq: int) -> dict:
    row = dict(row)
    if t not in ID_TABLES:
        row.pop("id", None)
    if t in JSEQ_TABLES:
        row["jseq"] = jseq
    return row


def _clash_columns(e: Exception) -> tuple[str, ...]:
    """Postgres names the key in a duplicate: `Key (run_id, ts)=(...) already exists.`"""
    m = re.search(r"Key \(([^)]*)\)=", str(e))
    return tuple(c.strip().strip('"') for c in m.group(1).split(",")) if m else ()


def _eq_filter(v) -> str:
    return "is.null" if v is None else f"eq.{_s(v)}"


def same_row(sent: dict, stored: dict) -> bool:
    """Is the stored row the one sent? Every column sent, as Postgres gives it back: numbers to the cent (numeric
    columns round), times as instants, JSON by value. Columns the database fills itself are not compared."""
    return all(_same(v, stored.get(k)) for k, v in sent.items() if k != "id" or "id" in stored)


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    if isinstance(a, int | float) or isinstance(b, int | float):
        try:
            x, y = float(a), float(b)
        except (TypeError, ValueError):
            return False
        return abs(x - y) <= 0.005 + 1e-9 * max(abs(x), abs(y))
    if isinstance(a, dict) and isinstance(b, dict):
        return all(_same(a.get(k), b.get(k)) for k in set(a) | set(b))
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if str(a) == str(b):
        return True
    try:
        return datetime.fromisoformat(str(a)) == datetime.fromisoformat(str(b))
    except ValueError:
        return False


def _schema_missing(e: Exception) -> bool:
    """PostgREST's answers for a table or column that does not exist (as opposed to Supabase being down)."""
    text = str(e)
    return any(c in text for c in ("PGRST205", "PGRST204", "42P01", "42703", "does not exist", "Could not find"))


def _duplicate(e: Exception) -> bool:
    text = str(e)
    return "409" in text or "23505" in text or "duplicate key" in text


def send(remote, jseq: int, op: dict) -> None:
    """Updates and upserts (idempotent as they are), and inserts outside a Replicator (no duplicate handling)."""
    t, kind = op["table"], op["op"]
    if kind == "insert":
        remote.insert(t, _row_0018(t, op["row"], jseq), returning=False)
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
