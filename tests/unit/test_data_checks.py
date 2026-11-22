"""M12 ticket 3.11 (#473): deterministic checks on today's minute bars (kaibot.ingest.data_checks) and their
report-only wiring in LiveMarket behind KAIBOT_DATA_CHECKS (off by default). Each kind of bad data the ticket names
is planted in otherwise clean bars and must give its finding; clean bars give none; with the flag off nothing runs,
and with it on the bars the engine reads are the same as with it off."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from kaibot.engine import market as mk
from kaibot.ingest import data_checks as dc

OPEN = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)  # 09:30 ET


def bars(n: int = 60, px: float = 100.0, drop: tuple[int, ...] = (), **bad) -> pd.DataFrame:
    """`n` clean minute bars from the open at `px`; `drop` removes minutes; `bad` sets the close of minute
    m<k> to a value (bars(m10=0.0))."""
    rows = [{"ts": pd.Timestamp(OPEN) + pd.Timedelta(minutes=m), "o": px, "h": px + 0.1, "l": px - 0.1, "c": px,
             "v": 1000.0, "vw": px} for m in range(n) if m not in drop]
    df = pd.DataFrame(rows)
    for k, v in bad.items():
        df.loc[df["ts"] == pd.Timestamp(OPEN) + pd.Timedelta(minutes=int(k[1:])), "c"] = v
    return df


def at(minute: int, second: int = 5) -> datetime:
    """`second` seconds into minute `minute` of the session: minutes 0..minute-1 have completed."""
    return OPEN + timedelta(minutes=minute, seconds=second)


def kinds(findings) -> list[tuple[str, str]]:
    return [(f.symbol, f.check) for f in findings]


# ---------------------------------------------------------------- the pure checks


def test_stale_seconds():
    assert dc.stale_seconds(at(60, 0), at(60, 30), 300) is None
    assert dc.stale_seconds(at(50, 0), at(60, 0), 300) == 600
    assert dc.stale_seconds(at(55, 0), at(60, 0), 300) is None  # exactly at the limit is still fresh
    assert dc.stale_seconds(None, at(60), 300) == math.inf
    assert dc.stale_seconds(at(61, 0), at(60, 0), 300) is None  # a bar from "the future" is not stale


@pytest.mark.parametrize("value", [0.0, -1.5, float("nan"), float("inf"), None])
def test_bad_price_rows_finds_a_price_that_is_not_above_zero(value):
    df = bars(10)
    df["l"] = df["l"].astype(object)
    df.loc[3, "l"] = value
    assert dc.bad_price_rows(df) == [3]


def test_bad_price_rows_clean_empty_and_partial_frames():
    assert dc.bad_price_rows(bars(10)) == []
    assert dc.bad_price_rows(None) == []
    assert dc.bad_price_rows(bars(0)) == []
    assert dc.bad_price_rows(pd.DataFrame({"ts": [OPEN], "c": [-1.0]})) == [0]  # only the columns it has
    assert dc.bad_price_rows(pd.DataFrame({"ts": [OPEN], "v": [0.0]})) == []  # zero volume is not a price


def test_bar_gaps_counts_bars_and_the_longest_run_without_one():
    assert dc.bar_gaps(range(60), 59, 30) == (30, 30, 0)
    assert dc.bar_gaps([m for m in range(60) if not 40 <= m < 50], 59, 30) == (20, 30, 10)
    assert dc.bar_gaps([0, 1, 2], 9, 30) == (3, 10, 7)  # the window never reaches before the open
    assert dc.bar_gaps([5, 5, 5], 9, 30) == (1, 10, 5)  # a minute counts once
    assert dc.bar_gaps([], 9, 30) == (0, 10, 10)
    assert dc.bar_gaps(range(60), -1, 30) == (0, 0, 0)  # before the first completed minute
    assert dc.bar_gaps(range(100), 59, 30) == (30, 30, 0)  # minutes after `upto_m` are not counted


def test_window_return_uses_the_last_close_at_or_before_each_end():
    closes = {0: 100.0, 5: 110.0}
    assert dc.window_return(closes, 5, 5) == pytest.approx(0.10)
    assert dc.window_return(closes, 7, 5) == pytest.approx(0.10)  # minute 2 -> the close of minute 0; 7 -> 5
    assert dc.window_return(closes, 3, 5) is None  # nothing at or before minute -2
    assert dc.window_return({0: 0.0, 5: 110.0}, 5, 5) is None
    assert dc.window_return({0: 100.0, 5: float("nan")}, 5, 5) is None


def test_unmatched_move():
    assert dc.unmatched_move(0.08, 0.001, 0.05, 0.01)
    assert dc.unmatched_move(-0.08, 0.001, 0.05, 0.01)  # either direction
    assert not dc.unmatched_move(0.08, 0.02, 0.05, 0.01)  # the index moved too
    assert not dc.unmatched_move(0.08, -0.02, 0.05, 0.01)  # any index move of that size counts, whatever its sign
    assert not dc.unmatched_move(0.02, 0.0, 0.05, 0.01)
    assert not dc.unmatched_move(None, 0.0, 0.05, 0.01)
    assert not dc.unmatched_move(0.08, None, 0.05, 0.01)


# ---------------------------------------------------------------- planted bad data on bar frames


def test_clean_bars_give_no_finding():
    today = {"SPY": bars(60, 500.0), "AAA": bars(60), "BBB": bars(60, 50.0)}
    assert dc.check_all(today, ["AAA", "BBB"], "SPY", OPEN, at(60)) == []


def test_nothing_is_checked_before_the_first_minute_has_completed():
    assert dc.check_all({}, ["AAA"], "SPY", OPEN, OPEN + timedelta(seconds=30)) == []
    assert dc.check_all({}, ["AAA"], "SPY", OPEN, OPEN - timedelta(minutes=5)) == []  # before the open


@pytest.mark.parametrize(("fault", "check"), [
    (bars(40), dc.STALE),  # the feed stopped 20 minutes ago
    (bars(0), dc.NO_BARS),
    (None, dc.NO_BARS),
    (bars(60, m30=0.0), dc.BAD_PRICE),
    (bars(60, m30=-4.2), dc.BAD_PRICE),
    (bars(60, m30=float("nan")), dc.BAD_PRICE),
    (bars(60, drop=tuple(range(31, 58))), dc.BAR_GAP),  # 3 of the last 30 minutes
    (bars(60, drop=tuple(range(35, 55))), dc.BAR_GAP),  # 10 of 30 present would pass on count... 20 in a row do not
    (bars(60, m59=108.0), dc.UNMATCHED_MOVE),  # +8% in the last minute, SPY flat
    (bars(60, m59=92.0), dc.UNMATCHED_MOVE),
])
def test_each_planted_fault_gives_its_finding(fault, check):
    today = {"SPY": bars(60, 500.0), "GOOD": bars(60)}
    if fault is not None:
        today["BAD"] = fault
    found = dc.check_all(today, ["GOOD", "BAD"], "SPY", OPEN, at(60))
    assert ("BAD", check) in kinds(found)
    assert {f.symbol for f in found} == {"BAD"}  # the clean symbol and the index are not blamed


def test_a_move_the_index_made_too_is_not_a_finding():
    today = {"SPY": bars(60, 500.0, m59=540.0), "AAA": bars(60, m59=108.0)}
    assert dc.check_all(today, ["AAA"], "SPY", OPEN, at(60)) == []


def test_the_index_is_checked_first_and_never_against_itself():
    today = {"SPY": bars(40, 500.0, m39=560.0), "AAA": bars(60)}
    found = dc.check_all(today, ["SPY", "AAA"], "SPY", OPEN, at(60))
    assert kinds(found) == [("SPY", dc.STALE), ("SPY", dc.BAR_GAP)]


def test_limits_decide_and_findings_carry_value_and_limit():
    today = {"SPY": bars(60, 500.0), "AAA": bars(57)}  # last bar ended 3 minutes and 5 seconds ago
    assert dc.check_all(today, ["AAA"], "SPY", OPEN, at(60)) == []
    (f,) = dc.check_all(today, ["AAA"], "SPY", OPEN, at(60), dc.Limits(max_age_s=120))
    assert (f.symbol, f.check, f.value, f.limit) == ("AAA", dc.STALE, 185.0, 120)
    assert "185 s" in f.detail


def test_the_same_input_gives_the_same_findings_and_the_input_is_not_changed():
    today = {"SPY": bars(60, 500.0), "B": bars(60, m30=0.0), "A": bars(40), "C": bars(60, m59=108.0)}
    before = {k: v.copy() for k, v in today.items()}
    first = dc.check_all(today, ["C", "B", "A"], "SPY", OPEN, at(60))
    assert first == dc.check_all(today, ["A", "B", "C"], "SPY", OPEN, at(60))
    assert [f.symbol for f in first] == sorted(f.symbol for f in first)
    for k, v in today.items():
        pd.testing.assert_frame_equal(v, before[k])


# ---------------------------------------------------------------- LiveMarket: report only, behind the flag


class Data:
    """The one call LiveMarket.update makes: bars per symbol between two times."""

    def __init__(self, frames):
        self.frames = frames

    def bars(self, symbols, start, end, feed="iex", timeframe="1Min"):
        return {s: f[(f["ts"] >= start) & (f["ts"] < end)].reset_index(drop=True)
                for s, f in self.frames.items() if s in symbols}


def live(tmp_path, frames) -> mk.LiveMarket:
    syms = [s for s in frames if s not in ("SPY", "XLK")]
    m = mk.LiveMarket(Data(frames), syms, {s: "XLK" for s in syms}, "SPY", tmp_path, lambda: pd.Series(dtype=float))
    m.day, m.open_utc = OPEN.date(), OPEN
    return m


FRAMES = {"SPY": bars(60, 500.0), "XLK": bars(60, 200.0), "GOOD": bars(60), "BAD": bars(60, m30=0.0)}


@pytest.mark.parametrize(("value", "on"), [(None, False), ("", False), ("0", False), ("no", False), ("off", False),
                                           ("1", True), ("true", True), (" On ", True), ("YES", True)])
def test_the_flag_is_off_unless_set(value, on):
    assert mk.data_checks_on({} if value is None else {mk.DATA_CHECKS_ENV: value}) is on


def test_off_by_default_nothing_runs(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(mk.DATA_CHECKS_ENV, raising=False)
    monkeypatch.setattr(mk, "check_all", lambda *a, **k: pytest.fail("the checks ran with the flag off"))
    m = live(tmp_path, FRAMES)
    with caplog.at_level(logging.WARNING):
        m.update(at(60))
    assert m.last_data_findings == []
    assert "data check" not in caplog.text


def test_on_it_reports_once_and_changes_nothing_the_engine_reads(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(mk.DATA_CHECKS_ENV, raising=False)
    off = live(tmp_path, FRAMES)
    off.update(at(60))
    off.update(at(60, 30))
    monkeypatch.setenv(mk.DATA_CHECKS_ENV, "1")
    on = live(tmp_path, FRAMES)
    with caplog.at_level(logging.WARNING, logger="kaibot.engine.market"):
        on.update(at(60))
        on.update(at(60, 30))  # the fault is still there: it is not logged again
    assert kinds(on.last_data_findings) == [("BAD", dc.BAD_PRICE)]
    assert on.data_findings(at(60)) == on.last_data_findings
    lines = [r.getMessage() for r in caplog.records if "data check" in r.getMessage()]
    assert len(lines) == 1 and "report only" in lines[0] and "BAD bad_price" in lines[0]
    # the same bars, the same last prices and the same fetch position as with the flag off: the bad bar is kept
    assert on._last_fetch == off._last_fetch
    assert on.today_bars.keys() == off.today_bars.keys()
    for s in off.today_bars:
        pd.testing.assert_frame_equal(on.today_bars[s], off.today_bars[s])
        assert on.last_close(s) == off.last_close(s)


def test_a_check_that_fails_does_not_cost_the_tick(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(mk.DATA_CHECKS_ENV, "1")

    def boom(*a, **k):
        raise ValueError("a bug in a check")

    monkeypatch.setattr(mk, "check_all", boom)
    m = live(tmp_path, FRAMES)
    with caplog.at_level(logging.ERROR, logger="kaibot.engine.market"):
        m.update(at(60))  # does not raise
    assert len(m.today_bars["GOOD"]) == 60
    assert m.last_data_findings == []
    assert "nothing is blocked" in caplog.text


def test_new_findings_are_those_not_in_the_last_report():
    a = dc.Finding("A", dc.STALE, 400.0, 300.0, "x")
    b = dc.Finding("B", dc.BAD_PRICE, 1.0, 0.0, "y")
    assert mk.new_findings([a, b], frozenset({("A", dc.STALE)})) == [b]
    assert mk.new_findings([a], frozenset()) == [a]
