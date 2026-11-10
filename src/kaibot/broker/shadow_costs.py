"""Conservative shadow cost model (research 06). Every venue fill and every backtest fill is re-priced here.

The dashboard reports the WORSE of venue P&L and shadow P&L.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SEC_FEE_PER_DOLLAR_SOLD = 0.0000278  # placeholder: update from current SEC Section 31 rate
TAF_PER_SHARE_SOLD = 0.000166  # placeholder: update from current FINRA TAF schedule


@dataclass(frozen=True)
class QuoteCtx:
    bid: float
    ask: float
    sigma_1m: float  # 1-minute return std dev (fraction)
    vol_1m: float  # 1-minute share volume (SIP history-based baseline)


def min_spread(mid: float, spread: float) -> float:
    return max(spread, mid * 0.0001, 0.01)


def shadow_fill_price(side: str, qty: float, q: QuoteCtx) -> float:
    mid = (q.bid + q.ask) / 2
    spread = min_spread(mid, q.ask - q.bid)
    impact = 0.25 * spread + 0.5 * q.sigma_1m * mid * math.sqrt(max(qty, 0) / max(q.vol_1m, 1))
    if side == "buy":
        return mid + spread / 2 + impact
    return mid - spread / 2 - impact


def max_fill_qty(q: QuoteCtx, participation: float = 0.05) -> float:
    return math.floor(q.vol_1m * participation)


def sell_fees(qty: float, price: float) -> float:
    return qty * price * SEC_FEE_PER_DOLLAR_SOLD + qty * TAF_PER_SHARE_SOLD
