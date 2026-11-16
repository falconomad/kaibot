"""#266: spin-offs and symbol changes in the ledger (written before the code). A spin-off moves part of the parent's
cost basis to the new shares (Alpaca's cost basis): equity, cash and the shares of the parent do not change; the
new shares are a sleeve of the same rule. A symbol change moves the sleeve to the new symbol. Both fold once by ref."""

from __future__ import annotations

from kaibot.engine.ledger import Ledger

RUN = 7


def fold(*entries) -> Ledger:
    return Ledger.fold(entries)


def run(amount=10_000.0):
    return ("run", {"run_id": RUN, "amount": amount})


def buy(sym, qty, px, strategy="trend_momentum"):
    return ("fill", {"run_id": RUN, "strategy": strategy, "symbol": sym, "side": "buy", "qty": qty, "px": px,
                     "fees": 0.0})


def spin(qty, cost, ref="spin:1"):
    return ("spinoff", {"run_id": RUN, "symbol": "P", "new_symbol": "N", "ex_date": "2026-08-04", "qty": qty,
                        "cost": cost, "ref": ref})


def test_a_spin_off_moves_cost_to_the_new_shares_pro_rata_and_keeps_cash():
    lg = fold(run(), buy("P", 30, 100.0, "a"), buy("P", 10, 100.0, "b"), spin(20, 1000.0))
    b = lg.book(RUN)
    assert b.cash == 10_000.0 - 4_000.0
    assert b.sleeves[("a", "P")].qty == 30 and b.sleeves[("a", "P")].cost == 3000.0 - 750.0
    assert b.sleeves[("a", "N")].qty == 15 and b.sleeves[("a", "N")].cost == 750.0
    assert b.sleeves[("b", "N")].qty == 5 and b.sleeves[("b", "N")].cost == 250.0
    assert b.sleeves[("a", "N")].origin == "spin_off:P"
    # equity at cost: the spin-off itself changes nothing
    assert abs(b.equity({}) - 10_000.0) < 1e-9


def test_a_spin_off_is_booked_once_and_never_without_a_parent():
    lg = fold(run(), buy("P", 40, 100.0), spin(20, 1000.0), spin(20, 1000.0))
    assert lg.book(RUN).sleeves[("trend_momentum", "N")].qty == 20
    lg = fold(run(), spin(20, 1000.0))
    assert lg.book(RUN).sleeves == {}


def test_the_spun_value_is_kept_in_the_pot():
    """Parent 40 x 100 = 4000. Ex-date: parent 60, child 8 per share (20 shares = 160 x ... ) -> no loss booked
    beyond the market's: equity = cash + 40 x 60 + 20 x 80."""
    lg = fold(run(), buy("P", 40, 100.0), spin(20, 1600.0),
              ("mark", {"run_id": RUN, "session": "2026-08-04", "prices": {"trend_momentum|P": 60.0,
                                                                         "trend_momentum|N": 80.0},
                        "pnl_venue": 0.0, "pnl_shadow": 0.0}))
    c = lg.book(RUN).closes[-1]
    assert c.close_equity_venue == 6_000.0 + 40 * 60.0 + 20 * 80.0 == 10_000.0


def test_a_symbol_change_moves_the_sleeve_with_its_cost_and_mark():
    lg = fold(run(), buy("OLD", 40, 50.0),
              ("mark", {"run_id": RUN, "session": "2026-08-03", "prices": {"trend_momentum|OLD": 52.0},
                        "pnl_venue": 0.0, "pnl_shadow": 0.0}),
              ("symbol_change", {"run_id": RUN, "symbol": "OLD", "new_symbol": "NEW", "ex_date": "2026-08-04",
                                 "ref": "sc:1"}))
    b = lg.book(RUN)
    assert ("trend_momentum", "OLD") not in b.sleeves
    s = b.sleeves[("trend_momentum", "NEW")]
    assert s.qty == 40 and s.cost == 2000.0 and s.mark == 52.0 and s.symbol == "NEW"
    assert b.qty_by_symbol() == {"NEW": 40}


def test_a_spin_off_splits_the_last_mark_so_the_marked_value_is_kept():
    """The engine's intraday equity starts from the last close's marks: the parent's mark keeps (1 - f) of its
    value and the new shares get f, f being the share of basis the venue moved (Alpaca allocates by value)."""
    lg = fold(run(), buy("P", 40, 100.0),
              ("mark", {"run_id": RUN, "session": "2026-08-03", "prices": {"trend_momentum|P": 110.0},
                        "pnl_venue": 0.0, "pnl_shadow": 0.0}),
              spin(20, 1000.0))  # a quarter of the 4000 basis
    b = lg.book(RUN)
    p, n = b.sleeves[("trend_momentum", "P")], b.sleeves[("trend_momentum", "N")]
    assert abs(p.mark - 82.5) < 1e-9 and abs(n.mark - 55.0) < 1e-9
    assert abs(p.qty * p.mark + n.qty * n.mark - 40 * 110.0) < 1e-9
