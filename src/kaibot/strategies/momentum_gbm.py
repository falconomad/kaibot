"""`trend_momentum_gbm` (M12 ticket 3.12, issue #474): docs/prereg/trend_momentum_gbm.md with Amendment 1, frozen.

`trend_momentum` unchanged, except the ORDER of the week's new buys: instead of the regression score, a small
gradient-boosted model's prediction of each candidate's next-21-session return (minus SPY's), ranked. Which names
may be bought, kept or sold, every size, stop and exit stay the rule's (Amendment 1, item 3). The model is not an
LLM, and no LLM output enters it.

- Features (row 3), per stock and decision day, each ranked across the day's ranked universe, read from closes at
  or before the decision close only: the 21-session return; the 126- and 252-session returns skipping 21; the
  63-session volatility of daily returns; the close over its 100-session average; the close over its 252-session
  high; the rule's regression score; the 20-session average dollar volume.
- Target (row 4): the next 21 sessions' return minus SPY's, ranked across the day's stocks.
- Training rows (row 5): every weekly decision day (`momentum.is_weekly_decision`) with all features and a target
  that ends at least 21 sessions before the refit day (the embargo). Everything is read from the decision's own
  point-in-time view: a target ending 21 sessions before row k uses rows <= k - 21.
- Model (row 6): `HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_depth=3,
  min_samples_leaf=200, l2_regularization=1.0, early_stopping=False, random_state=474)`; neighbour `ridge` uses
  `Ridge(alpha=1.0)` on the same features (missing values filled with 0.5, the middle rank).
- Refit (row 7): on the first decision day of each calendar year (and of July for `refit6m`), on every training row
  then available. Until a refit has at least 52 decision days of rows, the regression score's order stands.

RESEARCH ONLY. Not in `registry.STRATEGIES` or `registry.MULTI_DAY`; scikit-learn is the `ml` extra (never in the
engine image) and is imported only inside `_fit`, so importing this module loads no model code. A test checks that
no live module imports it. The object keeps its fitted model, so every run needs a fresh one (`fresh`).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from kaibot.strategies.base import Event, Holding, Mode, TargetList, When
from kaibot.strategies.momentum import DailyContext, TrendMomentum, is_weekly_decision, regression_score

HORIZON = 21  # the target's sessions
EMBARGO = 21  # a training target ends at least this many sessions before the refit day
MIN_DECISION_DAYS = 52  # a refit with fewer decision days of rows is not used (row 7)
LOOKBACK = 252 + 1  # the longest feature (the 252-session high and return) needs this many closes
FEATURES = ("ret21", "ret126_21", "ret252_21", "vol63", "sma100_gap", "high252_gap", "regression", "adv20")
GBM_PARAMS = {"max_iter": 200, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 200,
              "l2_regularization": 1.0, "early_stopping": False, "random_state": 474}
NEIGHBOURS: dict[str, dict] = {  # the pre-registration's five, one setting each
    "depth2": {"max_depth": 2},
    "lr10": {"learning_rate": 0.10},
    "noscore": {"use_score": False},
    "refit6m": {"refit_months": (1, 7)},
    "ridge": {"model": "ridge"},
}


def _rank(x: np.ndarray) -> np.ndarray:
    """Cross-sectional rank in (0, 1], NaN kept."""
    return pd.Series(x).rank(pct=True).to_numpy()


def features_at(c: np.ndarray, adv: np.ndarray, k: int, cols: np.ndarray, reg_window: int = 90,
                use_score: bool = True) -> np.ndarray:
    """(len(cols) x features) at decision row k from closes `c` rows <= k (and `adv` row k), each ranked across
    `cols`. NaN where a stock lacks the history."""
    n = len(cols)
    if k - LOOKBACK + 1 < 0 or n == 0:
        return np.full((n, len(FEATURES) - (0 if use_score else 1)), np.nan)
    w = c[k - LOOKBACK + 1:k + 1][:, cols]  # rows k-252..k
    last = w[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        ret21 = last / w[-1 - 21] - 1
        ret126 = w[-1 - 21] / w[-1 - 126] - 1
        ret252 = w[-1 - 21] / w[0] - 1
        r = w[-64:][1:] / w[-64:][:-1] - 1
        vol63 = np.std(r, axis=0, ddof=1)
        sma = last / w[-100:].mean(axis=0) - 1
        high = last / np.nanmax(w, axis=0) - 1
        cols_out = [ret21, ret126, ret252, vol63, sma, high]
        if use_score:
            cols_out.append(regression_score(w[-reg_window:]))
        cols_out.append(adv[k, cols].astype(float))
    return np.column_stack([_rank(x) for x in cols_out])


def target_at(c: np.ndarray, spy: int, t: int, cols: np.ndarray) -> np.ndarray:
    """The next 21 sessions' return of each stock minus SPY's, from row t, ranked across `cols`."""
    with np.errstate(invalid="ignore", divide="ignore"):
        x = (c[t + HORIZON, cols] / c[t, cols] - 1) - (c[t + HORIZON, spy] / c[t, spy] - 1)
    return _rank(x)


@dataclass
class TrendMomentumGbm:
    name: str = "trend_momentum_gbm"
    max_depth: int = 3
    learning_rate: float = 0.05
    use_score: bool = True
    refit_months: tuple[int, ...] = (1,)
    model: str = "gbm"  # gbm | ridge
    min_decision_days: int = MIN_DECISION_DAYS
    mode: Mode = Mode.SHADOW
    holding: Holding = Holding.MULTI_DAY
    schedule: tuple[When, ...] = (When(Event.AFTER_CLOSE),)
    owner_filters: bool = False
    room: bool = False
    fitted: Any = None  # the current model, or None (the regression order stands)
    refits: list[dict] = field(default_factory=list)  # one row per refit: row, decision days, rows, used
    order_days: dict[str, int] = field(default_factory=dict)  # decision days by order used (row 7's report)

    rule = "trend_momentum_gbm"

    def __post_init__(self) -> None:
        self._base = TrendMomentum()
        self._last_refit: tuple[int, int] | None = None

    # -- what the runner reads: the rule underneath, unchanged
    @property
    def market_filter(self) -> bool:
        return self._base.market_filter

    @property
    def atr_risk(self) -> float:
        return self._base.atr_risk

    @property
    def max_names(self) -> int:
        return self._base.max_names

    @property
    def warmup(self) -> int:
        return max(self._base.warmup, LOOKBACK)

    def signals(self, v):
        return self._base.signals(v)

    def decision_day(self, prev: date, fill: date) -> bool:
        return self._base.decision_day(prev, fill)

    def with_params(self, **kw) -> TrendMomentumGbm:
        return dataclasses.replace(self, fitted=None, refits=[], order_days={}, **kw)

    def fresh(self) -> TrendMomentumGbm:
        return self.with_params()

    def neighbours(self) -> list[TrendMomentumGbm]:
        return [self.with_params(name=f"{self.rule}~{n}", **kw) for n, kw in NEIGHBOURS.items()]

    # -- the model

    def _universe(self, v, t: int) -> np.ndarray:
        """Columns of the ranked universe at row t (the rule's own: the universe filter and a score)."""
        d = v.d
        c = d.sig_c[max(t - self._base.reg_window + 1, 0):t + 1]
        if c.shape[0] < self._base.reg_window:
            return np.array([], dtype=int)
        score = regression_score(c)
        return np.flatnonzero(d.ok[t] & np.isfinite(score))

    def training_rows(self, v, k: int) -> tuple[np.ndarray, np.ndarray, int]:
        """(X, y, decision days) from every weekly decision row t whose target ends at least EMBARGO sessions
        before row k (t + HORIZON <= k - EMBARGO), all read from rows <= k."""
        d = v.d
        dates = d.dates
        xs, ys, days = [], [], 0
        for t in range(LOOKBACK, k - EMBARGO - HORIZON + 1):
            if not is_weekly_decision(dates[t].astype(object), dates[t + 1].astype(object)):
                continue
            cols = self._universe(v, t)
            if cols.size < 2:
                continue
            x = features_at(d.sig_c, d.adv, t, cols, self._base.reg_window, self.use_score)
            y = target_at(d.sig_c, d.spy, t, cols)
            keep = np.isfinite(y)
            if keep.sum() < 2:
                continue
            xs.append(x[keep])
            ys.append(y[keep])
            days += 1
        if not xs:
            return np.empty((0, len(FEATURES))), np.empty(0), 0
        return np.vstack(xs), np.concatenate(ys), days

    def _fit(self, x: np.ndarray, y: np.ndarray):
        if self.model == "ridge":
            from sklearn.linear_model import Ridge

            return Ridge(alpha=1.0).fit(np.nan_to_num(x, nan=0.5), y)
        from sklearn.ensemble import HistGradientBoostingRegressor

        params = {**GBM_PARAMS, "max_depth": self.max_depth, "learning_rate": self.learning_rate}
        return HistGradientBoostingRegressor(**params).fit(x, y)

    def _predict(self, x: np.ndarray) -> np.ndarray:
        if self.model == "ridge":
            return self.fitted.predict(np.nan_to_num(x, nan=0.5))
        return self.fitted.predict(x)

    def _refit_due(self, fill: date) -> bool:
        key = (fill.year, max([m for m in self.refit_months if m <= fill.month] or [0]))
        if key[1] == 0:
            return False
        if self._last_refit != key:
            self._last_refit = key
            return True
        return False

    # -- the decision

    def on_event(self, ctx: DailyContext) -> TargetList | None:
        t = self._base.on_event(ctx)
        if t is None:
            return None
        v, k = ctx.view, ctx.view.k
        if self._refit_due(ctx.fill_session):
            x, y, days = self.training_rows(v, k)
            used = days >= self.min_decision_days
            self.refits.append({"row": int(k), "fill": str(ctx.fill_session), "decision_days": days,
                                "rows": int(len(y)), "used": used})
            if used:
                self.fitted = self._fit(x, y)
        buy = list(t.features.get("buy", []))
        keep = list(t.features.get("keep", []))
        order = "regression"
        if self.fitted is not None and len(buy) > 1:
            col = v.d._col
            cols = np.array([col[s] for s in buy])
            x = features_at(v.d.sig_c, v.d.adv, k, cols, self._base.reg_window, self.use_score)
            pred = self._predict(x)
            # best prediction first; equal predictions keep the regression score's order (row 8)
            buy = [buy[i] for i in sorted(range(len(buy)), key=lambda i: (-float(pred[i])
                                                                          if math.isfinite(pred[i]) else math.inf, i))]
            order = "model"
        self.order_days[order] = self.order_days.get(order, 0) + 1
        weights = {s: 1.0 for s in [*keep, *buy]}
        return dataclasses.replace(t, strategy=self.name, weights=weights,
                                   features={**t.features, "buy": buy, "order": order,
                                             "refits": len(self.refits)})

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None
