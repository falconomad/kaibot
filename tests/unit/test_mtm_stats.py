"""Gate v2 statistics on daily mark-to-market returns (issue #126): every statistic against a series whose truth
is known. Seeded and small: the whole file runs in a few seconds."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from kaibot.evaluation import metrics, mtm


def ar1(phi: float, shape, sd: float, rng) -> np.ndarray:
    """x_t = phi * x_(t-1) + e_t along the last axis: known autocorrelation phi^k."""
    e = rng.normal(0.0, sd, shape)
    x = np.empty(shape)
    x[..., 0] = e[..., 0]
    for i in range(1, x.shape[-1]):
        x[..., i] = phi * x[..., i - 1] + e[..., i]
    return x


def days(n: int, start: str = "2017-05-01") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n)


# ---------------------------------------------------------------- Newey-West


def test_lag_rule_depends_on_the_sample_length_only():
    assert [mtm.nw_lag(n) for n in (60, 100, 252, 2400)] == [3, 4, 4, 8]  # floor(4 * (n / 100)^(2/9))
    assert mtm.nw_lag(2) == 1 and mtm.nw_lag(1) == 0  # never more lags than the sample allows


def test_long_run_covariance_without_lags_is_the_plain_covariance():
    h = np.random.default_rng(0).normal(size=(500, 2))
    h -= h.mean(axis=0)
    assert np.allclose(mtm.hac_long_run(h, 0), np.cov(h.T, bias=True))


def test_long_run_variance_of_ar1_matches_the_bartlett_formula():
    """For an AR(1) the truncated Bartlett sum is known: g0 * (1 + 2 * sum_j (1 - j/(L+1)) * phi^j)."""
    phi, lags = 0.5, 6
    x = ar1(phi, (60_000,), 1.0, np.random.default_rng(1))
    x -= x.mean()
    want = x.var() * (1 + 2 * sum((1 - j / (lags + 1)) * phi**j for j in range(1, lags + 1)))
    assert mtm.hac_long_run(x[:, None], lags)[0, 0] == pytest.approx(want, rel=0.04)


# ---------------------------------------------------------------- Sharpe


def test_on_independent_days_the_newey_west_t_agrees_with_the_plain_t():
    rng = np.random.default_rng(2)
    s = mtm.sharpe_hac(rng.normal(0.0005, 0.01, 2500))
    assert s.lags == 8 and s.t == pytest.approx(s.t_naive, rel=0.10)
    assert s.sharpe == pytest.approx(s.mean_ann / s.vol_ann)
    ratios = [(lambda x: x.se / x.se_naive)(mtm.sharpe_hac(rng.normal(0.0004, 0.01, 500))) for _ in range(300)]
    assert 0.95 < float(np.mean(ratios)) < 1.05  # the same error on average, not a systematically bigger one


def test_positive_autocorrelation_makes_the_error_larger():
    s = mtm.sharpe_hac(ar1(0.4, (3000,), 0.01, np.random.default_rng(3)) + 0.0005)
    full = math.sqrt((1 + 0.4) / (1 - 0.4))  # what an estimator with every lag would report: 1.53
    assert 1.25 < s.se / s.se_naive < full * 1.05
    assert abs(s.t) < abs(s.t_naive)


def test_newey_west_t_rejects_a_true_zero_about_as_often_as_it_should():
    """400 series with NO edge and autocorrelation 0.3. At the 5% level the plain t cries wolf about three
    times too often; the Newey-West t is close to 5% (a little above: the fixed lag is finite)."""
    x = ar1(0.3, (400, 1000), 0.01, np.random.default_rng(11))
    stats = [mtm.sharpe_hac(row) for row in x]
    hac = float(np.mean([abs(s.t) > 1.96 for s in stats]))
    naive = float(np.mean([abs(s.t_naive) > 1.96 for s in stats]))
    assert naive > 0.12 and hac < 0.10 and hac < naive - 0.05


@pytest.mark.parametrize(("phi", "plain_is"), [(0.0, "right"), (0.2, "too small"), (-0.2, "too large")])
def test_reported_error_is_the_true_spread_of_the_sharpe(phi: float, plain_is: str):
    """What a standard error means: over many samples of a known process with a real edge, the reported error
    equals the spread of the estimate. Fat-tailed days, three kinds of autocorrelation."""
    rng = np.random.default_rng(42)
    shocks = rng.standard_t(4, (600, 800)) * 0.008
    x = shocks.copy()
    for i in range(1, x.shape[1]):
        x[:, i] = phi * x[:, i - 1] + shocks[:, i]
    stats = [mtm.sharpe_hac(row + 0.0008) for row in x]
    true_spread = float(np.std([s.sharpe for s in stats], ddof=1))
    assert float(np.mean([s.se for s in stats])) == pytest.approx(true_spread, rel=0.08)
    plain = stats[0].se_naive / true_spread
    assert {"right": 0.92 < plain < 1.08, "too small": plain < 0.90, "too large": plain > 1.12}[plain_is]


def test_without_lags_the_variance_is_the_one_the_deflated_sharpe_uses():
    r = np.random.default_rng(4).standard_t(5, 4000) * 0.01 + 0.0006
    s = mtm.sharpe_hac(r, lags=0, periods=1)
    skew, kurt = metrics.skew_kurtosis(r)
    assert s.se**2 * r.size == pytest.approx(1 - skew * s.sharpe + (kurt - 1) / 4 * s.sharpe**2, rel=0.01)


def test_a_constant_series_has_no_sharpe():
    s = mtm.sharpe_hac(np.full(50, 0.001))
    assert s.sharpe == 0.0 and math.isinf(s.se) and s.t == 0.0


def test_every_sharpe_is_printed_with_its_haircut_and_a_warning_above_one():
    assert mtm.sharpe_line(0.8) == "0.80 (after the 50% haircut: 0.40)"
    rng = np.random.default_rng(5)
    good = mtm.sharpe_hac(rng.normal(0.0012, 0.01, 2000))  # annual Sharpe near 1.9
    assert good.warning and good.haircut == pytest.approx(good.sharpe / 2) and "WARNING" in good.line()
    plain = mtm.sharpe_hac(rng.normal(0.0002, 0.01, 2000))
    assert not plain.warning and "haircut" in plain.line() and "WARNING" not in plain.line()


def test_missing_days_are_refused_not_skipped():
    with pytest.raises(ValueError, match="NaN"):
        mtm.sharpe_hac([0.01, float("nan"), 0.02])
    net = pd.Series(0.001, index=days(10))
    with pytest.raises(ValueError, match="no return for 3"):
        mtm.align(net, net.iloc[:7])
    assert mtm.align(net.iloc[2:5], net)[1].index.equals(net.index[2:5])  # extra benchmark days are dropped


# ---------------------------------------------------------------- alpha and beta


def test_planted_alpha_and_beta_are_recovered():
    rng = np.random.default_rng(6)
    m = rng.normal(0.0004, 0.01, 4000)
    ab = mtm.alpha_beta(0.0003 + 0.7 * m + rng.normal(0, 0.005, 4000), m)
    assert ab.beta == pytest.approx(0.7, abs=3 * ab.beta_se) and ab.beta_se < 0.02
    assert ab.alpha == pytest.approx(0.0003 * 252, abs=3 * ab.alpha_se)
    assert ab.alpha_t == pytest.approx(ab.alpha / ab.alpha_se)
    assert ab.alpha_se / ab.alpha_se_ols == pytest.approx(1.0, abs=0.15)  # independent errors: both agree
    assert ab.tracking_error == pytest.approx(0.005 * math.sqrt(252), rel=0.05) and 0.6 < ab.r2 < 0.72


def test_alpha_error_grows_with_autocorrelated_residuals_and_keeps_its_size():
    rng = np.random.default_rng(12)
    m = rng.normal(0.0004, 0.01, (300, 600))
    e = ar1(0.3, (300, 600), 0.005, rng)
    fits = [mtm.alpha_beta(0.7 * mk + ek, mk) for mk, ek in zip(m, e, strict=True)]  # true alpha is zero
    hac = float(np.mean([abs(f.alpha_t) > 1.96 for f in fits]))
    ols = float(np.mean([abs(f.alpha / f.alpha_se_ols) > 1.96 for f in fits]))
    assert float(np.mean([f.alpha_se / f.alpha_se_ols for f in fits])) > 1.15
    assert ols > 0.11 and hac < 0.10 and hac < ols - 0.04


def test_reported_alpha_error_is_the_true_spread_of_alpha():
    rng = np.random.default_rng(43)
    m = rng.normal(0.0004, 0.01, (400, 800))
    fits = [mtm.alpha_beta(0.0002 + 0.7 * mk + ek, mk) for mk, ek in zip(m, rng.normal(0, 0.005, m.shape), strict=True)]
    alphas = np.array([f.alpha for f in fits])
    assert float(alphas.mean()) == pytest.approx(0.0002 * 252, abs=0.006)  # unbiased
    assert float(np.mean([f.alpha_se for f in fits])) == pytest.approx(float(alphas.std(ddof=1)), rel=0.08)


def test_exposure_matched_benchmark_uses_yesterdays_exposure():
    m = np.array([0.01, 0.02, -0.01, 0.03])
    assert np.allclose(mtm.exposure_matched(m, [0.5, 1.0, 0.0, 0.25]), [0.0, 0.01, -0.01, 0.0])


# ---------------------------------------------------------------- return, drawdown, years, turnover


def test_cagr_total_return_and_drawdown_on_known_curves():
    one_year = np.full(252, 1.10 ** (1 / 252) - 1)
    assert mtm.total_return(one_year) == pytest.approx(0.10) and mtm.cagr(one_year) == pytest.approx(0.10)
    assert mtm.cagr(np.tile(one_year, 2)) == pytest.approx(0.10)
    assert mtm.cagr([-1.0, 0.5]) == -1.0  # a total loss
    assert mtm.max_drawdown([0.10, -0.50, 0.20]) == pytest.approx(0.50)  # 1.10 -> 0.55
    assert mtm.max_drawdown([-0.10, 0.05]) == pytest.approx(0.10)  # a loss on day one counts
    assert mtm.max_drawdown(one_year) == 0.0


def test_calendar_year_table():
    idx = pd.bdate_range("2023-07-03", "2025-03-31")
    net = pd.Series(np.where(idx.year == 2024, 0.001, -0.0005), index=idx)
    spy = pd.Series(0.0004, index=idx)
    rows = mtm.calendar_years(net, spy)
    assert [r["year"] for r in rows] == [2023, 2024, 2025]
    assert [r["partial"] for r in rows] == [True, False, True]
    y24 = rows[1]
    assert y24["sessions"] == int((idx.year == 2024).sum())
    assert y24["return"] == pytest.approx(1.001 ** y24["sessions"] - 1)
    assert y24["excess"] == pytest.approx(y24["return"] - (1.0004 ** y24["sessions"] - 1))
    assert rows[0]["return"] < 0 and rows[0]["max_drawdown"] == pytest.approx(-rows[0]["return"])
    assert y24["max_drawdown"] == 0.0


def test_turnover_is_annual_and_two_way():
    assert mtm.turnover(np.full(252, 2.0 / 252)) == pytest.approx(2.0)  # the book bought and sold once a year
    with pytest.raises(ValueError):
        mtm.turnover([-0.1])


# ---------------------------------------------------------------- costs


def test_cost_multiples_and_break_even_with_constant_gross_and_cost():
    gross, cost = 0.0006, 0.0002
    net, c = np.full(500, gross - cost), np.full(500, cost)
    assert np.allclose(mtm.at_cost_multiple(net, c, 1.0), net)
    assert np.allclose(mtm.at_cost_multiple(net, c, 2.0), gross - 2 * cost)
    assert np.allclose(mtm.at_cost_multiple(net, c, 0.0), gross)
    assert mtm.breakeven_cost_multiple(net, c) == pytest.approx(gross / cost, abs=1e-4)  # 3x
    assert mtm.breakeven_cost_multiple(np.full(500, -0.0001), np.full(500, 0.00005)) == 0.0  # loses with no costs
    assert math.isinf(mtm.breakeven_cost_multiple(net, np.zeros(500)))  # no costs at all
    assert 0 < mtm.breakeven_cost_multiple(np.full(500, -0.0001), np.full(500, 0.0003)) < 1  # gross 2 bp: 2/3
    with pytest.raises(ValueError, match="negative"):
        mtm.at_cost_multiple(net, -c, 2.0)


def test_cost_table_reports_each_multiple_and_the_break_even():
    rng = np.random.default_rng(7)
    net, c = rng.normal(0.0006, 0.008, 1500), np.full(1500, 0.0001)
    t = mtm.cost_table(net, c)
    assert list(t) == ["1x", "2x", "3x", "breakeven_multiple"]
    assert t["1x"]["cagr"] > t["2x"]["cagr"] > t["3x"]["cagr"]
    assert t["1x"]["sharpe"]["sharpe"] > t["3x"]["sharpe"]["sharpe"] and t["2x"]["sharpe"]["haircut_share"] == 0.5


# ---------------------------------------------------------------- deflated Sharpe


def test_deflated_sharpe_is_the_m4_formula_when_the_variance_is_given():
    r = np.random.default_rng(8).normal(0.0006, 0.01, 1500)
    d = mtm.deflated_sharpe(r, 200, 0.0004)
    assert d.dsr == pytest.approx(metrics.deflated_sharpe(r, 200, 0.0004), abs=1e-12)
    assert d.hurdle == pytest.approx(metrics.expected_max_sharpe(200, 0.0004) * math.sqrt(252))
    one = mtm.deflated_sharpe(r, 1)
    assert one.dsr == one.psr and one.hurdle == 0.0  # one trial: nothing to deflate


def test_more_configurations_raise_the_hurdle():
    r = np.random.default_rng(9).normal(0.0006, 0.01, 2400)
    few, many = mtm.deflated_sharpe(r, 30), mtm.deflated_sharpe(r, 8286)
    assert many.hurdle > few.hurdle > 0 and many.dsr < few.dsr < few.psr
    assert "1/(T-1)" in many.var_source and many.trial_sharpe_var == pytest.approx(1 / 2399)


def test_the_best_of_many_unskilled_rules_rarely_clears_the_deflated_sharpe():
    """Truth known: 100 rules with NO edge, keep the best. Undeflated it looks significant nearly every time;
    deflated by the 100 configurations it does not. The hurdle is the expected best Sharpe of the 100."""
    rng = np.random.default_rng(13)
    n_rules, n_days, reps = 100, 250, 200
    dsr, psr, best = [], [], []
    for _ in range(reps):
        r = rng.normal(0.0, 0.01, (n_rules, n_days))
        sr = r.mean(axis=1) / r.std(axis=1, ddof=1)
        d = mtm.deflated_sharpe(r[int(np.argmax(sr))], n_rules)
        dsr.append(d.dsr > 0.95)
        psr.append(d.psr > 0.95)
        best.append(sr.max() * math.sqrt(252))
    assert np.mean(psr) > 0.90 and np.mean(dsr) < 0.05
    assert float(np.mean(best)) == pytest.approx(d.hurdle, rel=0.05)


# ---------------------------------------------------------------- bootstrap drawdown bands


def test_stationary_bootstrap_indices():
    idx = mtm.stationary_indices(1000, 400, 500, 20, np.random.default_rng(0))
    assert idx.shape == (500, 400) and idx.min() >= 0 and idx.max() <= 999
    starts = (np.diff(idx, axis=1) % 1000) != 1  # a new block wherever the next index is not the next session
    assert float(starts.mean()) == pytest.approx(1 / 20, rel=0.10)  # mean block length 20
    wraps = (idx[:, :-1] == 999) & (idx[:, 1:] == 0)
    assert wraps.any()  # a block that reaches the end continues at the start
    single = mtm.stationary_indices(50, 30, 4, 1, np.random.default_rng(0))
    assert len(np.unique(single)) > 20  # blocks of one day: an ordinary resample
    with pytest.raises(ValueError):
        mtm.stationary_indices(10, 5, 1, 0.5, np.random.default_rng(0))


def test_path_max_drawdown_matches_the_scalar_function():
    paths = np.random.default_rng(1).normal(0.0, 0.02, (20, 80))
    assert np.allclose(mtm.path_max_drawdown(paths), [mtm.max_drawdown(p) for p in paths])


def test_bands_match_a_direct_simulation_of_an_independent_process():
    rng = np.random.default_rng(14)
    sample = rng.normal(0.0004, 0.01, 4000)
    truth = mtm.path_max_drawdown(rng.normal(0.0004, 0.01, (20_000, 120)))
    b = mtm.drawdown_bands(sample, horizon=120, runs=4000, mean_block=20, seed=1)
    assert b.q95 == pytest.approx(float(np.quantile(truth, 0.95)), rel=0.12)
    assert b.q99 == pytest.approx(float(np.quantile(truth, 0.99)), rel=0.15)
    assert b.median < b.q95 < b.q99 and b.horizon == 120 and b.sample_sessions == 4000


def test_blocks_keep_the_clustering_that_makes_drawdowns_deep():
    """Autocorrelated returns: resampling single days understates the 95% drawdown by about a quarter; blocks
    of 20 sessions come close to the truth of the known process."""
    rng = np.random.default_rng(15)
    sample = ar1(0.3, (4000,), 0.01, rng) + 0.0003
    truth = float(np.quantile(mtm.path_max_drawdown(ar1(0.3, (20_000, 120), 0.01, rng) + 0.0003), 0.95))
    block = mtm.drawdown_bands(sample, horizon=120, runs=4000, mean_block=20, seed=1).q95
    single = mtm.drawdown_bands(sample, horizon=120, runs=4000, mean_block=1, seed=1).q95
    assert block > single * 1.15 and abs(block - truth) < abs(single - truth)
    assert block == pytest.approx(truth, rel=0.12)


def test_bands_are_seeded_and_deepen_with_the_horizon():
    r = np.random.default_rng(16).normal(0.0003, 0.012, 1500)
    a = mtm.drawdown_bands(r, horizon=60, runs=1000, seed=7)
    assert a == mtm.drawdown_bands(r, horizon=60, runs=1000, seed=7)
    assert a != mtm.drawdown_bands(r, horizon=60, runs=1000, seed=8)
    assert mtm.drawdown_bands(r, horizon=250, runs=1000, seed=7).q95 > a.q95
    assert mtm.drawdown_bands(r, runs=300, seed=7).horizon == 1500  # default: the length of the test
    assert mtm.drawdown_bands(np.full(100, 0.001), horizon=60, runs=200).q99 == 0.0  # never falls: no drawdown
