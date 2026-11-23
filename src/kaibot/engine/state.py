"""One trading state for the engine (M10 1.21, #446), step 1: named and derived, not yet a switch.

`active`: anything may be sent. `reducing_only`: no order that opens or adds to a position (no buy); sales, protective
stops and cancels still go. `halted`: only cancels.

Step 1 derives the state from the blocks the engine already has (`Engine.entry_block()`: no running run, a command
selling the book, the account not clean, a reconciliation difference that stops every buy, a fail-closed halt, an
unverified lease, clock skew, the daily stop, a refused owner config). Each of them already means "reducing only":
sales and stops are never blocked. Nothing maps to `halted` yet. Behaviour is unchanged. `tests/dst/world.py` checks
the property on every order that reaches the venue (`engine/order_path.py`): no buy leaves while the state is not
`active`. Later steps: the owner's and the triggers' switch onto this state, then the gate in `order_path`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TradingState(StrEnum):
    ACTIVE = "active"
    REDUCING_ONLY = "reducing_only"
    HALTED = "halted"


@dataclass(frozen=True)
class Trading:
    state: TradingState
    why: str | None = None


def from_entry_block(why: str | None) -> Trading:
    """Step 1's map: every reason the engine gives for "no new position now" is `reducing_only`."""
    return Trading(TradingState.ACTIVE) if why is None else Trading(TradingState.REDUCING_ONLY, why)


def allows(state: TradingState, action: str, side: str | None = None) -> bool:
    """May an order action leave in this state? `action`: "submit" (with `side`), "replace" or "cancel". A replace
    changes an order already working (a stop's price, a sale's limit): it neither opens nor adds, so `reducing_only`
    allows it and `halted` does not."""
    if action == "cancel" or state is TradingState.ACTIVE:
        return True
    if state is TradingState.HALTED:
        return False
    return action == "replace" or side == "sell"
