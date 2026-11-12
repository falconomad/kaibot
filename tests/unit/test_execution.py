"""Issue #118 (S78), part B: execution styles, the holding policy, the target reconciliation loop (with the
portfolio builder), standalone protective GTC stops (partial fills, resizes, the 90-day re-arm, splits), the daily
runner through the order manager, and dividends accrued at the ex-date on the live path."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_order_manager import DAY, NEXT, bar, et, manager, run, venue

from kaibot.engine.dividends import ExDateDividends, match_payment, parse_cash_dividends, receivable
from kaibot.engine.execution import ExecutionPolicy, ExecutionRefused, flatten_at_close
from kaibot.engine.orders import St
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.reconcile import Reconciler
from kaibot.engine.targets import Target, reconcile_targets, targets_from_plan, working_shares
from kaibot.risk.gate import BookLimits
from kaibot.risk.portfolio import Instrument, PortfolioBuilder
from kaibot.strategies.base import Holding, TargetList

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
K = ("rule", "A")


# ---------------------------------------------------------------- execution styles


def test_closing_auction_orders_are_whole_share_cls_before_1550_or_from_1900():
    p = ExecutionPolicy("close")
    r = p.request("A", "buy", 10, et(DAY, "15:49"), "c1")
    assert (r.type, r.time_in_force, r.qty) == ("market", "cls", 10)
    assert p.request("A", "buy", 10, et(DAY, "19:00"), "c2").time_in_force == "cls"
    for t in ("15:50", "18:59"):
        with pytest.raises(ExecutionRefused, match="15:50"):
            p.request("A", "buy", 10, et(DAY, t), "c3")
    with pytest.raises(ExecutionRefused, match="whole shares"):
        p.request("A", "buy", 1.5, et(DAY, "10:00"), "c4")


def test_marketable_limits_start_at_0945_and_cross_the_spread_by_the_offset():
    p = ExecutionPolicy("marketable_limit")
    with pytest.raises(ExecutionRefused, match="09:45"):
        p.request("A", "buy", 5, et(DAY, "09:44"), "c", bid=99.9, ask=100.0)
    b = p.request("A", "buy", 5, et(DAY, "09:45"), "c", bid=99.9, ask=100.0)
    s = p.request("A", "sell", 5, et(DAY, "10:00"), "c", bid=99.9, ask=100.0)
    assert (b.type, b.time_in_force, b.limit_price) == ("limit", "day", 100.05)
    assert s.limit_price == 99.85
    with pytest.raises(ExecutionRefused, match="ask"):
        p.request("A", "buy", 5, et(DAY, "10:00"), "c")
    assert p.timed_out(et(DAY, "10:00").isoformat(), et(DAY, "10:02"))
    assert not p.timed_out(et(DAY, "10:00").isoformat(), et(DAY, "10:01"))


def test_no_market_on_open_live_and_brackets_stay_for_intraday_rules():
    with pytest.raises(ExecutionRefused, match="market-on-open"):
        ExecutionPolicy("open")
    research = ExecutionPolicy("open", research_open=True)  # the stress fill model of the research runs only
    assert research.request("A", "buy", 3, et(DAY, "08:00"), "c").time_in_force == "opg"
    br = ExecutionPolicy("bracket").request("A", "buy", 3, et(DAY, "10:00"), "c", stop=95.0, target=110.0)
    assert (br.order_class, br.time_in_force, br.stop_loss, br.take_profit) == ("bracket", "gtc", 95.0, 110.0)
    lim = ExecutionPolicy("limit").request("A", "sell", 3, et(DAY, "10:00"), "c", limit=101.0, tif="gtc")
    assert (lim.type, lim.limit_price, lim.time_in_force) == ("limit", 101.0, "gtc")


def test_holding_policy_intraday_sleeves_flatten_at_close_minus_5_multi_day_do_not():
    assert flatten_at_close(Holding.INTRADAY) and not flatten_at_close(Holding.MULTI_DAY)


# ---------------------------------------------------------------- the target loop


def loop(om, held, targets, clock, **kw):
    return reconcile_targets(om, ExecutionPolicy("close"), targets, held, now=clock(), session=DAY, **kw)


def test_target_minus_held_minus_working_and_a_second_run_sends_nothing():
    b, clock = venue()
    om, _, _ = manager(b, clock)
    want = [Target("rule", "A", 30, "buy"), Target("rule", "B", 0, "sell")]
    r1 = loop(om, {}, want, clock)
    assert [(r.symbol, r.side, r.qty, r.tif) for r in r1.sent] == [("A", "buy", 30, "cls")]
    assert working_shares(om, K) == 30
    r2 = loop(om, {}, want, clock)
    assert not r2.sent and not r2.canceled and len(b.orders) == 1


def test_the_loop_books_the_auction_fill_and_cancels_an_order_that_no_longer_fits():
    b, clock = venue()
    om, _, booked = manager(b, clock)
    mkt = ExecutionPolicy("marketable_limit")
    reconcile_targets(om, ExecutionPolicy("close"), [Target("rule", "A", 30, "buy")], {}, now=clock(), session=DAY)
    run(b, clock, 30, 31)
    om.sync()
    assert sum(f.qty for f in booked) == 0  # a cls order trades only at the close
    run(b, clock, 32, 389)
    clock.set(et(DAY, "16:30"))
    om.sync()
    assert sum(f.qty for f in booked) == 30
    # a new day, target down: 30 held -> 10, nothing working: one sale of 20
    clock.set(et(NEXT, "10:00"))
    res = reconcile_targets(om, mkt, [Target("rule", "A", 10, "sell")], {K: 30.0}, now=clock(), session=NEXT,
                            quotes={"A": (99.9, 100.0)})
    assert [(r.side, r.qty, r.type) for r in res.sent] == [("sell", 20, "limit")]
    # the target moves before it fills: the working order is cancelled, the new difference waits for the next run
    res = reconcile_targets(om, mkt, [Target("rule", "A", 0, "sell")], {K: 30.0}, now=clock(), session=NEXT,
                            quotes={"A": (99.9, 100.0)})
    assert res.canceled and not res.sent and om.get(res.canceled[0]).state is St.CANCELED
    res = reconcile_targets(om, mkt, [Target("rule", "A", 0, "sell")], {K: 30.0}, now=clock(), session=NEXT,
                            quotes={"A": (99.9, 100.0)})
    assert [(r.side, r.qty) for r in res.sent] == [("sell", 30)] and res.sent[0].coid.endswith("-2")  # a new attempt


def test_the_loop_skips_unknown_and_unreconciled_symbols_and_reports_why():
    b, clock = venue()
    b.script.timeout("A", accepted=False)
    om, _, _ = manager(b, clock)
    loop(om, {}, [Target("rule", "A", 5, "buy")], clock)
    res = loop(om, {}, [Target("rule", "A", 0, "x"), Target("rule", "A", 5, "buy")], clock)
    assert not res.sent and not res.canceled  # the unknown order counts as working: never cancelled or added to
    assert res.skipped and "unknown" in res.skipped[0][1]
    rc = Reconciler(lambda: {"ledger_qty": {"B": 1.0}, "broker_positions": {}})
    rc.check()
    res = loop(om, {}, [Target("rule", "B", 5, "buy")], clock, reconciler=rc)
    assert res.skipped == [(("rule", "B"), "unresolved quantity difference in B")] and not res.sent


def test_sales_go_first_unless_the_caller_keeps_its_order():
    b, clock = venue()
    b.pos["B"] = {"qty": 10, "avg_entry_price": 100.0}
    om, _, _ = manager(b, clock)
    want = [Target("rule", "A", 5, "buy"), Target("rule", "B", 0, "sell")]
    assert [r.symbol for r in loop(om, {("rule", "B"): 10.0}, want, clock).sent] == ["B", "A"]


def test_the_portfolio_builder_feeds_the_loop_and_its_orders_are_what_the_venue_receives():
    b, clock = venue()
    b.pos["B"] = {"qty": 40, "avg_entry_price": 100.0}
    om, _, _ = manager(b, clock)
    builder = PortfolioBuilder(BookLimits(max_names=5))
    market = {"A": Instrument(100.0, 2.0, "tech"), "B": Instrument(100.0, 2.0, "energy")}
    plan = builder.build([TargetList("rule", None, {"A": 1.0})], {("rule", "B"): 40}, market, equity=100_000.0)
    res = loop(om, {("rule", "B"): 40.0}, targets_from_plan(plan), clock)
    sent = {(r.symbol, r.side, r.qty) for r in res.sent}
    assert sent == {(s, "buy" if q > 0 else "sell", abs(q)) for (_, s), q in plan.orders.items()}
    assert [r.side for r in res.sent] == ["sell", "buy"]
    assert not loop(om, {("rule", "B"): 40.0}, targets_from_plan(plan), clock).sent


# ---------------------------------------------------------------- protective stops


def protected(qty=30):
    b, clock = venue(replace_new_id=True)
    om, _, _ = manager(b, clock)
    b.pos["A"] = {"qty": qty, "avg_entry_price": 100.0}
    return b, clock, om, ProtectiveStops(om, run="1")


def test_every_position_gets_one_whole_share_gtc_stop_and_partial_fills_are_protected_at_the_filled_qty():
    b, clock, om, ps = protected(12)
    did = ps.ensure({K: 12.7}, {K: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(K)
    assert did == [f"stop {r.coid}"] and (r.side, r.qty, r.type, r.tif, r.stop) == ("sell", 12, "stop", "gtc", 90.0)
    assert ps.ensure({K: 12.7}, {K: 90.0}, clock(), DAY) == []  # already protected: nothing sent
    b.pos["A"]["qty"] = 30  # the rest of the entry filled
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (r2,) = ps.stops_of(K)
    assert r2.coid == r.coid and r2.qty == 30 and b.order(r2.order_id)["qty"] == 30
    ps.ensure({}, {}, clock(), DAY)  # the sleeve is gone: its stop is cancelled
    assert not ps.stops_of(K) and om.get(r.coid).state is St.CANCELED


def test_a_sale_first_frees_the_shares_the_stop_holds():
    b, clock, om, ps = protected(30)
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    res = loop(om, {K: 30.0}, [Target("rule", "A", 10, "sell")], clock, stops=ps)
    assert [(r.side, r.qty) for r in res.sent] == [("sell", 20)] and res.sent[0].state is St.ACCEPTED
    assert ps.stops_of(K)[0].qty == 10
    res = loop(om, {K: 30.0}, [Target("rule", "A", 0, "sell")], clock, stops=ps)  # the 20 still work: cancel
    assert res.canceled


def test_a_stop_is_re_armed_before_alpacas_90_day_expiry():
    b, clock, om, ps = protected(30)
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(K)
    clock.set(clock() + timedelta(days=84))
    assert ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY) == []
    clock.set(clock() + timedelta(days=2))  # within 5 days of the expiry
    did = ps.ensure({K: 30.0}, {K: 90.0}, clock(), "2026-12-10")
    (new,) = ps.stops_of(K)
    assert did[0] == f"rearm {r.coid}" and om.get(r.coid).state is St.CANCELED and new.coid != r.coid
    assert new.expires_at > r.expires_at


def test_stops_are_cancelled_before_a_split_and_re_created_after_it():
    b, clock, om, ps = protected(30)
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    (r,) = ps.stops_of(K)
    ps.before_split("A")
    assert om.get(r.coid).state is St.CANCELED
    assert ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY) == []  # suspended until the split is booked
    b.pos["A"]["qty"] = 120  # 4:1
    ps.after_split("A")
    ps.ensure({K: 120.0}, {K: 22.5}, clock(), NEXT)
    (new,) = ps.stops_of(K)
    assert (new.qty, new.stop) == (120, 22.5) and new.coid != r.coid


def test_a_replaced_stop_keeps_working_and_its_fill_is_booked():
    b, clock = venue(edits={m: {"l": 95.0} for m in range(35, 40)}, replace_new_id=True)
    om, _, booked = manager(b, clock)
    b.pos["A"] = {"qty": 30, "avg_entry_price": 100.0}
    ps = ProtectiveStops(om, run="1")
    ps.ensure({K: 30.0}, {K: 90.0}, clock(), DAY)
    ps.ensure({K: 30.0}, {K: 96.0}, clock(), DAY)  # re-priced: Alpaca answers with a new order id
    (r,) = ps.stops_of(K)
    assert r.stop == 96.0 and len(r.ids) == 2
    run(b, clock, 30, 36)
    om.sync()
    assert [(f.side, f.qty, f.purpose) for f in booked] == [("sell", 30, "stop")] and "A" not in b.pos


# ---------------------------------------------------------------- the daily runner with protective stops


def test_the_daily_runner_keeps_a_protective_stop_on_every_position_and_reconciles_every_session():
    import numpy as np
    from test_engine_momentum import FIXTURE, POL

    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData, run_daily
    from kaibot.strategies import momentum as mom

    m, info = dr.load_fixture(FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    seen = []
    real = ProtectiveStops.ensure

    def ensure(self, sleeves, prices, now, session):
        out = real(self, sleeves, prices, now, session)
        stops = {x.symbol: x for x in self.om.working(purposes=("stop",))}
        seen.append(set(stops) == {s for _, s in sleeves} and all(
            stops[s].qty == int(q) and stops[s].tif == "gtc" and stops[s].state is St.ACCEPTED
            for (_, s), q in sleeves.items()))
        return out

    ProtectiveStops.ensure = ensure
    try:
        run_ = run_daily(mom.VERSIONS["trend_momentum"], data, POL, i0, len(data.dates) - 1, protect_atr=10.0)
    finally:
        ProtectiveStops.ensure = real
    assert not run_.refused and run_.fills and len(seen) > 200 and all(seen)
    assert any(r.purpose == "stop" for r in run_.om.orders.values())
    assert all(o["time_in_force"] in ("cls", "gtc") for o in run_.broker.orders.values())


# ---------------------------------------------------------------- dividends at the ex-date (live)


def test_alpaca_cash_dividends_are_read_from_the_corporate_actions_answer():
    payload = json.loads((FIXTURES / "alpaca_corporate_actions.json").read_text())
    divs = parse_cash_dividends(payload)
    assert divs and all(d.per_share > 0 and len(d.ex_date) == 10 for d in divs)
    assert len({(d.symbol, d.ex_date, d.per_share, d.special) for d in divs}) == len(divs)
    asked = []

    class Data:
        def get(self, path, params):
            asked.append((path, params))
            return payload

    zzz = next(d for d in divs if d.symbol == "ZZZ")
    got = ExDateDividends(Data())(zzz.ex_date, ["ZZZ"])
    assert got and all(d.symbol == "ZZZ" and d.ex_date == zzz.ex_date for d in got)
    assert asked[0][0] == "/v1/corporate-actions" and asked[0][1]["types"] == "cash_dividend"
    assert ExDateDividends(Data())(zzz.ex_date, []) == [] and len(asked) == 1


def test_a_payment_is_matched_to_its_accrual_once():
    acc = {"r1": {"ref": "r1", "symbol": "A", "ex_date": "2026-08-04", "pay_date": "2026-08-20", "amount": 3.0}}
    assert match_payment({"symbol": "A", "date": "2026-08-20"}, acc) == "r1"
    assert match_payment({"symbol": "B", "date": "2026-08-20"}, acc) is None
    entries = [("div_accrual", acc["r1"]), ("div_paid", {"ref": "r1"})]
    assert receivable(entries[:1]) == 3.0 and receivable(entries) == 0.0


def test_time_flatten_takes_intraday_sleeves_only(monkeypatch):
    from kaibot.engine import core

    multi = type("S", (), {"holding": Holding.MULTI_DAY})()
    monkeypatch.setattr(core.registry, "get", lambda name: multi if name == "held_rule" else None)
    assert core._holding("held_rule") is Holding.MULTI_DAY and core._holding("dip_reversal") is Holding.INTRADAY
    assert bar(0) < et(DAY, "10:00")
