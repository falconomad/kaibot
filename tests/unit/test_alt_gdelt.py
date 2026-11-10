import urllib.parse
from datetime import UTC, date, datetime

import pytest
from alt_helpers import FIX, FakeNet

from kaibot.common.ratelimit import BudgetSpec
from kaibot.ingest.alt import gdelt, store

DAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 23, 40, tzinfo=UTC)
TONE = (FIX / "gdelt_timelinetone.json").read_bytes()
VOL = (FIX / "gdelt_timelinevolraw.json").read_bytes()
Q = {"tariffs": gdelt.QUERIES["tariffs"]}


def test_parse_timeline_validates_points():
    pts = gdelt.parse_timeline(TONE)
    assert len(pts) == 5  # "bogus" date and "NaN" string value are skipped
    assert pts[1] == ("Average Tone", datetime(2026, 11, 9, tzinfo=UTC), -1.8421, None)
    assert gdelt.parse_timeline(VOL)[0][3] == 21873.0
    assert gdelt.parse_timeline(b"{}") == [] and gdelt.parse_timeline(b"") == []
    with pytest.raises(gdelt.BadPayload):
        gdelt.parse_timeline((FIX / "gdelt_429.txt").read_bytes(), "text/html")
    with pytest.raises(gdelt.BadPayload):
        gdelt.parse_timeline(b'{"timeline": {"oops": 1}}')


def test_collect_paces_queries_and_is_idempotent(tmp_path):
    net = FakeNet([("timelinetone", TONE), ("timelinevolraw", VOL)], spec=BudgetSpec(min_interval=6.0))
    n = gdelt.collect(tmp_path, DAY, now=NOW, queries=Q, **net.kw)
    assert n == 4 + 2  # the 2026-11-08 bucket is outside the day
    assert net.clock.sleeps == [6.0]  # two requests, 6 s apart
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(net.calls[0][0]).query)
    assert q["startdatetime"] == ["20261109000000"] and q["enddatetime"] == ["20261109234000"]
    assert q["format"] == ["json"] and q["mode"] == ["timelinetone"]
    assert gdelt.collect(tmp_path, DAY, now=NOW, queries=Q, **net.kw) == 0


def test_revised_bucket_is_a_new_version_known_only_from_the_refetch(tmp_path):
    net = FakeNet([("timelinetone", TONE), ("timelinevolraw", VOL)])
    gdelt.collect(tmp_path, DAY, now=NOW, queries=Q, **net.kw)
    later = datetime(2026, 11, 10, 23, 40, tzinfo=UTC)
    net2 = FakeNet([("timelinetone", TONE.replace(b"-1.0562", b"-3.5")), ("timelinevolraw", VOL)])
    assert gdelt.collect(tmp_path, DAY, now=later, queries=Q, **net2.kw) == 1
    key = gdelt.KEY
    b = datetime(2026, 11, 9, 0, 30, tzinfo=UTC)
    v = lambda t: store.read_pit(tmp_path, gdelt.SOURCE, t, key).set_index("bucket_ts").loc[b, "value"]  # noqa: E731
    assert v(NOW) == -1.0562 and v(later) == -3.5


def test_throttle_text_and_errors_skip_query_not_crash(tmp_path):
    net = FakeNet([("timelinetone", (FIX / "gdelt_429.txt").read_bytes(), {"Content-Type": "text/html"}),
                   ("timelinevolraw", 400)])
    assert gdelt.collect(tmp_path, DAY, now=NOW, queries=Q, **net.kw) == 0


def test_429_is_retried_with_shared_backoff(tmp_path):
    calls = {"n": 0}
    net = FakeNet([])

    def opener(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeNet([("", 429)])(req, timeout)
        return FakeNet([("timelinetone", TONE), ("timelinevolraw", VOL)])(req, timeout)

    n = gdelt.collect(tmp_path, DAY, now=NOW, queries=Q, opener=opener, budget=net.budget)
    assert n == 6 and calls["n"] == 3 and net.clock.t >= 1.0


def test_future_day_is_noop(tmp_path):
    net = FakeNet([])
    assert gdelt.collect(tmp_path, date(2026, 11, 10), now=NOW, queries=Q, **net.kw) == 0 and net.calls == []
