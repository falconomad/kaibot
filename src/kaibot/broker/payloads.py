"""#545: the broker's answers that move money in the book, checked before anything is booked from them.

Alpaca documents a FILL activity's `side` as `buy` / `sell` / `sell_short` and its numbers as decimal strings, an
order's `filled_qty` as a decimal string between 0 and its `qty`, and a dividend's `net_amount` as a decimal string.
Nothing else is expected from a healthy venue, but nothing else is booked either: a payload that fails a check is
never guessed at. The checks live here, in one place, and are applied

- at the adapter boundary (`broker/alpaca.AlpacaBroker`) and in the simulator (`broker/sim.SimBroker`) alike, so a
  test cannot pass a payload the live path would refuse: a FILL activity or an order view that fails is passed on
  marked `INVALID` (with the reason), never dropped (the engine must see that something happened);
- again where the engine books (`engine/orders.OrderManager`, `engine/ledger`), whatever venue it was given: a fill
  that fails is kept as a fill the engine cannot attribute (`OrderManager.foreign`, so the reconciliation's
  `foreign_fill` difference blocks buys in that symbol), an order view that fails leaves the order as it was (an
  order not confirmed yet stays `unknown`, which blocks its symbol), a dividend that fails is not booked.

`sell_short` is a valid Alpaca side, but this engine never sells short: such a fill is refused (fail closed).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime

INVALID = "_invalid"  # the key a refused payload carries, with the reason
FILL_SIDES = frozenset({"buy", "sell"})  # booked; Alpaca's `sell_short` is refused (the engine never shorts)


class PayloadError(ValueError):
    """A broker payload that is not booked; the message says which field and why."""


def number(v: object, what: str, *, positive: bool = False, nonneg: bool = False) -> float:
    """A finite number from a decimal string or a number (not a bool); `positive`: > 0, `nonneg`: >= 0."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise PayloadError(f"{what} is not a number: {v!r}"[:200])
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise PayloadError(f"{what} is not a number: {v!r}"[:200]) from None
    if not math.isfinite(x):
        raise PayloadError(f"{what} is not finite: {v!r}"[:200])
    if positive and x <= 0:
        raise PayloadError(f"{what} is not above zero: {v!r}"[:200])
    if nonneg and x < 0:
        raise PayloadError(f"{what} is negative: {v!r}"[:200])
    return x


def timestamp(v: object, what: str) -> str:
    """An ISO-8601 time (Alpaca's `Z` accepted), returned as given."""
    if not isinstance(v, str) or not v:
        raise PayloadError(f"{what} is not a time: {v!r}"[:200])
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise PayloadError(f"{what} is not a time: {v!r}"[:200]) from None
    return v


def _text(v: object, what: str) -> str:
    if isinstance(v, bool) or not isinstance(v, (str, int)) or str(v) == "":
        raise PayloadError(f"{what} is missing or not text: {v!r}"[:200])
    return str(v)


def fill(a: Mapping) -> dict:
    """A FILL activity as the engine books it: a copy with `side` exactly in FILL_SIDES, `qty` and
    `price` finite and above zero, `fees` finite and not negative (absent: 0), `transaction_time` a time when
    present, `id`, `order_id` and `symbol` present. PayloadError otherwise."""
    if not isinstance(a, Mapping):
        raise PayloadError(f"the activity is not an object: {type(a).__name__}")
    if a.get(INVALID):
        raise PayloadError(str(a[INVALID]))
    side = a.get("side")
    # exactly as Alpaca documents it: "BUY" or " buy" is no guess at "buy"
    if not isinstance(side, str) or side not in FILL_SIDES:
        raise PayloadError(f"side is not buy or sell: {side!r}"[:200])
    out = dict(a)
    out["id"], out["order_id"] = _text(a.get("id"), "id"), _text(a.get("order_id"), "order_id")
    out["symbol"], out["side"] = _text(a.get("symbol"), "symbol"), side
    out["qty"] = number(a.get("qty"), "qty", positive=True)
    out["price"] = number(a.get("price"), "price", positive=True)
    fees = a.get("fees")
    out["fees"] = 0.0 if fees in (None, "") else number(fees, "fees", nonneg=True)
    if a.get("transaction_time") is not None:
        timestamp(a.get("transaction_time"), "transaction_time")
    return out


def order(o: Mapping, qty: float | None = None) -> Mapping:
    """An order view whose fill can be read: `filled_qty` finite and between 0 and the order's quantity (its own
    `qty` when it has one, else `qty`), a `qty` it has finite and above zero. Returned unchanged; PayloadError
    otherwise. Fields the engine does not book from are not checked here."""
    if not isinstance(o, Mapping):
        raise PayloadError(f"the order is not an object: {type(o).__name__}")
    if o.get(INVALID):
        raise PayloadError(str(o[INVALID]))
    q = number(o["qty"], "qty", positive=True) if o.get("qty") not in (None, "") else qty
    f = o.get("filled_qty")
    filled = 0.0 if f in (None, "") else number(f, "filled_qty", nonneg=True)
    if q is not None and filled > float(q) + 1e-9:
        raise PayloadError(f"filled_qty {f!r} is above the order's quantity {q:g}"[:200])
    return o


def fill_price(o: Mapping) -> float:
    """QA-Ops on #545: the average fill price of a filled order view (or bracket leg) that a path books from
    (`filled_avg_price`, a decimal string): finite and above zero, and the view not refused at the adapter.
    PayloadError otherwise (the caller books nothing and tries again at the next read)."""
    if not isinstance(o, Mapping):
        raise PayloadError(f"the order is not an object: {type(o).__name__}")
    if o.get(INVALID):
        raise PayloadError(str(o[INVALID]))
    return number(o.get("filled_avg_price"), "filled_avg_price", positive=True)


def dividend_amount(r: Mapping) -> float:
    """A dividend activity's `net_amount`: finite (withholding rows are negative)."""
    return number(r.get("net_amount"), "net_amount")


def mark(rows: Iterable, check) -> list:
    """The adapter's pass over a list of payloads: each one that fails `check` is passed on with INVALID set to the
    reason (a copy), never dropped; the others unchanged."""
    out = []
    for r in rows:
        if isinstance(r, Mapping):
            try:
                check(r)
            except PayloadError as e:
                r = {**r, INVALID: str(e)}
        out.append(r)
    return out


def mark_activities(rows: Iterable) -> list:
    """FILL rows checked with `fill`; the other activity types are left to their own decoders (engine/ledger)."""
    return mark(rows, lambda r: fill(r) if r.get("activity_type", "FILL") == "FILL" else None)


def mark_order(o):
    """One order view (`order`, `order_by_client_id`, `submit`): marked when its fill cannot be read."""
    return mark([o], order)[0] if isinstance(o, Mapping) else o


_READS = {"activities": mark_activities, "open_orders": lambda rows: mark(rows, order) if isinstance(rows, list)
          else rows, "order": mark_order, "order_by_client_id": mark_order, "submit": mark_order,
          "replace": mark_order}


def checked(cls):
    """Class decorator for a venue (the paper adapter and the simulator, the same way): the answers of its order
    and activity reads are passed through `mark_activities` / `mark_order`, so a payload the engine must not book
    reaches it marked INVALID, from either venue."""
    import functools

    for name, check in _READS.items():
        fn = cls.__dict__.get(name)
        if fn is None:
            continue

        def wrap(fn=fn, check=check):
            @functools.wraps(fn)
            def call(self, *a, **k):
                return check(fn(self, *a, **k))
            return call

        setattr(cls, name, wrap())
    return cls
