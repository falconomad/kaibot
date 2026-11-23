"""The index core as a multi-day strategy (M11 ticket 2.7, issue #458): docs/prereg/index_core.md, frozen.

`IndexCore` asks to hold the universe's market symbol (SPY) at a weight w in 0..1 of the index fund's own cap
(`BookLimits.max_index_pct`). With the volatility cap on (the judged configuration), w = min(1, 0.15 / sigma),
sigma being SPY's last 21 daily close-to-close returns' standard deviation times sqrt(252); with no estimate, w = 1.
It decides after the close of the live rule's weekly decision day (`momentum.is_weekly_decision`), or after every
close for the neighbour `daily`. It never sizes and never sends: the portfolio builder's index mode
(`risk/portfolio.py`, built by `risk/index_core.builder`) turns w into whole shares, the daily runner sends, and
`SimBroker` fills (`engine/daily.run_daily`).

Research only: not in `registry.STRATEGIES` nor in `registry.MULTI_DAY`, so no level, owner config or flag can switch
it on, and the live engine never imports this module.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from kaibot.strategies.base import Event, Holding, Mode, TargetList, When
from kaibot.strategies.momentum import DailyContext, is_weekly_decision

PERIODS = 252
NEIGHBOURS: dict[str, dict] = {  # docs/prereg/index_core.md, "Neighbours": one setting each
    "vol12": {"vol_target": 0.12},
    "vol18": {"vol_target": 0.18},
    "lookback63": {"lookback": 63},
    "daily": {"decide": "daily"},
    "nocap": {"cap": False},
}


def index_weight(closes: np.ndarray, lookback: int, target: float) -> tuple[float, float | None]:
    """(w, sigma): w = min(1, target / sigma) on the last `lookback` close-to-close returns of `closes` (the newest
    last), sigma annualised with sqrt(252). Fewer than `lookback` finite returns, or sigma not above 0: (1, None),
    no scaling (pre-registration, row 6)."""
    c = np.asarray(closes, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = c[1:] / c[:-1] - 1.0
    r = r[-lookback:]
    if r.size < lookback or not np.isfinite(r).all():
        return 1.0, None
    sigma = float(np.std(r, ddof=1)) * math.sqrt(PERIODS)
    if not sigma > 0:
        return 1.0, None
    return min(1.0, target / sigma), sigma


@dataclass(frozen=True)
class IndexCore:
    name: str = "index_core"
    vol_target: float = 0.15
    lookback: int = 21
    decide: str = "weekly"  # weekly | daily
    cap: bool = True  # False: the plain core (neighbour nocap), w = 1 always
    mode: Mode = Mode.SHADOW  # never LIVE: research only
    holding: Holding = Holding.MULTI_DAY
    schedule: tuple[When, ...] = (When(Event.AFTER_CLOSE),)
    owner_filters: bool = False
    room: bool = False
    market_filter: bool = False  # no index trend filter (the carry-over of engine/daily reads this)
    max_names: int = 1
    atr_risk: float = 0.001  # not used for the index (it is not volatility-sized); kept for the runner's checks

    rule = "index_core"

    @property
    def warmup(self) -> int:
        return self.lookback + 1

    def with_params(self, **kw) -> IndexCore:
        return dataclasses.replace(self, **kw)

    def neighbours(self) -> list[IndexCore]:
        return [self.with_params(name=f"{self.rule}~{n}", **kw) for n, kw in NEIGHBOURS.items()]

    def decision_day(self, prev: date, fill: date) -> bool:
        return True if self.decide == "daily" else is_weekly_decision(prev, fill)

    def weight_of(self, closes: np.ndarray) -> float:
        return index_weight(closes, self.lookback, self.vol_target)[0] if self.cap else 1.0

    def on_event(self, ctx: DailyContext) -> TargetList | None:
        if ctx.event is not Event.AFTER_CLOSE or not self.decision_day(ctx.session, ctx.fill_session):
            return None
        v = ctx.view
        sym = v.symbols[v.spy]
        closes = v.close(self.lookback + 1)[:, v.spy]
        w, sigma = index_weight(closes, self.lookback, self.vol_target)
        if not self.cap:
            w = 1.0
        return TargetList(self.name, ctx.fill_session, {sym: w}, sizing="index",
                          reasons=(f"index weight {w:.3f}",),
                          features={"index_weight": w, "sigma": sigma, "vol_target": self.vol_target if self.cap
                                    else None, "lookback": self.lookback})

    def signals(self, v):
        """For the runner's carry-over (engine/daily.carry_targets): the core ranks no stock, so nothing of it is
        carried (SPY is outside the stock universe, and the core's next decision re-sizes it)."""
        n = len(v.symbols)
        return np.full(n, np.nan), np.zeros(n, dtype=bool), lambda j: None

    # no stop, no exits of its own: the target list is the whole rule
    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None


# ---------------------------------------------------------------- #459: a return sleeve on the core, one book


@dataclass(frozen=True)
class OnCore:
    """The book of docs/prereg/index_core_trend_momentum.md (M11 ticket 2.8): one multi-day "strategy" whose target
    list is the sleeve's own list (its stocks, best first) plus the core's index weight, decided together on the
    sleeve's decision day on the same bars. The sleeve sees only its own stocks (`held` without the index fund); the
    portfolio builder's index mode sizes both in one plan under one account (shared cash, one cap per name), the
    sleeve's stocks under `budget_pct` of equity at its scaled k (`risk/index_core.sleeve_builder`).

    Research only, like `IndexCore`: not registered, never imported by the live engine."""

    sleeve: Any
    core: IndexCore = IndexCore()
    budget_pct: float = 10.0
    name: str = "index_core_trend_momentum"
    mode: Mode = Mode.SHADOW
    holding: Holding = Holding.MULTI_DAY
    schedule: tuple[When, ...] = (When(Event.AFTER_CLOSE),)
    owner_filters: bool = False
    room: bool = False
    max_names: int = 30

    rule = "index_core_trend_momentum"

    @property
    def market_filter(self) -> bool:  # the sleeve's (the runner's carry-over reads it)
        return bool(getattr(self.sleeve, "market_filter", False))

    @property
    def atr_risk(self) -> float:
        """The sleeve's k at its budget: one tenth of the rule's at a 10% budget (the rule was tested at 100%)."""
        return float(self.sleeve.atr_risk) * self.budget_pct / 100

    @property
    def warmup(self) -> int:
        return max(int(getattr(self.sleeve, "warmup", 0)), self.core.warmup)

    def with_params(self, **kw) -> OnCore:
        return dataclasses.replace(self, **kw)

    def on_event(self, ctx: DailyContext) -> TargetList | None:
        if ctx.event is not Event.AFTER_CLOSE or not self.sleeve.decision_day(ctx.session, ctx.fill_session):
            return None
        v = ctx.view
        ix = v.symbols[v.spy]
        own = dataclasses.replace(ctx, held={s: q for s, q in ctx.held.items() if s != ix})
        t = self.sleeve.on_event(own)
        if t is None:
            return None
        closes = v.close(self.core.lookback + 1)[:, v.spy]
        w, sigma = index_weight(closes, self.core.lookback, self.core.vol_target)
        if not self.core.cap:
            w = 1.0
        weights = {s: x for s, x in t.weights.items() if s != ix}
        weights[ix] = w  # last: the builder sizes the index after the sleeve
        return dataclasses.replace(t, strategy=self.name, weights=weights,
                                   features={**t.features, "index_weight": w, "sigma": sigma,
                                             "sleeve": self.sleeve.name, "budget_pct": self.budget_pct})

    def signals(self, v):
        return self.sleeve.signals(v)

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None
