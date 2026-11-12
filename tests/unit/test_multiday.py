"""Issue #113: the multi-day simulator, the four pre-declared rules, their walk-forward, the earnings blackout,
the benchmarks, the one-command summary and the trade breakdown. Small synthetic bars only, no network."""

import dataclasses
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kaibot.replay import earnings
from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.multiday import (
    Caps,
    DailyBars,
    Engine,
    build_daily,
    daily_from_minutes,
    equal_weight_benchmark,
    indicators,
    load_or_build_daily,
    spy_buy_and_hold,
)
from kaibot.replay.multiday_rules import (
    EXPERIMENTS,
    OvernightSpy,
    RelStrength,
    TrendHighs,
    WinnersRun,
    book_digest,
    make_rule,
    variant_a_trades,
    walk_forward,
)
from kaibot.replay.sim import Trade, half_spread
from kaibot.replay.walkforward import first_train_month, make_folds
from kaibot.risk.profiles import load_profiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_trades  # noqa: E402
import experiments_summary  # noqa: E402
import walk_forward_multiday as wfm  # noqa: E402

POL = load_profiles()["balanced"]  # risk 0.5% a trade, position <= 15%
COSTS = ("cost_spread", "cost_impact", "cost_slippage", "cost_fees")
CAPS = Caps()


def sessions(n: int, first: str = "2024-01-02") -> np.ndarray:
    return np.busday_offset(np.datetime64(first), np.arange(n), roll="forward").astype("datetime64[D]")


def flat(n: int, symbols=("AAA",), price: float = 100.0, rng: float = 1.0) -> DailyBars:
    """Every session opens and closes at `price` with a high / low `rng` away: the 14-day ATR is 2 * rng."""
    c = np.full((n, len(symbols)), price)
    return DailyBars.make(sessions(n), symbols, c.copy(), c + rng, c - rng, c)


def set_day(b: DailyBars, i: int, j: int, o: float, h: float, l: float, c: float) -> None:  # noqa: E741
    b.o[i, j], b.h[i, j], b.l[i, j], b.c[i, j], b.c1555[i, j] = o, h, l, c, c


class Script:
    """A rule that does what the test says: (session, column, kwargs) entries at the open or at the close."""

    name = "script"

    def __init__(self, opens=(), closes=(), caps: Caps = CAPS):
        self.opens, self.closes, self.caps = list(opens), list(closes), caps

    def at_open(self, i, eng, cfg, eq):
        for day, j, kw in self.opens:
            if day == i:
                eng.enter(i, j, eq, "t", self.caps, **kw)

    def at_close(self, i, eng, cfg, eq):
        for day, j, kw in self.closes:
            if day == i:
                eng.enter(i, j, eq, "t", self.caps, at="close", **kw)


def run(b: DailyBars, rule, i0: int = 15, i1: int | None = None, **kw) -> Engine:
    eng = Engine(b, indicators(b), POL, rule, lambda i: None, **kw)
    eng.run(i0, len(b.dates) - 1 if i1 is None else i1, close_at_end=i1 is None)  # a cut-off run stays open
    return eng


def split_ok(t) -> bool:
    return math.isclose(t.pnl, t.gross_pnl - sum(getattr(t, k) for k in COSTS), abs_tol=1e-6)


# ---------------------------------------------------------------- 1. daily bars from minute bars


def minute_day(first_m: int = 0, last_m: int = N_MIN - 1) -> SymbolDays:
    """One session 100 -> 101 with the bars before `first_m` and after `last_m` missing (late open, half day)."""
    px = np.full(N_MIN, np.nan)
    px[first_m:last_m + 1] = np.linspace(100.0, 101.0, last_m + 1 - first_m)
    v = np.where(np.isnan(px), 0.0, 1000.0)
    v[first_m], v[last_m] = 7000.0, 9000.0
    c = pd.Series(px).ffill().to_numpy()  # what data.from_frame does with a minute that has no bar
    o, h, l = c.copy(), c + 0.05, c - 0.05  # noqa: E741
    arr = [a[None, :] for a in (o, h, l, c, v, c)]
    return SymbolDays("AAA", np.array([np.datetime64("2024-06-03")]), *arr)


def test_daily_bar_uses_the_first_and_last_regular_minutes():
    d = daily_from_minutes(minute_day())
    assert d["o"][0] == 100.0 and d["c"][0] == 101.0 and d["v_first"][0] == 7000.0 and d["v_last"][0] == 9000.0
    assert math.isclose(d["h"][0], 101.05) and math.isclose(d["l"][0], 99.95)
    assert d["v"][0] == 7000.0 + 9000.0 + 388 * 1000.0
    assert math.isclose(d["c1555"][0], 100.0 + 385 / 389)  # the 15:55 bar, not the last one
    late = daily_from_minutes(minute_day(first_m=3, last_m=209))  # opens at 09:33, half day ends 13:00
    assert late["o"][0] == 100.0 and late["c"][0] == 101.0 == late["c1555"][0]
    assert late["v_first"][0] == 7000.0 and late["v_last"][0] == 9000.0


def test_daily_cache_is_built_once_and_rebuilt_when_the_minutes_change(tmp_path):
    def write(sym: str, month: str, close: float) -> None:
        ts = pd.date_range(f"{month}-03 14:30", periods=N_MIN, freq="min", tz="UTC")
        f = tmp_path / "bars_1m" / f"symbol={sym}" / f"{month}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"ts": ts, "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 100.0,
                      "vw": close}).to_parquet(f)

    write("AAA", "2024-01", 50.0)
    write("SPY", "2024-01", 400.0)
    args = (tmp_path, ["AAA", "SPY", "ZZZ"], "2024-01-01", "2024-03-31")
    b, built = load_or_build_daily(*args)
    assert built and b.symbols == ["AAA", "SPY", "ZZZ"] and str(b.dates[0]) == "2024-01-03"
    assert b.c[0, 0] == 50.0 and b.h[0, 1] == 401.0 and np.isnan(b.c[0, 2]) and b.v[0, 0] == 100.0 * N_MIN
    again, built = load_or_build_daily(*args)
    assert not built and np.array_equal(again.c, b.c, equal_nan=True) and np.array_equal(again.dates, b.dates)
    write("AAA", "2024-02", 55.0)  # a new month file: the cache is stale
    b2, built = load_or_build_daily(*args)
    assert built and len(b2.dates) == 2 and b2.c[1, 0] == 55.0 and np.isnan(b2.c[1, 1])
    assert (tmp_path / "multiday" / "daily_bars.parquet").is_file()


# ---------------------------------------------------------------- 2. fills, stops, holding, sizing


def test_stop_gapped_through_fills_at_the_open_and_the_gap_is_reported():
    b = flat(30)
    set_day(b, 21, 0, 90.0, 91.0, 89.0, 90.0)  # opens 10 below a stop at about 96
    eng = run(b, Script(opens=[(20, 0, {"stop_atr": 2.0})]))
    (t,) = eng.book.trades
    entry_mid, stop = 100.0, t.entry - 2.0 * 2.0
    assert t.reason == "stop" and t.gap and str(t.exit_date) == str(b.dates[21]) and t.hold_days == 1
    assert t.exit < 90.0 and math.isclose(t.stop, stop)  # sold at the open minus costs, not at the stop
    assert math.isclose(t.cost_slippage, (stop - 90.0) * t.qty) and t.cost_slippage > 0
    assert math.isclose(t.gross_pnl, (stop - entry_mid) * t.qty)  # the signal is credited the stop level
    assert split_ok(t) and math.isclose(t.pnl, (t.exit - t.entry) * t.qty - t.cost_fees)
    assert math.isclose(eng.equity, 100_000.0 + t.pnl)


def test_stop_touched_inside_the_day_fills_at_the_stop():
    b = flat(30)
    set_day(b, 21, 0, 100.0, 100.5, 94.0, 95.0)  # opens above the stop, trades through it
    (t,) = run(b, Script(opens=[(20, 0, {"stop_atr": 2.0})])).book.trades
    assert t.reason == "stop" and not t.gap and t.cost_slippage == 0.0
    assert t.stop - 2 * half_spread(t.stop) - 0.05 < t.exit < t.stop and split_ok(t)


def test_a_stop_cannot_gap_on_the_entry_day_but_the_low_can_reach_it():
    b = flat(30)
    set_day(b, 20, 0, 100.0, 100.5, 95.0, 96.0)
    (t,) = run(b, Script(opens=[(20, 0, {"stop_atr": 2.0})])).book.trades
    assert t.reason == "stop" and not t.gap and t.hold_days == 0


def test_trailing_stop_moves_up_at_each_close_and_never_down():
    b = flat(40)
    for k, c in enumerate((104.0, 108.0, 112.0, 110.0, 109.0)):  # two up days too many, then a slide
        set_day(b, 21 + k, 0, c, c + 1, c - 1, c)
    eng = Engine(b, indicators(b), POL, Script(opens=[(20, 0, {"stop_atr": 3.0, "trail_atr": 3.0})]), lambda i: None)
    stops = []
    for i in range(15, 26):
        eng.step(i)
        if 0 in eng.pos:
            stops.append(eng.pos[0].stop)
    assert stops == sorted(stops) and stops[-1] > stops[0]  # 20..25: only ever raised
    atr = indicators(b).atr
    assert math.isclose(eng.pos[0].stop, 112.0 - 3.0 * atr[23, 0])  # highest close minus 3 ATRs of that day
    set_day(b, 26, 0, 109.0, 109.5, eng.pos[0].stop - 1.0, 108.0)
    stop = eng.pos[0].stop
    eng.step(26)
    (t,) = eng.book.trades
    assert t.reason == "trail" and not t.gap and math.isclose(t.stop, stop) and t.pnl > 0 and split_ok(t)


def test_max_holding_sells_at_that_close():
    b = flat(40)
    eng = run(b, Script(opens=[(20, 0, {"max_hold": 5})]))
    (t,) = eng.book.trades
    assert t.reason == "max_hold" and t.hold_days == 5 and str(t.exit_date) == str(b.dates[25])
    assert math.isnan(t.stop) and split_ok(t)
    assert math.isclose(t.gross_pnl, 0.0, abs_tol=1e-9) and t.pnl < 0  # flat prices: the whole loss is costs
    assert [r["positions"] for r in eng.book.daily][5:11] == [1, 1, 1, 1, 1, 0]


def test_size_is_set_by_a_three_atr_gap_and_capped_by_the_position_limit():
    b = flat(30, symbols=("WILD", "CALM"), rng=1.0)
    b.h[:, 1], b.l[:, 1] = 100.05, 99.95  # ATR 0.1: the gap rule alone would buy 166% of equity
    eng = run(b, Script(opens=[(20, 0, {}), (20, 1, {})]), i1=20)
    wild, calm = eng.pos[0], eng.pos[1]
    assert wild.qty == math.floor(100_000 * 0.005 / (3 * 2.0))  # $500 risk / (3 x ATR 2) = 83 shares
    assert wild.qty * 3 * 2.0 <= 500.0 and math.isclose(wild.risk, wild.qty * 6.0)
    assert calm.qty == math.floor(100_000 * 0.15 / 100.0)  # 15% of equity


def test_caps_on_positions_and_on_total_held_and_never_more_than_the_cash():
    syms = tuple(f"S{k}" for k in range(8))
    b = flat(30, symbols=syms, rng=0.64)  # ATR 1.28: the gap rule gives 130 shares = 13% of equity each
    eng = run(b, Script(opens=[(20, j, {}) for j in range(8)]), i1=20)
    assert sorted(p.qty for p in eng.pos.values()) == [80, 130, 130, 130, 130]  # the fifth gets what is left of 60%
    assert sum(p.qty * 100.0 for p in eng.pos.values()) == 0.60 * 100_000 and eng.cash > 0
    few = run(b, Script(opens=[(20, j, {}) for j in range(8)], caps=Caps(max_positions=2)), i1=20)
    assert sorted(few.pos) == [0, 1]  # the third is refused
    tight = run(b, Script(opens=[(20, j, {}) for j in range(8)], caps=Caps(max_exposure_pct=20.0)), i1=20)
    assert sorted(p.qty for p in tight.pos.values()) == [70, 130]
    poor = Engine(b, indicators(b), POL, Script(opens=[(20, 0, {})]), lambda i: None, equity0=100_000.0)
    poor.cash = 5_000.0  # the equity says 130 shares, the cash says 49
    poor.step(20)
    assert poor.pos[0].qty == 49 and poor.cash >= 0


def test_quantities_do_not_depend_on_the_print_they_fill_at():
    """Orders are sized before they fill: the open print (for a buy at the open) and the closing print (for a buy
    decided at 15:55) may change the fill, never the number of shares, also not of the order placed next."""
    caps = Caps(max_exposure_pct=20.0)  # the second order gets what the first leaves of the 20%
    for hook, field in (("opens", "o"), ("closes", "c")):
        qty = []
        for print_px in (100.0, 110.0):
            b = flat(30, symbols=("AAA", "BBB"), rng=0.64)
            getattr(b, field)[20, 0] = print_px  # AAA fills 10% higher; everything known before the fill is equal
            eng = run(b, Script(**{hook: [(20, 0, {}), (20, 1, {})]}, caps=caps), i1=20)
            qty.append((eng.pos[0].qty, eng.pos[1].qty))
            assert eng.pos[0].fill > print_px  # the fill does follow the print
        assert qty[0] == qty[1] == (130, 70)


def test_entry_at_the_close_is_sold_at_the_next_open():
    b = flat(30)
    set_day(b, 21, 0, 101.0, 101.5, 100.5, 101.0)
    (t,) = run(b, Script(closes=[(20, 0, {"exit_next_open": True})])).book.trades
    assert t.entry_at == "close" and t.reason == "open" and t.hold_days == 1
    assert math.isclose(t.gross_pnl, 1.0 * t.qty) and t.entry > 100.0 and t.exit < 101.0 and split_ok(t)


def test_a_symbol_without_a_bar_is_not_bought_and_a_held_one_waits():
    b = flat(30, symbols=("AAA", "BBB"))
    for k in ("o", "h", "l", "c", "c1555"):
        getattr(b, k)[20, 1] = np.nan  # BBB has no bar on the entry day
        getattr(b, k)[22, 0] = np.nan  # AAA is halted on the day its holding period ends
    eng = run(b, Script(opens=[(20, 0, {"max_hold": 2}), (20, 1, {})]))
    (t,) = eng.book.trades
    assert t.symbol == "AAA" and t.reason == "max_hold" and str(t.exit_date) == str(b.dates[23])


# ---------------------------------------------------------------- 3. earnings blackout


def test_results_headlines_are_told_from_previews_and_analyst_notes():
    yes = ["Apple Q4 EPS $1.64 Beats $1.60 Estimate, Sales $94.93B Beat $94.58B Estimate",
           "Apple Reports Q4 EPS $2.91 vs $2.78 Est., Sales $62.9B vs $61.57B Est.",
           "Microsoft Q1 Adj. EPS $2.99 Beats $2.65 Estimate, Sales $56.5B Beat $54.5B Estimate",
           "Moderna Q2 EPS $(3.62) Misses $(4.04) Estimate, Sales $344M Beat $319.65M Estimate",
           "Realty Income Q2 AFFO $1.06 Beats $1.05 Estimate", "Netflix Q2 Sales $7.97B Miss $8.04B Estimate",
           "Tesla Reports Third Quarter 2023 Results",
           "Microsoft Shares Are Trading Higher After The Company Reported Better-Than-Expected Q2 Financial Results."]
    no = ["Apple Sees Q1 Sales $117B-$121B vs $117.9B Est.", "Earnings Scheduled For October 28, 2021",
          "Apple Q4 Earnings Preview: What To Expect", "Apple To Report Q4 Earnings On Thursday",
          "Morgan Stanley Maintains Overweight on Apple, Raises Price Target to $200",
          "Analyst Cuts Apple Q1 EPS Estimate To $2.05", "Apple Raises Q2 EPS Guidance To $1.50-$1.60",
          "Apple Expects Q1 Revenue Of $120B", "Apple Preliminary Q4 Sales $90B",
          "Apple Q4 2023 Earnings Call Transcript",
          "Why Nvidia Shares Are Rising After AMD's Q3 Results Beat Estimates", "Apple Unveils New iPhone", ""]
    assert [h for h in yes if not earnings.is_results_headline(h)] == []
    assert [h for h in no if earnings.is_results_headline(h)] == []
    assert earnings.is_results_headline(yes[0], n_symbols=2)  # two share classes
    assert not earnings.is_results_headline(yes[0], n_symbols=5)  # a round-up of many tickers


def test_a_report_after_1555_belongs_to_the_next_session():
    dates = sessions(260, "2024-01-02")
    h = "AAA Q1 EPS $1.00 Beats $0.90 Estimate"
    news = pd.DataFrame({
        "created_at": pd.to_datetime(["2024-02-01 12:00", "2024-02-01 21:10", "2024-02-02 13:00",  # one report
                                      "2024-05-02 20:05", "2024-08-03 15:00"], utc=True),  # after the close; Saturday
        "headline": [h, h, h, h, h], "symbols": ["AAA"] * 5})
    ev = earnings.event_sessions(news, dates)
    assert [str(dates[i]) for i in ev] == ["2024-02-01", "2024-05-03", "2024-08-05"]
    assert len(earnings.event_sessions(news.assign(headline="AAA Q1 Earnings Preview"), dates)) == 0


def test_blackout_window_sits_around_last_years_date_and_ends_when_the_report_is_seen():
    dates = sessions(600, "2023-01-02")
    day = dates.astype(np.int64)
    idx = [int(np.searchsorted(dates, np.datetime64(d))) for d in ("2023-02-01", "2023-05-03", "2023-08-02",
                                                                 "2023-11-01", "2024-01-31", "2024-05-08")]
    hold, entry = earnings.blackout(dates, idx)
    assert not hold[:idx[0]].any() and not entry[:idx[0]].any()  # nothing known before the first report
    e = idx[4]  # 2024-01-31; expected 2023-02-01 + 364 days = 2024-01-31
    assert hold[e - 1] and not hold[e] and hold[e - 6]  # flat the night before, free again once it is out
    first = int(np.flatnonzero(hold[idx[3] + 1:e])[0]) + idx[3] + 1
    assert day[first + 1] >= day[e] - 10 > day[first]  # the first flat night is 10 days before the expected date
    assert entry[first - 3] and not hold[first - 3] and not entry[first - 8]  # no new entries a week earlier
    assert hold[idx[5] - 1]  # reported 5 days later than last year's date: still inside the window
    no_news_after = earnings.blackout(dates, idx[:2])[0]
    assert no_news_after[idx[1] + 55:idx[1] + 75].any() and not no_news_after[idx[1] + 85:idx[1] + 100].any()


@settings(max_examples=25, deadline=None)
@given(st.lists(st.integers(5, 590), min_size=1, max_size=12, unique=True), st.integers(10, 580))
def test_blackout_row_t_only_uses_reports_seen_by_t(events, t):
    dates = sessions(600, "2023-01-02")
    events = sorted(events)
    full = earnings.blackout(dates, events)
    upto = earnings.blackout(dates, [e for e in events if e <= t])
    assert np.array_equal(full[0][:t + 1], upto[0][:t + 1]) and np.array_equal(full[1][:t + 1], upto[1][:t + 1])


def test_engine_skips_entries_and_sells_before_a_blackout():
    b = flat(40, symbols=("AAA", "BBB"))
    hold = np.zeros(b.c.shape, dtype=bool)
    entry = np.zeros_like(hold)
    hold[24:28, 0] = True  # AAA must be flat after the close of session 24
    entry[19:28, 1] = True  # BBB: no new entry on what is known at the close of 19
    eng = run(b, Script(opens=[(20, 0, {"max_hold": 30}), (20, 1, {}), (29, 1, {})]), i1=30, hold_blk=hold,
              entry_blk=entry)
    (t,) = [t for t in eng.book.trades if t.symbol == "AAA"]
    assert t.reason == "earnings" and str(t.exit_date) == str(b.dates[24]) and split_ok(t)
    assert len(eng.book.trades) == 1 and eng.pos[1].entry_i == 29  # BBB: only the entry after the window


def test_coverage_decides_whether_the_filter_is_reliable():
    dates = sessions(900, "2021-01-04")
    has = np.ones((900, 3), dtype=bool)
    regular = [int(np.searchsorted(dates, np.datetime64("2021-02-03") + np.timedelta64(91 * k, "D")))
               for k in range(14)]
    ev = {"AAA": regular, "BBB": regular}
    hold, _ = earnings.calendar(dates, ["AAA", "BBB", "SPY"], ev)
    cov = earnings.coverage(dates, ["AAA", "BBB", "SPY"], ev, hold, has, ["AAA", "BBB"])
    assert cov["reliable"] and cov["flat_before_report_share"] == 1.0 and cov["stocks_plausible_share"] == 1.0
    assert 0.05 < cov["blackout_share_of_sessions"] < 0.2 and 3.7 < cov["median_reports_per_year"] < 4.1
    assert not hold[:, 2].any()
    sparse = {"AAA": regular, "BBB": regular[:2]}  # one stock's reports stop being detected
    hold2, _ = earnings.calendar(dates, ["AAA", "BBB", "SPY"], sparse)
    cov2 = earnings.coverage(dates, ["AAA", "BBB", "SPY"], sparse, hold2, has, ["AAA", "BBB"])
    assert not cov2["reliable"] and cov2["stocks_plausible_share"] == 0.5


# ---------------------------------------------------------------- 4. the four rules


def market(seed: int = 5, n: int = 520, n_stocks: int = 8, first: str = "2022-01-03") -> tuple[DailyBars, list[str]]:
    """Random walks with different drifts (so there are trends, new highs and rankings), SPY last."""
    rng = np.random.default_rng(seed)
    stocks = [f"S{k}" for k in range(n_stocks)]
    drift = np.r_[np.linspace(-0.0006, 0.0018, n_stocks), 0.0004]
    r = rng.normal(drift, 0.014, (n, n_stocks + 1))
    r[:, -1] = rng.normal(0.0004, 0.008, n)
    c = np.r_[np.linspace(40, 180, n_stocks), 400.0] * np.exp(np.cumsum(r, axis=0))
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.004, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, c.shape)))
    c1555 = c * np.exp(rng.normal(0, 0.0005, c.shape))
    return DailyBars.make(sessions(n, first), [*stocks, "SPY"], o, h, lo, c, np.full(c.shape, 8e6), c1555), stocks


def intraday_frame(b: DailyBars, stocks: list[str], seed: int = 3, n: int = 150) -> pd.DataFrame:
    """Made-up variant A trades: bought at 99.5% of the 15:55 price or above it, mostly sold at 15:55."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        i, s = int(rng.integers(230, len(b.dates))), stocks[int(rng.integers(len(stocks)))]
        px = float(b.c1555[i, b.col(s)])
        up = bool(rng.random() < 0.6)
        entry = px * (0.985 if up else 1.006)
        qty, risk_ps = 100.0, px * 0.004
        reason = "time" if rng.random() < 0.8 else "stop"
        exit_px = px - 0.02 if reason == "time" else entry - risk_ps
        gross = (exit_px + 0.02 - entry + 0.03) * qty
        rows.append(Trade(s, b.dates[i], 60, 385, qty, entry, exit_px, entry - risk_ps, entry + 2 * risk_ps, reason,
                          (exit_px - entry) * qty - 0.2, risk_ps * qty, gross, 3.0, 2.0, 0.0, 0.2, 18.0))
    df = pd.DataFrame([dataclasses.asdict(t) for t in rows]).drop_duplicates(["symbol", "date"])
    df["gross_pnl"] = df["pnl"] + df[list(COSTS)].sum(axis=1)  # keep the split exact
    return df.sort_values("date", ignore_index=True)


@pytest.fixture(scope="module")
def mkt():
    b, stocks = market()
    return b, stocks, indicators(b)


def rule_of(name: str, b: DailyBars, stocks: list[str], ind=None):
    vix = np.where(np.arange(len(b.dates)) % 7 == 0, 31.0, 18.0)
    return make_rule(name, b, ind or indicators(b), stocks, "SPY", vix, intraday_frame(b, stocks))


def account(name: str, b: DailyBars, stocks: list[str], cfg=None, i1=None, close=True, **kw) -> Engine:
    cfg = cfg or EXPERIMENTS[name].default
    eng = Engine(b, indicators(b), POL, rule_of(name, b, stocks), lambda i: cfg, **kw)
    eng.run(210, len(b.dates) - 1 if i1 is None else i1, close_at_end=close)
    return eng


def test_grids_are_small_declared_and_contain_the_fixed_setting():
    assert list(EXPERIMENTS) == ["trend_highs", "rel_strength", "winners_run", "overnight_spy"]
    sizes = {n: len(e.grid) for n, e in EXPERIMENTS.items()}
    assert sizes == {"trend_highs": 9, "rel_strength": 6, "winners_run": 3, "overnight_spy": 3}
    for e in EXPERIMENTS.values():
        assert e.grid[0] == e.default and len(set(e.grid)) == len(e.grid) <= 9
    assert EXPERIMENTS["trend_highs"].default.label == "trend_highs_high_n55_trail_atr3"
    assert EXPERIMENTS["overnight_spy"].axes["vix_max"] == (0, 25, 30)  # 0 = no VIX filter


def test_trend_highs_buys_new_highs_above_the_average_and_trails(mkt):
    b, stocks, ind = mkt
    eng = account("trend_highs", b, stocks)
    trades = eng.book.trades
    assert len(trades) > 10 and {t.reason for t in trades} <= {"stop", "trail", "max_hold", "end_of_data"}
    assert max(t.hold_days for t in trades) <= 40 and all(t.symbol != "SPY" for t in trades)
    c = pd.DataFrame(b.c)
    for t in trades[:15]:
        j, k = b.col(t.symbol), int(np.searchsorted(b.dates, t.date)) - 1  # the signal day
        assert b.c[k, j] > c[j].iloc[k - 55:k].max() and b.c[k, j] > c[j].iloc[k - 199:k + 1].mean()
        assert b.c[k, j] / b.c[k - 20, j] > b.c[k, -1] / b.c[k - 20, -1]  # stronger than SPY over 20 days
        assert math.isclose(t.entry, b.o[k + 1, j], rel_tol=2e-3) and t.entry > b.o[k + 1, j]  # next open + costs
    assert max(r["positions"] for r in eng.book.daily) <= 5 and max(r["exposure"] for r in eng.book.daily) < 0.75


def test_rel_strength_holds_the_top_names_weekly_and_goes_flat_under_the_average(mkt):
    b, stocks, ind = mkt
    rule = RelStrength(b, ind, b.cols(stocks), b.col("SPY"))
    eng = account("rel_strength", b, stocks)
    week = rule.week
    entries = {int(np.searchsorted(b.dates, t.date)) for t in eng.book.trades}
    assert len(eng.book.trades) > 10 and all(week[i] != week[i - 1] for i in entries)  # only a week's first session
    i = sorted(entries)[len(entries) // 2]
    mom = b.c[i - 1 - 21] / b.c[i - 1 - 126] - 1
    top5 = {stocks[j] for j in np.argsort(-mom[:len(stocks)])[:5]}
    assert {t.symbol for t in eng.book.trades if int(np.searchsorted(b.dates, t.date)) == i} <= top5
    assert {t.reason for t in eng.book.trades} <= {"rebalance", "market_filter", "max_hold", "end_of_data"}
    bear, _ = market()
    bear.c[400:, -1] *= 0.5  # SPY far under its 200-day average from session 400 on
    eng2 = account("rel_strength", bear, stocks)
    assert any(t.reason == "market_filter" for t in eng2.book.trades)
    after = [r["positions"] for r in eng2.book.daily if r["date"] >= bear.dates[406]]
    assert max(after) == 0  # flat from the next weekly rebalance
    cfg10 = next(c for c in EXPERIMENTS["rel_strength"].grid if c.get("top_n") == 10 and c.get("months") == 6)
    ten = account("rel_strength", b, stocks, cfg=cfg10)
    assert ten.book.trades[0].config.endswith("top_n10") and max(r["positions"] for r in ten.book.daily) > 5
    assert max(r["exposure"] for r in ten.book.daily) < 0.75


def test_overnight_spy_holds_one_night_and_obeys_the_vix_threshold(mkt):
    b, stocks, ind = mkt
    eng = account("overnight_spy", b, stocks)
    trades = eng.book.trades
    vix = np.where(np.arange(len(b.dates)) % 7 == 0, 31.0, 18.0)
    assert len(trades) > 200 and all(t.symbol == "SPY" and t.hold_days == 1 and t.reason == "open" for t in trades)
    assert all(vix[int(np.searchsorted(b.dates, t.date))] < 30 for t in trades)
    t = trades[10]
    i = int(np.searchsorted(b.dates, t.date))
    assert math.isclose(t.gross_pnl, (b.o[i + 1, -1] - b.c[i, -1]) * t.qty) and split_ok(t)
    assert t.qty == math.floor(min(0.005 * t.equity / (3 * ind.atr[i - 1, -1]) * b.c1555[i, -1], 0.15 * t.equity)
                               / b.c1555[i, -1])  # the gap rule or the 15% cap, sized on the 15:55 price
    cfg0 = next(c for c in EXPERIMENTS["overnight_spy"].grid if c.get("vix_max") == 0)
    no_filter = account("overnight_spy", b, stocks, cfg=cfg0)
    assert no_filter.book.trades[0].config == "overnight_spy_vix_max0" and len(no_filter.book.trades) > len(trades)
    assert sum(t.cost_spread + t.cost_impact + t.cost_fees for t in trades) > 0


def intraday_base(it: pd.DataFrame, equity0: float = 100_000.0) -> pd.Series:
    """The intraday replay's own equity at the start of each session: flat at each month start plus the P&L of
    the month's earlier sessions (written out here the slow way, independently of the rule's code)."""
    day = it.groupby("date")["pnl"].sum().sort_index()
    return pd.Series({d: equity0 + sum(v for e, v in day.items() if e < d and (e.year, e.month) == (d.year, d.month))
                      for d in day.index})


def test_winners_run_keeps_only_winners_and_only_what_the_gap_sizing_allows(mkt):
    b, stocks, ind = mkt
    it = intraday_frame(b, stocks)
    base = intraday_base(it)
    eng = account("winners_run", b, stocks)
    carried = [t for t in eng.book.trades if t.hold_days > 0 or t.reason in ("trail", "max_hold", "end_of_data")]
    same_day = [t for t in eng.book.trades if t not in carried]
    assert len(carried) > 10 and max(t.hold_days for t in carried) <= 5
    assert {t.reason for t in carried} <= {"stop", "trail", "max_hold", "end_of_data"}
    src = it.set_index(["symbol", "date"])
    for t in carried:
        row = src.loc[(t.symbol, pd.Timestamp(t.date))]
        i, j = int(np.searchsorted(b.dates, t.date)), b.col(t.symbol)
        assert row["reason"] == "time" and (b.c1555[i, j] - row["entry"]) * row["qty"] >= 0.5 * row["risk"]
        assert t.entry == row["entry"]  # the kept shares keep the intraday fill
        assert t.qty * 3 * ind.atr[i - 1, j] <= 0.005 * t.equity + 1e-6  # a 3 ATR gap costs at most the trade risk
    losers = it[(it["reason"] != "time") & (it["date"] >= pd.Timestamp(b.dates[210]))]
    assert len(losers) > 5
    for r in losers.itertuples():  # sold the same day, at the size of this account: same return as in the replay
        (t,) = [t for t in same_day if t.symbol == r.symbol and pd.Timestamp(t.date) == r.date]
        assert t.hold_days == 0 and math.isclose(t.pnl / t.equity, r.pnl / base[r.date], rel_tol=1e-9)
    assert max(r["positions"] for r in eng.book.daily) <= 3 and max(r["exposure"] for r in eng.book.daily) < 0.30
    for sym, d in {(t.symbol, t.date) for t in eng.book.trades}:  # sold part + kept part = the scaled quantity
        parts = [t for t in eng.book.trades if t.symbol == sym and t.date == d]
        want = src.loc[(sym, pd.Timestamp(d))]["qty"] * parts[0].equity / base[pd.Timestamp(d)]
        assert math.isclose(sum(t.qty for t in parts), want) and len(parts) <= 2
    assert any(len([t for t in eng.book.trades if (t.symbol, t.date) == k]) == 2
               for k in {(t.symbol, t.date) for t in carried})  # a partly kept trade is two rows


def test_winners_run_without_a_carry_has_exactly_the_intraday_returns(mkt, monkeypatch):
    b, stocks, ind = mkt
    it = intraday_frame(b, stocks)
    monkeypatch.setattr(WinnersRun, "min_profit_r", math.inf)  # nothing qualifies: every trade is sold at 15:55
    eng = account("winners_run", b, stocks)
    it = it[it["date"] >= pd.Timestamp(b.dates[210])]
    assert len(eng.book.trades) == len(it) and all(t.hold_days == 0 for t in eng.book.trades)
    want = (it.groupby("date")["pnl"].sum() / intraday_base(intraday_frame(b, stocks))).dropna()
    got = pd.Series({pd.Timestamp(r["date"]): r["pnl"] / r["start"] for r in eng.book.daily})
    assert np.allclose(got.reindex(want.index), want, rtol=1e-9, atol=1e-12)  # each day: the replay's own return
    assert (got.drop(want.index) == 0).all() and abs(eng.equity - 100_000.0) > 1_000  # and it compounds
    assert math.isclose(eng.equity, 100_000.0 * float(np.prod(1 + want)), rel_tol=1e-9)


@pytest.mark.parametrize("name", list(EXPERIMENTS))
def test_cost_split_sums_to_net_and_trades_explain_the_equity(mkt, name):
    b, stocks, ind = mkt
    eng = account(name, b, stocks)
    assert eng.book.trades and all(split_ok(t) for t in eng.book.trades)
    assert math.isclose(sum(t.pnl for t in eng.book.trades), eng.equity - 100_000.0, abs_tol=1e-4)
    assert all(t.cost_slippage >= 0 and t.cost_spread > 0 and t.cost_fees > 0 for t in eng.book.trades)
    assert all((t.cost_slippage > 0) == t.gap for t in eng.book.trades)
    assert min(r["exposure"] for r in eng.book.daily) >= 0 and not eng.pos
    daily = pd.DataFrame(eng.book.daily)
    assert np.allclose(daily["start"].to_numpy()[1:], daily["end"].to_numpy()[:-1])


# ---------------------------------------------------------------- 5. no look-ahead


class Recorder(Engine):
    """An engine that writes down every decision: each attempted buy (quantity 0 = refused) and each sale at the
    open, with the session, so two runs can be compared decision by decision."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.log: list[tuple] = []

    def enter(self, i, j, eq, config, caps, *, at="open", **kw):
        q = super().enter(i, j, eq, config, caps, at=at, **kw)
        self.log.append((i, at, "buy", j, q, round(self.pos[j].fill, 9) if q and at == "open" else None))
        return q

    def sell_open(self, i, j, reason):
        ok = super().sell_open(i, j, reason)
        self.log.append((i, "open", "sell", j, reason, ok))
        return ok


BLK = np.random.default_rng(1).random((520, 9)) < 0.04  # an arbitrary earnings blackout, the same in every run


TIGHT = dataclasses.replace(POL, max_position_pct=4.0)  # the position cap binds: size depends on the price too


def decisions(name: str, b: DailyBars, stocks: list[str], t: int, pol=POL) -> dict:
    """The account run up to and including session t, split into what was decided when."""
    cfg = EXPERIMENTS[name].default
    eng = Recorder(b, indicators(b), pol, rule_of(name, b, stocks), lambda i: cfg, hold_blk=BLK, entry_blk=BLK)
    eng.run(210, t - 1, close_at_end=False)
    held = sorted((j, p.qty, None if math.isnan(p.stop) else round(p.stop, 9)) for j, p in eng.pos.items())
    eng.step(t)
    day = b.dates[t]
    done = [(x.symbol, x.reason, x.qty, str(x.exit_date), round(x.exit, 9)) for x in eng.book.trades]
    return {
        "before": [x for x in done if x[3] < str(day)] + [r["end"] for r in eng.book.daily[:-1]],
        "held": held,  # what is held into session t, with the stops resting at its open
        "open": [e for e in eng.log if e[0] == t and e[1] == "open"],
        "stops": [x for x in done if x[3] == str(day) and x[1] in ("stop", "trail")],
        "close": ([e[:5] for e in eng.log if e[0] == t and e[1] == "close"]  # which, and how many shares
                  + [x[:3] for x in done if x[3] == str(day) and x[1] in ("max_hold", "earnings")]),
        "log": eng.log,
    }


def shifted_market(t: int, seed: int, known: tuple[str, ...]) -> DailyBars:
    """The same market, except that everything not yet known is different: all later sessions, and of session t
    every field that is not in `known` (prices x random factors of about 5%, volumes x 3)."""
    rng = np.random.default_rng(seed)
    m, _ = market()
    f = np.exp(rng.normal(0, 0.05, (len(m.dates) - t, len(m.symbols))))
    for k in ("o", "h", "l", "c", "c1555"):
        getattr(m, k)[t + (k in known):] *= f[(k in known):]
    for k in ("v", "v_first", "v_last"):
        getattr(m, k)[t + (k in known):] *= 3.0
    return m


AT_OPEN = ("o", "v_first")  # what exists when session t opens: its open print (sessions < t are untouched)
AT_1555 = ("o", "v_first", "h", "l", "v", "c1555")  # ... and at 15:55: everything but the closing print


@pytest.fixture(scope="module")
def decision_days(mkt):
    """Per rule: the sessions on which the fixed setting decides something at the open / at the close."""
    b, stocks, _ = mkt
    out = {}
    for name in EXPERIMENTS:
        full = decisions(name, b, stocks, len(b.dates) - 2)
        out[name] = (sorted({e[0] for e in full["log"] if e[1] == "open"} - {len(b.dates) - 2}),
                     sorted({e[0] for e in full["log"] if e[1] == "close"} - {len(b.dates) - 2}))
    assert all(len(out[n][0]) > 20 for n in ("trend_highs", "rel_strength", "overnight_spy"))
    assert all(len(out[n][1]) > 20 for n in ("winners_run", "overnight_spy"))
    return out


@pytest.mark.parametrize("name", list(EXPERIMENTS))
@settings(max_examples=30, deadline=None)
@given(data=st.data(), seed=st.integers(0, 10_000), tight=st.booleans())
def test_future_bars_do_not_change_todays_decisions(mkt, decision_days, name, data, seed, tight):
    """Property: replace every price and volume that is not yet known when a decision is made, and the decision
    (which stocks, how many shares, the fill at the open, the stops resting at the open) is the same.
    - at the open of t nothing of session t but its open print exists;
    - at 15:55 of t the closing print does not exist, nor any later session."""
    b, stocks, _ = mkt
    pol = TIGHT if tight else POL
    opens, closes = decision_days[name]
    t = data.draw(st.sampled_from(opens or closes))
    want = decisions(name, b, stocks, t, pol)
    got = decisions(name, shifted_market(t, seed, AT_OPEN), stocks, t, pol)
    assert all(got[k] == want[k] for k in ("before", "held", "open"))
    t = data.draw(st.sampled_from(closes or opens))
    want = decisions(name, b, stocks, t, pol)
    got = decisions(name, shifted_market(t, seed, AT_1555), stocks, t, pol)
    assert all(got[k] == want[k] for k in ("before", "held", "open", "stops", "close"))


def test_overnight_rule_reads_only_vix_values_already_published(mkt):
    b, stocks, ind = mkt
    cfg = EXPERIMENTS["overnight_spy"].default
    vix = np.where(np.arange(len(b.dates)) % 7 == 0, 31.0, 18.0)  # row i = the close of the day before session i
    later = vix.copy()
    later[353:] = 40.0  # from session 353 on the rule would stand aside
    logs = []
    for v in (vix, later):
        eng = Recorder(b, ind, POL, OvernightSpy(b, b.col("SPY"), v), lambda i: cfg)
        eng.run(210, 352, close_at_end=False)
        logs.append(eng.log)
    assert logs[0] == logs[1] and logs[0][-1][:3] == (352, "close", "buy")


def test_the_shift_really_changes_what_comes_later(mkt):
    b, stocks, _ = mkt
    m = shifted_market(300, 1, AT_OPEN)
    assert np.array_equal(m.c[:300], b.c[:300]) and np.array_equal(m.o[:301], b.o[:301])
    assert not np.isclose(m.c[300:], b.c[300:]).any() and not np.isclose(m.o[301:], b.o[301:]).any()
    m = shifted_market(300, 1, AT_1555)
    assert np.array_equal(m.c1555[:301], b.c1555[:301]) and not np.isclose(m.c[300:], b.c[300:]).any()
    full, other = decisions("trend_highs", b, stocks, 400), decisions("trend_highs", m, stocks, 400)
    assert full["log"] != other["log"]  # a different future does lead to different later decisions


def test_one_missing_session_does_not_blank_the_long_averages(mkt):
    b, stocks, ind = mkt
    hole, _ = market()
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(hole, k)[300, 3] = np.nan  # S3 has no bar on session 300
    ind2 = indicators(hole)
    assert np.isfinite(ind2.atr[300:330, 3]).all() and np.isfinite(ind2.adv[300:330, 3]).all()
    assert ind2.ret[300, 3] == 0.0 and np.isclose(ind2.ret[301, 3], b.c[301, 3] / b.c[299, 3] - 1)
    r1, r2 = TrendHighs(b, ind, b.cols(stocks), 8), TrendHighs(hole, ind2, hole.cols(stocks), 8)
    assert not r2.signal[55][300, 3]  # nothing on the day without a bar
    assert np.array_equal(r1.signal[55][320:, 3], r2.signal[55][320:, 3]) and r1.signal[55][320:, 3].any()
    m1, m2 = RelStrength(b, ind, b.cols(stocks), 8), RelStrength(hole, ind2, hole.cols(stocks), 8)
    assert np.isfinite(m2.mom[6][300:, 3]).all() and not m2.ok[300, 3] and m2.ok[301, 3]
    assert np.array_equal(m1.mom[6][:300], m2.mom[6][:300], equal_nan=True)


def test_indicators_at_t_use_sessions_up_to_t(mkt):
    b, stocks, ind = mkt
    cut, _ = market()
    for k in ("o", "h", "l", "c", "v"):
        getattr(cut, k)[301:] *= 1.7
    ind2 = indicators(cut)
    for k in ("atr", "sigma", "adv", "ret"):
        assert np.array_equal(getattr(ind, k)[:301], getattr(ind2, k)[:301], equal_nan=True)
    assert not np.array_equal(ind.atr[301:], ind2.atr[301:], equal_nan=True)
    r1, r2 = TrendHighs(b, ind, b.cols(stocks), 8), TrendHighs(cut, ind2, cut.cols(stocks), 8)
    assert all(np.array_equal(r1.signal[n][:301], r2.signal[n][:301]) for n in r1.signal)


# ---------------------------------------------------------------- 6. walk-forward


@pytest.fixture(scope="module")
def wf(mkt):
    b, stocks, ind = mkt
    folds = make_folds(first_train_month("2022-08-01", 90), b.dates[-1], train_months=6)
    rule = rule_of("trend_highs", b, stocks, ind)
    return walk_forward(b, ind, POL, rule, EXPERIMENTS["trend_highs"], folds, min_train_trades=3), folds


def test_walk_forward_carries_one_account_across_the_folds(mkt, wf):
    b, stocks, ind = mkt
    res, folds = wf
    assert len(res.folds) == len(folds) == 8 and len(res.grid) == 9
    assert str(res.tuned.daily[0]["date"]) >= str(folds[0].test_start)  # flat until the first test month
    daily = pd.DataFrame(res.tuned.daily)
    assert np.allclose(daily["start"].to_numpy()[1:], daily["end"].to_numpy()[:-1])  # equity is never reset
    assert any(np.datetime64(t.date, "M") != np.datetime64(t.exit_date, "M") for t in res.tuned.trades)
    assert all(split_ok(t) for t in res.tuned.trades)
    for t in res.tuned.trades:  # a trade carries the configuration chosen for the month it was opened in
        assert t.config == res.schedule[np.datetime64(t.date, "M")].label
    assert {t.config for t in res.fixed.trades} == {EXPERIMENTS["trend_highs"].default.label}
    dates, mat = res.oos_matrix()
    assert mat.shape == (len(daily), 9) and np.array_equal(dates, daily["date"].to_numpy().astype("datetime64[D]"))


def test_the_choice_for_a_month_cannot_see_that_month(mkt, wf):
    b, stocks, ind = mkt
    res, folds = wf
    k = len(folds) - 2
    cut, _ = market()
    i = cut.index_from(folds[k].test_start)
    for f in ("o", "h", "l", "c", "c1555"):
        getattr(cut, f)[i:] *= np.linspace(1.0, 0.4, len(cut.dates) - i)[:, None]  # the test months crash
    ind2 = indicators(cut)
    res2 = walk_forward(cut, ind2, POL, rule_of("trend_highs", cut, stocks, ind2), EXPERIMENTS["trend_highs"], folds,
                        min_train_trades=3)
    for (_, a), (_, c) in list(zip(res.folds, res2.folds, strict=True))[:k + 1]:
        assert a == c  # same choice, train Sharpe and train trade count up to and including fold k
    assert book_digest(res.tuned) != book_digest(res2.tuned)
    before = [t for t in res.tuned.trades if t.exit_date < folds[k].test_start]
    before2 = [t for t in res2.tuned.trades if t.exit_date < folds[k].test_start]
    assert before == before2 and len(before) > 0


def test_walk_forward_survives_a_last_month_without_bars_and_says_when_there_is_nothing_to_test(mkt):
    b, stocks, ind = mkt
    rule, exp = rule_of("overnight_spy", b, stocks, ind), EXPERIMENTS["overnight_spy"]
    folds = make_folds(np.datetime64("2023-01"), np.datetime64("2024-01-31"), train_months=6)  # bars end 2023-12
    res = walk_forward(b, ind, POL, rule, exp, folds, min_train_trades=3)
    dates, mat = res.oos_matrix()
    assert str(folds[-1].test_month) == "2024-01" and len(dates) == len(res.tuned.daily) and mat.shape[1] == 3
    with pytest.raises(ValueError, match="no session in the test months"):
        walk_forward(b, ind, POL, rule, exp, make_folds(np.datetime64("2023-08"), np.datetime64("2024-03-31"),
                                                        train_months=6), min_train_trades=3)


def test_walk_forward_is_deterministic(mkt, wf):
    b, stocks, ind = mkt
    res, folds = wf
    again = walk_forward(b, ind, POL, rule_of("trend_highs", b, stocks, ind), EXPERIMENTS["trend_highs"], folds,
                         min_train_trades=3)
    assert book_digest(again.tuned) == book_digest(res.tuned) and book_digest(again.fixed) == book_digest(res.fixed)


# ---------------------------------------------------------------- 7. benchmarks


def test_benchmarks_spy_and_the_equal_weight_universe():
    n = 80
    c = np.column_stack([100 * 1.001 ** np.arange(n), 50 * 1.003 ** np.arange(n), 400 * 1.002 ** np.arange(n)])
    b = DailyBars.make(sessions(n), ["AAA", "BBB", "SPY"], np.vstack([c[:1], c[:-1]]), c * 1.001, c * 0.999, c)
    spy = spy_buy_and_hold(b, "SPY", 30, 79)
    assert len(spy) == 50 and np.allclose(spy, 0.002)
    ew = equal_weight_benchmark(b, indicators(b), b.cols(["AAA", "BBB"]), 30, 79)
    total = float(np.prod(1 + ew) - 1)
    lo_bound, hi_bound = 1.001 ** 50 - 1, 1.003 ** 50 - 1
    assert len(ew) == 50 and lo_bound < total < hi_bound  # between the two stocks, a little under their average
    assert total < (lo_bound + hi_bound) / 2 + 0.002
    still = flat(60, symbols=("AAA", "BBB", "SPY"))
    cost_only = equal_weight_benchmark(still, indicators(still), np.array([0, 1]), 20, 59)
    assert -0.002 < float(np.prod(1 + cost_only) - 1) < 0  # flat prices: only the cost of buying and rebalancing
    late = flat(60, symbols=("AAA", "NEW", "SPY"))
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(late, k)[:40, 1] = np.nan  # listed later: joins at the next month's rebalance, no error before
    assert np.isfinite(equal_weight_benchmark(late, indicators(late), np.array([0, 1]), 20, 59)).all()


# ---------------------------------------------------------------- 8. variant A is untouched


def test_variant_a_trades_are_the_unchanged_intraday_replay():
    from test_walkforward import FIRST, LAST, _regime, _slice
    from test_walkforward import market as minute_market

    from kaibot.replay.events import events_digest
    from kaibot.replay.pipeline import prepare
    from kaibot.replay.sim import Book, run_portfolio
    from kaibot.replay.walkforward import Window, month_bounds

    stocks, etfs, sector_of, spy = minute_market(11, FIRST, LAST)

    def load(lo, hi):
        return Window({s: _slice(sd, lo, hi) for s, sd in stocks.items()},
                      {e: _slice(sd, lo, hi) for e, sd in etfs.items()}, _slice(spy, lo, hi))

    months = [np.datetime64("2024-07"), np.datetime64("2024-08")]
    got = variant_a_trades(load, sector_of, {}, _regime, set(), POL, months, block_months=2)
    want: list = []
    for m in months:  # a plain replay of each month, as scripts/run_replay.py would do it
        lo, hi = month_bounds(m)
        prep = prepare(stocks, etfs, sector_of, {}, lo, hi)
        reg = {d: v for d, v in _regime(spy).items() if lo <= d <= hi}
        want += run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant="A").trades
    assert len(got) == len(want) > 20
    again = Book(0.0, [Trade(**r) for r in got.to_dict("records")])
    assert events_digest(again) == events_digest(Book(0.0, want))
    assert np.allclose(got["pnl"], [t.pnl for t in want]) and list(got.columns)[:3] == ["symbol", "date", "entry_m"]
    # The intraday replay itself still gives the numbers taken on main before the cost split (d73927c), on the
    # same fixed sample as test_experiments.test_net_results_unchanged_for_a_fixed_sample.
    lo, hi = np.datetime64("2024-06-01"), np.datetime64("2024-08-31")
    prep = prepare(stocks, etfs, sector_of, {}, lo, hi)
    reg = {d: v for d, v in _regime(spy).items() if lo <= d <= hi}
    for variant, n, net in (("A", 133, 28689.623973493788), ("v1", 133, 20888.47733879249)):
        book = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant=variant)
        assert len(book.trades) == n and sum(t.pnl for t in book.trades) == pytest.approx(net, rel=1e-12)


def test_nothing_the_engine_runs_knows_about_the_multiday_research():
    """Research only: no module outside the three new ones imports them, so the engine, the risk gate, the live
    strategies and the intraday replay cannot be affected; and the new code charges costs with the replay's own
    functions instead of copies of them."""
    for f in (ROOT / "src" / "kaibot").rglob("*.py"):
        if f.name not in ("multiday.py", "multiday_rules.py", "multiday_core.py", "earnings.py"):
            text = f.read_text()
            assert "multiday" not in text and "replay.earnings" not in text, f
    from kaibot.broker import shadow_costs
    from kaibot.replay import multiday, sim

    assert multiday.buy_px is sim.buy_px and multiday.sell_px is sim.sell_px
    assert multiday.cost_parts is sim.cost_parts and multiday.half_spread is sim.half_spread
    assert multiday.sell_fees is shadow_costs.sell_fees


# ---------------------------------------------------------------- 9. the stakeholder's command


@pytest.fixture(scope="module")
def runs(tmp_path_factory, mkt):
    """`walk_forward_multiday.py` for three rules on the synthetic market (daily bars and variant A's trades are
    handed in instead of being built from minute files), as run_experiments.sh multiday would run them."""
    b, stocks, _ = mkt
    data = tmp_path_factory.mktemp("multiday")
    uni = data / "universe_test.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": stocks[:4], "XLF": stocks[4:]}, "market": "SPY"}))
    (data / "inputs").mkdir()
    days = pd.date_range("2021-12-01", "2024-03-01", freq="D")
    (data / "inputs" / "fred_vixcls.json").write_text(json.dumps(
        [{"date": str(d.date()), "value": "." if d.weekday() > 4 else str(16 + d.day % 17)} for d in days]))
    (data / "trials.jsonl").write_text(json.dumps({"trial_id": "old", "n_trials": 30}) + "\n")
    news_dir = data / "news" / f"symbol={stocks[0]}"
    news_dir.mkdir(parents=True)
    pd.DataFrame({"created_at": pd.to_datetime(["2022-02-01 12:00", "2022-05-03 12:00"], utc=True),
                  "headline": ["S0 Q4 EPS $1 Beats $0.9 Estimate"] * 2, "symbols": ["S0"] * 2}).to_parquet(
        news_dir / "all.parquet")
    mp = pytest.MonkeyPatch()
    seen = {}

    def daily(data_dir, symbols, start, end, **kw):
        seen["symbols"] = list(symbols)
        return b, False

    mp.setattr(wfm, "load_or_build_daily", daily)
    mp.setattr(wfm, "intraday_cache", lambda *a, **k: intraday_frame(b, stocks))
    mp.setattr(wfm, "first_train_month", lambda start, warmup: np.datetime64("2022-05"))
    for rule in ("trend_highs", "overnight_spy", "winners_run"):
        mp.setattr(sys, "argv", ["walk_forward_multiday.py", "--rule", rule, "--data", str(data), "--universe",
                                 str(uni), "--start", "2022-01-03", "--end", str(b.dates[-1]),
                                 "--min-train-trades", "3"])
        wfm.main()
    mp.undo()
    return data, uni, seen


def test_run_writes_reports_trades_and_trial_rows(runs, mkt):
    data, uni, seen = runs
    b, stocks, _ = mkt
    assert seen["symbols"] == [*stocks, "XLK", "XLF", "SPY"]
    reps = {json.loads(f.read_text())["experiment"]: json.loads(f.read_text())
            for f in (data / "reports").glob("walkforward_balanced_md_*.json")}
    assert set(reps) == {"trend_highs", "overnight_spy", "winners_run"}
    rep = reps["trend_highs"]
    s = rep["summary"]
    assert rep["set"] == "multiday" and s["folds"] == 8 and s["dsr"]["n_trials"] == 9 * 8 + 30
    assert rep["earnings"]["mode"] == "off (not reliable)" and "NOT applied" in rep["earnings"]["note"]
    assert reps["overnight_spy"]["earnings"]["mode"] == "not applicable"
    t = s["tuned"]
    for k in ("trades", "trades_per_month", "avg_hold_days", "net_return_pct", "cagr_pct", "mean_bp_gross",
              "mean_bp_net", "t_stat_per_trade", "hit_rate", "daily_sharpe_ann", "max_drawdown_pct",
              "months_positive", "time_invested_pct"):
        assert t[k] is not None
    assert math.isclose(t["vs"]["spy"]["excess_return_pp"], t["net_return_pct"] - s["spy"]["return_pct"], abs_tol=0.011)
    assert math.isclose(t["vs"]["universe_ew"]["sharpe_minus_benchmark"],
                        t["daily_sharpe_ann"] - s["universe_ew"]["daily_sharpe_ann"], abs_tol=0.011)
    assert s["universe_ew"] == reps["overnight_spy"]["summary"]["universe_ew"]  # same sessions, same benchmark
    fold_rets = [f["test_ret_pct"] / 100 for f in s["per_fold"]]  # the months compound to the account's return
    assert len(fold_rets) == 8 and math.isclose(np.prod(1 + np.array(fold_rets)) - 1, t["net_return_pct"] / 100,
                                                abs_tol=2e-4) and any(fold_rets)
    trades = pd.read_csv(data / "reports" / rep["files"]["tuned"]["trades"])
    assert len(trades) == t["trades"] > 0 and {"exit_date", "hold_days", "gap", "equity", "config", *COSTS} <= set(
        trades.columns)
    assert np.allclose(trades["pnl"], trades["gross_pnl"] - trades[list(COSTS)].sum(axis=1))
    assert math.isclose(t["costs"]["net_pnl"], float(trades["pnl"].sum()), abs_tol=0.01)
    assert math.isclose(t["costs"]["gap_slippage"], float(trades["cost_slippage"].sum()), abs_tol=0.01)
    rows = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    mine = [r for r in rows if r.get("variant") == "multiday"]
    assert len(mine) == 6 and {r["strategy"] for r in mine} == set(reps)
    assert sorted(r["n_trials"] for r in mine if r["strategy"] == "trend_highs") == [1, 72]
    assert all((data / "trials" / f"{r['trial_id']}.csv").is_file() for r in mine)
    md = next((data / "reports").glob("walkforward_balanced_md_overnight_spy_*.md")).read_text()
    assert "Survivorship bias" in md and "| Spread $ |" in md and "Return minus the universe equal-weight" in md
    assert "Status: provisional (decision 15)" in md and rep["status"].startswith("provisional")


def test_summary_table_has_both_benchmarks_and_the_survivorship_line(runs, monkeypatch, capsys):
    data, uni, _ = runs
    end = json.loads(next((data / "reports").glob("walkforward_balanced_md_trend_*.json")).read_text())["end"]
    has = ["experiments_summary.py", "--data", str(data), "--start", "2022-01-03", "--end", end, "--universe", str(uni)]
    for name, code in (("trend_highs", 0), ("rel_strength", 1), ("control", 1)):
        monkeypatch.setattr(sys, "argv", [*has, "--has", name])
        with pytest.raises(SystemExit) as e:
            experiments_summary.main()
        assert e.value.code == code
    monkeypatch.setattr(sys, "argv", ["experiments_summary.py", "--data", str(data), "--set", "multiday"])
    experiments_summary.main()
    text = (data / "reports" / "experiments_multiday_summary.md").read_text()
    assert text in capsys.readouterr().out
    lines = text.splitlines()
    assert lines[2].startswith("**Survivorship bias:** the universe is today's 300 large caps")  # the first line
    assert lines[4].startswith("**Provisional (decision 15):**") and "cannot pass Gate B" in lines[4]
    head = next(x for x in lines if x.startswith("| Experiment | Trades |"))
    assert head == ("| Experiment | Trades | Trades/month | Avg holding days | Net return % | CAGR % | "
                    "Gross bp/trade | Net bp/trade | t-stat | Hit rate | Sharpe | Max drawdown % | Months positive | "
                    "% time invested | Deflated Sharpe |")
    first = [x.split(" | ")[0] for x in lines[lines.index(head) + 2:lines.index(head) + 10]]
    assert first == ["| trend_highs (tuned monthly)", "| trend_highs (fixed 55-day high, 3 ATR)",
                     "| winners_run (tuned monthly)", "| winners_run (fixed 5 sessions)",
                     "| overnight_spy (tuned monthly)", "| overnight_spy (fixed VIX < 30)",
                     "| SPY buy-and-hold (benchmark, price only)",
                     "| Universe equal-weight, rebalanced monthly, after costs (benchmark)"]
    assert "| Experiment | Net return % | Minus SPY (points) | Minus equal-weight universe (points) |" in text
    assert "| Experiment | Avg position $ | Gross $ | Spread $ | Impact $ | Gap slippage $ |" in text
    logged = 30 + (9 * 8 + 1) + 2 * (3 * 8 + 1)  # the old row, then tuned + fixed of each of the three runs
    assert "Not run yet: rel_strength." in text and f"all {logged} configurations" in text
    for x in lines:
        if x.startswith("| ") and "---" not in x:
            assert len(x.split(" | ")) in (len(experiments_summary.MD_COLUMNS), len(experiments_summary.VS_COLUMNS),
                                           len(experiments_summary.COST_COLUMNS))
    monkeypatch.setattr(sys, "argv", ["experiments_summary.py", "--data", str(data)])  # the #109 table still works
    experiments_summary.main()
    assert "Not run yet: control, mornings, wide_stop, high_vix, mornings_wide_stop." in capsys.readouterr().out


def test_analyze_trades_reads_multiday_files(runs, capsys, monkeypatch):
    data, uni, _ = runs
    f = next((data / "reports").glob("trades_balanced_md_trend_highs_*_tuned.csv"))
    monkeypatch.setattr(sys, "argv", ["analyze_trades.py", str(f), "--universe", str(uni)])
    analyze_trades.main()
    out = capsys.readouterr().out
    for needle in ("== Gross vs net ==", "stops jumped by the open:", "== By year (entry) ==", "== By exit ==",
                   "== By holding period (sessions) ==", "== By configuration ==", "== By sector ==", "avg_hold",
                   "XLK", "trend_highs_high_n"):
        assert needle in out
    assert "By entry time" not in out and "By VIX" not in out
    t = analyze_trades.add_groups(analyze_trades.load_trades(f))
    by_hold = analyze_trades.breakdown(t, "holding", 100_000.0)
    assert by_hold.loc["all", "trades"] == len(t) == by_hold["trades"][:-1].sum()
    assert math.isclose(by_hold.loc["all", "net_bp"], float((t["pnl"] / t["equity"]).mean()) * 1e4, abs_tol=1e-3)
    w = next((data / "reports").glob("trades_balanced_md_winners_run_*_tuned.csv"))
    tw = analyze_trades.add_groups(analyze_trades.load_trades(w))
    assert "same session" in set(tw["holding"].astype(str)) and "time" in set(tw["reason"])


def test_run_experiments_script_has_the_multiday_command():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in ('"${1:-}" = "multiday"', "trend_highs rel_strength overnight_spy winners_run",
                   "walk_forward_multiday.py", "--set multiday", "--has", "control mornings wide_stop high_vix "
                   "mornings_wide_stop", "experiments_multiday_summary.md"):
        assert needle in sh
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "run_experiments.sh")]).returncode == 0


def test_build_daily_matches_the_minute_arrays():
    sd = minute_day()
    b = build_daily(["AAA", "NONE"], lambda s: sd if s == "AAA" else None)
    assert b.symbols == ["AAA", "NONE"] and b.c[0, 0] == 101.0 and np.isnan(b.c[0, 1])
    back = DailyBars.from_frame(b.to_frame(), b.symbols)
    assert all(np.array_equal(getattr(back, k), getattr(b, k), equal_nan=True) for k in ("o", "h", "l", "c", "v"))
    assert OvernightSpy.caps.max_positions == 1
