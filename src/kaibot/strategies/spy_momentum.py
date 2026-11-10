"""SPY last-30-minute momentum (Balanced and Hunter). Gao, Han, Li & Zhou (2018), JFE.

If SPY's return from the prior close to 10:00 ET is above +0.5 sigma (sigma = trailing std of that
same-window return), buy SPY at 15:30 ET and exit market-on-close. Long only, no LLM, at most one trade a day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kaibot.replay.data import SymbolDays

ENTRY_M = 359  # decide at the close of the 15:29 bar (15:30:00), fill at the 15:30 bar's open
EXIT_SUBMIT_M = 375  # 15:45: send the market-on-close sell
MOC_CUTOFF_M = 380  # Alpaca refuses new MOC orders from 15:50 ET; after that the 15:55 flatten takes over
TEN_AM_M = 29  # the 09:59 bar closes at 10:00
LOOKBACK = 60  # sessions for sigma
STOP_ATR = 3.0  # protective stop below entry (5-min ATR), held at the broker until the MOC goes in


@dataclass(frozen=True)
class MomentumInputs:
    ret_close_to_10: float  # fraction
    sigma_close_to_10: float  # trailing std (e.g. 60 sessions) of the same window
    event_blackout: bool
    already_traded_today: bool


def should_buy(x: MomentumInputs, z_threshold: float = 0.5) -> bool:
    if x.already_traded_today or x.event_blackout or x.sigma_close_to_10 <= 0:
        return False
    return x.ret_close_to_10 / x.sigma_close_to_10 > z_threshold


def inputs_from_days(sd: SymbolDays, today: int, lookback: int = LOOKBACK) -> tuple[float, float]:
    """(today's prior-close -> 10:00 return, trailing std of that same return over `lookback` earlier sessions).

    Reads only today's bars up to 10:00 and earlier sessions' closes, so it is point-in-time at 10:00 or later.
    """
    c = np.asarray(sd.c, dtype=float)
    if today < 1:
        return float("nan"), float("nan")
    ret_today = c[today, TEN_AM_M] / c[today - 1, -1] - 1
    lo = max(1, today - lookback)
    past = c[lo:today, TEN_AM_M] / c[lo - 1:today - 1, -1] - 1
    past = past[np.isfinite(past)]
    sigma = float(np.std(past, ddof=1)) if len(past) >= 20 else float("nan")
    return float(ret_today), sigma
