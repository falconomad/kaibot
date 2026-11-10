"""SPY last-30-minute momentum (Balanced and Hunter). Gao, Han, Li & Zhou (2018), JFE.

If SPY's return from the prior close to 10:00 ET is above +0.5 sigma (sigma = trailing std of that
same-window return), buy SPY at 15:30 ET and exit market-on-close. Long only, no LLM, at most one trade a day.
"""

from __future__ import annotations

from dataclasses import dataclass


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
