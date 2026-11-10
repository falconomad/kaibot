"""Shared types for the shadow strategies (ORB, news continuation): a same-session panel and a candidate.

A Panel holds one regular session for many symbols as (rows, 390) arrays (minute 0 = 09:30 ET). In the live engine
minutes after the last completed one are NaN; in a replay they hold the future, which the strategies never read
(each decision at minute m reads columns 0..m only; tests/unit/test_shadow_strategies.py checks this).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Panel:
    symbols: np.ndarray  # (rows,) str
    o: np.ndarray  # (rows, 390) float, NaN where unknown
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    vw: np.ndarray

    def __len__(self) -> int:
        return len(self.symbols)


@dataclass(frozen=True)
class ShadowCandidate:
    strategy: str
    symbol: str
    row: int  # row in the Panel
    m: int  # decided at the close of minute m; a fill can only happen at the open of m + 1
    entry_ref: float  # close of minute m
    stop: float
    target: float | None  # None: no profit target, exit at the 15:55 flatten
    exit_rule: str
    reasons: tuple[str, ...]
    features: dict = field(default_factory=dict)


def rolling_before(x: np.ndarray, n: int, fn: str = "mean", min_periods: int | None = None) -> np.ndarray:
    """Length len(x) + 1: index i holds fn(x[i-n:i]), i.e. the statistic known BEFORE session i (the last index is
    for the session after the last one in x, which is what the live engine needs)."""
    r = getattr(pd.Series(np.r_[x, np.nan]).shift(1).rolling(n, min_periods=min_periods or n), fn)()
    return r.to_numpy()


def rolling_before_rows(x: np.ndarray, n: int) -> np.ndarray:
    """2-D version of rolling_before(mean) for NaN-free (days, k) arrays: row i = mean of rows i-n..i-1."""
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])  # cs[i] = sum of rows < i
    out = np.full((len(x) + 1, x.shape[1]), np.nan)
    if len(x) >= n:
        out[n:] = (cs[n:] - cs[:-n]) / n
    return out


def ffill_rows(x: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs along axis 1 (vectorised)."""
    mask = np.isnan(x)
    if not mask.any():
        return x
    idx = np.where(~mask, np.arange(x.shape[1])[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    return x[np.arange(x.shape[0])[:, None], idx]  # leading NaNs stay NaN (idx 0 points at a NaN)
