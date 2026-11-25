"""M13 ticket 4.3 (#477): the fencing token, checked by everything in the engine that can place an order.

`FencedBroker` wraps the engine's broker adapter (still a `BrokerAdapter`, the same calls). Every write (submit,
replace, cancel, close a position) first asks the lease (`EngineLease.fence`): the tenure must be proved fresh, or
it is renewed with its epoch (migration 0042) before the call. So a paused engine that resumes after another engine,
or another process of the same install, took the lease sends nothing: the renewal answers "not ours", the lease is
marked lost, the order is refused here (`Fenced`, nothing reaches the venue) and the main loop stops on the lost
lease. When the renewal cannot reach Supabase, an entry is refused and an order that only reduces goes out (the
fail-safe design for a Supabase outage, docs/ops/failsafe.md). Reads pass straight through.

`Fenced` is a BrokerError with status 403, so every caller already treats it as "refused, there is no order"
(engine/orders.py REFUSED): the order manager marks the order rejected, it never looks for it at the venue.

Audit 2, 5.8: a refusal because the renewal FAILED (`lease.NOT_PROVED`; not a lost lease) is not the venue's "no":
the order never left this process, like one called lost. `unproved` tells such a record apart, so the daily path
sends that buy again at its next tick of the same window (engine/live_daily.py `send`) under a new attempt. A
refusal for a lost lease ("no longer ours") stays a refusal: the engine stops on it.
"""

from __future__ import annotations

import logging

from kaibot.broker.protocol import BrokerBase, BrokerError, OrderRequest
from kaibot.engine.lease import NOT_PROVED, LeaseLost

log = logging.getLogger("kaibot.engine")


def unproved(reason: str | None) -> bool:
    """Audit 2, 5.8: is an order record's reason the fence's refusal for a failed renewal? (orders.py writes it
    as "403: fenced: <why>".) Such an order never reached the venue."""
    return f"fenced: {NOT_PROVED}" in str(reason or "")


class Fenced(BrokerError):
    """The lease's fence refused the call: nothing was sent to the venue."""

    def __init__(self, why: str) -> None:
        super().__init__(403, f"fenced: {why}", f"fenced: {why}")


class FencedBroker(BrokerBase):
    """The broker adapter behind the lease's fence. `lease`: set by engine/__main__.py `fence_orders` at a normal
    start, at a degraded start (reducing only until the lease is taken) and when `confirm_lease` succeeds (None: no
    fence, as for a replay or before the start-up)."""

    def __init__(self, inner, lease=None) -> None:
        self.inner, self.lease = inner, lease
        self.refused = 0  # calls the fence refused (for the tests and the log)

    def __getattr__(self, name: str):
        if name.startswith("__") or name in ("inner", "lease"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    def _fence(self, what: str, reducing) -> None:
        """`reducing`: a bool, or a callable asked only when the lease cannot be proved (Supabase unreachable)."""
        lease = self.lease
        if lease is None:
            return
        try:
            why = lease.fence(reducing)
        except LeaseLost:
            why = "the engine lease is no longer ours"
        if why is not None:
            self.refused += 1
            log.error("order not sent (%s): %s", what, why)
            raise Fenced(why)

    # ---------------------------------------------------------------- writes
    def submit(self, order: OrderRequest) -> dict:
        self._fence(f"{order.side} {order.symbol} {order.client_order_id}", reducing=order.side == "sell")
        return self.inner.submit(order)

    def replace(self, order_id: str, *, stop_price: float | None = None, limit_price: float | None = None,
                **kw) -> dict:
        # a price change (the engine's stops and sale limits) adds nothing it holds. A quantity change reduces when the
        # order is a sale: the engine's only quantity replaces resize its protective stops (protect.py), which must
        # go on in a Supabase outage (QA-Ops on #584). The side is read from the venue, and only then; a buy, or an
        # order whose side cannot be read, is fenced like an entry.
        qty = kw.get("qty")

        def reduces() -> bool:
            if qty is None:
                return True
            try:
                o = self.inner.order(order_id)
            except Exception:  # noqa: BLE001 - unknown side: not provably reducing
                return False
            return isinstance(o, dict) and o.get("side") == "sell"

        self._fence(f"replace {order_id}", reducing=reduces)
        return self.inner.replace(order_id, stop_price=stop_price, limit_price=limit_price, **kw)

    def cancel(self, order_id: str) -> None:
        self._fence(f"cancel {order_id}", reducing=True)
        return self.inner.cancel(order_id)

    def close_position(self, symbol: str):
        self._fence(f"close {symbol}", reducing=True)
        return self.inner.close_position(symbol)
