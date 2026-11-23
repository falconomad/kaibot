"""M10 1.21 (#446), step 1: invariant 10 of the deterministic simulation, "no buy leaves while the one trading state
is not active", checked at the order seam (World._state_seam). It holds on the seeds of the ordinary run
(test_dst.py), and it has teeth: an engine that reports `reducing_only` while it still buys is caught."""

from __future__ import annotations

from world import Scenario, run_scenario

from kaibot.broker.faulty import NO_FAULTS
from kaibot.engine import order_path
from kaibot.engine.core import Engine
from kaibot.engine.state import Trading, TradingState


def test_a_buy_while_reducing_only_is_a_violation(monkeypatch):
    monkeypatch.setattr(Engine, "trading_state", lambda self: Trading(TradingState.REDUCING_ONLY, "planted"))
    res = run_scenario(Scenario.from_seed(1, NO_FAULTS))
    assert any(v.startswith("invariant 10: a buy of ") and "(planted)" in v for v in res.violations), res.violations


def test_the_seam_is_restored_after_a_run():
    saved = order_path.submit, order_path.submit_bracket, order_path.submit_oto_stop
    assert run_scenario(Scenario.from_seed(2, NO_FAULTS)).ok
    assert (order_path.submit, order_path.submit_bracket, order_path.submit_oto_stop) == saved
