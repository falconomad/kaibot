"""Reconciliation of the ledger with the venue (S78, issue #118; audit D8): quantity per symbol and cash, from the
account's own records, at start-up and periodically. The engine refuses to trade on a difference it cannot explain.

`compare` is pure. What it checks:
- quantity: the ledger's shares per symbol (all sleeves) against the broker's positions;
- fills: every FILL activity is booked once to an order the manager knows (`OrderManager.foreign`), and no order
  reports more filled shares than its booked activities (`unbooked`: the activity has not arrived yet);
- cash: the broker's cash change since the run began against the ledger's, plus dividends accrued at the ex-date
  and not yet paid (receivables), within CASH_TOLERANCE (fees the ledger does not model).

`Reconciler` keeps the differences of the last check. While any is open, `allows(symbol, side)` refuses new
orders in that symbol, and a cash difference refuses every buy; protective stops and cancels are never refused
(they only take risk off). A difference heals by itself when the next check finds none (a late activity), or is
resolved by a person with `resolve(note)`, journalled.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT = 1.0, 0.05
QTY_EPS = 1e-6


@dataclass(frozen=True)
class Difference:
    kind: str  # quantity | foreign_fill | unbooked | cash
    symbol: str | None
    detail: dict

    def blocks(self, symbol: str, side: str, purpose: str) -> bool:
        if purpose in ("stop", "cancel"):
            return False
        if self.kind == "cash":
            return side == "buy"
        return self.symbol == symbol


@dataclass
class Reconciliation:
    diffs: list[Difference] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diffs

    def summary(self) -> dict:
        return {"ok": self.ok, "diffs": [{"kind": d.kind, "symbol": d.symbol, **d.detail} for d in self.diffs]}


def compare(ledger_qty: Mapping[str, float], broker_positions: Mapping[str, dict], *, om=None,
            ledger_cash: float | None = None, ledger_cash0: float | None = None, broker_cash: float | None = None,
            broker_cash0: float | None = None, receivable: float = 0.0, equity: float | None = None) -> Reconciliation:
    out = Reconciliation()
    bq = {s: float(p.get("qty") or 0) for s, p in broker_positions.items()}
    for s in sorted(set(bq) | {s for s, q in ledger_qty.items() if abs(q) > QTY_EPS}):
        mine, theirs = float(ledger_qty.get(s, 0.0)), bq.get(s, 0.0)
        if abs(mine - theirs) > QTY_EPS:
            out.diffs.append(Difference("quantity", s, {"ledger": mine, "broker": theirs}))
    if om is not None:
        for aid, a in sorted(om.foreign.items()):
            out.diffs.append(Difference("foreign_fill", a.get("symbol"), {"activity": aid,
                                                                          "order_id": a.get("order_id"),
                                                                          "qty": a.get("qty"), "side": a.get("side")}))
        for r in sorted(om.orders.values(), key=lambda r: r.coid):
            if r.cum > r.booked + QTY_EPS:
                out.diffs.append(Difference("unbooked", r.symbol, {"coid": r.coid, "venue": r.cum,
                                                                   "booked": r.booked}))
    if None not in (ledger_cash, ledger_cash0, broker_cash, broker_cash0):
        theirs = broker_cash - broker_cash0
        mine = ledger_cash - ledger_cash0 - receivable
        tol = max(CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT / 100 * abs(equity or 0.0))
        if abs(theirs - mine) > tol:
            out.diffs.append(Difference("cash", None, {"ledger_change": round(mine, 2),
                                                       "broker_change": round(theirs, 2),
                                                       "receivable": round(receivable, 2), "tolerance": tol}))
    return out


class Reconciler:
    """`check` reads the venue through `read` (a callable returning the keyword arguments of `compare`), so the
    same object serves the daily runner and the live engine. Every check and resolution is journalled."""

    def __init__(self, read: Callable[[], dict], journal=None) -> None:
        self.read, self.journal = read, journal
        self.last = Reconciliation()
        self.checked = False

    def check(self, when: str = "periodic") -> Reconciliation:
        self.last = compare(**self.read())
        self.checked = True
        if self.journal is not None and (self.last.diffs or when == "startup"):
            self.journal.append("reconcile", {"when": when, **self.last.summary()})
        return self.last

    def allows(self, symbol: str, side: str, purpose: str = "target") -> str | None:
        """None when an order may go out; else why not."""
        if not self.checked and purpose not in ("stop", "cancel"):
            return "not reconciled yet"
        for d in self.last.diffs:
            if d.blocks(symbol, side, purpose):
                return f"unresolved {d.kind} difference" + (f" in {d.symbol}" if d.symbol else "")
        return None

    def resolve(self, note: str) -> None:
        """A person looked at the differences and accepts the book as it is (written down, never silent)."""
        if self.journal is not None:
            self.journal.append("reconcile_resolved", {"note": note, **self.last.summary()})
        self.last = Reconciliation()
