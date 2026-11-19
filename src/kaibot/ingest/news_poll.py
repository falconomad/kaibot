"""The engine's news poller: Alpaca/Benzinga news into kaibot.news_item on a schedule (Data, 19 Nov 2026).

Why: the news desk (kaibot.news.NewsDesk, story S33) was only ever polled on demand, by the agent room's evidence
pack (off), the live pre-open review (off) and the shadow pre-open review (#310, once a session at the 15:50 ET send
step, and only with an LLM key and a planned buy). With those off or not yet run nothing fetched news, so
news_item stayed empty ("news window: 0 items reloaded") and the dashboard's stock summaries (kb_summary_facts) had
no news either.

What: one daemon thread inside the engine process (so the engine stays the only writer of the desk's dedup window
and no second process or launchd job is needed; same Docker image anywhere). Every 5 minutes on weekdays
04:00-20:00 ET and every 30 minutes otherwise it fetches the news of the engine's scope:

    planned   today's planned buys of the daily path (the latest `md_decision` per rule whose fill session is today
              or later: target weights and `features.buy`), first in the request order
    held      the intraday and multi-day book
    watch     the market's symbols (`LiveMarket.all`: the seed universe, custom tickers, sector ETFs, SPY)

A symbol seen for the first time gets a 72-hour backfill (the pre-open review's window, PREOPEN_NEWS_HOURS), so a
planned buy decided after the close (20:15 ET) has its news hours before the review at the send step. Later polls
ask only for the time since the last good poll plus a 10-minute overlap (the desk skips what it already stores).
With the agent room on (the desk has a gateway, so every new cluster costs one Haiku triage) only planned and held
symbols are polled.

Point-in-time: the desk stamps every row's available_ts with its own clock at ingest, never the provider's time.

The fetch runs outside the desk's lock (`LockedDesk.lock`), the insert inside it, so the tick's pump-risk read and
the reviews never wait on the network. Requests go through AlpacaData.news, under the shared `alpaca` budget of
config/sources.yaml (150/min): about 2-3 requests a poll for ~75 symbols, roughly 600 a weekday.

Health: `status()` is the heartbeat's `news` block (state starting | ok | empty | failing, last poll, last item,
counts of the ET day). `take_events()` hands the main loop one `news_feed_unhealthy` event when the state turns
failing (every request of a poll failed) or empty (no story for 2 hours on a weekday 09:30-16:00 ET), and one
`news_feed_ok` when it recovers. Switch: KAIBOT_NEWS_POLL=off stops the poller (default on).
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("kaibot.ingest.news_poll")
ET = ZoneInfo("America/New_York")
FLAG_ENV = "KAIBOT_NEWS_POLL"
BACKFILL = timedelta(hours=72)  # = engine/m3glue.PREOPEN_NEWS_HOURS
OVERLAP = timedelta(minutes=10)
EVERY_S, QUIET_EVERY_S = 300.0, 1800.0
ACTIVE = (time(4, 0), time(20, 0))  # ET, weekdays: pre-market to after-hours
EMPTY_AFTER = timedelta(hours=2)
EMPTY_WINDOW = (time(9, 30), time(16, 0))  # ET, weekdays: a liquid universe without a story for 2 h is a dead feed
CHUNK = 50  # symbols per request (Alpaca's news endpoint)


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(FLAG_ENV) or "on").strip().lower() not in ("off", "0", "false", "no")


@dataclass
class Scope:
    watch: set[str] = field(default_factory=set)
    planned: set[str] = field(default_factory=set)
    held: set[str] = field(default_factory=set)


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat() if t is not None else None


def _weekday_between(t: datetime, window: tuple[time, time]) -> bool:
    et = t.astimezone(ET)
    return et.weekday() < 5 and window[0] <= et.time() < window[1]


class NewsPoller:
    """`desk`: the engine's NewsDesk (its `ingest` and `gateway` are used). `source`: AlpacaData (its `news`).
    `scope_fn`: () -> Scope. `lock`: the LockedDesk's lock, shared with the room and the reviews."""

    def __init__(self, desk, source, scope_fn: Callable[[], Scope], *, lock: threading.Lock | None = None,
                 clock: Callable[[], datetime] | None = None, tick_s: float = 15.0) -> None:
        self.desk, self.source, self.scope_fn = desk, source, scope_fn
        self.lock = lock or threading.Lock()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tick_s = tick_s
        self._known: set[str] = set()  # symbols fetched successfully at least once (backfilled)
        self._scope: Scope | None = None
        self._state, self._error = "starting", None
        self._reported = "ok"  # the last state an event was emitted for
        self._events: list[tuple[str, dict]] = []
        self._started: datetime | None = None
        self._last_poll = self._last_ok = self._last_item = None
        self._day: date | None = None
        self._counts = {"calls": 0, "fetched": 0, "added": 0}
        self._planned_polled = False
        self._n = {"symbols": 0, "planned": 0}
        self._mu = threading.Lock()  # status/events between the poller thread and the main loop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------- schedule ----------------
    def every_s(self, now: datetime | None = None) -> float:
        return EVERY_S if _weekday_between(now or self.clock(), ACTIVE) else QUIET_EVERY_S

    def due(self) -> bool:
        now = self.clock()
        return self._last_poll is None or (now - self._last_poll).total_seconds() >= self.every_s(now)

    # ---------------- one poll ----------------
    def _fetch(self, symbols: list[str], start: datetime, end: datetime, raw: list, errors: list) -> int:
        ok = 0
        for i in range(0, len(symbols), CHUNK):
            chunk = symbols[i:i + CHUNK]
            try:
                raw.extend(self.source.news(chunk, start, end) or [])
                ok += 1
                self._known.update(chunk)
            except Exception as e:  # one failed chunk must not lose the others
                errors.append(f"{type(e).__name__}: {e}"[:160])
            self._counts["calls"] += 1
        return ok

    def poll_once(self) -> dict:
        """Fetch (outside the desk lock), store (inside it), update the health. Never raises."""
        now = self.clock()
        self._roll_day(now)
        if self._started is None:
            self._started = now
        errors: list[str] = []
        raw: list[dict] = []
        calls = ok = added = 0
        try:
            sc = self.scope_fn()
            self._scope = sc
        except Exception as e:  # e.g. the book changed while it was read: the last scope, else nothing
            log.warning("news scope unreadable: %s", type(e).__name__)
            sc = self._scope
            if sc is None:
                errors.append(f"scope: {type(e).__name__}: {e}"[:160])
        if sc is not None:
            focus = sc.planned | sc.held
            syms = focus if getattr(self.desk, "gateway", None) is not None else focus | sc.watch
            ordered = sorted(sc.planned) + sorted(sc.held - sc.planned) + sorted(syms - focus)
            ordered = [s for s in ordered if s in syms]
            new = [s for s in ordered if s not in self._known]
            old = [s for s in ordered if s in self._known]
            before = self._counts["calls"]
            if new:
                ok += self._fetch(new, now - BACKFILL, now, raw, errors)
            if old:
                since = max(now - BACKFILL, (self._last_ok or now - BACKFILL) - OVERLAP)
                ok += self._fetch(old, since, now, raw, errors)
            calls = self._counts["calls"] - before
            if raw:
                try:
                    with self.lock:
                        out = self.desk.ingest(raw)
                    added = int(out.get("added") or 0)
                except Exception as e:
                    errors.append(f"store: {type(e).__name__}: {e}"[:160])
            with self._mu:
                self._n = {"symbols": len(syms), "planned": len(sc.planned)}
                self._planned_polled = sc.planned <= self._known
        fetched = len(raw)
        with self._mu:
            self._last_poll = now
            self._counts["fetched"] += fetched
            self._counts["added"] += added
            if ok:
                self._last_ok = now
            if fetched:
                self._last_item = now
            failing = sc is None or (calls > 0 and ok == 0)
            self._error = "; ".join(errors)[:300] or None
            if failing:
                self._state = "failing"
            elif self._empty(now):
                self._state = "empty"
                self._error = (f"no news for any of {self._n['symbols']} symbols in "
                               f"{EMPTY_AFTER.total_seconds() / 3600:.0f} h of market hours")
            else:
                self._state = "ok"
            self._transition()
        if errors:
            log.warning("news poll: %s", self._error)
        return {"fetched": fetched, "added": added, "calls": calls, "ok": ok, "state": self._state}

    def _empty(self, now: datetime) -> bool:
        if not _weekday_between(now, EMPTY_WINDOW) or self._started is None:
            return False
        if now - self._started < EMPTY_AFTER:
            return False
        return self._last_item is None or now - self._last_item >= EMPTY_AFTER

    def _transition(self) -> None:
        bad = self._state in ("failing", "empty")
        if bad and self._reported != self._state:
            self._events.append(("news_feed_unhealthy", self._detail()))
            self._reported = self._state
        elif self._state == "ok" and self._reported != "ok":
            self._events.append(("news_feed_ok", self._detail()))
            self._reported = "ok"

    def _detail(self) -> dict:
        return {"state": self._state, "error": self._error, "symbols": self._n["symbols"],
                "last_ok": _iso(self._last_ok), "last_item": _iso(self._last_item)}

    def _roll_day(self, now: datetime) -> None:
        d = now.astimezone(ET).date()
        if d != self._day:
            self._day = d
            self._counts = {"calls": 0, "fetched": 0, "added": 0}

    # ---------------- what the main loop reads ----------------
    def status(self) -> dict:
        """The heartbeat's `news` block."""
        with self._mu:
            return {"state": self._state, "error": self._error, "last_poll": _iso(self._last_poll),
                    "last_ok": _iso(self._last_ok), "last_item": _iso(self._last_item),
                    "symbols": self._n["symbols"], "planned": self._n["planned"],
                    "planned_polled": self._planned_polled, "day": str(self._day) if self._day else None,
                    "calls_today": self._counts["calls"], "fetched_today": self._counts["fetched"],
                    "added_today": self._counts["added"], "every_s": self.every_s()}

    def take_events(self) -> list[tuple[str, dict]]:
        with self._mu:
            out, self._events = self._events, []
        return out

    # ---------------- thread ----------------
    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    if self.due():
                        self.poll_once()
                except Exception:  # poll_once never raises; belt and braces for the thread
                    log.exception("news poller step failed")
                self._stop.wait(self.tick_s)

        self._thread = threading.Thread(target=loop, name="news-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            with contextlib.suppress(Exception):
                self._thread.join(timeout_s)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------- the engine's scope ----------------
def planned_symbols(daily, today: date) -> set[str]:
    """Symbols of each rule's latest `md_decision` for a fill session on or after `today`: target weights and the
    decision's buys. Empty without the daily path."""
    if daily is None:
        return set()
    latest: dict[str, dict] = {}
    for b in daily._entries("md_decision"):
        if str(b.get("fill_session") or "") >= str(today):
            latest[str(b.get("strategy"))] = b
    out: set[str] = set()
    for b in latest.values():
        t = b.get("target") or {}
        out |= {str(p[0]) for p in t.get("weights") or [] if p}
        out |= {str(s) for s in (t.get("features") or {}).get("buy") or []}
    return out


def engine_scope(eng, today: date | None = None) -> Scope:
    today = today or datetime.now(ET).date()
    held = set(list(eng.pos))
    with contextlib.suppress(Exception):
        held |= set(eng._md_symbols())
    return Scope(watch=set(getattr(eng.market, "all", []) or []),
                 planned=planned_symbols(getattr(eng, "daily", None), today), held=held)


def for_engine(eng, desk, source, lock: threading.Lock | None, env: Mapping[str, str] | None = None
               ) -> NewsPoller | None:
    """The engine's poller, or None with KAIBOT_NEWS_POLL=off."""
    if not enabled(env):
        log.warning("news poller: OFF (%s=off): news_item is filled only by the reviews' own polls", FLAG_ENV)
        return None
    return NewsPoller(desk, source, lambda: engine_scope(eng), lock=lock)


__all__ = ["NewsPoller", "Scope", "engine_scope", "planned_symbols", "for_engine", "enabled", "FLAG_ENV"]
