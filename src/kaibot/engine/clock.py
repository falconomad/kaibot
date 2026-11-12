"""Simulated time (issue #115). One `SimClock` is the engine's `now`, the simulated broker's clock and the
simulated data feed's clock, so the three can never disagree. It moves only when the runner moves it and never
reads the wall clock. (The live loop in engine/__main__.py still reads `datetime.now(UTC)` itself.)"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SimClock:
    def __init__(self, t: datetime):
        self.t = t if t.tzinfo is not None else t.replace(tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def set(self, t: datetime) -> datetime:
        """Move to `t`. Simulated time never runs backwards."""
        if t < self.t:
            raise ValueError(f"simulated time cannot go back from {self.t.isoformat()} to {t.isoformat()}")
        self.t = t
        return t

    def advance(self, **delta) -> datetime:
        return self.set(self.t + timedelta(**delta))
