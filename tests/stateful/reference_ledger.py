"""The reference ledger of the stateful tests (M12 ticket 3.1, #463): the simplest book that could be right.

About a hundred lines, exact decimal arithmetic, average cost per (strategy, symbol), no optimisation and no shared
code with `kaibot.engine.ledger`: the state machine feeds it the venue's executions and asserts the engine's ledger
agrees to the cent. It knows nothing about journals, orders or restarts; an execution id it has seen is ignored, so
feeding it the same execution twice is harmless (exactly-once is its definition, not something it has to achieve).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
Key = tuple[str, str]  # (strategy, symbol)


def dec(x) -> Decimal:
    """A float, int or text as the exact decimal of its shortest text form (0.1 is 0.1, not 0.1000000000000000055)."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def cents(x) -> Decimal:
    return dec(x).quantize(CENT)


def same_money(a, b, tolerance: Decimal = Decimal("0.005")) -> bool:
    """Two dollar amounts agree "to the cent": the engine keeps floats, so half a cent of rounding is allowed."""
    return abs(dec(a) - dec(b)) <= tolerance


@dataclass
class Holding:
    qty: Decimal = ZERO
    cost: Decimal = ZERO  # what the shares held cost, buy fees included


@dataclass
class ReferenceLedger:
    start_cash: Decimal
    cash: Decimal = ZERO
    realized: Decimal = ZERO
    fees: Decimal = ZERO
    holdings: dict[Key, Holding] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    oversold: list[str] = field(default_factory=list)  # executions that sold more than their sleeve held

    def __post_init__(self) -> None:
        self.start_cash = dec(self.start_cash)
        self.cash = self.start_cash

    def execute(self, exec_id: str, strategy: str, symbol: str, side: str, qty, price, fees=0) -> bool:
        """Book one venue execution. False when this execution id was booked before (nothing changes)."""
        if exec_id in self.seen:
            return False
        self.seen.add(exec_id)
        qty, price, fees = dec(qty), dec(price), dec(fees)
        self.fees += fees
        h = self.holdings.setdefault((strategy, symbol), Holding())
        if side == "buy":
            h.qty += qty
            h.cost += qty * price + fees
            self.cash -= qty * price + fees
            return True
        if qty > h.qty:
            self.oversold.append(exec_id)
        basis = h.cost * qty / h.qty if h.qty else qty * price
        proceeds = qty * price - fees
        self.cash += proceeds
        self.realized += proceeds - basis
        h.qty -= qty
        h.cost -= basis
        if h.qty <= 0:
            del self.holdings[(strategy, symbol)]
        return True

    # ---------------------------------------------------------------- reads
    def qty(self, strategy: str, symbol: str) -> Decimal:
        h = self.holdings.get((strategy, symbol))
        return h.qty if h else ZERO

    def qty_by_symbol(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for (_, symbol), h in self.holdings.items():
            out[symbol] = out.get(symbol, ZERO) + h.qty
        return out

    def invested(self, prices) -> Decimal:
        """The shares held at `prices` {symbol: price}."""
        return sum((h.qty * dec(prices[symbol]) for (_, symbol), h in self.holdings.items()), ZERO)

    def unrealized(self, prices) -> Decimal:
        return sum((h.qty * dec(prices[symbol]) - h.cost for (_, symbol), h in self.holdings.items()), ZERO)

    def equity(self, prices) -> Decimal:
        """Cash plus positions. The accounting identity: this is also start cash + realised + unrealised."""
        return self.cash + self.invested(prices)

    def identity_gap(self, prices) -> Decimal:
        """cash + positions - (start + realised + unrealised): zero, always, by construction of a correct book."""
        return self.equity(prices) - (self.start_cash + self.realized + self.unrealized(prices))
