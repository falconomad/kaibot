"""Cost models for `SimBroker` fills in research runs (issue #202). RESEARCH ONLY, opt-in per run.

`SimBroker` prices a fill through an object shaped like `broker.sim.ShadowCosts` (`market`, `auction`, `fees`,
optionally `auction_fill`). Its default is cost model v1 (`broker.sim.COSTS_V1`), which every other run keeps.
This module adds the two the SPY noise-band Gate B run needs (decision 15: go/no-go on cost model v2),
without touching the venue's rules or the live code (live code never imports cost model v2: test_costs_v2.py):

- `V2SimCosts`: `kaibot.broker.costs_v2.CostV2` behind the venue's interface. A market order pays the continuous
  half-spread of the symbol's 20-session ADV bucket (sessions before the fill only) plus v2's impact; a closing
  print pays the close venue's half-spread and half the impact; an opening print twice the midday half-spread
  (`opg` orders, not used by the noise band). Fees: v2's 2026 SEC and TAF on every sale. `mult` scales spread and
  impact (2x / 3x cost runs), never the fees, as scripts/momentum_core.py scales them.
- `FixedBpCosts`: the pre-registration's all-in scenarios (0.5 / 1 / 2 bp a side, the noise-band pre-registration,
  section 4): every buy pays and every sale gives up `bp`, no separate fee (the percentage includes it).

Each fill's mid and cost are recorded in `log` (side, mid, price, qty, fee, venue), so a report can split gross from
net without guessing which price a fill started from.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from kaibot.broker.costs_v2 import CostV2, FillCtx, V2Params
from kaibot.replay.data import ET

ADV_SESSIONS = 20


def daily_dollar_volume(frame: pd.DataFrame) -> pd.Series:
    """Regular-hours dollar volume per ET session (sum of bar VWAP x volume, the close where VWAP is missing)."""
    t = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(ET)
    mins = t.dt.hour * 60 + t.dt.minute - (9 * 60 + 30)
    keep = (mins >= 0) & (mins < 390)
    px = frame["vw"].where(frame["vw"].notna() & (frame["vw"] > 0), frame["c"]) if "vw" in frame else frame["c"]
    dv = (px * frame["v"])[keep]
    return dv.groupby(t[keep].dt.date).sum().sort_index()


def adv_table(frame: pd.DataFrame, sessions: int = ADV_SESSIONS) -> dict[date, float]:
    """{session: average dollar volume of the `sessions` sessions BEFORE it} (point in time; NaN until known)."""
    dv = daily_dollar_volume(frame)
    adv = dv.rolling(sessions, min_periods=sessions).mean().shift(1)
    return {d: float(v) for d, v in adv.items()}


@dataclass
class _Logged:
    log: list = field(default_factory=list)
    clock: Callable[[], datetime] | None = None  # the venue's clock (SimClock): when each fill happened

    def _note(self, side: str, mid: float, px: float, qty: float, venue: str) -> float:
        t = self.clock() if self.clock is not None else None
        self.log.append({"t": t, "side": side, "mid": float(mid), "price": float(px), "qty": float(qty),
                         "venue": venue, "fee": 0.0})
        return px


@dataclass
class V2SimCosts(_Logged):
    """Cost model v2 at the simulated venue. `adv`: {symbol: {session: ADV $}} (`adv_table`)."""

    adv: dict = field(default_factory=dict)
    mult: float = 1.0
    params: V2Params = field(default_factory=V2Params)

    @property
    def model(self) -> CostV2:
        return CostV2(self.params)

    def _price(self, side: str, ctx: FillCtx) -> float:
        f = self.model.fill(side, ctx)
        return ctx.mid + (f.price - ctx.mid) * self.mult

    def _adv(self, symbol: str | None, day: date | None) -> float:
        if symbol is None or day is None:
            return math.nan
        return float(self.adv.get(symbol, {}).get(day, math.nan))

    def market(self, side: str, price: float, qty: float, sigma_1m: float, vol_1m: float) -> float:
        """The venue passes no symbol to a market fill: this model prices runs on one symbol (the SPY run), so
        the symbol is the only one in `adv` and the session is the venue clock's ET date."""
        if len(self.adv) != 1 or self.clock is None:
            raise ValueError("V2SimCosts prices market fills of a one-symbol run: give adv for one symbol and clock")
        symbol = next(iter(self.adv))
        day = self.clock().astimezone(ZoneInfo(ET)).date()
        ctx = FillCtx(price, qty, sigma_1m, vol_1m, self._adv(symbol, day), "continuous")
        return self._note(side, price, self._price(side, ctx), qty, "continuous")

    def auction_fill(self, side: str, price: float, qty: float, symbol: str, day: date, vol: float) -> float:
        venue = "close"  # SimBroker calls this for `cls` and `opg`; the noise band sends `cls` only
        ctx = FillCtx(price, qty, 0.0, vol if math.isfinite(vol) and vol > 0 else 1.0, self._adv(symbol, day), venue)
        return self._note(side, price, self._price(side, ctx), qty, venue)

    def auction(self, side: str, price: float) -> float:  # pragma: no cover - auction_fill is always used
        raise NotImplementedError("V2SimCosts prices auctions through auction_fill")

    def fees(self, side: str, qty: float, price: float) -> float:
        fee = self.model.sell_fees(qty, price) if side == "sell" else 0.0
        if self.log:
            self.log[-1]["fee"] = fee
        return fee


@dataclass
class FixedBpCosts(_Logged):
    """All-in `bp` a side on every buy and sale (the prereg's 0.5 / 1 / 2 bp scenarios); 0 = gross."""

    bp: float = 0.5

    def _px(self, side: str, price: float) -> float:
        return price * (1 + self.bp / 1e4) if side == "buy" else price * (1 - self.bp / 1e4)

    def market(self, side: str, price: float, qty: float, sigma_1m: float, vol_1m: float) -> float:
        return self._note(side, price, self._px(side, price), qty, "continuous")

    def auction_fill(self, side: str, price: float, qty: float, symbol: str, day: date, vol: float) -> float:
        return self._note(side, price, self._px(side, price), qty, "close")

    def auction(self, side: str, price: float) -> float:  # pragma: no cover - auction_fill is always used
        return self._px(side, price)

    def fees(self, side: str, qty: float, price: float) -> float:
        if self.log:
            self.log[-1]["fee"] = 0.0
        return 0.0


def costs_for(scenario: str, adv: dict | None = None, clock: Callable[[], datetime] | None = None):
    """"v2", "v2x2", "v2x3" or a bp figure ("0", "0.5", "1", "2"). `clock` can also be set later (`.clock = `)."""
    if scenario.startswith("v2"):
        mult = float(scenario[3:]) if "x" in scenario else 1.0
        return V2SimCosts(adv=adv or {}, mult=mult, clock=clock)
    return FixedBpCosts(bp=float(scenario), clock=clock)

