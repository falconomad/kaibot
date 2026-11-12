"""Protective stops for multi-day sleeves (S78, issue #118; decision 15 sections 3 and 6): one standalone GTC stop
sell per (strategy, symbol), in whole shares, resting at the broker so a position is protected while the engine is
off. Sent through the order manager like every other order.

- `ensure` keeps exactly one working stop per sleeve at the sleeve's whole shares and stop price: it creates a
  missing one (a new position, a partial fill: protected at the quantity filled), resizes or re-prices one that
  differs (replace), cancels stops whose sleeve is gone, and re-arms a stop within `REARM_DAYS` of Alpaca's
  90-day GTC expiry (cancel, then a new stop once the cancel is confirmed);
- `release(key, keep)` frees shares for a sale: the stop shrinks to what is kept, or is cancelled. A sale is sent
  only once the venue confirmed it (a working stop holds the shares: Alpaca refuses the sale otherwise);
- splits: `before_split` cancels the symbol's stops before the ex-date (bracket legs and stops are not adjusted
  reliably: DNR/DNC), `after_split` lets `ensure` re-create them at the new share count and the scaled price.

A fraction of a share (never held in v3.0: whole shares only) is not covered.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta

from kaibot.broker.protocol import OrderRequest
from kaibot.engine.orders import TERMINAL, UNCONFIRMED, OrderManager, St

REARM_DAYS = 5  # re-arm a GTC stop this many days before its expiry
Key = tuple[str, str]


class ProtectiveStops:
    def __init__(self, om: OrderManager, run="0") -> None:
        self.om, self.run = om, run
        self.suspended: set[str] = set()  # symbols with a split under way: no stop is re-created yet

    def stops_of(self, key: Key):
        return self.om.working(symbol=key[1], strategy=key[0], purposes=("stop",))

    def ensure(self, sleeves: Mapping[Key, float], prices: Mapping[Key, float], now: datetime,
               session: str) -> list[str]:
        """`sleeves`: shares per (strategy, symbol); `prices`: the stop price of each. Returns what it did."""
        done: list[str] = []
        for key in sorted(set(sleeves) | {(r.strategy, r.symbol) for r in self.om.working(purposes=("stop",))}):
            strategy, symbol = key
            if symbol in self.suspended:
                continue
            whole = int(math.floor(float(sleeves.get(key, 0.0)) + 1e-9))
            px = prices.get(key)
            recs = self.stops_of(key)
            if whole < 1 or px is None or not px > 0:
                for r in recs:
                    self.om.cancel(r.coid)
                    done.append(f"cancel {r.coid}")
                continue
            for r in recs[1:]:  # one stop per sleeve
                self.om.cancel(r.coid)
                done.append(f"cancel {r.coid}")
            r = recs[0] if recs else None
            if r is not None and (r.state in UNCONFIRMED or r.state in (St.PENDING_CANCEL, St.PENDING_REPLACE)):
                continue  # wait for the venue
            if r is not None and _expiring(r.expires_at, now):
                self.om.cancel(r.coid)
                done.append(f"rearm {r.coid}")
                if r.state not in TERMINAL:
                    continue  # the new stop goes out once the cancel is confirmed
                r = None
            if r is None:
                coid = self.om.next_coid(self.run, strategy, symbol, session, "stop")
                self.om.submit(OrderRequest(symbol, "sell", whole, "stop", "gtc", coid, stop_price=round(px, 2)),
                               strategy=strategy, purpose="stop", session=session, override_block=True)
                done.append(f"stop {coid}")
            elif int(r.qty) != whole or abs(float(r.stop or 0) - round(px, 2)) > 0.004:
                self.om.replace(r.coid, stop=round(px, 2), qty=whole if int(r.qty) != whole else None)
                done.append(f"replace {r.coid}")
        return done

    def release(self, key: Key, keep: float) -> bool:
        """Before selling down to `keep` shares: True once no stop holds the shares to be sold."""
        whole = int(math.floor(float(keep) + 1e-9))
        ok = True
        for r in self.stops_of(key):
            if r.state in (St.PENDING_CANCEL, St.PENDING_REPLACE) or r.state in UNCONFIRMED:
                ok = False
            elif whole < 1:
                self.om.cancel(r.coid)
                ok = ok and not r.open
            elif int(r.qty) > whole:
                self.om.replace(r.coid, qty=whole)
                ok = ok and int(r.qty) <= whole and r.state is not St.PENDING_REPLACE
        return ok

    def before_split(self, symbol: str) -> None:
        self.suspended.add(symbol)
        for r in self.om.working(symbol=symbol, purposes=("stop",)):
            self.om.cancel(r.coid)

    def after_split(self, symbol: str) -> None:
        """The ledger has booked the split: `ensure` re-creates the stops at the new share count and the caller's
        scaled stop price."""
        self.suspended.discard(symbol)


def _expiring(expires_at: str | None, now: datetime) -> bool:
    if not expires_at:
        return False
    return datetime.fromisoformat(expires_at) - now <= timedelta(days=REARM_DAYS)
