"""Issue #111 (S71): cost model v2, its components, the per-bucket table against the report, the opt-in wiring
into the research harnesses, and proof that v1 (and therefore live behaviour) is unchanged."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.broker import shadow_costs
from kaibot.broker.costs_v2 import CostV1, CostV2, FillCtx, V2Params, get_model, round_trip_bp
from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.multiday import DailyBars, Engine, equal_weight_benchmark, indicators
from kaibot.replay.multiday_rules import EXPERIMENTS, book_digest, make_rule
from kaibot.replay.sim import EXIT_M, buy_px, cost_parts, run_portfolio, sell_px
from kaibot.risk.profiles import load_profiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import momentum_core  # noqa: E402

V2 = get_model("v2")
POL = load_profiles()["balanced"]
COSTS = ("cost_spread", "cost_impact", "cost_slippage", "cost_fees")
SIG = 0.02 / math.sqrt(N_MIN)  # 2% daily volatility as a 1-minute sigma


def ctx(mid=100.0, qty=139, vol=1e6, adv=1e9, venue="continuous", sigma=SIG, raw=None):
    return FillCtx(mid, qty, sigma, vol, adv, venue, raw)


# ---------------------------------------------------------------- 1. spread by liquidity bucket


@pytest.mark.parametrize(("adv", "bp"), [(2e9, 1.75), (500e6, 1.75), (499e6, 3.75), (100e6, 3.75), (99e6, 6.5),
                                         (50e6, 6.5), (49e6, 10.0), (math.nan, 10.0), (0.0, 10.0)])
def test_half_spread_is_the_bucket_midpoint(adv, bp):
    assert V2.half_spread_bp(adv) == bp


def test_report_table_round_trip_by_bucket():
    """The report's table (market in, market out, $14k, fees included): >$500M 3-6 bp, $100-500M 6-11 bp,
    $50-100M 11-18 bp. v2's midpoints land inside each range; v1 charges the same in every bucket."""
    want = {1e9: (3.0, 6.0, 3.801), 250e6: (6.0, 11.0, 8.012), 75e6: (11.0, 18.0, 14.168)}
    v1 = {adv: round_trip_bp(CostV1(), adv=adv, vol=2_900)["total"] for adv in want}
    for adv, (lo, hi, pinned) in want.items():
        rt = round_trip_bp(V2, adv=adv)
        assert lo <= rt["total"] <= hi and rt["total"] == pytest.approx(pinned, abs=1e-3)
        assert rt["impact"] < 1.0 and rt["fees"] == pytest.approx(0.23, abs=0.01)
    assert len(set(v1.values())) == 1 and v1[1e9] == pytest.approx(5.51, abs=0.01)  # audit F4 table A: 5.2 + fees


def test_spread_charge_scales_continuous_fills_and_is_bounded():
    lo = CostV2(V2Params(spread_charge=0.6))
    assert lo.fill("buy", ctx()).half == pytest.approx(0.6 * V2.fill("buy", ctx()).half)
    assert lo.fill("buy", ctx(venue="close")).half == V2.fill("buy", ctx(venue="close")).half  # auctions: not
    for bad in (0.5, 1.01):
        with pytest.raises(ValueError):
            V2Params(spread_charge=bad)


def test_tick_floor_is_on_the_raw_price():
    """An adjusted $2.50 that was $250 raw pays the bucket's bp, not the half-cent floor (audit E4); without the
    raw price the floor still applies, as the docstring says."""
    raw = V2.fill("buy", ctx(mid=2.5, qty=5_000, raw=250.0))
    assert raw.half / 2.5 * 1e4 == pytest.approx(1.75)
    adj = V2.fill("buy", ctx(mid=2.5, qty=5_000))
    assert adj.half == pytest.approx(0.005) and adj.half / 2.5 * 1e4 == pytest.approx(20.0)


# ---------------------------------------------------------------- 2. venues, impact, fees, order types


def test_close_is_half_a_spread_and_the_open_twice_the_midday_half_spread():
    mid = V2.fill("buy", ctx()).half
    assert V2.fill("buy", ctx(venue="close")).half == pytest.approx(mid)
    assert V2.fill("sell", ctx(venue="open")).half == pytest.approx(2 * mid)
    assert V2.fill("buy", ctx(venue="close")).impact == pytest.approx(0.5 * V2.fill("buy", ctx()).impact)
    with pytest.raises(ValueError):
        V2.fill("buy", ctx(venue="midday"))
    with pytest.raises(ValueError):
        V2.fill("short", ctx())


def test_impact_is_near_zero_with_no_fixed_quarter_spread():
    one = V2.fill("buy", ctx(qty=1))
    assert one.impact / 100 * 1e4 < 1e-3  # one share: nothing like v1's quarter-spread
    v1 = CostV1().fill("buy", ctx(qty=1))
    assert v1.impact >= 0.25 * 2 * v1.half - 1e-12
    assert V2.fill("buy", ctx(adv=1e9)).impact / 100 * 1e4 < 0.1
    assert 0.3 < V2.fill("buy", ctx(adv=75e6)).impact / 100 * 1e4 < 1.0  # report: 0.5 to 1 bp at $50-100M


def test_participation_cap_charges_only_the_excess_and_stays_bounded():
    inside = V2.fill("buy", ctx(vol=13_900))  # 139 shares = 1% of the minute
    lin = V2.p.impact_lin_k * 0.02 * 13_900 / 1e9
    assert inside.impact / 100 == pytest.approx(lin)
    stale = V2.fill("buy", ctx(vol=0, adv=50e6))  # nothing traded in the minute: every share is excess
    assert stale.impact > inside.impact and stale.impact / 100 * 1e4 < 3.0  # v1: about 60 bp a side here
    assert CostV1().fill("buy", ctx(vol=0, adv=50e6)).impact / 100 * 1e4 > 50
    huge = V2.fill("buy", ctx(qty=5e6, vol=0, adv=50e6))
    assert huge.impact / 100 * 1e4 == pytest.approx(V2.p.impact_cap_bp)


def test_fees_at_2026_rates_rounded_up_and_capped():
    exact = CostV2(V2Params(round_fees_up=False))
    assert exact.sell_fees(139, 100.0) == pytest.approx(13_900 * 20.60e-6 + 139 * 0.000195)
    assert V2.sell_fees(139, 100.0) == pytest.approx(0.29 + 0.03)  # each fee up to the next cent
    assert exact.sell_fees(100_000, 10.0) == pytest.approx(1e6 * 20.60e-6 + 9.79)  # TAF cap per trade
    assert exact.sell_fees(100, 2.5, raw_price=250.0) == pytest.approx(250 * 20.60e-6 + 1 * 0.000195)  # raw shares
    assert V2.sell_fees(0, 100.0) == 0.0


def test_limit_orders_fill_through_a_tick_and_with_the_haircut():
    assert V2.limit_fill("sell", 101.0, 99.0, 101.0, "k") is None  # touched, not through
    keys = [f"S{k}|2024-01-02|tgt" for k in range(4_000)]
    got = [V2.limit_fill("sell", 101.0, 99.0, 101.02, k) for k in keys]
    rate = sum(f is not None for f in got) / len(keys)
    assert abs(rate - 0.8) < 0.03
    f = next(f for f in got if f is not None)
    assert f.price == 101.0 and f.half == 0.0 and f.impact == 0.0
    assert got == [V2.limit_fill("sell", 101.0, 99.0, 101.02, k) for k in keys]  # deterministic
    always, never = CostV2(V2Params(limit_fill_prob=1.0)), CostV2(V2Params(limit_fill_prob=0.0))
    assert always.limit_fill("buy", 99.0, 98.98, 100.0, "x") is not None
    assert never.limit_fill("buy", 99.0, 98.0, 100.0, "x") is None


def test_stop_gapped_through_fills_at_the_open_as_an_opening_fill():
    c = ctx(mid=95.0)
    gap = V2.stop_fill(95.0, 90.0, 89.0, c)
    assert gap.price < 90.0 and gap.half == pytest.approx(2 * 1.75e-4 * 90.0)
    touch = V2.stop_fill(95.0, 97.0, 94.0, c)
    assert touch.price < 95.0 and touch.half == pytest.approx(1.75e-4 * 95.0)
    assert V2.stop_fill(95.0, 97.0, 96.0, c) is None


def test_v1_behind_the_protocol_is_the_replays_v1_to_the_bit():
    for mid, qty, vol in ((100.0, 139, 2_900), (2.5, 5_000, 0), (412.37, 33, 1e5)):
        b = CostV1().fill("buy", ctx(mid=mid, qty=qty, vol=vol))
        s = CostV1().fill("sell", ctx(mid=mid, qty=qty, vol=vol))
        assert b.price == buy_px(mid, qty, SIG, vol) and s.price == sell_px(mid, qty, SIG, vol)
        assert (b.half, b.impact) == cost_parts(mid, b.price)
        assert CostV1().sell_fees(qty, mid) == shadow_costs.sell_fees(qty, mid)


def test_model_switch():
    assert get_model().name == "v1" and get_model("v2").name == "v2"
    assert get_model("v2", spread_charge=0.6).p.spread_charge == 0.6
    with pytest.raises(ValueError):
        get_model("v3")
    with pytest.raises(ValueError):
        get_model("v1", spread_charge=0.6)


# ---------------------------------------------------------------- 3. live behaviour unchanged


def test_live_code_never_imports_v2_and_v1_constants_are_unchanged():
    for f in (ROOT / "src" / "kaibot").rglob("*.py"):
        if f.parent.name == "replay" or f.name == "costs_v2.py":
            continue
        assert "costs_v2" not in f.read_text(), f
    assert shadow_costs.SEC_FEE_PER_DOLLAR_SOLD == 0.0000278 and shadow_costs.TAF_PER_SHARE_SOLD == 0.000166
    q = shadow_costs.QuoteCtx(99.99, 100.01, 0.001, 1000.0)
    spread = 0.02
    assert shadow_costs.shadow_fill_price("buy", 100, q) == pytest.approx(
        100.0 + spread / 2 + 0.25 * spread + 0.5 * 0.001 * 100.0 * math.sqrt(0.1))


# ---------------------------------------------------------------- 4. the intraday replay with v2


DAY = np.datetime64("2024-06-03")


def one_day(path: np.ndarray, volume: float = 5e5) -> dict[str, SymbolDays]:
    c = path[None, :].astype(float)
    return {"AAA": SymbolDays("AAA", np.array([DAY]), c.copy(), c.copy(), c.copy(), c, np.full(c.shape, volume),
                              c.copy())}


def one_cand() -> pd.DataFrame:
    return pd.DataFrame([{"symbol": "AAA", "date": DAY, "m": 20, "price": 100.0, "atr5": 0.4, "vwap": 99.0,
                          "z": -2.0, "resid_z": 0.0, "turnover": 1.0, "sigma_1m": 0.0005, "news_24h": False}])


def run_one(path, **kw):
    reg = {DAY: (np.zeros(N_MIN, dtype=bool), 18.0)}
    return run_portfolio(one_day(path), one_cand(), reg, set(), POL, variant="A", **kw)


def test_intraday_replay_v2_charges_the_bucket_spread_and_v1_is_unchanged():
    flat = np.full(N_MIN, 100.0)
    old, same = run_one(flat), run_one(flat, costs=get_model("v1"))
    assert [t.__dict__ for t in old.trades] == [t.__dict__ for t in same.trades]
    (t,) = run_one(flat, costs=V2).trades
    assert t.reason == "time" and t.exit_m == EXIT_M and t.gross_pnl == 0.0
    assert t.cost_spread == pytest.approx(2 * 10e-4 * 100.0 * t.qty)  # one session: ADV unknown -> 10 bp a side
    assert t.cost_fees == pytest.approx(V2.sell_fees(t.qty, t.exit))
    assert t.pnl == pytest.approx(t.gross_pnl - sum(getattr(t, k) for k in COSTS), abs=1e-6)
    path = flat.copy()
    path[40:] = 103.0  # through the 2R target: a limit fill, no exit spread
    (t,) = run_one(path, costs=V2).trades
    assert t.reason == "target" and t.cost_spread == pytest.approx(10e-4 * 100.0 * t.qty)


# ---------------------------------------------------------------- 5. the multi-day engine with v2


def market(seed: int = 5, n: int = 320, n_stocks: int = 6) -> tuple[DailyBars, list[str]]:
    rng = np.random.default_rng(seed)
    stocks = [f"S{k}" for k in range(n_stocks)]
    r = rng.normal(np.r_[np.linspace(-0.0004, 0.0016, n_stocks), 0.0004], 0.014, (n, n_stocks + 1))
    c = np.r_[np.linspace(40, 180, n_stocks), 400.0] * np.exp(np.cumsum(r, axis=0))
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.004, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, c.shape)))
    dates = np.busday_offset(np.datetime64("2023-01-03"), np.arange(n), roll="forward").astype("datetime64[D]")
    vol = np.full(c.shape, 8e6) * np.linspace(0.05, 1.0, n_stocks + 1)  # ADV from about $16M to $1bn+
    return DailyBars.make(dates, [*stocks, "SPY"], o, h, lo, c, vol, c), stocks


@pytest.fixture(scope="module")
def mkt():
    b, stocks = market()
    return b, stocks, indicators(b)


def account(name, b, stocks, ind, **kw) -> Engine:
    vix = np.full(len(b.dates), 18.0)
    rule = make_rule(name, b, ind, stocks, "SPY", vix, None)
    eng = Engine(b, ind, POL, rule, lambda i: EXPERIMENTS[name].default, **kw)
    eng.run(210, len(b.dates) - 1)
    return eng


@pytest.mark.parametrize("name", ["trend_highs", "rel_strength"])
def test_multiday_v2_keeps_the_identities_and_v1_is_the_default(mkt, name):
    b, stocks, ind = mkt
    v1 = account(name, b, stocks, ind)
    assert book_digest(v1.book) == book_digest(account(name, b, stocks, ind, costs=get_model("v1")).book)
    eng = account(name, b, stocks, ind, costs=V2)
    assert eng.book.trades and book_digest(eng.book) != book_digest(v1.book)
    for t in eng.book.trades:
        assert t.pnl == pytest.approx(t.gross_pnl - sum(getattr(t, k) for k in COSTS), abs=1e-6)
        assert t.cost_spread > 0 and t.cost_impact >= 0 and t.cost_fees > 0
    assert sum(t.pnl for t in eng.book.trades) == pytest.approx(eng.equity - 100_000.0, abs=1e-4)
    dbl = account(name, b, stocks, ind, costs=V2, cost_mult=2.0)
    assert sum(t.cost_spread for t in dbl.book.trades) > sum(t.cost_spread for t in eng.book.trades)


def test_multiday_v2_open_entry_pays_twice_the_bucket_half_spread(mkt):
    b, stocks, ind = mkt

    class OneBuy:
        name = "one"

        def at_open(self, i, eng, cfg, eq):
            if i == 250:
                eng.enter(i, 5, eq, "t", eng_caps, max_hold=3)

        def at_close(self, i, eng, cfg, eq):
            pass

    from kaibot.replay.multiday import Caps

    eng_caps = Caps()
    eng = Engine(b, ind, POL, OneBuy(), lambda i: None, costs=V2)
    eng.run(240, 260)
    (t,) = eng.book.trades
    bp = V2.half_spread_bp(float(ind.adv[249, 5]))
    entry_half = 2 * bp / 1e4 * b.o[250, 5]
    exit_half = bp / 1e4 * b.c[253, 5]  # max_hold sells at the close: one half-spread
    assert t.reason == "max_hold"
    assert t.cost_spread == pytest.approx((entry_half + exit_half) * t.qty, rel=1e-9)


def test_equal_weight_benchmark_with_v2_costs_differs_only_by_costs(mkt):
    b, stocks, ind = mkt
    cols = b.cols(stocks)
    r1 = equal_weight_benchmark(b, ind, cols, 220, 300)
    r2 = equal_weight_benchmark(b, ind, cols, 220, 300, costs=V2)
    assert np.array_equal(r1, equal_weight_benchmark(b, ind, cols, 220, 300, costs=get_model("v1")))
    assert not np.array_equal(r1, r2) and np.max(np.abs(r1 - r2)) < 5e-3


# ---------------------------------------------------------------- 6. scripts: opt-in, v1 reports still found


def test_momentum_core_keeps_v1_and_v2_reports_apart(tmp_path):
    def write(name, **extra):
        (tmp_path / name).write_text(json.dumps({"set": "core", "version": "trend_momentum", "rows": [], **extra}))

    write("core_balanced_trend_momentum_20261101T000000.json")  # before #111: no "costs" key = v1
    write("core_balanced_trend_momentum_costsv2_20261102T000000.json", costs="v2")
    v1 = momentum_core.find_report(tmp_path, "balanced", "trend_momentum")
    v2 = momentum_core.find_report(tmp_path, "balanced", "trend_momentum", "v2")
    assert "costs" not in v1[1] and v2[1]["costs"] == "v2"
    assert momentum_core.summary_name() == "experiments_core_summary.md"
    assert momentum_core.summary_name("v2") == "experiments_core_summary_costsv2.md"


@pytest.mark.parametrize("script", ["momentum_core.py", "walk_forward.py", "walk_forward_multiday.py"])
def test_scripts_take_an_opt_in_costs_flag_defaulting_to_v1(script):
    text = (ROOT / "scripts" / script).read_text()
    assert 'ap.add_argument("--costs", default="v1", choices=list(MODELS)' in text
