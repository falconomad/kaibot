"""Issue #143: the honest rerun of the momentum core. Gate v2 wired into the multi-day runners (prereg.require, the
gate_v2 hash, the exposure-matched control), the point-in-time universe (#123) and the daily bar store (#124: raw
prices for orders and costs, split-adjusted prices without dividends for the signals, dividends, SPY total
return). Small seeded synthetic data only, no network."""

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
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.ingest import sp500_pit
from kaibot.ingest.daily_store import DailyPanel
from kaibot.replay import multiday_core as mc
from kaibot.replay.multiday import Engine, indicators
from kaibot.replay.multiday_rules import book_digest
from kaibot.replay.sim import half_spread

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core as core_script  # noqa: E402
import test_multiday as tm  # noqa: E402
import test_multiday_core as tc  # noqa: E402
import walk_forward_multiday as wfm_script  # noqa: E402

POL = tm.POL
N, FIRST, I0 = 560, "2022-01-03", 280


# ---------------------------------------------------------------- 0. today's results are unchanged

# Written down from the code of #127 as merged (main @ af35af9) BEFORE any change of #143: trades, final equity
# and the events digest of the base and a stress account of each version on the synthetic market of #127.
PINNED_CORE = {
    ("trend_momentum", "base", 100.0): (109, 125685.6006, "1beb63952bbc1c17"),
    ("trend_momentum", "stress", 60.0): (64, 120772.5994, "5ae6cf3d94af54d7"),
    ("mom_12_1_band", "base", 100.0): (22, 136827.0345, "63195d1dcee90cf0"),
    ("mom_12_1_band", "stress", 60.0): (16, 129887.3954, "2429859c52c03a19"),
    ("mom_12_1_band_nofilter", "base", 100.0): (27, 141053.8994, "447b67ce1c3af51a"),
    ("mom_12_1_band_nofilter", "stress", 60.0): (20, 132386.1183, "9e418d4cd644ee21"),
}


@pytest.fixture(scope="module")
def today():
    b, stocks = tc.core_market()
    return b, stocks, mc.Market.from_bars(b, b.cols(stocks), b.col("SPY"))


@pytest.mark.parametrize("key", list(PINNED_CORE))
def test_today_universe_core_results_are_unchanged(today, key):
    b, stocks, m = today
    version, fill, cap = key
    n, equity, digest = PINNED_CORE[key]
    ind = indicators(b)
    old = mc.run_rule(b, ind, POL, mc.Signals(b, ind, b.cols(stocks), b.col("SPY")), mc.VERSIONS[version], tc.I0,
                      len(b.dates) - 1, fill=fill, exposure_pct=cap)[0]
    new = m.run(POL, mc.VERSIONS[version], tc.I0, len(b.dates) - 1, fill=fill, exposure_pct=cap)[0]
    for book in (old, new):
        assert len(book.trades) == n and math.isclose(book.equity, equity, abs_tol=1e-3)
        assert book_digest(book).startswith(digest)
    assert all(t.dividends == 0.0 for t in new.trades) and sum(r["dividends"] for r in new.daily) == 0.0


def test_daily_rows_carry_the_costs_and_the_dollars_traded(today):
    b, stocks, m = today
    book, _ = m.run(POL, mc.VERSIONS["trend_momentum"], tc.I0, len(b.dates) - 1)
    d = pd.DataFrame(book.daily)
    costs = sum(t.cost_spread + t.cost_impact + t.cost_fees for t in book.trades)
    assert math.isclose(d["cost"].sum(), costs, rel_tol=1e-9)  # spread, impact and fees: gap slippage is a price
    assert math.isclose(d["traded"].sum(), book.bought + book.sold, rel_tol=1e-9)
    assert (d["cost"] >= 0).all() and (d["traded"] > 0).sum() > 30


# ---------------------------------------------------------------- 1. a synthetic raw panel

def raw_market(seed: int = 5, n: int = N, n_stocks: int = 30, actions=(), cut=None):
    """Thirty random walks priced $100-$600 at the start (a 4-for-1 split of a dear one stays above the cost
    model's cent floors), SPY last. `actions`: (symbol, session, ratio, cash) on the raw basis; a split divides
    the raw prices from that session on by the ratio and multiplies the volume. `cut`: {symbol: last session with
    a bar}."""
    rng = np.random.default_rng(seed)
    stocks = [f"P{k:02d}" for k in range(n_stocks)]
    mkt = rng.normal(0.0006, 0.007, n)
    mkt[400:440] -= 0.004
    drift = np.linspace(-0.0004, 0.0015, n_stocks)
    r = drift + np.outer(mkt, rng.uniform(0.7, 1.4, n_stocks)) + rng.normal(0, 0.008, (n, n_stocks))
    c = np.exp(np.cumsum(np.column_stack([r, mkt]), axis=0)) * np.r_[np.linspace(100, 600, n_stocks), 400.0]
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.003, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, c.shape)))
    v = np.full(c.shape, 2e6)
    dates = np.array(tm.sessions(n, FIRST), dtype="datetime64[D]")
    syms = [*stocks, "SPY"]
    acts = []
    for sym, i, ratio, cash in actions:
        j = syms.index(sym)
        if ratio:
            for a in (o, h, lo, c):
                a[i:, j] /= ratio
            v[i:, j] *= ratio
        acts.append((sym, dates[i], ratio, cash))
    for sym, last in (cut or {}).items():
        j = syms.index(sym)
        for a in (o, h, lo, c, v):
            a[last + 1:, j] = np.nan
    return DailyPanel.make(dates, syms, o, h, lo, c, v, actions=acts), stocks


def test_the_store_market_reads_signals_adjusted_and_orders_raw():
    panel, stocks = raw_market(actions=[("P20", 430, 4.0, None)])
    m = mc.Market.from_panel(panel, stocks, "SPY")
    j = m.raw.col("P20")
    assert m.bars_source == "store" and m.universe == "today" and m.member is None
    assert m.raw.c[431, j] == pytest.approx(panel.c[431, j]) and m.sig.c[431, j] == pytest.approx(panel.c[431, j])
    assert m.sig.c[429, j] == pytest.approx(panel.c[429, j] / 4)  # history on the basis of the last session
    assert m.signals.px[429, j] == pytest.approx(panel.c[429, j])  # what an order sized that evening pays
    assert not m.signals.big_move[425:520, j].any()  # a split is not a 75% fall
    assert m.signals.atr20[429, j] == pytest.approx(m.signals.atr20[431, j] * 4, rel=0.35)  # raw dollars
    assert m.ind.adv[440, j] == pytest.approx(m.ind.adv[420, j], rel=0.2)  # dollar volume: no jump
    assert m.signals.shares_basis(430, j) == 4.0 and m.signals.shares_basis(431, j) == 1.0


# ---------------------------------------------------------------- 2. raw against adjusted: a 4-for-1 split

def test_a_4_for_1_split_creates_no_signal_and_no_cost_jump():
    """The same market twice, once with a 4-for-1 split of a stock the rule holds at the time. The signals are
    identical (they read split-adjusted prices), so are the decisions; the held position becomes four times the
    shares at a quarter of the price; costs on raw prices do not jump; the account is the same up to per-share
    fees. (Sized once at entry, so no whole-share rounding enters: the banded rule.)"""
    plain, stocks = raw_market()
    a = mc.Market.from_panel(plain, stocks, "SPY")
    spec = mc.VERSIONS["mom_12_1_band_nofilter"]
    book_a, rule_a = a.run(POL, spec, I0, N - 1)
    t = next(x for x in book_a.trades if x.date < plain.dates[420] and x.exit_date > plain.dates[460]
             and plain.c[440, plain.col(x.symbol)] > 220)  # a quarter of the price stays above the cent floors
    split_at = 440
    panel, _ = raw_market(actions=[(t.symbol, split_at, 4.0, None)])
    b = mc.Market.from_panel(panel, stocks, "SPY")
    book_b, rule_b = b.run(POL, spec, I0, N - 1)
    for name in ("big_move", "below_avg", "market_on", "ok"):
        assert np.array_equal(getattr(a.signals, name), getattr(b.signals, name)), name
    np.testing.assert_allclose(a.signals.trend_score(90), b.signals.trend_score(90), rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(a.signals.score(spec), b.signals.score(spec), rtol=1e-12, equal_nan=True)
    assert rule_a.orders == rule_b.orders  # the same decisions, the same shares
    got = next(x for x in book_b.trades if x.symbol == t.symbol and x.date == t.date)
    assert (got.exit_date, got.reason, got.qty) == (t.exit_date, t.reason, 4 * t.qty)
    assert got.entry == pytest.approx(t.entry / 4) and got.exit == pytest.approx(t.exit / 4, rel=1e-6)
    ea, eb = np.array([r["end"] for r in book_a.daily]), np.array([r["end"] for r in book_b.daily])
    np.testing.assert_allclose(eb, ea, rtol=2e-5)  # the same account, up to per-share fees on 4x the shares
    ra, rb = np.diff(ea) / ea[:-1], np.diff(eb) / eb[:-1]
    assert np.abs(rb - ra).max() < 2e-5  # no jump on the split day or any other
    ca, cb = pd.DataFrame(book_a.daily)["cost"], pd.DataFrame(book_b.daily)["cost"]
    assert abs(cb.sum() / ca.sum() - 1) < 0.01  # no cost jump: the floors apply to the price really paid
    assert got.cost_spread + got.cost_impact == pytest.approx(t.cost_spread + t.cost_impact, rel=1e-9)
    # the trend rule sees no 75% fall either: no gap or trend sale of the split stock that the plain market lacks
    tr = mc.VERSIONS["trend_momentum"]
    sells = [[(i, r) for i, k, j, r in m.run(POL, tr, I0, N - 1)[1].orders if k == "sell" and j == plain.col(
        t.symbol) and r in ("gap", "trend") and split_at <= i < split_at + 90] for m in (a, b)]
    assert sells[1] == sells[0]


def test_dividends_are_credited_on_raw_shares_and_spy_is_total_return():
    plain, stocks = raw_market()
    spec = mc.VERSIONS["mom_12_1_band_nofilter"]
    book0, _ = mc.Market.from_panel(plain, stocks, "SPY").run(POL, spec, I0, N - 1)
    t = next(x for x in book0.trades if x.date < plain.dates[400] and x.exit_date > plain.dates[430])
    spy_divs = [("SPY", i, None, 2.0) for i in spy_quarters()]  # four a year: the table is complete
    panel, _ = raw_market(actions=[(t.symbol, 420, None, 3.0), *spy_divs])
    m = mc.Market.from_panel(panel, stocks, "SPY")
    book, _ = m.run(POL, spec, I0, N - 1)
    got = next(x for x in book.trades if x.symbol == t.symbol and x.date == t.date)
    assert got.dividends == pytest.approx(3.0 * got.qty) and book.dividends == pytest.approx(3.0 * got.qty)
    assert math.isclose(sum(x.pnl + x.dividends for x in book.trades), book.equity - 100_000.0, abs_tol=1e-4)
    assert pd.DataFrame(book.daily)["dividends"].sum() == pytest.approx(book.dividends)
    j = m.spy
    tr, po = m.spy_total_return(I0, N - 1), m.spy_price_only(I0, N - 1)
    c = panel.c[:, j]
    q = next(i for i in spy_quarters() if i >= I0)
    assert tr[q - I0] == pytest.approx((c[q] + 2.0) / c[q - 1] - 1)
    assert po[q - I0] == pytest.approx(c[q] / c[q - 1] - 1)
    want = np.prod([1 + 2 / c[i] for i in spy_quarters() if I0 <= i < N])
    assert np.prod(1 + tr) / np.prod(1 + po) == pytest.approx(want)
    assert m.notes["spy_total_return"]["source"].startswith("table (every full year has four dividends)")
    assert mc.Market.from_bars(tc.core_market()[0], np.arange(80), 80).spy_total_return(I0, N - 1) is None


def spy_quarters() -> list[int]:
    """Sessions of the synthetic calendar nearest the 20th of March, June, September and December."""
    d = np.array(tm.sessions(N, FIRST), dtype="datetime64[D]")
    out = []
    for y in range(2022, 2025):
        for mth in (3, 6, 9, 12):
            t = int(np.argmin(np.abs(d - np.datetime64(f"{y}-{mth:02d}-20", "D")).astype(int)))
            if abs(int((d[t] - np.datetime64(f"{y}-{mth:02d}-20", "D")).astype(int))) < 5:
                out.append(t)
    return out


def test_spy_total_return_never_lets_a_missing_dividend_flatter_the_rules():
    """The stakeholder's table misses SPY dividends in 2016 and 2018: a weaker SPY flatters every rule."""
    quarters = spy_quarters()
    full = [("SPY", i, None, 2.0) for i in quarters]
    gappy = [a for a in full if a[1] not in quarters[5:7]]  # two dividends of 2023 missing from the table
    p_full, _ = raw_market(actions=full)
    p_gap, _ = raw_market(actions=gappy)
    r_full, info_full = mc.spy_total_return(p_full, "SPY")
    r_gap, info_gap = mc.spy_total_return(p_gap, "SPY")
    assert info_full["source"].startswith("table (every") and info_full["dividends_per_year"][2023] == 4
    assert info_gap["source"].startswith("table completed: 2") and info_gap["dividends_per_year"][2023] == 2
    assert [a["date"] for a in info_gap["added"]] == [str(p_gap.dates[i]) for i in quarters[5:7]]
    g = lambda r: float(np.nanprod(1 + r[1:]))  # noqa: E731
    assert g(r_gap) == pytest.approx(g(r_full), rel=1e-9)  # completed at the largest nearby amount: the same here
    assert g(r_gap) > g(p_gap.total_return("SPY"))
    # Alpaca's all-adjusted factor is preferred when the store has it (here it knows the two dividends) ...
    j = p_gap.col("SPY")
    ref = np.full(p_gap.c.shape, np.nan)
    adj_full = p_full.adjusted("total")
    ref[:, j] = adj_full.pf_total[:, j]
    p_ref = dataclasses.replace(p_gap, ref_total=ref)
    r_ref, info_ref = mc.spy_total_return(p_ref, "SPY")
    assert info_ref["source"].startswith("alpaca_adjusted") and g(r_ref) == pytest.approx(g(r_full), rel=1e-3)
    # ... but never when it is lower than the table's
    ref[:, j] = 1.0
    r_low, info_low = mc.spy_total_return(dataclasses.replace(p_full, ref_total=ref), "SPY")
    assert info_low["source"].endswith("above Alpaca's factor") and g(r_low) == pytest.approx(g(r_full))
    assert info_full["points_per_year_over_price"] > 0


def test_a_disputed_split_is_never_held_across_and_never_entered_near():
    """A split the table and Alpaca's bars disagree about (the store's check: split_basis_days > 0): the share
    count cannot be carried through it. Located from Alpaca's volume factor; no entries in a window around it,
    and a holding is sold at the close before the first disputed session."""
    plain, stocks = raw_market()
    spec = mc.VERSIONS["mom_12_1_band_nofilter"]
    book0, _ = mc.Market.from_panel(plain, stocks, "SPY").run(POL, spec, I0, N - 1)
    t = next(x for x in book0.trades if x.date < plain.dates[400] and x.exit_date > plain.dates[460])
    j = plain.col(t.symbol)
    ref = np.ones(plain.c.shape)
    ref[430:470, j] = 4.0  # Alpaca's volumes say another share basis on these sessions
    p = dataclasses.replace(plain, ref_split=ref)
    m = mc.Market.from_panel(p, stocks, "SPY", disputed=[t.symbol, "NOT_IN_PANEL"])
    w = mc.DISPUTE_WINDOW
    assert m.exits == {j: [429]} and not m.signals.ok[430 - w:470 + w, j].any() and m.signals.ok[480, j]
    assert "sold at the close of" in m.notes["disputed_splits"][t.symbol]
    book, rule = m.run(POL, spec, I0, N - 1)
    out = next(x for x in book.trades if x.symbol == t.symbol and x.date == t.date)
    assert out.reason == "split_dispute" and out.exit_date == plain.dates[429] and out.cost_spread > 0
    assert not [x for x in book.trades if x.symbol == t.symbol and 430 - w <= p.index_from(x.date) <= 469 + w]
    assert math.isclose(sum(x.pnl for x in book.trades), book.equity - 100_000.0, abs_tol=1e-4)
    never = mc.Market.from_panel(plain, stocks, "SPY", disputed=[t.symbol])  # no Alpaca factor: cannot locate
    assert not never.signals.ok[:, j].any() and "never bought" in never.notes["disputed_splits"][t.symbol]
    ew = m.equal_weight(I0, N - 1)
    assert np.isfinite(ew).all()


# ---------------------------------------------------------------- 3. the point-in-time universe

def test_a_stock_not_yet_a_member_is_never_bought_and_a_leaver_is_sold_by_its_rule():
    panel, stocks = raw_market()
    spec = mc.VERSIONS["mom_12_1_band_nofilter"]
    free = mc.Market.from_panel(panel, stocks, "SPY")
    book0, rule0 = free.run(POL, spec, I0, N - 1)
    bought = [panel.symbols[j] for i, k, j, _ in rule0.orders if k == "buy"]
    late = bought[0]
    member = np.ones(panel.c.shape, dtype=bool)
    member[:, panel.col("SPY")] = False
    member[:470, panel.col(late)] = False  # joins the index at session 470
    book1, _ = mc.Market.from_panel(panel, stocks, "SPY", member=member).run(POL, spec, I0, N - 1)
    stay = next(x for x in book1.trades if x.symbol != late and
                panel.index_from(x.exit_date) - panel.index_from(x.date) > 40)
    leaver = stay.symbol
    gone = panel.index_from(stay.date) + 10
    member[gone:, panel.col(leaver)] = False  # leaves the index ten sessions after the rule bought it
    m = mc.Market.from_panel(panel, stocks, "SPY", member=member)
    assert m.universe == "point_in_time"
    book, rule = m.run(POL, spec, I0, N - 1)
    jl, jx = panel.col(late), panel.col(leaver)
    assert not m.signals.ok[:470, jl].any() and not m.signals.ok[gone:, jx].any()
    assert all(i - 1 >= 470 for i, k, j, _ in rule.orders if k == "buy" and j == jl)  # never bought before it joined
    assert any(t.symbol == late for t in book0.trades if t.date < panel.dates[470])  # ... though the rule wanted it
    after = [(i, r) for i, k, j, r in rule.orders if k == "sell" and j == jx and i > gone]
    first_decision = int(np.flatnonzero(m.signals.monthly[gone + 1:])[0]) + gone + 1
    assert after and after[0] == (first_decision, "rank")  # its own exit rule: out of the ranked universe
    assert not any(k == "buy" and j == jx and i > gone for i, k, j, _ in rule.orders)
    ew_free, ew_pit = free.equal_weight(I0, N - 1), m.equal_weight(I0, N - 1)
    assert not np.allclose(ew_free, ew_pit) and np.isfinite(ew_pit).all()


def test_a_held_company_whose_bars_stop_is_closed_at_its_terminal_return():
    panel, stocks = raw_market()
    spec = mc.VERSIONS["mom_12_1_band_nofilter"]
    book0, _ = mc.Market.from_panel(panel, stocks, "SPY").run(POL, spec, I0, N - 1)
    t = next(x for x in book0.trades if x.date < panel.dates[400] and x.exit_date > panel.dates[440])
    last = 430
    for r in (-0.30, -1.0, 0.0):
        cut, _ = raw_market(cut={t.symbol: last})
        m = mc.Market.from_panel(cut, stocks, "SPY", terminal_returns={t.symbol: r} if r != -0.30 else None)
        j = m.raw.col(t.symbol)
        assert m.terminal[j] == (last, r)
        book, _ = m.run(POL, spec, I0, N - 1)
        out = next(x for x in book.trades if x.symbol == t.symbol and x.date == t.date)
        assert out.reason == "delisted" and out.exit_date == cut.dates[last]
        assert out.exit == pytest.approx(cut.c[last, j] * (1 + r)) and out.cost_fees == 0.0  # no sale costs
        mid = cut.c[cut.index_from(out.date), j]
        assert out.cost_spread == pytest.approx(half_spread(mid) * out.qty)  # only the entry's half spread
        assert math.isclose(sum(x.pnl for x in book.trades), book.equity - 100_000.0, abs_tol=1e-4)
        assert all(np.isfinite([row["end"] for row in book.daily]))
    m = mc.Market.from_panel(raw_market(cut={"P05": N - 3})[0], stocks, "SPY")
    assert m.raw.col("P05") not in m.terminal  # bars that stop at the end of the data are not a hole


# ---------------------------------------------------------------- 4. the exposure-matched control

@pytest.mark.parametrize("version,store", [("trend_momentum", False), ("mom_12_1_band", False),
                                           ("trend_momentum", True)])
def test_the_control_copies_the_rules_positions_and_matches_its_exposure(today, version, store):
    if store:
        panel, stocks = raw_market(actions=[("P25", 430, 4.0, None), ("P27", 450, None, 5.0)], cut={"P04": 470})
        m, i1 = mc.Market.from_panel(panel, stocks, "SPY"), N - 1
    else:
        m, i1 = today[2], len(today[0].dates) - 1
    spec = mc.VERSIONS[version]
    book, rule = m.run(POL, spec, I0, i1)
    slots = mc.matched_schedule(book, rule, m.raw)
    assert len(slots) == len(rule.opened) == sum(rule.entries.values()) and slots
    assert (sum(len(s.resizes) for s in slots) > 0) == spec.resize
    seen = []
    c = mc.exposure_matched_control(m, POL, spec, book, rule, I0, i1, runs=12, seed=3, progress=seen.append)
    r = c["control_result"]
    assert seen == list(range(1, 13)) and c["kind"] == "exposure_matched" and c["runs"] == 12
    assert r["simulated_strategy_sharpe"] == pytest.approx(r["strategy_sharpe"], abs=1e-9)  # the replay is the rule
    assert c["committed_exposure_gap_pts"]["max"] <= 2.0 and c["matched"]  # the same money at work, within 2 points
    assert c["sharpe_percentile"] == min(c["sharpe_percentile_rule_run"], c["sharpe_percentile_replayed_names"])
    again = mc.exposure_matched_control(m, POL, spec, book, rule, I0, i1, runs=12, seed=3)
    assert again["sharpe"] == c["sharpe"] and len(set(c["sharpe"])) > 1  # seeded, and the names do differ
    # one random portfolio, opened up: same entry and exit sessions as the rule, names from the eligible pool
    from kaibot.evaluation import random_control as rc

    sched = [rc.Slot(s.entry, s.exit - s.entry, s.weight) for s in slots]
    fill = m.raw.c

    def eligible(e):
        return np.flatnonzero(m.signals.ok[e - 1] & np.isfinite(fill[e]) & np.isfinite(m.signals.px[e - 1]))

    sel = rc.draw_selection(sched, eligible, np.random.default_rng(1))
    assert all(sel[k] in eligible(s.entry) for k, s in enumerate(slots))
    ctl = mc.SlotReplay(m.signals, slots, sel, "close", mc.Caps(spec.max_names, 100.0))
    rb = Engine(m.raw, m.ind, POL, ctl, lambda i: None, actions=m.actions, terminal=m.terminal).run(I0, i1)
    # a random name whose bars stop is closed at its terminal return before the slot ends: its exit is the slot's
    opened = sorted((m.raw.index_from(t.date), m.raw.index_from(t.exit_date) if t.reason != "delisted" else next(
        sl.exit for k, sl in enumerate(slots) if k in ctl.events and ctl.events[k][0][0] == m.raw.index_from(t.date)
        and sel[k] == m.raw.col(t.symbol))) for t in rb.trades if t.reason != "resize")
    # a slot whose entry came to under one share of the random name is bought at its first resize instead
    want = sorted((ctl.events[k][0][0], s.exit) for k, s in enumerate(slots) if k in ctl.events)
    assert opened == want and sum(ctl.events[k][0][0] == s.entry for k, s in enumerate(slots)
                                  if k in ctl.events) >= 0.9 * len(slots)
    assert {m.raw.symbols[j] for j in sel} != {m.raw.symbols[s.col] for s in slots}


def test_the_control_is_not_matched_when_a_slot_cannot_be_filled(today):
    b, stocks, m = today
    spec = mc.VERSIONS["mom_12_1_band"]
    book, rule = m.run(POL, spec, I0, len(b.dates) - 1)
    thin = mc.Market.from_bars(b, b.cols(stocks[:3]), b.col("SPY"))  # three names for up to 20 slots at a time
    thin_book, thin_rule = book, rule
    c = mc.exposure_matched_control(thin, POL, spec, thin_book, thin_rule, I0, len(b.dates) - 1, runs=3)
    assert not c["matched"] and c["control_result"]["unfilled"] > 0
    g = mc.gate_b(mc.account_stats(book), None, None, {"cagr_pct": 1, "max_drawdown_pct": 1}, {**c, "runs": 500},
                  {}, None)
    assert g[2]["verdict"] == mc.CANNOT and "not matched" in g[2]["detail"]


# ---------------------------------------------------------------- 5. no pre-registration, no run

def test_the_runners_refuse_without_a_committed_pre_registration(tmp_path, monkeypatch):
    assert core_script.prereg("trend_momentum")["status"] == "pre-registered"
    assert core_script.prereg("trend_momentum")["sha256"] == __import__("hashlib").sha256(
        (ROOT / "docs" / "prereg" / "trend_momentum.md").read_bytes()).hexdigest()
    repo = tmp_path / "repo"
    (repo / "docs" / "prereg").mkdir(parents=True)
    f = repo / "docs" / "prereg" / "trend_momentum.md"
    f.write_bytes((ROOT / "docs" / "prereg" / "trend_momentum.md").read_bytes())
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    monkeypatch.setattr(core_script, "ROOT", repo)
    with pytest.raises(SystemExit, match="not committed"):
        core_script.prereg("trend_momentum")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "prereg"], check=True)
    assert len(core_script.prereg("trend_momentum")["sha256"]) == 64
    f.write_text(f.read_text() + "\nedited after the commit\n")
    with pytest.raises(SystemExit, match="changes that are not committed"):
        core_script.prereg("trend_momentum")
    monkeypatch.setattr(wfm_script, "ROOT", tmp_path)  # no docs/prereg at all: the walk-forward stops first
    monkeypatch.setattr(sys, "argv", ["walk_forward_multiday.py", "--rule", "trend_highs", "--end", "2024-01-31",
                                      "--data", str(tmp_path / "nothing")])
    with pytest.raises(SystemExit, match="no pre-registration for rule 'trend_highs'"):
        wfm_script.main()


def test_the_walk_forward_logs_the_prereg_and_gate_hashes():
    text = (ROOT / "scripts" / "walk_forward_multiday.py").read_text()
    assert text.index("prereg.require(a.rule, ROOT)") < text.index("load_or_build_daily(")
    for needle in ('"prereg_sha256": prereg_sha', '"gate_v2_sha256": gate_sha'):
        assert text.count(needle) == 2, needle  # the report and every trial row


# ---------------------------------------------------------------- 6. the stakeholder's command on a store

def write_store(root: Path, panel: DailyPanel, keys: dict[str, str], actions) -> None:
    """A daily store on disk in #124's layout: one Parquet file of raw bars per key, the corporate actions."""
    for j, sym in enumerate(panel.symbols):
        has = np.isfinite(panel.c[:, j])
        f = root / "bars" / f"symbol={keys.get(sym, sym)}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"date": pa.array(panel.dates[has]), **{k: getattr(panel, k)[has, j]
                                                                         for k in ("o", "h", "l", "c", "v")}}), f)
    rows = [(keys.get(s, s), "forward_split" if ratio else "cash_dividend", np.datetime64(d, "D"),
             float(ratio or np.nan), float(cash or np.nan), False) for s, d, ratio, cash in actions]
    pq.write_table(pa.table({"key": [r[0] for r in rows], "type": [r[1] for r in rows],
                             "ex_date": pa.array(np.array([r[2] for r in rows], dtype="datetime64[D]")),
                             "ratio": [r[3] for r in rows], "cash": [r[4] for r in rows],
                             "dup": [r[5] for r in rows]}), root / "corporate_actions.parquet")


@pytest.fixture(scope="module")
def pit_runs(tmp_path_factory):
    """`momentum_core.py --universe pit` on a synthetic store: two of the thirty stocks are former members stored
    as SYMBOL@ASOF keys (one leaves in session 400 and its bars stop in 420, one was never in today's list), one of
    today's names joins the index late, SPY pays dividends. Then today's run on the same store and the summary."""
    data = tmp_path_factory.mktemp("pit")
    acts_idx = [("P21", 430, 4.0, None), ("P26", 450, None, 4.0), ("SPY", 300, None, 1.6), ("SPY", 363, None, 1.7),
                ("SPY", 426, None, 1.8), ("SPY", 489, None, 1.9)]
    panel, stocks = raw_market(actions=acts_idx, cut={"P28": 420})
    d = panel.dates
    today_list = [s for s in stocks if s not in ("P28", "P29")]
    log = [sp500_pit.Change(date(2021, 12, 1), tuple(s for s in stocks if s != "P27")),
           sp500_pit.Change(d[350].astype(object), ("P27",), ()),
           sp500_pit.Change(d[400].astype(object), (), ("P28",))]
    ms = sp500_pit.Membership(log, {}, today_list, date(2024, 2, 1))
    keys = {s: ms.keys(s)[-1] for s in ("P28", "P29")}
    assert keys["P28"].startswith("P28@") and keys["P29"].startswith("P29@")
    write_store(data / "daily", panel, keys, [(s, d[i], r, c) for s, i, r, c in acts_idx])
    uni = data / "universe_test.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": today_list[:14], "XLF": today_list[14:]}, "market": "SPY"}))
    mp = pytest.MonkeyPatch()
    mp.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, **k: ms))
    mp.setattr(core_script, "first_train_month", lambda start, warmup: np.datetime64("2022-03"))
    base = ["--data", str(data), "--universe-file", str(uni), "--start", FIRST, "--end", str(d[-1]),
            "--control-runs", "3"]
    out = {}
    for universe, version in (("pit", "trend_momentum"), ("pit", "mom_12_1_band"),
                              ("pit", "mom_12_1_band_nofilter"), ("today", "trend_momentum")):
        extra = ["--bars", "store"] if universe == "today" else []
        mp.setattr(sys, "argv", ["momentum_core.py", "--version", version, "--universe", universe, *extra, *base])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            core_script.main()
        out[(universe, version)] = buf.getvalue()
    mp.setattr(sys, "argv", ["momentum_core.py", "--summary", "--set", "core_pit", "--data", str(data)])
    with contextlib.redirect_stdout(io.StringIO()):
        core_script.main()
    yield data, panel, ms, keys, base, out, mp
    mp.undo()


def test_pit_run_reports_marks_benchmarks_hashes_and_trial_rows(pit_runs):
    data, panel, ms, keys, base, out, mp = pit_runs
    hit = core_script.find_report(data / "reports", "balanced", "trend_momentum", mode="point_in_time")
    f, rep = hit
    assert f.name.startswith("core_balanced_trend_momentum_pit_") and rep["universe_mode"] == "point_in_time"
    assert rep["bars"] == "store" and rep["control_kind"] == "exposure_matched" and rep["status"].startswith(
        "PROVISIONAL")
    assert rep["data"]["stocks"] == 30 and rep["data"]["terminal_closes"] == 1  # P28's bars stop while a member
    assert len(rep["gate_v2_sha256"]) == 64 and rep["prereg"]["status"] == "pre-registered"
    assert set(rep["benchmarks"]) >= {"spy", "spy_total_return", "universe_ew", "spy_tr_scaled"}
    assert rep["benchmarks"]["spy_total_return"]["return_pct"] > rep["benchmarks"]["spy"]["return_pct"]
    marks = {g["mark"]: g for g in rep["gate_b"]}
    assert marks[2]["verdict"] in ("pass", "fail") and "SPY total return" in marks[2]["detail"]
    assert marks[4]["verdict"] in ("pass", "fail") and marks[5]["verdict"] in ("pass", "fail")
    assert marks[3]["verdict"] == mc.CANNOT and "needs >= 500" in marks[3]["detail"]  # 3 runs only
    base_row = next(r for r in rep["rows"] if r["label"] == rep["base_label"])
    assert base_row["control"]["kind"] == "exposure_matched" and base_row["control"]["runs"] == 3
    tag = f.stem
    spy = pd.read_csv(data / "reports" / tag / "spy_daily.csv")
    assert list(spy.columns) == ["date", "ret", "ret_total", "ret_price_only"] and len(spy) == rep["oos_sessions"]
    assert np.allclose(spy["ret"], spy["ret_total"]) and (spy["ret_total"] >= spy["ret_price_only"] - 1e-12).all()
    assert (data / "reports" / tag / "control_trend_momentum_base_1x_cap100_hold.json").is_file()
    daily = pd.read_csv(data / "reports" / base_row["files"]["daily"])
    assert {"cost", "traded", "dividends"} <= set(daily.columns)
    logged = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    mine = [r for r in logged if r["universe_mode"] == "point_in_time"]
    assert len(mine) == 29 * 3 and all(r["prereg_sha256"] and len(r["gate_v2_sha256"]) == 64 for r in mine)
    today = [r for r in logged if r["universe_mode"] == "today"]
    assert len(today) == 29 and {r["bars"] for r in today} == {"store"}
    for args, code in ((["--universe", "pit"], 0), (["--universe", "today"], 1),
                       (["--universe", "today", "--bars", "store"], 0)):
        mp.setattr(sys, "argv", ["momentum_core.py", "--has", "trend_momentum", *args, *base])
        with pytest.raises(SystemExit) as e:
            core_script.main()
        assert e.value.code == code, args


def test_pit_universe_buys_only_members(pit_runs):
    data, panel, ms, keys, base, out, mp = pit_runs
    for version in mc.VERSIONS:
        f, rep = core_script.find_report(data / "reports", "balanced", version, mode="point_in_time")
        for row in rep["rows"]:
            tr = pd.read_csv(data / "reports" / row["files"]["trades"])
            p27 = tr[tr["symbol"] == "P27"]
            assert (pd.to_datetime(p27["date"]) > pd.Timestamp(panel.dates[350])).all()  # joined in session 350
            p28 = tr[tr["symbol"] == keys["P28"]]
            assert (pd.to_datetime(p28["date"]) <= pd.Timestamp(panel.dates[400])).all()  # left in session 400


def test_core_pit_summary_compares_today_with_the_point_in_time_universe(pit_runs):
    data, panel, ms, keys, base, out, mp = pit_runs
    text = (data / "reports" / core_script.SUMMARY_PIT).read_text()
    assert text.startswith("# Momentum core, ten-year test on the point-in-time S&P 500")
    assert "BENCHMARK: SPY buy-and-hold (total return, dividends reinvested)" in text
    assert "BENCHMARK: SPY buy-and-hold (price only, no dividends)" in text
    assert "BENCHMARK: equal-weight index members of each month, rebalanced monthly, after costs" in text
    assert "## Today's list against the point-in-time universe (base rows)" in text
    assert "| | Today's list (minute bars) | Today's list (daily store) | Point-in-time S&P 500 (daily store) |" in text
    assert text.count("Gate B mark 5 (the base row still passes marks 1-2 on the point-in-time universe)") == 3
    assert "not run" in text  # no minute-bar run in this folder
    assert text.count("### `") >= 6 and mc.NOT_A_PASS in text


def test_spy_total_return_is_read_from_the_store_for_a_minute_bar_run(pit_runs, tmp_path):
    data, panel, ms, keys, base, out, mp = pit_runs
    tr, note = core_script.spy_total_return_from_store(data, "SPY", panel.dates)
    c = panel.c[:, panel.col("SPY")]
    assert tr[300] == pytest.approx((c[300] + 1.6) / c[299] - 1)
    assert note["source"].startswith("table completed") and note["dividends_per_year"][2023] == 4  # 2022: none
    assert tr[301] == pytest.approx(c[301] / c[300] - 1)
    none, why = core_script.spy_total_return_from_store(tmp_path, "SPY", panel.dates)
    assert none is None and "fetch_daily.sh" in why


def test_run_experiments_has_the_core_pit_set_and_it_is_documented():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in ('"${1:-}" = "core_pit"', "--universe pit", "--summary --set core_pit",
                   "experiments_core_pit_summary.md", 'KAIBOT_CORE_CONTROL_RUNS:-500'):
        assert needle in sh, needle
    assert sh.index('"${1:-}" = "core_pit"') < sh.index("No .env found")  # needs no .env
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "run_experiments.sh")]).returncode == 0
    doc = (ROOT / "docs" / "m0" / "reproduce.md").read_text()
    for needle in (".venv/bin/python scripts/fetch_sp500_history.py", "./scripts/fetch_daily.sh",
                   ".venv/bin/python scripts/fetch_sp500_history.py --holes", "./scripts/run_experiments.sh core_pit",
                   "experiments_core_pit_summary.md", "--costs v2"):
        assert needle in doc, needle
    seq = [doc.index(x) for x in ("scripts/fetch_sp500_history.py\n", "./scripts/fetch_daily.sh\n",
                                  "fetch_sp500_history.py --holes", "./scripts/run_experiments.sh core_pit\n")]
    assert seq == sorted(seq)


def test_cost_model_v2_runs_on_the_store_market_and_in_the_control():
    from kaibot.broker.costs_v2 import get_model

    panel, stocks = raw_market(actions=[("P20", 430, 4.0, None), ("SPY", 300, None, 1.5)])
    m = mc.Market.from_panel(panel, stocks, "SPY")
    spec = mc.VERSIONS["trend_momentum"]
    v1, _ = m.run(POL, spec, I0, N - 1)
    v2, rule = m.run(POL, spec, I0, N - 1, costs=get_model("v2"))
    c1, c2 = (pd.DataFrame(b.daily)["cost"].sum() for b in (v1, v2))
    assert c2 != c1 and c2 > 0
    c = mc.exposure_matched_control(m, POL, spec, v2, rule, I0, N - 1, runs=3, costs=get_model("v2"))
    r = c["control_result"]
    assert r["simulated_strategy_sharpe"] == pytest.approx(r["strategy_sharpe"], abs=1e-9)
    assert np.isfinite(m.equal_weight(I0, N - 1, costs=get_model("v2"))).all()
