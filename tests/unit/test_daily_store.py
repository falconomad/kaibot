"""Daily bar store, read side (story S84, #124): raw prices for orders and costs, prices adjusted at read time
as of a decision date for signals, total return, dividends and splits of a held position. No network."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
from daily_helpers import FIXTURES, ROOT, START, FakeAlpaca, adjusted, bars, run, sessions
from hypothesis import given, settings
from hypothesis import strategies as st

from kaibot.ingest.daily_store import FIELDS, DailyPanel, DailyStore, event_arrays, table_factors

FIX_RAW = json.loads((FIXTURES / "alpaca_bars_1day_raw.json").read_text())
FIX_ALL = json.loads((FIXTURES / "alpaca_bars_1day_all.json").read_text())
FIX_ACTIONS = json.loads((FIXTURES / "alpaca_corporate_actions.json").read_text())
RATIOS = [2.0, 3.0, 4.0, 10.0, 1.5, 0.5, 0.1, 1.05]  # forward splits, a 3-for-2, reverse splits, a 5% stock dividend


def panel_of(closes, actions=(), first="2024-01-02", volume=None) -> DailyPanel:
    """One symbol X: open = previous close (on the new share basis on a split day), high and low 1% outside."""
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    for i, ratio, _ in actions:
        o[i] /= ratio or 1.0
    days = sessions(first, len(c))
    v = None if volume is None else np.asarray(volume, dtype=float)[:, None]
    return DailyPanel.make(days, ["X"], o[:, None], np.maximum(o, c)[:, None] * 1.01, np.minimum(o, c)[:, None] * 0.99,
                           c[:, None], v, [("X", days[i], ratio, cash) for i, ratio, cash in actions])


def returns(x: np.ndarray) -> np.ndarray:
    return x[1:] / x[:-1] - 1.0


@st.composite
def histories(draw, min_n=6, max_n=40):
    """(split-neutral closes, splits [(row, ratio)], dividends [(row, cash as a share of the previous close)])."""
    n = draw(st.integers(min_n, max_n))
    moves = draw(st.lists(st.floats(-0.08, 0.08), min_size=n - 1, max_size=n - 1))
    econ = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + np.array(moves)]))
    rows = st.integers(1, n - 1)
    splits = draw(st.lists(st.tuples(rows, st.sampled_from(RATIOS)), max_size=3, unique_by=lambda e: e[0]))
    divs = draw(st.lists(st.tuples(rows, st.floats(0.0005, 0.05)), max_size=4, unique_by=lambda e: e[0]))
    return econ, sorted(splits), sorted(divs)


def build(econ, splits, divs) -> tuple[DailyPanel, np.ndarray]:
    """Raw panel of a share whose split-neutral close is `econ`: after each split the raw price is divided by its
    ratio. Dividends are cash per raw share, sized as a share of the previous raw close."""
    cum = np.ones(len(econ))
    for row, ratio in splits:
        cum[row:] *= ratio
    raw = econ / cum
    actions = [(row, ratio, None) for row, ratio in splits] + [(row, None, y * raw[row - 1]) for row, y in divs]
    return panel_of(raw, actions, volume=1e6 * cum), cum


# ---------------------------------------------------------------- the two examples of the story


def test_a_four_for_one_split_in_the_middle_leaves_adjusted_returns_continuous():
    raw = [400.0, 404.0, 408.0, 412.0, 103.5, 104.0, 105.0]  # 412 -> 103.5 is +0.49% for the holder, not -75%
    p = panel_of(raw, [(4, 4.0, None)], volume=[1e6] * 4 + [4e6] * 3)
    assert returns(p.c[:, 0])[3] == pytest.approx(-0.7488, abs=1e-4), "the raw series shows the split as a crash"
    for mode in ("split", "total"):
        a = p.adjusted(mode)
        np.testing.assert_allclose(a.c[:, 0], [100.0, 101.0, 102.0, 103.0, 103.5, 104.0, 105.0])
        assert returns(a.c[:, 0])[3] == pytest.approx(103.5 / 103.0 - 1.0)
        np.testing.assert_allclose(a.v[:, 0], 4e6), "volume is put on the same share basis"
        np.testing.assert_allclose(a.o[1:, 0], a.c[:-1, 0]), "opens, highs and lows move with the closes"


def test_reading_as_of_a_date_before_the_split_shows_the_old_unadjusted_scale():
    raw = [400.0, 404.0, 408.0, 412.0, 103.5, 104.0, 105.0]
    p = panel_of(raw, [(4, 4.0, None)])
    before = p.adjusted("total", as_of=p.dates[3])
    np.testing.assert_array_equal(before.c[:4, 0], raw[:4]), "nobody knew the split-adjusted scale on that day"
    assert np.isnan(before.c[4:, 0]).all(), "and nobody had seen the later sessions"
    assert before.as_of == p.dates[3] and before.kind == "total"
    on_the_day = p.adjusted("total", as_of=p.dates[4])
    np.testing.assert_allclose(on_the_day.c[:5, 0], [100.0, 101.0, 102.0, 103.0, 103.5])
    assert on_the_day.c[4, 0] == raw[4], "the price of the decision date is always the raw, tradable price"


# ---------------------------------------------------------------- properties


@settings(max_examples=150, deadline=None)
@given(histories())
def test_splits_never_break_adjusted_returns(h):
    econ, splits, _ = h
    p, cum = build(econ, splits, [])
    for mode in ("split", "total"):
        a = p.adjusted(mode)
        np.testing.assert_allclose(returns(a.c[:, 0]), returns(econ), rtol=1e-9, atol=1e-12)
        np.testing.assert_allclose(a.c[:, 0], econ / cum[-1], rtol=1e-9), "on the share basis of the last session"
        np.testing.assert_allclose(a.c[:, 0] * a.v[:, 0], p.c[:, 0] * p.v[:, 0], rtol=1e-9), "dollar volume is kept"


@settings(max_examples=150, deadline=None)
@given(histories(), st.data())
def test_nothing_after_the_decision_date_changes_what_is_seen_on_it(h, data):
    """No look-ahead, to the last bit: two histories that agree up to D and differ in every later price and
    corporate action give the same adjusted view as of D."""
    econ, splits, divs = h
    n = len(econ)
    d = data.draw(st.integers(0, n - 1))
    other, splits2, divs2 = data.draw(histories(min_n=n, max_n=n))
    econ2 = np.concatenate([econ[:d + 1], other[d + 1:] * 3.0])
    keep = [e for e in splits if e[0] <= d], [e for e in divs if e[0] <= d]
    later = [e for e in splits2 if e[0] > d], [e for e in divs2 if e[0] > d]
    a, _ = build(econ, splits, divs)
    b, _ = build(econ2, keep[0] + later[0], keep[1] + later[1])
    np.testing.assert_array_equal(a.c[:d + 1], b.c[:d + 1]), "the two raw histories agree up to D"
    for mode in ("split", "total"):
        va, vb = a.adjusted(mode, as_of=a.dates[d]), b.adjusted(mode, as_of=b.dates[d])
        for k in (*FIELDS, "split", "div", "pf_split", "pf_total"):
            np.testing.assert_array_equal(getattr(va, k), getattr(vb, k), err_msg=f"{mode} {k}")
        assert np.isnan(va.c[d + 1:]).all()


@settings(max_examples=150, deadline=None)
@given(histories(), st.data())
def test_an_action_after_the_decision_date_is_not_applied(h, data):
    econ, splits, divs = h
    p, _ = build(econ, splits, divs)
    d = data.draw(st.integers(0, len(econ) - 1))
    for mode in ("split", "total"):
        v = p.adjusted(mode, as_of=p.dates[d])
        assert v.c[d, 0] == p.c[d, 0], "the decision date's price is the raw price"
        for t in range(d + 1):
            f = 1.0
            for row, ratio in splits:
                if t < row <= d:
                    f /= ratio
            for row, y in divs:
                if t < row <= d and mode == "total":
                    f *= 1.0 - y  # the dividend as a share of the close before it
            assert v.c[t, 0] == pytest.approx(p.c[t, 0] * f, rel=1e-9)
            assert v.factor[t, 0] == pytest.approx(f, rel=1e-9)


@settings(max_examples=150, deadline=None)
@given(histories(), st.data())
def test_the_whole_window_view_is_the_as_of_view_times_one_constant(h, data):
    """Why a fast harness may compute ratios once on `adjusted()`: cut at D it differs from the view as of D by a
    single factor per symbol, so returns and any other ratio are identical."""
    econ, splits, divs = h
    p, _ = build(econ, splits, divs)
    d = data.draw(st.integers(1, len(econ) - 1))
    for mode in ("split", "total"):
        whole, then = p.adjusted(mode), p.adjusted(mode, as_of=p.dates[d])
        k = whole.c[:d + 1, 0] / then.c[:d + 1, 0]
        np.testing.assert_allclose(k, k[0], rtol=1e-9)
        np.testing.assert_allclose(returns(whole.c[:d + 1, 0]), returns(then.c[:d + 1, 0]), rtol=1e-8, atol=1e-10)
        assert whole.c[d, 0] / whole.factor[d, 0] == pytest.approx(p.c[d, 0]), "a level back on the tradable scale"


@settings(max_examples=150, deadline=None)
@given(histories())
def test_a_held_position_with_dividends_reinvested_is_the_total_return_index(h):
    econ, splits, divs = h
    p, _ = build(econ, splits, divs)
    shares, value = 1.0, [p.c[0, 0]]
    for i in range(1, len(p.dates)):
        shares, cash = p.carry(i, 0, shares)
        shares += cash / p.c[i, 0]  # the dividend buys more shares at that day's close
        value.append(shares * p.c[i, 0])
    index = p.total_return_index("X", base=p.c[0, 0])
    np.testing.assert_allclose(index, value, rtol=1e-9)
    price_only = p.adjusted("split").c[:, 0]
    assert index[-1] / index[0] >= price_only[-1] / price_only[0] - 1e-9, "dividends never subtract"
    if not divs:
        np.testing.assert_allclose(index / index[0], price_only / price_only[0], rtol=1e-9)


@settings(max_examples=150, deadline=None)
@given(histories())
def test_dividends_only_change_the_total_mode(h):
    econ, splits, divs = h
    p, _ = build(econ, splits, divs)
    s, t = p.adjusted("split"), p.adjusted("total")
    assert (t.c[:, 0] <= s.c[:, 0] * (1 + 1e-12)).all(), "a dividend lowers the earlier adjusted prices, never raises"
    np.testing.assert_array_equal(t.v, s.v), "volume is adjusted for splits only"
    ex = {row for row, _ in divs}
    for i in range(1, len(econ)):
        if i not in ex:
            assert t.c[i, 0] / t.c[i - 1, 0] == pytest.approx(s.c[i, 0] / s.c[i - 1, 0], rel=1e-9)


# ---------------------------------------------------------------- the arithmetic, case by case


def test_a_dividend_is_removed_with_the_close_before_its_ex_date():
    p = panel_of([100.0, 102.0, 99.0, 100.0], [(2, None, 2.04)])  # 2% of the close before
    t = p.adjusted("total")
    np.testing.assert_allclose(t.c[:, 0], [98.0, 99.96, 99.0, 100.0])
    assert returns(t.c[:, 0])[1] == pytest.approx(99.0 / (102.0 - 2.04) - 1.0)
    np.testing.assert_array_equal(p.adjusted("split").c, p.c), "split-only leaves dividends alone"
    np.testing.assert_allclose(p.total_return("X")[1:], [0.02, (99.0 + 2.04) / 102.0 - 1.0, 100.0 / 99.0 - 1.0])
    assert np.isnan(p.total_return("X")[0])


def test_split_and_dividend_on_the_same_day_and_the_shares_of_a_held_position():
    # 2-for-1 on row 2 together with a $1 dividend per share held before the split, then $0.50 per new share
    p = panel_of([100.0, 100.0, 50.0, 50.0, 51.0], [(2, 2.0, 1.0), (4, None, 0.5)])
    assert p.carry(2, 0, 10.0) == (20.0, 10.0)
    assert p.hold(0, 0, 4, 10.0) == (20.0, 20.0), "10 shares: $10 before the split, then 20 shares x $0.50"
    assert p.hold(0, 2, 4, 10.0) == (10.0, 5.0), "bought on the ex-date: no first dividend, no split"
    assert p.hold(0, 0, 1, 10.0) == (10.0, 0.0), "sold before the ex-date"
    assert p.hold(0, 1, 2, 10.0) == (20.0, 10.0), "sold on the ex-date: the dividend is still earned"
    assert p.total_return("X")[2] == pytest.approx((50.0 * 2 + 1.0) / 100.0 - 1.0)
    t = p.adjusted("total")
    assert t.c[1, 0] == pytest.approx(100.0 * (1 - 1.0 / 100.0) / 2 * (1 - 0.5 / 50.0))
    with pytest.raises(ValueError, match="raw panel"):
        t.carry(2, 0, 10.0)


def test_actions_land_on_the_first_bar_on_or_after_the_ex_date_or_nowhere():
    days = sessions("2024-01-02", 6)  # Tue 2 Jan .. Tue 9 Jan
    has = np.ones((6, 1), dtype=bool)
    has[2, 0] = False  # no bar on Thursday 4 Jan (a halt)
    weekend, halt = date(2024, 1, 6), days[2]
    outside = [(date(2023, 12, 1), 3.0, np.nan), (date(2024, 2, 1), 5.0, np.nan)]
    split, div = event_arrays(np.asarray(days, dtype="datetime64[D]"), has,
                              {0: [(weekend, 2.0, np.nan), (halt, np.nan, 0.5), *outside]})
    assert split[:, 0].tolist() == [1, 1, 1, 1, 2, 1], "Saturday's split takes effect on Monday"
    assert div[:, 0].tolist() == [0, 0, 0, 0.5, 0, 0], "the dividend of the halted day lands on the next bar"
    # before the first session and after the last bar: not in the panel at all


def test_a_dividend_as_large_as_the_price_is_ignored_and_counted():
    p = panel_of([10.0, 10.0, 10.0], [(1, None, 10.0), (2, None, 0.1)])
    assert p.notes["bad_dividends"] == 1
    np.testing.assert_allclose(p.adjusted("total").c[:, 0], [9.9, 9.9, 10.0])
    _, total, bad = table_factors(p.c, p.split, p.div)
    assert bad == 1 and np.isfinite(total).all()


def test_several_symbols_with_gaps_share_one_calendar():
    days = sessions("2024-01-02", 5)
    c = np.array([[10.0, np.nan], [11.0, np.nan], [np.nan, 50.0], [6.0, 51.0], [6.5, 26.0]])
    p = DailyPanel.make(days, ["A", "B"], c, c, c, c, None, [("A", days[2], 2.0, None), ("B", days[4], 2.0, 1.0)])
    assert p.split[:, 0].tolist() == [1, 1, 1, 2, 1], "A has no bar on its ex-date: the split lands on the next bar"
    a = p.adjusted("total")
    np.testing.assert_allclose(a.c[[0, 1, 3, 4], 0], [5.0, 5.5, 6.0, 6.5])
    assert np.isnan(a.c[2, 0]) and np.isnan(a.c[:2, 1]).all()
    np.testing.assert_allclose(a.c[2:, 1], [50.0 * (1 - 1 / 51) / 2, 51.0 * (1 - 1 / 51) / 2, 26.0])
    assert p.total_return("A")[3] == pytest.approx(6.0 * 2 / 11.0 - 1.0), "the previous close is the last one seen"
    assert p.col("B") == 1 and p.cols(["B", "nope", "A"]).tolist() == [1, 0]
    assert p.index_from("2024-01-06") == 4 and p.index_upto("2024-01-06") == 3 and p.index_upto("2023-01-01") == -1


def test_wrong_use_is_refused():
    p = panel_of([1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="mode"):
        p.adjusted("dividends")
    with pytest.raises(ValueError, match="source"):
        p.adjusted("total", source="yahoo")
    with pytest.raises(ValueError, match="before the first session"):
        p.adjusted("total", as_of="2020-01-01")
    with pytest.raises(ValueError, match="raw panel"):
        p.adjusted("split").adjusted("total")
    assert np.array_equal(p.factor, np.ones((3, 1))) and p.kind == "raw"


def test_the_frame_has_the_fields_of_the_harness_daily_bars():
    p = panel_of([10.0, 11.0, 12.0])
    df = p.adjusted("total").to_frame()
    assert list(df.columns) == ["symbol", "date", "o", "h", "l", "c", "v", "c1555", "v_first", "v_last"]
    assert FIELDS == ("o", "h", "l", "c", "v", "c1555", "v_first", "v_last")
    assert len(df) == 3 and df["symbol"].tolist() == ["X"] * 3 and str(df["date"].dtype) == "datetime64[ns]"
    assert df["c1555"].isna().all(), "without minute data there is no 15:55 price, and the close never stands in"
    assert (df["v_first"] == 0.02 * df["v"]).all() and (df["v_last"] == 0.02 * df["v"]).all()


def test_the_harness_daily_bars_read_the_raw_and_the_adjusted_panel_unchanged():
    """The hand-off of docs/ops/daily_bars.md against the real `DailyBars` of replay/multiday.py (#113)."""
    from kaibot.replay.multiday import FIELDS as HARNESS_FIELDS
    from kaibot.replay.multiday import DailyBars, indicators

    assert HARNESS_FIELDS == FIELDS
    closes = [100.0 + i for i in range(30)]
    closes[15:] = [c / 4 for c in closes[15:]]
    i_split = 15
    p = panel_of(closes, actions=[(i_split, 4.0, None)])
    assert p.split[i_split, 0] == 4.0
    for view in (p, p.adjusted("total")):
        b = DailyBars.from_frame(view.to_frame(), view.symbols)
        assert np.array_equal(b.dates, view.dates)
        for k in ("o", "h", "l", "c", "v", "v_first", "v_last"):
            np.testing.assert_array_equal(getattr(b, k), getattr(view, k))
    raw, sig = (DailyBars.from_frame(v.to_frame(), v.symbols) for v in (p, p.adjusted("total")))
    assert raw.c[i_split, 0] < raw.c[i_split - 1, 0] / 3, "market side: the raw split is visible"
    assert indicators(sig) is not None and sig.c[i_split, 0] > sig.c[i_split - 1, 0], "signal side: continuous"


def test_the_handoff_recipe_for_multiday_and_multiday_core():
    """docs/ops/daily_bars.md: signals on the split-adjusted side, ATR back in raw dollars via `factor`, and the
    90-session move filter of replay/multiday_core.py (#127) not tripped by a split."""
    from kaibot.replay.multiday import DailyBars, indicators
    from kaibot.replay.multiday_core import Signals

    i_split, n = 60, 120
    closes = [100.0 * 1.001 ** i for i in range(n)]
    closes[i_split:] = [c / 2 for c in closes[i_split:]]
    p = panel_of(closes, actions=[(i_split, 2.0, None)])
    adj = p.adjusted("split")
    raw, sig = (DailyBars.from_frame(v.to_frame(), v.symbols) for v in (p, adj))
    f = adj.factor
    ind_raw, ind_sig = indicators(raw), indicators(sig)
    for i in (40, 100):  # windows wholly before and wholly after the split
        assert ind_sig.atr[i, 0] / f[i, 0] == pytest.approx(ind_raw.atr[i, 0], rel=1e-9)
    assert ind_raw.atr[i_split + 2, 0] > 3 * ind_sig.atr[i_split + 2, 0] / f[i_split + 2, 0], "raw ATR spikes"
    keep = np.isfinite(raw.c[:, raw.col("X")])
    assert len(p.total_return("X")[keep]) == len(raw.keep_sessions(keep).dates)
    on_sig = Signals(sig, ind_sig, np.array([0]), 0)
    on_raw = Signals(raw, ind_raw, np.array([0]), 0)
    assert not on_sig.big_move[i_split:i_split + 90, 0].any(), "signal side: a split is not a 15% move"
    assert on_raw.big_move[i_split:i_split + 90, 0].all(), "on raw bars a split would bar the stock for 90 sessions"


# ---------------------------------------------------------------- through the store on disk


@pytest.fixture
def alpaca(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return fake


def stored(tmp_path, alpaca, *symbols, start="2026-10-01") -> DailyStore:
    assert run(tmp_path, "--symbols", ",".join(symbols), start=start) == 0
    return DailyStore(tmp_path / "data" / "daily")


def spy_bars(start="2026-10-01"):
    days = sessions(start)
    return days, bars(days, [600.0 + i for i in range(len(days))])


def test_the_fixture_reads_back_raw_and_adjusted(tmp_path, alpaca):
    alpaca.add("SPY", spy_bars()[1])
    alpaca.add("ZZZ", FIX_RAW["bars"], FIX_ALL["bars"])
    alpaca.actions = {k: v for k, v in FIX_ACTIONS["corporate_actions"].items() if k != "cash_mergers"}
    s = stored(tmp_path, alpaca, "ZZZ")
    assert s.keys() == ["SPY", "ZZZ"] and s.meta("ZZZ")["rows"] == 10
    df = s.bars("ZZZ", start="2026-10-26", end="2026-10-27")
    assert df["c"].tolist() == [410.0, 103.0] and df["v"].tolist() == [1e6, 4e6], "raw, as traded"
    assert list(df.columns) == ["date", "o", "h", "l", "c", "v", "n", "vw", "available_ts"]
    p = s.panel(["ZZZ"])
    assert p.div[:, 0].tolist() == [0, 0, 0, 4.2, 0, 0, 0, 0, 0.3, 0], "1.20 + 3.00 special; the repeat is skipped"
    assert p.split[:, 0].tolist() == [1, 1, 1, 1, 1, 1, 4, 1, 1, 1]
    a = p.adjusted("split")
    np.testing.assert_allclose(a.c[:, 0], [100.0, 101.0, 102.0, 100.8, 101.5, 102.5, 103.0, 104.0, 102.0, 105.0])
    np.testing.assert_allclose(a.v[:, 0], 4e6)
    acts = s.actions("ZZZ")
    assert len(acts) == 6 and not acts["dup"].any() and len(s.actions("ZZZ", dups=True)) == 7
    assert s.actions()["key"].unique().tolist() == ["ZZZ"]
    # Alpaca's own series (the fixture applies the $1.20 dividend and the split) as the factor
    r = p.adjusted("total", source="reference")
    np.testing.assert_allclose(r.c[:, 0], [b["c"] for b in FIX_ALL["bars"]], rtol=1e-6)
    np.testing.assert_allclose(p.adjusted("split", source="reference").c[:, 0], a.c[:, 0], rtol=1e-9)
    assert r.adj_source == {"ZZZ": "reference"} and a.adj_source == {"ZZZ": "table"}
    assert p.notes["references"] == 1


def test_a_window_is_the_whole_history_seen_as_of_its_last_day(tmp_path, alpaca):
    alpaca.add("SPY", spy_bars()[1])
    alpaca.add("ZZZ", FIX_RAW["bars"], FIX_ALL["bars"])
    alpaca.actions = {k: v for k, v in FIX_ACTIONS["corporate_actions"].items() if k != "cash_mergers"}
    s = stored(tmp_path, alpaca, "ZZZ")
    whole = s.panel(["ZZZ", "SPY"])
    cut = s.panel(["ZZZ", "SPY"], start="2026-10-20", end="2026-10-26")
    assert (str(cut.dates[0]), str(cut.dates[-1])) == ("2026-10-20", "2026-10-26")
    assert cut.split.max() == 1.0, "the split of the 27th does not exist in a window that ends on the 26th"
    for mode in ("split", "total"):
        for source in ("table", "reference"):
            a = cut.adjusted(mode, source=source)
            b = whole.adjusted(mode, as_of="2026-10-26", source=source)
            i0, i1 = whole.index_from("2026-10-20"), whole.index_upto("2026-10-26")
            np.testing.assert_allclose(a.c, b.c[i0:i1 + 1], rtol=1e-12)
    np.testing.assert_array_equal(cut.adjusted("split").c[:, 0], cut.c[:, 0]), "before the split: the old scale"
    with pytest.raises(ValueError, match="no daily bars between"):
        s.panel(["ZZZ"], start="2030-01-01")
    with pytest.raises(ValueError, match="no daily bars for any"):
        s.panel(["NOPE"])
    assert np.isnan(s.panel(["ZZZ", "NOPE"]).c[:, 1]).all(), "a key without bars is an empty column"


def test_spy_total_return_series_adds_the_dividends_to_the_price_return(tmp_path, alpaca):
    days = sessions("2025-01-02")
    closes = 500.0 * np.cumprod(np.full(len(days), 1.0004))
    alpaca.add("SPY", bars(days, closes))
    paid = [d for d in days if d.day >= 18 and d.month in (3, 6, 9, 12)]
    ex = [next(d for d in paid if (d.year, d.month) == ym) for ym in sorted({(d.year, d.month) for d in paid})]
    for d in ex:
        alpaca.act("cash_dividend", symbol="SPY", rate=1.75, ex_date=d.isoformat(), process_date=d.isoformat())
    s = stored(tmp_path, alpaca, "SPY", start="2025-01-01")
    tr = s.total_return("SPY")
    assert list(tr.columns) == ["date", "close", "dividend", "split", "ret", "index"]
    assert (tr["dividend"] > 0).sum() == len(ex) == 7 and tr["index"].iloc[0] == 1.0
    price = closes[-1] / closes[0]
    assert tr["index"].iloc[-1] > price * 1.02, "seven dividends of about 0.33% each"
    i = int(np.flatnonzero(tr["dividend"].to_numpy() > 0)[0])
    assert tr["ret"].iloc[i] == pytest.approx((closes[i] + 1.75) / closes[i - 1] - 1.0)
    p = s.panel(["SPY"])
    np.testing.assert_allclose(p.total_return("SPY")[1:], tr["ret"].to_numpy()[1:])
    # what a harness does for its benchmark: the daily returns of sessions i0..i1
    i0, i1 = p.index_from("2026-01-02"), p.index_upto("2026-06-30")
    window = p.total_return("SPY")[i0:i1 + 1]
    assert len(window) == i1 - i0 + 1 and np.isfinite(window).all()
    part = s.total_return("SPY", start="2026-01-02", end="2026-06-30")
    assert part["index"].iloc[-1] == pytest.approx(np.prod(1 + window[1:]))


def test_xlc_before_its_first_bar_continues_with_spy(tmp_path, alpaca):
    days, spy = spy_bars()
    alpaca.add("SPY", spy)
    late = days[10:]
    alpaca.add("XLC", bars(late, [80.0 + 0.5 * i for i in range(len(late))]))
    alpaca.act("cash_dividend", symbol="SPY", rate=1.8, ex_date=days[5].isoformat(), process_date=days[5].isoformat())
    s = stored(tmp_path, alpaca, "XLC")
    a = s.panel(["XLC", "SPY"]).adjusted("total")
    assert np.isnan(a.c[:10, 0]).all(), "the hole: no XLC before its first bar"
    x = a.spliced_close("XLC", "SPY")
    np.testing.assert_array_equal(x[10:], a.c[10:, 0]), "from its first bar on: XLC itself"
    np.testing.assert_allclose(returns(x)[:10], returns(a.c[:, 1])[:10], rtol=1e-12), "before: SPY's total returns"
    assert x[9] / a.c[9, 1] == pytest.approx(x[10] / a.c[10, 1]), "joined at the first bar without a jump"
    assert np.isfinite(x).all()
    np.testing.assert_array_equal(a.spliced_close("SPY", "XLC"), a.c[:, 1]), "nothing to fill: unchanged"


def test_minute_fields_are_put_back_on_the_raw_basis_or_dropped(tmp_path, alpaca):
    alpaca.add("SPY", spy_bars()[1])
    alpaca.add("ZZZ", FIX_RAW["bars"], FIX_ALL["bars"])
    alpaca.act("forward_split", symbol="ZZZ", new_rate=4, old_rate=1, ex_date="2026-10-27", process_date="2026-10-27")
    s = stored(tmp_path, alpaca, "ZZZ")
    raw_c = [b["c"] for b in FIX_RAW["bars"]]
    days = pd.to_datetime([b["t"][:10] for b in FIX_RAW["bars"]])
    scale = np.array([0.25] * 6 + [1.0] * 4)  # the minute history is split-adjusted as of its download
    minute = pd.DataFrame({"symbol": "ZZZ", "date": days, "c": np.array(raw_c) * scale * 1.0005,
                           "c1555": np.array(raw_c) * scale * 0.999, "v_first": 30_000 / scale,
                           "v_last": 50_000 / scale})
    minute.loc[2, "c"] = raw_c[2]  # one day of the minute history is still on the pre-split basis
    extra = pd.DataFrame({"symbol": ["OTHER", "ZZZ"], "date": pd.to_datetime(["2026-10-20", "2026-12-01"]), "c": 1.0,
                          "c1555": 1.0, "v_first": 1.0, "v_last": 1.0})
    p = s.panel(["ZZZ"], intraday=pd.concat([minute, extra], ignore_index=True))
    good = [i for i in range(10) if i != 2]
    np.testing.assert_allclose(p.c1555[good, 0], np.array(raw_c)[good] * 0.999), "a raw 15:55 price"
    np.testing.assert_allclose(p.v_first[good, 0], 30_000.0), "raw shares"
    np.testing.assert_allclose(p.v_last[good, 0], 50_000.0)
    assert np.isnan(p.c1555[2, 0]) and p.v_first[2, 0] == 0.02 * p.v[2, 0], "the stale day is dropped, volume estimated"
    assert p.notes["intraday_rows"] == 9 and p.notes["intraday_scale_mismatch"] == 1
    a = p.adjusted("split")
    np.testing.assert_allclose(a.c1555[good, 0], (np.array(raw_c) * scale)[good] * 0.999)
    np.testing.assert_allclose(a.v_first[good, 0], (30_000 / scale)[good])
    cut = s.panel(["ZZZ"], end="2026-10-23", intraday=minute)
    np.testing.assert_allclose(cut.c1555[[0, 1, 3, 4], 0], np.array(raw_c)[[0, 1, 3, 4]] * 0.999), \
        "the basis of the minute history does not depend on the window that is read"
    none = s.panel(["ZZZ"], minute_share=None)
    assert np.isnan(none.c1555).all() and np.isnan(none.v_first).all() and np.isnan(none.v_last).all()
    with pytest.raises(ValueError, match="intraday frame needs"):
        s.panel(["ZZZ"], intraday=minute.drop(columns=["c1555"]))


def test_auto_uses_alpacas_factor_only_for_flagged_keys(tmp_path, alpaca):
    days, spy = spy_bars()
    alpaca.add("SPY", spy)
    raw = bars(days, [100.0] * 10 + [80.0] * (len(days) - 10))  # a spin-off on row 10 takes 20% of the price away
    alpaca.add("PAR", raw, adjusted(raw, other={days[10]: 0.8}))
    alpaca.add("OKK", raw, raw)
    s = stored(tmp_path, alpaca, "PAR,OKK")
    p = s.panel(["PAR", "OKK"])
    assert p.flagged == frozenset() and s.flagged() == frozenset()
    table = p.adjusted("total", source="auto")
    assert table.adj_source == {"PAR": "table", "OKK": "table"} and returns(table.c[:, 0])[9] == pytest.approx(-0.2)
    check = s.root / "check"
    check.mkdir()
    (check / "adjustment.csv").write_text("key,verdict\nPAR,mismatch\nOKK,ok\n")
    p = s.panel(["PAR", "OKK"])
    assert p.flagged == frozenset({"PAR"})
    auto = p.adjusted("total", source="auto")
    assert auto.adj_source == {"PAR": "reference", "OKK": "table"}
    assert abs(returns(auto.c[:, 0])[9]) < 1e-12, "no false 20% loss in the signal series of the parent"
    assert returns(p.adjusted("total").c[:, 0])[9] == pytest.approx(-0.2), "the table alone does not know spin-offs"
    then = p.adjusted("total", as_of=days[9], source="auto")
    np.testing.assert_allclose(then.c[:10, 0], 100.0), "as of the day before, nothing is adjusted"


def test_the_live_engine_never_imports_the_daily_store():
    """Research data only: nothing on the live path may read it (CLAUDE.md, issue #124)."""
    src = ROOT / "src" / "kaibot"
    live = ["engine", "risk", "broker", "strategies", "agents", "news", "features", "store"]
    research = {"engine/daily_report.py"}  # #161: the research command's loader; the live engine never imports it
    offenders = [str(f.relative_to(src)) for pkg in live for f in (src / pkg).rglob("*.py")
                 if ("ingest.daily_" in f.read_text() or "ingest import daily_" in f.read_text())
                 and str(f.relative_to(src)) not in research]
    assert offenders == []
    for name in ("daily_bars", "daily_store", "daily_check"):
        text = (src / "ingest" / f"{name}.py").read_text()
        assert not any(f"kaibot.{pkg}" in text for pkg in ("engine", "risk", "broker", "strategies", "replay")), name


def test_reading_needs_the_corporate_actions_table(tmp_path, alpaca):
    alpaca.add("SPY", spy_bars()[1])
    assert run(tmp_path, "--symbols", "SPY", "--no-actions", start="2026-10-01") == 0
    s = DailyStore(tmp_path / "data" / "daily")
    with pytest.raises(FileNotFoundError, match="no corporate-actions table"):
        s.panel(["SPY"])
    with pytest.raises(FileNotFoundError, match="no corporate-actions table"):
        s.actions()
    assert s.holes().empty and s.reference("NOPE") is None and len(s.reference("SPY")) == len(sessions(START)) - 22
