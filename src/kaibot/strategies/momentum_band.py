"""`trend_momentum_band` (M11 ticket 2.9, issue #460): docs/prereg/trend_momentum_band.md with Amendment 1, frozen.

`trend_momentum`'s signal (the 90-session regression slope times R-squared; not buyable below the 100-day average or
after a 15% day; buys only while SPY is above its 200-day average), with two changes the audit names: entry only in
the top 10%, exit only out of the top 20% (Novy-Marx and Velikov's buy-hold spread), and the book split into n
tranches (4) that each decide once every four weeks, in their own weekly slot (`week_number % 4 == t x 4 / n`).

Each tranche keeps the list of stocks it chose (in rank order) from one of its decisions to the next. On a
tranche's decision day it keeps its stocks still in the top 20% and buyable, and fills up to `ceil(30 / n)` stocks
from the top 10% (while the market filter is on). The book's target is one weight per stock, the number of
tranches holding it over n; the portfolio builder (`engine/daily.momentum_builder`: k = 0.1%, 15% per name, 30
names, 40% sector, 3% reserve) sizes the whole book on every tranche decision day (Amendment 1, item 2).

Research only: not in `registry.MULTI_DAY` or `registry.STRATEGIES`, so nothing live can switch it on. The object
holds the tranches' lists, so every run needs a fresh one (`with_params`, `fresh`).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from datetime import date

from kaibot.strategies.base import Event, Holding, Mode, TargetList, When
from kaibot.strategies.momentum import (
    SMA_MARKET,
    DailyContext,
    TrendMomentum,
    is_weekly_decision,
    market_on,
    ranking,
    week_number,
)

SLOTS = 4  # weekly slots of the four-week cycle (Amendment 1, item 1)
NEIGHBOURS: dict[str, dict] = {  # the pre-registration's five, one setting each
    "noband": {"enter_top": 0.20},
    "exit30": {"exit_top": 0.30},
    "onetranche": {"n_tranches": 1},
    "twotranches": {"n_tranches": 2},
    "nofilter": {"market_filter": False},
}


@dataclass
class TrendMomentumBand:
    name: str = "trend_momentum_band"
    n_tranches: int = 4
    enter_top: float = 0.10
    exit_top: float = 0.20
    market_filter: bool = True
    reg_window: int = 90
    max_names: int = 30
    atr_risk: float = 0.001
    mode: Mode = Mode.SHADOW
    holding: Holding = Holding.MULTI_DAY
    schedule: tuple[When, ...] = (When(Event.AFTER_CLOSE),)
    owner_filters: bool = False
    room: bool = False
    tranches: dict[int, list[str]] = field(default_factory=dict)  # tranche -> its stocks, best first

    rule = "trend_momentum_band"

    def __post_init__(self) -> None:
        if self.n_tranches not in (1, 2, 4):
            raise ValueError("n_tranches is 1, 2 or 4 (Amendment 1)")
        if not 0 < self.enter_top <= self.exit_top <= 1:
            raise ValueError("need 0 < enter_top <= exit_top <= 1")
        self._base = TrendMomentum(reg_window=self.reg_window, exit_top=self.exit_top,
                                   market_filter=self.market_filter)

    @property
    def warmup(self) -> int:
        return self._base.warmup

    @property
    def per_tranche(self) -> int:
        return math.ceil(self.max_names / self.n_tranches)

    def with_params(self, **kw) -> TrendMomentumBand:
        return dataclasses.replace(self, tranches={}, **kw)

    def fresh(self) -> TrendMomentumBand:
        return self.with_params()

    def neighbours(self) -> list[TrendMomentumBand]:
        return [self.with_params(name=f"{self.rule}~{n}", **kw) for n, kw in NEIGHBOURS.items()]

    def tranche_of(self, fill: date) -> int | None:
        """The tranche that decides for `fill`'s week, or None (Amendment 1, item 1)."""
        slot = week_number(fill) % SLOTS
        for t in range(self.n_tranches):
            if t * SLOTS // self.n_tranches == slot:
                return t
        return None

    def decision_day(self, prev: date, fill: date) -> bool:
        return is_weekly_decision(prev, fill) and self.tranche_of(fill) is not None

    def signals(self, v):
        return self._base.signals(v)

    def on_event(self, ctx: DailyContext) -> TargetList | None:
        if ctx.event is not Event.AFTER_CLOSE or not self.decision_day(ctx.session, ctx.fill_session):
            return None
        t = self.tranche_of(ctx.fill_session)
        v = ctx.view
        score, ok, sell_if = self.signals(v)
        order = ranking(score, ok)
        n = len(order)
        place = {int(j): k for k, j in enumerate(order.tolist())}
        keep_top = set(order[:math.ceil(self.exit_top * n)].tolist())
        col = v.d._col  # symbol -> column (a stock no longer in the data is "universe")
        dropped: dict[str, str] = {}
        keep = []
        for s in self.tranches.get(t, []):
            j = col.get(s)
            why = "universe" if j is None else (sell_if(j) or (None if j in keep_top else "rank"))
            if why is None:
                keep.append(s)
            else:
                dropped[s] = why
        buy: list[str] = []
        if not self.market_filter or market_on(v.close(SMA_MARKET)[:, v.spy]):
            for j in order[:math.ceil(self.enter_top * n)].tolist():
                if len(keep) + len(buy) >= self.per_tranche:
                    break
                s = v.symbols[j]
                if s in keep or sell_if(j) is not None:
                    continue
                buy.append(s)
        self.tranches[t] = keep + buy
        count: dict[str, int] = {}
        for names in self.tranches.values():
            for s in names:
                count[s] = count.get(s, 0) + 1
        rank = {s: place.get(col.get(s, -1), n) for s in count}
        held = [s for s in ctx.held if s in count]
        new = sorted((s for s in count if s not in ctx.held), key=lambda s: (rank[s], s))
        weights = {s: count[s] / self.n_tranches for s in [*held, *new]}
        sell = {s: "rank" for s in ctx.held if s not in count}
        return TargetList(self.name, ctx.fill_session, weights, sizing="atr_risk", resize=True,
                          reasons=tuple(f"sell {s}: {r}" for s, r in sell.items()),
                          features={"tranche": t, "tranche_names": list(self.tranches[t]), "dropped": dropped,
                                    "sell": sell, "ranked": n, "max_names": self.max_names,
                                    "atr_risk": self.atr_risk, "tranches": {k: len(x) for k, x in
                                                                            sorted(self.tranches.items())}})

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos):
        return None

    def close_out(self, ctx, pos):
        return None
