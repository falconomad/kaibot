"""S77 (#117): the ledger's accounting, written before the ledger (decision 15 section 3; audit B1 items 4, 5, 13).

The ledger is a pure fold over journal entries: positions per (strategy, symbol), cash, a mark-to-market close per
session, dividends and splits. Start equity of a session is the previous session's mark-to-market equity."""

from __future__ import annotations

import pandas as pd
import pytest

from kaibot.engine.ledger import (
    Action,
    Ledger,
    actions_from_activities,
    actions_from_daily_store,
)

RUN = 7


def fill(side, sym, qty, px, strategy="trend", fees=0.0, **kw):
    return ("fill", {"run_id": RUN, "strategy": strategy, "symbol": sym, "side": side, "qty": qty, "px": px,
                     "fees": fees, **kw})


def mark(session, prices, venue=0.0, shadow=None):
    return ("mark", {"run_id": RUN, "session": session, "prices": prices, "pnl_venue": venue,
                     "pnl_shadow": venue if shadow is None else shadow})


def run(amount=10_000.0):
    return ("run", {"run_id": RUN, "amount": amount})


def fold(*entries) -> Ledger:
    return Ledger.fold(entries)


def test_positions_are_keyed_by_strategy_and_symbol():
    lg = fold(run(), fill("buy", "AAPL", 4, 200.0, "trend"), fill("buy", "AAPL", 3, 201.0, "momentum"))
    b = lg.book(RUN)
    assert set(b.sleeves) == {("trend", "AAPL"), ("momentum", "AAPL")}
    assert b.sleeves[("trend", "AAPL")].qty == 4 and b.sleeves[("momentum", "AAPL")].qty == 3
    assert b.qty_by_symbol() == {"AAPL": 7}
    assert b.cash == pytest.approx(10_000 - 800 - 603)
    lg.apply(*fill("sell", "AAPL", 3, 205.0, "momentum", pnl_venue=12.0, pnl_shadow=11.0))
    assert set(b.sleeves) == {("trend", "AAPL")}, "selling one rule's shares leaves the other rule's"
    assert b.cash == pytest.approx(10_000 - 800 + 12)


def test_new_run_starts_at_its_amount_and_flat_days_add_reported_pnl_exactly():
    lg = fold(run(5000.0))
    assert lg.start_equity(RUN) == 5000.0
    lg.apply(*mark("2026-11-09", {}, venue=12.34, shadow=11.99))
    lg.apply(*mark("2026-11-10", {}, venue=-3.5, shadow=-3.0))
    # The same float the engine used before (pot + the sum of least(venue, shadow) per day): sizing is unchanged.
    assert lg.start_equity(RUN) == 5000.0 + sum([11.99, -3.5])
    assert [c.session for c in lg.book(RUN).closes] == ["2026-11-09", "2026-11-10"]


def test_start_equity_is_yesterdays_mark_to_market_with_an_open_position():
    lg = fold(run(10_000.0), fill("buy", "MSFT", 10, 100.0))
    lg.apply(*mark("2026-11-09", {("trend", "MSFT"): 103.0}))
    c = lg.book(RUN).closes[-1]
    assert c.invested == pytest.approx(1030.0) and c.cash == pytest.approx(9000.0)
    assert c.close_equity == pytest.approx(10_030.0) and lg.start_equity(RUN) == pytest.approx(10_030.0)
    # Day 2: it closes lower. The day's change is against yesterday's mark, not the fill (audit B1 item 4).
    lg.apply(*mark("2026-11-10", {("trend", "MSFT"): 101.0}))
    c2 = lg.book(RUN).closes[-1]
    assert c2.start_equity == pytest.approx(10_030.0) and c2.close_equity == pytest.approx(10_010.0)
    assert c2.change == pytest.approx(-20.0)
    assert lg.book(RUN).sleeves[("trend", "MSFT")].mark == 101.0
    # Day 3: sold at 104 (+40 on the trade); the day's change is +30 from yesterday's mark.
    lg.apply(*fill("sell", "MSFT", 10, 104.0, pnl_venue=40.0, pnl_shadow=40.0))
    lg.apply(*mark("2026-11-11", {}, venue=40.0))
    c3 = lg.book(RUN).closes[-1]
    assert c3.close_equity == pytest.approx(10_040.0) and c3.change == pytest.approx(30.0)
    assert c3.invested == 0 and c3.cash == pytest.approx(10_040.0)


def test_cost_basis_includes_fees_and_partial_sells_keep_the_average():
    lg = fold(run(), fill("buy", "X", 10, 50.0, fees=1.0), fill("buy", "X", 10, 60.0, fees=1.0))
    s = lg.book(RUN).sleeves[("trend", "X")]
    assert s.avg_cost == pytest.approx((500 + 1 + 600 + 1) / 20)
    lg.apply(*fill("sell", "X", 5, 70.0, fees=0.5))
    assert s.qty == 15 and s.avg_cost == pytest.approx((1102) / 20)
    assert lg.book(RUN).realized_venue == pytest.approx(5 * 70 - 0.5 - 5 * 1102 / 20)


def test_cash_dividend_is_booked_to_every_sleeve_that_holds_the_symbol():
    lg = fold(run(), fill("buy", "KO", 10, 60.0, "trend"), fill("buy", "KO", 30, 60.0, "band"))
    lg.apply("dividend", {"run_id": RUN, "symbol": "KO", "ex_date": "2026-11-10", "per_share": 0.5, "ref": "d1"})
    b = lg.book(RUN)
    assert b.cash == pytest.approx(10_000 - 2400 + 20.0)
    assert b.sleeves[("trend", "KO")].dividends == pytest.approx(5.0)
    assert b.sleeves[("band", "KO")].dividends == pytest.approx(15.0)
    lg.apply("dividend", {"run_id": RUN, "symbol": "KO", "ex_date": "2026-11-10", "per_share": 0.5, "ref": "d1"})
    assert b.cash == pytest.approx(10_000 - 2400 + 20.0), "the same action booked twice counts once"
    lg.apply(*mark("2026-11-10", {("trend", "KO"): 59.5, ("band", "KO"): 59.5}))
    c = b.closes[-1]
    assert c.dividends == pytest.approx(20.0) and c.close_equity == pytest.approx(10_000 - 20 + 20.0)


def test_total_dividend_amount_from_the_broker_is_split_by_shares():
    lg = fold(run(), fill("buy", "KO", 10, 60.0, "trend"), fill("buy", "KO", 30, 60.0, "band"))
    lg.apply("dividend", {"run_id": RUN, "symbol": "KO", "ex_date": "2026-11-10", "amount": 19.0, "ref": "a1"})
    b = lg.book(RUN)
    assert b.sleeves[("trend", "KO")].dividends == pytest.approx(4.75)
    assert b.sleeves[("band", "KO")].dividends == pytest.approx(14.25)


def test_split_changes_shares_and_marks_but_not_equity():
    lg = fold(run(), fill("buy", "NVDA", 3, 1000.0))
    lg.apply(*mark("2026-11-09", {("trend", "NVDA"): 1000.0}))
    lg.apply("split", {"run_id": RUN, "symbol": "NVDA", "ex_date": "2026-11-10", "ratio": 10.0, "ref": "s1"})
    s = lg.book(RUN).sleeves[("trend", "NVDA")]
    assert s.qty == 30 and s.avg_cost == pytest.approx(100.0) and s.mark == pytest.approx(100.0)
    assert lg.start_equity(RUN) == pytest.approx(10_000.0)
    lg.apply(*mark("2026-11-10", {("trend", "NVDA"): 100.0}))
    assert lg.book(RUN).closes[-1].change == pytest.approx(0.0)


def test_reverse_split_with_a_fraction_pays_cash_in_lieu():
    lg = fold(run(), fill("buy", "Z", 25, 2.0))
    lg.apply("split", {"run_id": RUN, "symbol": "Z", "ex_date": "2026-11-10", "ratio": 0.1, "price": 21.0,
                       "ref": "r1"})
    b = lg.book(RUN)
    s = b.sleeves[("trend", "Z")]
    assert s.qty == 2, "whole shares only: 2.5 new shares become 2 and half a share in cash"
    assert b.cash == pytest.approx(10_000 - 50 + 0.5 * 21.0)
    assert s.cost == pytest.approx(50.0 * 2 / 2.5)


def test_fold_is_deterministic_and_order_is_the_journal_order():
    entries = [run(), fill("buy", "A", 1, 10.0), mark("2026-11-09", {("trend", "A"): 11.0})]
    a, b = Ledger.fold(entries), Ledger.fold(list(entries))
    assert a.snapshot(RUN) == b.snapshot(RUN)
    assert a.snapshot(RUN)["closes"][-1]["close_equity"] == pytest.approx(10_001.0)


def test_imported_run_continues_the_legacy_start_equity_exactly():
    """A run that began before the ledger: the pot plus its realised daily results, as the old engine summed them."""
    days = [10.5, -2.25, 7.0]
    lg = Ledger.fold([("run", {"run_id": RUN, "amount": 5000.0, "prior_days": days, "prior_cash": 5015.25})])
    assert lg.start_equity(RUN) == 5000.0 + sum(days)
    assert lg.book(RUN).cash == pytest.approx(5015.25)


def test_import_of_an_open_position_takes_its_cost_out_of_cash():
    lg = Ledger.fold([("run", {"run_id": RUN, "amount": 5000.0, "prior_days": [], "prior_cash": 5000.0}),
                      ("position", {"run_id": RUN, "strategy": "dip_reversal", "symbol": "A", "qty": 5,
                                    "px": 100.0, "session": "2026-11-09"})])
    b = lg.book(RUN)
    assert b.cash == pytest.approx(4500.0) and b.sleeves[("dip_reversal", "A")].qty == 5
    assert lg.start_equity(RUN) == pytest.approx(5000.0)


def test_unknown_run_has_no_start_equity():
    assert Ledger().start_equity(99) is None


# ---------------------------------------------------------------- corporate actions: research and live hooks
def test_actions_from_the_daily_store_table():
    df = pd.DataFrame([
        {"key": "AAPL", "symbol": "AAPL", "type": "forward_split", "ex_date": "2026-11-10", "ratio": 4.0,
         "cash": None, "dup": False, "id": "x1"},
        {"key": "KO", "symbol": "KO", "type": "cash_dividend", "ex_date": "2026-11-10", "ratio": None,
         "cash": 0.51, "dup": False, "id": "x2"},
        {"key": "KO", "symbol": "KO", "type": "cash_dividend", "ex_date": "2026-11-10", "ratio": None,
         "cash": 0.51, "dup": True, "id": "x3"},
        {"key": "T", "symbol": "T", "type": "name_change", "ex_date": "2026-11-10", "ratio": None, "cash": None,
         "dup": False, "id": "x4"},
        {"key": "MSFT", "symbol": "MSFT", "type": "cash_dividend", "ex_date": "2026-11-11", "ratio": None,
         "cash": 0.8, "dup": False, "id": "x5"},
    ])
    got = actions_from_daily_store(df, "2026-11-10")
    assert got == [Action("AAPL", "2026-11-10", "split", ratio=4.0, ref="daily:x1"),
                   Action("KO", "2026-11-10", "dividend", per_share=0.51, ref="daily:x2")]


ALPACA_ACTIVITIES = [  # the shape documented for non-trade activities (docs.alpaca.markets, account activities)
    {"activity_type": "DIV", "id": "20261110::a", "date": "2026-11-10", "net_amount": "1.02", "symbol": "T",
     "cusip": "00206R102", "qty": "2", "per_share_amount": "0.51"},
    {"activity_type": "DIVNRA", "id": "20261110::b", "date": "2026-11-10", "net_amount": "-0.15", "symbol": "T"},
    {"activity_type": "SSP", "id": "20261110::c", "date": "2026-11-10", "net_amount": "0", "symbol": "NVDA",
     "qty": "27"},
    {"activity_type": "FILL", "id": "20261110::d", "symbol": "T"},
]


def test_actions_from_alpaca_activities_fixture():
    got = actions_from_activities(ALPACA_ACTIVITIES, ledger_qty={"T": 2, "NVDA": 3}, broker_qty={"NVDA": 30})
    assert got == [Action("T", "2026-11-10", "dividend", amount=1.02, ref="alpaca:20261110::a"),
                   Action("T", "2026-11-10", "dividend", amount=-0.15, ref="alpaca:20261110::b"),
                   Action("NVDA", "2026-11-10", "split", ratio=10.0, ref="alpaca:20261110::c")]


def test_alpaca_split_without_a_known_ratio_is_not_guessed():
    got = actions_from_activities([ALPACA_ACTIVITIES[2]], ledger_qty={"NVDA": 3}, broker_qty={})
    assert got == []
