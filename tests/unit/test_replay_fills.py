"""Issue #75: the optional stricter replay fills (replay/fills.py).

- The default path (fills=None) is pinned to the digests origin/main produced before the option existed.
- A fill uses the bar after the signal bar and nothing later; the decision never sees the fill bar.
"""

from __future__ import annotations

import copy
import hashlib

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_determinism import END, START, market

from kaibot.replay.data import N_MIN
from kaibot.replay.events import events_digest
from kaibot.replay.fills import BASELINE, REALISTIC, FillModel, fill_model
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import buy_px, random_entry_runs, run_portfolio, sell_px
from kaibot.risk.profiles import load_profiles

POL = load_profiles()["balanced"]

# events_digest and sha256(round(random totals, 6)) of this fixture on origin/main @ 2ce38e7, before `fills` existed
# (numpy 2.5.4, pandas 3.1.1). If a numpy release ever changes the fixture's random stream, regenerate these on a
# checkout of that commit, not on the current code.
GOLDEN = {
    "v1": ("7baee4247a6cd126c27bb3b4c77f16961a6a28ded73d682db4f42b556e3eed09",
           "bf901da1fb9d91675280f7eff5236ac5e07c3c3e4d2bfbcf22da5f03f74bdb05"),
    "A": ("dda56d8455f19cd33e3e5b9ebe4febc12ecb8319e3e825ff2c958a91fdece471",
          "f17a4d183bda3e325734e9c99adb9f7f10fd4965fb29a7b1b3a0b208ba5dd693"),
}


@pytest.fixture(scope="module")
def mkt():
    stocks, etfs, sector_of, spy = market(11)
    prep = prepare(stocks, etfs, sector_of, {}, START, END)
    reg = {d: (np.zeros(N_MIN, dtype=bool), 18.0) for d in spy.dates if START <= d <= END}
    return prep, reg


def book_of(prep, reg, variant="A", fills=None, bars=None):
    return run_portfolio(bars or prep.bars, prep.cands, reg, set(), POL, variant=variant, fills=fills)


def rand_of(prep, book, variant, fills=None):
    tpd = pd.Series([t.date for t in book.trades]).value_counts().sort_index()
    return random_entry_runs(prep.bars, prep.atr5, prep.vwap, prep.sigma, tpd, POL, 100_000.0, n_runs=5,
                             variant=variant, fills=fills)


def first_trade(book):
    return min(book.trades, key=lambda t: (t.date, t.entry_m, t.symbol))


def row(sd, date):
    return int(np.nonzero(sd.dates == date)[0][0])


# --- default path unchanged ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["v1", "A"])
def test_default_fills_are_byte_identical_to_main(mkt, variant):
    prep, reg = mkt
    book = book_of(prep, reg, variant)
    rand = rand_of(prep, book, variant)
    assert len(book.trades) >= 5
    assert (events_digest(book), hashlib.sha256(np.round(rand, 6).tobytes()).hexdigest()) == GOLDEN[variant]


@pytest.mark.parametrize("variant", ["v1", "A"])
def test_no_op_fill_model_equals_the_default(mkt, variant):
    prep, reg = mkt
    a, b = book_of(prep, reg, variant), book_of(prep, reg, variant, fills=BASELINE)
    assert events_digest(a) == events_digest(b)
    assert np.array_equal(rand_of(prep, a, variant), rand_of(prep, b, variant, fills=BASELINE))


def test_cli_default_is_the_untouched_code_path():
    assert fill_model("baseline") is None
    assert fill_model("baseline", 2.0) == FillModel("baseline", slippage_bps=2.0)
    assert fill_model("realistic") == REALISTIC
    assert fill_model("realistic", 3.0).slippage_bps == 3.0
    with pytest.raises(ValueError):
        fill_model("optimistic")


# --- the fill model itself (properties) ---------------------------------------------------------------------------

qtys = st.floats(0, 1e6, allow_nan=False)
vols = st.floats(0, 1e9, allow_nan=False)
prices = st.floats(1, 5000, allow_nan=False)
sigmas = st.floats(0, 0.01, allow_nan=False)
models = st.builds(FillModel, name=st.just("x"), require_traded_bar=st.booleans(),
                   participation=st.one_of(st.none(), st.floats(0.001, 1.0)), slippage_bps=st.floats(0, 50))


@given(models, qtys, vols)
def test_entry_qty_never_exceeds_the_gate_and_respects_the_cap(fm, qty, vol):
    q = fm.entry_qty(qty, vol)
    assert 0 <= q <= qty
    if fm.participation is not None:
        assert q <= vol * fm.participation
        assert q == qty or q == np.floor(q)  # a capped fill is whole shares
    if fm.require_traded_bar and vol == 0:
        assert q == 0
    if fm.participation is None and not (fm.require_traded_bar and vol == 0):
        assert q == qty


@given(models, prices, st.floats(1, 1e5), sigmas, st.floats(1, 1e8))
def test_a_fill_model_only_ever_adds_cost(fm, px, qty, sigma, vol):
    assert fm.buy(px, qty, sigma, vol) >= buy_px(px, qty, sigma, vol)
    assert fm.sell(px, qty, sigma, vol) <= sell_px(px, qty, sigma, vol)
    assert fm.buy(px, qty, sigma, vol) > fm.sell(px, qty, sigma, vol)  # the spread is always crossed


@given(prices, st.floats(1, 1e5), sigmas, st.floats(1, 1e8), st.floats(0, 25), st.floats(0, 25))
def test_more_slippage_is_never_cheaper(px, qty, sigma, vol, a, b):
    lo, hi = FillModel("x", slippage_bps=min(a, b)), FillModel("x", slippage_bps=max(a, b))
    assert hi.buy(px, qty, sigma, vol) >= lo.buy(px, qty, sigma, vol)
    assert hi.sell(px, qty, sigma, vol) <= lo.sell(px, qty, sigma, vol)


@pytest.mark.parametrize("kw", [{"slippage_bps": -1.0}, {"slippage_bps": float("nan")}, {"participation": 0.0},
                                {"participation": 1.5}, {"participation": -0.1}])
def test_a_fill_model_cannot_be_made_optimistic(kw):
    with pytest.raises(ValueError):
        FillModel("x", **kw)


def test_nan_volume_counts_as_no_trade():
    assert REALISTIC.entry_qty(100, float("nan")) == 0


# --- next bar and point-in-time -----------------------------------------------------------------------------------

@pytest.mark.parametrize("fills", [None, REALISTIC, FillModel("x", slippage_bps=3.0)])
def test_every_entry_fills_on_the_bar_after_its_signal_bar(mkt, fills):
    prep, reg = mkt
    book = book_of(prep, reg, fills=fills)
    signals = {(r.symbol, r.date, int(r.m)): r for r in prep.cands.itertuples()}
    assert book.trades
    for t in book.trades:
        cnd = signals[(t.symbol, np.datetime64(t.date), t.entry_m - 1)]  # KeyError = not the next bar
        sd = prep.bars[t.symbol]
        r = row(sd, t.date)
        price = (fills.buy if fills else buy_px)(sd.o[r, t.entry_m], t.qty, cnd.sigma_1m, sd.v[r, t.entry_m])
        assert t.entry == pytest.approx(price, rel=1e-12)  # the open and volume of bar m+1, nothing else


def test_bars_after_the_fill_bar_cannot_change_the_entry(mkt):
    prep, reg = mkt
    t = first_trade(book_of(prep, reg, fills=REALISTIC))
    sd = copy.deepcopy(prep.bars[t.symbol])
    r = row(sd, t.date)
    for k in ("o", "h", "l", "c", "v", "vw"):  # rewrite the future of that session, after the fill bar
        getattr(sd, k)[r, t.entry_m + 1:] *= 0.5
    t2 = first_trade(book_of(prep, reg, fills=REALISTIC, bars={**prep.bars, t.symbol: sd}))
    assert (t2.symbol, t2.date, t2.entry_m, t2.qty, t2.entry) == (t.symbol, t.date, t.entry_m, t.qty, t.entry)


def test_no_print_on_the_next_bar_means_no_fill_and_no_later_fill(mkt):
    prep, reg = mkt
    t = first_trade(book_of(prep, reg, fills=REALISTIC))
    sd = copy.deepcopy(prep.bars[t.symbol])
    r = row(sd, t.date)
    sd.v[r, t.entry_m] = 0.0
    bars = {**prep.bars, t.symbol: sd}
    key = (t.symbol, t.date, t.entry_m)
    strict = book_of(prep, reg, fills=REALISTIC, bars=bars)
    assert key not in {(x.symbol, x.date, x.entry_m) for x in strict.trades}
    signal_bars = {int(m) for m in prep.cands[(prep.cands.symbol == t.symbol) & (prep.cands.date == t.date)].m}
    for x in strict.trades:  # anything that symbol still trades that day comes from its own signal bar
        if (x.symbol, x.date) == (t.symbol, t.date):
            assert x.entry_m - 1 in signal_bars
    # the default keeps filling at the carried-forward price: that is the optimism the option removes
    assert key in {(x.symbol, x.date, x.entry_m) for x in book_of(prep, reg, bars=bars).trades}


def test_fill_bar_volume_caps_the_quantity_but_not_the_decision(mkt):
    prep, reg = mkt
    t = first_trade(book_of(prep, reg, fills=REALISTIC))
    assert t.qty > 5
    sd = copy.deepcopy(prep.bars[t.symbol])
    r = row(sd, t.date)
    sd.v[r, t.entry_m] = 100.0  # 5% of 100 shares
    t2 = first_trade(book_of(prep, reg, fills=REALISTIC, bars={**prep.bars, t.symbol: sd}))
    assert (t2.symbol, t2.date, t2.entry_m) == (t.symbol, t.date, t.entry_m)  # still decided, on the same bar
    assert t2.qty == 5.0 and t2.risk == pytest.approx(t.risk / t.qty * 5.0)


def test_slippage_raises_the_entry_and_lowers_net_pnl_of_the_same_trade(mkt):
    prep, reg = mkt
    a = first_trade(book_of(prep, reg))
    b = first_trade(book_of(prep, reg, fills=FillModel("x", slippage_bps=5.0)))
    assert (a.symbol, a.date, a.entry_m, a.qty) == (b.symbol, b.date, b.entry_m, b.qty)
    sd = prep.bars[a.symbol]
    assert b.entry == pytest.approx(a.entry + sd.o[row(sd, a.date), a.entry_m] * 5e-4, rel=1e-12)


def test_random_baseline_pays_the_same_fills(mkt):
    prep, reg = mkt
    book = book_of(prep, reg)
    base = rand_of(prep, book, "A")
    costly = rand_of(prep, book, "A", fills=FillModel("x", slippage_bps=5.0))
    assert costly.shape == base.shape and (costly < base).all()
