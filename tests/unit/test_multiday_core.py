"""Issue #127: the momentum core (trend_momentum, mom_12_1_band) in the multi-day research harness of #113.
Small seeded synthetic bars only, no network."""

import math
import sys
from pathlib import Path

import pytest

from kaibot.replay.multiday_rules import EXPERIMENTS

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "unit"))
import test_multiday as tm  # noqa: E402  (the #113 fixtures: synthetic market, scripted rule, helpers)

# ---------------------------------------------------------------- 0. the first-look rules are pinned

# Written down from the engine of #113 as merged (main @ 45f0dc6), BEFORE the engine class was extended for #127:
# trades, final equity, shares traded, sessions held and spread + impact paid by each first-look rule's fixed
# setting on the synthetic market. Tolerances are far below one share or one cent of drift.
PINNED = {
    "trend_highs": (38, 112737.9158, 2572.0, 889, 107.0224),
    "rel_strength": (42, 119292.9402, 3340.0, 1470, 121.6304),
    "winners_run": (199, 135846.5669, 16784.2462, 304, 854.7216),
    "overnight_spy": (264, 99887.7043, 5216.0, 264, 774.7487),
}


@pytest.mark.parametrize("name", list(EXPERIMENTS))
def test_first_look_rules_are_unchanged_by_the_engine_extensions(name):
    b, stocks = tm.market()
    eng = tm.account(name, b, stocks)
    tr = eng.book.trades
    n, equity, shares, held, costs = PINNED[name]
    assert len(tr) == n and sum(t.hold_days for t in tr) == held
    assert math.isclose(eng.equity, equity, abs_tol=1e-3)
    assert math.isclose(sum(t.qty for t in tr), shares, abs_tol=1e-3)
    assert math.isclose(sum(t.cost_spread + t.cost_impact for t in tr), costs, abs_tol=1e-3)
