"""The momentum core of decision 15 as engine strategies (issue #161, story S94): `trend_momentum` (rule 1, Clenow's
"Stocks on the Move") and `mom_12_1_band` (rule 2, banded 12-1 momentum, with and without the index filter).

Frozen exactly as docs/prereg/trend_momentum.md and docs/prereg/mom_12_1_band.md (with Amendments 1 and 2) say;
nothing here is tuned. The research harness (replay/, the momentum core of #127) implements the same rules; the parity
test (tests/unit/test_engine_momentum.py) proves both give the same targets, order by order.

These are multi-day strategies (`Holding.MULTI_DAY`). They run only in the engine's daily research path
(`engine/daily.py`, `python -m kaibot.engine.research --strategy trend_momentum`). They are not in
`registry.STRATEGIES`, so no level and no owner config can switch them on, and `registry.live()` and the live
`Engine` refuse them by name until #118-#120 and Gate B (decision 15 section 7).

What a strategy does here, and what it does not:
- asked once per session after the close (`Event.AFTER_CLOSE`), it reads the daily bars up to that close through
  `ctx.view` (point-in-time: row `ctx.view.k` is the last row it can see) and, on its decision day, returns a
  `TargetList`: the names to keep, the ranked names it would buy (best first), and why each held name is sold;
- it never sizes and never sends an order. Share counts come from the shared ATR sizing rule
  (`engine/daily.atr_risk_sizing`, the stand-in the portfolio builder #119 replaces), orders from the daily
  runner, fills from `SimBroker`.

Signals are on split-adjusted closes without dividends (pre-registrations, choice 1; Amendment 2: the daily store's
official close for `--universe pit`). A missing close carries the last one for at most 5 sessions.
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

# ---- frozen settings (the pre-registrations, section 2) ----
SMA_STOCK, SMA_MARKET = 100, 200  # the stock's trend average; SPY's market filter
MOVE_WINDOW, MOVE_MAX = 90, 0.15  # no 15% one-day move in the last 90 returns
ANNUAL_SESSIONS = 250  # the published annualisation of the regression slope
SKIP = 21  # a month is 21 sessions; 12-1 skips the latest month
DECISION_WEEKDAY = 2  # Wednesday (Monday = 0)
RESIZE_BUFFER = 0.10  # no resize trade within 10% of the target


@dataclass
class DailyContext:
    """What a multi-day strategy sees after the close of `session`: the daily bars up to that close (`view`), the
    session its orders fill on (`fill_session`), and its own book (`held`: symbol -> shares). Reads only."""

    event: Event
    session: date  # the close the decision reads (T)
    fill_session: date  # the session its orders fill on (T + 1)
    view: Any  # engine.daily.DailyView, rows <= view.k only
    held: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- calendar


def week_number(d: date) -> int:
    """Monday-based weeks since 1970 (prereg choice 8: resize weeks are the even ones)."""
    days = (np.datetime64(d, "D") - np.datetime64("1970-01-01", "D")).astype(int)
    return int((days + 3) // 7)


def is_weekly_decision(prev: date, fill: date) -> bool:
    """`fill` is the first session of its week that falls on Wednesday or later (Wednesday itself when it trades)."""
    late = fill.weekday() >= DECISION_WEEKDAY
    return late and not (prev.weekday() >= DECISION_WEEKDAY and week_number(prev) == week_number(fill))


def is_monthly_decision(prev: date, fill: date) -> bool:
    """`fill` is the first session of its month."""
    return (fill.year, fill.month) != (prev.year, prev.month)


# ---------------------------------------------------------------- signals at one row (reads rows <= k)


def regression_score(c: np.ndarray) -> np.ndarray:
    """Annualised slope of the least-squares line through the window's log closes (rows = sessions, oldest
    first), times its R-squared; NaN when a close is missing or the price did not move."""
    n = c.shape[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.log(c)
    w = np.lib.stride_tricks.sliding_window_view(y, n, axis=0)  # (1, symbols, n): the harness's arithmetic
    x = np.arange(n) - (n - 1) / 2
    sxx = float((x * x).sum())
    mean = w.mean(axis=-1)
    sxy = np.einsum("tsn,n->ts", w, x)
    syy = np.einsum("tsn,tsn->ts", w, w) - n * mean * mean
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        slope = sxy / sxx
        r2 = np.where(syy > 1e-12, sxy * sxy / (sxx * syy), np.nan)
        return ((np.exp(slope * ANNUAL_SESSIONS) - 1) * np.clip(r2, 0.0, 1.0))[0]


def momentum_score(c: np.ndarray, ok: np.ndarray, lookbacks: tuple[int, ...]) -> np.ndarray:
    """Average cross-sectional percentile rank of the returns from each lookback to 21 sessions ago, among the
    names that pass the universe filter and have every return (`c`: the last max(lookbacks) + 1 closes)."""
    last = c.shape[0] - 1
    rets = [c[last - SKIP] / c[last - n] - 1 for n in lookbacks]
    have = ok & np.all([np.isfinite(r) for r in rets], axis=0)
    ranks = [pd.DataFrame(np.where(have, r, np.nan)[None, :]).rank(axis=1, pct=True).to_numpy()[0] for r in rets]
    return np.mean(ranks, axis=0)


def below_average(c: np.ndarray) -> np.ndarray:
    """The last close is below its 100-session simple average (True also when there are not 100 closes)."""
    w = c[-SMA_STOCK:]
    avg = w.mean(axis=0) if w.shape[0] == SMA_STOCK else np.full(c.shape[1], np.nan)
    with np.errstate(invalid="ignore"):
        return ~(c[-1] >= avg)


def big_move(c: np.ndarray) -> np.ndarray:
    """A one-day close-to-close move above 15% (absolute) among the last 90 returns (`c`: the last 91 closes)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.abs(c[1:] / c[:-1] - 1)[-MOVE_WINDOW:]
        m = np.where(np.isfinite(r).any(axis=0), np.nanmax(np.where(np.isfinite(r), r, -np.inf), axis=0), np.nan)
        return m > MOVE_MAX


def market_on(spy: np.ndarray) -> bool:
    """SPY's last close above its 200-session simple average; a missing average is "off" (choice 12 / 10)."""
    w = spy[-SMA_MARKET:]
    if w.shape[0] < SMA_MARKET or not np.isfinite(w).all():
        return False
    return bool(spy[-1] > w.mean())


def ranking(score: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Columns of the ranked universe, best first; ties keep the column (universe file) order."""
    js = np.flatnonzero(np.isfinite(score) & ok)
    return js[np.argsort(-score[js], kind="stable")]


# ---------------------------------------------------------------- the two rules


@dataclass(frozen=True)
class _Momentum:
    """Shared shape of both rules. Fields are the frozen settings; a neighbour (prereg section 3) is
    `dataclasses.replace(rule, name=..., <setting>=...)`."""

    name: str
    max_names: int
    enter_top: float
    exit_top: float = 0.20
    market_filter: bool = True
    atr_risk: float = 0.001  # read by the sizing rule: shares = equity x atr_risk / ATR(20)
    mode: Mode = Mode.SHADOW  # never LIVE: the live engine refuses multi-day rules (#161)
    holding: Holding = Holding.MULTI_DAY
    schedule: tuple[When, ...] = (When(Event.AFTER_CLOSE),)
    owner_filters: bool = False
    room: bool = False

    rule = ""  # trend_momentum | mom_12_1_band: the pre-registration this version belongs to

    def with_params(self, **kw) -> _Momentum:
        return dataclasses.replace(self, **kw)

    # -- what the engine reads

    def decision_day(self, prev: date, fill: date) -> bool:  # pragma: no cover - each rule overrides
        raise NotImplementedError

    def on_event(self, ctx: DailyContext) -> TargetList | None:
        if ctx.event is not Event.AFTER_CLOSE or not self.decision_day(ctx.session, ctx.fill_session):
            return None
        v = ctx.view
        score, ok, sell_if = self.signals(v)
        order = ranking(score, ok)
        n = len(order)
        keep_top = set(order[:math.ceil(self.exit_top * n)].tolist())
        col = v.col
        sell: dict[str, str] = {}
        for sym in ctx.held:
            j = col(sym)
            reason = sell_if(j)
            if reason is None and j not in keep_top:
                reason = "rank"  # out of the top band, or no longer in the ranked universe (choice 6 / 4)
            if reason is not None:
                sell[sym] = reason
        keep = [s for s in ctx.held if s not in sell]
        buy: list[str] = []
        if not self.market_filter or market_on(v.close(SMA_MARKET)[:, v.spy]):
            for j in order[:math.ceil(self.enter_top * n)].tolist():
                sym = v.symbols[j]
                if sym in ctx.held or sell_if(j) is not None:
                    continue  # already held (a holding being sold is not bought back the same day), or not buyable
                buy.append(sym)
        weights = {s: 1.0 for s in [*keep, *buy]}
        return TargetList(self.name, ctx.fill_session, weights, sizing="atr_risk",
                          reasons=tuple(f"sell {s}: {r}" for s, r in sell.items()),
                          features={"keep": keep, "buy": buy, "sell": sell, "ranked": n,
                                    "max_names": self.max_names, "atr_risk": self.atr_risk,
                                    "resize": self.resize_week(ctx.fill_session)})

    def resize_week(self, fill: date) -> bool:
        return False

    def signals(self, v):  # pragma: no cover - each rule overrides
        raise NotImplementedError

    # exits are part of the target list (a held name missing from it is sold); there is no stop to manage
    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None


@dataclass(frozen=True)
class TrendMomentum(_Momentum):
    """Rule 1 (docs/prereg/trend_momentum.md): weekly; rank by the annualised 90-session exponential regression
    slope x R-squared; a name below its 100-day average or with a >15% day in the window is not buyable and is
    sold; buys only while SPY is above its 200-day average; sold when out of the top 20%; resized every second
    week. At most 30 names."""

    name: str = "trend_momentum"
    max_names: int = 30
    enter_top: float = 0.20
    reg_window: int = 90
    resize: bool = True
    rule = "trend_momentum"

    @property
    def warmup(self) -> int:
        return max(self.reg_window, SMA_STOCK, MOVE_WINDOW + 1, SMA_MARKET)

    def decision_day(self, prev: date, fill: date) -> bool:
        return is_weekly_decision(prev, fill)

    def resize_week(self, fill: date) -> bool:
        return self.resize and week_number(fill) % 2 == 0

    def signals(self, v):
        c = v.close(max(self.reg_window, SMA_STOCK, MOVE_WINDOW + 1))
        score = regression_score(c[-self.reg_window:])
        below, gap = below_average(c), big_move(c[-(MOVE_WINDOW + 1):])
        ok = v.ok()

        def sell_if(j: int) -> str | None:
            return "gap" if gap[j] else "trend" if below[j] else None

        return score, ok, sell_if


@dataclass(frozen=True)
class MomBand(_Momentum):
    """Rule 2 (docs/prereg/mom_12_1_band.md): monthly; score = average percentile rank of the t-6..t-2, t-9..t-2
    and t-12..t-2 returns; enter in the top 10%, up to 20 names; exit below the top 20%; sized once at entry.
    `mom_12_1_band` with the SPY 200-day filter for new buys, `mom_12_1_band_nofilter` without it."""

    name: str = "mom_12_1_band"
    max_names: int = 20
    enter_top: float = 0.10
    lookbacks: tuple[int, ...] = (126, 189, 252)
    rule = "mom_12_1_band"

    @property
    def warmup(self) -> int:
        return max(self.lookbacks) + SKIP

    def decision_day(self, prev: date, fill: date) -> bool:
        return is_monthly_decision(prev, fill)

    def signals(self, v):
        ok = v.ok()
        score = momentum_score(v.close(max(self.lookbacks) + 1), ok, self.lookbacks)
        return score, ok, lambda j: None


VERSIONS: dict[str, _Momentum] = {
    "trend_momentum": TrendMomentum(),
    "mom_12_1_band": MomBand(),
    "mom_12_1_band_nofilter": MomBand(name="mom_12_1_band_nofilter", market_filter=False),
}
# At most five per rule, declared in the pre-registrations (section 3). A plateau check, not a menu.
NEIGHBOURS: dict[str, dict[str, dict]] = {
    "trend_momentum": {"reg60": {"reg_window": 60}, "reg125": {"reg_window": 125}, "exit30": {"exit_top": 0.30},
                       "nofilter": {"market_filter": False}, "risk15": {"atr_risk": 0.0015}},
    "mom_12_1_band": {"only12": {"lookbacks": (252,)}, "only6": {"lookbacks": (126,)}, "exit30": {"exit_top": 0.30},
                      "names10": {"max_names": 10}, "risk15": {"atr_risk": 0.0015}},
}


def neighbours(version: str) -> list[_Momentum]:
    base = VERSIONS[version]
    return [base.with_params(name=f"{version}~{n}", **kw) for n, kw in NEIGHBOURS[base.rule].items()]
