"""M11 ticket 2.7 (#458): the index core. Written before the code (CLAUDE.md: tests first for risk/gate.py and
sizing). docs/prereg/index_core.md is the frozen construction; docs/research/event_driven/design.md section 2.9 the
risk-rule design.

- `BookLimits.max_index_pct`: the index fund's own cap, separate from the per-name cap; hard ceiling 97%; 0 by
  default, so every live profile holds no index fund;
- the portfolio builder's index mode (`PortfolioBuilder(index_symbol="SPY")`, ranked fill only): the index fund
  takes the rest of the book, never more than `max_index_pct`, the exposure cap, the cash less the reserve or the
  gap-scenario room; exempt from the per-name cap, the sector cap and volatility sizing; one name;
- a sleeve's buy is funded by selling SPY at the same session (the swap), and the SPY sale is sent first;
- `strategies/index_core.IndexCore`: the weight of the pre-registration (a 15% volatility target on 21 returns);
- `config/index_core.yaml` is off and the live profiles are unchanged.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from kaibot.risk import gate
from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, gap_loss_ps, tighten_book, validate_book
from kaibot.risk.portfolio import Instrument, PortfolioBuilder
from kaibot.strategies.base import TargetList

hyp = pytest.importorskip("hypothesis")
st = hyp.strategies

D = date(2026, 11, 25)
EQ = 100_000.0
# Hunter's book for the core: the hard ceilings, 20% per name, 100% exposure, 97% index cap
LIM = replace(BOOK_HARD_CEILINGS, max_name_pct=20.0, max_gross_pct=100.0, max_overnight_pct=100.0,
              max_index_pct=97.0)


def builder(lim=LIM, **kw) -> PortfolioBuilder:
    kw.setdefault("index_symbol", "SPY")
    return PortfolioBuilder(lim, fill="ranked", cash_reserve=0.03, **kw)


def spy(px=500.0, atr=5.0) -> Instrument:
    return Instrument(px=px, atr20=atr, sector=None)


def stock(px=100.0, atr=2.0, sector="tech") -> Instrument:
    return Instrument(px=px, atr20=atr, sector=sector)


def tl(weights, name="book", **kw) -> TargetList:
    return TargetList(name, D, weights, **kw)


def value(plan, market, pred) -> float:
    return sum(q * market[s].px for (_, s), q in plan.targets.items() if pred(s))


# ---------------------------------------------------------------- the limit and its ceiling


def test_max_index_pct_is_its_own_cap_with_a_ceiling_of_97_and_off_by_default():
    assert BookLimits().max_index_pct == 0.0  # no index fund unless a book asks for one
    assert BOOK_HARD_CEILINGS.max_index_pct == 97.0  # 100% less the builder's 3% cash reserve (design.md 2.9)
    assert BOOK_HARD_CEILINGS.max_name_pct == 20.0  # the per-name cap is unchanged and separate
    with pytest.raises(ValueError, match="max_index_pct"):
        validate_book(replace(LIM, max_index_pct=97.5))
    with pytest.raises(ValueError):
        validate_book(replace(LIM, max_index_pct=-1))
    assert tighten_book(LIM, {"max_index_pct": 57}).max_index_pct == 57.0  # an owner may only lower it
    with pytest.raises(ValueError):
        tighten_book(replace(LIM, max_index_pct=57.0), {"max_index_pct": 60})
    assert gate.index_cap_dollars(EQ, LIM) == pytest.approx(97_000.0)


def test_the_live_profiles_hold_no_index_fund():
    from kaibot.risk import profiles

    for research in (False, True):
        for name, lim in profiles.load_book_limits(research=research).items():
            assert lim.max_index_pct == 0.0, name
    for level in profiles.load_profiles():
        assert profiles.rule_book_limits("trend_momentum", level).max_index_pct == 0.0


def test_the_index_core_config_is_off_and_matches_the_preregistration():
    from kaibot.risk import index_core as ic

    cfg = ic.load()
    assert cfg.enabled is False and cfg.index_symbols == ("SPY",)
    assert {lv: c.max_index_pct for lv, c in cfg.levels.items()} == {"conservative": 27.0, "balanced": 57.0,
                                                                      "aggressive": 97.0}
    assert {lv: c.exposure_pct for lv, c in cfg.levels.items()} == {"conservative": 30.0, "balanced": 60.0,
                                                                     "aggressive": 100.0}
    for lv, c in cfg.levels.items():
        lim = ic.book_limits(lv, cfg)
        assert lim.max_index_pct == c.max_index_pct and lim.max_gross_pct == c.exposure_pct
        validate_book(lim)


def test_a_bad_index_core_config_is_refused(tmp_path):
    from kaibot.risk import index_core as ic

    good = (ic.DEFAULT_PATH).read_text()
    for bad, why in ((good.replace("max_index_pct: 97", "max_index_pct: 98"), "max_index_pct"),
                     (good.replace("enabled: false", "enabled: maybe"), "enabled"),
                     (good.replace("max_index_pct: 57", "max_index_pct: 59"), "exposure")):
        f = tmp_path / "index_core.yaml"
        f.write_text(bad)
        with pytest.raises(ValueError, match=why):
            ic.load(f)


# ---------------------------------------------------------------- the builder's index mode: worked examples


def test_index_mode_is_off_without_an_index_symbol_so_every_existing_plan_is_unchanged():
    plain = PortfolioBuilder(LIM, fill="ranked", cash_reserve=0.03)
    market = {"AAA": stock(), "SPY": spy()}
    a = plain.build([tl({"AAA": 1.0})], {}, market, equity=EQ, cash=EQ)
    b = builder().build([tl({"AAA": 1.0})], {}, market, equity=EQ, cash=EQ)
    assert a.targets == b.targets and a.sequence == b.sequence
    with pytest.raises(ValueError, match="ranked"):
        PortfolioBuilder(LIM, index_symbol="SPY")


def test_the_core_alone_holds_spy_up_to_the_cap_less_the_reserve_in_whole_shares():
    market = {"SPY": spy(px=500.0)}
    p = builder().build([tl({"SPY": 1.0})], {}, market, equity=EQ, cash=EQ)
    assert p.targets[("book", "SPY")] == 194  # floor(97,000 / 500)
    assert p.binding[("book", "SPY")].startswith("index")
    half = builder().build([tl({"SPY": 0.5})], {}, market, equity=EQ, cash=EQ)
    assert half.targets[("book", "SPY")] == 97  # floor(0.5 x 97,000 / 500)
    bal = builder(replace(LIM, max_index_pct=57.0, max_gross_pct=60.0, max_overnight_pct=60.0))
    assert bal.build([tl({"SPY": 1.0})], {}, market, equity=EQ, cash=EQ).targets[("book", "SPY")] == 114


def test_the_index_is_exempt_from_the_name_cap_the_sector_cap_and_volatility_sizing():
    # 97% in one symbol: far above the 20% per-name cap and the 40% sector cap; ATR would size it at 10%
    p = builder().build([tl({"SPY": 1.0})], {}, {"SPY": spy(atr=5.0)}, equity=EQ, cash=EQ)
    assert p.targets[("book", "SPY")] * 500.0 > 0.9 * EQ and not p.guarded


def test_a_zero_index_cap_holds_no_index_and_sells_one_that_is_held():
    lim0 = replace(LIM, max_index_pct=0.0)
    p = builder(lim0).build([tl({"SPY": 1.0})], {("book", "SPY"): 100}, {"SPY": spy()}, equity=EQ, cash=EQ / 2)
    assert p.targets[("book", "SPY")] == 0 and p.orders[("book", "SPY")] == -100


def test_the_gap_scenario_holds_less_spy_and_the_rest_stays_cash():
    # a 2020-like market: SPY's ATR20 at 6% of its price -> gap 18% a share; 15% of equity / 18% = 83% of equity
    p = builder().build([tl({"SPY": 1.0})], {}, {"SPY": spy(px=500.0, atr=30.0)}, equity=EQ, cash=EQ)
    q = p.targets[("book", "SPY")]
    assert q * gap_loss_ps(500.0, 30.0) <= 0.15 * EQ + 1e-6 and q == math.floor(0.15 * EQ / 90.0)
    assert p.binding[("book", "SPY")] == "index_gap"


def test_the_band_keeps_a_holding_within_ten_percent_that_fits():
    market = {"SPY": spy(px=500.0)}
    near = builder().build([tl({"SPY": 1.0})], {("book", "SPY"): 185}, market, equity=EQ, cash=EQ - 185 * 500)
    assert near.targets[("book", "SPY")] == 185 and not near.orders
    far = builder().build([tl({"SPY": 1.0})], {("book", "SPY"): 150}, market, equity=EQ, cash=EQ - 150 * 500)
    assert far.targets[("book", "SPY")] == 194


def test_the_swap_a_sleeve_buy_is_funded_by_selling_spy_first():
    """The book is 97% SPY with 3% cash; the list adds a stock. SPY is cut at the same decision by what the stock
    needs, the SPY sale comes first in the plan, and the cash never goes below the reserve."""
    market = {"SPY": spy(px=500.0), "AAA": stock(px=100.0, atr=2.0)}
    held = {("book", "SPY"): 194}
    cash = EQ - 194 * 500.0
    p = builder().build([tl({"AAA": 1.0, "SPY": 1.0})], held, market, equity=EQ, cash=cash)
    assert p.targets[("book", "AAA")] == 50  # its own volatility size: 100,000 x 0.001 / 2
    assert p.orders[("book", "SPY")] < 0 and p.orders[("book", "AAA")] == 50
    assert p.sequence.index(("book", "SPY")) < p.sequence.index(("book", "AAA"))
    after = cash - sum(q * market[s].px for (_, s), q in p.orders.items())
    assert after >= 0.03 * EQ - 1e-6
    assert value(p, market, lambda s: True) <= 0.97 * EQ + 1e-6


def test_a_sleeve_budget_caps_everything_but_the_index():
    market = {"SPY": spy(), **{f"S{i}": stock(atr=1.0) for i in range(10)}}
    p = builder(sleeve_budget_pct=10.0).build([tl({**{f"S{i}": 1.0 for i in range(10)}, "SPY": 1.0})], {}, market,
                                              equity=EQ, cash=EQ)
    assert value(p, market, lambda s: s != "SPY") <= 0.10 * EQ + 1e-6
    assert value(p, market, lambda s: True) <= 0.97 * EQ + 1e-6
    assert value(p, market, lambda s: s == "SPY") >= 0.85 * EQ


def test_spy_counts_as_one_name():
    market = {"SPY": spy(), **{f"S{i}": stock(atr=10.0) for i in range(5)}}
    p = builder(replace(LIM, max_names=3)).build([tl({**{f"S{i}": 1.0 for i in range(5)}, "SPY": 1.0})], {},
                                                 market, equity=EQ, cash=EQ)
    names = {s for (_, s), q in p.targets.items() if q > 0}
    assert "SPY" in names and len(names) == 3


def test_only_one_list_may_name_the_index():
    with pytest.raises(ValueError, match="index"):
        builder().build([tl({"SPY": 1.0}, "a"), tl({"SPY": 1.0}, "b")], {}, {"SPY": spy()}, equity=EQ, cash=EQ)


# ---------------------------------------------------------------- properties


@hyp.settings(max_examples=250, deadline=None)
@hyp.given(spy_px=st.floats(50, 800), spy_atr_pct=st.floats(0.003, 0.08), w=st.floats(0, 1),
           held_spy=st.floats(0, 1), n_stocks=st.integers(0, 6), held_stock=st.floats(0, 1),
           cap=st.sampled_from([(27.0, 30.0), (57.0, 60.0), (97.0, 100.0)]),
           budget=st.sampled_from([None, 10.0, 25.0]), seed=st.integers(0, 10_000))
def test_property_no_buy_beyond_the_caps(spy_px, spy_atr_pct, w, held_spy, n_stocks, held_stock, cap, budget, seed):
    """Whatever the book holds (inside its caps) and whatever the list asks: after the plan the index fund is at
    most `max_index_pct`, the sleeve at most its budget, the book at most its exposure cap, every stock at most the
    per-name cap, the gap scenario at most 15% when the plan buys, and the cash after the plan's orders at least
    the 3% reserve whenever the plan buys anything."""
    idx_pct, exp_pct = cap
    lim = replace(LIM, max_index_pct=idx_pct, max_gross_pct=exp_pct, max_overnight_pct=exp_pct)
    rng = np.random.default_rng(seed)
    market = {"SPY": spy(spy_px, spy_px * spy_atr_pct)}
    for i in range(n_stocks):
        px = float(rng.uniform(10, 600))
        market[f"S{i}"] = stock(px, px * float(rng.uniform(0.005, 0.06)), f"x{i % 3}")
    # a held book inside its caps: the stocks inside the sleeve's room, SPY inside what is left
    sleeve_room = min(exp_pct, budget if budget is not None else exp_pct) / 100 * EQ * 0.9
    held: dict = {}
    used = 0.0
    for i in range(n_stocks // 2):
        px = market[f"S{i}"].px
        q = math.floor(min(held_stock * 0.04 * EQ, sleeve_room - used, lim.max_name_pct / 100 * EQ) / px)
        if q > 0:
            held[("book", f"S{i}")] = q
            used += q * px
    spy_room = min(idx_pct / 100 * EQ, exp_pct / 100 * EQ - used, EQ * 0.97 - used)
    q = math.floor(max(spy_room, 0) * held_spy / spy_px)
    if q > 0:
        held[("book", "SPY")] = q
    cash = EQ - sum(q * market[s].px for (_, s), q in held.items())
    weights = {f"S{i}": 1.0 for i in range(n_stocks)}
    weights["SPY"] = w
    p = builder(lim, sleeve_budget_pct=budget).build([tl(weights)], held, market, equity=EQ, cash=cash)
    final = {s: q for (_, s), q in p.targets.items()}
    for (_, s), q in held.items():
        final.setdefault(s, q)
    val = {s: q * market[s].px for s, q in final.items()}
    tol = 1e-6 * EQ
    assert val.get("SPY", 0.0) <= idx_pct / 100 * EQ + tol
    assert sum(val.values()) <= exp_pct / 100 * EQ + tol
    if budget is not None:
        assert sum(v for s, v in val.items() if s != "SPY") <= budget / 100 * EQ + tol
    for s, v in val.items():
        if s != "SPY":
            assert v <= lim.max_name_pct / 100 * EQ + tol
    buys = {k: o for k, o in p.orders.items() if o > 0}
    if buys:
        after = cash - sum(o * market[s].px for (_, s), o in p.orders.items())
        assert after >= 0.03 * EQ - tol
        gap = sum(q * gap_loss_ps(market[s].px, market[s].atr20) for s, q in final.items())
        assert gap <= lim.max_gap_loss_pct / 100 * EQ + tol
    # sales first: every sale (SPY's included) comes before every purchase in the plan
    kinds = [p.orders[k] > 0 for k in p.sequence]
    assert kinds == sorted(kinds)


@hyp.settings(max_examples=150, deadline=None)
@hyp.given(stock_px=st.floats(5, 900), atr_pct=st.floats(0.005, 0.08), spy_px=st.floats(100, 700),
           idx=st.sampled_from([27.0, 57.0, 97.0]))
def test_property_a_sleeve_buy_on_a_full_core_is_funded_by_spy_at_the_same_session(stock_px, atr_pct, spy_px, idx):
    """SPY holds the whole index cap; a sleeve buys one stock. The plan sells SPY at the same decision, never
    more SPY than the stock needs beyond the reserve, and the stock gets its full volatility size when it fits."""
    exp = idx + 3.0
    lim = replace(LIM, max_index_pct=idx, max_gross_pct=exp, max_overnight_pct=exp)
    market = {"SPY": spy(spy_px, spy_px * 0.01), "AAA": stock(stock_px, stock_px * atr_pct)}
    q0 = math.floor(idx / 100 * EQ / spy_px)
    cash = EQ - q0 * spy_px
    p = builder(lim).build([tl({"AAA": 1.0, "SPY": 1.0})], {("book", "SPY"): q0}, market, equity=EQ, cash=cash)
    a = p.targets.get(("book", "AAA"), 0)
    want = min(math.floor(EQ * 0.001 / (stock_px * atr_pct)), math.floor(lim.max_name_pct / 100 * EQ / stock_px))
    if want >= 1 and want * stock_px <= 0.5 * exp / 100 * EQ:
        assert a == want
    if a > 0:
        t = p.targets[("book", "SPY")]
        sold = q0 - t
        after = cash + sold * spy_px - a * stock_px
        assert after >= 0.03 * EQ - 1e-6 and t * spy_px + a * stock_px <= exp / 100 * EQ + 1e-6
        if q0 * spy_px + a * stock_px > exp / 100 * EQ or cash - a * stock_px < 0.03 * EQ:
            assert sold > 0 and p.sequence.index(("book", "SPY")) < p.sequence.index(("book", "AAA"))
        # SPY is cut no further than its cap, the exposure cap and the cash require (one share of rounding)
        room = min(idx / 100 * EQ, exp / 100 * EQ - a * stock_px, cash + q0 * spy_px - 0.03 * EQ - a * stock_px)
        assert t * spy_px >= room - spy_px - 1e-6


# ---------------------------------------------------------------- the strategy


def test_the_index_core_weight_is_a_15_percent_volatility_target_on_21_returns():
    from kaibot.strategies.index_core import IndexCore, index_weight

    rng = np.random.default_rng(7)
    calm = 100 * np.cumprod(1 + rng.normal(0, 0.005, 300))  # about 8% a year: no scaling
    wild = 100 * np.cumprod(1 + rng.normal(0, 0.03, 300))  # about 48% a year
    w, sigma = index_weight(calm, 21, 0.15)
    assert w == 1.0 and 0.04 < sigma < 0.12
    w, sigma = index_weight(wild, 21, 0.15)
    assert w == pytest.approx(0.15 / sigma) and 0.2 < w < 0.5
    r = np.diff(wild[-22:]) / wild[-22:-1]
    assert sigma == pytest.approx(float(np.std(r, ddof=1)) * math.sqrt(252))
    assert index_weight(np.array([100.0] * 5), 21, 0.15) == (1.0, None)  # no estimate: no scaling
    core = IndexCore()
    assert (core.vol_target, core.lookback, core.decide, core.name, core.rule) == (0.15, 21, "weekly", "index_core",
                                                                                   "index_core")
    assert {n.name for n in core.neighbours()} == {f"index_core~{k}" for k in
                                                   ("vol12", "vol18", "lookback63", "daily", "nocap")}
    assert IndexCore().with_params(cap=False).weight_of(wild) == 1.0


# ---------------------------------------------------------------- through the engine with SimBroker (the fixture)


@pytest.fixture(scope="module")
def fixture_data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return data, i0, len(data.dates) - 1


def test_the_core_through_the_engine_holds_only_spy_inside_its_cap(fixture_data):
    from kaibot.engine.daily import run_daily
    from kaibot.risk import index_core as ic
    from kaibot.risk.profiles import load_profiles
    from kaibot.strategies.index_core import IndexCore

    data, i0, i1 = fixture_data
    pol = load_profiles()["aggressive"]
    b = ic.builder("aggressive")
    run = run_daily(IndexCore(), data, pol, i0, i1, builder=b)
    assert {f["symbol"] for f in run.fills} <= {"SPY"} and run.fills
    assert not run.refused and not run.guards and not run.ledger.overdrafts and run.cash_gap < 0.01
    # sized at the previous close at most 97%; a close mark above that is the market's move since (never a buy
    # beyond the cap: the builder's property tests), and stays under the 1% cash floor
    for row in run.book.daily[5:-1]:
        assert row["exposure"] <= 0.99
    assert max(r["exposure"] for r in run.book.daily) > 0.5


def test_a_sleeve_buy_and_the_spy_sale_fill_at_the_same_close_through_the_engine(fixture_data):
    """A test-only book: the core plus one stock bought on one decision. The stock's buy and SPY's sale are sent
    at the same session, fill at the same closing print, and the venue's cash never goes below zero."""
    from kaibot.engine.daily import run_daily
    from kaibot.risk import index_core as ic
    from kaibot.risk.profiles import load_profiles
    from kaibot.strategies.index_core import IndexCore

    data, i0, i1 = fixture_data
    stock_sym = data.symbols[int(data.stocks[0])]
    core = IndexCore(decide="daily")
    day_buy = data.day(i0 + 30)

    class CoreWithOneBuy:
        name = rule = "core_test"
        mode, holding, schedule = core.mode, core.holding, core.schedule
        owner_filters = room = market_filter = False

        def on_event(self, ctx):
            t = core.on_event(ctx)
            if t is None:
                return None
            w = dict(t.weights)
            if ctx.session >= day_buy:
                w = {stock_sym: 1.0, **w}
            return replace(t, strategy=self.name, weights=w)

        def signals(self, v):
            return core.signals(v)

    pol = load_profiles()["aggressive"]
    run = run_daily(CoreWithOneBuy(), data, pol, i0, i1, builder=ic.builder("aggressive"))
    first = min(f["i"] for f in run.fills if f["symbol"] == stock_sym and f["side"] == "buy")
    assert any(f["i"] == first and f["symbol"] == "SPY" and f["side"] == "sell" for f in run.fills)
    assert not run.ledger.overdrafts and run.cash_gap < 0.01 and not run.refused
    for row in run.book.daily[5:-1]:
        assert row["exposure"] <= 0.99
