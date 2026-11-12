"""Integrity of the daily bars (issue #169). Offline: synthetic bars, and one store filled through `FakeAlpaca`."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from daily_helpers import NOW, FakeAlpaca, bars, load_script, run

from kaibot.ingest import daily_integrity as di
from kaibot.ingest import nyse_sessions
from kaibot.ingest.daily_bars import bars_table
from kaibot.ingest.daily_store import DailyStore

DAYS = nyse_sessions.sessions(date(2026, 10, 1), date(2026, 11, 11))  # 30 sessions, no holiday inside


def frame(days=DAYS, closes=None) -> pd.DataFrame:
    """Raw bars as `DailyStore.bars` returns them, built through the store's own writer."""
    closes = closes if closes is not None else [100.0 + i for i in range(len(days))]
    t = bars_table(bars(list(days), closes), days[-1])
    df = t.to_pandas()
    df["date"] = df["date"].astype("datetime64[s]")
    return df


def kinds(found) -> list[str]:
    return [f["kind"] for f in found]


def test_a_clean_series_has_no_findings():
    df = frame()
    assert di.check_bars("AAA", df) == []
    row = di.summarise("AAA", df, [])
    assert row["verdict"] == "clean" and row["bars"] == 30 and row["first"] == "2026-10-01"


def test_bad_prices_and_ohlc_order_are_errors():
    df = frame()
    df.loc[3, "c"] = 0.0
    df.loc[5, "l"] = np.nan
    df.loc[7, "h"] = df.loc[7, "c"] * 0.99  # high below the close
    df.loc[9, "l"] = df.loc[9, "o"] * 1.01  # low above the open
    found = di.check_bars("AAA", df)
    assert sorted(kinds(found)) == ["bad_price", "bad_price", "ohlc_order", "ohlc_order"]
    assert {f["date"] for f in found if f["kind"] == "ohlc_order"} == {str(DAYS[7]), str(DAYS[9])}
    assert all(f["severity"] == "error" for f in found)
    assert di.summarise("AAA", df, found)["verdict"] == "error"


def test_volume_negative_is_an_error_and_zero_a_warning():
    df = frame()
    df.loc[2, "v"] = -5.0
    df.loc[4, "v"] = 0.0
    found = di.check_bars("AAA", df)
    assert [(f["kind"], f["severity"]) for f in found] == [("bad_volume", "error"), ("zero_volume", "warn")]


def test_duplicate_and_unsorted_dates():
    df = frame()
    df = pd.concat([df.iloc[:10], df.iloc[[9]], df.iloc[[12]], df.iloc[10:12], df.iloc[13:]], ignore_index=True)
    found = di.check_bars("AAA", df)
    assert sorted(kinds(found)) == ["duplicate_date", "unsorted"]
    assert next(f for f in found if f["kind"] == "duplicate_date")["date"] == str(DAYS[9])


def test_a_bar_on_a_holiday_or_weekend_is_not_a_session():
    days = [date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 28)]  # Thanksgiving, Sat
    found = di.check_bars("AAA", frame(days, [10.0, 10.1, 10.2, 10.3]))
    assert [(f["kind"], f["date"]) for f in found] == [("not_a_session", "2026-11-26"),
                                                       ("not_a_session", "2026-11-28")]


def test_missing_sessions_are_grouped_into_runs():
    keep = [d for i, d in enumerate(DAYS) if i not in (4, 10, 11, 12)]
    found = di.check_bars("AAA", frame(keep))
    assert [(f["kind"], f["date"], f["detail"]) for f in found] == [
        ("missing_sessions", str(DAYS[4]), "1 session"),
        ("missing_sessions", str(DAYS[10]), f"3 sessions through {DAYS[12]}")]
    assert all(f["severity"] == "warn" for f in found)


def test_available_ts_before_the_close_plus_15_minutes_is_an_error():
    df = frame()
    df.loc[6, "available_ts"] = pd.Timestamp(datetime(2026, 10, 9, 20, 5, tzinfo=UTC))  # 16:05 ET: too early
    df.loc[8, "available_ts"] = pd.Timestamp(datetime(2026, 10, 13, 20, 15, tzinfo=UTC))  # 16:15 ET: just in time
    found = di.check_bars("AAA", df)
    assert [(f["kind"], f["date"]) for f in found] == [("early_available", str(DAYS[6]))]
    assert DAYS[6] == date(2026, 10, 9) and DAYS[8] == date(2026, 10, 13)


def test_early_close_days_use_the_13_00_close():
    day = date(2026, 11, 27)  # day after Thanksgiving: closes 13:00 ET
    assert nyse_sessions.early_close(day)
    df = frame([day], [50.0])
    df.loc[0, "available_ts"] = pd.Timestamp(datetime(2026, 11, 27, 18, 20, tzinfo=UTC))  # 13:20 ET
    assert di.check_bars("AAA", df) == []


def test_a_flat_unchanged_run_is_stale_only_from_five_bars():
    closes = [100.0 + i for i in range(len(DAYS))]
    for i in range(10, 15):
        closes[i] = 109.0  # bars 9..14 close at 109; 10..14 are flat
    df = frame(closes=closes)
    for i in range(10, 15):
        df.loc[i, ["o", "h", "l"]] = 109.0
    found = di.check_bars("AAA", df)
    assert [(f["kind"], f["date"]) for f in found] == [("stale", str(DAYS[10]))]
    assert found[0]["detail"] == f"5 flat bars at 109.0 through {DAYS[14]}"
    df.loc[14, ["o", "h", "l"]] = [109.0, 110.0, 108.0]  # only four flat bars left
    assert di.check_bars("AAA", df) == []


def test_jumps_are_reported_unless_a_split_or_spin_off_lands_on_the_bar():
    closes = [100.0] * 10 + [25.0] * 10 + [60.0] * 10
    df = frame(closes=closes)
    found = di.check_bars("AAA", df)
    assert [(f["kind"], f["date"]) for f in found] == [("jump", str(DAYS[10])), ("jump", str(DAYS[20]))]
    assert found[0]["detail"] == "close 100.0 -> 25.0 (-75.0%)"
    split = pd.DataFrame({"type": ["forward_split", "cash_dividend"],
                          "ex_date": np.array([DAYS[10], DAYS[20]], dtype="datetime64[D]")})
    left = di.check_bars("AAA", df, split)
    assert [(f["kind"], f["date"]) for f in left] == [("jump", str(DAYS[20]))], "a cash dividend explains nothing"
    # a spin-off whose ex-date has no bar (a missing session) lands on the next bar and still explains the move
    gap = frame([d for i, d in enumerate(DAYS) if i != 10], closes[:10] + closes[11:])
    spin = pd.DataFrame({"type": ["spin_off"], "ex_date": np.array([DAYS[10]], dtype="datetime64[D]")})
    left = di.check_bars("AAA", gap, spin)
    assert [(f["kind"], f["date"]) for f in left] == [("missing_sessions", str(DAYS[10])), ("jump", str(DAYS[20]))]


def test_an_empty_frame_has_no_findings():
    assert di.check_bars("AAA", frame().iloc[:0]) == []


@pytest.fixture
def alpaca(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


def test_the_store_check_writes_its_files_and_runs_with_fetch_daily_check(tmp_path, alpaca, capsys):
    alpaca.add("SPY", bars(DAYS, [600.0 + i for i in range(len(DAYS))]))
    alpaca.add("AAA", bars(DAYS, [100.0] * 15 + [30.0] * 15))
    alpaca.add("BBB", bars([d for i, d in enumerate(DAYS) if i != 3], [20.0 + 0.1 * i for i in range(29)]))
    assert run(tmp_path, "--symbols", "AAA,BBB", start="2026-10-01") == 0
    store = DailyStore(tmp_path / "data" / "daily")
    before = {p: p.stat().st_mtime_ns for p in (store.root / "bars").iterdir()}

    said: list[str] = []
    res = di.run_integrity(tmp_path / "data", log=said.append)
    out = store.root / "check"
    assert {"integrity.csv", "integrity_summary.csv", "integrity.txt"} <= {p.name for p in out.iterdir()}
    assert (out / "integrity.txt").read_text().splitlines() == said
    assert res["keys"] == 3 and res["verdicts"] == {"clean": 1, "warn": 2}
    assert res["findings"] == {"jump": 1, "missing_sessions": 1}
    summary = pd.read_csv(out / "integrity_summary.csv").set_index("key")
    assert summary.loc["SPY", "verdict"] == "clean" and summary.loc["AAA", "jump"] == 1
    assert summary.loc["BBB", "missing_sessions"] == 1
    assert said[0] == "Bar integrity: 3 keys: 1 clean, 2 warn, 0 error."
    assert {p: p.stat().st_mtime_ns for p in (store.root / "bars").iterdir()} == before, "read-only"

    for p in out.iterdir():
        p.unlink()
    assert load_script().main(["--data", str(tmp_path / "data"), "--check"], now=NOW) == 0
    assert "Bar integrity: 3 keys" in capsys.readouterr().out
    assert (out / "integrity.csv").exists()
