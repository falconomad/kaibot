import numpy as np

from kaibot.evaluation.metrics import (
    GateReport,
    deflated_sharpe,
    max_drawdown,
    min_trades_needed,
    pbo_cscv,
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


def test_pbo_noise_is_about_half():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, (512, 20))  # 20 trials, none better than another
    res = pbo_cscv(r, n_blocks=10)
    assert res.n_splits == 252 and res.n_trials == 20
    assert 0.3 < res.pbo < 0.7


def test_pbo_real_edge_is_near_zero():
    rng = np.random.default_rng(2)
    r = rng.normal(0, 0.01, (512, 20))
    r[:, 7] += 0.004  # one trial has a genuine daily edge
    assert pbo_cscv(r, n_blocks=10).pbo < 0.05


def test_pbo_noise_winner_does_not_carry_oos():
    """Among noise trials, the IS winner's Sharpe does not carry over OOS (slope well below 1)."""
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.01, (400, 50))
    res = pbo_cscv(r, n_blocks=8)
    assert res.oos_degradation < 0.5  # IS Sharpe of the chosen trial does not carry OOS


def test_pbo_rejects_bad_shapes():
    import pytest

    with pytest.raises(ValueError):
        pbo_cscv(np.zeros((100, 1)))
    with pytest.raises(ValueError):
        pbo_cscv(np.zeros((100, 3)), n_blocks=7)
    with pytest.raises(ValueError):
        pbo_cscv(np.zeros((10, 3)), n_blocks=16)
