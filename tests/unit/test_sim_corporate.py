"""#266: SimBroker credits a spin-off and applies a symbol change as Alpaca does (written before the code).
Alpaca: the parent keeps its shares, its cost basis may be lowered, the new shares are a new position, and an SSO
activity is posted; a symbol change renames the position (SC activity).
A spin-off sets `cost_basis` on both positions."""

from __future__ import annotations

import pandas as pd
from test_sim_broker import DAY, NEXT, buy, flat, run, venue

from kaibot.broker.sim import SimBroker


def held_a() -> tuple[SimBroker, object]:
    b, clock = venue({"A": pd.concat([flat(DAY), flat(NEXT)], ignore_index=True)})
    buy(b, 30)
    run(b, clock, 30, 31)
    return b, clock


def test_a_spin_off_credits_new_shares_and_moves_basis():
    b, _ = held_a()
    p = b.positions()["A"]
    basis = float(p["qty"]) * float(p["avg_entry_price"])
    b.spin_off("A", "AN", ratio=0.5, basis_share=0.2, day=NEXT)
    pos = b.positions()
    assert float(pos["A"]["qty"]) == 30 and abs(float(pos["A"]["cost_basis"]) - 0.8 * basis) < 1e-6
    assert float(pos["AN"]["qty"]) == 15 and abs(float(pos["AN"]["cost_basis"]) - 0.2 * basis) < 1e-6
    (row,) = b.activities(types=("SSO",))
    assert row["symbol"] == "AN" and row["date"] == NEXT and float(row["qty"]) == 15


def test_a_spin_off_pays_the_fraction_in_cash_and_never_credits_a_position_not_held():
    b, _ = held_a()
    cash = b.cash
    b.spin_off("A", "AN", ratio=1 / 7, basis_share=0.1, day=NEXT, price=7.0)  # 30 / 7 = 4 whole + 2/7
    assert float(b.positions()["AN"]["qty"]) == 4 and abs(b.cash - cash - 2.0) < 1e-6
    b.spin_off("Z", "ZN", ratio=1.0, basis_share=0.1, day=NEXT)
    assert "ZN" not in b.positions()


def test_a_symbol_change_renames_the_position():
    b, _ = held_a()
    before = dict(b.positions()["A"])
    b.symbol_change("A", "A2", day=NEXT)
    pos = b.positions()
    assert "A" not in pos and pos["A2"]["qty"] == before["qty"]
    (row,) = b.activities(types=("SC",))
    assert row["symbol"] == "A2" and row["date"] == NEXT
