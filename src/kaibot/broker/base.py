"""One interface for every execution venue: Kaibot ledger simulator, Alpaca paper, (later) Alpaca live.

The engine never imports a vendor SDK directly. Switching venue = config change, not code change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str  # kb-{run}-{signal}-{attempt}; idempotency key at the venue (adopt on duplicate)
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    limit_price: float | None
    stop_loss: float
    take_profit: float | None
    time_in_force: Literal["day"] = "day"


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float  # venue-reported price
    ts: datetime


class BrokerAdapter(Protocol):
    name: str

    async def submit(self, order: OrderRequest) -> str: ...
    async def cancel(self, client_order_id: str) -> None: ...
    async def positions(self) -> dict[str, float]: ...
    async def account_equity(self) -> float: ...
    def fills(self) -> AsyncIterator[Fill]: ...
