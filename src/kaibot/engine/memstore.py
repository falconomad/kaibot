"""An in-memory engine store for simulations (issue #116): the subset of engine/store.Store the engine uses,
kept in Python lists. The research command runs the engine on it. It is not a journal: the ledger story (#117,
S77) replaces it with the local append-only journal, and nothing here survives the process."""

from __future__ import annotations

import itertools


class MemoryStore:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.ids = itertools.count(1)
        self.beats: dict = {}

    def insert(self, table, row, returning=True):
        rows = row if isinstance(row, list) else [row]
        out = []
        for r in rows:
            r = dict(r)
            r.setdefault("id", next(self.ids))
            self.tables.setdefault(table, []).append(r)
            out.append(r)
        return out[0] if isinstance(row, dict) else out

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        t = self.tables.setdefault(table, [])
        for r in t:
            if all(str(r.get(k)) == str(row.get(k)) for k in keys):
                r.update(row)
                return
        t.append(dict(row))

    def update(self, table, match, values):
        for r in self.tables.get(table, []):
            if all(r.get(k) == v for k, v in match.items()):
                r.update(values)

    def select(self, table, params):
        """PostgREST-style filters the engine uses: eq, lte, is.null, in.(...); order id.desc or
        from_session.desc; limit."""
        rows = list(self.tables.get(table, []))
        for k, v in params.items():
            if k in ("order", "limit", "select"):
                continue
            op, _, val = v.partition(".")
            if op == "eq":
                rows = [r for r in rows if str(r.get(k)) == val]
            elif op == "lte":
                rows = [r for r in rows if str(r.get(k)) <= val]
            elif op == "is" and val == "null":
                rows = [r for r in rows if r.get(k) is None]
            elif op == "in":
                opts = val.strip("()").split(",")
                rows = [r for r in rows if str(r.get(k)) in opts]
        order = params.get("order", "")
        if order.startswith("from_session.desc"):
            rows.sort(key=lambda r: (r["from_session"], r["id"]), reverse=True)
        elif order.startswith("id.desc"):
            rows.sort(key=lambda r: r["id"], reverse=True)
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        return rows

    def heartbeat(self, process, detail):
        self.beats[process] = detail

    def event(self, kind, detail, run_id=None):
        self.insert("event", {"type": kind, "payload": detail, "run_id": run_id}, returning=False)

    def pending_commands(self):
        return [c for c in self.tables.get("command", []) if c.get("applied_at") is None]

    def mark_applied(self, cmd_id, payload_update=None):
        self.update("command", {"id": cmd_id},
                    {"applied_at": "now", **({"payload": payload_update} if payload_update else {})})

    def active_run(self):
        rows = [r for r in self.tables.get("run", []) if r.get("status") in ("scheduled", "running", "paused")]
        return rows[-1] if rows else None

    def open_trades(self, run_id):
        return [t for t in self.tables.get("trade", []) if t["run_id"] == run_id and t.get("exit_ts") is None]

    def run_realized(self, run_id):
        return sum(min(r["pnl_venue"], r["pnl_shadow"]) for r in self.tables.get("daily_result", [])
                   if r["run_id"] == run_id)
