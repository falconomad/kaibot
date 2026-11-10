"""Story S08: walk-forward folds, train-only parameter choice, determinism, and parity with a plain replay."""

import copy
import sys
from pathlib import Path

import numpy as np
import pytest

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.events import events_digest
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import run_portfolio
from kaibot.replay.walkforward import (
    DEFAULT,
    GRIDS,
    MonthStats,
    Params,
    Window,
    choose,
    first_train_month,
    make_folds,
    make_grid,
    month_bounds,
    run_walk_forward,
)
from kaibot.risk.profiles import load_profiles


def market(seed: int, first: str, last: np.datetime64):
    """Like test_determinism.market() but over ~10 months and with SECTOR-wide sell-offs and rebounds (stock =
    sector + noise), so the residual filter passes and every grid configuration trades."""
    rng = np.random.default_rng(seed)
    dates = np.array([d for d in np.arange(np.datetime64(first), last + np.timedelta64(1, "D")) if np.is_busday(d)],
                     dtype="datetime64[D]")
    nd = len(dates)

    def bars(name, r, gap, price):
        c = price * np.exp(np.cumsum(gap)[:, None] + np.cumsum(r, axis=1))
        o = np.c_[c[:, :1], c[:, :-1]]
        h = np.maximum(o, c) * (1 + 0.0002)
        lo = np.minimum(o, c) * (1 - 0.0002)
        v = rng.uniform(2e5, 6e5, (nd, N_MIN))
        return SymbolDays(name, dates, o, h, lo, c, v, (h + lo + c) / 3)

    def sector_moves():
        r = rng.normal(0, 0.0003, (nd, N_MIN))
        for d in range(60, nd, 2):
            m = int(rng.integers(30, 220))
            depth = rng.uniform(0.0006, 0.0022)
            r[d, m:m + 25] -= depth
            r[d, m + 30:m + 70] += depth * rng.uniform(0.3, 1.0)
        return r, rng.normal(0, 0.008, nd)

    etfs, stocks, sector_of = {}, {}, {}
    for e, names in (("XLK", ("AAA", "BBB", "CCC")), ("XLF", ("DDD", "EEE", "FFF"))):
        r, gap = sector_moves()
        etfs[e] = bars(e, r, gap, 100.0)
        for i, s in enumerate(names):
            sector_of[s] = e
            stocks[s] = bars(s, r + rng.normal(0, 0.0004, r.shape), gap + rng.normal(0, 0.004, nd), 50 + 30 * i)
    spy = bars("SPY", rng.normal(0, 0.0002, (nd, N_MIN)), rng.normal(0, 0.005, nd), 400.0)
    return stocks, etfs, sector_of, spy

FIRST, LAST = "2024-01-02", np.datetime64("2024-10-31")
POL = load_profiles()["balanced"]
GRID = make_grid(GRIDS["small"])  # 4 configs, default first


def _slice(sd: SymbolDays | None, lo, hi) -> SymbolDays | None:
    if sd is None:
        return None
    k = (sd.dates >= lo) & (sd.dates <= hi)
    if not k.any():
        return None
    return SymbolDays(sd.symbol, sd.dates[k], *(getattr(sd, a)[k] for a in ("o", "h", "l", "c", "v", "vw")))


def _regime(spy):
    return {d: (np.zeros(N_MIN, dtype=bool), 18.0) for d in spy.dates}


@pytest.fixture(scope="module")
def mkt():
    return market(11, FIRST, LAST)


def _run(mkt, block_months=3, train_months=2, min_trades=5, grid=GRID):
    stocks, etfs, sector_of, spy = mkt
    seen = []

    def load(lo, hi):
        seen.append((lo, hi))
        return Window({s: _slice(sd, lo, hi) for s, sd in stocks.items()},
                      {e: _slice(sd, lo, hi) for e, sd in etfs.items()}, _slice(spy, lo, hi))

    folds = make_folds(first_train_month(FIRST), LAST, train_months=train_months)
    wf = run_walk_forward(load, sector_of, {}, _regime, set(), POL, folds, grid, variant="A",
                          min_train_trades=min_trades, block_months=block_months)
    return wf, seen


@pytest.fixture(scope="module")
def wf_run(mkt):
    return _run(mkt)


def test_folds_roll_and_never_overlap():
    first = first_train_month("2016-01-01")
    assert str(first) == "2016-05"  # 120-day warm-up before the first train session
    folds = make_folds(first, np.datetime64("2026-11-06"))
    assert str(folds[0].test_month) == "2017-05" and str(folds[-1].test_month) == "2026-11"
    assert len(folds) == 115
    for a, b in zip(folds, folds[1:], strict=False):
        assert b.test_month == a.test_month + np.timedelta64(1, "M")  # roll by one month
        assert b.test_start > a.test_end  # test windows never overlap
    for f in folds:
        assert len(f.train_months) == 12
        assert f.train_end < f.test_start  # test never touches train
        assert f.test_month not in f.train_months
        assert f.train_months[-1] + np.timedelta64(1, "M") == f.test_month  # contiguous
        assert all(f.test_month != g.test_month for g in folds if g is not f)
    assert make_folds(first, np.datetime64("2017-04-30")) == []


def test_grid_has_default_first_and_at_most_three_params():
    g = make_grid(GRIDS["default"])
    assert len(g) == 27 and g[0] == DEFAULT and len(set(g)) == 27
    assert Params(-1.5, 1.5, 2.0) == DEFAULT  # today's pre-registered variant A values
    with pytest.raises(ValueError):
        make_grid({"z_max": (-2.0,)})  # must contain the default
    with pytest.raises(ValueError):
        make_grid({"z_max": (-1.5,), "foo": (1,)})


def _ms(rets, trades):
    return MonthStats(np.datetime64("2024-01", "M"), np.array([], dtype="datetime64[D]"), np.asarray(rets, float),
                      trades, "")


def test_choose_best_sharpe_with_trade_floor():
    a, b, c = Params(-1.5, 1.5, 2.0), Params(-2.0, 1.5, 2.0), Params(-1.5, 2.0, 2.0)
    train = {a: [_ms([0.001, -0.001, 0.0005], 50)],
             b: [_ms([0.002, 0.0021, 0.0019], 3)],  # best Sharpe but under the floor
             c: [_ms([0.001, 0.0005, 0.0008], 50)]}
    sel = choose(train, [a, b, c], min_trades=10)
    assert sel.params == c and sel.eligible == 2 and not sel.fallback
    sel = choose(train, [a, b, c], min_trades=1000)
    assert sel.params == a and sel.fallback
    tie = {a: [_ms([0.001, 0.002], 50)], c: [_ms([0.001, 0.002], 50)]}
    assert choose(tie, [a, c], 1).params == a  # ties -> grid order (default first)


def test_walk_forward_trades_and_loads_per_block(wf_run):
    wf, seen = wf_run
    assert len(wf.folds) == 4  # train May-Jun -> test Jul ... Oct
    assert sum(f.tuned_stats.trades for f in wf.folds) >= 5, "fixture must trade OOS"
    # loads: one window per block, each starting 120 days before the block; never the whole history at once
    assert len(seen) == 2
    for lo, hi in seen:
        assert (hi - lo).astype(int) < 120 + 3 * 31 + 1
    # choices are among the grid; the OOS matrix covers every config on every test session
    dates, m = wf.oos_matrix()
    assert m.shape == (len(dates), len(GRID)) and len(dates) == sum(len(f.tuned_stats.dates) for f in wf.folds)
    for f in wf.folds:
        assert f.selection.params in GRID
        assert f.tuned_stats == wf.stats[f.fold.test_month][f.selection.params]
        assert np.all((f.tuned_stats.dates >= f.fold.test_start) & (f.tuned_stats.dates <= f.fold.test_end))


def test_choice_uses_only_train_data(mkt, wf_run):
    """Scrambling every price in a fold's test month must not change that fold's choice (nor earlier folds')."""
    wf, _ = wf_run
    fold = wf.folds[1]
    stocks, etfs, sector_of, spy = copy.deepcopy(mkt)
    rng = np.random.default_rng(3)
    for sd in [*stocks.values(), *etfs.values(), spy]:
        k = (sd.dates >= fold.fold.test_start) & (sd.dates <= fold.fold.test_end)
        f = np.exp(rng.normal(0, 0.02, (int(k.sum()), N_MIN)).cumsum(axis=1))
        for a in ("o", "h", "l", "c", "vw"):
            getattr(sd, a)[k] *= f
        sd.v[k] *= 3
    wf2, _ = _run((stocks, etfs, sector_of, spy))
    for a, b in zip(wf.folds[:2], wf2.folds[:2], strict=True):
        assert a.selection == b.selection
    for m in fold.fold.train_months:  # train stats are untouched by test-window prices
        assert all(wf.stats[m][p].events_sha256 == wf2.stats[m][p].events_sha256 for p in GRID)
    # ...and the perturbation did reach the test month (otherwise the check is vacuous)
    assert wf.folds[1].tuned_stats.events_sha256 != wf2.folds[1].tuned_stats.events_sha256


def test_deterministic_hashes(mkt, wf_run):
    wf, _ = wf_run
    wf2, _ = _run(copy.deepcopy(mkt))
    assert wf.events_sha256("tuned") == wf2.events_sha256("tuned")
    assert wf.events_sha256("default") == wf2.events_sha256("default")
    assert wf.input_sha256 == wf2.input_sha256
    assert [f.selection for f in wf.folds] == [f.selection for f in wf2.folds]


def test_block_size_does_not_change_results(mkt, wf_run):
    wf, _ = wf_run
    wf1, seen = _run(mkt, block_months=1)
    assert len(seen) == 6
    assert wf.events_sha256("tuned") == wf1.events_sha256("tuned")
    assert wf.events_sha256("default") == wf1.events_sha256("default")


def test_no_tuning_path_reproduces_run_portfolio(mkt, wf_run):
    """The default configuration on a test month is exactly a plain replay of that month."""
    wf, _ = wf_run
    stocks, etfs, sector_of, spy = mkt
    for f in wf.folds:
        lo, hi = month_bounds(f.fold.test_month)
        prep = prepare(stocks, etfs, sector_of, {}, lo, hi, z_max=DEFAULT.z_max)
        reg = {d: v for d, v in _regime(spy).items() if lo <= d <= hi}
        book = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant="A")
        assert events_digest(book) == events_digest(f.default) == f.default_stats.events_sha256
        assert len(book.trades) == f.default_stats.trades


def test_default_params_leave_replay_unchanged(mkt):
    """Exposing atr_mult / target_r must not change a default replay (target_r=None == the variant's value)."""
    stocks, etfs, sector_of, spy = mkt
    lo, hi = np.datetime64("2024-06-01"), np.datetime64("2024-08-31")
    prep = prepare(stocks, etfs, sector_of, {}, lo, hi)
    reg = {d: v for d, v in _regime(spy).items() if lo <= d <= hi}
    for variant, tr in (("A", 2.0), ("v1", 1.5)):
        a = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant=variant)
        b = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant=variant, target_r=tr, atr_mult=1.5)
        assert events_digest(a) == events_digest(b)
    base = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant="A")
    wider = run_portfolio(prep.bars, prep.cands, reg, set(), POL, variant="A", target_r=3.0)
    assert len(base.trades) and events_digest(wider) != events_digest(base)


def test_report_summary(wf_run):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from walk_forward import markdown, summarize

    wf, _ = wf_run
    s = summarize(wf, 100_000.0, n_logged=7, blocks=4)
    assert s["folds"] == 4 and s["dsr"]["n_trials"] == len(GRID) * 4 + 7
    assert s["tuned"]["trades"] == sum(len(f.tuned.trades) for f in wf.folds) > 0
    assert 0 <= s["pbo_across_grid"]["pbo"] <= 1 and 0 <= s["tuned_beat_default_share"] <= 1
    assert s["spy"]["return_pct"] is not None and len(s["per_fold"]) == 4
    rep = {"summary": s, "variant": "A", "level": "balanced", "grid_name": "small", "grid_axes": GRIDS["small"],
           "min_train_trades": 5, "input_sha256": wf.input_sha256, "events_sha256_tuned": "x",
           "events_sha256_default": "y", "code_sha": "z", "caveats": ["c"]}
    assert "| t-stat per trade |" in markdown(rep)
