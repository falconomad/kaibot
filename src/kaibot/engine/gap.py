"""QA-Ops (the 24-25 Nov 2026 incident): the engine says when it was not running while it should have been.

The owner's Mac mini fell asleep at 23:45 Berlin; the engine's heartbeat stopped for about 17 hours, the nightly bar
refresh did not run and the next multi-day decision came late. Nothing on the dashboard said so afterwards: the
watchdog only noticed after the Mac woke. Now the engine writes one `engine_gap` event (a health event, `warn`), and
logs one `engine gap: ...` WARNING line (a Healthchecks.io fail ping, scripts/healthcheck.py), with the gap's length,
in two cases:

- at a start, when the engine's last heartbeat row (Supabase, `process = <engine>`) is more than `GAP_S` old and was
  not a clean stop's (`state: stopped`, written by every clean stop, `service.sh stop`/`hold` included). A crash, a
  power cut or a sleep leaves the last running heartbeat behind: the engine should have been running all that time;
- while it runs, when two loop passes (normally about 10 s apart) are more than `GAP_S` apart by the wall clock: the
  process was frozen, which is what a Mac asleep with the engine still running looks like when it wakes.

A report only: nothing here trades, waits or stops the engine, and a failure to read or write is logged and
forgotten. A heartbeat that cannot be read at the start (Supabase down) means no check at that start.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

log = logging.getLogger("kaibot.engine")

GAP_S = 3600.0  # more than an hour without the engine: say so (a crash restarted by launchd is about a minute)


def _ts(v) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _hours(s: float) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h} h {m:02d} min"


class GapWatch:
    """`before_start` reads the last heartbeat (before this engine writes its own), `after_start` reports a gap since
    it, `tick` (every loop pass) reports a frozen process. Each gap is reported once."""

    def __init__(self, store, run_id: Callable[[], int | None], host: str, process: str = "engine",
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), gap_s: float = GAP_S):
        self.store, self.run_id, self.host, self.process, self.now, self.gap_s = (
            store, run_id, host, process, now, gap_s)
        self.prior: dict | None = None  # the last heartbeat before this start: {"ts", "state", "host"}
        self.last_pass: datetime | None = None

    def before_start(self, remote) -> None:
        """Read the engine's heartbeat row from Supabase (`remote`: the plain store, never the journal's breaker)."""
        if remote is None:
            return
        try:
            q = getattr(remote, "quick", None)
            rows = (q() if q is not None else remote).select("heartbeat",
                                                            {"process": f"eq.{self.process}", "limit": "1"})
        except Exception as e:
            log.info("last heartbeat unreadable at start (%s): no gap check this start", str(e)[:120])
            return
        if not rows:
            return
        detail = rows[0].get("detail") or {}
        detail = detail if isinstance(detail, dict) else {}
        ts = _ts(rows[0].get("ts"))
        if ts is not None:
            self.prior = {"ts": ts, "state": detail.get("state"), "host": detail.get("host")}

    def after_start(self) -> dict | None:
        p, self.last_pass = self.prior, self.now()
        if p is None or p["state"] == "stopped":  # never ran here, or stopped on purpose: no gap to report
            return None
        return self._report(p["ts"], self.last_pass, "start", last_state=p["state"], last_host=p["host"])

    def tick(self) -> dict | None:
        now, prev = self.now(), self.last_pass
        self.last_pass = now
        if prev is None:
            return None
        return self._report(prev, now, "running")

    def _report(self, since: datetime, until: datetime, seen: str, **extra) -> dict | None:
        secs = (until - since).total_seconds()
        if secs <= self.gap_s:
            return None
        why = ("the last heartbeat before this start was not a clean stop's" if seen == "start"
               else "the engine process was frozen between two loop passes (the Mac asleep?)")
        payload = {"gap_s": int(secs), "gap_h": round(secs / 3600, 2), "since": since.isoformat(),
                   "until": until.isoformat(), "seen": seen, "host": self.host,
                   **{k: v for k, v in extra.items() if v is not None}}
        log.warning("engine gap: no engine heartbeat for %s (%s to %s; %s). Decisions, the nightly refresh and the "
                    "watchdog may have been late or missed; check the Mac's sleep setting (run_engine.sh --check)",
                    _hours(secs), payload["since"][:16], payload["until"][:16], why)
        with contextlib.suppress(Exception):
            self.store.event("engine_gap", payload, self.run_id())
        return payload
