"""The one broker interface (decision 15, section 3): `AlpacaPaper` trades the paper account, `SimBroker` stands in
for it in tests and research. The engine is typed against `Broker` and never imports a vendor SDK.

Synchronous on purpose: the engine is one single-threaded loop. Orders, positions, the account, the clock and the
calendar are plain dicts in Alpaca's shape, because that is what the engine reads and stores today.

Nine primitives carry everything (`submit`, `cancel`, `replace`, `open_orders`, `positions`, `account`,
`activities`, `clock`, `calendar`), plus three reads the engine needs (`order`, `order_by_client_id`,
`close_position`). The engine's present call sites (`submit_bracket`, `replace_stop`, ...) are one-line wrappers
defined once in `BrokerBase`, so the paper venue and the simulator receive the SAME `OrderRequest` for the same
engine call. The order manager (S78, engine/orders.py) calls the primitives directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

SIDES = ("buy", "sell")
ORDER_TYPES = ("market", "limit", "stop", "stop_limit")
TIMES_IN_FORCE = ("day", "gtc", "opg", "cls", "ioc", "fok")
ORDER_CLASSES = ("simple", "bracket", "oto", "oco")


class BrokerError(RuntimeError):
    """The venue refused or failed a request. `status` follows HTTP: 403 and 422 are rejections, 0 means no
    answer at all (the order may or may not exist at the venue: look it up by its client order id)."""

    def __init__(self, status: int, body: str, message: str | None = None):
        super().__init__(message if message is not None else f"broker {status}: {body[:300]}")
        self.status = status
        self.body = body


@dataclass(frozen=True)
class OrderRequest:
    """One order as the venue receives it. Prices are sent with two decimals, as Alpaca requires above $1."""

    symbol: str
    side: str  # buy | sell
    qty: float  # shares; a fractional quantity makes it a fractional order (day only at Alpaca)
    type: str = "market"  # market | limit | stop | stop_limit
    time_in_force: str = "day"  # day | gtc | opg (opening auction) | cls (closing auction) | ioc | fok
    client_order_id: str | None = None  # kb-<run>-<signal>-<attempt>: the idempotency key at the venue
    limit_price: float | None = None
    stop_price: float | None = None
    order_class: str = "simple"  # simple | bracket (entry + stop + target) | oto (entry + stop) | oco
    take_profit: float | None = None  # limit price of the profit leg (bracket, oco)
    stop_loss: float | None = None  # stop price of the protective leg (bracket, oto, oco)

    @property
    def fractional(self) -> bool:
        return not float(self.qty).is_integer()

    def body(self) -> dict:
        """Alpaca's order body. Key order is fixed: the request bytes are part of the golden tests."""
        b: dict = {"symbol": self.symbol, "qty": _qty(self.qty), "side": self.side, "type": self.type,
                   "time_in_force": self.time_in_force}
        if self.limit_price is not None:
            b["limit_price"] = f"{self.limit_price:.2f}"
        if self.stop_price is not None:
            b["stop_price"] = f"{self.stop_price:.2f}"
        if self.order_class != "simple":
            b["order_class"] = self.order_class
        if self.client_order_id:
            b["client_order_id"] = self.client_order_id
        if self.take_profit is not None:
            b["take_profit"] = {"limit_price": f"{self.take_profit:.2f}"}
        if self.stop_loss is not None:
            b["stop_loss"] = {"stop_price": f"{self.stop_loss:.2f}"}
        return b


def _qty(q: float) -> str:
    return str(int(q)) if float(q).is_integer() else f"{float(q):.9f}".rstrip("0")


@runtime_checkable
class Broker(Protocol):
    name: str

    # ---- orders ----
    def submit(self, order: OrderRequest) -> dict:
        """Place the order and return it. A client_order_id the venue already knows returns that order."""

    def cancel(self, order_id: str) -> None:
        """Cancel an open order or leg. An order that is already gone is not an error."""

    def replace(self, order_id: str, *, stop_price: float | None = None, limit_price: float | None = None,
                qty: float | None = None, client_order_id: str | None = None) -> dict:
        """Change the price (or the quantity) of a working order; returns the order that now works (its id may be
        new: Alpaca replaces the order, the old one turns `replaced`). `client_order_id`: the client id the order
        works under from now on (Alpaca generates a new one if none is sent; issue #197)."""

    def order(self, order_id: str) -> dict:
        """One order with its legs nested."""

    def order_by_client_id(self, coid: str) -> dict | None: ...

    def open_orders(self) -> list[dict]: ...

    # ---- book ----
    def positions(self) -> dict[str, dict]:
        """Open positions by symbol."""

    def close_position(self, symbol: str) -> dict | None:
        """Cancel the symbol's open orders and sell the position at market; None if there is nothing to sell."""

    def account(self) -> dict: ...

    def activities(self, after: datetime | None = None, types: tuple[str, ...] = ("FILL",)) -> list[dict]:
        """Account activities of these types (Alpaca's codes: FILL, DIV, SSP, ...), oldest first, optionally only
        those after a time. The ledger (S77) reads dividends and splits through it."""

    # ---- time ----
    def clock(self) -> dict:
        """{timestamp, is_open, next_open, next_close}."""

    def calendar(self, day: str) -> dict | None:
        """{date, open, close} of a session (ET wall times), or None when the market is closed that day."""

    # ---- today's call sites in Engine and Watchdog: see BrokerBase ----
    def submit_bracket(self, coid: str, symbol: str, qty: int, stop: float, target: float) -> dict: ...

    def submit_oto_stop(self, coid: str, symbol: str, qty: int, stop: float) -> dict: ...

    def submit_moc_sell(self, coid: str, symbol: str, qty: int) -> dict: ...

    def replace_stop(self, order_id: str, stop: float) -> dict: ...

    def replace_limit(self, order_id: str, limit: float) -> dict: ...

    def cancel_order(self, order_id: str) -> None: ...


BrokerAdapter = Broker  # the name CLAUDE.md and the tech design use: "never bypass BrokerAdapter"


class BrokerBase:
    """The engine's present order calls, written once over `submit`, `replace` and `cancel`."""

    def submit_bracket(self, coid: str, symbol: str, qty: int, stop: float, target: float) -> dict:
        """Market buy with a broker-held stop and take-profit.

        GTC, not DAY: day legs expire at 16:00, so if the engine is dead at the close the position would sit
        overnight with no stop. GTC legs keep protecting it until the engine closes it at the next open."""
        return self.submit(OrderRequest(symbol, "buy", int(qty), "market", "gtc", coid, order_class="bracket",
                                        take_profit=target, stop_loss=stop))

    def submit_oto_stop(self, coid: str, symbol: str, qty: int, stop: float) -> dict:
        """Market buy with a broker-held protective stop only (OTO), for exits the engine times itself (MOC)."""
        return self.submit(OrderRequest(symbol, "buy", int(qty), "market", "gtc", coid, order_class="oto",
                                        stop_loss=stop))

    def submit_moc_sell(self, coid: str, symbol: str, qty: int) -> dict:
        """Market-on-close sell (time_in_force=cls; Alpaca accepts these until 15:50 ET)."""
        return self.submit(OrderRequest(symbol, "sell", int(qty), "market", "cls", coid))

    def replace_stop(self, order_id: str, stop: float) -> dict:
        return self.replace(order_id, stop_price=stop)

    def replace_limit(self, order_id: str, limit: float) -> dict:
        return self.replace(order_id, limit_price=limit)

    def cancel_order(self, order_id: str) -> None:
        self.cancel(order_id)
