"""Supabase (PostgREST) writer for schema `kaibot`, using the service_role key. Engine-side only: the key stays
in the engine's .env and never reaches the browser or the repo."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, url: str, service_key: str):
        self.base = f"{url}/rest/v1"
        self._prev_hash: str | None = None
        self._chain_loaded = False
        self.h = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Accept-Profile": "kaibot",
            "Content-Profile": "kaibot",
        }

    def _call(self, method: str, path: str, params: dict | None = None, body: Any = None, prefer: str | None = None):
        url = f"{self.base}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="(),.*:")
        h = dict(self.h)
        if prefer:
            h["Prefer"] = prefer
        data = json.dumps(body, default=_json).encode() if body is not None else None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, data=data, headers=h, method=method)
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code >= 500 and attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise StoreError(f"{method} {path} -> {e.code}: {text[:300]}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise StoreError(f"{method} {path}: {e}") from None
        return None

    # ---------- generic ----------
    def insert(self, table: str, row: dict | list[dict], returning: bool = True):
        out = self._call("POST", table, body=row, prefer="return=representation" if returning else "return=minimal")
        if returning and isinstance(row, dict):
            return out[0] if out else None
        return out

    def upsert(self, table: str, row: dict | list[dict], on_conflict: str):
        return self._call(
            "POST", table, {"on_conflict": on_conflict}, body=row, prefer="resolution=merge-duplicates,return=minimal"
        )

    def update(self, table: str, match: dict, values: dict):
        params = {k: f"eq.{v}" for k, v in match.items()}
        return self._call("PATCH", table, params, body=values, prefer="return=minimal")

    def select(self, table: str, params: dict) -> list[dict]:
        return self._call("GET", table, params) or []

    # ---------- engine helpers ----------
    def heartbeat(self, process: str, detail: dict) -> None:
        self.upsert("heartbeat", {"process": process, "ts": datetime.now(UTC), "detail": detail}, "process")

    def event(self, kind: str, detail: dict, run_id: int | None = None) -> None:
        """Append to the hash-chained event log (append-only in Postgres)."""
        if not self._chain_loaded:
            last = self.select("event", {"select": "hash", "order": "id.desc", "limit": "1"})
            self._prev_hash = last[0]["hash"] if last else None
            self._chain_loaded = True
        payload = json.loads(json.dumps(detail, default=_json))
        ts = datetime.now(UTC)
        h = hashlib.sha256(
            f"{self._prev_hash or ''}|{ts.isoformat()}|{kind}|{json.dumps(payload, sort_keys=True)}".encode()
        ).hexdigest()
        self.insert(
            "event",
            {"ts": ts, "run_id": run_id, "type": kind, "payload": payload, "prev_hash": self._prev_hash, "hash": h},
            returning=False,
        )
        self._prev_hash = h

    def pending_commands(self) -> list[dict]:
        return self.select("command", {"applied_at": "is.null", "order": "id.asc"})

    def mark_applied(self, cmd_id: int, payload_update: dict | None = None) -> None:
        vals: dict = {"applied_at": datetime.now(UTC)}
        if payload_update is not None:
            vals["payload"] = payload_update
        self.update("command", {"id": cmd_id}, vals)

    def active_run(self) -> dict | None:
        rows = self.select(
            "run", {"kind": "eq.paper", "status": "in.(scheduled,running,paused)", "order": "id.desc", "limit": "1"}
        )
        return rows[0] if rows else None

    def open_trades(self, run_id: int) -> list[dict]:
        return self.select("trade", {"run_id": f"eq.{run_id}", "exit_ts": "is.null", "order": "id.asc"})

    def run_realized(self, run_id: int) -> float:
        rows = self.select("daily_result", {"run_id": f"eq.{run_id}", "select": "pnl_reported"})
        return float(sum(float(r["pnl_reported"]) for r in rows))


def _json(o):
    if isinstance(o, datetime):
        return o.astimezone(UTC).isoformat()
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    raise TypeError(type(o))
