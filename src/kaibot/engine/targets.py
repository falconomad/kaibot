"""The target reconciliation loop for multi-day sleeves (S78, issue #118; decision 15 section 3): once per decision
time, orders = target shares - (broker-confirmed position + working orders), per (strategy, symbol).

Self-healing and idempotent: a missed fill, a restart or a manual trade is corrected at the next run, and running
it twice sends nothing new, because the first run's orders are working when the second one looks. A working order
that no longer fits the target is cancelled first and the difference is sent at the next run, once the cancel is
confirmed (never two orders racing for one sleeve). An order is not sent when the order manager has an
unconfirmed order in the symbol, when reconciliation has an open difference (engine/reconcile.py), or when the
execution style refuses it (window, fractional shares): each such key is reported with the reason. A sale first
frees the shares a protective stop holds (engine/protect.py) and goes out once they are free.

The targets come from the portfolio builder (`risk/portfolio.PortfolioBuilder.build(...).targets`, which `targets_
from_plan` turns into this loop's input) or, on the research path, from the frozen sizing of the pre-registrations
(engine/daily.py). The loop itself never sizes anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from kaibot.engine.execution import ExecutionPolicy, ExecutionRefused
from kaibot.engine.orders import UNCONFIRMED, OrderManager, OrderRecord

Key = tuple[str, str]


@dataclass(frozen=True)
class Target:
    strategy: str
    symbol: str
    shares: int
    purpose: str = "target"
    meta: dict = field(default_factory=dict)

    @property
    def key(self) -> Key:
        return (self.strategy, self.symbol)


@dataclass
class LoopResult:
    sent: list[OrderRecord] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    skipped: list[tuple[Key, str]] = field(default_factory=list)
    deferred: list[Key] = field(default_factory=list)  # a sale waiting for its protective stop to free the shares


def targets_from_plan(plan, purposes: Mapping[Key, str] | None = None) -> list[Target]:
    """The portfolio builder's whole-share targets as this loop's input: sales first, then the rest, by key."""
    out = [Target(s, sym, int(q), (purposes or {}).get((s, sym), "target")) for (s, sym), q in plan.targets.items()]
    return sorted(out, key=lambda t: (plan.orders.get(t.key, 0) >= 0, t.key))


def working_shares(om: OrderManager, key: Key) -> float:
    """Shares the working (non-protective) orders of a sleeve still add (+) or take away (-)."""
    return sum((r.left if r.side == "buy" else -r.left)
               for r in om.working(symbol=key[1], strategy=key[0]) if r.purpose != "stop")


def reconcile_targets(om: OrderManager, policy: ExecutionPolicy, targets: Sequence[Target],
                      held: Mapping[Key, float], *, now: datetime, session: str, run="0", reconciler=None,
                      stops=None, quotes: Mapping[str, tuple[float, float]] | None = None,
                      sells_first: bool = True) -> LoopResult:
    """`held`: broker-confirmed shares per sleeve (the ledger after the manager booked the venue's fills).
    `quotes`: symbol -> (bid, ask) for marketable limits. `sells_first`: sales before purchases (they fund them);
    False keeps the caller's order (the research path matches the harness's order of the auction)."""
    res = LoopResult()
    todo = []
    for t in targets:
        diff = int(t.shares) - float(held.get(t.key, 0.0)) - working_shares(om, t.key)
        if abs(diff) < 1e-9:
            continue
        todo.append((t, diff))
    if sells_first:
        todo.sort(key=lambda x: x[1] > 0)  # stable: the caller's order within sales and within purchases
    for t, diff in todo:
        working = [r for r in om.working(symbol=t.symbol, strategy=t.strategy) if r.purpose != "stop"]
        if any(r.state in UNCONFIRMED for r in working):  # never cancel or add to an order nobody has confirmed
            res.skipped.append((t.key, om.blocked(t.symbol) or "unconfirmed order"))
            continue
        if working:  # the book moved under a working order: cancel, decide again next time
            for r in working:
                om.cancel(r.coid)
                res.canceled.append(r.coid)
            continue
        side = "buy" if diff > 0 else "sell"
        why = om.blocked(t.symbol) or (reconciler.allows(t.symbol, side, t.purpose) if reconciler else None)
        if why:
            res.skipped.append((t.key, why))
            continue
        if side == "sell" and stops is not None and not stops.release(t.key, float(held.get(t.key, 0.0)) + diff):
            res.deferred.append(t.key)
            continue
        coid = om.next_coid(run, t.strategy, t.symbol, session, t.purpose)
        bid, ask = (quotes or {}).get(t.symbol, (None, None))
        try:
            req = policy.request(t.symbol, side, abs(diff), now, coid, bid=bid, ask=ask)
        except ExecutionRefused as e:
            res.skipped.append((t.key, str(e)))
            continue
        res.sent.append(om.submit(req, strategy=t.strategy, purpose=t.purpose, session=session, meta=t.meta))
    return res
