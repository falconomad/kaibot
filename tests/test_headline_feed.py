"""The dashboard's headline line, engine side (src/kaibot/news/feed.py, src/kaibot/ingest/edgar_8k_feed.py).

- Source A copies what the engine already fetches: the number of news requests is the same with the feed on.
- The write is fire-and-forget: `offer` never blocks or raises, a slow or failing store never delays a poll, a
  failure is logged once and queued as one event for the main thread.
- The licence follows the source; headlines are trimmed plain text; links are https or nothing.
- Source B (EDGAR 8-K) parses recorded-format fixtures, renders one line from item codes, asks at most once per
  symbol in 10 minutes and only in market hours. No live call here.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import Clock, FakeStore
from test_news_poll import T0 as POLL_T0
from test_news_poll import FakeNews, art

from kaibot.engine.m3glue import LockedDesk
from kaibot.ingest import edgar_8k_feed as E
from kaibot.ingest import news_poll as NP
from kaibot.news import NewsDesk
from kaibot.news import feed as F

FIX = Path(__file__).parent / "fixtures"
SUB = json.loads((FIX / "headline_feed" / "submissions_cik0000320193.json").read_text())
AAPL_RECORDED = json.loads((FIX / "sec_submissions_aapl.json").read_text())
TICKERS = json.loads((FIX / "sec_company_tickers.json").read_text())
MON_11ET = datetime(2026, 11, 23, 16, 0, tzinfo=UTC)  # Monday 23 Nov 2026, 11:00 ET: market open


class RecStore:
    """The store's upsert, recorded; `fail` raises, `delay` sleeps (a slow Supabase)."""

    def __init__(self, fail=False, delay=0.0):
        self.rows: list[dict] = []
        self.calls = 0
        self.fail, self.delay = fail, delay
        self.quick_timeout = None

    def quick(self, timeout):
        self.quick_timeout = timeout
        return self

    def upsert(self, table, rows, on_conflict):
        self.calls += 1
        assert table == "headline" and on_conflict == "dedup_key"
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("POST headline: <urlopen error timed out>")
        self.rows.extend(rows)


def a(headline="Zelman Upgrades Acme to Outperform", syms=("ACME",), at="2026-11-23T14:00:00Z", **kw):
    return {"id": kw.pop("id", 1), "headline": headline, "symbols": list(syms), "created_at": at,
            "url": kw.pop("url", "https://www.benzinga.com/x"), "source": "benzinga", **kw}


# ---------------- rows ----------------
def test_an_article_becomes_one_trimmed_plain_owner_only_row():
    long = "Citigroup Adjusts Price Target on Acme to $440 From $410, " + "and says a great deal more " * 10
    r = F.alpaca_row(a(long, syms=("BETA", "ACME", "bad sym", 7)), frozenset({"ACME"}), MON_11ET)
    assert len(r["headline"]) <= 140 and r["headline"].endswith("…")
    assert r["symbols"] == ["ACME", "BETA"]  # the held/decided stock first
    assert r["focus"] is True and r["licence"] == "owner_only" and r["source"] == "alpaca_benzinga"
    assert r["tags"] == ["analyst_up"]
    assert r["dedup_key"] == "alp:1" and r["created_at"] == "2026-11-23T14:00:00+00:00"
    assert set(r) == {"dedup_key", "symbols", "headline", "source", "licence", "url", "tags", "focus", "created_at"}


@pytest.mark.parametrize("url,want", [("https://www.benzinga.com/x", "https://www.benzinga.com/x"),
                                      ("http://www.benzinga.com/x", None), ("javascript:alert(1)", None),
                                      ("https://x.test/a b", None), (None, None), (5, None)])
def test_links_are_https_or_nothing(url, want):
    assert F.alpaca_row(a(url=url), frozenset(), MON_11ET)["url"] == want


def test_headline_text_is_cleaned_never_interpreted():
    r = F.alpaca_row(a("<b>Acme</b>\x00 names‮ new CEO\n<script>x</script>"), frozenset(), MON_11ET)
    assert "\x00" not in r["headline"] and "\n" not in r["headline"] and "‮" not in r["headline"]
    assert r["headline"].startswith("<b>Acme</b>")  # kept as text; the dashboard renders text only


def test_the_four_tags_and_only_them():
    assert frozenset(json.loads((Path(F.__file__).parent / "tagset" / "measure_v2.json")
                                               .read_text())["shipped"]) == F.FEED_TAGS
    down = F.alpaca_row(a("Acme Downgraded to Sell at Zelman"), frozenset(), MON_11ET)
    assert down["tags"] == ["analyst_down"]
    assert F.alpaca_row(a("Acme Reports Q3 Results"), frozenset(), MON_11ET)["tags"] == []  # results is not shipped


@pytest.mark.parametrize("bad", [None, "x", {}, {"headline": "x"}, a(syms=()), a(at="soon"), a(headline="  ")])
def test_unusable_articles_are_skipped(bad):
    assert F.alpaca_row(bad, frozenset(), MON_11ET) is None


def test_the_licence_follows_the_source_and_fails_closed():
    assert F.licence_of("alpaca_benzinga") == "owner_only" and F.licence_of("sec_edgar") == "public"
    assert F.licence_of("anything_else") == "owner_only"


# ---------------- fire and forget ----------------
def test_offer_never_blocks_never_raises_and_drops_when_full():
    f = F.HeadlineFeed(RecStore(), max_offers=2)
    t = time.monotonic()
    for _ in range(50):
        assert f.offer([a()]) is None
    f.offer(object())  # not iterable: dropped, no exception
    f.offer(None)
    assert time.monotonic() - t < 0.5
    assert f.counts["offered"] == 2 and f.counts["dropped"] == 49


def test_step_writes_in_batches_and_never_twice():
    st = RecStore()
    f = F.HeadlineFeed(st, clock=lambda: MON_11ET)
    f.offer([a(id=i, headline=f"Acme story {i}") for i in range(120)])
    f.step()
    assert st.quick_timeout == F.STORE_TIMEOUT_S
    assert st.calls == 3 and len(st.rows) == 120
    f.offer([a(id=i, headline=f"Acme story {i}") for i in range(120)])  # the next poll's overlap
    f.step()
    assert st.calls == 3


def test_a_failing_store_is_logged_once_one_event_then_ok(caplog):
    st = RecStore(fail=True)
    clock = Clock(MON_11ET)
    f = F.HeadlineFeed(st, clock=clock)
    caplog.set_level(logging.WARNING, logger="kaibot.news.feed")
    for i in range(5):
        f.offer([a(id=i)])
        f.step()  # never raises
        clock.advance(seconds=61)
    warnings = [r for r in caplog.records if "write failed" in r.getMessage()]
    assert len(warnings) == 1
    assert [k for k, _ in f.take_events()] == ["headline_feed_failed"]
    st.fail = False
    f.offer([a(id=99)])
    f.step()
    assert [k for k, _ in f.take_events()] == ["headline_feed_ok"] and f.status()["state"] == "ok"


def test_after_a_failure_it_waits_before_writing_again():
    st = RecStore(fail=True)
    clock = Clock(MON_11ET)
    f = F.HeadlineFeed(st, clock=clock)
    f.offer([a(id=1)])
    f.step()
    for i in range(10):
        f.offer([a(id=i + 2)])
        f.step()  # inside RETRY_S: no call
    assert st.calls == 1


# ---------------- Source A: what the engine already fetches, no new request ----------------
def _poller(feed):
    clock = Clock(POLL_T0)
    desk = NewsDesk(FakeStore(), clock=clock)
    locked = LockedDesk(desk)
    src = FakeNews([art("ACME wins a contract"), art("BETA beats estimates", sym="BETA")], lock=locked.lock)
    p = NP.NewsPoller(desk, src, lambda: NP.Scope({"ACME", "BETA"}, set(), {"ACME"}), lock=locked.lock, clock=clock)
    p.feed = feed
    if feed is not None:
        desk.feed = feed
    return p, desk, src, clock


def test_the_feed_adds_no_news_request():
    counts = []
    for feed in (None, F.HeadlineFeed(RecStore())):
        p, desk, src, clock = _poller(feed)
        p.poll_once()
        clock.advance(minutes=5)
        p.poll_once()
        desk.poll(["ACME"])  # the pre-open review's read
        if feed is not None:
            feed.step()
        counts.append(len(src.calls))
    assert counts[0] == counts[1] > 0


def test_the_poller_hands_over_what_it_fetched_outside_the_desk_lock():
    st = RecStore()
    f = F.HeadlineFeed(st, clock=lambda: POLL_T0)
    held = []
    orig = f.offer

    def spy(raw, focus=()):
        held.append(p.lock.locked())
        orig(raw, focus)

    f.offer = spy
    p, desk, src, _ = _poller(f)
    p.poll_once()
    f.step()
    assert held == [False]
    rows = {r["symbols"][0]: r for r in st.rows}
    assert set(rows) == {"ACME", "BETA"} and rows["ACME"]["focus"] and not rows["BETA"]["focus"]


def test_a_slow_store_never_delays_a_poll():
    st = RecStore(delay=3.0)
    f = F.HeadlineFeed(st, clock=lambda: POLL_T0, tick_s=0.01)
    f.start()
    try:
        p, desk, *_ = _poller(f)
        t = time.monotonic()
        p.poll_once()
        desk.poll(["ACME"])  # the pre-open review's read at the near-close send step
        f.offer([a(id=7)])
        assert time.monotonic() - t < 1.0
    finally:
        f.stop(timeout_s=0.1)


def test_a_raising_feed_never_breaks_the_poll():
    class Bad:
        def offer(self, *a, **k):
            raise RuntimeError("boom")

    p, desk, src, _ = _poller(Bad())
    assert p.poll_once()["fetched"] == 2
    assert desk.poll(["ACME"])["fetched"] >= 0


# ---------------- switches and wiring ----------------
def test_switches():
    assert F.for_engine(RecStore(), lambda: (), {"KAIBOT_HEADLINE_FEED": "off"}) is None
    f = F.for_engine(RecStore(), lambda: (), {})
    assert f is not None and f.edgar is None  # Source B is off by default
    f = F.for_engine(RecStore(), lambda: (), {"KAIBOT_HEADLINE_EDGAR": "on"})
    assert f.edgar is None  # no SEC_USER_AGENT: stays off
    env = {"KAIBOT_HEADLINE_EDGAR": "on", "SEC_USER_AGENT": "Kaibot ops@example.com"}
    f = F.for_engine(RecStore(), lambda: (), env)
    assert isinstance(f.edgar, E.EdgarFilings)


def test_the_engine_wires_the_feed_into_the_poller_and_the_desk():
    from kaibot.engine import __main__ as M

    class Eng:
        pos: dict = {}
        market = type("Mk", (), {"all": ["ACME"]})()
        daily = None

        def _md_symbols(self):
            return ["BETA"]

    p, desk, *_ = _poller(None)
    f = M.headline_feed(Eng(), RecStore(), desk, p, env={})
    assert f is not None and desk.feed is f and p.feed is f and f.focus_fn() == {"BETA"}
    assert M.headline_feed(Eng(), RecStore(), desk, None, env={"KAIBOT_HEADLINE_FEED": "off"}) is None


# ---------------- Source B: EDGAR 8-K ----------------
def test_parse_the_fixture_into_item_code_lines():
    rows = E.parse_submissions(SUB, "AAPL", datetime(2026, 11, 20, tzinfo=UTC))
    assert [r["headline"] for r in rows] == [
        "AAPL filed results (8-K item 2.02)",
        "AAPL filed Reg FD disclosure (8-K item 7.01)",
        "AAPL filed management change (8-K item 5.02)",
        "AAPL filed material agreement and results (8-K items 1.01, 2.02)",
        "AAPL filed an amended shareholder vote results (8-K/A item 5.07)",
    ]
    assert {r["licence"] for r in rows} == {"public"} and {r["source"] for r in rows} == {"sec_edgar"}
    assert rows[2]["tags"] == ["management_change"] and rows[0]["tags"] == []
    assert rows[0]["url"] == ("https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/"
                              "0000320193-26-000101-index.htm")
    assert all(r["key"].startswith("sec:") for r in rows)


def test_parse_the_recorded_submissions():
    rows = E.parse_submissions(AAPL_RECORDED, "AAPL", datetime(2026, 11, 1, tzinfo=UTC))
    assert [r["headline"] for r in rows] == ["AAPL filed other event (8-K item 8.01)"]


@pytest.mark.parametrize("doc", [None, [], {}, {"cik": "x"}, {"cik": "1", "filings": {"recent": {"form": "8-K"}}},
                                 {"cik": "1", "filings": {"recent": {"accessionNumber": [1], "form": [],
                                                                     "acceptanceDateTime": [], "items": []}}}])
def test_unexpected_documents_give_nothing(doc):
    assert E.parse_submissions(doc, "AAPL", MON_11ET - timedelta(days=3)) == []


def test_render_line_is_bounded_and_says_more():
    assert E.render_line("AAPL", "8-K", ["9.01"]) == "AAPL filed exhibits (8-K item 9.01)"
    assert E.render_line("AAPL", "8-K", []) == "AAPL filed a current report (8-K)"
    line = E.render_line("AAPL", "8-K", ["1.01", "2.03", "5.02", "8.01"])
    assert line == "AAPL filed material agreement and new debt and more (8-K items 1.01, 2.03, 5.02, 8.01)"
    assert len(E.render_line("AAPL", "8-K", [f"{i}.0{j}" for i in range(1, 9) for j in range(1, 8)])) <= 140


def test_ticker_map():
    m = E.ticker_ciks(TICKERS)
    assert m["AAPL"] == "0000320193" and m["MSFT"] == "0000789019"
    assert E.ticker_ciks(None) == {} and E.ticker_ciks({"0": {"ticker": "x y"}}) == {}


class FakeSec:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        if url == E.TICKERS_URL:
            return TICKERS
        if "CIK0000320193" in url:
            return SUB
        raise OSError("not recorded")


def test_edgar_asks_once_per_symbol_in_10_minutes_in_market_hours_only():
    sec = FakeSec()
    clock = Clock(MON_11ET)
    src = E.EdgarFilings(sec, clock=clock)
    rows = src.rows(["AAPL", "MSFT", "SPY"])
    assert {r["symbols"][0] for r in rows} == {"AAPL"}
    asked = [u for u in sec.urls if "submissions" in u]
    assert len(asked) == 2  # AAPL and MSFT (SPY has no CIK: not asked)
    for _ in range(9):
        clock.advance(minutes=1)
        assert src.rows(["AAPL", "MSFT", "SPY"]) == []
    assert len([u for u in sec.urls if "submissions" in u]) == 2
    clock.advance(minutes=1)
    src.rows(["AAPL", "MSFT"])
    assert len([u for u in sec.urls if "submissions" in u]) == 4
    assert sec.urls.count(E.TICKERS_URL) == 1  # the map once a day


@pytest.mark.parametrize("when", [datetime(2026, 11, 23, 14, 0, tzinfo=UTC),   # 09:00 ET, before the open
                                  datetime(2026, 11, 23, 21, 30, tzinfo=UTC),  # 16:30 ET
                                  datetime(2026, 11, 22, 16, 0, tzinfo=UTC),   # Sunday
                                  datetime(2026, 11, 26, 16, 0, tzinfo=UTC),   # Thanksgiving
                                  datetime(2026, 11, 27, 18, 30, tzinfo=UTC)])  # 13:30 ET, the early close
def test_edgar_never_asks_outside_market_hours(when):
    sec = FakeSec()
    assert E.EdgarFilings(sec, clock=lambda: when).rows(["AAPL"]) == [] and sec.urls == []


def test_edgar_caps_symbols_per_pass():
    sec = FakeSec()
    src = E.EdgarFilings(sec, clock=lambda: MON_11ET, max_per_pass=3)
    src.rows([f"S{i}" for i in range(20)])
    assert len(src._asked) == 3


def test_user_agent_must_name_a_contact():
    assert E.valid_user_agent("Kaibot ops@example.com")
    for bad in (None, "", "Kaibot", "ops@example.com", "Kaibot ops@example", "Kaibot\r\nX: y a@b.co"):
        assert not E.valid_user_agent(bad)
    assert E.from_env({}) is None and E.from_env({"SEC_USER_AGENT": "nobody"}) is None


def test_the_feed_writes_edgar_rows_as_public_for_held_stocks():
    st = RecStore()
    sec = FakeSec()
    f = F.HeadlineFeed(st, focus_fn=lambda: {"AAPL"}, edgar=E.EdgarFilings(sec, clock=lambda: MON_11ET),
                       clock=lambda: MON_11ET)
    f.step()
    assert st.rows and {r["licence"] for r in st.rows} == {"public"} and all(r["focus"] for r in st.rows)
    assert all(len(r["headline"]) <= 140 for r in st.rows)
    n = len(sec.urls)
    f.step()  # within EDGAR_EVERY_S: no new pass
    assert len(sec.urls) == n


def test_edgar_off_makes_no_sec_request(monkeypatch):
    called = []
    monkeypatch.setattr(E, "sec_get", lambda ua: called.append(ua))
    f = F.for_engine(RecStore(), lambda: {"AAPL"}, {})
    f.step()
    assert called == [] and f.edgar is None


def test_offer_from_another_thread_is_safe():
    st = RecStore()
    f = F.HeadlineFeed(st, clock=lambda: MON_11ET, max_offers=1000)
    ts = [threading.Thread(target=lambda k=k: [f.offer([a(id=k * 100 + i)]) for i in range(50)]) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    f.step()
    assert len(st.rows) == 200
