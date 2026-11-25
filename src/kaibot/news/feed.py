"""The dashboard's headline line: a bounded, fire-and-forget copy of headlines into kaibot.headline (migration 0043).

Display only. Nothing here decides, sizes or places anything, and nothing flows back: `offer` returns None and the
rest runs on this module's own daemon thread. No order path, strategy, risk, broker, agent or the news desk's
decision reads imports this module (tests/unit/test_news_tags.py); engine/__main__.py only wires it.

Source A (owner only, licence `owner_only`): the Alpaca/Benzinga articles the engine ALREADY fetches, handed over by
the news poller (ingest/news_poll.py, after its fetch, outside the desk lock) and by NewsDesk.poll (the pre-open
review's and the room's reads). No new news request is made for this (tests count the fetches). Each row: symbols,
the headline cleaned and trimmed to HEADLINE_MAX characters, source, the provider's created_at, the four shipped
tags where the tagger fires (FEED_TAGS), `focus` (a held or decided stock), licence, and an https link or none.

Source B (licence `public`, off by default: KAIBOT_HEADLINE_EDGAR): SEC 8-K filings of the held and decided stocks,
one line from the item codes (ingest/edgar_8k_feed.py). Asked on this thread, at most once per symbol in 10 minutes,
market hours only.

The licence is fixed by the source (alpaca_benzinga -> owner_only, sec_edgar -> public), here and again by a check
constraint in the table. The database function the dashboard reads returns owner_only rows only to the owner.

Bounds and failure: a queue of MAX_OFFERS offers of at most MAX_PER_OFFER articles each; a full queue drops the offer
(counted). Writes go in batches of BATCH rows on a one-try 5 s copy of the store. The first failure is logged once
and queued as a `headline_feed_failed` event (written by the main thread, so the event chain is never touched from
here); after a failure the feed waits RETRY_S before it writes again and drops what came meanwhile; the first
success after that is `headline_feed_ok`. Retention (7 days, 500 rows) is the table's own trigger. Nothing here
raises to a caller.

Switch: KAIBOT_HEADLINE_FEED (on unless switched off).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from kaibot.news import tags as T
from kaibot.news import text

log = logging.getLogger("kaibot.news.feed")
FLAG_ENV = "KAIBOT_HEADLINE_FEED"
EDGAR_ENV = "KAIBOT_HEADLINE_EDGAR"
TABLE = "headline"
HEADLINE_MAX = 140
MAX_OFFERS = 16
MAX_PER_OFFER = 200
BATCH = 50
MAX_SYMBOLS = 10
STORE_TIMEOUT_S = 5.0
RETRY_S = 60.0
EDGAR_EVERY_S = 30.0
SEEN_MAX = 5000
# The tags measured as shipping (tags.SHIPPED, = tagset/measure_v2.json "shipped", PR #597). One source: the tests
# pin it to measure_v2.json, to 0043's check constraint on kaibot.headline.tags and to the dashboard's Tag type.
FEED_TAGS: frozenset[str] = T.SHIPPED
LICENCES = {"alpaca_benzinga": "owner_only", "sec_edgar": "public"}
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def licence_of(source: str) -> str:
    """The licence a row of `source` carries. Unknown source: owner_only (fail closed)."""
    return LICENCES.get(source, "owner_only")


def https_url(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    v = text.clean(v, 500)
    return v if re.match(r"^https://[^\s\"'<>]+$", v) else None


def trim(headline: object, n: int = HEADLINE_MAX) -> str:
    h = text.clean(headline, 1000)
    if len(h) <= n:
        return h
    cut = h[: n - 1]
    if " " in cut[n // 2:]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "…"


def feed_tags(headline: str) -> list[str]:
    try:
        return sorted(T.all_tags(headline) & FEED_TAGS)
    except Exception:
        return []


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat()


def _ts(v: object) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def alpaca_row(raw: object, focus: frozenset[str], now: datetime) -> dict | None:
    """One untrusted Alpaca/Benzinga article -> a kaibot.headline row, or None."""
    if not isinstance(raw, dict):
        return None
    full = text.clean(raw.get("headline"), 1000)
    created = _ts(raw.get("created_at"))
    if not full or created is None:
        return None
    syms = raw.get("symbols") if isinstance(raw.get("symbols"), list) else []
    symbols = sorted({s.strip().upper() for s in syms if isinstance(s, str) and _SYMBOL.match(s.strip().upper())})
    if not symbols:
        return None
    # the stocks this headline is about first: a held or decided one, then the rest
    symbols = sorted(symbols, key=lambda s: (s not in focus, s))[:MAX_SYMBOLS]
    key = "alp:" + (str(raw.get("id")) if str(raw.get("id") or "").isdigit()
                    else hashlib.sha1(full.encode()).hexdigest())
    return {"dedup_key": key[:80], "symbols": symbols, "headline": trim(full), "source": "alpaca_benzinga",
            "licence": licence_of("alpaca_benzinga"), "url": https_url(raw.get("url")), "tags": feed_tags(full),
            "focus": bool(focus & set(symbols)), "created_at": _iso(min(created, now))}


def edgar_row(r: dict, focus: frozenset[str]) -> dict | None:
    syms = [s for s in r.get("symbols") or [] if isinstance(s, str) and _SYMBOL.match(s)][:MAX_SYMBOLS]
    h = trim(r.get("headline"))
    if not syms or not h or not isinstance(r.get("created_at"), datetime):
        return None
    tags = sorted(set(r.get("tags") or []) & FEED_TAGS)
    return {"dedup_key": str(r["key"])[:80], "symbols": syms, "headline": h, "source": "sec_edgar",
            "licence": licence_of("sec_edgar"), "url": https_url(r.get("url")), "tags": tags,
            "focus": bool(focus & set(syms)), "created_at": _iso(r["created_at"])}


class HeadlineFeed:
    """`store`: the Supabase store (service role). `focus_fn`: () -> held and decided symbols (for Source B and the
    flag of rows handed over without one). `edgar`: an EdgarFilings or None (Source B off)."""

    def __init__(self, store, *, focus_fn: Callable[[], Iterable[str]] | None = None, edgar=None,
                 clock: Callable[[], datetime] | None = None, max_offers: int = MAX_OFFERS,
                 tick_s: float = 1.0) -> None:
        quick = getattr(store, "quick", None)
        self.store = quick(STORE_TIMEOUT_S) if callable(quick) else store
        self.focus_fn = focus_fn or (lambda: ())
        self.edgar = edgar
        self.clock = clock or (lambda: datetime.now(UTC))
        self.tick_s = tick_s
        self._q: queue.Queue = queue.Queue(maxsize=max_offers)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._failing = False
        self._retry_at: datetime | None = None
        self._edgar_at: datetime | None = None
        self._events: list[tuple[str, dict]] = []
        self._mu = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.counts = {"offered": 0, "dropped": 0, "written": 0, "failed": 0, "edgar_rows": 0}

    # ---------------- called by the engine's threads: never blocks, never raises ----------------
    def offer(self, raw_items, focus: Iterable[str] = ()) -> None:
        try:
            items = list(raw_items or [])[:MAX_PER_OFFER]
            if not items:
                return
            self._q.put_nowait(("alpaca", items, frozenset(focus or ())))
            self.counts["offered"] += 1
        except queue.Full:
            self.counts["dropped"] += 1
        except Exception:  # an odd argument must not reach the caller
            self.counts["dropped"] += 1

    def take_events(self) -> list[tuple[str, dict]]:
        with self._mu:
            out, self._events = self._events, []
        return out

    def status(self) -> dict:
        return {"state": "failing" if self._failing else "ok", **self.counts,
                "edgar": "on" if self.edgar is not None else "off"}

    # ---------------- the feed's own thread ----------------
    def _focus(self) -> frozenset[str]:
        try:
            return frozenset(str(s) for s in self.focus_fn() or ())
        except Exception:
            return frozenset()

    def _new(self, rows: list[dict]) -> list[dict]:
        out, keys = [], set()
        for r in rows:
            k = r["dedup_key"]
            if k in self._seen or k in keys:
                continue
            keys.add(k)
            out.append(r)
        return out

    def _write(self, rows: list[dict]) -> None:
        rows = self._new(rows)
        if not rows:
            return
        now = self.clock()
        if self._retry_at is not None and now < self._retry_at:
            self.counts["dropped"] += 1
            return
        for i in range(0, len(rows), BATCH):
            part = rows[i:i + BATCH]
            try:
                self.store.upsert(TABLE, part, "dedup_key")
            except Exception as e:
                self.counts["failed"] += 1
                self._retry_at = now + timedelta(seconds=RETRY_S)
                if not self._failing:
                    self._failing = True
                    msg = f"{type(e).__name__}: {e}"[:200]
                    log.warning("headline feed: write failed, the dashboard's headlines pause (%s)", msg)
                    with self._mu:
                        self._events.append(("headline_feed_failed", {"error": msg}))
                return
            for r in part:
                self._seen[r["dedup_key"]] = None
            while len(self._seen) > SEEN_MAX:
                self._seen.popitem(last=False)
            self.counts["written"] += len(part)
        self._retry_at = None
        if self._failing:
            self._failing = False
            log.info("headline feed: writing again")
            with self._mu:
                self._events.append(("headline_feed_ok", {"written": self.counts["written"]}))

    def step(self) -> None:
        """One pass: what was offered, then Source B when it is due. Never raises."""
        now = self.clock()
        rows: list[dict] = []
        try:
            while True:
                _, items, focus = self._q.get_nowait()
                rows.extend(x for x in (alpaca_row(r, focus, now) for r in items) if x is not None)
        except queue.Empty:
            pass
        except Exception:
            log.exception("headline feed: reading the queue failed")
        if self.edgar is not None and (self._edgar_at is None
                                       or (now - self._edgar_at).total_seconds() >= EDGAR_EVERY_S):
            self._edgar_at = now
            focus = self._focus()
            try:
                got = [x for x in (edgar_row(r, focus) for r in self.edgar.rows(sorted(focus))) if x is not None]
                self.counts["edgar_rows"] += len(got)
                rows.extend(got)
            except Exception as e:
                log.debug("headline feed: edgar pass failed (%s)", type(e).__name__)
        if rows:
            try:
                self._write(rows)
            except Exception:
                log.exception("headline feed: write step failed")

    def start(self) -> None:
        if self.running():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                with contextlib.suppress(Exception):
                    self.step()
                self._stop.wait(self.tick_s)

        self._thread = threading.Thread(target=loop, name="headline-feed", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            with contextlib.suppress(Exception):
                self._thread.join(timeout_s)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def for_engine(store, focus_fn, env) -> HeadlineFeed | None:
    """The engine's feed, or None with KAIBOT_HEADLINE_FEED=off. Source B only with KAIBOT_HEADLINE_EDGAR=on and a
    valid SEC_USER_AGENT."""
    from kaibot.common import switches

    if not switches.get(FLAG_ENV, env):
        log.info("headline feed: OFF (%s=off)", FLAG_ENV)
        return None
    edgar = None
    if switches.get(EDGAR_ENV, env):
        from kaibot.ingest import edgar_8k_feed

        edgar = edgar_8k_feed.from_env(env)
        if edgar is None:
            log.warning("headline feed: %s=on but SEC_USER_AGENT is not 'Name contact@domain': EDGAR filings off",
                        EDGAR_ENV)
    return HeadlineFeed(store, focus_fn=focus_fn, edgar=edgar)


__all__ = ["HeadlineFeed", "FEED_TAGS", "LICENCES", "licence_of", "alpaca_row", "edgar_row", "for_engine",
           "FLAG_ENV", "EDGAR_ENV"]
