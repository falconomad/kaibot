"""Issue #127: the momentum core (trend_momentum, mom_12_1_band) in the multi-day research harness of #113.
Small seeded synthetic bars only, no network."""

import contextlib
import dataclasses
import io
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

from kaibot.replay import multiday_core as mc
from kaibot.replay.multiday import Caps, DailyBars, Engine, indicators
from kaibot.replay.multiday_rules import EXPERIMENTS, book_digest
from kaibot.replay.sim import half_spread

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core as core_script  # noqa: E402
import test_multiday as tm  # noqa: E402  (the #113 fixtures: synthetic market, scripted rule, helpers)

# ---------------------------------------------------------------- 0. the first-look rules are pinned

# Written down from the engine of #113 as merged (main @ 45f0dc6), BEFORE the engine class was extended for #127:
# trades, final equity, shares traded, sessions held and spread + impact paid by each first-look rule's fixed
# setting on the synthetic market. Tolerances are far below one share or one cent of drift.
PINNED = {
    "trend_highs": (38, 112737.9158, 2572.0, 889, 107.0224),
    "rel_strength": (42, 119292.9402, 3340.0, 1470, 121.6304),
    "winners_run": (199, 135846.5669, 16784.2462, 304, 854.7216),
    "overnight_spy": (264, 99887.7043, 5216.0, 264, 774.7487),
}


@pytest.mark.parametrize("name", list(EXPERIMENTS))
def test_first_look_rules_are_unchanged_by_the_engine_extensions(name):
    b, stocks = tm.market()
    eng = tm.account(name, b, stocks)
    tr = eng.book.trades
    n, equity, shares, held, costs = PINNED[name]
    assert len(tr) == n and sum(t.hold_days for t in tr) == held
    assert math.isclose(eng.equity, equity, abs_tol=1e-3)
    assert math.isclose(sum(t.qty for t in tr), shares, abs_tol=1e-3)
    assert math.isclose(sum(t.cost_spread + t.cost_impact for t in tr), costs, abs_tol=1e-3)


# ---------------------------------------------------------------- 1. the engine extensions


def test_cost_multiplier_scales_spread_and_impact_and_leaves_fees():
    b = tm.flat(30)
    one, two, zero = (tm.run(b, tm.Script(opens=[(20, 0, {"max_hold": 2})]), cost_mult=m).book.trades[0]
                      for m in (1.0, 2.0, 0.0))
    assert one.qty == two.qty == zero.qty and all(tm.split_ok(t) for t in (one, two, zero))
    assert math.isclose(two.cost_spread, 2 * one.cost_spread) and math.isclose(two.cost_impact, 2 * one.cost_impact)
    assert math.isclose(two.cost_fees, one.cost_fees, rel_tol=1e-3) and one.cost_fees > 0
    assert zero.cost_spread == 0 and abs(zero.cost_impact) < 1e-9 and math.isclose(zero.pnl, -zero.cost_fees)
    assert math.isclose(one.pnl - two.pnl, one.cost_spread + one.cost_impact, rel_tol=1e-3)
    assert one.entry - 100.0 == pytest.approx((two.entry - 100.0) / 2)  # the fills themselves moved


class Resizer:
    """Buys 100 shares at the close of session 20 (ordered the evening before), cuts to 60 on 22, adds up to 90 on
    24 and sells at the close of 26."""

    name = "resizer"

    def __init__(self, shares: float = 100.0, at: str = "close"):
        self.shares, self.at, self.got = shares, at, {}

    def at_open(self, i, eng, cfg, eq):
        if self.at == "open":
            self.act(i, eng, eq)

    def at_close(self, i, eng, cfg, eq):
        if self.at == "close":
            self.act(i, eng, eq)

    def act(self, i, eng, eq):
        if i == 20:
            self.got[i] = eng.enter(i, 0, eq, "t", tm.CAPS, at=self.at, shares=self.shares, prev_close=True)
        elif i in (22, 24):
            self.got[i] = eng.adjust(i, 0, 60 if i == 22 else 90, eq, tm.CAPS, at=self.at)
        elif i == 26:
            self.got[i] = eng.sell_close(i, 0, "rank") if self.at == "close" else eng.sell_open(i, 0, "rank")


@pytest.mark.parametrize("at", ["close", "open"])
def test_partial_buys_and_sells_keep_the_books_exact(at):
    b = tm.flat(30)
    tm.set_day(b, 22, 0, 104.0, 105.0, 103.0, 104.0)
    tm.set_day(b, 24, 0, 98.0, 99.0, 97.0, 98.0)
    tm.set_day(b, 26, 0, 101.0, 102.0, 100.0, 101.0)
    rule = Resizer(at=at)
    eng = tm.run(b, rule)
    assert rule.got == {20: 100.0, 22: -40.0, 24: 30.0, 26: True}
    cut, out = eng.book.trades
    assert (cut.reason, cut.qty, cut.hold_days) == ("resize", 40.0, 2) and (out.reason, out.qty) == ("rank", 90.0)
    assert tm.split_ok(cut) and tm.split_ok(out) and cut.cost_spread > 0 and out.cost_spread > 0
    assert math.isclose(cut.gross_pnl, 40 * 4.0) and math.isclose(out.gross_pnl, 60 * 1.0 + 30 * 3.0)
    assert out.entry == pytest.approx((60 * cut.entry + 30 * 98.0) / 90, rel=1e-3)  # the share-weighted fill
    assert math.isclose(cut.pnl + out.pnl, eng.equity - 100_000.0, abs_tol=1e-6)  # the trades explain the equity
    assert math.isclose(eng.book.bought - eng.book.sold + cut.cost_fees + out.cost_fees, -(cut.pnl + out.pnl),
                        abs_tol=1e-6)
    assert [r["positions"] for r in eng.book.daily][5:12] == [1, 1, 1, 1, 1, 1, 0]
    daily = pd.DataFrame(eng.book.daily)
    assert np.allclose(daily["start"].to_numpy()[1:], daily["end"].to_numpy()[:-1])


def test_the_sizing_hook_obeys_the_caps_and_never_reads_the_fill_day():
    qty = []
    for close_20 in (100.0, 112.0):  # the print the order fills at
        b = tm.flat(30, symbols=("AAA", "BBB"))
        b.c[20, 0] = close_20
        eng = tm.run(b, Resizer(shares=1e9), i1=20)
        qty.append(eng.pos[0].qty)
        assert eng.pos[0].fill > close_20 and eng.pos[0].entry_at == "close"
    assert qty == [150.0, 150.0]  # 15% of equity at the PREVIOUS close, whatever the fill print
    b = tm.flat(30)
    small = tm.run(b, Resizer(shares=7.9), i1=20)
    assert small.pos[0].qty == 7.0  # whole shares, never more than asked
    eng = tm.run(b, Resizer(shares=150.0), i1=24)  # 22: sell down to 60; 24: add up to 90
    assert eng.pos[0].qty == 90.0
    capped = Engine(b, indicators(b), tm.POL, Resizer(shares=150.0), lambda i: None)
    capped.run(15, 23, close_at_end=False)
    assert capped.adjust(24, 0, 1e9, 100_000.0, tm.CAPS) == 90.0 and capped.pos[0].qty == 150.0  # the 15% cap
    assert capped.adjust(25, 0, 150.0, 100_000.0, tm.CAPS) == 0.0  # already there
    tight = Engine(b, indicators(b), tm.POL, Resizer(shares=150.0), lambda i: None)
    tight.run(15, 23, close_at_end=False)
    assert tight.adjust(24, 0, 150.0, 100_000.0, Caps(max_exposure_pct=7.0)) == 10.0  # 7% of equity = 70 shares


def test_a_sale_at_the_close_waits_for_a_bar_and_a_resize_without_one_is_dropped():
    b = tm.flat(30)
    for k in ("o", "h", "l", "c", "c1555"):
        getattr(b, k)[[22, 26], 0] = np.nan
    rule = Resizer()
    eng = tm.run(b, rule, i1=26)
    assert rule.got == {20: 100.0, 22: 0.0, 24: -10.0, 26: False} and eng.pos[0].qty == 90.0


# ---------------------------------------------------------------- 2. signals


def core_market(seed: int = 9, n: int = 560, n_stocks: int = 80) -> tuple[DailyBars, list[str]]:
    """Eighty random walks with a common market factor and different drifts, SPY last. SPY falls for 40 sessions
    (so the 200-day filter switches off and on), one strong stock jumps 22% in a day, one trades under $5, one
    lists late. Made for plumbing, not for performance."""
    rng = np.random.default_rng(seed)
    stocks = [f"S{k:02d}" for k in range(n_stocks)]
    mkt = rng.normal(0.0006, 0.007, n)
    mkt[400:440] -= 0.004
    drift = np.linspace(-0.0005, 0.0016, n_stocks)
    r = drift + np.outer(mkt, rng.uniform(0.7, 1.4, n_stocks)) + rng.normal(0, 0.008, (n, n_stocks))
    r[JUMP] = 0.20  # a 22% day in a stock the trend rule holds at the time
    c = np.exp(np.cumsum(np.column_stack([r, mkt]), axis=0)) * np.r_[np.linspace(30, 240, n_stocks), 400.0]
    c[:, 3] *= 3.0 / c[:, 3].max()  # never above $3: fails the universe filter
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.003, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, c.shape)))
    b = DailyBars.make(tm.sessions(n, "2022-01-03"), [*stocks, "SPY"], o, h, lo, c, np.full(c.shape, 8e6))
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(b, k)[:330, 7] = np.nan  # S07 has no bars before session 330
    return b, stocks


JUMP = (340, 76)
SPY = 80
I0 = 280  # the first test session of the synthetic runs (the signals need 273 sessions)


@pytest.fixture(scope="module")
def core():
    b, stocks = core_market()
    ind = indicators(b)
    return b, stocks, ind, mc.Signals(b, ind, b.cols(stocks), b.col("SPY"))


def test_regression_score_is_the_annualised_slope_times_r_squared(core):
    b, stocks, ind, sig = core
    for n in (60, 90, 125):
        sc = sig.trend_score(n)
        assert np.isnan(sc[:n - 1]).all() and np.isfinite(sc[n - 1:, 0]).all()
        for t, j in ((n - 1, 0), (300, 12), (559, 79)):
            y = np.log(sig.c[t - n + 1:t + 1, j])
            slope, icpt = np.polyfit(np.arange(n), y, 1)
            r2 = 1 - ((y - (slope * np.arange(n) + icpt)) ** 2).sum() / ((y - y.mean()) ** 2).sum()
            assert sc[t, j] == pytest.approx((math.exp(slope) ** 250 - 1) * r2, rel=1e-9)
    assert np.isnan(sig.trend_score(90)[330 + 88, 7]) and np.isfinite(sig.trend_score(90)[330 + 89, 7])
    line = np.exp(0.001 * np.arange(120))[:, None]  # a perfect exponential: R-squared 1
    assert mc.regression_score(line, 90)[-1, 0] == pytest.approx(math.exp(0.25) - 1, rel=1e-9)


def test_atr_averages_gap_filter_and_universe_filter(core):
    b, stocks, ind, sig = core
    t, j = 400, 10
    prev = b.c[t - 20:t, j]
    tr = np.maximum.reduce([b.h[t - 19:t + 1, j] - b.l[t - 19:t + 1, j], np.abs(b.h[t - 19:t + 1, j] - prev),
                            np.abs(b.l[t - 19:t + 1, j] - prev)])
    assert sig.atr20[t, j] == pytest.approx(tr.mean())
    assert sig.below_avg[t, j] == (not b.c[t, j] >= b.c[t - 99:t + 1, j].mean())
    assert sig.below_avg[:99].all()  # no 100-day average yet: not buyable
    t0, jj = JUMP
    assert not sig.big_move[t0 - 1, jj] and sig.big_move[t0:t0 + 90, jj].all() and not sig.big_move[t0 + 90, jj]
    assert not sig.ok[:, 3].any() and not sig.ok[:, SPY].any()  # under $5; SPY is not a stock of the universe
    assert not sig.ok[:344, 7].any() and sig.ok[350:, 7].all()  # 15 sessions of dollar volume first
    spy = b.c[:, SPY]
    assert sig.market_on[450] == (spy[450] > spy[251:451].mean()) and not sig.market_on[:199].any()
    on = sig.market_on[I0:]
    assert on.any() and (~on).any()  # the fixture has both states in the test window


def test_momentum_score_is_the_average_percentile_rank_of_three_skip_month_returns(core):
    b, stocks, ind, sig = core
    sc = sig.momentum_score((126, 189, 252))
    t = 400
    have = np.flatnonzero(np.isfinite(sc[t]))
    assert 7 not in have and 3 not in have and SPY not in have and len(have) == 78  # no history / filtered / SPY
    assert np.isnan(sc[:252]).all()
    rets = np.array([sig.c[t - 21, have] / sig.c[t - n, have] - 1 for n in (126, 189, 252)])
    ranks = (rets.argsort(axis=1).argsort(axis=1) + 1) / len(have)
    assert np.allclose(sc[t, have], ranks.mean(axis=0))
    only12 = sig.momentum_score((252,))
    assert np.allclose(only12[t, have], ranks[2])


def test_decision_days_are_wednesdays_or_the_next_session_and_month_starts(core):
    b, stocks, ind, sig = core
    wd = np.array([pd.Timestamp(d).weekday() for d in b.dates])
    assert (wd[sig.weekly] == 2).all() and sig.weekly.sum() == len(set(sig.week.tolist()))
    keep = np.ones(len(b.dates), dtype=bool)
    gone = int(np.flatnonzero(sig.weekly)[40])
    keep[gone] = False  # a Wednesday without a session
    b2 = b.keep_sessions(keep)
    s2 = mc.Signals(b2, indicators(b2), b2.cols(stocks), b2.col("SPY"))
    assert s2.weekly[gone] and pd.Timestamp(b2.dates[gone]).weekday() == 3  # that week decides on Thursday
    assert s2.weekly.sum() == sig.weekly.sum()
    first = np.flatnonzero(sig.monthly)
    months = b.dates.astype("datetime64[M]")
    assert all(months[i] != months[i - 1] for i in first) and len(first) == len(set(months.tolist())) - 1
    assert sig.week[sig.weekly][1] - sig.week[sig.weekly][0] == 1


# ---------------------------------------------------------------- 3. the two rules


def account(core, version: str, i1: int | None = None, **kw):
    b, stocks, ind, sig = core
    return mc.run_rule(b, ind, tm.POL, sig, mc.VERSIONS[version], I0, len(b.dates) - 1 if i1 is None else i1, **kw)


def test_the_frozen_settings_and_neighbours_are_the_pre_registered_ones():
    a, m, mn = (mc.VERSIONS[k] for k in ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"))
    assert (a.reg_window, a.enter_top, a.exit_top, a.max_names, a.atr_risk, a.market_filter, a.resize) == (
        90, 0.20, 0.20, 30, 0.001, True, True)
    assert (m.lookbacks, m.enter_top, m.exit_top, m.max_names, m.atr_risk, m.market_filter, m.resize) == (
        (126, 189, 252), 0.10, 0.20, 20, 0.001, True, False)
    assert dataclasses.replace(m, name=mn.name, market_filter=False) == mn
    assert {s.name.split("~")[1]: {k: v for k, v in s.params().items() if v != a.params()[k] and k != "name"}
            for s in mc.neighbours("trend_momentum")} == {
        "reg60": {"reg_window": 60}, "reg125": {"reg_window": 125}, "exit30": {"exit_top": 0.30},
        "nofilter": {"market_filter": False}, "risk15": {"atr_risk": 0.0015}}
    assert {s.name.split("~")[1]: {k: v for k, v in s.params().items() if v != mn.params()[k] and k != "name"}
            for s in mc.neighbours("mom_12_1_band_nofilter")} == {
        "only12": {"lookbacks": (252,)}, "only6": {"lookbacks": (126,)}, "exit30": {"exit_top": 0.30},
        "names10": {"max_names": 10}, "risk15": {"atr_risk": 0.0015}}
    assert (mc.SMA_STOCK, mc.SMA_MARKET, mc.MOVE_WINDOW, mc.MOVE_MAX, mc.ATR_SESSIONS, mc.RESIZE_BUFFER,
            mc.ANNUAL_SESSIONS, mc.DECISION_WEEKDAY) == (100, 200, 90, 0.15, 20, 0.10, 250, 2)
    assert mc.FILLS == {"base": ("close", 1.0), "stress": ("open", 2.0)} and mc.COSTS == (1.0, 2.0, 3.0)
    assert mc.EXPOSURES == (30.0, 60.0, 100.0)
    for rule in ("trend_momentum", "mom_12_1_band"):  # the pre-registration exists and names the same numbers
        text = (ROOT / "docs" / "prereg" / f"{rule}.md").read_text()
        for needle in ("12 November 2026", "PROVISIONAL", "S71", "S83", "S84", "who is on the other side",
                       "Neighbours for the plateau check", "Fill model", "Benchmarks", "at least 6 of 10 calendar",
                       "90th percentile of matched random entries", "0.001 / ATR(20"):
            assert needle in text, (rule, needle)


def test_trend_momentum_follows_the_published_rules(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "trend_momentum")
    spec = rule.spec
    buys = [o for o in rule.orders if o[1] == "buy"]
    sells = [o for o in rule.orders if o[1] == "sell"]
    resizes = [o for o in rule.orders if o[1] == "resize"]
    assert len(buys) > 30 and len(sells) > 20 and len(resizes) > 10
    assert all(sig.weekly[i] for i, *_ in rule.orders)  # Wednesdays only
    assert {r for *_, r in sells} == {"rank", "trend", "gap"}
    for i, _, j, shares in buys:
        k = i - 1
        order = rule.ranking(k)
        top = order[:math.ceil(0.20 * len(order))].tolist()
        assert sig.market_on[k] and j in top and not sig.below_avg[k, j] and not sig.big_move[k, j] and sig.ok[k, j]
        assert shares <= math.floor(book.daily[i - I0]["start"] * 0.001 / sig.atr20[k, j])
    for i, _, j, why in sells:
        k = i - 1
        order = rule.ranking(k)
        top = order[:math.ceil(0.20 * len(order))].tolist()
        assert why == ("gap" if sig.big_move[k, j] else "trend" if sig.below_avg[k, j] else "rank")
        assert why != "rank" or j not in top
    assert all(sig.week[i] % 2 == 0 for i, *_ in resizes)  # every second week
    off = [i for i in np.flatnonzero(sig.weekly) if i > I0 and not sig.market_on[i - 1]]
    assert len(off) > 5 and not {i for i, *_ in buys} & set(off)  # filter off: no buys ...
    held_through = [book.daily[i - I0]["positions"] for i in off]
    assert max(held_through) > 0  # ... but holdings stay until their own exit
    assert {t.reason for t in book.trades} <= {"rank", "trend", "gap", "resize", "end_of_data"}  # no stop, no clock
    assert max(r["positions"] for r in book.daily) <= spec.max_names
    assert all(tm.split_ok(t) for t in book.trades)
    assert math.isclose(sum(t.pnl for t in book.trades), book.equity - 100_000.0, abs_tol=1e-4)
    assert all(t.rule == "trend_momentum" and t.config == "trend_momentum" for t in book.trades)
    assert sum(rule.entries.values()) == len({(t.symbol, str(t.date)) for t in book.trades})


def test_resize_trades_only_beyond_the_buffer_and_toward_the_target(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "trend_momentum", exposure_pct=60.0)
    eng_rule = mc.CoreRule(sig, tm.POL, rule.spec, "close", 60.0)
    i = int(next(i for i in np.flatnonzero(sig.weekly) if i > I0 + 40 and sig.week[i] % 2 == 0))
    k, eq = i - 1, 1_000_000.0
    j = int(next(j for j in rule.ranking(k) if not sig.below_avg[k, j] and not sig.big_move[k, j]))
    t = mc.target_shares(sig, tm.POL, rule.spec, k, j, eq)
    px = float(sig.c[k, j])
    assert t == min(math.floor(eq * 0.001 / sig.atr20[k, j]), math.floor(eq * 0.15 / px)) and t > 20
    inside, above, below = ({j: (round(t * f), px)} for f in (1.08, 1.30, 0.70))
    assert eng_rule.plan(i, inside, eq)["resize"] == []
    assert eng_rule.plan(i, above, eq)["resize"] == [(j, t)] == eng_rule.plan(i, below, eq)["resize"]
    assert eng_rule.plan(i + 5, above, eq)["resize"] == []  # the odd week: no resize
    held = round(t * 0.70)
    tight = mc.CoreRule(sig, tm.POL, rule.spec, "close", (held + 3.5) * px / eq * 100)  # room for 3 more shares
    p = tight.plan(i, {j: (held, px)}, eq)
    assert p["resize"] == [(j, held + 3.0)]  # cut to the room left under the exposure cap
    assert (held + 3) * px + sum(n * sig.c[k, jj] for jj, n in p["buy"]) <= (held + 3.5) * px
    assert eng_rule.plan(i, {j: (held, px)}, eq)["sell"] == {} and len(eng_rule.plan(i, {}, eq)["buy"]) > 3


def test_a_buy_cut_to_the_room_left_is_the_last_buy_of_the_decision(core):
    """Pre-registered choice 10 (trend) / 8 (band): a buy that does not fit in full under the exposure cap is cut
    to the room left, in whole shares, and buying stops there."""
    b, stocks, ind, sig = core
    spec, eq = mc.VERSIONS["mom_12_1_band_nofilter"], 100_000.0
    for i in np.flatnonzero(sig.monthly)[::-1]:  # a day where a later candidate is cheaper than the left-over room
        k = int(i) - 1
        full = mc.CoreRule(sig, tm.POL, spec, "close").plan(int(i), {}, eq)["buy"]
        if len(full) >= 3 and full[1][1] >= 4 and sig.c[k, full[2][0]] < 0.9 * sig.c[k, full[1][0]]:
            break
    else:
        pytest.fail("no suitable decision day in the synthetic market")
    (j0, n0), (j1, n1) = full[0], full[1]
    room = n0 * sig.c[k, j0] + (n1 // 2 + 0.9) * sig.c[k, j1]  # half of the second buy, and 0.9 of a share more
    cut = mc.CoreRule(sig, tm.POL, spec, "close", room / eq * 100).plan(int(i), {}, eq)["buy"]
    assert cut == [(j0, n0), (j1, float(n1 // 2))]  # the left-over room would buy a cheaper stock: it does not


def test_a_sale_still_waiting_for_a_bar_is_kept_on_the_next_decision_day(core):
    """A holding whose sale had no bar waits for its next bar, even when the next decision day would keep it."""
    b, stocks, ind, sig = core
    rule = mc.CoreRule(sig, tm.POL, mc.VERSIONS["mom_12_1_band_nofilter"], "close")
    i = int(np.flatnonzero(sig.monthly)[-3])
    j = int(rule.ranking(i - 1)[0])  # the best stock: the rule itself would keep it
    held = {j: (10.0, float(sig.c[i - 1, j]))}
    assert rule.plan(i, held, 100_000.0)["sell"] == {}
    rule.waiting = {j: "rank"}
    assert rule.plan(i, held, 100_000.0)["sell"] == {j: "rank"}


def test_momentum_band_enters_in_the_top_tenth_and_stays_until_out_of_the_top_fifth(core):
    b, stocks, ind, sig = core
    for version, filtered in (("mom_12_1_band", True), ("mom_12_1_band_nofilter", False)):
        book, rule = account(core, version)
        buys = [o for o in rule.orders if o[1] == "buy"]
        sells = [o for o in rule.orders if o[1] == "sell"]
        assert len(buys) >= 5 and len(sells) >= 3 and not [o for o in rule.orders if o[1] == "resize"]
        assert all(sig.monthly[i] for i, *_ in rule.orders)  # the first session of a month only
        for i, _, j, _shares in buys:
            order = rule.ranking(i - 1)
            assert j in order[:math.ceil(0.10 * len(order))].tolist() and (sig.market_on[i - 1] or not filtered)
        for i, _, j, why in sells:
            order = rule.ranking(i - 1)
            assert why == "rank" and j not in order[:math.ceil(0.20 * len(order))].tolist()
        off_buys = [i for i, *_ in buys if not sig.market_on[i - 1]]
        assert bool(off_buys) == (not filtered)
        assert {t.reason for t in book.trades} <= {"rank", "end_of_data"}
        assert max(r["positions"] for r in book.daily) <= 20 and all(tm.split_ok(t) for t in book.trades)
        held = {}
        for t in book.trades:  # kept while in the top 20%: every month start between entry and exit
            held.setdefault(t.symbol, []).append((t.date, t.exit_date))
        for sym, spans in held.items():
            for d0, d1 in spans:
                for i in np.flatnonzero(sig.monthly & (b.dates > d0) & (b.dates < d1)):
                    order = rule.ranking(i - 1)
                    assert b.col(sym) in order[:math.ceil(0.20 * len(order))].tolist()
    few = mc.CoreRule(sig, tm.POL, dataclasses.replace(mc.VERSIONS["mom_12_1_band_nofilter"], max_names=2), "close")
    i = int(np.flatnonzero(sig.monthly)[-3])
    assert len(few.plan(i, {}, 100_000.0)["buy"]) == 2  # best first, up to the number of names


def test_stress_fills_at_the_next_open_at_twice_the_cost_and_base_at_the_next_close(core):
    b, stocks, ind, sig = core
    base, rb = account(core, "mom_12_1_band_nofilter", exposure_pct=60.0)
    stress, rs = account(core, "mom_12_1_band_nofilter", exposure_pct=60.0, fill="stress")
    assert {t.entry_at for t in base.trades} == {"close"} and {t.entry_at for t in stress.trades} == {"open"}
    first = min(rb.entries)
    assert rb.orders[0][0] == rs.orders[0][0] == first  # the same decision ...
    assert [o for o in rb.orders if o[0] == first] == [o for o in rs.orders if o[0] == first]  # ... and orders
    tb = next(t for t in base.trades if t.date == b.dates[first])
    ts = next(t for t in stress.trades if t.date == b.dates[first] and t.symbol == tb.symbol)
    j = b.col(tb.symbol)
    assert tb.entry > b.c[first, j] and ts.entry > b.o[first, j]
    assert ts.entry / b.o[first, j] - 1 > 1.9 * (tb.entry / b.c[first, j] - 1) * 0.9  # doubled spread and impact
    half = half_spread(float(b.o[first, j]))
    assert ts.entry - b.o[first, j] >= 2 * half and tb.entry - b.c[first, j] < 2 * half_spread(float(b.c[first, j]))
    costly, _ = account(core, "mom_12_1_band_nofilter", exposure_pct=60.0, cost=3.0)
    paid = [sum(t.cost_spread + t.cost_impact for t in x.trades) for x in (base, costly)]
    assert 2.5 * paid[0] < paid[1] < 3.5 * paid[0]


def test_a_holding_without_a_bar_on_the_fill_day_is_sold_when_it_trades_again(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "trend_momentum")
    i, _, j, why = next(o for o in rule.orders if o[1] == "sell" and o[0] + 2 < len(b.dates))
    hole, _ = core_market()
    for k in ("o", "h", "l", "c", "c1555", "v", "v_first", "v_last"):
        getattr(hole, k)[i, j] = np.nan  # no bar on the day the sale should fill
    ind2 = indicators(hole)
    book2, rule2 = mc.run_rule(hole, ind2, tm.POL, mc.Signals(hole, ind2, hole.cols(stocks), hole.col("SPY")),
                               mc.VERSIONS["trend_momentum"], I0, len(b.dates) - 1)
    assert (i, "sell", j, why) in rule2.orders
    late = [t for t in book2.trades if t.symbol == b.symbols[j] and t.exit_date == b.dates[i + 1]]
    assert len(late) == 1 and late[0].reason == why
    assert not [t for t in book2.trades if t.symbol == b.symbols[j] and t.exit_date == b.dates[i]]


# ---------------------------------------------------------------- 4. no look-ahead


def shifted(t: int, seed: int) -> DailyBars:
    """The same market, except that session t and everything after it is different (prices x factors of about
    5%, volumes x 3). A decision for session t is made the evening before, so nothing of t may matter."""
    rng = np.random.default_rng(seed)
    m, _ = core_market()
    f = np.exp(rng.normal(0, 0.05, (len(m.dates) - t, len(m.symbols))))
    for k in ("o", "h", "l", "c", "c1555"):
        getattr(m, k)[t:] *= f
    for k in ("v", "v_first", "v_last"):
        getattr(m, k)[t:] *= 3.0
    return m


def decided(b: DailyBars, stocks: list[str], version: str, fill: str, exposure: float, t: int) -> dict:
    """The account run through session t: everything decided up to and including t, and everything filled before."""
    ind = indicators(b)
    sig = mc.Signals(b, ind, b.cols(stocks), b.col("SPY"))
    where, factor = mc.FILLS[fill]
    rule = mc.CoreRule(sig, tm.POL, mc.VERSIONS[version], where, exposure)
    eng = Engine(b, ind, tm.POL, rule, lambda i: None, cost_mult=factor)
    eng.run(I0, t - 1, close_at_end=False)
    held = sorted((j, p.qty, round(p.fill, 9)) for j, p in eng.pos.items())
    equity = [r["end"] for r in eng.book.daily]
    done = [(x.symbol, x.reason, x.qty, str(x.exit_date), round(x.exit, 9)) for x in eng.book.trades]
    eng.step(t)
    return {"orders": list(rule.orders), "held": held, "equity": equity, "done": done, "at_t": [
        o for o in rule.orders if o[0] == t]}


@pytest.mark.parametrize("version", list(mc.VERSIONS))
@pytest.mark.parametrize("fill", ["base", "stress"])
@settings(max_examples=8, deadline=None)
@given(data=st.data(), seed=st.integers(0, 10_000), exposure=st.sampled_from([30.0, 60.0, 100.0]))
def test_future_bars_do_not_change_a_decision(core, version, fill, data, seed, exposure):
    """Property: change every price and volume of session t and of all later sessions, and the orders decided
    for session t (which stocks, how many shares, which sales and resizes) and everything before are the same."""
    b, stocks, ind, sig = core
    days = [int(i) for i in np.flatnonzero(sig.decision_days(mc.VERSIONS[version])) if I0 + 5 < i < len(b.dates) - 1]
    t = data.draw(st.sampled_from(days))
    want = decided(b, stocks, version, fill, exposure, t)
    got = decided(shifted(t, seed), stocks, version, fill, exposure, t)
    assert got == want


def test_the_shift_changes_later_decisions_and_the_decision_days_have_orders(core):
    b, stocks, ind, sig = core
    for version in mc.VERSIONS:
        full = decided(b, stocks, version, "base", 100.0, len(b.dates) - 2)
        with_orders = {o[0] for o in full["orders"]}
        assert len(with_orders) >= 8
        t = sorted(with_orders)[len(with_orders) // 2]
        assert decided(b, stocks, version, "base", 100.0, t)["at_t"]  # the property test compares real orders
        other = decided(shifted(t, 1), stocks, version, "base", 100.0, len(b.dates) - 2)
        assert other["orders"] != full["orders"]
        assert [o for o in other["orders"] if o[0] <= t] == [o for o in full["orders"] if o[0] <= t]


def test_signals_at_t_use_sessions_up_to_t(core):
    b, stocks, ind, sig = core
    m = shifted(400, 3)
    ind2 = indicators(m)
    s2 = mc.Signals(m, ind2, m.cols(stocks), m.col("SPY"))
    for name in ("ok", "atr20", "below_avg", "big_move", "market_on", "weekly", "monthly", "week"):
        x, y = getattr(sig, name), getattr(s2, name)
        assert np.array_equal(x[:400], y[:400], equal_nan=True), name
    for spec in (*mc.VERSIONS.values(), *mc.neighbours("trend_momentum"), *mc.neighbours("mom_12_1_band")):
        assert np.array_equal(sig.score(spec)[:400], s2.score(spec)[:400], equal_nan=True), spec.name
        assert not np.array_equal(sig.score(spec)[400:], s2.score(spec)[400:], equal_nan=True)


# ---------------------------------------------------------------- 5. the matched random control


def test_random_control_mirrors_dates_entry_counts_sizing_and_holding_periods(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "mom_12_1_band_nofilter", exposure_pct=60.0)
    holds = [t.hold_days for t in book.trades if t.reason != "resize"]
    books = []
    for seed in (1, 1, 2):
        ctl = mc.RandomEntries(sig, tm.POL, rule.spec, "close", 60.0, rule.entries, holds, seed)
        books.append(Engine(b, ind, tm.POL, ctl, lambda i: None).run(I0, len(b.dates) - 1))
    one, same, other = books
    assert book_digest(one) == book_digest(same) != book_digest(other)  # seeded
    by_day: dict[int, int] = {}
    for t in one.trades:
        i = b.index_from(t.date)
        by_day[i] = by_day.get(i, 0) + 1
        k, j = i - 1, b.col(t.symbol)
        assert sig.ok[k, j]  # the same universe and liquidity filter, on the decision's data
        assert t.qty <= mc.target_shares(sig, tm.POL, rule.spec, k, j, t.equity)  # the same sizing
        assert t.reason in ("random_hold", "end_of_data") and t.entry_at == "close"
        if t.reason == "random_hold":
            assert t.hold_days in holds  # exits drawn from the rule's own holding periods
    assert set(by_day) <= set(rule.entries) and all(n <= rule.entries[i] for i, n in by_day.items())
    assert sum(by_day.values()) >= 0.6 * sum(rule.entries.values())  # most entries fit under the same caps
    assert {t.symbol for t in one.trades} != {t.symbol for t in book.trades}  # not the ranked names


def test_control_reports_the_percentile_of_the_rule(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "trend_momentum", exposure_pct=60.0)
    seen = []
    c = mc.matched_random_control(b, ind, tm.POL, sig, rule.spec, book, rule, I0, len(b.dates) - 1, runs=6, seed=9,
                                  exposure_pct=60.0, progress=seen.append)
    assert seen == [1, 2, 3, 4, 5, 6] and c["runs"] == 6 == len(c["sharpe"]) == len(c["cagr_pct"])
    mine = mc.curve_stats(mc.daily_returns(book))
    assert c["sharpe_percentile"] == round(100 * sum(s < mine["sharpe"] for s in c["sharpe"]) / 6, 1)
    assert c["cagr_percentile"] == round(100 * sum(s < mine["cagr_pct"] for s in c["cagr_pct"]) / 6, 1)
    assert c["entries"] == sum(rule.entries.values()) and c["median"]["sharpe"] == round(
        float(np.median(c["sharpe"])), 2)
    again = mc.matched_random_control(b, ind, tm.POL, sig, rule.spec, book, rule, I0, len(b.dates) - 1, runs=6,
                                      seed=9, exposure_pct=60.0)
    assert again["sharpe"] == c["sharpe"] and len(set(c["sharpe"])) > 1
    assert mc.percentile_of(0.5, [0.1, 0.2, 0.5, 0.9]) == 50.0 and mc.percentile_of(1.0, []) is None


# ---------------------------------------------------------------- 6. the statistics and the Gate B marks


def test_account_statistics(core):
    b, stocks, ind, sig = core
    book, rule = account(core, "trend_momentum")
    s = mc.account_stats(book)
    d = pd.DataFrame(book.daily)
    r = (d["pnl"] / d["start"]).to_numpy()
    eq = np.cumprod(1 + r)
    assert s["return_pct"] == round((eq[-1] - 1) * 100, 2) == round((book.equity / 100_000 - 1) * 100, 2)
    assert s["cagr_pct"] == round((eq[-1] ** (252 / len(r)) - 1) * 100, 2)
    assert s["sharpe"] == round(r.mean() / r.std(ddof=1) * math.sqrt(252), 2)
    assert s["sharpe_haircut"] == round(s["sharpe"] / 2, 2)
    peak = np.maximum.accumulate(np.r_[1.0, eq])
    assert s["max_drawdown_pct"] == round(((peak - np.r_[1.0, eq]) / peak).max() * 100, 2)  # mark-to-market
    assert list(s["years"]) == ["2023", "2024"] and s["n_years"] == 2
    assert math.isclose(np.prod([1 + v / 100 for v in s["years"].values()]) - 1, s["return_pct"] / 100, abs_tol=2e-4)
    assert s["positive_years"] == sum(v > 0 for v in s["years"].values())
    assert s["avg_exposure_pct"] == round(d["exposure"].mean() * 100, 1) <= s["max_exposure_pct"]
    assert s["avg_names"] == round(d["positions"].mean(), 1)
    years = len(r) / 252
    assert s["turnover_pct_per_year"] == round((book.bought + book.sold) / 2 / d["start"].mean() / years * 100, 1)
    cost = sum(t.cost_spread + t.cost_impact + t.cost_slippage + t.cost_fees for t in book.trades)
    assert s["cost_bp_per_year"] == round(cost / d["start"].mean() / years * 1e4, 1) > 0
    assert s["trades"] + s["resize_sales"] == len(book.trades) and s["resize_sales"] > 0
    assert s["avg_hold_days"] == round(np.mean([t.hold_days for t in book.trades if t.reason != "resize"]), 1)
    by = pd.DataFrame([(t.symbol, t.pnl) for t in book.trades], columns=["s", "p"]).groupby("s")["p"].sum()
    want = round(by.sort_values(ascending=False)[:5].sum() / by.sum() * 100, 1) if by.sum() > 0 else None
    assert s["top5_profit_share_pct"] == want
    assert math.isclose(book.bought - book.sold + sum(t.cost_fees for t in book.trades), -s["net_pnl"], abs_tol=0.02)


def test_break_even_is_stated_from_the_0x_run_as_pre_registered():
    """Profit falls from the 0x run's P0 to the 1x run's P1, so it reaches zero at P0 / (P0 - P1) times the costs;
    the multiple with the 1x trades held fixed is kept beside it as a cross-check."""
    assert mc.break_even_cost(900.0, 1000.0, 100.0) == {"multiple": 10.0, "note": "from the 0x and 1x runs",
                                                        "pnl_at_zero_cost": 1000.0, "trades_fixed_multiple": 10.0}
    assert mc.break_even_cost(-50.0, 100.0, 140.0)["multiple"] == 0.7  # profitable before costs, not after
    assert mc.break_even_cost(-50.0, 100.0, 140.0)["trades_fixed_multiple"] == 0.6
    assert mc.break_even_cost(-150.0, -40.0, 100.0)["note"] == "loses before costs"
    assert mc.break_even_cost(-150.0, -40.0, 100.0)["multiple"] is None
    assert mc.break_even_cost(10.0, 10.0, 0.0)["note"] == "costs did not lower the profit"
    assert mc.break_even_cost(10.0, None, 5.0)["note"] == "no 0x run"


def stats(ret: float, pos: int, cagr: float = 10.0, dd: float = 12.0) -> dict:
    return {"return_pct": ret, "positive_years": pos, "n_years": 10, "cagr_pct": cagr, "max_drawdown_pct": dd}


def test_gate_b_marks_say_pass_fail_or_cannot_be_judged():
    spy = {"cagr_pct": 11.0, "max_drawdown_pct": 20.0}
    ctl = {"runs": 200, "sharpe_percentile": 93.0, "cagr_percentile": 88.0}
    be = mc.break_even_cost(900.0, 1000.0, 100.0)
    g = mc.gate_b(stats(150.0, 7), stats(140.0, 6), stats(130.0, 6), spy, ctl, {"a": stats(5.0, 6), "b": stats(9.0, 8)},
                  be)
    assert [x["mark"] for x in g] == [1, 2, 3, 4, 5, 6] and [x["text"] for x in g] == list(mc.GATE_B_MARKS)
    v = [x["verdict"] for x in g]
    assert v == ["pass", mc.CANNOT, mc.CANNOT, mc.CANNOT, mc.CANNOT, "pass"]
    assert mc.CANNOT == "cannot be judged yet (provisional data)"
    assert "would pass" in g[1]["detail"] and "price only until S84" in g[1]["detail"]  # 10 >= 11 - 2, 12 <= 15
    assert "needs >= 500 runs" in g[2]["detail"] and "percentile 93.0 of 200" in g[2]["detail"]
    assert "at 3x: net return 130.0%" in g[3]["detail"] and "break-even at 10.0x" in g[3]["detail"]
    assert "S83" in g[4]["detail"]
    g = mc.gate_b(stats(150.0, 5), stats(-1.0, 6), None, spy, {**ctl, "runs": 500, "sharpe_percentile": 71.0},
                  {"a": stats(5.0, 6), "b": stats(-9.0, 8)}, None)
    assert [x["verdict"] for x in g] == ["fail", mc.CANNOT, "fail", "fail", mc.CANNOT, "fail"]
    assert "failing: b" in g[5]["detail"]
    g = mc.gate_b(stats(150.0, 7), None, None, spy, {**ctl, "runs": 500}, {}, None)
    assert [x["verdict"] for x in g] == ["pass", mc.CANNOT, "pass", mc.CANNOT, mc.CANNOT, mc.CANNOT]
    assert mc.mark2_price_only(stats(1, 1, cagr=12.0, dd=22.0), spy)  # +1 point with drawdown <= 1.1x
    assert not mc.mark2_price_only(stats(1, 1, cagr=11.5, dd=16.0), spy)
    assert not mc.mark2_price_only(stats(1, 1, cagr=8.9, dd=5.0), spy)


# ---------------------------------------------------------------- 7. the stakeholder's command


@pytest.fixture(scope="module")
def core_runs(tmp_path_factory, core):
    """`momentum_core.py` for the three versions on the synthetic market, as `run_experiments.sh core` runs them
    (the daily bars are handed in instead of being built from minute files). The third run pretends the earnings
    estimate is reliable, so the approximate exit-before-earnings variant runs too."""
    b, stocks, ind, sig = core
    data = tmp_path_factory.mktemp("core")
    uni = data / "universe_test.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": stocks[:15], "XLF": stocks[15:]}, "market": "SPY"}))
    (data / "trials.jsonl").write_text(json.dumps({"trial_id": "old", "n_trials": 30}) + "\n")
    mp = pytest.MonkeyPatch()
    seen, out = {}, {}

    def daily(data_dir, symbols, start, end, **kw):
        seen["symbols"] = list(symbols)
        return b, False

    mp.setattr(core_script.wfm, "load_or_build_daily", daily)
    mp.setattr(core_script, "first_train_month", lambda start, warmup: np.datetime64("2022-03"))
    args = ["--data", str(data), "--universe", str(uni), "--start", "2022-01-03", "--end", str(b.dates[-1]),
            "--control-runs", "3"]
    blk = np.zeros(b.c.shape, dtype=bool)
    blk[::45] = True  # "be flat tonight" every 45th session, for every stock
    for version in ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"):
        if version == "mom_12_1_band_nofilter":
            mp.setattr(core_script.wfm, "earnings_filter", lambda *a, **k: (
                {"mode": "on", "note": "test window", "coverage": {"reliable": True}}, blk, blk))
        mp.setattr(sys, "argv", ["momentum_core.py", "--version", version, *args])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            core_script.main()
        out[version] = buf.getvalue()
    mp.undo()
    return data, uni, seen, out, args


def test_core_run_writes_reports_files_and_one_trial_row_per_run(core_runs, core):
    data, uni, seen, out, args = core_runs
    b, stocks, ind, sig = core
    assert seen["symbols"] == [*stocks, "XLK", "XLF", "SPY"]  # the multiday set's list: its bar cache is reused
    reps = {v: core_script.find_report(data / "reports", "balanced", v)[1] for v in mc.VERSIONS}
    rep = reps["trend_momentum"]
    assert rep["set"] == "core" and rep["status"].startswith("PROVISIONAL") and rep["control_runs"] == 3
    assert rep["oos_window"] == ["2023-03-01", str(b.dates[-1])] and rep["earnings_variant"] == "skipped"
    assert rep["prereg"]["file"] == "docs/prereg/trend_momentum.md" and len(rep["prereg"]["sha256"]) == 64
    assert "exit-before-earnings variant SKIPPED" in out["trend_momentum"]
    rows = rep["rows"]
    grid = [r for r in rows if r["kind"] == "grid"]
    assert len(grid) == 2 * 3 * 3 and len([r for r in rows if r["kind"] == "break_even_input"]) == 6
    assert {(r["fill"], r["cost"], r["exposure_pct"]) for r in grid} == {
        (f, c, e) for f in ("base", "stress") for c in (1.0, 2.0, 3.0) for e in (30.0, 60.0, 100.0)}
    assert {r["earnings"] for r in rows} == {"hold"}
    assert sorted(r["spec"].split("~")[1] for r in rows if r["kind"] == "neighbour") == [
        "exit30", "nofilter", "reg125", "reg60", "risk15"]
    base = next(r for r in rows if r["label"] == rep["base_label"])
    assert rep["base_label"] == "trend_momentum|base|1x|cap100|hold" and base["trades"] > 0
    for k in ("return_pct", "cagr_pct", "sharpe", "sharpe_haircut", "max_drawdown_pct", "years", "positive_years",
              "avg_exposure_pct", "max_exposure_pct", "avg_names", "turnover_pct_per_year", "cost_bp_per_year",
              "trades", "avg_hold_days", "break_even", "control"):
        assert base[k] is not None, k
    assert base["control"]["runs"] == 3 and set(rep["control"]) == {
        f"trend_momentum|base|1x|cap{e}|hold" for e in (30, 60, 100)}
    lo, mid, hi = (next(r for r in grid if r["fill"] == "base" and r["cost"] == 1.0 and r["exposure_pct"] == e)
                   for e in (30.0, 60.0, 100.0))
    assert lo["avg_exposure_pct"] < mid["avg_exposure_pct"] < hi["avg_exposure_pct"] and lo["max_exposure_pct"] < 40
    x1, x3 = (next(r for r in grid if r["fill"] == "base" and r["cost"] == c and r["exposure_pct"] == 60.0)
              for c in (1.0, 3.0))
    assert 2.5 * x1["cost_bp_per_year"] < x3["cost_bp_per_year"] < 3.5 * x1["cost_bp_per_year"]
    bench = rep["benchmarks"]
    assert set(bench) == {"spy", "spy_scaled", "universe_ew"} and bench["spy_scaled"]["scale_pct"] == base[
        "avg_exposure_pct"]
    spy = b.c[:, b.col("SPY")]
    assert bench["spy"]["return_pct"] == round((spy[-1] / spy[b.index_from("2023-03-01") - 1] - 1) * 100, 2)
    assert abs(bench["spy_scaled"]["return_pct"]) < abs(bench["spy"]["return_pct"])
    trades = pd.read_csv(data / "reports" / base["files"]["trades"])
    daily = pd.read_csv(data / "reports" / base["files"]["daily"])
    assert len(trades) == base["trades"] + base["resize_sales"] and len(daily) == rep["oos_sessions"]
    assert np.allclose(trades["pnl"], trades["gross_pnl"] - trades[list(tm.COSTS)].sum(axis=1))
    assert math.isclose(trades["pnl"].sum(), base["net_pnl"], abs_tol=0.01)
    assert [g["verdict"] for g in rep["gate_b"]][1] == mc.CANNOT and len(rep["gate_b"]) == 6
    # the third run had a usable earnings window: both pre-declared versions, the second labelled approximate
    third = reps["mom_12_1_band_nofilter"]
    assert third["earnings_variant"] == "run (approximate)" and "APPROXIMATE" in out["mom_12_1_band_nofilter"]
    assert len([r for r in third["rows"] if r["kind"] == "grid"]) == 36
    label = "mom_12_1_band_nofilter|base|1x|cap100|exit_approx"
    exits = pd.read_csv(data / "reports" / next(r for r in third["rows"] if r["label"] == label)["files"]["trades"])
    assert "earnings" in set(exits["reason"])
    holds = pd.read_csv(data / "reports" / next(
        r for r in third["rows"] if r["label"] == third["base_label"])["files"]["trades"])
    assert "earnings" not in set(holds["reason"])
    logged = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    mine = [r for r in logged if r.get("variant") == "multiday_core"]
    assert len(mine) == 29 + 29 + 53 and all(r["status"] == "provisional" for r in mine)
    assert {r["experiment"] for r in mine} == set(mc.VERSIONS)
    assert all((data / "trials" / f"{r['trial_id']}.csv").is_file() for r in mine)
    assert all(set(r["params"]) >= {"fill", "cost_multiple", "exposure_cap_pct", "earnings", "reg_window",
                                    "lookbacks", "market_filter", "atr_risk"} for r in mine)
    # a configuration counts once: its cost and fill stress reports add nothing to the trial count
    assert sum(r["n_trials"] for r in mine) == (3 + 5) + (3 + 5) + (6 + 5)
    assert all(r["n_trials"] == int(r["params"]["fill"] == "base" and r["params"]["cost_multiple"] == 1) for r in mine)


def test_core_summary_is_one_table_with_benchmarks_and_gate_marks(core_runs, monkeypatch, capsys):
    data, uni, seen, out, args = core_runs
    for version, runs, code in (("trend_momentum", "3", 0), ("trend_momentum", "200", 1), ("mom_12_1_band", "3", 0)):
        monkeypatch.setattr(sys, "argv", ["momentum_core.py", "--has", version, *args[:-1], runs])
        with pytest.raises(SystemExit) as e:
            core_script.main()
        assert e.value.code == code
    monkeypatch.setattr(sys, "argv", ["momentum_core.py", "--summary", "--data", str(data)])
    core_script.main()
    text = (data / "reports" / "experiments_core_summary.md").read_text()
    assert text in capsys.readouterr().out
    lines = text.splitlines()
    assert lines[2].startswith("**Survivorship bias:**") and lines[4].startswith("**PROVISIONAL (decision 15):**")
    assert all(s in lines[4] for s in ("S71", "S83", "S84", "price-only SPY"))
    heads = [x for x in lines if x.startswith("| Rule / version |")]
    assert len(heads) == 1  # ONE table
    cols = heads[0].strip("| ").split(" | ")
    assert cols == ["Rule / version", "Fill", "Costs", "Cap %", "Earnings", "Total return %", "CAGR %", "Sharpe",
                    "Sharpe x 0.5", "Max drawdown %", "2023", "2024", "Positive years", "Avg exposure %",
                    "Max exposure %", "Avg names", "Turnover %/yr", "Cost bp/yr", "Trades", "Avg holding days",
                    "Top-5 stocks' share of profit %", "Break-even cost x", "Sharpe pctile vs random",
                    "CAGR pctile vs random"]
    body = [x for x in lines if x.startswith("| ") and "---" not in x][1:]
    assert all(len(x.strip("|").split("|")) == len(cols) for x in body)
    first = [x.split(" | ")[0].strip("| ") for x in body]
    assert first[0] == "trend_momentum" and body[0].split(" | ")[1:5] == ["base", "1x", "100", "hold through"]
    assert first.count("trend_momentum") == 18 and first.count("mom_12_1_band_nofilter") == 36
    assert first.count("BENCHMARK: SPY buy-and-hold (price only, no dividends)") == 1
    assert first.count("BENCHMARK: equal-weight universe, rebalanced monthly, after costs") == 1
    for version in mc.VERSIONS:
        assert first.count(f"BENCHMARK: SPY (price only) scaled to {version}'s average exposure, rest in cash") == 1
        assert first.count(f"BENCHMARK: random entries matched to {version} (median of 3 runs)") == 3
        assert f"### `{version}` (base fill, 1x costs, 100% cap, hold through earnings)" in text
    assert sum("neighbour" in x for x in first) == 15
    assert any("exit before (APPROXIMATE)" in x for x in body)
    assert text.count("- Mark ") == 18 and text.count("**cannot be judged yet (provisional data)**") >= 9
    assert text.count("Overall: cannot pass Gate B yet") == 3 and "Not run yet" not in text
    assert "docs/prereg/mom_12_1_band.md" in text and "exit-before-earnings variant skipped" in text
    for f in (data / "reports").glob("core_balanced_mom_12_1_band_nofilter_*.json"):
        f.unlink()
    monkeypatch.setattr(sys, "argv", ["momentum_core.py", "--summary", "--data", str(data)])
    core_script.main()
    assert "Not run yet: mom_12_1_band_nofilter." in capsys.readouterr().out


def test_no_pre_registration_no_run(monkeypatch, tmp_path):
    assert core_script.prereg("trend_momentum")["file"] == "docs/prereg/trend_momentum.md"
    monkeypatch.setattr(core_script, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as e:
        core_script.prereg("trend_momentum")
    assert "pre-registration" in str(e.value)


def test_run_experiments_script_has_the_core_command_and_it_is_documented():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in ('"${1:-}" = "core"', "trend_momentum mom_12_1_band mom_12_1_band_nofilter", "momentum_core.py",
                   "--summary", "--has", "KAIBOT_CORE_CONTROL_RUNS", "experiments_core_summary.md",
                   "usage: $0 [multiday|core|spy]"):
        assert needle in sh
    assert sh.index('"${1:-}" = "core"') < sh.index("No .env found")  # the core set needs no .env
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "run_experiments.sh")]).returncode == 0
    doc = (ROOT / "docs" / "m0" / "reproduce.md").read_text()
    for needle in ("./scripts/run_experiments.sh core", "experiments_core_summary.md", "docs/prereg/", "PROVISIONAL",
                   "KAIBOT_CORE_CONTROL_RUNS=500"):
        assert needle in doc


def test_the_core_is_research_only():
    """Nothing the engine runs imports the momentum core, and the core charges costs through the harness's engine
    (no cost arithmetic of its own, no second simulator)."""
    for f in (ROOT / "src" / "kaibot").rglob("*.py"):
        if f.name != "multiday_core.py":
            assert "multiday_core" not in f.read_text(), f
    text = (ROOT / "src" / "kaibot" / "replay" / "multiday_core.py").read_text()
    for banned in ("buy_px", "sell_px", "sell_fees", "shadow_fill_price", "kaibot.engine", "kaibot.broker",
                   "kaibot.strategies"):
        assert banned not in text, banned
    assert "Engine(" in text
