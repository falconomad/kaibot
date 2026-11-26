"""Audit 2, ticket 5.12 (#692): the engine starts the daily-bars fetch itself when its night job did not run.

The multi-day rules decide on the daily bar store, which `scripts/fetch_daily.sh` refreshes. Until now only the
service's night job (`com.kaibot.daily`, 03:15 and 07:45 on the Mac, Berlin time) ran it, so a Mac asleep through
both runs had no bars for the previous session, and the engine waited (`multi_day_waiting`, then
`multi_day_data_late` at the deadline) until somebody ran the script by hand: on 25 Nov 2026 the decision came at
16:05 ET, after its send window.

Now, at start-up and on waking (engine/gap.py: two loop passes more than an hour apart), the engine asks whether the
store is complete for the session whose bars are due (`entry_checks.expected_session`, the deadline the alarm uses)
while that session's decision can still be sent (before the fill session's send window opens, 15:50 ET or 10
minutes before an early close). If it is not, it starts the same script, `scripts/fetch_daily.sh`, as a background
process: its own process group, output appended to logs/fetch_daily.log like the night job's, killed after
FETCH_TIMEOUT_S. The engine's loop only polls it; nothing waits for it. The script holds a lock that the night job
holds too (scripts/fetch_lock.py, data/fetch_daily.lock), so two fetches never run at once: a second one says so and
ends at once. The fetch slows itself to 40 requests a minute while the engine runs (the script's own rule).

What the engine decides and sends does not change: the decision is taken as before, once the bars are in (an
earlier store is the only difference), and `multi_day_data_late` still raises the alarm at the deadline. Without
the script (the Docker image has no scripts/), with KAIBOT_ENGINE_FETCH=0, or without the multi-day path, nothing
is started. Events: `daily_fetch` (started, finished) and `daily_fetch_failed` (a non-zero exit or the timeout).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time as _time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("kaibot.engine")
ET = ZoneInfo("America/New_York")
FETCH_TIMEOUT_S = 45 * 60  # a full refresh at 40 requests a minute takes about 17 minutes
ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "fetch_daily.sh"
LOG = ROOT / "logs" / "fetch_daily.log"


class EngineFetch:
    """`wake(why)` asks for a check (at the start, on waking); `step()`, every loop pass, makes that check and
    starts the fetch when it is wanted, and polls a running one. `missing(k, now)`: why session k's daily view is
    not usable now (None: it is). `send_from(day)`: the ET time the send window of `day` opens. Never raises."""

    def __init__(self, missing: Callable[[date, datetime], str | None], prev_session: Callable[[date], date],
                 next_session: Callable[[date], date], send_from: Callable[[date], time],
                 event: Callable[[str, dict], None] = lambda kind, body: None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC), *,
                 cmd: Sequence[str] | None = None, cwd: Path = ROOT, log_path: Path | None = LOG,
                 timeout_s: float = FETCH_TIMEOUT_S, clock: Callable[[], float] = _time.monotonic,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self.missing, self.prev_session, self.next_session, self.send_from = (missing, prev_session, next_session,
                                                                              send_from)
        self.event, self.now, self.clock = event, now, clock
        self.cmd = list(cmd) if cmd is not None else ["/bin/bash", str(SCRIPT)]
        self.cwd, self.log_path, self.timeout_s, self.popen = Path(cwd), log_path, timeout_s, popen
        self.proc: subprocess.Popen | None = None
        self._started: float | None = None
        self._for: str | None = None  # the session the running fetch is for
        self._asked: str | None = None
        self._log = None

    def wake(self, why: str) -> None:
        self._asked = why

    def status(self) -> dict | None:
        if self.proc is None:
            return None
        return {"running": True, "session": self._for, "pid": self.proc.pid}

    def step(self) -> None:
        try:
            self._poll()
            if self._asked is not None and self.proc is None:
                why, self._asked = self._asked, None
                self._maybe_start(why)
        except Exception:  # a fetch must never cost the engine a pass
            log.exception("engine fetch step failed")

    # ------------------------------------------------------------------
    def due(self, now: datetime) -> tuple[date, str] | None:
        """(session, why) when its bars are due, missing, and its decision can still be sent; else None."""
        from kaibot.ingest.entry_checks import expected_session

        k = expected_session(now, self.prev_session)
        fill = self.next_session(k)
        opens = datetime.combine(fill, self.send_from(fill), ET)
        if now >= opens:
            return None  # the window of the decision these bars are for has opened: a fetch now changes nothing
        why = self.missing(k, now)
        return (k, why) if why else None

    def _maybe_start(self, asked: str) -> None:
        now = self.now()
        due = self.due(now)
        if due is None:
            return
        k, why = due
        if self.cmd[0] == "/bin/bash" and not Path(self.cmd[-1]).exists():
            log.warning("daily bars of %s missing (%s) and no %s here: not fetched by the engine", k, why[:160],
                        self.cmd[-1])
            return
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = open(self.log_path, "a")  # noqa: SIM115 - closed when the fetch ends
            self._log.write(f"== {now.astimezone(ET):%Y-%m-%d %H:%M:%S %Z} started by the engine ({asked})\n")
            self._log.flush()
        out = self._log if self._log is not None else subprocess.DEVNULL
        self.proc = self.popen(self.cmd, cwd=str(self.cwd), stdin=subprocess.DEVNULL, stdout=out,
                               stderr=subprocess.STDOUT, start_new_session=True, env=dict(os.environ))
        self._started, self._for = self.clock(), str(k)
        body = {"state": "started", "session": str(k), "why": why[:200], "asked": asked, "pid": self.proc.pid}
        log.warning("daily bars of %s missing at %s (%s): the engine starts ./scripts/fetch_daily.sh (pid %s; "
                    "log logs/fetch_daily.log)", k, asked, why[:160], self.proc.pid)
        self.event("daily_fetch", body)

    def _poll(self) -> None:
        p = self.proc
        if p is None:
            return
        rc = p.poll()
        took = self.clock() - (self._started if self._started is not None else self.clock())
        if rc is None and took < self.timeout_s:
            return
        if rc is None:  # past the bound: the whole group (bash, the downloader, caffeinate)
            with contextlib.suppress(Exception):
                os.killpg(p.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                rc = p.wait(timeout=10)
            if rc is None:
                with contextlib.suppress(Exception):
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=5)
            rc = "timeout"
        body = {"session": self._for, "exit": rc, "took_s": int(took)}
        self.proc, self._started, self._for = None, None, None
        if self._log is not None:
            with contextlib.suppress(Exception):
                self._log.close()
            self._log = None
        if rc == 0:
            log.info("the engine's daily fetch finished (%s s)", body["took_s"])
            self.event("daily_fetch", {"state": "finished", **body})
        else:
            log.error("the engine's daily fetch failed (exit %s after %s s): see logs/fetch_daily.log; "
                      "multi_day_data_late raises the alarm at the deadline", rc, body["took_s"])
            self.event("daily_fetch_failed", body)


def for_engine(eng) -> EngineFetch | None:
    """The engine's fetch, or None: no multi-day path, or KAIBOT_ENGINE_FETCH=0."""
    from kaibot.common import switches

    daily = getattr(eng, "daily", None)
    if daily is None or not switches.get("KAIBOT_ENGINE_FETCH"):
        return None
    load = daily.load

    def missing(k: date, now: datetime) -> str | None:
        from kaibot.engine.live_daily import DataNotReady, StoreFailedCheck

        quick = getattr(load, "available", None)
        if quick is not None:
            why = quick(k, now)
            if why:
                return why
        try:  # the view itself: a refresh that stopped part-way leaves SPY's bar but too few stocks
            load(k, now)
        except StoreFailedCheck:
            return None  # a bar fails the integrity check: a fetch does not cure that (docs/ops/daily_bars.md)
        except DataNotReady as e:
            return str(e)
        return None

    return EngineFetch(missing, daily.prev_session, daily.next_session, daily.send_from,
                       event=lambda kind, body: eng.store.event(kind, body, eng._rid()), now=eng.now)
