"""The target reconciliation loop for multi-day sleeves (S78, issue #118; decision 15 section 3): once per decision
time, orders = target shares - (broker-confirmed position + working orders), per (strategy, symbol).

Self-healing and idempotent: a missed fill, a restart or a manual trade is corrected at the next run, and running
it twice sends nothing new, because the first run's orders are working when the second one looks. A working order
that no longer fits the target is cancelled first and the difference is sent at the next run, once the cancel is
confirmed (never two orders racing for one sleeve). An order is not sent when the order manager has an
unconfirmed order in the symbol, when a finished order's fill is not booked yet (#333: its shares are neither
held nor working), when reconciliation refuses it (engine/reconcile.py: an open difference refuses
buys, never a sale that only reduces exposure), or when the
execution style refuses it (window, fractional shares): each such key is reported with the reason. A sale first
frees the shares a protective stop holds (engine/protect.py) and goes out once they are free.

The pre-send gate (M9 1.16, issue #441): `presend(target, side, shares)` is asked once every other check has
passed, before a sale's protective stop is released, and its reason withholds the order (`LoopResult.withheld`):
nothing is journalled or sent for it and no attempt number is used, so the next run of the loop (the next tick, or
the next session's carry-over) asks again. The live gate (execution.PreSendGate) gives a reason for buys only: a
sale is never withheld.

The targets come from the portfolio builder (`risk/portfolio.PortfolioBuilder.build(...).targets`, which `targets_
from_plan` turns into this loop's input) or, on the research path, from the frozen sizing of the pre-registrations
(engine/daily.py). The loop itself never sizes anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
    withheld: list[tuple[Key, str, str]] = field(default_factory=list)  # (key, side, why): the pre-send gate (1.16)
    aged: list[tuple[Key, str]] = field(default_factory=list)  # a sale no longer waiting for an old unbooked fill


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
                      sells_first: bool = True,
                      presend: Callable[[Target, str, float], str | None] | None = None) -> LoopResult:
    """`held`: broker-confirmed shares per sleeve (the ledger after the manager booked the venue's fills).
    `quotes`: symbol -> (bid, ask) for marketable limits. `sells_first`: sales before purchases (they fund them);
    False keeps the caller's order (the research path matches the harness's order of the auction).
    `presend`: the pre-send gate (execution.PreSendGate through the caller's quotes); None: no gate."""
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
        # #333 (QA-Ops): filled at the venue, not booked yet. #507: whatever session the finished order belongs to
        # (yesterday's fill still unbooked today is not in `held` either: the target would be bought, or sold, twice)
        if om.unbooked(t.symbol, t.strategy) > 1e-9:
            # QA-Ops second key on #524: the wait must not freeze a sleeve's SALES for good when the activity never
            # arrives. A buy always waits (and the reconciliation's "unbooked" difference blocks it anyway). A sale
            # waits while the unbooked order is of today; from the next day on it goes out for the shares the
            # ledger holds less what the unbooked sales already took at the venue, and the result says so (`aged`).
            gone = om.unbooked_sold(t.symbol, t.strategy, now) if diff < 0 else None
            if gone is not None:
                diff = min(diff + gone, 0.0)
            if gone is None or diff > -1e-9:
                res.skipped.append((t.key, "a fill the venue reported is not booked yet: waiting"))
                continue
            res.aged.append((t.key, f"a fill of an earlier day is still not booked: the sale of {abs(diff):g} "
                                    f"shares goes out without it ({gone:g} shares of unbooked sales left out)"))
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
        why = om.blocked(t.symbol) or (reconciler.allows(t.symbol, side, t.purpose, abs(diff)) if reconciler
                                       else None)  # a sale that reduces exposure is never refused
        if why:
            res.skipped.append((t.key, why))
            continue
        why = presend(t, side, abs(diff)) if presend is not None else None
        if why:  # before the stop release: a withheld sale leaves its protective stop where it is
            res.withheld.append((t.key, side, why))
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
