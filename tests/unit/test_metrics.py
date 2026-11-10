import numpy as np

from kaibot.evaluation.metrics import (
    GateReport,
    deflated_sharpe,
    max_drawdown,
    min_trades_needed,
    profit_factor,
    random_entry_percentile,
    t_stat,
)


def test_min_trades_matches_research():
    assert min_trades_needed(0.0010, 0.0100) == 900


def test_noise_is_not_an_edge():
    rng = np.random.default_rng(20261110)
    r = rng.normal(0, 0.01, 900)
    assert abs(t_stat(r)) < 3
    assert deflated_sharpe(r, n_trials=50, trial_sharpe_var=0.0004) < 0.95


def test_basic_metrics():
    assert profit_factor([0.02, -0.01, 0.01]) == 3.0
    assert abs(max_drawdown([100, 110, 99, 120]) - 0.1) < 1e-12
    assert random_entry_percentile(5.0, [1, 2, 3, 6]) == 75.0


def test_gate_report_below_900_can_only_kill():
    g = GateReport(312, 1.2, 1.1, 0.4, 60, 0.05, 0.04, 0.02, 0.08)
    assert g.can_only_kill and not g.passed
