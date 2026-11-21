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
- `sharpe_line`      every Sharpe is printed with its 50% haircut beside it;
- `tbill_daily`, `with_cash_yield`, `cash_views`  the Treasury-bill rate as the return on idle cash and as the
                     risk-free rate in the Sharpe and in alpha (M11 ticket 2.4): a fully idle account earns the
                     bill rate and has an excess Sharpe of zero; a fully invested one is unchanged.

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


def _rf(rf, n: int):
    """The risk-free rate as one number or as one value per session (never a series of another length)."""
    if np.isscalar(rf):
        return float(rf)
    a = _arr(rf, "risk-free rate")
    if a.size != n:
        raise ValueError(f"the risk-free rate has {a.size} values, the returns have {n}: one per session is needed")
    return a


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
    `rf` is one number or one value per session (`tbill_daily`).

    Standard error (Lo 2002, "The Statistics of Sharpe Ratios", the GMM form for returns that are not i.i.d.):
    with theta = (mu, sigma^2) estimated from the moments h_t = (r_t - mu, (r_t - mu)^2 - sigma^2),

        sqrt(T) * (theta_hat - theta) -> N(0, S),    S = long-run covariance of h_t (Newey-West, `nw_lag`),
        Var(SR_hat) = g' S g / T,                    g = (1 / sigma, -mu / (2 sigma^3))   (delta method).

    With no lags this is (1 - skew * SR + (kurt - 1) / 4 * SR^2) / T, the variance used by the deflated Sharpe;
    for normal i.i.d. returns it is (1 + SR^2 / 2) / T, so t is close to the plain mean / (sd / sqrt(T)).
    Positive autocorrelation makes S, and so the error, larger. A constant series has no Sharpe: 0 with an
    infinite error."""
    r = _arr(returns)
    r = r - _rf(rf, r.size)
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
    y, m = _arr(returns), _arr(benchmark, "benchmark returns")
    if y.size != m.size:
        raise ValueError("returns and benchmark must cover the same sessions")
    n = y.size
    y, m = y - _rf(rf, n), m - _rf(rf, n)
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


# ---------------------------------------------------------------- Treasury bills: idle cash and the risk-free rate

DAYS_DISCOUNT = 360  # a bill's quoted rate is a bank discount on a 360-day year
DAYS_YEAR = 365
BILL_DAYS = 91  # the 3-month bill (FRED series DTB3)


def discount_to_yield(discount_pct, days: int = BILL_DAYS):
    """Investment yield (a fraction a year, simple, on 365 days) of a bill quoted at a bank discount of
    `discount_pct` percent: y = 365 * d / (360 - d * days). The discount is taken off the face value, the yield
    is earned on the price paid, so the yield is the higher of the two (5.00% discount on 91 days: 5.13%)."""
    d = np.asarray(discount_pct, dtype=float) / 100.0
    return DAYS_YEAR * d / (DAYS_DISCOUNT - d * days)


def tbill_daily(rate_pct: pd.Series, sessions, *, max_stale_days: int = 10) -> pd.Series:
    """The Treasury-bill return of each session, point in time (M11 ticket 2.4).

    `rate_pct`: the 3-month bill's secondary-market rate in percent, bank discount basis, indexed by its
    observation date (FRED `DTB3`; `scripts/fetch_tbill.py` caches it). `sessions`: the sessions to price.

    Session t's return is what cash held from the close of session t-1 to the close of t earned:

        rf_t = yield(rate observed at the last date STRICTLY BEFORE t) * (calendar days from t-1 to t) / 365.

    Strictly before, for two reasons that agree: the rate of day d is published after that day's close (the
    Federal Reserve's H.15 release, which FRED copies the next business day), so it is the newest rate a
    decision before session t could know; and cash lent at the close of t-1 earns the rate of t-1, not of t.
    Calendar days, so a weekend pays three days. The first session has no previous session: it is paid one day.

    A session whose newest earlier observation is more than `max_stale_days` calendar days old is an error (a
    hole in the file must be fixed, not filled with an old rate), and so is a session before the first
    observation. Returns a Series on `sessions`."""
    idx = pd.DatetimeIndex(pd.to_datetime(sessions))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    idx = idx.normalize()
    if not idx.is_monotonic_increasing or not idx.is_unique:
        raise ValueError("sessions must be sorted and unique")
    obs = dated(rate_pct.dropna(), "Treasury-bill rate")
    if not len(obs):
        raise ValueError("the Treasury-bill series is empty")
    pos = obs.index.searchsorted(idx, side="left") - 1  # the last observation dated before the session
    if (pos < 0).any():
        raise ValueError(f"no Treasury-bill rate before {idx[pos < 0][0].date()}: the series starts "
                         f"{obs.index[0].date()}")
    age = (idx - obs.index[pos]).days
    if (age > max_stale_days).any():
        bad = idx[age > max_stale_days][0]
        raise ValueError(f"the newest Treasury-bill rate before {bad.date()} is {int(age.max())} days old "
                         f"(limit {max_stale_days}): refresh the file")
    days = np.r_[1, np.diff(idx.to_numpy()).astype("timedelta64[D]").astype(int)] if len(idx) else np.array([])
    y = discount_to_yield(obs.to_numpy()[pos])
    return pd.Series(y * days / DAYS_YEAR, index=idx, name="rf")


def idle_share(exposure) -> np.ndarray:
    """Share of equity that sat in cash going into each session: 1 - yesterday's close exposure, never below 0
    (the book is unlevered; a rounding above 100% invested is not a loan). Before the first session the book is
    flat, so the first session is fully idle (the convention of `exposure_matched`)."""
    e = _arr(exposure, "exposure")
    return np.clip(1.0 - np.r_[0.0, e[:-1]], 0.0, 1.0)


def with_cash_yield(net, exposure, rf) -> np.ndarray:
    """Daily returns when idle cash earns the Treasury-bill rate instead of 0%:

        r'_t = r_t + (1 - exposure_(t-1)) * rf_t.

    `net`: the account's daily returns as simulated (cash earns nothing). `exposure`: share of equity invested
    at each close. `rf`: `tbill_daily` on the same sessions. First order: the interest is not itself reinvested
    in positions, so later position sizes are those of the zero-cash run (about rf^2 a day; the same reading as
    `at_cost_multiple`). Alpaca pays no interest on a paper account's cash, so this is what a real cash sweep
    into bills would have added, not what the paper account shows."""
    r, e, f = _arr(net), _arr(exposure, "exposure"), _arr(rf, "risk-free rate")
    if not r.size == e.size == f.size:
        raise ValueError("returns, exposure and the risk-free rate must cover the same sessions")
    return r + idle_share(e) * f


def exposure_matched_with_cash(benchmark, exposure, rf) -> np.ndarray:
    """`exposure_matched` with the rest of the account in Treasury bills: e_(t-1) * m_t + (1 - e_(t-1)) * rf_t."""
    return with_cash_yield(exposure_matched(benchmark, exposure), exposure, rf)


def cash_views(net, benchmark, exposure, rf, *, periods: int = PERIODS, haircut: float = 0.5,
               warn_above: float = 1.0) -> dict:
    """Both versions of one account, side by side (ticket 2.4: "every report shows both versions"):

    - `zero_cash`: the account as simulated, idle cash at 0%, Sharpe against a risk-free rate of 0 (every report
      before 21 Nov 2026);
    - `tbill_cash`: idle cash earns the bill rate, and the Sharpe and the alpha are in excess of it.

    Each holds the rule's curve, the benchmark's, the alpha/beta regression and the benchmark held at the rule's
    exposure. The benchmark's own return is the same in both (SPY is fully invested); only its Sharpe changes.
    `exposure` None (no exposure series was written): the rule's `tbill_cash` curve cannot be computed and is
    None, the Sharpe of the simulated curve in excess of bills is still shown as `simulated_excess`."""
    r, m, f = _arr(net), _arr(benchmark, "benchmark returns"), _arr(rf, "risk-free rate")
    if not r.size == m.size == f.size:
        raise ValueError("returns, benchmark and the risk-free rate must cover the same sessions")
    kw = {"periods": periods, "haircut": haircut, "warn_above": warn_above}
    zero = {"rule": curve(r, **kw), "benchmark": curve(m, **kw),
            "alpha_beta": asdict(alpha_beta(r, m, periods=periods)), "exposure_matched": None}
    bill = {"rule": None, "simulated_excess": curve(r, rf=f, **kw), "benchmark": curve(m, rf=f, **kw),
            "alpha_beta": None, "exposure_matched": None}
    out = {"rf": {"mean_ann": float(np.prod(1.0 + f) ** (periods / f.size) - 1.0), "total": total_return(f),
                  "sessions": int(f.size)},
           "mean_idle_share": None, "zero_cash": zero, "tbill_cash": bill}
    if exposure is not None:
        e = _arr(exposure, "exposure")
        rc = with_cash_yield(r, e, f)
        out["mean_idle_share"] = float(idle_share(e).mean())
        zero["exposure_matched"] = curve(exposure_matched(m, e), **kw)
        bill.update(rule=curve(rc, rf=f, **kw), alpha_beta=asdict(alpha_beta(rc, m, periods=periods, rf=f)),
                    exposure_matched=curve(exposure_matched_with_cash(m, e, f), rf=f, **kw),
                    cash_added_cagr=cagr(rc, periods) - cagr(r, periods))
    return out


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
    """One row per calendar year: sessions, the calendar months with at least one session (`months`), compounded
    return, the benchmark's, the difference, the year's own maximum drawdown, and `partial` when the year has
    fewer than 240 sessions (a first or last year)."""
    net = dated(net)
    bench = align(net, benchmark)[1] if benchmark is not None else None
    out = []
    for year, r in net.groupby(net.index.year):
        row = {"year": int(year), "sessions": int(r.size), "months": int(r.index.month.nunique()),
               "return": total_return(r), "max_drawdown": max_drawdown(r),
               "partial": bool(r.size < FULL_YEAR_SESSIONS)}
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
          lags: int | None = None, rf=0.0) -> dict:
    """Return, CAGR, Sharpe (with its error, t and haircut) and mark-to-market drawdown of one daily series.
    `rf` (a number or one value per session) is taken off in the Sharpe only: return, CAGR and drawdown are
    what the account made."""
    r = _arr(returns)
    return {"sessions": int(r.size), "total_return": total_return(r), "cagr": cagr(r, periods),
            "max_drawdown": max_drawdown(r),
            "sharpe": asdict(sharpe_hac(r, periods=periods, lags=lags, haircut=haircut, warn_above=warn_above,
                                        rf=rf))}


def cost_table(net, costs, multiples: Sequence[float] = (1.0, 2.0, 3.0), **kw) -> dict:
    """The curve at each cost multiple and the break-even multiple. Keys are the multiples as "1x", "2x", ..."""
    out = {f"{m:g}x": curve(at_cost_multiple(net, costs, m), **kw) for m in multiples}
    out["breakeven_multiple"] = breakeven_cost_multiple(net, costs)
    return out
