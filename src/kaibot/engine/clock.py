"""Session times and simulated time.

`session_minutes` (moved here from engine/market.py by M10 1.19, #444, so the multi-day book's engine reads a
session's open and length without importing the intraday universe's market): unchanged.

Simulated time (issue #115). One `SimClock` is the engine's `now`, the simulated broker's clock and the
simulated data feed's clock, so the three can never disagree. It moves only when the runner moves it and never
reads the wall clock. (The live loop in engine/__main__.py still reads `datetime.now(UTC)` itself.)"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd

ET = "America/New_York"
N_MIN = 390  # the regular session's minutes; both equal kaibot.replay.data's (a test compares them)


def session_minutes(cal: dict, day: date) -> tuple[datetime, int]:
    """(open as UTC datetime, number of regular-session minutes) from an Alpaca calendar row."""
    o = pd.Timestamp(f"{day} {cal['open']}", tz=ET)
    c = pd.Timestamp(f"{day} {cal['close']}", tz=ET)
    return o.tz_convert(UTC).to_pydatetime(), min(int((c - o).total_seconds() // 60), N_MIN)


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
