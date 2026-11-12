"""Checks on the daily bar store (story S84, #124): minute history against the daily bars, and the corporate-actions
table against Alpaca's own adjusted bars. No network: the store is filled through `FakeAlpaca`, the minute history
is written in the layout of scripts/fetch_history.py (data/bars_1m/symbol=X/YYYY-MM.parquet)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from daily_helpers import ET, NOW, FakeAlpaca, adjusted, bars, load_script, run, sessions

from kaibot.ingest import daily_check as dc
from kaibot.ingest.daily_store import DailyStore

DAYS = sessions("2026-10-01")  # 1 Oct .. 11 Nov 2026: 30 sessions across the end of summer time (1 Nov)
MINUTES = (time(9, 29), time(9, 30), time(9, 31), time(12, 0), time(15, 55), time(15, 58), time(15, 59), time(16, 0))


@pytest.fixture
def alpaca(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    fake.add("SPY", bars(DAYS, [600.0 + i for i in range(len(DAYS))]))
    return fake


def stored(tmp_path, *symbols) -> DailyStore:
    assert run(tmp_path, "--symbols", ",".join(symbols), start="2026-10-01") == 0
    return DailyStore(tmp_path / "data" / "daily")


def write_minutes(data: Path, symbol: str, days, closes, scale=None, shares=None, drop=()) -> None:
    """Eight one-minute bars a session: 09:29 (before the open) and 16:00 (the closing auction) must not count.
    The regular session trades 600 x `shares` shares; `scale` puts prices and volumes on a split-adjusted basis
    (price x scale, volume / scale), the way a minute history downloaded after a split holds them."""
    scale = np.ones(len(days)) if scale is None else np.asarray(scale, dtype=float)
    shares = np.ones(len(days)) if shares is None else np.asarray(shares, dtype=float)
    rows = []
    for d, c, k, n in zip(days, closes, scale, shares, strict=True):
        if d in drop:
            continue
        px = {time(9, 29): c * 0.90, time(9, 30): c * 0.995, time(9, 31): c * 1.02, time(12, 0): c * 0.97,
              time(15, 55): c * 0.998, time(15, 58): c * 0.9985, time(15, 59): c * 0.9995, time(16, 0): c}
        for m in MINUTES:
            ts = datetime.combine(d, m, ET).astimezone(UTC)
            p = px[m] * k
            vol = {time(9, 30): 150, time(15, 59): 90}.get(m, 5000 if m in (time(9, 29), time(16, 0)) else 90) * n / k
            rows.append({"ts": ts, "available_ts": ts + timedelta(minutes=1), "o": p, "h": p * 1.001, "l": p * 0.999,
                         "c": p, "v": float(vol), "n": 3, "vw": p})
    for month in sorted({r["ts"].strftime("%Y-%m") for r in rows}):
        part = [r for r in rows if r["ts"].strftime("%Y-%m") == month]
        out = data / "bars_1m" / f"symbol={symbol}" / f"{month}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({
            "ts": pa.array([r["ts"] for r in part], pa.timestamp("s", tz="UTC")),
            "available_ts": pa.array([r["available_ts"] for r in part], pa.timestamp("s", tz="UTC")),
            **{k: [r[k] for r in part] for k in ("o", "h", "l", "c", "v", "n", "vw")}}), out)


def daily_from(days, closes, volume=1000):
    """Alpaca's daily bar of the same sessions: opens with the 09:30 print, closes with the auction."""
    out = bars(days, closes, volume)
    for b, c in zip(out, closes, strict=True):
        b.update(o=c * 0.995, h=c * 1.02 * 1.001, l=c * 0.97 * 0.999)
    return out


# ---------------------------------------------------------------- minute history -> one bar per session


def test_minute_bars_become_one_bar_per_session_of_regular_hours(tmp_path):
    closes = [100.0 + i for i in range(len(DAYS))]
    write_minutes(tmp_path / "data", "AAA", DAYS, closes)
    m = dc.minute_daily(tmp_path / "data", "AAA")
    assert list(m.columns) == ["symbol", "date", "o", "h", "l", "c", "v", "c1555", "v_first", "v_last"]
    assert len(m) == len(DAYS) and m["date"].dt.date.tolist() == DAYS, "summer and winter time land on the right day"
    first = m.iloc[0]
    assert first["o"] == pytest.approx(99.5), "the 09:30 bar opens the session, not the 09:29 one"
    assert first["c"] == pytest.approx(99.95), "the 15:59 bar closes it, not the 16:00 auction"
    assert first["h"] == pytest.approx(102.0 * 1.001) and first["l"] == pytest.approx(97.0 * 0.999)
    assert first["c1555"] == pytest.approx(99.8), "the last bar a decision at 15:55 can have seen"
    assert (first["v"], first["v_first"], first["v_last"]) == (600.0, 150.0, 90.0)
    part = dc.minute_daily(tmp_path / "data", "AAA", start="2026-11-02", end="2026-11-06")
    assert part["date"].dt.date.tolist() == [d for d in DAYS if date(2026, 11, 2) <= d <= date(2026, 11, 6)]
    assert dc.minute_daily(tmp_path / "data", "NOPE") is None
    assert dc.minute_daily(tmp_path / "data", "AAA", start="2030-01-01") is None


def test_differences_between_minute_and_daily_bars_are_measured_not_fixed(tmp_path, alpaca):
    closes = [100.0 + i for i in range(len(DAYS))]
    daily = daily_from(DAYS, closes)
    daily[3]["o"] = closes[3] * 0.99  # one official open that is not the first minute's
    alpaca.add("AAA", daily)
    s = stored(tmp_path, "AAA")
    before = pq.read_table(s.root / "bars" / "symbol=AAA.parquet")
    write_minutes(tmp_path / "data", "AAA", DAYS, closes, drop={DAYS[7]})
    days, summary = dc.compare_minutes(s, tmp_path / "data", ["AAA", "SPY", "AAA@2026-10-05"])
    assert summary["symbol"].tolist() == ["AAA"], "no minute files for SPY; as-of keys are not in the minute history"
    row = summary.iloc[0]
    assert (row["sessions"], row["only_in_daily"], row["only_in_minutes"]) == (len(DAYS) - 1, 1, 0)
    assert row["other_basis"] == 0
    assert days["c_bp"].tolist() == pytest.approx([-5.0] * (len(DAYS) - 1)), "15:59 close against the auction close"
    assert row["c_differs_pct"] == 100.0 and row["c_median_bp"] == 5.0 and row["c_max_bp"] == 5.0
    assert row["o_differs_pct"] == pytest.approx(100 / (len(DAYS) - 1), abs=0.01) and row["o_max_bp"] == pytest.approx(
        50.505, abs=0.01)
    assert row["h_differs_pct"] == 0.0 and row["l_differs_pct"] == 0.0
    assert row["v_ratio_median"] == 0.6, "600 of the day's 1000 shares trade between 09:30 and 15:59"
    assert before.equals(pq.read_table(s.root / "bars" / "symbol=AAA.parquet")), "neither side is changed"


def test_split_adjusted_minutes_compare_on_one_basis_and_a_stale_month_is_named(tmp_path, alpaca):
    cum = np.array([1.0] * 12 + [4.0] * (len(DAYS) - 12))  # 4-for-1 on the 13th session, 19 Oct
    econ = np.array([400.0 + i for i in range(len(DAYS))])
    raw = econ / cum
    alpaca.add("SPL", daily_from(DAYS, raw, 1000 * cum), adjusted(daily_from(DAYS, raw, 1000 * cum),
                                                                 splits={DAYS[12]: 4.0}))
    alpaca.act("forward_split", symbol="SPL", new_rate=4, old_rate=1, ex_date=DAYS[12].isoformat(),
               process_date=DAYS[12].isoformat())
    s = stored(tmp_path, "SPL")
    basis = cum / 4.0  # what a download after the split holds: earlier prices divided by 4
    write_minutes(tmp_path / "data", "SPL", DAYS, raw, scale=basis, shares=cum)
    days, summary = dc.compare_minutes(s, tmp_path / "data", ["SPL"])
    assert summary.iloc[0]["other_basis"] == 0 and days["c_bp"].tolist() == pytest.approx([-5.0] * len(DAYS))
    assert summary.iloc[0]["v_ratio_median"] == 0.6
    # the same minute history downloaded BEFORE the split: its October prices are four times the adjusted ones
    write_minutes(tmp_path / "data", "SPL", DAYS, raw, shares=cum)
    days, summary = dc.compare_minutes(s, tmp_path / "data", ["SPL"])
    assert summary.iloc[0]["other_basis"] == 12, "the twelve sessions before the split are on another basis"
    stale = days[days["other_basis"]]
    assert (stale["date"].min(), stale["date"].max()) == (DAYS[0].isoformat(), DAYS[11].isoformat())
    assert summary.iloc[0]["c_median_bp"] == 5.0, "they are kept out of the auction statistics"


# ---------------------------------------------------------------- table against Alpaca's adjusted bars


def flat(px=100.0):
    return bars(DAYS, [px + 0.25 * i for i in range(len(DAYS))])


def div(alpaca, sym, day, cash, **extra):
    alpaca.act("cash_dividend", symbol=sym, rate=cash, ex_date=day.isoformat(), process_date=day.isoformat(), **extra)


def test_a_complete_table_is_verified_against_alpacas_adjusted_bars(tmp_path, alpaca):
    raw = flat()
    for b in raw[15:]:
        b.update({k: b[k] / 2 for k in ("o", "h", "l", "c", "vw")}, v=b["v"] * 2)
    alpaca.add("OKK", raw, adjusted(raw, splits={DAYS[15]: 2.0}, cash={DAYS[5]: 0.8, DAYS[20]: 0.4}))
    alpaca.act("forward_split", symbol="OKK", new_rate=2, old_rate=1, ex_date=DAYS[15].isoformat(),
               process_date=DAYS[15].isoformat())
    div(alpaca, "OKK", DAYS[5], 0.8)
    div(alpaca, "OKK", DAYS[20], 0.4)
    verdicts, steps = dc.check_adjustment(stored(tmp_path, "OKK"))
    assert verdicts.set_index("key")["verdict"].to_dict() == {"OKK": "ok", "SPY": "ok"} and steps.empty
    assert list(steps.columns) == ["key", "date", "class", "alpaca_factor", "table_factor"]


def test_every_kind_of_disagreement_is_found_dated_and_named(tmp_path, alpaca):
    raw = flat()
    # MISS: Alpaca adjusts for a dividend the table does not have
    alpaca.add("MISS", raw, adjusted(raw, cash={DAYS[5]: 0.8, DAYS[20]: 0.4}))
    div(alpaca, "MISS", DAYS[5], 0.8)
    # SPIN: a spin-off, which the table lists but does not apply
    alpaca.add("SPIN", raw, adjusted(raw, other={DAYS[10]: 0.8}))
    alpaca.act("spin_off", source_symbol="SPIN", new_symbol="NEWCO", source_rate=1, new_rate=0.25,
               ex_date=DAYS[10].isoformat(), process_date=DAYS[10].isoformat())
    # TWICE: two equal payments on one day; the table keeps one and flags the other as a repeat
    alpaca.add("TWICE", raw, adjusted(adjusted(raw, cash={DAYS[8]: 1.0}), cash={DAYS[8]: 1.0}))
    div(alpaca, "TWICE", DAYS[8], 1.0, id="a", cusip="1")
    div(alpaca, "TWICE", DAYS[8], 1.0, id="b", cusip="2")
    # EXTRA: the table has a dividend that Alpaca's bars do not apply
    alpaca.add("EXTRA", raw, raw)
    div(alpaca, "EXTRA", DAYS[12], 2.0)
    # NOSP: a 2-for-1 split that is missing from the table
    halved = [dict(b) for b in raw]
    for b in halved[15:]:
        b.update({k: b[k] / 2 for k in ("o", "h", "l", "c", "vw")}, v=b["v"] * 2)
    alpaca.add("NOSP", halved, adjusted(halved, splits={DAYS[15]: 2.0}))
    verdicts, steps = dc.check_adjustment(stored(tmp_path, "MISS,SPIN,TWICE,EXTRA,NOSP"))
    v = verdicts.set_index("key")
    assert v["verdict"].to_dict() == {"MISS": "mismatch", "SPIN": "mismatch", "TWICE": "mismatch", "EXTRA": "mismatch",
                                      "NOSP": "mismatch", "SPY": "ok"}
    got = {r["key"]: (r["date"], r["class"], r["alpaca_factor"], r["table_factor"]) for _, r in steps.iterrows()}
    assert len(steps) == 5
    missed = pytest.approx(1 - 0.4 / raw[19]["c"], abs=1e-6)
    assert got["MISS"] == (DAYS[20].isoformat(), "missing_in_table", missed, 1.0)
    assert got["SPIN"] == (DAYS[10].isoformat(), "spin_off", 0.8, 1.0)
    assert got["TWICE"][:2] == (DAYS[8].isoformat(), "dropped_duplicate") and got["TWICE"][2] < got["TWICE"][3] < 1
    extra = pytest.approx(1 - 2.0 / raw[11]["c"], abs=1e-6)
    assert got["EXTRA"] == (DAYS[12].isoformat(), "factor_differs", 1.0, extra)
    assert got["NOSP"] == (DAYS[15].isoformat(), "missing_in_table", 0.5, 1.0)
    assert v.loc["NOSP", "split_basis_days"] == 15 and v.loc["MISS", "split_basis_days"] == 0
    assert v.loc["SPIN", "worst_step_pct"] == pytest.approx(22.314, abs=0.01) and v.loc["SPIN", "first_step"] == str(
        DAYS[10])


def test_coarsely_rounded_adjusted_prices_are_not_reported_as_missing_actions(tmp_path, alpaca):
    cheap = flat(3.0)
    alpaca.add("PENNY", cheap, adjusted(cheap, cash={DAYS[5]: 0.03, DAYS[20]: 0.03}, decimals=2))
    div(alpaca, "PENNY", DAYS[5], 0.03)
    div(alpaca, "PENNY", DAYS[20], 0.03)
    dear = flat(250.0)
    alpaca.add("DEAR", dear, adjusted(dear, cash={DAYS[5]: 1.5}, decimals=2))
    div(alpaca, "DEAR", DAYS[5], 1.5)
    verdicts, steps = dc.check_adjustment(stored(tmp_path, "PENNY,DEAR"))
    v = verdicts.set_index("key")["verdict"].to_dict()
    assert v["DEAR"] == "ok" and v["PENNY"] in ("ok", "noisy") and steps.empty, "rounding is not a corporate action"


def test_keys_without_an_adjusted_series_cannot_be_verified(tmp_path, alpaca):
    alpaca.add("AAA", flat())
    assert run(tmp_path, "--symbols", "AAA", "--no-reference", start="2026-10-01") == 0
    s = DailyStore(tmp_path / "data" / "daily")
    verdicts, _ = dc.check_adjustment(s)
    assert set(verdicts["verdict"]) == {"no_reference"}
    said: list[str] = []
    assert dc.run_check(tmp_path / "data", log=said.append)["adjustment"] == {"no_reference": 2}
    assert s.flagged() == frozenset() and s.adjustment_report()["verdict"].tolist() == ["no_reference"] * 2


def test_the_check_says_what_is_missing_when_there_are_no_corporate_actions(tmp_path, alpaca):
    assert run(tmp_path, "--symbols", "SPY", "--no-actions", start="2026-10-01") == 0
    said: list[str] = []
    assert dc.run_check(tmp_path / "data", log=said.append) == {}
    assert "No corporate-actions table yet" in said[0] and len(said) == 1


def test_a_split_missing_from_the_table_is_called_out_as_unsafe_to_trade(tmp_path, alpaca):
    raw = flat()
    for b in raw[15:]:
        b.update({k: b[k] / 2 for k in ("o", "h", "l", "c", "vw")}, v=b["v"] * 2)
    alpaca.add("NOSP", raw, adjusted(raw, splits={DAYS[15]: 2.0}))
    s = stored(tmp_path, "NOSP")
    said: list[str] = []
    res = dc.run_check(tmp_path / "data", log=said.append)
    assert res["split_unknown"] == ["NOSP"]
    assert "WARNING: for 1 keys the table and Alpaca's bars disagree about a split (NOSP)" in "\n".join(said)
    report = s.adjustment_report()
    assert report.loc[report["split_basis_days"] > 0, "key"].tolist() == ["NOSP"]


# ---------------------------------------------------------------- the whole check, and the command


def test_run_check_writes_the_logs_and_a_summary_in_plain_words(tmp_path, alpaca, capsys):
    closes = [100.0 + i for i in range(len(DAYS))]
    alpaca.add("AAA", daily_from(DAYS, closes))
    raw = flat()
    alpaca.add("GE", raw, adjusted(raw, other={DAYS[10]: 0.8}))
    alpaca.act("spin_off", source_symbol="GE", new_symbol="GEHC", source_rate=3, new_rate=1,
               ex_date=DAYS[10].isoformat(), process_date=DAYS[10].isoformat())
    alpaca.add("XLC", bars(DAYS[5:], [80.0] * (len(DAYS) - 5)))
    spy = alpaca.raw[("SPY", None)]
    alpaca.add("SPY", spy, adjusted(spy, cash={DAYS[3]: 1.8, DAYS[25]: 1.8}))
    for d in (DAYS[3], DAYS[25]):
        div(alpaca, "SPY", d, 1.8)
    s = stored(tmp_path, "AAA", "GE", "XLC")
    write_minutes(tmp_path / "data", "AAA", DAYS, closes)
    said: list[str] = []
    res = dc.run_check(tmp_path / "data", log=said.append)
    out = s.root / "check"
    assert sorted(p.name for p in out.iterdir()) == ["adjustment.csv", "adjustment_steps.csv", "minute_vs_daily.csv",
                                                     "minute_vs_daily_summary.csv", "summary.txt"]
    assert (out / "summary.txt").read_text().splitlines() == said
    text = "\n".join(said)
    assert "1 symbols, 30 sessions compared (AAA)" in text
    assert "close differs on 100.0% of sessions: median 5.00 bp" in text and "open  differs on 0.0%" in text
    assert "hold 60.0% of the daily volume" in text
    assert "4 keys: 1 mismatch, 3 ok" in text and "1 spin_off (in 1 keys" in text
    assert "SPY total return 2026-10-01..2026-11-11: 2 dividends" in text
    assert f"XLC's first bar is {DAYS[5]}" in text and "spliced_close('XLC', 'SPY')" in text
    assert res["adjustment"] == {"mismatch": 1, "ok": 3} and res["minute_sessions"] == 30 and res["xlc_first"] == str(
        DAYS[5])
    assert pd.read_csv(out / "minute_vs_daily.csv")["c_bp"].abs().max() == pytest.approx(5.0)
    assert s.flagged() == frozenset({"GE"}), "the reader's source='auto' uses Alpaca's factor for GE from now on"
    assert s.panel(["GE"]).adjusted("total", source="auto").adj_source == {"GE": "reference"}

    fd = load_script()
    assert fd.main(["--data", str(tmp_path / "data"), "--check", "--sample", "aaa"], now=NOW) == 0
    printed = capsys.readouterr().out
    assert "Minute history against daily bars: 1 symbols" in printed and "Details:" in printed
    assert fd.main(["--data", str(tmp_path / "nothing"), "--check"], now=NOW) == 1
    assert "run ./scripts/fetch_daily.sh first" in capsys.readouterr().out


def test_the_sample_is_fixed_names_plus_a_seeded_draw_of_keys_with_minute_files(tmp_path, alpaca):
    for sym in ("AAPL", "AAA", "BBB", "CCC"):
        alpaca.add(sym, flat())
        write_minutes(tmp_path / "data", sym, DAYS[:2], [10.0, 10.0])
    s = stored(tmp_path, "AAPL", "AAA", "BBB", "CCC")
    one = dc.pick_sample(s, tmp_path / "data", extra=2)
    assert one[0] == "AAPL" and len(one) == 3 and "SPY" not in one, "SPY has no minute files here"
    assert one == dc.pick_sample(s, tmp_path / "data", extra=2), "the same draw every time"
    assert dc.pick_sample(s, tmp_path / "data", extra=0) == ["AAPL"]


def test_a_missing_spy_dividend_year_is_called_out(tmp_path, monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    days = sessions("2024-01-02")
    fake.add("SPY", bars(days, [500.0 + 0.1 * i for i in range(len(days))]))
    for y, months in ((2024, (3, 6, 9, 12)), (2025, (3, 6)), (2026, (3, 6, 9))):
        for m in months:
            d = next(x for x in days if (x.year, x.month) == (y, m) and x.day >= 15)
            div(fake, "SPY", d, 1.7)
    assert run(tmp_path, "--symbols", "SPY", start="2024-01-01") == 0
    said: list[str] = []
    res = dc.run_check(tmp_path / "data", log=said.append)
    assert res["spy_thin_years"] == [2025] and res["spy_dividends"] == 9
    assert "WARNING: fewer than 4 dividends in 2025" in "\n".join(said)
    assert 0.9 < res["spy_dividend_points_per_year"] < 1.4
