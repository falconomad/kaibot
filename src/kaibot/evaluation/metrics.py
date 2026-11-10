"""Statistics behind the go-live bar (PRD v2): >=900 trades over >=3 months, t > 3, DSR > 0,
above the 95th percentile of 1,000 random-entry runs, Sharpe above SPY, drawdown within cap.

References: Harvey, Liu & Zhu (2016) for t > 3; Bailey & Lopez de Prado (2014) deflated Sharpe ratio.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats


def t_stat(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / (r.std(ddof=1) / math.sqrt(r.size)))


def sharpe(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    sd = r.std(ddof=1) if r.size > 1 else 0.0
    return float(r.mean() / sd) if sd > 0 else 0.0


def profit_factor(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    losses = -r[r < 0].sum()
    return float(r[r > 0].sum() / losses) if losses > 0 else math.inf


def max_drawdown(equity: Sequence[float]) -> float:
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(e)
    return float(((peak - e) / peak).max()) if e.size else 0.0


def deflated_sharpe(returns: Sequence[float], n_trials: int, trial_sharpe_var: float) -> float:
    """Probability that the true Sharpe > the expected max Sharpe of `n_trials` unskilled strategies.

    `trial_sharpe_var` is the variance of the Sharpe ratios across all logged trials (per-period units).
    DSR > 0.95 is strong; the PRD bar uses DSR > 0 as reported by `dsr_z` (z-score form) below.
    """
    r = np.asarray(returns, dtype=float)
    n = r.size
    if n < 3 or n_trials < 1:
        return 0.0
    sr = sharpe(r)
    euler = 0.5772156649
    if n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = math.sqrt(trial_sharpe_var) * ((1 - euler) * stats.norm.ppf(1 - 1 / n_trials)
                                             + euler * stats.norm.ppf(1 - 1 / (n_trials * math.e)))
    skew = float(stats.skew(r))
    kurt = float(stats.kurtosis(r, fisher=False))
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr**2, 1e-12))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(n - 1) / denom))


def random_entry_percentile(strategy_total: float, random_totals: Sequence[float]) -> float:
    """Share of random-entry runs (same count, universe, sizing, exits) the strategy beat, in %."""
    rt = np.asarray(random_totals, dtype=float)
    return float((rt < strategy_total).mean() * 100) if rt.size else 0.0


def min_trades_needed(mean: float, sd: float, t_target: float = 3.0) -> int:
    """n >= (t * sd / mean)^2  (research 06: 10 bp mean, 100 bp sd -> 900 trades)."""
    return math.ceil((t_target * sd / mean) ** 2) if mean > 0 else 10**9


@dataclass(frozen=True)
class GateReport:
    trades: int
    months: float
    t: float
    dsr: float
    random_pct: float
    sharpe: float
    spy_sharpe: float
    max_dd: float
    dd_cap: float

    @property
    def checks(self) -> dict[str, bool]:
        return {
            "trades>=900": self.trades >= 900,
            "months>=3": self.months >= 3,
            "t>3": self.t > 3,
            "dsr>0.5": self.dsr > 0.5,  # probability form; equivalent to a positive deflated z
            "random_pct>=95": self.random_pct >= 95,
            "sharpe>spy": self.sharpe > self.spy_sharpe,
            "drawdown_within_cap": self.max_dd <= self.dd_cap,
        }

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    @property
    def can_only_kill(self) -> bool:
        return self.trades < 900
