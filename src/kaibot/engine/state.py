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

Step 3 (#446): the gate. `Engine.trading_state` is the strictest of the switch and the derived state, and
engine/order_path.py asks it before every order call (`allows`): the property above is now enforced at the seam,
not only checked by the simulation. The heartbeat's detail carries the state and the entry block's code
(`BLOCK_CODES`) and words on every beat (`Engine.trading_detail`).

Step 4 (#446; Product's ruling of 25 Nov: no owner "halted" switch for now). Every entry block is now a hold on the
one switch, under its own source (`ENTRY_SOURCES`, in `entry_block`'s order: the run (not running, paused), a
command selling the book, the account, the reconciliation, the fail-closed halt, the lease, clock skew, the daily
stop, a refused owner config, the daily-data check). `Engine.entry_reason` takes or lets go of each of them
(`Engine._hold_entry_blocks`) and then reads the first one held: `entry_block` comes from the switch alone. The
conditions are read where they were read (on every `entry_block`), in the same order, and stop at the first that
blocks, as before, so the daily-data check (which journals a new block) is asked exactly as often as before; the
holds below the first are let go, so the switch holds only what is known now. All of them are `reducing_only`, as
in step 1. The fail-closed halt keeps its own trigger (`Engine.kill`, step 2). The market filter is not one of them:
it holds back one rule's carried buys, not every buy, and a hold would refuse the rest (the state stays `active`;
the heartbeat reports it as in step 3).

Audit 2, 5.10: `positions` (last, so every block before it is read as before): the per-minute drift check could not
read the broker's positions (`Engine._check_drift` contains that failure instead of failing the minute). Until a read
works again no buy goes out, because the book is not proved to match the broker; sales and stops go on, and the
minute completes, so the next minute's send runs first again (engine/book_loop.py).

The first trigger of `halted` (`LEASE_LOST`): the engine lease is lost (another engine took it). Today the lease's
fence (engine/fence.py) then refuses every order call, cancels included, and the main loop stops; the hold is
taken exactly while that is so (`lease.lost`, and the engine's broker fenced by that lease), so the gate refuses
what the fence would refuse anyway and a cancel still reaches the fence. Nothing that reaches the venue changes;
the heartbeat says `halted` instead of `reducing_only`.
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


# The heartbeat's `entry_block` codes (`Engine.entry_reason`, `Engine.trading_detail`); the words stay
# `Engine.entry_block`'s. `market_filter_off` is not an engine block (the state stays `active`): a multi-day rule's
# latest send found the market filter off and held its carried buys back.
BLOCK_CODES = ("no_run", "paused", "run_not_running", "command_selling", "account_not_clean", "reconcile_difference",
               "halted", "lease_unverified", "clock_skew", "daily_stop", "config_refused", "data_check",
               "market_filter_off", "positions_unread")


# Step 4: the switch's sources of the entry blocks, in `Engine.entry_block`'s order (the first one held is the block).
# `halt` is the fail-closed halt's own hold (`Engine.kill`); `entry_block` words it "halted: <kind>".
HALT = "halt"
ENTRY_SOURCES = ("run", "command", "account", "reconcile", HALT, "lease", "clock", "daily_stop", "config",
                 "data_check", "positions")  # audit 2 5.10: the drift check could not read the broker's positions
LEASE_LOST = "lease_lost"  # the first `halted` trigger: the lease's fence already refuses every order call


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
        self._codes: dict[str, str] = {}

    def hold(self, source: str, state: TradingState, why: str, code: str | None = None) -> None:
        """`code`: the heartbeat's short code for it (`BLOCK_CODES`), when the source has more than one (step 4)."""
        if state is TradingState.ACTIVE:
            raise ValueError("a hold is reducing_only or halted; release() lets go")
        self._holds[source] = Trading(state, why)
        if code is None:
            self._codes.pop(source, None)
        else:
            self._codes[source] = code

    def release(self, source: str) -> None:
        self._holds.pop(source, None)
        self._codes.pop(source, None)

    def first(self, sources) -> tuple[str, str | None, str] | None:
        """Step 4: the first of `sources` (in their order) that holds the switch, as (source, code, why); None if none
        does."""
        for s in sources:
            h = self._holds.get(s)
            if h is not None:
                return s, self._codes.get(s), h.why or ""
        return None

    def held(self, source: str) -> str | None:
        """The reason `source` holds the switch with (None: it does not)."""
        h = self._holds.get(source)
        return h.why if h is not None else None

    def state(self) -> Trading:
        if not self._holds:
            return Trading(TradingState.ACTIVE)
        return max(self._holds.values(), key=lambda t: _RANK[t.state])
