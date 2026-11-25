"""The engine's one order path to the venue (M10 1.20, #445, step 1).

Every call of the engine that places, changes or cancels an order at the broker goes through this module, so a
guarantee about sending (the lease fence, an idempotency key, a trading state) can be written once, here. Step 1 is
only the seam: each function passes its call through unchanged, and `tests/unit/test_order_path.py` fails on any
broker write outside this file. Not here: the broker adapters themselves (`kaibot.broker`), the fence that wraps
them (`engine/fence.py`), and the watchdog (`engine/watchdog.py`, its own process and its own fail-safe path).

Next steps (each behaviour-neutral on its own, see the PR for #445): the retired intraday path's and the flatten's
direct sends move onto the order manager's journal; then one start-up recovery routine.

M10 1.21 step 3 (#446): the trading state's gate. The engine attaches its `trading_state` to its broker (`attach`);
every call here except a cancel asks it first (`engine/state.allows`): in `reducing_only` no buy leaves (sales,
replaces and cancels do), in `halted` only cancels. A refused call raises `NotAllowed`, a BrokerError with status
403 like the lease's `Fenced`, so every caller already treats it as "refused, there is no order" (the order manager
marks it rejected and never looks for it at the venue). A state that cannot be read counts as `reducing_only`. A
broker with no engine attached (replays, tools, the watchdog's own client) is not gated. The engine's own checks
(`entry_block`, `_may_enter`) are asked before any buy is built, so the gate only refuses what slipped past them:
the issue's property ("no buy leaves in reducing, only cancels in halted") is now a guarantee, not only a test.
"""

from __future__ import annotations

import logging

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.state import TradingState, allows

log = logging.getLogger("kaibot.engine")

_GATE = "_kb_trading_gate"  # the key in the broker object's own __dict__ (never a broker attribute lookup)


class NotAllowed(BrokerError):
    """The trading state refused the call: nothing was sent to the venue."""

    def __init__(self, state: str, why: str | None) -> None:
        msg = f"trading {state}: {why or 'no reason given'}"
        super().__init__(403, msg, msg)


def attach(broker, state_fn) -> None:
    """The engine's trading state (`() -> Trading`), asked before every order call made through this module on this
    broker. A later engine on the same broker (a restart in one process) replaces it."""
    try:
        vars(broker)[_GATE] = state_fn
    except TypeError:  # an object without __dict__: no gate (as before)
        log.warning("order path: broker %s takes no trading-state gate", type(broker).__name__)


def _attached(broker):
    try:
        return vars(broker).get(_GATE)
    except TypeError:
        return None


def _gate(broker, action: str, side: str | None, what: str) -> None:
    fn = _attached(broker)
    if fn is None:
        return
    try:
        t = fn()
        state, why = TradingState(t.state), t.why
    except Exception as e:  # fail closed for what adds risk only: sales, replaces and cancels still go
        state, why = TradingState.REDUCING_ONLY, f"the trading state could not be read ({type(e).__name__})"
    if not allows(state, action, side):
        log.error("order not sent (%s): trading is %s (%s)", what, state.value, why)
        raise NotAllowed(state.value, why)


def submit(broker, order: OrderRequest) -> dict:
    if _attached(broker) is not None:  # a side that cannot be read is no sale: refused unless active
        side = getattr(order, "side", None)
        _gate(broker, "submit", side, f"{side} {getattr(order, 'symbol', '?')}")
    return broker.submit(order)


def submit_bracket(broker, coid: str, symbol: str, qty: int, stop: float, target: float) -> dict:
    _gate(broker, "submit", "buy", f"bracket buy {symbol}")
    return broker.submit_bracket(coid, symbol, qty, stop, target)


def submit_oto_stop(broker, coid: str, symbol: str, qty: int, stop: float) -> dict:
    _gate(broker, "submit", "buy", f"buy {symbol} with its stop")
    return broker.submit_oto_stop(coid, symbol, qty, stop)


def submit_moc_sell(broker, coid: str, symbol: str, qty: int) -> dict:
    _gate(broker, "submit", "sell", f"market-on-close sell {symbol}")
    return broker.submit_moc_sell(coid, symbol, qty)


def close_position(broker, symbol: str):
    _gate(broker, "submit", "sell", f"close {symbol}")
    return broker.close_position(symbol)


def replace(broker, order_id: str, **kw) -> dict:
    _gate(broker, "replace", None, f"replace {order_id}")
    return broker.replace(order_id, **kw)


def replace_stop(broker, order_id: str, stop: float) -> dict:
    _gate(broker, "replace", None, f"replace stop {order_id}")
    return broker.replace_stop(order_id, stop)


def replace_limit(broker, order_id: str, limit: float) -> dict:
    _gate(broker, "replace", None, f"replace limit {order_id}")
    return broker.replace_limit(order_id, limit)


def cancel(broker, order_id: str) -> None:
    return broker.cancel(order_id)


def cancel_order(broker, order_id: str) -> None:
    return broker.cancel_order(order_id)
