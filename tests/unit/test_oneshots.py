"""Issue #112 (story S72): the six one-shot ten-year tests. The four daily rules (`replay/oneshots_daily.py`), the
two intraday rules (`replay/oneshots_intraday.py`) and the runner (`scripts/oneshots.py`,
`./scripts/run_experiments.sh oneshots`). Small seeded synthetic data only, no network, no real history."""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.evaluation import prereg
from kaibot.ingest import sp500_pit
from kaibot.replay import multiday_core as mc
from kaibot.replay import oneshots_daily as od
from kaibot.replay import oneshots_intraday as oi
from kaibot.replay.data import ET, N_MIN, SymbolDays, load_symbol
from kaibot.replay.multiday import DailyBars
from kaibot.replay.multiday_rules import book_digest
from kaibot.strategies import news_continuation as news
from kaibot.strategies import orb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core as core_script  # noqa: E402
import oneshots as script  # noqa: E402
import test_core_pit as tp  # noqa: E402
import test_multiday as tm  # noqa: E402
import test_multiday_core as tc  # noqa: E402

POL = tm.POL


# ---------------------------------------------------------------- the frozen settings are the pre-registered ones


@pytest.mark.parametrize("rule", od.RULES)
def test_daily_neighbours_are_the_pre_registered_ones(rule):
    p = prereg.load(rule, ROOT, committed=False)
    assert set(p.neighbours) == set(od.NEIGHBOURS[rule]) and len(p.neighbours) <= 5
    assert p.status == "pre-registered" and p.date == date(2026, 11, 13)


def test_intraday_neighbours_and_settings_are_the_pre_registered_ones():
    assert set(prereg.load("orb_stocks_in_play", ROOT, committed=False).neighbours) == {"top10", "top40", "stop20"}
    assert set(prereg.load("news_continuation", ROOT, committed=False).neighbours) == set(oi.NEWS_VARIANTS) - {"base"}
    assert oi.NEWS_VARIANTS["base"] == news.DEFAULT and oi.PLACE_M == 20 and oi.ORB_TOP["base"] == orb.DEFAULT.top_n
    assert (oi.KEEP_MIN_R, oi.KEEP_MIN_T, oi.KEEP_DROP_SYMBOLS) == (0.05, 3.0, 10)
    v = od.VERSIONS
    assert (v["pullback_uptrend"].max_names, v["pullback_uptrend"].rsi_max, v["pullback_uptrend"].fill) == (4, 10.0,
                                                                                                          "open")
    assert [v[k].max_names for k in od.RULE_VERSIONS["ath_breakout"]] == [10, 5, 20]
    assert (v["ath_breakout"].trail_atr, v["ath_breakout"].atr_n, v["ath_breakout"].min_price) == (10.0, 42, 10.0)
    assert (v["post_earnings_week"].entry_day, v["post_earnings_week"].hold, v["post_earnings_week"].fill) == (
        1, 5, "close")
    assert [v[k].hold for k in od.RULE_VERSIONS["weekly_fall_rebound"]] == [5, 20]
    assert (v["weekly_fall_rebound"].fall, v["weekly_fall_rebound"].decile) == (-0.15, 0.10)


# ---------------------------------------------------------------- daily signals


def test_rsi_two_is_wilders():
    c = np.array([[10.0], [11.0], [10.5], [10.0], [10.8], [np.nan], [11.0], [11.5], [11.2]])
    r = od.rsi_wilder(c, 2)[:, 0]
    assert np.isnan(r[:2]).all()
    up, dn = (1.0 + 0.0) / 2, (0.0 + 0.5) / 2  # the first average is the simple mean of two changes
    assert r[2] == pytest.approx(100 - 100 / (1 + up / dn))
    up, dn = up / 2, (dn + 0.5) / 2
    assert r[3] == pytest.approx(100 - 100 / (1 + up / dn))
    up, dn = (up + 0.8) / 2, dn / 2
    assert r[4] == pytest.approx(100 - 100 / (1 + up / dn))
    assert np.isnan(r[5:8]).all() and np.isfinite(r[8])  # a missing close restarts the average
    assert od.rsi_wilder(np.array([[1.0], [2.0], [3.0]]))[2, 0] == 100.0


@pytest.fixture(scope="module")
def market():
    """The momentum core's synthetic market, plus two stocks that fall 20% in five sessions (twice each) and
    recover, and SEC-style announcement days for every stock."""
    b, stocks = tc.core_market()
    c = b.c.copy()
    for j, start in ((20, 330), (20, 450), (40, 360), (40, 500)):
        f = np.ones(len(c))
        f[start:start + 5] = np.linspace(0.96, 0.80, 5)
        f[start + 5:start + 15] = np.linspace(0.82, 1.0, 10)
        c[:, j] *= f
    o = np.vstack([c[:1], c[:-1]])
    b = DailyBars.make(b.dates, b.symbols, o, np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c, b.v)
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(b, k)[:330, 7] = np.nan
    m = mc.Market.from_bars(b, b.cols(stocks), b.col("SPY"))
    rng = np.random.default_rng(4)
    events = {j: sorted(rng.choice(np.arange(5, len(b.dates) - 12), 9, replace=False).tolist())
              for j in m.stocks.tolist()}
    return b, m, od.DailySignals(m, events)


def run(market, version, **kw):
    b, m, sig = market
    spec = kw.pop("spec", None) or od.VERSIONS[version]
    return od.run_daily(m, sig, POL, spec, tc.I0, len(b.dates) - 1, **kw)


def entries(book, b):
    return [(b.index_from(t.date), b.col(t.symbol), t) for t in book.trades if t.reason != "end_of_data"]


def test_pullback_buys_the_next_open_after_the_signal_and_sells_the_open_after_a_close_above_sma5(market):
    b, m, sig = market
    book, rule = run(market, "pullback_uptrend")
    sma200, sma5, rsi = sig.sma(200), sig.sma(5), sig.rsi2()
    assert len(book.trades) > 20 and rule.signals > len(book.trades)
    for i, j, t in entries(book, b):
        k = i - 1
        assert sig.ok[k, j] and sig.c[k, j] > sma200[k, j] and sig.c[k, j] < sma5[k, j] and rsi[k, j] < 10
        assert t.entry_at == "open"
        x = b.index_from(t.exit_date)
        assert t.reason == "above_sma5" and sig.c[x - 1, j] > sma5[x - 1, j]
        assert all(not sig.c[q, j] > sma5[q, j] for q in range(i, x - 1))  # the first close above it
    assert max(r["positions"] for r in book.daily) <= 4
    for t in book.trades:  # a quarter of equity per slot, capped by the level's 15%
        assert t.qty * t.entry <= 0.15 * t.equity * 1.02 + t.entry


def test_pullback_takes_the_lowest_rsi_first(market):
    b, m, sig = market
    rule = od.DailyRule(sig, POL, od.VERSIONS["pullback_uptrend"], "open")
    rsi = sig.rsi2()
    for k in range(tc.I0, len(b.dates) - 1):
        js = rule.candidates(k)
        assert list(rsi[k, js]) == sorted(rsi[k, js])


def test_ath_breakout_enters_after_an_all_time_high_and_exits_after_a_close_below_the_ratchet(market):
    b, m, sig = market
    book, rule = run(market, "ath_breakout", seed=1)
    ath, atr = sig.all_time_high(252), sig.atr(42)
    trail = [t for t in book.trades if t.reason == "trail"]
    assert trail and len(book.trades) > len(trail)
    for i, j, t in entries(book, b):
        assert ath[i - 1, j] and sig.px[i - 1, j] >= 10.0 and t.entry_at == "open"
        raw = b.c[:i - 1, j]
        assert sig.c[i - 1, j] >= np.nanmax(raw)
    for t in trail:
        i, x, j = b.index_from(t.date), b.index_from(t.exit_date), b.col(t.symbol)
        hi = np.maximum.accumulate(sig.c[i:x, j])
        stop = np.maximum.accumulate(hi - 10 * atr[i:x, j])
        assert sig.c[x - 1, j] < stop[-1] and (sig.c[i:x - 1, j] >= stop[:-1]).all()


def test_ath_breakout_draws_random_subsets_per_seed_and_reproduces_a_seed(market):
    a1, _ = run(market, "ath_breakout", seed=3)
    a2, _ = run(market, "ath_breakout", seed=3)
    others = [run(market, "ath_breakout", seed=s)[0] for s in (4, 5, 6)]
    assert book_digest(a1) == book_digest(a2)
    names = {tuple(sorted({t.symbol for t in bk.trades})) for bk in (a1, *others)}
    assert len(names) > 1  # different subsets of the same signals
    stats = {s: mc.account_stats(bk) for s, bk in zip((3, 4, 5, 6), (a1, *others), strict=True)}
    med = od.median_seed(stats)
    order = sorted(stats, key=lambda s: (stats[s]["sharpe"], s))
    assert med == order[1]  # the 2nd of 4 (the 100th of 200)
    d = od.distribution(list(stats.values()))
    assert d["runs"] == 4 and d["cagr_pct"]["p5"] <= d["cagr_pct"]["p50"] <= d["cagr_pct"]["p95"]
    assert 0.0 <= d["share_passing_mark1_pct"] <= 100.0


def test_post_earnings_week_buys_the_close_of_day_one_and_sells_the_close_of_day_six(market):
    b, m, sig = market
    book, rule = run(market, "post_earnings_week")
    thr = sig.ear_threshold(0.80, 252, 100)
    ear = {(d0, j): e for d0, xs in sig.ear().items() for j, e in xs}
    assert len(book.trades) > 10
    for i, j, t in entries(book, b):
        d0 = i - 1
        assert (d0, j) in ear and ear[(d0, j)] >= thr[d0] and ear[(d0, j)] > 0 and t.entry_at == "close"
        assert b.index_from(t.exit_date) - i == 5 and t.reason == "max_hold"
    b2, _ = run(market, "post_earnings_week", spec=od.neighbours("post_earnings_week")[0])  # day2
    for i, j, _t in entries(b2, b):
        assert (i - 2, j) in ear


def test_the_earnings_threshold_reads_announcements_up_to_the_decision_day_only(market):
    b, m, sig = market
    thr = sig.ear_threshold(0.80, 252, 100)
    ev = {j: [d for d in days if d <= 400] for j, days in sig.events.items()}
    cut = od.DailySignals(m, ev).ear_threshold(0.80, 252, 100)
    assert np.allclose(thr[:401], cut[:401], equal_nan=True) and np.isnan(thr[:20]).all()


@pytest.mark.parametrize("version", ["weekly_fall_rebound", "weekly_fall_rebound_20"])
def test_weekly_fall_rebound_buys_the_planted_falls_at_the_next_close_and_holds_h_sessions(market, version):
    b, m, sig = market
    book, rule = run(market, version)
    hold = od.VERSIONS[version].hold
    got = entries(book, b)
    assert {b.symbols[j] for _, j, _ in got} >= {"S20", "S40"}
    r5 = sig.ret5()
    for i, j, t in got:
        k = i - 1
        assert r5[k, j] <= -0.15 and t.entry_at == "close"
        have = sig.ok[k] & np.isfinite(r5[k])
        assert (r5[k, have] < r5[k, j]).sum() < 0.10 * have.sum()  # bottom decile
        if t.reason == "max_hold":
            assert b.index_from(t.exit_date) - i == hold


@pytest.mark.parametrize("version", ["pullback_uptrend", "ath_breakout", "post_earnings_week", "weekly_fall_rebound"])
def test_future_bars_do_not_change_earlier_decisions(market, version):
    b, m, sig = market
    t = 430
    c = b.c.copy()
    c[t:] *= np.random.default_rng(1).uniform(0.7, 1.3, c[t:].shape)
    o = np.vstack([b.o[:t], c[t - 1:-1]])
    b2 = DailyBars.make(b.dates, b.symbols, o, np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c, b.v)
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(b2, k)[:330, 7] = np.nan
    m2 = mc.Market.from_bars(b2, b2.cols([s for s in b.symbols if s != "SPY"]), b2.col("SPY"))
    sig2 = od.DailySignals(m2, sig.events)

    def orders(mk, sg):
        book, _ = od.run_daily(mk, sg, POL, od.VERSIONS[version], tc.I0, t - 1, seed=2)
        return [(x.symbol, str(x.date), x.qty) for x in book.trades if x.reason != "end_of_data"]

    assert orders(m, sig) == orders(m2, sig2)


def test_buys_keep_the_cash_reserve_and_never_borrow(market):
    for version in ("pullback_uptrend", "ath_breakout_k20", "post_earnings_week"):
        book, _ = run(market, version, seed=0)
        for r in book.daily:
            assert r["exposure"] <= 1.0 + 1e-9
        assert all(t.qty >= 1 for t in book.trades)


def test_the_exposure_matched_control_runs_with_open_fills(market):
    b, m, sig = market
    spec = od.VERSIONS["pullback_uptrend"]
    book, rule = run(market, "pullback_uptrend")
    c = mc.exposure_matched_control(m, POL, spec, book, rule, tc.I0, len(b.dates) - 1, runs=5,
                                    fills=od.fills(spec))
    assert c["kind"] == "exposure_matched" and c["slots"] == len(rule.opened) and c["matched"]
    assert c["committed_exposure_gap_pts"]["max"] <= mc.EXPOSURE_TOLERANCE_PTS


def test_the_default_control_fills_are_unchanged():
    import inspect

    src = inspect.getsource(mc.exposure_matched_control)
    assert "(fills or FILLS)[fill]" in src and mc.FILLS == {"base": ("close", 1.0), "stress": ("open", 2.0)}


# ---------------------------------------------------------------- intraday: synthetic minutes


def minute_days(sym: str, n: int = 80, seed: int = 0, first: str = "2019-01-02", hot_every: int = 4) -> SymbolDays:
    """Random-walk minutes; every `hot_every`-th session trades six times the usual volume in its first five
    minutes with an up first candle, then rises steadily."""
    rng = np.random.default_rng(seed)
    dates = np.busday_offset(np.datetime64(first), np.arange(n), roll="forward").astype("datetime64[D]")
    r = rng.normal(0, 0.0006, (n, N_MIN))
    hot = np.arange(n) % hot_every == hot_every - 1
    r[hot, :5] = 0.002
    r[hot, 5:] += 0.0002
    lp = np.log(40 + seed) + np.cumsum(r.ravel()).reshape(n, N_MIN)
    c = np.exp(lp)
    o = np.exp(lp - r)
    h, lo = np.maximum(o, c) * 1.0004, np.minimum(o, c) * 0.9996
    v = np.full((n, N_MIN), 3000.0) * rng.uniform(0.9, 1.1, (n, 1))
    v[hot, :5] *= 6
    return SymbolDays(sym, dates, o, h, lo, c, v, (h + lo) / 2)


def test_orb_orders_wait_for_0950_and_the_pessimistic_fill_is_never_better():
    sd = minute_days("AAA", seed=1)
    rows = oi.orb_symbol_rows(sd)
    trig = rows[rows["triggered"]]
    assert len(trig) and (trig["trigger_m"] >= 20).all()
    for _, r in trig.iterrows():
        i = int(np.flatnonzero(sd.dates == r["date"])[0])
        m = int(r["trigger_m"])
        assert sd.h[i, m] >= r["or_high"] and not (sd.h[i, 20:m] >= r["or_high"]).any()
        assert r["or_high"] == pytest.approx(sd.h[i, :5].max())
        assert r["r_net_base_pess"] <= r["r_net_base_trig"] + 1e-9
        assert r["r_net_base_pess"] < r["r_gross_base_pess"]  # costs are charged
    assert (rows["rel"] >= 1.0).all() and set(rows["up"]) <= {True, False}


def test_orb_relative_volume_reads_only_earlier_sessions_and_the_first_five_minutes():
    sd = minute_days("AAA", seed=2)
    base = oi.orb_symbol_rows(sd).set_index("date")
    sd2 = dataclasses.replace(sd, v=sd.v.copy())
    sd2.v[:, 5:] *= 3.0  # later minutes of every day
    sd2.v[60:] *= 5.0  # later sessions
    other = oi.orb_symbol_rows(sd2).set_index("date")
    common = base.index[base.index < sd.dates[60]]
    assert np.allclose(base.loc[common, "rel"], other.loc[common, "rel"])


def test_orb_selection_ranks_down_candles_too_and_trades_only_triggered_up_candles():
    rows = pd.DataFrame({"date": ["d1"] * 4 + ["d2"], "symbol": ["A", "B", "C", "D", "A"],
                         "rel": [9.0, 8.0, 7.0, 6.0, 2.0], "up": [False, True, True, True, True],
                         "triggered": [False, True, False, True, True], "trigger_m": [-1, 21, -1, 30, 25]})
    for key in ("r_net", "r_gross", "ret"):
        rows[f"{key}_base_pess"] = [np.nan, 0.5, np.nan, -1.0, 0.2]
    sel = oi.orb_select(rows, top_n=2)
    assert list(sel["symbol"]) == ["B", "A"]  # A's down candle takes a place on d1, D is third
    assert list(oi.orb_select(rows, top_n=4)["symbol"]) == ["B", "D", "A"]


def test_orb_keep_marks():
    def trades(r, syms):
        return pd.DataFrame({"date": pd.date_range("2018-01-01", periods=len(r)), "symbol": syms, "r_net": r,
                             "ret": np.asarray(r) * 0.001})
    rng = np.random.default_rng(0)
    good = trades(rng.normal(0.2, 1.0, 3000), [f"S{k % 50}" for k in range(3000)])
    assert oi.orb_verdict(oi.trade_stats(good))["verdict"] == "keep"
    w = rng.normal(0.0, 1.0, 3000)
    weak = trades(w - w.mean() + 0.03, [f"S{k % 50}" for k in range(3000)])  # mean exactly 0.03R
    assert oi.orb_verdict(oi.trade_stats(weak))["verdict"] == "propose deletion"
    r = np.full(400, -0.05)
    r[:10] = 30.0  # all of the profit in ten symbols
    concentrated = trades(r, [f"S{k}" if k < 10 else "Z" for k in range(400)])
    st = oi.trade_stats(concentrated)
    v = oi.orb_verdict(st)
    assert st["total_r_without_best10"] < 0 and not v["mark_without_best10"] and v["verdict"] == "propose deletion"
    assert st["total_r_without_top1pct"] < st["total_r"] and set(st["years"]) == {"2018", "2019"}


def test_news_flags_match_a_plain_timestamp_loop():
    dates = np.array(["2021-03-12", "2021-03-15", "2021-11-05", "2021-11-08"], dtype="datetime64[D]")  # DST
    rng = np.random.default_rng(3)
    t = np.sort(np.datetime64("2021-03-11T00:00") + rng.integers(0, 250 * 86400, 600).astype("timedelta64[s]"))
    got = oi.news_flags(dates, t, oi.NEWS_GRID)

    def utc(day, minute):
        x = pd.Timestamp(f"{day} 09:30", tz=ET) + pd.Timedelta(minutes=minute)
        return np.datetime64(x.tz_convert("UTC").tz_localize(None), "s")

    for i in range(1, len(dates)):
        for g, m in enumerate(oi.NEWS_GRID):
            lo, hi = utc(dates[i - 1], N_MIN), utc(dates[i], m + 1) - np.timedelta64(15, "m")
            assert got[i, g] == bool(((t >= lo) & (t <= hi)).any())
    assert not got[0].any()


def news_day_sd() -> SymbolDays:
    """Every sixth session: a 4% opening move on twelve times the usual volume, then a 09:40-09:44 pullback whose
    low dips under the previous five-minute low but holds the session VWAP, and closes up."""
    sd = minute_days("NWS", n=60, seed=5, hot_every=1000)
    for i in range(25, 60, 6):
        base = sd.c[i - 1, -1]
        path = base * np.r_[np.linspace(1.008, 1.04, 5), np.full(5, 1.045), [1.043, 1.042, 1.043, 1.045, 1.046],
                            np.full(N_MIN - 15, 1.05)]
        sd.o[i], sd.c[i] = np.r_[base, path[:-1]], path
        sd.h[i], sd.l[i] = np.maximum(sd.o[i], sd.c[i]) * 1.0001, np.minimum(sd.o[i], sd.c[i]) * 0.9999
        sd.l[i, 11] = base * 1.0395
        sd.v[i] *= 12
        sd.vw[i] = (sd.h[i] + sd.l[i]) / 2
    return sd


def test_news_rows_need_a_headline_at_least_15_minutes_old_and_share_one_setup_per_minute():
    sd = news_day_sd()
    none = oi.news_symbol_rows(sd, np.array([], dtype="datetime64[s]"))
    assert not len(none)
    t = np.array([oi.session_opens_utc(sd.dates[i:i + 1])[0] - np.timedelta64(3600, "s") for i in range(25, 60, 6)])
    rows = oi.news_symbol_rows(sd, t)
    base = rows[rows["variant"] == "base"]
    assert len(base) and (base["m"] >= news.DEFAULT.first_m).all() and base["rand_r"].map(len).min() > 50
    assert base.groupby("date").size().max() == 1
    for vname, p in oi.NEWS_VARIANTS.items():  # the shared setup equals the rule's own setup with its params
        stats = {k: v[:-1] for k, v in news.prior_stats(sd).items()}
        from kaibot.strategies.shadow_types import Panel

        panel = Panel(np.array(["NWS"] * len(sd.dates)), sd.o, sd.h, sd.l, sd.c, sd.v, sd.vw)
        flags = oi.news_flags(sd.dates, t, oi.NEWS_GRID)
        first = {}
        for g, m in enumerate(oi.NEWS_GRID):
            with np.errstate(invalid="ignore", divide="ignore"):
                mask, _ = news.setup(panel, stats, m, p)
            for i in np.flatnonzero(mask & flags[:, g]):
                first.setdefault(i, m)
        got = rows[rows["variant"] == vname]
        want = {sd.dates[i]: m for i, m in first.items()}
        assert dict(zip(got["date"], got["m"], strict=True)).items() <= want.items()
    assert set(base["m"]) == {14}  # the 09:44 decision (bar 09:40-09:44)
    late = t + np.timedelta64(3600 + 9 * 60, "s")  # 09:39: too fresh for the 09:44 decision (needs 15 minutes)
    lr = oi.news_symbol_rows(sd, late)
    assert not len(lr) or (lr["m"] > 14).all()


def test_complete_months_and_their_ranges():
    heads = {s: {"2019-01": 5, "2019-02": 4, "2019-03": 1, "2019-05": 6, "2019-06": 5} for s in "ABCDEFGHIJ"}
    heads["J"] = {"2019-01": 5, "2019-02": 4, "2019-05": 6}  # 90% have news in March? no: 9 of 10 -> still 90%
    bars = {s: ["2019-01", "2019-02", "2019-03", "2019-04", "2019-05", "2019-06"] for s in heads}
    got = oi.complete_months(heads, bars, np.datetime64("2019-06-20"))
    # March: 9 of 10 symbols but 9 headlines < half the median; April: none; June ends after the last headline
    assert got == ["2019-01", "2019-02", "2019-05"]
    assert oi.month_ranges(got) == ["2019-01 to 2019-02", "2019-05"]
    assert oi.complete_months(heads, bars, None) == []


def test_random_entry_percentile_and_the_news_marks():
    tr = pd.DataFrame({"rand_r": [[-1.0, 0.0, 1.0]] * 50})
    hi = oi.random_entry_percentile(tr, 2.0, runs=200)
    lo = oi.random_entry_percentile(tr, -2.0, runs=200)
    assert hi["percentile"] == 100.0 and lo["percentile"] == 0.0 and hi["runs"] == 200
    st = {"trades": 1200, "t": 3.5, "mean_r": 0.1}
    marks = oi.news_marks(st, hi, 1.2, 0.8)
    assert [x["verdict"] for x in marks[:4]] == ["pass"] * 4 and "not computed" in marks[4]["verdict"]
    assert oi.news_marks({"trades": 100, "t": 1.0, "mean_r": 0.0}, lo, 0.1, 0.8)[0]["verdict"] == "fail"


# ---------------------------------------------------------------- the runner, end to end on synthetic data


def write_minutes(data: Path, sd: SymbolDays) -> None:
    opens = oi.session_opens_utc(sd.dates)
    ts = (opens[:, None] + (np.arange(N_MIN) * 60).astype("timedelta64[s]")[None, :]).ravel()
    df = pd.DataFrame({"ts": pd.to_datetime(ts).tz_localize("UTC"), **{k: getattr(sd, k).ravel() for k in
                                                                       ("o", "h", "l", "c", "v", "vw")}})
    df["mon"] = df["ts"].dt.tz_convert(ET).dt.strftime("%Y-%m")
    for mon, g in df.groupby("mon"):
        f = data / "bars_1m" / f"symbol={sd.symbol}" / f"{mon}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        g.drop(columns="mon").to_parquet(f, index=False)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Every rule through `scripts/oneshots.py` on synthetic data: a daily store with the point-in-time
    membership log, SEC-style announcements, minute bars and headlines for six stocks; then the summary."""
    data = tmp_path_factory.mktemp("oneshots")
    acts = [("SPY", 300, None, 1.6), ("SPY", 363, None, 1.7)]
    panel, stocks = tp.raw_market(actions=acts)
    d = panel.dates
    ms = sp500_pit.Membership([sp500_pit.Change(date(2021, 12, 1), tuple(stocks))], {}, stocks, date(2024, 2, 1))
    tp.write_store(data / "daily", panel, {}, [(s, d[i], r, c) for s, i, r, c in acts])
    uni = data / "u.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": stocks[:15], "XLF": stocks[15:]}, "market": "SPY"}))
    mp = pytest.MonkeyPatch()
    mp.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, **k: ms))
    for mod in (core_script, script):
        mp.setattr(mod, "first_train_month", lambda start, warmup: np.datetime64("2022-03"))
    base = ["--data", str(data), "--universe-file", str(uni), "--start", tp.FIRST, "--end", str(d[-1]),
            "--control-runs", "3", "--seeds", "3"]
    out = {}

    def call(*argv):
        mp.setattr(sys, "argv", ["oneshots.py", *argv])
        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf):
            try:
                script.main()
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    out["earnings_missing"] = call("--rule", "post_earnings_week", *base)
    out["orb_missing"] = call("--rule", "orb_stocks_in_play", *base)
    rows = [{"symbol": s, "company": s, "session": d[i].astype(object), "report": True,
             "accepted_ts": pd.Timestamp(str(d[i - 1])).tz_localize("UTC") + pd.Timedelta(hours=21)}
            for s in stocks for i in range(25, len(d) - 10, 61)]
    (data / "earnings").mkdir()
    pd.DataFrame(rows).to_parquet(data / "earnings" / "announcements.parquet")
    for rule in od.RULES:
        out[rule] = call("--rule", rule, *base)
    # intraday: today's list is the same file; six of its stocks and SPY get minute bars from 2022-01
    mdata = data
    first = str(d[0])
    for k, s in enumerate([*stocks[:6], "SPY"]):
        write_minutes(mdata, minute_days(s, n=140, seed=10 + k, first=first))
    heads = []
    for s in stocks[:6]:
        opens = oi.session_opens_utc(minute_days(s, n=140, first=first).dates)
        heads.append((s, opens - np.timedelta64(1800, "s")))
    for s, t in heads:
        f = mdata / "news" / f"symbol={s}" / "all.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"created_at": pd.to_datetime(t).tz_localize("UTC")}).to_parquet(f, index=False)
    mend = ["--data", str(data), "--universe-file", str(uni), "--start", first, "--end", str(d[139]),
            "--control-runs", "3", "--seeds", "3"]
    mp.setattr(script, "first_train_month", lambda start, warmup: np.datetime64(first, "M"))
    for rule in script.INTRADAY:
        out[rule] = call("--rule", rule, *mend)
    out["orb_again"] = call("--rule", "orb_stocks_in_play", *mend)
    out["summary"] = call("--summary", "--data", str(data))
    yield data, base, mend, out, call
    mp.undo()


def test_missing_data_skips_with_a_message_and_a_note(runs):
    data, base, mend, out, call = runs
    code, text = out["earnings_missing"]
    assert code == script.SKIPPED and "SKIPPED" in text and "announcements.parquet" in text
    assert out["orb_missing"][0] == script.SKIPPED and "minute history" in out["orb_missing"][1]
    assert not (data / "reports" / "oneshots_post_earnings_week_skipped.json").exists()  # ran later: note removed
    assert all(out[r][0] in (0, None) for r in (*od.RULES, *script.INTRADAY))


def test_daily_reports_carry_marks_benchmarks_control_hashes_and_trial_rows(runs):
    data, base, mend, out, call = runs
    for rule in od.RULES:
        f, rep = script.find_report(data / "reports", rule)
        assert rep["status"].startswith("PROVISIONAL") and rep["universe_mode"] == "point_in_time"
        assert len(rep["gate_v2_sha256"]) == 64 and rep["prereg"]["file"] == f"docs/prereg/{rule}.md"
        assert set(rep["benchmarks"]) >= {"spy", "spy_total_return", "universe_ew"}
        bv = rep["versions"][od.RULE_VERSIONS[rule][0]]
        assert [g["mark"] for g in bv["gate_b"]] == [1, 2, 3, 4, 5, 6]
        assert bv["rows"][0]["control"]["kind"] == "exposure_matched" and bv["rows"][0]["control"]["runs"] == 3
        kinds = {r["kind"] for r in bv["rows"]}
        assert kinds >= {"grid", "break_even_input", "neighbour"}
        assert len([r for r in bv["rows"] if r["kind"] == "neighbour"]) == len(od.NEIGHBOURS[rule])
        assert set(rep["versions"]) == set(od.RULE_VERSIONS[rule])
    _, ath = script.find_report(data / "reports", "ath_breakout")
    av = ath["versions"]["ath_breakout"]
    assert av["distribution"]["base|1x"]["runs"] == 3 and set(av["distribution"]) >= {"stress|1x", "base|2x"}
    assert av["extra_marks"][0]["mark"] == "ath-1" and av["median_seed"] in (0, 1, 2)
    assert set(av["neighbour_distribution"]) == {f"ath_breakout~{n}" for n in od.NEIGHBOURS["ath_breakout"]}
    _, pe = script.find_report(data / "reports", "post_earnings_week")
    assert pe["earnings"]["events"] > 0
    trials = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    mine = [t for t in trials if t["variant"] == "oneshots"]
    assert all(t["prereg_sha256"] and t["status"] == "provisional" for t in mine)
    assert {t["strategy"] for t in mine} == {*od.RULES, *script.INTRADAY}
    assert all((data / "trials" / f"{t['trial_id']}.csv").is_file() for t in mine)


def test_has_finds_a_finished_report_with_the_same_settings(runs):
    data, base, mend, out, call = runs
    assert call("--has", "pullback_uptrend", *base)[0] == 0
    assert call("--has", "pullback_uptrend", *[x if x != "3" else "4" for x in base])[0] == 1
    assert call("--has", "ath_breakout", *base)[0] == 0
    assert call("--has", "orb_stocks_in_play", *mend)[0] == 0


def test_intraday_reports_and_the_resumable_symbol_cache(runs):
    data, base, mend, out, call = runs
    _, orb_rep = script.find_report(data / "reports", "orb_stocks_in_play")
    assert orb_rep["verdict"] in ("keep", "propose deletion") and orb_rep["universe_mode"] == "today"
    assert set(orb_rep["variants"]) == {"base", "trigger_fill", "gross", "top10", "top40", "stop20"}
    assert orb_rep["variants"]["base"]["trades"]["trades"] > 0
    cache = data / "oneshots" / "orb_stocks_in_play"
    assert len(list(cache.glob("*.parquet"))) == 6  # the six stocks (SPY is the calendar)
    assert "pass 1:" not in out["orb_again"][1]  # every symbol came from the cache
    _, nw = script.find_report(data / "reports", "news_continuation")
    assert "complete_news_months" in nw and nw["complete_news_month_count"] >= 1
    assert [m["mark"] for m in nw["marks"]][0] == "trades >= 900" and nw["verdict"].startswith("FAIL")


def test_the_summary_is_one_table_with_pass_fail_per_rule(runs):
    data, base, mend, out, call = runs
    text = (data / "reports" / script.SUMMARY).read_text()
    assert text.startswith("# One-shot ten-year tests (issue #112, story S72)") and "PROVISIONAL" in text
    assert text.count("| Rule / version |") == 1
    for rule in (*script.INTRADAY, *od.RULES):
        assert f"`{rule}`" in text
    assert "weekly_fall_rebound_20" in text and "ath_breakout_k5" in text and "ath_breakout_k20" in text
    assert "complete news months:" in text and ("**KEEP**" in text or "propose deletion" in text)


def test_no_pre_registration_no_run(monkeypatch, tmp_path):
    monkeypatch.setattr(script, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as e:
        script.prereg("pullback_uptrend")
    assert "pre-registration" in str(e.value)


def test_run_experiments_has_the_oneshots_set_and_it_is_documented():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in ('"${1:-}" = "oneshots"', "scripts/oneshots.py --summary", "KAIBOT_ONESHOT_CONTROL_RUNS:-500",
                   "KAIBOT_ONESHOT_SEEDS:-200", 'if [ "$rc" = 3 ]', "experiments_oneshots_summary.md",
                   "orb_stocks_in_play news_continuation pullback_uptrend ath_breakout post_earnings_week"):
        assert needle in sh, needle
    assert sh.index('"${1:-}" = "oneshots"') < sh.index("No .env found")
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "run_experiments.sh")]).returncode == 0
    doc = (ROOT / "docs" / "m0" / "reproduce.md").read_text()
    for needle in ("./scripts/run_experiments.sh oneshots", "experiments_oneshots_summary.md", "#112",
                   "complete news months"):
        assert needle in doc, needle


def test_minute_writer_round_trips_through_the_replay_loader(tmp_path):
    sd = minute_days("RT", n=5, seed=1)
    write_minutes(tmp_path, sd)
    back = load_symbol(tmp_path, "RT")
    assert np.array_equal(back.dates, sd.dates) and np.allclose(back.c, sd.c) and np.allclose(back.v, sd.v)
    assert math.isclose(back.o[0, 0], sd.o[0, 0])


def test_the_one_shots_are_research_only():
    """Nothing under src/ imports the one-shot modules; the daily rules charge costs only through the harness's
    engine and the intraday rules only through replay/sim.py and the shadow fees (no simulator of their own)."""
    for f in (ROOT / "src" / "kaibot").rglob("*.py"):
        if f.name not in ("oneshots_daily.py", "oneshots_intraday.py"):
            text = f.read_text()
            assert "oneshots_daily" not in text and "oneshots_intraday" not in text, f
    daily = (ROOT / "src" / "kaibot" / "replay" / "oneshots_daily.py").read_text()
    for banned in ("buy_px", "sell_px", "sell_fees", "shadow_fill_price", "kaibot.engine", "kaibot.broker"):
        assert banned not in daily, banned
    intraday = (ROOT / "src" / "kaibot" / "replay" / "oneshots_intraday.py").read_text()
    assert "kaibot.engine" not in intraday and "from kaibot.replay.sim import" in intraday
