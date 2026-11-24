"""One trading state for the engine (M10 1.21, #446), step 1: named and derived, not yet a switch.

`active`: anything may be sent. `reducing_only`: no order that opens or adds to a position (no buy); sales, protective
stops and cancels still go. `halted`: only cancels.

Step 1 derives the state from the blocks the engine already has (`Engine.entry_block()`: no running run, a command
selling the book, the account not clean, a reconciliation difference that stops every buy, a fail-closed halt, an
unverified lease, clock skew, the daily stop, a refused owner config). Each of them already means "reducing only":
sales and stops are never blocked. Nothing maps to `halted` yet. Behaviour is unchanged. `tests/dst/world.py` checks
the property on every order that reaches the venue (`engine/order_path.py`): no buy leaves while the state is not
`active`. Later steps: the owner's and the triggers' switch onto this state, then the gate in `order_path`.

Step 2 (#446): `TradingSwitch` is the state the triggers set explicitly. A trigger holds the engine in a state under
its own name and lets go of it; the engine's fail-closed halt (`Engine.kill`: drift, broker rejects, the LLM budget,
engine errors, the start-up checks) is now the switch's `halt` hold, set and released where `kill` was. The other
blocks are still derived (step 1). Behaviour is unchanged: `entry_block` reads the hold where it read `kill`, in the
same order, with the same words. The owner's switch (a `kb_command` kind, a migration) is the next hold.
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


_RANK = {TradingState.ACTIVE: 0, TradingState.REDUCING_ONLY: 1, TradingState.HALTED: 2}


class TradingSwitch:
    """The trading state the triggers set explicitly (#446 step 2). Each trigger holds the engine under its own
    `source` with a state and a reason, and releases it; the switch's state is the strictest hold (`active` with
    none). One hold per source: holding again replaces it."""

    def __init__(self) -> None:
        self._holds: dict[str, Trading] = {}

    def hold(self, source: str, state: TradingState, why: str) -> None:
        if state is TradingState.ACTIVE:
            raise ValueError("a hold is reducing_only or halted; release() lets go")
        self._holds[source] = Trading(state, why)

    def release(self, source: str) -> None:
        self._holds.pop(source, None)

    def held(self, source: str) -> str | None:
        """The reason `source` holds the switch with (None: it does not)."""
        h = self._holds.get(source)
        return h.why if h is not None else None

    def state(self) -> Trading:
        if not self._holds:
            return Trading(TradingState.ACTIVE)
        return max(self._holds.values(), key=lambda t: _RANK[t.state])
