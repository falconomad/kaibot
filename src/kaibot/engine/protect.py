"""Protective stops for multi-day sleeves (S78, issue #118; decision 15 sections 3 and 6): one standalone GTC stop
sell per (strategy, symbol), in whole shares, resting at the broker so a position is protected while the engine is
off. Sent through the order manager like every other order.

- `ensure` keeps exactly one working stop per sleeve at the sleeve's whole shares and stop price: it creates a
  missing one (a new position, a partial fill: protected at the quantity filled), resizes or re-prices one that
  differs (replace), cancels stops whose sleeve is gone, and re-arms a stop within `REARM_DAYS` (10) of Alpaca's
  90-day GTC expiry, or of its age (created + 90 days) when the venue gave no `expires_at`. The new stop cannot be
  placed BEFORE the old one is cancelled (the old stop holds the shares: Alpaca refuses a second sell for them), so
  the re-arm cancels, polls the cancel to its confirmation (`confirm_polls`, `wait`) and sends the new stop in the
  same call: the gap is the cancel's round trip, seconds, and starts 10 days before the expiry, so a re-arm that
  cannot finish is tried again at every later call long before the stop would lapse. Issue #179: the old stop
  may trigger before the cancel arrives, so the new one goes out only after its final state and the venue's
  position are read again (FILLED: none; a partial fill: the shares still held), journalled as `stop_rearm`;
  and no stop is created, or resized up, for a sleeve while one of its stop fills is not yet booked (the sleeve
  is stale); a resize up is capped at the venue's position, read again;
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

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.orders import TERMINAL, UNCONFIRMED, OrderManager, St

REARM_DAYS = 10  # re-arm a GTC stop this many days before its expiry
GTC_DAYS = 90  # Alpaca cancels GTC orders 90 days after creation
Key = tuple[str, str]


class ProtectiveStops:
    def __init__(self, om: OrderManager, run="0", confirm_polls: int = 5, wait=lambda s: None) -> None:
        """`confirm_polls`, `wait`: how often (and how long between, live: a sleep) a re-arm's cancel is polled for
        its confirmation before the new stop is sent."""
        self.om, self.run = om, run
        self.confirm_polls, self.wait = confirm_polls, wait
        self.suspended: set[str] = set()  # symbols with a split under way: no stop is re-created yet
        self._mine: dict[Key, list[str]] | None = None  # stop coids per sleeve (for `_unbooked`)

    def stops_of(self, key: Key):
        return self.om.working(symbol=key[1], strategy=key[0], purposes=("stop",))

    def ensure(self, sleeves: Mapping[Key, float], prices: Mapping[Key, float], now: datetime,
               session: str, only: set[str] | None = None) -> list[str]:
        """`sleeves`: shares per (strategy, symbol); `prices`: the stop price of each. `only`: look at these
        symbols alone (every other stop is left as it is). Returns what it did."""
        done: list[str] = []
        for key in sorted(set(sleeves) | {(r.strategy, r.symbol) for r in self.om.working(purposes=("stop",))}):
            strategy, symbol = key
            if only is not None and symbol not in only:
                continue
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
            if r is not None and _expiring(r.expires_at, r.created, now):
                self.om.cancel(r.coid)
                done.append(f"rearm {r.coid}")
                for _ in range(self.confirm_polls):
                    if r.state in TERMINAL:
                        break
                    self.wait(0.5)
                    self.om._poll(r)
                if r.state not in TERMINAL:
                    continue  # not confirmed yet: the next call sends the new stop (10 days of headroom)
                placed = self._rearm(r, key, whole, px, session)
                if placed:
                    done.append(f"stop {placed}")
                continue
            if r is None and self._unbooked(key):
                done.append(f"wait {strategy} {symbol}: a stop fill is not booked yet")
                continue
            if r is None:
                coid = self.om.next_coid(self.run, strategy, symbol, session, "stop")
                self.om.submit(OrderRequest(symbol, "sell", whole, "stop", "gtc", coid, stop_price=round(px, 2)),
                               strategy=strategy, purpose="stop", session=session, override_block=True)
                self._track(key, coid)
                done.append(f"stop {coid}")
            else:
                qty = self._resize_to(r, key, whole)
                if qty is not None or abs(float(r.stop or 0) - round(px, 2)) > 0.004:
                    self.om.replace(r.coid, stop=round(px, 2), qty=qty)
                    done.append(f"replace {r.coid}")
        return done

    def _resize_to(self, r, key: Key, whole: int) -> int | None:
        """The new quantity of a working stop, or None to keep it (QA-Ops review of #187). Down to the sleeve:
        always. Up: never while a stop fill of the sleeve is unbooked (the sleeve is stale), and never above the
        venue's position, read again (a failed read: no upsize, tried again at the next call)."""
        have = int(r.qty)
        if whole == have:
            return None
        if whole < have:
            return whole
        if self._unbooked(key):
            return None
        try:
            pos = self.om.broker.positions().get(key[1]) or {}
        except BrokerError:
            return None
        to = min(whole, int(math.floor(float(pos.get("qty") or 0) + 1e-9)))
        return to if to > have else None

    def _track(self, key: Key, coid: str) -> None:
        if self._mine is not None:
            self._mine.setdefault(key, []).append(coid)

    def _unbooked(self, key: Key) -> bool:
        """A stop of this sleeve has venue fills not yet booked to the ledger: the caller's sleeve is stale."""
        if self._mine is None:  # once (a restart: the manager folded its orders from the journal)
            self._mine = {}
            for r in self.om.orders.values():
                if r.purpose == "stop":
                    self._mine.setdefault((r.strategy, r.symbol), []).append(r.coid)
        mine = self._mine.get(key, [])
        mine += [r.coid for r in self.stops_of(key) if r.coid not in mine]
        return any((r := self.om.get(c)) is not None and r.cum > r.booked + 1e-9 for c in mine)

    def _rearm(self, old, key: Key, whole: int, px: float, session: str) -> str | None:
        """Issue #179: the re-arm's cancel is confirmed. The old stop may have triggered first (filled, in full or
        in part): read its final state and the venue's position again before the new stop goes out, so no stop
        sell rests on shares that are gone (on a margin account: a short-sale stop). FILLED (or REJECTED): no new
        stop; the fill booking and the next `ensure` decide. CANCELED/EXPIRED: a stop for the sleeve's shares less
        what the old one sold, capped at the venue's position. A failed position read places nothing (the next
        call creates the stop from the sleeve once nothing is left unbooked). Journalled as `stop_rearm`."""
        rec = {"coid": old.coid, "strategy": key[0], "symbol": key[1], "old_state": old.state.value,
               "old_filled": old.cum, "sleeve": whole, "broker_qty": None, "qty": 0, "placed": None}
        qty = 0
        if old.state in (St.CANCELED, St.EXPIRED):
            try:
                pos = self.om.broker.positions().get(key[1]) or {}
                rec["broker_qty"] = held = int(math.floor(float(pos.get("qty") or 0) + 1e-9))
                qty = max(0, min(whole - int(math.ceil(old.cum - 1e-9)), held))
            except BrokerError as e:
                rec["error"] = f"positions: {e.status}"
        rec["qty"] = qty
        if qty >= 1:
            coid = self.om.next_coid(self.run, key[0], key[1], session, "stop")
            self.om.submit(OrderRequest(key[1], "sell", qty, "stop", "gtc", coid, stop_price=round(px, 2)),
                           strategy=key[0], purpose="stop", session=session, override_block=True)
            rec["placed"] = coid
            self._track(key, coid)
        self.om.journal.append("stop_rearm", rec)
        return rec["placed"]

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


def _expiring(expires_at: str | None, created: str | None, now: datetime) -> bool:
    if expires_at:
        end = datetime.fromisoformat(expires_at)
    elif created:  # no expiry from the venue: by age, never silently lapsing
        end = datetime.fromisoformat(created) + timedelta(days=GTC_DAYS)
    else:
        return True
    return end - now <= timedelta(days=REARM_DAYS)
