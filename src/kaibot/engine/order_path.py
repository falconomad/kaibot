"""The engine's one order path to the venue (M10 1.20, #445, step 1).

Every call of the engine that places, changes or cancels an order at the broker goes through this module, so a
guarantee about sending (the lease fence, an idempotency key, a trading state) can be written once, here. Step 1 is
only the seam: each function passes its call through unchanged, and `tests/unit/test_order_path.py` fails on any
broker write outside this file. Not here: the broker adapters themselves (`kaibot.broker`), the fence that wraps
them (`engine/fence.py`), and the watchdog (`engine/watchdog.py`, its own process and its own fail-safe path).

Next steps (each behaviour-neutral on its own, see the PR for #445): the retired intraday path's and the flatten's
direct sends move onto the order manager's journal; then one start-up recovery routine.
"""

from __future__ import annotations

from kaibot.broker.protocol import OrderRequest


def submit(broker, order: OrderRequest) -> dict:
    return broker.submit(order)


def submit_bracket(broker, coid: str, symbol: str, qty: int, stop: float, target: float) -> dict:
    return broker.submit_bracket(coid, symbol, qty, stop, target)


def submit_oto_stop(broker, coid: str, symbol: str, qty: int, stop: float) -> dict:
    return broker.submit_oto_stop(coid, symbol, qty, stop)


def submit_moc_sell(broker, coid: str, symbol: str, qty: int) -> dict:
    return broker.submit_moc_sell(coid, symbol, qty)


def close_position(broker, symbol: str):
    return broker.close_position(symbol)


def replace(broker, order_id: str, **kw) -> dict:
    return broker.replace(order_id, **kw)


def replace_stop(broker, order_id: str, stop: float) -> dict:
    return broker.replace_stop(order_id, stop)


def replace_limit(broker, order_id: str, limit: float) -> dict:
    return broker.replace_limit(order_id, limit)


def cancel(broker, order_id: str) -> None:
    return broker.cancel(order_id)


def cancel_order(broker, order_id: str) -> None:
    return broker.cancel_order(order_id)
