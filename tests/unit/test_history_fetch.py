from datetime import UTC, datetime

import pyarrow.parquet as pq

from kaibot.ingest.alpaca_history import fetch_bars, is_rth


class FakeApi:
    def __init__(self, pages):
        self.pages, self.calls = pages, 0

    def get(self, path, params):
        p = self.pages[self.calls]
        self.calls += 1
        return p


def bar(t):
    return {"t": t, "o": 1, "h": 1, "l": 1, "c": 1, "v": 10, "n": 1, "vw": 1}


def test_rth_window_handles_dst():
    assert is_rth(datetime(2026, 1, 5, 14, 30, tzinfo=UTC))  # 09:30 EST
    assert not is_rth(datetime(2026, 1, 5, 14, 29, tzinfo=UTC))
    assert is_rth(datetime(2026, 7, 6, 13, 30, tzinfo=UTC))  # 09:30 EDT
    assert is_rth(datetime(2026, 7, 6, 19, 59, tzinfo=UTC)) and not is_rth(datetime(2026, 7, 6, 20, 0, tzinfo=UTC))


def test_rth_only_and_pagination(tmp_path):
    api = FakeApi([{"bars": [bar("2026-07-06T12:00:00Z"), bar("2026-07-06T13:30:00Z")], "next_page_token": "x"},
                   {"bars": [bar("2026-07-06T19:59:00Z"), bar("2026-07-06T22:00:00Z")]}])
    out = tmp_path / "2026-07.parquet"
    assert fetch_bars(api, "X", datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC), out,
                      rth_only=True) == 2
    assert api.calls == 2 and pq.read_table(out).num_rows == 2


def test_empty_month_leaves_marker(tmp_path):
    out = tmp_path / "2016-01.parquet"
    assert fetch_bars(FakeApi([{"bars": None}]), "PLTR", datetime(2016, 1, 1, tzinfo=UTC),
                      datetime(2016, 2, 1, tzinfo=UTC), out) == 0
    assert not out.exists() and out.with_suffix(".empty").exists()


def test_news_times_merge_both_layouts(tmp_path):
    import pandas as pd

    from kaibot.replay.data import load_news_times

    d = tmp_path / "news" / "symbol=X"
    d.mkdir(parents=True)
    pd.DataFrame({"created_at": pd.to_datetime(["2025-01-02 10:00", "2025-01-01 09:00"], utc=True)}).to_parquet(
        d / "all.parquet")
    pd.DataFrame({"created_at": pd.to_datetime(["2025-01-02 10:00", "2026-03-01 12:00"], utc=True)}).to_parquet(
        d / "2026.parquet")
    t = load_news_times(tmp_path, "X")
    assert len(t) == 3 and (t[:-1] <= t[1:]).all()
