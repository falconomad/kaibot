"""S98 (#372): `news_gap_week` in the daily one-shot harness (`replay/oneshots_daily.py`), as frozen in
docs/prereg/news_gap_week.md. Tests first for the three things the story names: no look-ahead (a headline or filing
after the decision time never counts), the earnings-day exclusion, and the control picks only no-news jumps.
Small synthetic data only; the end-to-end run through scripts/oneshots.py is in test_oneshots.py."""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from kaibot.evaluation import prereg
from kaibot.ingest import nyse_sessions as nyse
from kaibot.replay import multiday_core as mc
from kaibot.replay import oneshots_daily as od
from kaibot.replay.multiday import DailyBars

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
import test_multiday as tm  # noqa: E402
import test_multiday_core as tc  # noqa: E402

POL = tm.POL
# (session, column) of the planted jumps: +6% on 4x volume (the synthetic stocks move about 1% a day)
NEWS, NONEWS, LATE, EARN, FILED, UNCOVERED, OUTSIDE = (300, 10), (310, 11), (320, 12), (330, 13), (340, 14), \
    (350, 15), (360, 16)
PLANTED = (NEWS, NONEWS, LATE, EARN, FILED, UNCOVERED, OUTSIDE)


def _bars(extra: tuple = ()) -> DailyBars:
    b, _ = tc.core_market()
    c, v = b.c.copy(), b.v.copy()
    for k, j in (*PLANTED, *extra):
        c[k:, j] *= 1.06
        v[k, j] *= 4.0
    o = np.vstack([c[:1], c[:-1]])
    out = DailyBars.make(b.dates, b.symbols, o, np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c, v)
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(out, k)[:330, 7] = np.nan
    return out


def _ts(b: DailyBars, k: int, minutes_before_close: float) -> np.datetime64:
    d = date.fromisoformat(str(b.dates[k]))
    return np.datetime64(nyse.close_utc(d).replace(tzinfo=None), "s") - np.timedelta64(int(minutes_before_close * 60),
                                                                                       "s")


def _news(b: DailyBars, *, complete_from: int = 0) -> od.NewsInputs:
    """Headlines: NEWS has one 2 hours before its close; LATE has one 14 minutes before its close (too late) and
    one just after the previous close; UNCOVERED's company has no headline at all; every other planted column has
    an old headline (coverage) far from its jump. EARN's jump is day 0 of a report. FILED has a non-2.02 8-K in the
    window and no headline. Sessions before `complete_from` are outside the complete news months."""
    old = lambda j: _ts(b, 20, 600)  # noqa: E731  (coverage: a headline long before)
    heads = {j: np.array([old(j)]) for _, j in PLANTED if j != UNCOVERED[1]}
    heads[NEWS[1]] = np.sort(np.array([old(NEWS[1]), _ts(b, NEWS[0], 120)]))
    prev_close = _ts(b, LATE[0] - 1, 0)
    heads[LATE[1]] = np.sort(np.array([old(LATE[1]), prev_close, _ts(b, LATE[0], 14)]))
    heads[EARN[1]] = np.sort(np.array([old(EARN[1]), _ts(b, EARN[0], 120)]))
    heads[OUTSIDE[1]] = np.sort(np.array([old(OUTSIDE[1]), _ts(b, OUTSIDE[0], 120)]))
    non = {FILED[1]: np.array([_ts(b, FILED[0], 300)]), NEWS[1]: np.array([_ts(b, NEWS[0], 60)])}
    allf = {**non, NONEWS[1]: np.array([_ts(b, NONEWS[0] - 3, 60)])}  # an 8-K, but days before the window
    complete = np.arange(len(b.dates)) >= complete_from
    complete[OUTSIDE[0]] = False
    return od.NewsInputs(headlines=heads, filings_non202=non, filings_all=allf,
                         earnings={EARN[1]: [EARN[0]], NEWS[1]: [NEWS[0] + 40]}, complete=complete,
                         windows=od.news_windows(b.dates))


@pytest.fixture(scope="module")
def market():
    b = _bars()
    m = mc.Market.from_bars(b, b.cols([s for s in b.symbols if s != "SPY"]), b.col("SPY"))
    return b, m, od.DailySignals(m, None, _news(b))


def _events(sig: od.DailySignals, version: str = "news_gap_week", **kw) -> set[tuple[int, int]]:
    spec = dataclasses.replace(od.VERSIONS[version], **kw)
    return {(k, j) for k, xs in sig.news_gap(spec).items() for j, _ in xs}


# ---------------------------------------------------------------- the frozen settings are the pre-registered ones


def test_the_versions_and_neighbours_are_the_pre_registered_ones():
    p = prereg.load("news_gap_week", ROOT, committed=False)
    assert set(p.neighbours) == set(od.NEIGHBOURS["news_gap_week"]) == {"move2", "relvol2", "hold10", "info8k"}
    assert p.status == "pre-registered"
    v = od.VERSIONS["news_gap_week"]
    assert (v.max_names, v.weight, v.fill, v.hold, v.move_sigma, v.rel_vol, v.news) == (
        10, 0.10, "close", 5, 1.0, 3.0, "headline")
    c = od.VERSIONS["news_gap_week_nonews"]
    assert dataclasses.replace(c, name=v.name, news="headline") == v and c.news == "none"
    assert od.RULE_VERSIONS["news_gap_week"] == ("news_gap_week", "news_gap_week_nonews")
    assert {"news_gap_week_nonews"} == od.CONTROLS
    nb = {s.name.split("~")[1]: s for s in od.neighbours("news_gap_week")}
    assert (nb["move2"].move_sigma, nb["relvol2"].rel_vol, nb["hold10"].hold, nb["info8k"].news) == (
        2.0, 2.0, 10, "filing")
    assert np.timedelta64(900, "s") == od.NEWS_LEAD


def test_the_news_window_runs_from_the_previous_close_to_15_minutes_before_this_close_early_closes_too():
    d = np.array(["2019-11-27", "2019-11-29", "2019-12-02"], dtype="datetime64[D]")  # 29 Nov: closes at 13:00 ET
    after, upto = od.news_windows(d)
    assert np.isnat(after[0])
    assert str(after[1]) == "2019-11-27T21:00:00" and str(upto[1]) == "2019-11-29T17:45:00"
    assert str(after[2]) == "2019-11-29T18:00:00" and str(upto[2]) == "2019-12-02T20:45:00"
    t = np.array(["2019-12-02T20:45:00"], dtype="datetime64[s]")
    assert od._any_in(t, after[2], upto[2]) and not od._any_in(t + np.timedelta64(1, "s"), after[2], upto[2])
    assert not od._any_in(np.array([after[2]]), after[2], upto[2])  # at the previous close: not after it


# ---------------------------------------------------------------- the jump


def test_jumps_match_a_plain_loop(market):
    b, m, sig = market
    mask, ex = sig.jumps(1.0, 3.0)
    c, v, spy = b.c, b.v, b.col("SPY")
    for k in (25, 200, *[k for k, _ in PLANTED]):
        for j in (0, 10, 11, 7, 3):
            prior = np.array([c[i, j] / c[i - 1, j] - 1.0 for i in range(k - 20, k)])
            x = c[k, j] / c[k - 1, j] - 1.0 - (c[k, spy] / c[k - 1, spy] - 1.0)
            want = bool(np.isfinite(prior).all() and np.isfinite(v[k - 20:k, j]).all() and x > 0
                        and x >= prior.std(ddof=1) and v[k, j] >= 3.0 * v[k - 20:k, j].mean())
            assert mask[k, j] == want, (k, j)
            if np.isfinite(x):
                assert ex[k, j] == pytest.approx(x)
    assert all(mask[k, j] for k, j in PLANTED)
    assert not mask[tc.JUMP]  # a 22% day on normal volume is no event


def test_a_jump_needs_the_volume_and_the_move_of_its_setting(market):
    b, m, sig = market
    assert NEWS in _events(sig) and NEWS not in _events(sig, rel_vol=5.0)
    assert NEWS not in _events(sig, move_sigma=50.0)


# ---------------------------------------------------------------- the news test, point in time


def test_the_headline_version_needs_a_headline_in_the_window_coverage_and_a_complete_month(market):
    b, m, sig = market
    ev = _events(sig)
    assert NEWS in ev
    assert LATE not in ev  # 14 minutes before the close is after the decision cut-off; the previous close is before
    assert NONEWS not in ev and FILED not in ev  # no headline in the window (an 8-K is not a headline)
    assert UNCOVERED not in ev  # the company has no stored headline at all (a former member, say)
    assert OUTSIDE not in ev  # its month is not a complete news month
    b2 = _bars()
    m2 = mc.Market.from_bars(b2, b2.cols([s for s in b2.symbols if s != "SPY"]), b2.col("SPY"))
    late = od.DailySignals(m2, None, _news(b2, complete_from=NEWS[0] + 1))
    assert NEWS not in _events(late)


def test_a_headline_or_filing_after_the_decision_time_never_counts(market):
    """Move every headline and filing of the planted days to 1 second after the cut-off (or to the next day): no
    headline event is left, and the decisions before them do not change."""
    b, m, sig = market
    nw = sig.news
    up = nw.windows[1]
    later = {j: np.sort(np.array([up[k] + np.timedelta64(1, "s") for k, jj in PLANTED if jj == j] + [t[0]]))
             for j, t in nw.headlines.items()}
    moved = dataclasses.replace(nw, headlines=later, filings_non202={
        j: t + np.timedelta64(86400, "s") for j, t in nw.filings_non202.items()})
    s2 = od.DailySignals(m, None, moved)
    assert {(k, j) for k, j in _events(s2) if (k, j) in PLANTED} == set()
    assert FILED in _events(sig, news="filing") and FILED not in _events(s2, news="filing")


def test_an_earnings_day_is_never_an_event_day(market):
    b, m, sig = market
    for news in ("headline", "filing", "none"):
        assert EARN not in _events(sig, news=news)
    nw = dataclasses.replace(sig.news, earnings={})
    assert EARN in _events(od.DailySignals(m, None, nw))  # the same jump without the report is an event


def test_the_control_picks_only_jumps_with_no_headline_and_no_8k(market):
    b, m, sig = market
    ctl = _events(sig, "news_gap_week_nonews")
    assert NONEWS in ctl  # its only 8-K is days before the window
    assert LATE in ctl  # its in-window headline came too late, the earlier one before the previous close
    assert NEWS not in ctl and FILED not in ctl and EARN not in ctl
    assert UNCOVERED not in ctl and OUTSIDE not in ctl  # no coverage, or an incomplete month: unknown, not "no news"
    nw = sig.news
    for k, j in ctl:
        a, u = nw.windows[0][k], nw.windows[1][k]
        assert not od._any_in(nw.headlines.get(j), a, u) and not od._any_in(nw.filings_all.get(j), a, u)
        assert len(nw.headlines.get(j, ())) and nw.complete[k]
    assert not ctl & _events(sig)


def test_info8k_uses_non_202_filings_on_every_member_and_month(market):
    b, m, sig = market
    ev = _events(sig, news="filing")
    assert FILED in ev and NEWS in ev  # NEWS also has a non-2.02 8-K in its window
    assert NONEWS not in ev and LATE not in ev
    b2 = _bars()
    m2 = mc.Market.from_bars(b2, b2.cols([s for s in b2.symbols if s != "SPY"]), b2.col("SPY"))
    nw = dataclasses.replace(_news(b2), headlines={}, complete=np.zeros(len(b2.dates), dtype=bool))
    assert FILED in _events(od.DailySignals(m2, None, nw), news="filing")  # no coverage or month needed
    with pytest.raises(ValueError):
        od.DailySignals(m2, None, None).news_gap(od.VERSIONS["news_gap_week"])


# ---------------------------------------------------------------- the trades


def test_it_buys_the_close_after_the_event_day_and_sells_five_sessions_later(market):
    b, m, sig = market
    book, rule = od.run_daily(m, sig, POL, od.VERSIONS["news_gap_week"], tc.I0 - 5, len(b.dates) - 1)
    got = [(b.index_from(t.date), b.col(t.symbol), t) for t in book.trades]
    assert {(i - 1, j) for i, j, _ in got} == {(k, j) for k, j in _events(sig) if k >= tc.I0 - 5}
    for i, _j, t in got:
        assert t.entry_at == "close" and t.reason == "max_hold" and b.index_from(t.exit_date) - i == 5
    nb = {s.name.split("~")[1]: s for s in od.neighbours("news_gap_week")}
    b10, _ = od.run_daily(m, sig, POL, nb["hold10"], tc.I0 - 5, len(b.dates) - 1)
    assert all(b.index_from(t.exit_date) - b.index_from(t.date) == 10 for t in b10.trades)


def test_more_events_than_slots_take_the_largest_excess_return_first():
    extra = tuple((NEWS[0], j) for j in range(20, 32))
    b = _bars(extra)
    m = mc.Market.from_bars(b, b.cols([s for s in b.symbols if s != "SPY"]), b.col("SPY"))
    nw = _news(b)
    heads = dict(nw.headlines)
    for _, j in extra:
        heads[j] = np.array([_ts(b, NEWS[0], 100)])
    sig = od.DailySignals(m, None, dataclasses.replace(nw, headlines=heads))
    rule = od.DailyRule(sig, POL, od.VERSIONS["news_gap_week"], "close")
    cands = rule.candidates(NEWS[0])
    _, ex = sig.jumps(1.0, 3.0)
    assert len(cands) == 13 and cands == sorted(cands, key=lambda j: (-ex[NEWS[0], j], j))
    book, _ = od.run_daily(m, sig, POL, od.VERSIONS["news_gap_week"], NEWS[0] - 2, NEWS[0] + 3)
    assert {b.col(t.symbol) for t in book.trades} == set(cands[:10])


def test_future_bars_and_news_do_not_change_earlier_decisions(market):
    b, m, sig = market
    t = 335
    c = b.c.copy()
    c[t:] *= np.random.default_rng(1).uniform(0.7, 1.3, c[t:].shape)
    o = np.vstack([b.o[:t], c[t - 1:-1]])
    b2 = DailyBars.make(b.dates, b.symbols, o, np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c, b.v * 1.0)
    b2.v[t:] *= 7.0
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(b2, k)[:330, 7] = np.nan
    m2 = mc.Market.from_bars(b2, b2.cols([s for s in b.symbols if s != "SPY"]), b2.col("SPY"))
    nw = sig.news
    more = {j: np.sort(np.r_[x, nw.windows[1][t:] - np.timedelta64(60, "s")]) for j, x in nw.headlines.items()}
    sig2 = od.DailySignals(m2, None, dataclasses.replace(nw, headlines=more))
    for version in ("news_gap_week", "news_gap_week_nonews"):
        def orders(mk, sg, version=version):
            book, _ = od.run_daily(mk, sg, POL, od.VERSIONS[version], tc.I0, t - 1)
            return [(x.symbol, str(x.date), x.qty) for x in book.trades if x.reason != "end_of_data"]
        assert orders(m, sig) == orders(m2, sig2)


# ---------------------------------------------------------------- against the control


def test_clearly_above_the_control_needs_a_higher_sharpe_and_welch_t_of_two():
    rng = np.random.default_rng(0)
    hi, lo = rng.normal(0.02, 0.03, 60), rng.normal(0.0, 0.03, 60)
    t = od.welch_t(hi, lo)
    assert t == pytest.approx((hi.mean() - lo.mean()) / np.sqrt(hi.var(ddof=1) / 60 + lo.var(ddof=1) / 60))
    assert t > 2.0
    up, down = {"sharpe": 0.9}, {"sharpe": 0.4}
    assert od.against_control(up, down, hi, lo)["verdict"] == "clearly above"
    assert od.against_control(down, up, hi, lo)["verdict"] == "not clearly above"  # the Sharpe must be higher too
    assert od.against_control(up, down, lo, hi)["verdict"] == "not clearly above"
    assert od.against_control(up, down, hi[:29], lo)["verdict"] == "cannot be judged"
    assert od.welch_t(hi[:1], lo) is None


def test_trade_excess_is_net_return_minus_spy_over_the_holding_sessions(market):
    b, m, sig = market
    book, _ = od.run_daily(m, sig, POL, od.VERSIONS["news_gap_week"], tc.I0 - 5, len(b.dates) - 1)
    x = od.trade_excess(book, m)
    spy = b.col("SPY")
    assert len(x) == len(book.trades) > 0
    for t, got in zip(book.trades, x, strict=True):
        a, e = b.index_from(t.date), b.index_from(t.exit_date)
        assert got == pytest.approx(t.pnl / (t.qty * t.entry) - (b.c[e, spy] / b.c[a, spy] - 1.0))
