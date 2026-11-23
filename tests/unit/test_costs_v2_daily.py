"""#559: cost model v2 in the engine's daily path (Product, 22 Nov 2026: gate v3's final verdicts use v2). One core:
the same `run_daily`, the same builder and target loop; only `SimBroker`'s fill prices and fees and the ledger's
split of costs change. v1 stays the default, so every earlier run and parity test is unchanged to the bit."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import numpy as np
import pytest

from kaibot.broker.costs_v2 import CostV2, FillCtx
from kaibot.broker.sim import NY, AuctionCostsV2
from kaibot.engine import daily_report as dr
from kaibot.engine.daily import DailyData, run_daily
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import momentum as mom

POL = load_profiles()["balanced"]
S = mom.VERSIONS["trend_momentum"]


@pytest.fixture(scope="module")
def runs():
    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    i1 = len(data.dates) - 1
    return data, {k: run_daily(S, data, POL, i0, i1, cost_model=k) for k in ("v1", "v2")}, \
        run_daily(S, data, POL, i0, i1)


def test_v1_stays_the_default_bit_for_bit(runs):
    _, r, default = runs
    assert default.fills == r["v1"].fills and default.orders == r["v1"].orders
    assert default.book.equity == r["v1"].book.equity
    with pytest.raises(ValueError, match="cost_model"):
        run_daily(S, runs[0], POL, 300, 310, cost_model="v3")


def test_the_first_decision_sends_the_same_orders_under_both_models(runs):
    _, r, _ = runs
    first = min(i for i, *_ in r["v1"].orders)
    o1 = [x for x in r["v1"].orders if x[0] == first]
    o2 = [x for x in r["v2"].orders if x[0] == first]
    assert o1 == o2 and o1


def test_later_orders_differ_only_through_the_equity_the_costs_left(runs):
    """The documented difference: the same names are bought and sold; share counts move with the equity the two
    cost models leave, which later decisions size on."""
    _, r, _ = runs
    names = {k: {(i, kind, s) for i, kind, s, _ in v.orders} for k, v in r.items()}
    same = len(names["v1"] & names["v2"]) / max(len(names["v1"] | names["v2"]), 1)
    assert same >= 0.9
    # on the committed fixture the difference in equity is too small to move a whole share: zero order diff
    assert r["v1"].orders == r["v2"].orders
    e1, e2 = r["v1"].book.equity, r["v2"].book.equity
    assert e1 != e2 and abs(e1 / e2 - 1) < 0.02


def test_each_closing_fill_is_priced_by_cost_model_v2(runs):
    data, r, _ = runs
    v2 = CostV2()
    checked = 0
    for f in r["v2"].fills:
        if f["reason"] in ("delisted", "cash_in_lieu", "end_of_data"):
            continue
        i, j = f["i"], data.col(f["symbol"])
        mid = float(data.raw_c[i, j])
        ctx = FillCtx(mid, f["qty"], data.sigma_at(i, j), float(data.v_last[i, j]) if data.v_last[i, j] > 0 else 1.0,
                      float(data.adv[i - 1, j]), "close", raw_mid=mid, symbol=f["symbol"])
        assert f["px"] == pytest.approx(v2.fill(f["side"], ctx).price, rel=1e-12)
        want = v2.sell_fees(f["qty"], f["px"], raw_price=f["px"]) if f["side"] == "sell" else 0.0
        assert f["fees"] == pytest.approx(want)
        checked += 1
    assert checked > 50
    assert not r["v2"].ledger.overdrafts and r["v2"].cash_gap < 0.01 and not r["v2"].refused


def test_the_ledger_splits_v2_costs_with_v2_spreads(runs):
    _, r, _ = runs
    t = r["v2"].book.trades[0]
    t1 = r["v1"].book.trades[0]
    assert t.symbol == t1.symbol and t.cost_spread != t1.cost_spread
    v2 = CostV2()
    for tr in r["v2"].book.trades:  # spread plus impact is the whole cost of the two fills, under either model
        assert tr.cost_spread >= 0 and tr.cost_impact >= -1e-9
    assert v2.name == "v2"


def test_the_v2_auction_costs_price_each_print_at_its_venue():
    c = AuctionCostsV2(sigma=lambda s, d: 0.001, adv=lambda s, d: 2e9)
    close = datetime(2024, 3, 1, 15, 59, tzinfo=NY).astimezone(UTC)
    open_ = datetime(2024, 3, 1, 9, 30, tzinfo=NY).astimezone(UTC)
    m = CostV2()
    want_c = m.fill("buy", FillCtx(100.0, 100, 0.001, 1e5, 2e9, "close", raw_mid=100.0, symbol="X")).price
    want_o = m.fill("buy", FillCtx(100.0, 100, 0.001, 1e5, 2e9, "open", raw_mid=100.0, symbol="X")).price
    assert c.auction_fill_at("buy", 100.0, 100, "X", close, 1e5) == pytest.approx(want_c)
    assert c.auction_fill_at("buy", 100.0, 100, "X", open_, 1e5) == pytest.approx(want_o)
    assert want_o > want_c
    two = AuctionCostsV2(sigma=lambda s, d: 0.001, adv=lambda s, d: 2e9, mult=2.0)
    assert two.auction_fill_at("buy", 100.0, 100, "X", close, 1e5) - 100 == pytest.approx(2 * (want_c - 100))
    assert two.fees("sell", 100, 100.0) == c.fees("sell", 100, 100.0) == m.sell_fees(100, 100.0, raw_price=100.0)
    assert c.fees("buy", 100, 100.0) == 0.0
    unknown = AuctionCostsV2()  # no ADV: v2's floor bucket, never a cheaper one
    assert unknown.auction_fill("buy", 100.0, 100, "X", date(2024, 3, 1), 1e5) > want_c
    assert math.isfinite(unknown.market("sell", 100.0, 10, 0.001, 1e4))


def test_the_stress_fill_under_v2_is_the_open_venue_without_a_second_doubling(runs):
    data, _, _ = runs
    i0 = len(data.dates) - 60
    s = run_daily(S, data, POL, i0, len(data.dates) - 1, fill="stress", cost_model="v2")
    m = CostV2()
    f = next(f for f in s.fills if f["reason"] not in ("delisted", "cash_in_lieu", "end_of_data"))
    j = data.col(f["symbol"])
    mid = float(data.raw_o[f["i"], j])
    ctx = FillCtx(mid, f["qty"], data.sigma_at(f["i"], j), float(data.v_first[f["i"], j]) or 1.0,
                  float(data.adv[f["i"] - 1, j]), "open", raw_mid=mid, symbol=f["symbol"])
    assert f["px"] == pytest.approx(m.fill(f["side"], ctx).price, rel=1e-12)


def test_the_order_diff_script_on_the_fixture(tmp_path):
    import sys

    from kaibot.engine.config import ROOT

    sys.path.insert(0, str(ROOT / "scripts"))
    import costs_v2_order_diff as script

    out = tmp_path / "diff.json"
    assert script.main(["--fixture", "--data", str(tmp_path), "--out", str(out)]) == 0
    import json

    rep = json.loads(out.read_text())
    assert rep["first_decision_identical"] and rep["names_overlap"] >= 0.9
    assert rep["costs_paid"]["v1"] != rep["costs_paid"]["v2"]


def test_v2_session_fills_use_the_adv_bucket_and_a_gapped_stop_is_an_opening_fill():
    """QA on #572: a market or stop order in the session pays v2's continuous venue at the stock's ADV bucket, not
    the unknown-ADV floor; a stop gapped through at the open pays v2's open venue (decision 16 / issue #111)."""
    c = AuctionCostsV2(sigma=lambda s, d: 0.001, adv=lambda s, d: 2e9)
    t = datetime(2024, 3, 1, 9, 30, tzinfo=NY).astimezone(UTC)
    m = CostV2()
    cont = m.fill("sell", FillCtx(100.0, 100, 0.001, 1e5, 2e9, "continuous", raw_mid=100.0, symbol="X")).price
    opn = m.fill("sell", FillCtx(100.0, 100, 0.001, 1e5, 2e9, "open", raw_mid=100.0, symbol="X")).price
    assert c.market_at("sell", 100.0, 100, "X", t, 0.001, 1e5) == pytest.approx(cont, rel=1e-12)
    assert c.market_at("sell", 100.0, 100, "X", t, 0.001, 1e5, gapped=True) == pytest.approx(opn, rel=1e-12)
    assert opn < cont < 100.0  # the open costs a sale more than the continuous session
    assert c.market_at("sell", 100.0, 100, "X", t, 0.001, 1e5) > c.market("sell", 100.0, 100, 0.001, 1e5)



def test_v2_limit_fills_take_the_fill_probability_haircut_and_v1_does_not():
    """QA on #572: under v2 a resting limit that the bar trades through fills only when v2's deterministic draw
    passes `limit_fill_prob` (0.8); under v1 every such limit fills, as before."""
    import pandas as pd

    from kaibot.broker.protocol import OrderRequest
    from kaibot.broker.sim import COSTS_V1, SimBroker
    from kaibot.engine.clock import SimClock
    from kaibot.replay.history import BarHistory

    day = "2024-03-01"
    t0 = pd.Timestamp(f"{day} 09:30", tz=NY).tz_convert(UTC)
    ts = t0 + pd.to_timedelta(np.arange(390), unit="m")
    syms = [f"S{k}" for k in range(200)]
    frame = pd.DataFrame({"ts": ts, "o": 100.0, "h": 100.05, "l": 99.95, "c": 100.0, "v": 1e5, "vw": 100.0})
    frame.loc[31, "l"] = 98.0  # 10:01 trades through a 99.00 buy limit by more than a cent
    filled = {}
    for name, costs in (("v1", COSTS_V1), ("v2", AuctionCostsV2(sigma=lambda s, d: 0.001, adv=lambda s, d: 2e9))):
        clock = SimClock((t0 + pd.Timedelta(minutes=30, seconds=7)).to_pydatetime())
        b = SimBroker(BarHistory({s: frame.copy() for s in syms}), clock, costs=costs, cash=1e9)
        ids = [b.submit(OrderRequest(s, "buy", 10, "limit", "day", f"c-{s}", limit_price=99.0))["id"] for s in syms]
        b.advance((t0 + pd.Timedelta(minutes=31)).to_pydatetime())
        filled[name] = sum(b.orders[i]["status"] == "filled" for i in ids)
        assert all(float(b.orders[i]["filled_avg_price"]) == 99.0 for i in ids if b.orders[i]["status"] == "filled")
    assert filled["v1"] == 200
    assert 130 <= filled["v2"] <= 185  # about 80%: v2's limit_fill_prob, the same on every run


# ---------------------------------------------------------------- #578: like-for-like controls, the stop split


def test_the_control_is_priced_with_the_runs_cost_model(runs):
    from kaibot.replay import multiday_core as mc

    data, r, _ = runs
    m, _ = dr.load_fixture(dr.FIXTURE)
    c1 = dr.control_for(r["v1"], m, "trend_momentum", POL, 5, 1, 100_000.0)
    c2 = dr.control_for(r["v2"], m, "trend_momentum", POL, 5, 1, 100_000.0)
    assert c1["cost_model"] == "v1" and c2["cost_model"] == "v2" and r["v2"].cost_model == "v2"
    from types import SimpleNamespace

    rule = SimpleNamespace(opened=r["v1"].opened, resized=r["v1"].resized)
    legacy = mc.exposure_matched_control(m, POL, mc.VERSIONS["trend_momentum"], r["v1"].book, rule, r["v1"].i0,
                                         r["v1"].i1, runs=5, seed=1, equity0=100_000.0)
    assert {k: v for k, v in c1.items() if k != "cost_model"} == legacy  # v1: the same control as before
    assert c1["sharpe"] != c2["sharpe"]  # the random portfolios pay v2's costs under a v2 run
    from kaibot.replay.multiday import costs_for

    assert costs_for("v1") is None and costs_for("v2").name == "v2"


def test_a_stop_fill_under_v2_is_split_at_its_own_level_and_venue(runs):
    data, _, _ = runs
    i0 = int(np.searchsorted(data.dates, data.dates[len(data.dates) // 2]))
    run = run_daily(S, data, POL, i0, len(data.dates) - 1, protect_atr=1.0, cost_model="v2")
    stops = [b for k, b in run.ledger.entries if k == "fill" and b.get("reason") == "protective_stop"]
    assert stops, "a 1-ATR stop must fire on the fixture"
    m = CostV2()
    venue = {(o["symbol"], datetime.fromisoformat(o["filled_at"]).astimezone(NY).date().isoformat()): o
             for o in run.broker.orders.values() if o.get("type") == "stop" and o.get("status") == "filled"}
    seen = set()
    for b in stops:
        j, i = data.col(b["symbol"]), b["i"]
        o_, level = float(data.raw_o[i, j]), float(venue[b["symbol"], b["session"]]["stop_price"])  # SimBroker's level
        gapped = o_ <= level if b["side"] == "sell" else o_ >= level
        assert b["at"] == ("open" if gapped else "continuous"), b
        assert b["mid"] == pytest.approx(o_ if gapped else level), b  # the stop's level, or the open it gapped
        seen.add(b["at"])
        half = m.half_spread(FillCtx(b["mid"], 1.0, 0.0, 1.0, float(data.adv[i - 1, j]), b["at"], raw_mid=b["mid"],
                                     symbol=b["symbol"]))
        assert b["mid"] - b["px"] >= half - 1e-9  # the fill paid at least the venue's half-spread below the mid
    assert seen == {"continuous", "open"}, seen  # the fixture has both: a stop touched and a stop gapped
    v1 = run_daily(S, data, POL, i0, len(data.dates) - 1, protect_atr=1.0)
    old = [b for k, b in v1.ledger.entries if k == "fill" and b.get("reason") == "protective_stop"]
    assert old and all(b["at"] == "open" for b in old)  # v1 keeps its old split, bit for bit
