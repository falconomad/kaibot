"""Per-source request budgets so free-tier limits are never exceeded.

Every ingestion adapter must acquire from its source's bucket before calling out.
Budgets are configured in config/sources.yaml (calls per window), not hard-coded.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Classic token bucket. `capacity` tokens refill evenly over `per_seconds`."""

    capacity: int
    per_seconds: float
    _tokens: float = field(init=False)
    _updated: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.per_seconds <= 0:
            raise ValueError("capacity and per_seconds must be positive")
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()

    @property
    def rate(self) -> float:
        return self.capacity / self.per_seconds

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def try_acquire(self, n: int = 1) -> bool:
        now = time.monotonic()
        self._refill(now)
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    async def acquire(self, n: int = 1) -> None:
        async with self._lock:
            while not self.try_acquire(n):
                await asyncio.sleep(max((n - self._tokens) / self.rate, 0.01))
