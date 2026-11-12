"""Statistics on DAILY MARK-TO-MARKET returns for evidence gate v2 (decision 15 section 7; audit G1).

The M4 gate judges per-trade P&L and assumes the trades are independent. A multi-day book breaks both: its
positions overlap and share the market's move, and P&L booked on the exit day hides open losses. Everything here
therefore takes one number per session: r_t = equity_t / equity_(t-1) - 1, marked at the close, after costs.

What is here, and what each test checks against a series whose truth is known (tests/unit/test_mtm_stats.py):

- `sharpe_hac`       annualised Sharpe, its Newey-West standard error and t (i.i.d. data: agrees with the plain t;
                     positively autocorrelated data: larger error, and a test of true size where the plain t
                     over-rejects);
- `alpha_beta`       regression on SPY total return with Newey-West errors (planted alpha and beta are recovered);
- `drawdown_bands`   stationary bootstrap of the maximum drawdown (i.i.d. data: matches a direct simulation of
                     the known process; clustered data: wider than an i.i.d. resample);
- `calendar_years`, `cagr`, `max_drawdown`, `turnover`;
- `at_cost_multiple`, `breakeven_cost_multiple`  (constant gross and cost: the break-even is gross / cost);
- `deflated_sharpe`  against the trial count (the best of N unskilled series rarely clears it, although its
                     undeflated probability nearly always does);
- `sharpe_line`      every Sharpe is printed with its 50% haircut beside it.

Not claimed: the Sharpe is annualised with sqrt(252), which is exact only for uncorrelated returns (Lo 2002
gives the correction); the standard error and t do allow for autocorrelation. Cost multiples scale each day's
cost and keep positions as they were (a first-order reading: with higher costs the account, and so later
position sizes, would be slightly smaller).

numpy, pandas and the standard library only (no scipy).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from kaibot.evaluation.metrics import expected_max_sharpe, norm_cdf, skew_kurtosis

PERIODS = 252  # sessions a year
FULL_YEAR_SESSIONS = 240  # fewer sessions than this in a calendar year: the year is shown as partial


# ---------------------------------------------------------------- inputs


def _arr(x, name: str = "returns") -> np.ndarray:
    a = np.asarray(x.to_numpy() if isinstance(x, pd.Series) else x, dtype=float).ravel()
    if not np.isfinite(a).all():
        raise ValueError(f"{name} contain NaN or infinite values: a missing day must be fixed, not skipped")
    return a


def dated(x: pd.Series, name: str = "returns") -> pd.Series:
    """A float series on a sorted, unique, timezone-free daily index (one row per session)."""
    if not isinstance(x, pd.Series):
        raise TypeError(f"{name} must be a pandas Series indexed by session date")
    idx = pd.DatetimeIndex(pd.to_datetime(x.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(_arr(x, name), index=idx.normalize(), name=name)
    if not s.index.is_unique:
        raise ValueError(f"{name}: more than one row for the same session")
    return s.sort_index()


def align(net: pd.Series, benchmark: pd.Series) -> tuple[pd.Series, pd.Series]:
    """The benchmark on exactly the strategy's sessions. A strategy session without a benchmark return is an
    error (dropping it would quietly change the test window)."""
    net, benchmark = dated(net, "strategy returns"), dated(benchmark, "benchmark returns")
    missing = net.index.difference(benchmark.index)
    if len(missing):
        raise ValueError(f"the benchmark has no return for {len(missing)} of the strategy's sessions "
                         f"(first: {missing[0].date()})")
    return net, benchmark.reindex(net.index)


# ---------------------------------------------------------------- Newey-West


def nw_lag(n: int) -> int:
    """The Newey-West truncation lag used everywhere here: floor(4 * (n / 100)^(2/9)).

    Why this rule: (1) it depends on the sample length only, so it is fixed before the result is seen and
    cannot be chosen to flatter it; (2) it grows with n, which the estimator needs to be consistent, and slowly
    enough to keep its variance down; (3) it is the usual fixed default in econometric software (the pilot lag
    of Newey & West 1994 for the Bartlett kernel), so the numbers can be reproduced elsewhere. Ten years of
    sessions give 8 lags, 60 sessions give 3. Daily mark-to-market returns of a book that holds for weeks are
    not built from overlapping windows, so their autocorrelation is short; a rule that reports on overlapping
    multi-day returns would need lags of at least the overlap instead.

    Limit, measured in the tests: with a first-order autocorrelation of 0.2 the reported error equals the true
    spread of the estimate; at 0.3 it is still about 5% too small (a 5% test rejects a true zero about 7% of
    the time, against 18% for the plain t), because a finite lag cannot see the whole tail."""
    return max(0, min(int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))), n - 1))


def hac_long_run(h: np.ndarray, lags: int) -> np.ndarray:
    """Newey-West (1987) long-run covariance of the columns of `h` (T x k, each column with mean zero):

        S = G_0 + sum_{j=1..L} (1 - j / (L + 1)) * (G_j + G_j'),     G_j = (1 / T) * sum_t h_t h_(t-j)'

    The Bartlett weights 1 - j / (L + 1) keep S positive semi-definite. L = 0 gives the plain covariance."""
    h = np.asarray(h, dtype=float)
    t = h.shape[0]
    s = h.T @ h / t
    for j in range(1, min(int(lags), t - 1) + 1):
        g = h[j:].T @ h[:-j] / t
        s += (1.0 - j / (lags + 1.0)) * (g + g.T)
    return s


# ---------------------------------------------------------------- Sharpe


@dataclass(frozen=True)
class SharpeStats:
    n: int
    sharpe: float  # annualised: mean / sd * sqrt(periods)
    se: float  # Newey-West standard error of the annualised Sharpe
    t: float  # sharpe / se
    lags: int
    t_naive: float  # mean / (sd / sqrt(n)): what a reading that assumes independent days reports
    se_naive: float  # sqrt(periods / n): the standard error behind t_naive
    haircut: float  # the Sharpe after the haircut: the number to plan with
    haircut_share: float
    warning: bool  # above the level where a long-only stock rule looks too good (decision 15)
    mean_ann: float
    vol_ann: float

    def line(self) -> str:
        return sharpe_line(self.sharpe, self.haircut_share) + (
            f", t {self.t:.2f} (Newey-West, {self.lags} lags; plain t {self.t_naive:.2f})"
            + (" - WARNING: above the level where a long-only stock rule is suspect" if self.warning else ""))


def sharpe_line(sharpe: float, haircut_share: float = 0.5) -> str:
    """How every backtested Sharpe is printed: the number and, beside it, the number after the haircut."""
    return f"{sharpe:.2f} (after the {haircut_share:.0%} haircut: {sharpe * (1.0 - haircut_share):.2f})"


def sharpe_hac(returns, *, periods: int = PERIODS, lags: int | None = None, haircut: float = 0.5,
               warn_above: float = 1.0, rf: float = 0.0) -> SharpeStats:
    """Annualised Sharpe of daily returns with a Newey-West standard error and t.

    SR = mu / sigma (daily, in excess of the daily risk-free rate `rf`, default 0), annualised by sqrt(periods).

    Standard error (Lo 2002, "The Statistics of Sharpe Ratios", the GMM form for returns that are not i.i.d.):
    with theta = (mu, sigma^2) estimated from the moments h_t = (r_t - mu, (r_t - mu)^2 - sigma^2),

        sqrt(T) * (theta_hat - theta) -> N(0, S),    S = long-run covariance of h_t (Newey-West, `nw_lag`),
        Var(SR_hat) = g' S g / T,                    g = (1 / sigma, -mu / (2 sigma^3))   (delta method).

    With no lags this is (1 - skew * SR + (kurt - 1) / 4 * SR^2) / T, the variance used by the deflated Sharpe;
    for normal i.i.d. returns it is (1 + SR^2 / 2) / T, so t is close to the plain mean / (sd / sqrt(T)).
    Positive autocorrelation makes S, and so the error, larger. A constant series has no Sharpe: 0 with an
    infinite error."""
    r = _arr(returns) - rf
    n = r.size
    if n < 3:
        raise ValueError("need at least 3 daily returns")
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    lag = nw_lag(n) if lags is None else max(0, int(lags))
    ann = math.sqrt(periods)
    if not sd > 0.0:
        return SharpeStats(n, 0.0, math.inf, 0.0, lag, 0.0, ann / math.sqrt(n), 0.0, haircut, False,
                           mu * periods, 0.0)
    d = r - mu
    h = np.column_stack([d, d * d - float(np.mean(d * d))])
    g = np.array([1.0 / sd, -mu / (2.0 * sd**3)])
    var = float(g @ hac_long_run(h, lag) @ g) / n
    sr = mu / sd
    se = math.sqrt(var) if var > 0.0 else math.inf
    return SharpeStats(n, sr * ann, se * ann, sr / se if math.isfinite(se) else 0.0, lag, sr * math.sqrt(n),
                       ann / math.sqrt(n), sr * ann * (1.0 - haircut), haircut, sr * ann > warn_above,
                       mu * periods, sd * ann)


# ---------------------------------------------------------------- alpha and beta against SPY


@dataclass(frozen=True)
class AlphaBeta:
    n: int
    alpha: float  # annualised: daily intercept * periods
    alpha_se: float  # Newey-West, annualised
    alpha_t: float
    beta: float
    beta_se: float
    r2: float
    tracking_error: float  # annualised sd of the residual
    lags: int
    alpha_se_ols: float  # the classical error, for comparison


def alpha_beta(returns, benchmark, *, periods: int = PERIODS, lags: int | None = None, rf: float = 0.0) -> AlphaBeta:
    """Alpha and beta of daily returns on a benchmark (SPY total return), with Newey-West errors.

    OLS of y_t = r_t - rf on x_t = (1, m_t - rf):  b = (X'X)^-1 X'y,  e = y - X b.
    Covariance (Newey & West 1987, with the usual T / (T - 2) small-sample factor):

        V = T / (T - 2) * (X'X)^-1 * [T * S] * (X'X)^-1,    S = long-run covariance of x_t * e_t (`hac_long_run`).

    alpha is the intercept times `periods`; alpha_t = intercept / its standard error. A long-only book is
    mostly market exposure, so the alpha and its t, not the raw Sharpe, say whether the rule added anything."""
    y, m = _arr(returns) - rf, _arr(benchmark, "benchmark returns") - rf
    if y.size != m.size:
        raise ValueError("returns and benchmark must cover the same sessions")
    n = y.size
    if n < 10:
        raise ValueError("need at least 10 sessions for a regression")
    x = np.column_stack([np.ones(n), m])
    xtx_inv = np.linalg.inv(x.T @ x)
    b = xtx_inv @ x.T @ y
    e = y - x @ b
    lag = nw_lag(n) if lags is None else max(0, int(lags))
    v = n / (n - 2.0) * xtx_inv @ (n * hac_long_run(x * e[:, None], lag)) @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(v), 0.0))
    s2 = float(e @ e) / (n - 2.0)
    tss = float(((y - y.mean()) ** 2).sum())
    return AlphaBeta(n, float(b[0]) * periods, float(se[0]) * periods, float(b[0] / se[0]) if se[0] > 0 else 0.0,
                     float(b[1]), float(se[1]), 1.0 - float(e @ e) / tss if tss > 0 else 0.0,
                     math.sqrt(s2 * periods), lag, math.sqrt(s2 * xtx_inv[0, 0]) * periods)


def exposure_matched(benchmark, exposure) -> np.ndarray:
    """Daily returns of holding the benchmark at the strategy's own exposure: yesterday's close exposure (share
    of equity invested, 0..1) times today's benchmark return; the first session earns nothing. "What the same
    amount of market, with no stock picking, would have made" (audit G1)."""
    m, e = _arr(benchmark, "benchmark returns"), _arr(exposure, "exposure")
    if m.size != e.size:
        raise ValueError("benchmark and exposure must cover the same sessions")
    return np.r_[0.0, e[:-1]] * m


# ---------------------------------------------------------------- return, drawdown, years, turnover


def total_return(returns) -> float:
    return float(np.prod(1.0 + _arr(returns)) - 1.0)


def cagr(returns, periods: int = PERIODS) -> float:
    """Compound annual growth: (1 + total return)^(periods / sessions) - 1; -1 after a total loss."""
    r = _arr(returns)
    growth = float(np.prod(1.0 + r))
    return growth ** (periods / r.size) - 1.0 if r.size and growth > 0.0 else -1.0


def max_drawdown(returns) -> float:
    """Largest peak-to-trough fall of the mark-to-market equity curve, as a positive share (0.25 = -25%).
    The curve starts at 1 before the first session, so a loss on day one counts."""
    eq = np.cumprod(1.0 + _arr(returns))
    if not eq.size:
        return 0.0
    peak = np.maximum(np.maximum.accumulate(eq), 1.0)
    return float((1.0 - eq / peak).max())


def calendar_years(net: pd.Series, benchmark: pd.Series | None = None) -> list[dict]:
    """One row per calendar year: sessions, compounded return, the benchmark's, the difference, the year's own
    maximum drawdown, and `partial` when the year has fewer than 240 sessions (a first or last year)."""
    net = dated(net)
    bench = align(net, benchmark)[1] if benchmark is not None else None
    out = []
    for year, r in net.groupby(net.index.year):
        row = {"year": int(year), "sessions": int(r.size), "return": total_return(r),
               "max_drawdown": max_drawdown(r), "partial": bool(r.size < FULL_YEAR_SESSIONS)}
        if bench is not None:
            row["benchmark"] = total_return(bench[bench.index.year == year])
            row["excess"] = row["return"] - row["benchmark"]
        out.append(row)
    return out


def turnover(traded, periods: int = PERIODS) -> float:
    """Annual two-way turnover: the mean of (value bought + value sold that session) / (equity at the start of
    the session), times `periods`. 2.0 means the book is bought and sold once a year. `traded` is that daily
    ratio."""
    t = _arr(traded, "traded value")
    if (t < 0).any():
        raise ValueError("traded value cannot be negative")
    return float(t.mean() * periods) if t.size else 0.0


# ---------------------------------------------------------------- costs


def at_cost_multiple(net, costs, multiple: float) -> np.ndarray:
    """Net daily returns when every cost is `multiple` times the modelled one.

    `costs` is the cost charged each session at 1x as a share of that session's starting equity (>= 0), so the
    gross return is net + costs and the return at k times the cost is net - (k - 1) * costs."""
    r, c = _arr(net), _arr(costs, "costs")
    if r.size != c.size:
        raise ValueError("returns and costs must cover the same sessions")
    if (c < 0).any():
        raise ValueError("a cost cannot be negative")
    return r - (multiple - 1.0) * c


def breakeven_cost_multiple(net, costs, *, upper: float = 1000.0, tol: float = 1e-6) -> float:
    """The cost multiple k at which the compounded net return is exactly zero:

        prod_t (1 + net_t - (k - 1) * cost_t) = 1.

    Growth falls as k rises, so k is found by bisection. 0.0 when the rule loses even with no costs, `inf`
    when it has no costs or still gains at `upper` times the modelled cost. Above 1 means the rule survives
    costs that much higher than modelled."""
    r, c = _arr(net), _arr(costs, "costs")

    def log_growth(k: float) -> float:
        x = 1.0 + at_cost_multiple(r, c, k)
        return float(np.log(x).sum()) if (x > 0).all() else -math.inf

    if log_growth(0.0) <= 0.0:
        return 0.0
    if not c.sum() > 0.0 or log_growth(upper) > 0.0:
        return math.inf
    lo, hi = 0.0, upper
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if log_growth(mid) > 0.0 else (lo, mid)
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- deflated Sharpe


@dataclass(frozen=True)
class DeflatedSharpe:
    dsr: float  # probability that the true Sharpe is above the hurdle
    psr: float  # the same probability against a hurdle of zero (no deflation), for comparison
    sharpe: float  # annualised
    hurdle: float  # annualised expected best Sharpe of n_trials unskilled trials
    n_trials: int
    trial_sharpe_var: float  # per-period variance of the trials' Sharpes used for the hurdle
    var_source: str


def deflated_sharpe(returns, n_trials: int, trial_sharpe_var: float | None = None, *,
                    periods: int = PERIODS) -> DeflatedSharpe:
    """Deflated Sharpe ratio (Bailey & Lopez de Prado 2014) of daily returns against `n_trials` configurations.

        DSR = Z( (SR - SR0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurt - 1) / 4 * SR^2) ),
        SR0 = sqrt(V) * ((1 - g) * Z^-1(1 - 1/N) + g * Z^-1(1 - 1/(N e))),

    with SR the daily Sharpe, N = `n_trials` (configurations in the trial registry, not rows) and V the variance
    of the daily Sharpes across those trials. When V is not known (`None` or not positive) the sampling variance
    of an unskilled Sharpe over this many days, 1 / (T - 1), is used: the hurdle is then the expected best of N
    independent unskilled trials, which is the strict reading. A single trial has no hurdle (DSR = PSR)."""
    r = _arr(returns)
    n = r.size
    if n < 3:
        raise ValueError("need at least 3 daily returns")
    n_trials = max(int(n_trials), 1)
    sd = float(r.std(ddof=1))
    sr = float(r.mean()) / sd if sd > 0.0 else 0.0
    if trial_sharpe_var is not None and trial_sharpe_var > 0.0:
        v, src = float(trial_sharpe_var), "variance of the logged trial Sharpes"
    else:
        v, src = 1.0 / (n - 1), "1/(T-1): the sampling variance of an unskilled Sharpe (no trial Sharpes given)"
    sr0 = expected_max_sharpe(n_trials, v)
    if n_trials == 1:
        src = "one trial: no hurdle"
    skew, kurt = skew_kurtosis(r) if sd > 0.0 else (0.0, 3.0)  # a series that never moved: Sharpe 0, no shape
    denom = math.sqrt(max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2, 1e-12))
    z = math.sqrt(n - 1) / denom
    ann = math.sqrt(periods)
    return DeflatedSharpe(norm_cdf((sr - sr0) * z), norm_cdf(sr * z), sr * ann, sr0 * ann, n_trials, v, src)


# ---------------------------------------------------------------- bootstrap drawdown bands


def stationary_indices(n: int, horizon: int, runs: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """Row indices of `runs` stationary-bootstrap resamples of length `horizon` from a series of length `n`
    (Politis & Romano 1994): each resample is a chain of blocks that start at a uniformly random session and
    whose lengths are geometric with mean `mean_block`; a block that runs off the end wraps to the start."""
    if n < 1 or horizon < 1 or runs < 1 or mean_block < 1:
        raise ValueError("n, horizon, runs and mean_block must all be at least 1")
    new = rng.random((runs, horizon)) < 1.0 / mean_block
    new[:, 0] = True
    starts = rng.integers(0, n, size=(runs, horizon))
    pos = np.arange(horizon)
    began = np.maximum.accumulate(np.where(new, pos, 0), axis=1)  # where the block covering each position began
    return (np.take_along_axis(starts, began, axis=1) + pos - began) % n


def path_max_drawdown(paths: np.ndarray) -> np.ndarray:
    """Maximum drawdown of each row of daily returns (the curve starts at 1 before the first session)."""
    eq = np.cumprod(1.0 + paths, axis=1)
    peak = np.maximum(np.maximum.accumulate(eq, axis=1), 1.0)
    return (1.0 - eq / peak).max(axis=1)


@dataclass(frozen=True)
class DrawdownBands:
    horizon: int  # sessions in each resampled path
    runs: int
    mean_block: float
    seed: int
    median: float
    q95: float  # Gate C: the live drawdown must stay at or inside this
    q99: float  # Gate C: beyond this the rule is retired
    sample_sessions: int  # length of the series that was resampled


def bootstrap_max_drawdowns(returns, *, horizon: int | None = None, runs: int = 5000, mean_block: float = 20.0,
                            seed: int = 0, chunk_cells: int = 2_000_000) -> np.ndarray:
    """Maximum drawdown of each of `runs` stationary-bootstrap paths of `horizon` sessions (default: the length
    of `returns`). Seeded, so the same inputs give the same numbers."""
    r = _arr(returns)
    if r.size < 2:
        raise ValueError("need at least 2 daily returns to resample")
    horizon = r.size if horizon is None else int(horizon)
    rng = np.random.default_rng(seed)
    out = np.empty(runs)
    step = max(1, chunk_cells // horizon)
    for a in range(0, runs, step):  # in chunks: 5,000 ten-year paths at once would need hundreds of MB
        b = min(a + step, runs)
        out[a:b] = path_max_drawdown(r[stationary_indices(r.size, horizon, b - a, mean_block, rng)])
    return out


def drawdown_bands(returns, *, horizon: int | None = None, runs: int = 5000, mean_block: float = 20.0,
                   seed: int = 0) -> DrawdownBands:
    """The 95% and 99% bands of the maximum drawdown over `horizon` sessions, from a stationary bootstrap of the
    test's daily returns. Blocks keep runs of bad days together, which is what makes drawdowns deep; resampling
    single days would understate them. For Gate C `horizon` is the number of live sessions so far, so a short
    live record is compared with what a stretch of the same length could look like in the test."""
    dd = bootstrap_max_drawdowns(returns, horizon=horizon, runs=runs, mean_block=mean_block, seed=seed)
    n = int(np.asarray(returns).size)
    return DrawdownBands(n if horizon is None else int(horizon), runs, float(mean_block), seed,
                         float(np.quantile(dd, 0.5)), float(np.quantile(dd, 0.95)), float(np.quantile(dd, 0.99)), n)


# ---------------------------------------------------------------- the table


def curve(returns, *, periods: int = PERIODS, haircut: float = 0.5, warn_above: float = 1.0,
          lags: int | None = None) -> dict:
    """Return, CAGR, Sharpe (with its error, t and haircut) and mark-to-market drawdown of one daily series."""
    r = _arr(returns)
    return {"sessions": int(r.size), "total_return": total_return(r), "cagr": cagr(r, periods),
            "max_drawdown": max_drawdown(r),
            "sharpe": asdict(sharpe_hac(r, periods=periods, lags=lags, haircut=haircut, warn_above=warn_above))}


def cost_table(net, costs, multiples: Sequence[float] = (1.0, 2.0, 3.0), **kw) -> dict:
    """The curve at each cost multiple and the break-even multiple. Keys are the multiples as "1x", "2x", ..."""
    out = {f"{m:g}x": curve(at_cost_multiple(net, costs, m), **kw) for m in multiples}
    out["breakeven_multiple"] = breakeven_cost_multiple(net, costs)
    return out
