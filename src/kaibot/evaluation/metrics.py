"""Statistics behind the go-live bar (PRD v2): >=900 trades over >=3 months, t > 3, DSR > 0,
above the 95th percentile of 1,000 random-entry runs, Sharpe above SPY, drawdown within cap.

References: Harvey, Liu & Zhu (2016) for t > 3; Bailey & Lopez de Prado (2014) deflated Sharpe ratio;
Bailey, Borwein, Lopez de Prado & Zhu (2015) probability of backtest overfitting (CSCV).
"""

from __future__ import annotations

import itertools
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


@dataclass(frozen=True)
class PBOResult:
    pbo: float  # share of IS/OOS splits where the in-sample winner ranks at or below the OOS median
    logits: np.ndarray  # lambda per split; <= 0 means the IS winner underperformed OOS
    n_splits: int
    n_trials: int
    oos_degradation: float  # slope of OOS Sharpe on IS Sharpe of the chosen trial (negative = overfit)


def _block_sharpe(m: np.ndarray) -> np.ndarray:
    sd = m.std(axis=0, ddof=1)
    return np.where(sd > 0, m.mean(axis=0) / np.where(sd > 0, sd, 1.0), 0.0)


def pbo_cscv(returns: np.ndarray, n_blocks: int = 16) -> PBOResult:
    """Probability of backtest overfitting via combinatorially symmetric cross-validation.

    `returns` is (T periods x N trials), all trials on the same calendar (e.g. daily returns of every logged
    trial over one window). Rows are cut into `n_blocks` equal blocks; for each of the C(S, S/2) ways to pick
    half of them as in-sample, the trial with the best IS Sharpe is ranked on the remaining OOS half.
    PBO = P(its OOS relative rank <= 0.5). ~0.5 for pure noise, -> 0 when one trial is genuinely better.
    """
    m = np.asarray(returns, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValueError("need a (T x N) matrix with N >= 2 trials")
    if n_blocks % 2 or n_blocks < 2:
        raise ValueError("n_blocks must be even and >= 2")
    t, n = m.shape
    if t < 2 * n_blocks:
        raise ValueError(f"need >= {2 * n_blocks} periods for {n_blocks} blocks, got {t}")
    m = m[t % n_blocks:]  # equal blocks: drop the oldest remainder rows
    blocks = np.split(m, n_blocks)
    logits, is_best, oos_best = [], [], []
    for is_idx in itertools.combinations(range(n_blocks), n_blocks // 2):
        is_set = set(is_idx)
        ins = np.vstack([blocks[i] for i in is_idx])
        oos = np.vstack([blocks[i] for i in range(n_blocks) if i not in is_set])
        sr_is, sr_oos = _block_sharpe(ins), _block_sharpe(oos)
        best = int(np.argmax(sr_is))
        rank = float(stats.rankdata(sr_oos)[best])  # 1 = worst, n = best (ties averaged)
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
        is_best.append(sr_is[best])
        oos_best.append(sr_oos[best])
    lam = np.asarray(logits)
    slope = float(np.polyfit(is_best, oos_best, 1)[0]) if np.ptp(is_best) > 0 else 0.0
    return PBOResult(float((lam <= 0).mean()), lam, len(lam), n, slope)


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
