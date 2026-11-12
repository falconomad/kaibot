import math

import numpy as np
import pytest

from kaibot.evaluation import metrics
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


def test_helpers_match_scipy():
    """metrics.py no longer imports scipy (the engine image has none): its replacements give scipy's numbers."""
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(0)
    for p in [*rng.uniform(1e-12, 1 - 1e-12, 500), 1 - 1 / 8286, 1 - 1 / (8286 * math.e), 0.5, 1e-300]:
        assert metrics.norm_ppf(p) == pytest.approx(stats.norm.ppf(p), rel=1e-12, abs=1e-12)
    for x in [*rng.normal(0, 4, 500), -40.0, -10.0, 0.0, 10.0]:
        assert metrics.norm_cdf(x) == pytest.approx(stats.norm.cdf(x), rel=1e-11, abs=1e-300)
    assert metrics.norm_ppf(0.0) == -math.inf and metrics.norm_ppf(1.0) == math.inf  # scipy's answers at the ends
    for n in (5, 60, 900):
        r = rng.standard_t(4, n) * 0.01 + 0.0004
        skew, kurt = metrics.skew_kurtosis(r)
        assert skew == pytest.approx(stats.skew(r), rel=1e-12)
        assert kurt == pytest.approx(stats.kurtosis(r, fisher=False), rel=1e-12)
    for x in (rng.integers(0, 6, 40).astype(float), rng.normal(size=33), np.zeros(4)):
        assert np.array_equal(metrics.rank_average(x), stats.rankdata(x))  # ties share the average rank


def test_deflated_sharpe_of_a_series_that_never_moved_is_not_a_number():
    """scripts/experiments_summary.py prints "n/a" for it: there is nothing to deflate."""
    assert all(math.isnan(v) for v in metrics.skew_kurtosis(np.zeros(30)))
    assert math.isnan(deflated_sharpe(np.zeros(30), n_trials=40, trial_sharpe_var=0.001))
    assert math.isnan(metrics.rank_average([1.0, float("nan")])[0])


def test_expected_best_sharpe_of_unskilled_trials():
    assert metrics.expected_max_sharpe(1, 0.01) == 0.0 and metrics.expected_max_sharpe(50, 0.0) == 0.0
    few, many = metrics.expected_max_sharpe(20, 0.0004), metrics.expected_max_sharpe(8286, 0.0004)
    assert few == pytest.approx(0.02 * 1.9, rel=0.02) and many == pytest.approx(0.02 * 3.82, rel=0.02)


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
