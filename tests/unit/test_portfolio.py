"""Issue #119 (M7·S79): the portfolio builder. Written before the code (CLAUDE.md: tests first for sizing).

Targets (from multi-day strategies) + the current book + limits -> whole-share target positions per
(strategy, symbol). Pure: no clock, no broker, no database."""

import math
from dataclasses import replace
from datetime import date

import pytest

from kaibot.risk.gate import BookLimits, gap_loss_ps
from kaibot.risk.portfolio import Instrument, PortfolioBuilder, exposures
from kaibot.strategies.base import TargetList

hyp = pytest.importorskip("hypothesis")
st = hyp.strategies

D = date(2026, 11, 12)
# decision 15 section 6: tests run at Balanced with a 100% exposure cap
LIM = BookLimits(atr_risk_pct=0.1, max_name_pct=15, max_gross_pct=100, max_overnight_pct=100, max_sector_pct=30,
                 max_names=20, min_cash_pct=2, max_gap_loss_pct=12)
PB = PortfolioBuilder(LIM)


def tl(strategy, weights, **kw):
    return TargetList(strategy, D, weights, **kw)


def mk(px=100.0, atr=2.0, sector="tech"):
    return Instrument(px=px, atr20=atr, sector=sector)


# ------------------------------------------------------------------ worked examples
def test_volatility_sizing_worked_example():
    # shares = equity x 0.001 / ATR20 = 100,000 x 0.001 / 2 = 50 ($5,000, 5% of equity)
    plan = PB.build([tl("mom", {"AAA": 1.0})], {}, {"AAA": mk()}, equity=100_000)
    assert plan.targets == {("mom", "AAA"): 50}
    assert plan.orders == {("mom", "AAA"): 50}
    assert plan.binding[("mom", "AAA")] == "vol"


def test_weight_scales_and_is_clamped_to_one():
    plan = PB.build([tl("mom", {"AAA": 0.5, "BBB": 3.0, "CCC": -1.0})], {},
                    {"AAA": mk(), "BBB": mk(), "CCC": mk()}, equity=100_000)
    assert plan.targets[("mom", "AAA")] == 25 and plan.targets[("mom", "BBB")] == 50
    assert plan.targets.get(("mom", "CCC"), 0) == 0


def test_k_is_never_above_the_level_ceiling():
    wide = PortfolioBuilder(LIM, k=0.01)  # asks for 1% per ATR; the level allows 0.1%
    assert wide.build([tl("mom", {"AAA": 1.0})], {}, {"AAA": mk()}, equity=100_000).targets[("mom", "AAA")] == 50
    narrow = PortfolioBuilder(LIM, k=0.0005)  # a smaller k is taken as given
    assert narrow.build([tl("mom", {"AAA": 1.0})], {}, {"AAA": mk()}, equity=100_000).targets[("mom", "AAA")] == 25


def test_per_name_cap_binds_across_strategies():
    # ATR 0.5 -> 200 shares = $20,000 wanted by each; the name cap is 15% = $15,000 for the symbol in total
    plan = PB.build([tl("a", {"AAA": 1.0}), tl("b", {"AAA": 1.0})], {}, {"AAA": mk(atr=0.5)}, equity=100_000)
    assert plan.targets[("a", "AAA")] + plan.targets[("b", "AAA")] <= 150
    assert plan.binding[("a", "AAA")] == "name_cap"


def test_sector_cap_binds():
    names = {f"T{i}": mk(sector="tech") for i in range(10)}  # 10 x $5,000 = $50,000 wanted, 30% allowed
    plan = PB.build([tl("mom", dict.fromkeys(names, 1.0))], {}, names, equity=100_000)
    assert sum(plan.targets.values()) * 100 <= 30_000
    assert set(plan.binding.values()) == {"sector"}


def test_unknown_sector_is_one_bucket():
    names = {f"U{i}": mk(sector=None) for i in range(10)}
    plan = PB.build([tl("mom", dict.fromkeys(names, 1.0))], {}, names, equity=100_000)
    assert sum(plan.targets.values()) * 100 <= 30_000


def test_gross_cap_and_cash_buffer():
    names = {f"N{i}": mk(sector=f"s{i}") for i in range(20)}  # 20 x $5,000 = $100,000 wanted
    plan = PB.build([tl("mom", dict.fromkeys(names, 1.0))], {}, names, equity=100_000)
    assert sum(plan.targets.values()) * 100 <= 98_000  # 100% gross, 2% cash buffer
    assert plan.exposure.gross <= 98_000
    half = PortfolioBuilder(replace(LIM, max_gross_pct=30))
    assert sum(half.build([tl("mom", dict.fromkeys(names, 1.0))], {}, names,
                          equity=100_000).targets.values()) * 100 <= 30_000


def test_other_exposure_counts_toward_gross_but_not_overnight():
    names = {f"N{i}": mk(sector=f"s{i}") for i in range(4)}  # $20,000 wanted
    plan = PB.build([tl("mom", dict.fromkeys(names, 1.0))], {}, names, equity=100_000, other_exposure=90_000)
    assert sum(plan.targets.values()) * 100 <= 8_000
    assert plan.exposure.overnight == pytest.approx(sum(plan.targets.values()) * 100)


def test_overnight_cap_zero_buys_nothing():
    plan = PortfolioBuilder(replace(LIM, max_overnight_pct=0)).build([tl("mom", {"AAA": 1.0})], {},
                                                                    {"AAA": mk()}, equity=100_000)
    assert plan.targets.get(("mom", "AAA"), 0) == 0


def test_gap_scenario_cap():
    # wild names: ATR 5% -> gap loss 15% per $; 0.1%/5% = 2% of equity per name; 20 names = 40% -> 6% gap loss
    names = {f"W{i}": mk(atr=5.0, sector=f"s{i}") for i in range(20)}
    plan = PortfolioBuilder(replace(LIM, max_gap_loss_pct=3)).build([tl("mom", dict.fromkeys(names, 1.0))], {},
                                                                    names, equity=100_000)
    assert plan.exposure.gap_loss <= 3_000 + 1e-6
    assert "gap" in set(plan.binding.values())


def test_budget_per_strategy():
    names = {f"N{i}": mk(sector=f"s{i}") for i in range(10)}
    plan = PB.build([tl("a", dict.fromkeys(names, 1.0)), tl("b", {"ZZZ": 1.0})], {},
                    {**names, "ZZZ": mk(sector="z")}, equity=100_000, budgets={"a": 20})
    assert sum(q for (s, _), q in plan.targets.items() if s == "a") * 100 <= 20_000
    assert plan.targets[("b", "ZZZ")] == 50


def test_max_names_keeps_held_names_first_then_weight():
    names = {f"N{i:02d}": mk(sector=f"s{i}") for i in range(6)}
    weights = {"N00": 0.2, "N01": 0.9, "N02": 0.8, "N03": 0.7, "N04": 0.1, "N05": 0.6}
    plan = PortfolioBuilder(replace(LIM, max_names=3)).build([tl("mom", weights)], {("mom", "N04"): 5}, names,
                                                             equity=100_000)
    held = {sym for (_, sym), q in plan.targets.items() if q > 0}
    assert held == {"N04", "N01", "N02"}
    assert {s.symbol for s in plan.skipped if s.reason == "max_names"} == {"N00", "N03", "N05"}


def test_unaffordable_names_are_skipped_and_logged():
    # $10,000 equity: the name cap is $1,500, one share costs $3,000
    plan = PB.build([tl("mom", {"BIG": 1.0, "OK": 1.0})], {}, {"BIG": mk(px=3000, atr=60), "OK": mk(px=20, atr=0.4)},
                    equity=10_000)
    assert plan.targets.get(("mom", "BIG"), 0) == 0 and plan.targets[("mom", "OK")] > 0
    sk = [s for s in plan.skipped if s.symbol == "BIG"]
    assert sk and sk[0].reason == "unaffordable" and sk[0].strategy == "mom"


def test_missing_or_bad_data_keeps_the_position_and_logs():
    plan = PB.build([tl("mom", {"AAA": 1.0, "BBB": 1.0})], {("mom", "AAA"): 7},
                    {"AAA": mk(atr=float("nan")), "BBB": mk(atr=2)}, equity=100_000)
    assert plan.targets[("mom", "AAA")] == 7 and ("mom", "AAA") not in plan.orders
    assert any(s.symbol == "AAA" and s.reason == "no_data" for s in plan.skipped)


def test_held_symbol_without_a_price_is_an_error():
    with pytest.raises(ValueError, match="price"):
        PB.build([tl("mom", {"AAA": 1.0})], {("other", "ZZZ"): 3}, {"AAA": mk()}, equity=100_000)


def test_a_name_missing_from_the_list_is_sold():
    plan = PB.build([tl("mom", {"AAA": 1.0})], {("mom", "OLD"): 40, ("mom", "AAA"): 50},
                    {"AAA": mk(), "OLD": mk()}, equity=100_000)
    assert plan.targets[("mom", "OLD")] == 0 and plan.orders[("mom", "OLD")] == -40
    assert ("mom", "AAA") not in plan.orders  # already on target


def test_other_strategies_positions_are_left_alone():
    plan = PB.build([tl("mom", {"AAA": 1.0})], {("other", "BBB"): 30}, {"AAA": mk(), "BBB": mk()}, equity=100_000)
    assert ("other", "BBB") not in plan.orders and plan.targets.get(("other", "BBB"), 30) == 30


@pytest.mark.parametrize("current,expected_order", [(45, 5), (46, None), (50, None), (54, None), (55, -5)])
def test_no_trade_within_ten_percent_of_target(current, expected_order):
    plan = PB.build([tl("mom", {"AAA": 1.0})], {("mom", "AAA"): current}, {"AAA": mk()}, equity=100_000)
    assert plan.orders.get(("mom", "AAA")) == expected_order


def test_the_band_never_keeps_a_position_above_a_cap():
    # held 160 shares ($16,000) of a name capped at 15% = $15,000: within 10% of the target 150 but over the cap
    plan = PB.build([tl("mom", {"AAA": 1.0})], {("mom", "AAA"): 160}, {"AAA": mk(atr=0.5)}, equity=100_000)
    assert plan.targets[("mom", "AAA")] == 150 and plan.orders[("mom", "AAA")] == -10


def test_trend_filter_blocks_new_buys_when_declared():
    lst = tl("mom", {"NEW": 1.0, "HELD": 1.0}, index_filter=True)
    m = {"NEW": mk(), "HELD": mk()}
    down = PB.build([lst], {("mom", "HELD"): 20}, m, equity=100_000, index_up=False)
    assert down.targets.get(("mom", "NEW"), 0) == 0 and down.targets[("mom", "HELD")] == 20
    assert down.binding[("mom", "NEW")] == "trend"
    unknown = PB.build([lst], {}, m, equity=100_000, index_up=None)  # fail closed: unknown = down
    assert sum(unknown.targets.values()) == 0
    up = PB.build([lst], {("mom", "HELD"): 20}, m, equity=100_000, index_up=True)
    assert up.targets[("mom", "NEW")] == 50 and up.targets[("mom", "HELD")] == 50
    # a rule that does not declare the filter ignores the index
    free = PB.build([tl("mom", {"NEW": 1.0})], {}, m, equity=100_000, index_up=False)
    assert free.targets[("mom", "NEW")] == 50


def test_trend_filter_still_sells():
    plan = PB.build([tl("mom", {}, index_filter=True)], {("mom", "OLD"): 10}, {"OLD": mk()}, equity=100_000,
                    index_up=False)
    assert plan.orders[("mom", "OLD")] == -10


def test_exposures_helper_feeds_the_gate():
    m = {"AAA": mk(px=100, atr=2, sector="tech"), "BBB": mk(px=50, atr=5, sector="energy")}
    ex = exposures({("mom", "AAA"): 10, ("mom", "BBB"): 20}, m, other_exposure=500)
    assert ex.gross == pytest.approx(2_500) and ex.overnight == pytest.approx(2_000)
    assert ex.sectors == {"tech": 1_000, "energy": 1_000}
    assert ex.gap_loss == pytest.approx(10 * gap_loss_ps(100, 2) + 20 * gap_loss_ps(50, 5))
    f = ex.snapshot_fields("AAA", m)
    assert f == {"gross_exposure": 2_500, "overnight_exposure": 2_000, "sector_exposure": 1_000,
                 "gap_loss": pytest.approx(ex.gap_loss), "overnight": True}


def test_builder_is_deterministic_in_input_order():
    m = {f"N{i}": mk(px=10 + i, atr=0.3 + i / 10, sector=f"s{i % 3}") for i in range(12)}
    a = [tl("a", {f"N{i}": 1.0 for i in range(8)}), tl("b", {f"N{i}": 0.5 for i in range(4, 12)})]
    b = [tl("b", dict(reversed(list(a[1].weights.items())))), tl("a", dict(reversed(list(a[0].weights.items()))))]
    pa = PortfolioBuilder(replace(LIM, max_names=7)).build(a, {}, m, equity=50_000)
    pb = PortfolioBuilder(replace(LIM, max_names=7)).build(b, {}, m, equity=50_000)
    assert pa.targets == pb.targets and pa.orders == pb.orders


def test_one_list_per_strategy():
    with pytest.raises(ValueError, match="twice"):
        PB.build([tl("a", {"X": 1.0}), tl("a", {"Y": 1.0})], {}, {"X": mk(), "Y": mk()}, equity=100_000)


# ------------------------------------------------------------------ properties
SYMS = [f"S{i:02d}" for i in range(14)]


@st.composite
def scenario(draw):
    equity = draw(st.floats(1_000, 5e6))
    m = {}
    for s in SYMS:
        px = draw(st.floats(1, 3000))
        m[s] = Instrument(px=px, atr20=px * draw(st.floats(0.003, 0.12)),
                          sector=draw(st.sampled_from(["a", "b", "c", None])))
    lists = []
    for name in draw(st.lists(st.sampled_from(["r1", "r2", "r3"]), min_size=1, max_size=3, unique=True)):
        syms = draw(st.lists(st.sampled_from(SYMS), max_size=10, unique=True))
        lists.append(tl(name, {s: draw(st.floats(0, 1.5)) for s in syms}, index_filter=draw(st.booleans())))
    current = {}
    for name in ("r1", "r2", "r3"):
        for s in draw(st.lists(st.sampled_from(SYMS), max_size=4, unique=True)):
            # a small existing book, so the book itself starts inside the caps
            current[(name, s)] = int(equity * draw(st.floats(0, 0.01)) / m[s].px)
    lim = BookLimits(atr_risk_pct=draw(st.floats(0.01, 0.25)), max_name_pct=draw(st.floats(1, 20)),
                     max_gross_pct=draw(st.floats(15, 100)), max_overnight_pct=draw(st.floats(15, 100)),
                     max_sector_pct=draw(st.floats(5, 40)), max_names=draw(st.integers(1, 30)),
                     min_cash_pct=draw(st.floats(1, 20)), max_gap_loss_pct=draw(st.floats(2, 15)))
    budgets = {n: draw(st.floats(5, 100)) for n in ("r1", "r2") if draw(st.booleans())}
    return dict(equity=equity, m=m, lists=lists, current=current, lim=lim, budgets=budgets,
                index_up=draw(st.sampled_from([True, False, None])), k=draw(st.floats(0.0001, 0.01)))


def _book_fits(sc, book):
    """The caps hold for `book` (used to keep the property to books that start inside them)."""
    lim, eq, m = sc["lim"], sc["equity"], sc["m"]
    ex = exposures(book, m)
    by_sym: dict[str, float] = {}
    for (_, s), q in book.items():
        by_sym[s] = by_sym.get(s, 0) + q * m[s].px
    return (ex.gross <= eq * min(lim.max_gross_pct, 100 - lim.min_cash_pct) / 100
            and ex.overnight <= eq * lim.max_overnight_pct / 100
            and all(v <= eq * lim.max_sector_pct / 100 for v in ex.sectors.values())
            and ex.gap_loss <= eq * lim.max_gap_loss_pct / 100
            and all(v <= eq * lim.max_name_pct / 100 for v in by_sym.values())
            and len([s for s, v in by_sym.items() if v > 0]) <= lim.max_names)


@hyp.settings(max_examples=400, deadline=None, suppress_health_check=[hyp.HealthCheck.too_slow])
@hyp.given(sc=scenario())
def test_property_every_cap_holds_on_the_built_book(sc):
    hyp.assume(_book_fits(sc, sc["current"]))
    lim, eq, m = sc["lim"], sc["equity"], sc["m"]
    plan = PortfolioBuilder(lim, k=sc["k"]).build(sc["lists"], sc["current"], m, equity=eq,
                                                  budgets=sc["budgets"], index_up=sc["index_up"])
    book = {**sc["current"], **plan.targets}
    tol = 1e-6 * eq
    assert all(isinstance(q, int) and q >= 0 for q in plan.targets.values())  # whole shares, long only
    ex = exposures(book, m)
    assert ex.gross <= eq * min(lim.max_gross_pct, 100 - lim.min_cash_pct) / 100 + tol
    assert ex.overnight <= eq * lim.max_overnight_pct / 100 + tol
    assert all(v <= eq * lim.max_sector_pct / 100 + tol for v in ex.sectors.values())
    assert ex.gap_loss <= eq * lim.max_gap_loss_pct / 100 + tol
    by_sym: dict[str, float] = {}
    for (_, s), q in book.items():
        by_sym[s] = by_sym.get(s, 0) + q * m[s].px
    assert all(v <= eq * lim.max_name_pct / 100 + tol for v in by_sym.values())
    assert len([s for s, v in by_sym.items() if v > 0]) <= lim.max_names
    for strat, pct in sc["budgets"].items():
        spent = sum(q * m[s].px for (n, s), q in book.items() if n == strat)
        held_before = sum(q * m[s].px for (n, s), q in sc["current"].items() if n == strat)
        if strat in {t.strategy for t in sc["lists"]}:
            assert spent <= max(eq * pct / 100, 0) + tol or spent <= held_before + tol
    # orders are exactly target - current
    for key, q in plan.targets.items():
        d = q - sc["current"].get(key, 0)
        assert plan.orders.get(key, 0) == d


@hyp.settings(max_examples=300, deadline=None, suppress_health_check=[hyp.HealthCheck.too_slow])
@hyp.given(sc=scenario())
def test_property_volatility_size_and_trend_filter(sc):
    hyp.assume(_book_fits(sc, sc["current"]))
    lim, eq, m = sc["lim"], sc["equity"], sc["m"]
    plan = PortfolioBuilder(lim, k=sc["k"]).build(sc["lists"], sc["current"], m, equity=eq,
                                                  budgets=sc["budgets"], index_up=sc["index_up"])
    k = min(sc["k"], lim.atr_risk_pct / 100)
    for t in sc["lists"]:
        for s, w in t.weights.items():
            key = (t.strategy, s)
            q, cur = plan.targets.get(key, 0), sc["current"].get(key, 0)
            # never above the volatility size (a held position may sit up to 10% above it: the no-trade band)
            vol = eq * k * min(max(w, 0), 1) / m[s].atr20
            assert q <= max(vol, 0) + 1e-9 or (q == cur and q <= vol * 1.1 / 0.9 + 1)
            if t.index_filter and sc["index_up"] is not True:
                assert q <= cur  # no new buys below the index's 200-day average (or when it is unknown)


@hyp.settings(max_examples=200, deadline=None, suppress_health_check=[hyp.HealthCheck.too_slow])
@hyp.given(sc=scenario())
def test_property_skipped_names_are_logged(sc):
    plan = PortfolioBuilder(sc["lim"], k=sc["k"]).build(sc["lists"], {}, sc["m"], equity=sc["equity"],
                                                        budgets=sc["budgets"], index_up=True)
    wanted = {(t.strategy, s) for t in sc["lists"] for s, w in t.weights.items() if w > 0}
    got = {k for k, q in plan.targets.items() if q > 0}
    logged = {(x.strategy, x.symbol) for x in plan.skipped}
    # every wanted name ends up held or logged with a reason (or was cut to zero by a cap, also logged)
    assert wanted - got <= logged


# ------------------------------------------------------------------ #168: the ranked fill (the pre-registered sizing)
# Written before the code. The momentum pre-registrations size best-first: a buy that does not fit is cut to the
# room and buying stops; purchases fit in the cash plus the plan's sales less a 3% reserve (Amendment 3); held names
# are resized only on resize weeks, outside a 10% buffer, reductions first. The builder's `fill="ranked"` must give
# exactly that (engine/daily.momentum_builder configures it per rule; tests/unit/test_builder_parity.py compares it
# with the research harness on the fixture and a 730-stock decade).

RLIM = replace(LIM, max_sector_pct=40, max_gap_loss_pct=15, max_names=30)
RB = PortfolioBuilder(RLIM, fill="ranked", cash_reserve=0.03)


def rk(weights, resize=False, strategy="mom"):
    return tl(strategy, weights, resize=resize)


def test_ranked_buys_best_first_cuts_the_one_that_does_not_fit_and_stops():
    # each wants 50 shares x $100 = $5,000; the 12% cap leaves room for 2.4 names
    b = PortfolioBuilder(replace(RLIM, max_gross_pct=12, max_overnight_pct=12), fill="ranked")
    m = {s: mk(sector=s) for s in ("CCC", "AAA", "BBB", "DDD")}
    plan = b.build([rk({"CCC": 1.0, "AAA": 1.0, "BBB": 1.0, "DDD": 1.0})], {}, m, equity=100_000)
    assert plan.targets == {("mom", "AAA"): 50, ("mom", "BBB"): 20, ("mom", "CCC"): 50}
    assert plan.sequence == [("mom", "CCC"), ("mom", "AAA"), ("mom", "BBB")]  # the list's order, not the symbol's
    assert plan.binding[("mom", "BBB")] == "gross" and plan.binding[("mom", "DDD")] == "gross"
    assert ("mom", "DDD") not in plan.orders and {x.symbol for x in plan.skipped} == {"DDD"}
    assert plan.guarded == {}


def test_ranked_a_size_below_one_share_is_skipped_and_buying_goes_on():
    m = {"BIG": mk(px=5000.0, atr=600.0), "AAA": mk()}  # 100,000 x 0.001 / 600 = 0.17 shares
    plan = RB.build([rk({"BIG": 1.0, "AAA": 1.0})], {}, m, equity=100_000, cash=100_000)
    assert plan.targets == {("mom", "AAA"): 50}
    assert [(x.symbol, x.reason) for x in plan.skipped] == [("BIG", "unaffordable")]


def test_ranked_max_names_counts_kept_names_and_stops():
    b = PortfolioBuilder(replace(RLIM, max_names=2), fill="ranked")
    m = {s: mk(sector=s) for s in ("H", "A", "B")}
    plan = b.build([rk({"H": 1.0, "A": 1.0, "B": 1.0})], {("mom", "H"): 50}, m, equity=100_000)
    assert plan.orders == {("mom", "A"): 50} and plan.binding[("mom", "B")] == "max_names"


def test_ranked_cash_reserve_and_sales_fund_the_purchases():
    m = {s: mk(sector=s) for s in ("OLD", "A", "B")}
    # cash $2,000 + the sale of OLD (40 x $100) - 3% of $100,000 = $3,000: 30 shares of A, then buying stops
    plan = RB.build([rk({"A": 1.0, "B": 1.0})], {("mom", "OLD"): 40}, m, equity=100_000, cash=2_000)
    assert plan.orders == {("mom", "OLD"): -40, ("mom", "A"): 30}
    assert plan.sequence == [("mom", "OLD"), ("mom", "A")]  # sells first: they fund the print's purchases
    assert plan.binding[("mom", "A")] == "cash" and plan.binding[("mom", "B")] == "cash"
    with pytest.raises(ValueError, match="cash"):
        RB.build([rk({"A": 1.0})], {}, m, equity=100_000)  # the reserve needs the account's cash


def test_ranked_holds_kept_names_unless_the_list_resizes():
    m = {"A": mk(), "B": mk()}  # target 50 each
    cur = {("mom", "A"): 70, ("mom", "B"): 30}
    held = RB.build([rk({"A": 1.0, "B": 1.0}, resize=False)], cur, m, equity=100_000, cash=50_000)
    assert held.orders == {} and held.binding[("mom", "A")] == "hold"
    plan = RB.build([rk({"A": 1.0, "B": 1.0}, resize=True)], cur, m, equity=100_000, cash=50_000)
    assert plan.orders == {("mom", "A"): -20, ("mom", "B"): 20}
    assert plan.sequence == [("mom", "A"), ("mom", "B")]  # reductions first: they make room


@pytest.mark.parametrize("current,order", [(44, 6), (45, None), (55, None), (56, -6)])
def test_ranked_resize_buffer_is_more_than_ten_percent_of_the_target(current, order):
    # the pre-registration trades when |held - target| > 10% of the target: 45 and 55 stay, 44 and 56 trade
    plan = RB.build([rk({"A": 1.0}, resize=True)], {("mom", "A"): current}, {"A": mk()}, equity=100_000,
                    cash=50_000)
    assert plan.orders.get(("mom", "A")) == order


def test_ranked_a_resize_increase_is_cut_to_the_room():
    b = PortfolioBuilder(replace(RLIM, max_gross_pct=10, max_overnight_pct=10), fill="ranked")
    plan = b.build([rk({"A": 1.0, "B": 1.0}, resize=True)], {("mom", "A"): 30, ("mom", "B"): 50},
                   {"A": mk(), "B": mk(sector="x")}, equity=100_000)
    assert plan.targets == {("mom", "A"): 50, ("mom", "B"): 50}  # B is at target; A grows by the $2,000 left
    plan = b.build([rk({"A": 1.0, "B": 1.0}, resize=True)], {("mom", "A"): 30, ("mom", "B"): 60},
                   {"A": mk(), "B": mk(sector="x")}, equity=100_000)
    assert plan.targets == {("mom", "A"): 50, ("mom", "B"): 50}  # B's reduction comes first and makes the room


def test_ranked_a_held_name_without_data_is_kept_and_counted():
    m = {"A": Instrument(px=100.0, atr20=float("nan")), "B": mk(sector="x")}
    b = PortfolioBuilder(replace(RLIM, max_gross_pct=10, max_overnight_pct=10), fill="ranked")
    plan = b.build([rk({"A": 1.0, "B": 1.0}, resize=True)], {("mom", "A"): 80}, m, equity=100_000)
    assert plan.targets == {("mom", "A"): 80, ("mom", "B"): 20}


def test_ranked_a_name_that_may_not_open_is_sized_counted_and_not_bought():
    """The research harness refuses such an entry at the fill after the room was counted (#161, kept by #168)."""
    b = PortfolioBuilder(replace(RLIM, max_gross_pct=10, max_overnight_pct=10), fill="ranked")
    m = {"A": Instrument(100.0, 2.0, "a", can_open=False), "B": mk(sector="b")}
    plan = b.build([rk({"A": 1.0, "B": 1.0})], {}, m, equity=100_000)
    assert plan.targets == {("mom", "B"): 50} and plan.binding[("mom", "A")] == "refused"
    assert ("mom", "A") in {(x.strategy, x.symbol) for x in plan.skipped if x.reason == "refused"}
    # the pro-rata fill just skips it
    assert PB.build([tl("mom", {"A": 1.0})], {}, m, equity=100_000).targets == {}


def test_ranked_uses_the_rules_k_when_the_limits_allow_it():
    b = PortfolioBuilder(replace(RLIM, atr_risk_pct=0.15), k=0.0015, fill="ranked")
    assert b.build([rk({"A": 1.0})], {}, {"A": mk()}, equity=100_000).targets == {("mom", "A"): 75}
    clamped = PortfolioBuilder(RLIM, k=0.0015, fill="ranked")  # the level's 0.1% still caps it
    assert clamped.build([rk({"A": 1.0})], {}, {"A": mk()}, equity=100_000).targets == {("mom", "A"): 50}


def test_ranked_the_per_name_cap_is_part_of_the_size():
    # ATR 0.5 -> 200 shares ($20,000); the 15% cap is $15,000 = 150 shares
    plan = RB.build([rk({"A": 1.0})], {}, {"A": mk(atr=0.5)}, equity=100_000, cash=100_000)
    assert plan.targets == {("mom", "A"): 150} and plan.guarded == {}


def test_ranked_outer_guards_cut_the_last_ranked_purchase_and_say_so():
    b = PortfolioBuilder(replace(RLIM, max_sector_pct=7), fill="ranked")  # $7,000 per sector
    m = {"A": mk(sector="t"), "B": mk(sector="t"), "C": mk(sector="u")}
    plan = b.build([rk({"A": 1.0, "B": 1.0, "C": 1.0})], {}, m, equity=100_000)
    assert plan.targets == {("mom", "A"): 50, ("mom", "B"): 20, ("mom", "C"): 50}  # B, ranked after A, gives way
    assert plan.guarded == {("mom", "B"): "sector"} and plan.binding[("mom", "B")] == "guard:sector"


def test_ranked_guards_never_force_a_sale():
    b = PortfolioBuilder(replace(RLIM, max_sector_pct=5), fill="ranked")  # the held name alone is above it
    plan = b.build([rk({"A": 1.0, "B": 1.0}, resize=True)], {("mom", "A"): 50}, {"A": mk(), "B": mk()},
                   equity=100_000)
    assert plan.targets == {("mom", "A"): 50, ("mom", "B"): 0} and plan.guarded == {("mom", "B"): "sector"}


def test_ranked_options_are_checked():
    with pytest.raises(ValueError):
        PortfolioBuilder(RLIM, fill="greedy")
    with pytest.raises(ValueError):
        PortfolioBuilder(RLIM, cash_reserve=0.03)  # the reserve belongs to the ranked fill
    with pytest.raises(ValueError):
        PortfolioBuilder(RLIM, fill="ranked", cash_reserve=1.5)


def test_pro_rata_fill_keeps_held_names_when_the_list_does_not_resize():
    plan = PB.build([tl("mom", {"A": 1.0}, resize=False)], {("mom", "A"): 70}, {"A": mk()}, equity=100_000)
    assert plan.orders == {} and plan.binding[("mom", "A")] == "hold"


def _reference(sc):
    """The harness's sizing (`replay/multiday_core.CoreRule.plan`), transcribed for one list of weight-1 names:
    sells, resizes (reductions first), buys best first until the room runs out; Amendment 3's cash reserve."""
    eq, lim, m, k = sc["equity"], sc["lim"], sc["m"], sc["k"]
    held = {s: (q, m[s].px) for (_, s), q in sc["current"].items() if q > 0}
    sell = [s for s in held if s not in sc["keep"]]
    cap = min(lim.max_gross_pct, lim.max_overnight_pct) / 100 * eq
    exposure = sum(q * last for s, (q, last) in held.items() if s not in sell)
    names = len(held) - len(sell)
    money = sc["cash"] + sum(q * last for s, (q, last) in held.items() if s in sell) - 0.03 * eq

    def fits(px):
        return min(math.floor(max(cap - exposure, 0.0) / px), math.floor(max(money, 0.0) / px))

    def target(s):
        return float(min(math.floor(eq * k / m[s].atr20), math.floor(eq * lim.max_name_pct / 100 / m[s].px)))

    out = {s: 0 for s in sell} | {s: q for s, (q, _) in held.items() if s not in sell}
    if sc["resize"]:
        want = [(s, q, last, target(s)) for s, (q, last) in held.items() if s not in sell]
        want = [w for w in want if w[3] >= 1 and abs(w[1] - w[3]) > 0.10 * w[3]]
        for s, q, last, t in sorted(want, key=lambda w: w[3] - w[1]):
            if t > q:
                t = q + min(t - q, fits(last))
                if t <= q:
                    continue
            exposure += (t - q) * last
            money -= (t - q) * last
            out[s] = int(t)
    for s in sc["buy"]:
        if names >= lim.max_names:
            break
        t = target(s)
        if t < 1:
            continue
        room = fits(m[s].px)
        if room < 1:
            break
        exposure += min(t, room) * m[s].px
        money -= min(t, room) * m[s].px
        names += 1
        out[s] = int(min(t, room))
        if room < t:
            break
    return {("mom", s): q for s, q in out.items()}


@st.composite
def ranked_scenario(draw):
    sc = draw(scenario())
    held = sorted({s for (_, s) in sc["current"]})
    current = {("mom", s): max(draw(st.integers(0, 400)), 1) for s in held}
    keep = [s for s in held if draw(st.booleans())]
    buy = draw(st.permutations([s for s in SYMS if s not in held]))[:draw(st.integers(0, 12))]
    lim = replace(sc["lim"], max_sector_pct=40, max_gap_loss_pct=15, min_cash_pct=1)
    return {**sc, "lim": lim, "current": current, "keep": keep, "buy": buy, "k": min(sc["k"], lim.atr_risk_pct / 100),
            "resize": draw(st.booleans()), "cash": draw(st.floats(0, sc["equity"]))}


@hyp.settings(max_examples=400, deadline=None, suppress_health_check=[hyp.HealthCheck.too_slow])
@hyp.given(sc=ranked_scenario())
def test_property_the_ranked_fill_is_the_pre_registered_sizing(sc):
    """Whenever no outer guard cuts, the ranked fill gives the harness's whole shares exactly; whenever one does,
    it only ever cut a purchase (and said which)."""
    b = PortfolioBuilder(sc["lim"], k=sc["k"], fill="ranked", cash_reserve=0.03)
    lst = tl("mom", {s: 1.0 for s in [*sc["keep"], *sc["buy"]]}, resize=sc["resize"])
    plan = b.build([lst], sc["current"], sc["m"], equity=sc["equity"], cash=sc["cash"])
    ref = _reference(sc)
    assert all(isinstance(q, int) and q >= 0 for q in plan.targets.values())
    if not plan.guarded:
        assert {k: q for k, q in plan.targets.items() if q or k in sc["current"]} == ref
    for key in plan.guarded:
        assert sc["current"].get(key, 0) <= plan.targets[key] < ref[key]
    assert plan.sequence == [k for k in plan.sequence if plan.orders[k] < 0] + [
        k for k in plan.sequence if plan.orders[k] > 0 and k in sc["current"]] + [
        k for k in plan.sequence if k not in sc["current"]]
