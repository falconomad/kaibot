"""The engine's news poller (src/kaibot/ingest/news_poll.py): news reaches kaibot.news_item without the room.

Before it, only the agent room's pack, the live pre-open review and the shadow review polled the desk, each on demand;
with the room off and the reviews not yet run nothing ever fetched news (19 Nov 2026: news_item had 0 rows).
"""

import threading
from datetime import UTC, date, datetime, timedelta

from fakes import Clock, FakeStore

from kaibot.engine.m3glue import LockedDesk, make_preopen_evidence
from kaibot.ingest import news_poll as NP
from kaibot.news import NewsDesk

# Thursday 19 Nov 2026, 08:30 ET (13:30 UTC): pre-market, inside the active window
T0 = datetime(2026, 11, 19, 13, 30, tzinfo=UTC)


def art(headline, sym="ACME", at=None, source="benzinga"):
    at = at or T0 - timedelta(hours=1)
    return {"headline": headline, "source": source, "author": "Benzinga Newsdesk", "symbols": [sym],
            "created_at": at.isoformat().replace("+00:00", "Z"), "url": "https://x.test/a", "summary": "s"}


class FakeNews:
    """AlpacaData.news stand-in: returns the articles of the requested symbols inside [start, end]."""

    def __init__(self, articles=(), lock=None):
        self.articles = list(articles)
        self.calls: list[tuple[list[str], datetime, datetime]] = []
        self.fail = False
        self.lock = lock
        self.lock_held_during_fetch = False

    def news(self, symbols, start, end, max_pages=10):
        if self.lock is not None and self.lock.locked():
            self.lock_held_during_fetch = True
        self.calls.append((list(symbols), start, end))
        if self.fail:
            raise OSError("network down")
        out = []
        for a in self.articles:
            t = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
            if set(a["symbols"]) & set(symbols) and start <= t <= end:
                out.append(a)
        return out


def make(articles=(), symbols=("ACME", "BETA"), planned=(), held=(), clock=None, gateway=None, **kw):
    clock = clock or Clock(T0)
    st = FakeStore()
    desk = NewsDesk(st, gateway=gateway, clock=clock)
    locked = LockedDesk(desk)
    src = FakeNews(articles, lock=locked.lock)
    p = NP.NewsPoller(desk, src, lambda: NP.Scope(set(symbols), set(planned), set(held)), lock=locked.lock,
                      clock=clock, **kw)
    return p, st, src, clock, locked


def items(st):
    return st.tables.get("news_item", [])


# ---------------- ingest ----------------
def test_first_poll_backfills_72h_and_writes_point_in_time_rows():
    old = art("ACME wins a contract", at=T0 - timedelta(hours=60))
    p, st, src, clock, _ = make([old, art("BETA beats estimates", sym="BETA")])
    out = p.poll_once()
    assert out["fetched"] == 2 and out["added"] == 2
    _, start, end = src.calls[0]
    assert end == T0 and start == T0 - timedelta(hours=72)
    rows = items(st)
    assert len(rows) == 2
    # available_ts is Kaibot's ingest time, never the provider's timestamp
    assert {r["available_ts"] for r in rows} == {"2026-11-19T13:30:00.000000+00:00"}
    assert min(r["published_at"] for r in rows).startswith("2026-11-17T01:30")


def test_later_polls_are_incremental_and_never_duplicate():
    a = art("ACME wins a contract")
    p, st, src, clock, _ = make([a])
    p.poll_once()
    clock.t = T0 + timedelta(minutes=5)
    out = p.poll_once()
    _, start, end = src.calls[-1]
    assert end == clock.t and start == T0 - NP.OVERLAP  # since the last good poll, with an overlap
    assert out["added"] == 0 and len(items(st)) == 1


def test_a_new_planned_buy_gets_a_72h_backfill_at_once_and_comes_first():
    scope = {"planned": set()}
    clock = Clock(T0)
    st = FakeStore()
    desk = NewsDesk(st, clock=clock)
    src = FakeNews([art("ZED guidance cut", sym="ZED", at=T0 - timedelta(hours=50))])
    p = NP.NewsPoller(desk, src, lambda: NP.Scope({"ACME"}, scope["planned"], set()), clock=clock)
    p.poll_once()
    assert not items(st)
    scope["planned"] = {"ZED"}  # the rule decided after the close: ZED is a planned buy
    clock.t = T0 + timedelta(minutes=5)
    p.poll_once()
    new_calls = [c for c in src.calls[1:] if "ZED" in c[0]]
    assert new_calls and new_calls[0][1] == clock.t - timedelta(hours=72)
    assert src.calls[1][0][0] == "ZED"  # planned buys lead the request order
    assert [r["tickers"] for r in items(st)] == [["ZED"]]
    assert p.status()["planned_polled"] is True


def test_fetch_happens_outside_the_desk_lock():
    p, st, src, *_ = make([art("ACME wins a contract")])
    p.poll_once()
    assert src.calls and not src.lock_held_during_fetch


def test_symbols_are_requested_in_chunks_of_50():
    syms = {f"S{i:03d}" for i in range(120)}
    p, st, src, *_ = make([], symbols=syms)
    p.poll_once()
    assert [len(c[0]) for c in src.calls] == [50, 50, 20]


def test_with_the_room_on_only_planned_and_held_symbols_are_polled():
    """The desk triages every new cluster through the LLM when the room is on: the poller then keeps to the
    symbols a decision is about, so a wide universe cannot spend the triage budget."""
    p, st, src, *_ = make([], symbols=("ACME", "BETA"), planned=("ZED",), held=("HELD",), gateway=object())
    p.poll_once()
    assert sorted(s for c in src.calls for s in c[0]) == ["HELD", "ZED"]


# ---------------- point-in-time and the pre-open review ----------------
def test_evidence_before_the_poll_sees_nothing_and_after_sees_the_story():
    p, st, src, clock, locked = make([art("ACME cuts guidance")])
    p.poll_once()
    before = locked.pack("ACME", None, T0 - timedelta(seconds=1), 72)
    after = locked.pack("ACME", None, T0 + timedelta(seconds=1), 72)
    assert before["items"] == [] and [i["headline"] for i in after["items"]] == ["ACME cuts guidance"]


def test_the_shadow_review_sees_polled_news_even_when_its_own_poll_fails():
    p, st, src, clock, locked = make([art("ZED guidance cut", sym="ZED")], symbols=(), planned=("ZED",))
    p.poll_once()
    src.fail = True  # the review's just-in-time poll (make_preopen_evidence) fails at 15:50
    clock.t = datetime(2026, 11, 19, 20, 50, tzinfo=UTC)
    locked.desk.source = src
    ev = make_preopen_evidence(locked)(["ZED"], clock.t)
    assert [i["headline"] for i in ev["ZED"]] == ["ZED guidance cut"]


# ---------------- health ----------------
def test_status_before_any_poll_is_starting():
    p, *_ = make()
    assert p.status()["state"] == "starting"


def test_a_failing_feed_is_reported_once_and_its_recovery_too():
    p, st, src, clock, _ = make([art("ACME wins a contract")])
    src.fail = True
    p.poll_once()
    s = p.status()
    assert s["state"] == "failing" and "OSError" in s["error"]
    clock.t += timedelta(minutes=5)
    p.poll_once()
    ev = p.take_events()
    assert [k for k, _ in ev] == ["news_feed_unhealthy"] and ev[0][1]["state"] == "failing"
    src.fail = False
    clock.t += timedelta(minutes=5)
    p.poll_once()
    assert p.status()["state"] == "ok"
    assert [k for k, _ in p.take_events()] == ["news_feed_ok"]
    assert p.take_events() == []


def test_no_items_for_two_hours_in_market_hours_is_empty():
    t = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)  # 10:00 ET
    clock = Clock(t)
    p, st, src, clock, _ = make([], clock=clock)
    p.poll_once()
    assert p.status()["state"] == "ok"  # too early to tell
    clock.t = t + NP.EMPTY_AFTER
    p.poll_once()
    s = p.status()
    assert s["state"] == "empty" and "no news" in s["error"]
    assert [k for k, _ in p.take_events()] == ["news_feed_unhealthy"]


def test_a_quiet_night_is_not_empty():
    t = datetime(2026, 11, 20, 4, 0, tzinfo=UTC)  # 23:00 ET
    p, st, src, clock, _ = make([], clock=Clock(t))
    p.poll_once()
    clock.t = t + 3 * NP.EMPTY_AFTER
    p.poll_once()
    assert p.status()["state"] == "ok"


def test_status_counts_the_et_day():
    p, st, src, clock, _ = make([art("ACME wins a contract"), art("BETA beats", sym="BETA")])
    p.poll_once()
    s = p.status()
    assert s["day"] == "2026-11-19" and s["calls_today"] == 1 and s["added_today"] == 2
    assert s["symbols"] == 2 and s["last_item"] is not None


# ---------------- cadence and thread ----------------
def test_cadence_is_5_minutes_by_day_and_30_at_night():
    p, st, src, clock, _ = make()
    assert p.due()  # never polled
    p.poll_once()
    clock.t = T0 + timedelta(minutes=4)
    assert not p.due()
    clock.t = T0 + timedelta(minutes=5)
    assert p.due()
    night = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)  # Friday 22:00 ET
    clock.t = night
    p.poll_once()
    clock.t = night + timedelta(minutes=29)
    assert not p.due()
    clock.t = night + timedelta(minutes=30)
    assert p.due()


def test_the_thread_polls_and_stops():
    p, st, src, clock, _ = make([art("ACME wins a contract")], tick_s=0.01)
    p.start()
    for _ in range(200):
        if items(st):
            break
        threading.Event().wait(0.01)
    p.stop()
    assert items(st) and not p.running()


def test_a_crashing_scope_is_contained():
    clock = Clock(T0)
    desk = NewsDesk(FakeStore(), clock=clock)

    def boom():
        raise RuntimeError("journal busy")

    p = NP.NewsPoller(desk, FakeNews(), boom, clock=clock)
    out = p.poll_once()
    assert out["fetched"] == 0 and p.status()["state"] == "failing"


def test_switch():
    assert NP.enabled({}) and NP.enabled({"KAIBOT_NEWS_POLL": "on"})
    assert not NP.enabled({"KAIBOT_NEWS_POLL": "off"}) and not NP.enabled({"KAIBOT_NEWS_POLL": "0"})


# ---------------- the engine's scope ----------------
class _Daily:
    def __init__(self, entries):
        self.entries = entries

    def _entries(self, kind):
        return self.entries if kind == "md_decision" else []


class _Market:
    all = ["ACME", "SPY", "XLK"]


class _Eng:
    def __init__(self, daily=None):
        self.market, self.pos, self.daily = _Market(), {"HELD": 1}, daily

    def _md_symbols(self):
        return {"MDHELD"}


def test_engine_scope_reads_the_market_the_book_and_todays_planned_buys():
    target = {"weights": [["ZED", 0.1], ["MDHELD", 0.1]], "features": {"buy": ["ZED", "YAK"]}}
    old = {"strategy": "trend_momentum", "fill_session": "2026-11-18", "target": {"weights": [["OLD", 1.0]]}}
    new = {"strategy": "trend_momentum", "fill_session": "2026-11-19", "target": target}
    sc = NP.engine_scope(_Eng(_Daily([old, new])), date(2026, 11, 19))
    assert sc.watch == {"ACME", "SPY", "XLK"}
    assert sc.held == {"HELD", "MDHELD"}
    assert sc.planned == {"ZED", "YAK", "MDHELD"}
    assert NP.engine_scope(_Eng(), date(2026, 11, 19)).planned == set()


def test_for_engine_honours_the_switch_and_the_main_loop_writes_its_events():
    from kaibot.engine import __main__ as M

    eng = _Eng()
    desk = NewsDesk(FakeStore(), clock=Clock(T0))
    assert NP.for_engine(eng, desk, FakeNews(), None, env={"KAIBOT_NEWS_POLL": "off"}) is None
    p = NP.for_engine(eng, desk, FakeNews(), None, env={})
    assert isinstance(p, NP.NewsPoller)

    class E:
        store = FakeStore()

        def _rid(self):
            return 7

    e = E()
    p._events = [("news_feed_unhealthy", {"state": "empty"})]
    M.report_news(e, p)
    M.report_news(e, None)
    assert [(r["type"], r["run_id"]) for r in e.store.tables["event"]] == [("news_feed_unhealthy", 7)]


# ---------------- QA-Ops (#355): a slow or down Supabase never holds the desk lock for long ----------------
class DownStore(FakeStore):
    """Supabase unreachable: every call fails as Store._call does after its tries (a network error)."""

    def __init__(self):
        super().__init__()
        self.down, self.calls, self.quick_timeout = True, 0, None

    def quick(self, timeout=5.0):
        self.quick_timeout = timeout
        return self

    def _maybe_fail(self):
        self.calls += 1
        if self.down:
            from kaibot.engine.store import StoreError

            raise StoreError("POST news_cluster: <urlopen error timed out>")

    def select(self, table, params):
        self._maybe_fail()
        return super().select(table, params)

    def insert(self, table, row, returning=True):
        self._maybe_fail()
        return super().insert(table, row, returning)

    def update(self, table, match, values):
        self._maybe_fail()
        return super().update(table, match, values)


class CountingLock:
    def __init__(self):
        self._l, self.holds = threading.Lock(), 0

    def __enter__(self):
        self._l.acquire()
        self.holds += 1
        return self

    def __exit__(self, *exc):
        self._l.release()

    def locked(self):
        return self._l.locked()


def many(n):
    return [art(f"ACME story number {i} about item {i * 7919}", at=T0 - timedelta(minutes=30 + i)) for i in range(n)]


def test_store_writes_go_in_batches_with_the_lock_released_between_them():
    lock = CountingLock()
    clock = Clock(T0)
    st = FakeStore()
    desk = NewsDesk(st, clock=clock)
    p = NP.NewsPoller(desk, FakeNews(many(35)), lambda: NP.Scope({"ACME"}, set(), set()), lock=lock, clock=clock)
    out = p.poll_once()
    assert out["added"] == 35 and len(items(st)) == 35
    assert lock.holds == 4  # ceil(35 / INGEST_BATCH): never one hold for the whole backfill
    assert desk.store is st  # the desk's own store is back after the poll


def test_supabase_down_costs_one_failed_call_backs_off_and_loses_nothing():
    clock = Clock(T0)
    st = DownStore()
    desk = NewsDesk(st, clock=clock)
    src = FakeNews(many(40))
    p = NP.NewsPoller(desk, src, lambda: NP.Scope({"ACME"}, set(), set()), clock=clock)
    p.poll_once()
    assert st.quick_timeout == NP.STORE_TIMEOUT_S  # one try, short timeout
    assert st.calls == 1  # the breaker: not 40 stories x Store's 4 tries of 20 s under the lock
    s = p.status()
    assert s["state"] == "failing" and "store" in s["error"] and s["retry_at"] is not None
    assert [k for k, _ in p.take_events()] == ["news_feed_unhealthy"]
    clock.t += timedelta(minutes=5)
    assert p.due()  # first retry after the normal 5 minutes
    p.poll_once()
    clock.t += timedelta(minutes=5)
    assert not p.due()  # then 10 minutes
    clock.t += timedelta(minutes=5)
    st.down = False
    assert p.due()
    p.poll_once()
    # the watermark did not move while the store was down: the 72 h backfill is asked again and all 40 are stored
    assert src.calls[-1][1] == clock.t - timedelta(hours=72)
    assert len(items(st)) == 40 and p.status()["state"] == "ok" and p.status()["retry_at"] is None
    assert [k for k, _ in p.take_events()] == ["news_feed_ok"]


def test_a_refused_row_does_not_trip_the_breaker():
    clock = Clock(T0)

    class Refuses(FakeStore):
        def insert(self, table, row, returning=True):
            if table == "news_item" and "number 3 " in row.get("headline", ""):
                from kaibot.engine.store import StoreError

                raise StoreError('POST news_item -> 409: {"code":"23505"}')
            return super().insert(table, row, returning)

    st = Refuses()
    p = NP.NewsPoller(NewsDesk(st, clock=clock), FakeNews(many(12)), lambda: NP.Scope({"ACME"}, set(), set()),
                      clock=clock)
    out = p.poll_once()
    assert out["added"] == 11 and p.status()["state"] == "ok"
