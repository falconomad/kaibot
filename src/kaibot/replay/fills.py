"""Optional stricter fill assumptions for replays (issue #75). Replay-only: the live engine never imports this.

The default replay fill (replay/sim.py, `fills=None`) already decides at the close of minute m, fills at the OPEN
of minute m+1, crosses a modelled spread (half-spread >= 1 bp and >= half a cent) and adds the shadow cost model's
impact and sell fees. A `FillModel` keeps all of that and adds three things the default leaves out:

1. `require_traded_bar`: the entry fills only if the bar after the signal bar actually traded (volume > 0).
   The loader carries the last close into minutes without a print, so the default would "fill" at a stale price.
   No print on the next bar means the order is treated as not filled and the trade is skipped. It is never moved
   to a later bar.
2. `participation`: the filled quantity is capped at this share of the fill bar's volume, rounded down to whole
   shares (the shadow cost model's `max_fill_qty`, 5%). A cap of zero shares skips the trade. The rest of the
   order is dropped, not worked over later bars.
3. `slippage_bps`: extra cost per side, in basis points of the bar price the fill starts from, on every market
   fill (entry, stop, trailing stop, time exit, daily-stop flatten). Target exits are resting limit orders: they
   keep the existing rule (the bar must trade through the limit by a cent, fill at the limit) and pay no slippage.

Point-in-time: the decision (signal, stop distance, gate-approved size) uses bars up to and including the signal
bar m only. The fill reads exactly two numbers from bar m+1, its open and its volume, both known once the order is
executed there, and nothing from any later bar. There are no quotes in the history, so the spread stays modelled;
`slippage_bps` is the knob for testing how much a wider real spread would cost.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from kaibot.replay.sim import buy_px, sell_px


@dataclass(frozen=True)
class FillModel:
    name: str = "baseline"
    require_traded_bar: bool = False
    participation: float | None = None  # share of the fill bar's volume, None = no cap
    slippage_bps: float = 0.0  # extra cost per side on market fills

    def __post_init__(self) -> None:
        if self.participation is not None and not 0 < self.participation <= 1:
            raise ValueError("participation must be in (0, 1]")
        if not (math.isfinite(self.slippage_bps) and self.slippage_bps >= 0):
            raise ValueError("slippage_bps must be >= 0: a fill model may only add cost")

    def entry_qty(self, qty: float, fill_bar_volume: float) -> float:
        """Shares filled on the bar after the signal bar. Never more than the gate approved; 0 = not filled."""
        vol = float(fill_bar_volume) if math.isfinite(fill_bar_volume) else 0.0
        if qty <= 0 or (self.require_traded_bar and vol <= 0):
            return 0.0
        if self.participation is None:
            return qty
        return min(qty, float(math.floor(vol * self.participation)))

    def buy(self, open_px: float, qty: float, sigma: float, vol: float) -> float:
        return buy_px(open_px, qty, sigma, vol) + open_px * self.slippage_bps / 1e4

    def sell(self, px: float, qty: float, sigma: float, vol: float) -> float:
        return sell_px(px, qty, sigma, vol) - px * self.slippage_bps / 1e4

    def params(self) -> dict:
        return asdict(self)


BASELINE = FillModel()  # same prices and quantities as fills=None
REALISTIC = FillModel("realistic", require_traded_bar=True, participation=0.05)


def fill_model(name: str, slippage_bps: float = 0.0) -> FillModel | None:
    """CLI name -> model. 'baseline' with no slippage returns None: the unchanged default code path."""
    if name == "baseline":
        return FillModel("baseline", slippage_bps=slippage_bps) if slippage_bps else None
    if name == "realistic":
        return FillModel("realistic", True, REALISTIC.participation, slippage_bps)
    raise ValueError(f"unknown fill model: {name}")
