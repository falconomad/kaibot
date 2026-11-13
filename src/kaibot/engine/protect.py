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

Fail-safe stops (issue #188). While the engine is silent the watchdog (`kbwd-<strategy>-<symbol>-<time>-wdstop`)
and the database dead-man (`kbdm-...-dmstop`, migration 0019) re-create a missing stop straight at the venue. That
stop holds the shares, so the manager's own stop would be refused and a sale could not free them. `adopt` takes
every resting fail-safe stop of a known sleeve into the order manager as that sleeve's own protective stop
(`OrderManager.adopt_order`: an `om_intent` under the venue's client id plus its `om_state`, and a `stop_adopted`
entry; a restart folds it and never adopts it twice). It runs at start-up (`startup`: after the manager's
recovery, before the fills are booked), at every `ensure` (each session's reconciliation of the stops) and before
every `release`. From then on the usual rules apply: never cancelled and re-armed on adoption (#179), re-priced
and resized through `replace`, freed by `release`. A fail-safe stop that FILLED while the engine was down is
adopted from its fill (`OrderManager.foreign`), so the next sync books the sale to the sleeve instead of leaving a
foreign fill. The strategy in the client id is matched to a sleeve by the client id's own spelling
(`failsafe.clean(name)[:40]`), so a neighbour name (`trend_momentum~reg60`, written `trend_momentumreg60`) maps back
to its sleeve; neighbours are research names and never live (`strategies.registry.live` loads only registered
rules). A fail-safe stop of no known sleeve is left resting and journalled once (`stop_adopt_skipped`). When the
open orders cannot be read, no new stop is created in that call (a fail-safe stop may hold the shares).
A stop the manager replaced works under a new client id of its own family (issue #197,
`OrderManager.replacement_coid`), so `adopt` knows it by `OrderManager.owner` (or its order id) and never takes it
for a new fail-safe stop; a partly filled stop is resized on what still rests (the venue's qty includes the fill).

One stop per sleeve: when the engine's stop and a fail-safe stop both rest for one sleeve, the larger is kept and
the other cancelled, then the kept one is resized. A resize up is capped at the venue's position less the working
stops of the symbol's other sleeves (#187; #192 for two sleeves in one symbol).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.failsafe import FAILSAFE_COID, clean
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
        self._skipped: set[str] = set()  # fail-safe stops of no known sleeve, journalled once

    def startup(self, sleeves, session: str) -> list[str] | None:
        """Start-up (issue #188): the manager recovers its orders from the venue, the fail-safe stops of the
        sleeves are adopted, then the fills are booked (a fail-safe stop that filled is then the sleeve's sale)."""
        self.om.recover()
        done = self.adopt(sleeves, session)
        self.om.sync()
        return done

    def adopt(self, sleeves, session: str | None = None, only: set[str] | None = None) -> list[str] | None:
        """Adopt every fail-safe stop (resting, or filled while the engine was down) of a sleeve in `sleeves`
        (keys, or a mapping keyed by them). Returns what it adopted; None when the open orders cannot be read."""
        keys = list(sleeves)
        try:
            orders = list(self.om.broker.open_orders())
        except BrokerError as e:
            self.om.errors.append(("", f"open orders: {e.status}"))
            return None
        for a in list(self.om.foreign.values()):  # fills of orders the manager does not know
            oid = str(a.get("order_id") or "")
            if oid and oid not in self.om.by_id and a.get("side") == "sell":
                try:
                    orders.append(self.om.broker.order(oid))
                except BrokerError:
                    continue
        done = []
        for o in orders:
            coid = str(o.get("client_order_id") or "")
            m = FAILSAFE_COID.match(coid)
            if m is None or o.get("side") != "sell" or o.get("type") not in ("stop", "stop_limit"):
                continue
            sym = str(o.get("symbol") or "")
            if (only is not None and sym not in only) or self.om.owner(coid) is not None \
                    or str(o.get("id") or "") in self.om.by_id:  # a stop the manager replaced is its own (#197)
                continue
            match = [k for k in keys if k[1] == sym and clean(k[0])[:40] == m.group(2)]
            if len(match) != 1:
                if coid not in self._skipped:
                    self._skipped.add(coid)
                    self.om.journal.append("stop_adopt_skipped", {
                        "coid": coid, "symbol": sym, "order_id": o.get("id"),
                        "why": "no sleeve of that strategy" if not match else "more than one sleeve matches"})
                continue
            key = match[0]
            by = "watchdog" if m.group(1) == "wd" else "deadman"
            r = self.om.adopt_order(o, strategy=key[0], purpose="stop",
                                    session=session or str(o.get("created_at") or "")[:10], meta={"by": by})
            self._track(key, r.coid)
            self.om.journal.append("stop_adopted", {"coid": r.coid, "by": by, "strategy": key[0], "symbol": sym,
                                                    "order_id": r.order_id, "qty": r.qty, "stop": r.stop,
                                                    "state": r.state.value})
            done.append(f"adopt {r.coid}")
        return done

    def stops_of(self, key: Key):
        return self.om.working(symbol=key[1], strategy=key[0], purposes=("stop",))

    def ensure(self, sleeves: Mapping[Key, float], prices: Mapping[Key, float], now: datetime,
               session: str, only: set[str] | None = None) -> list[str]:
        """`sleeves`: shares per (strategy, symbol); `prices`: the stop price of each. `only`: look at these
        symbols alone (every other stop is left as it is). Returns what it did."""
        done: list[str] = []
        adopted = self.adopt(sleeves, session, only)  # issue #188: fail-safe stops become the sleeves' own first
        if adopted is not None:
            done += adopted
        for key in sorted(set(sleeves) | {(r.strategy, r.symbol) for r in self.om.working(purposes=("stop",))}):
            strategy, symbol = key
            if only is not None and symbol not in only:
                continue
            if symbol in self.suspended:
                continue
            whole = int(math.floor(float(sleeves.get(key, 0.0)) + 1e-9))
            px = prices.get(key)
            # one stop per sleeve: keep the largest (the engine's own and a fail-safe one may both rest)
            recs = sorted(self.stops_of(key), key=lambda r: (r.state is St.PENDING_CANCEL, -(r.qty - r.cum)))
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
            if r is None and adopted is None:
                done.append(f"wait {strategy} {symbol}: open orders unreadable (a fail-safe stop may hold the shares)")
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
        venue's position, read again (a failed read: no upsize, tried again at the next call). Counted on what
        still rests (#197: a partly filled stop at restart): the venue's qty is the order's total, filled part
        included, so the quantity sent is the shares to keep covered plus what it already sold."""
        done = int(math.ceil(float(r.cum) - 1e-9))
        have = int(r.qty) - done
        if whole == have:
            return None
        if whole < have:
            return whole + done
        if self._unbooked(key):
            return None
        try:
            pos = self.om.broker.positions().get(key[1]) or {}
        except BrokerError:
            return None
        held = int(math.floor(float(pos.get("qty") or 0) + 1e-9))
        others = sum(int(math.ceil(x.qty - x.cum - 1e-9)) for x in self.om.working(symbol=key[1], purposes=("stop",))
                     if x.coid != r.coid)  # #192: the symbol's other stops hold shares too
        to = min(whole, held - others)
        return to + done if to > have else None

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
        if self.adopt({key}) is None:  # issue #188: a fail-safe stop may hold the shares; unknown: no sale yet
            return False
        ok = True
        for r in self.stops_of(key):
            if r.state in (St.PENDING_CANCEL, St.PENDING_REPLACE) or r.state in UNCONFIRMED:
                ok = False
            elif whole < 1:
                self.om.cancel(r.coid)
                ok = ok and not r.open
            elif int(r.qty) - int(math.ceil(float(r.cum) - 1e-9)) > whole:  # what still rests (#197)
                done = int(math.ceil(float(r.cum) - 1e-9))
                self.om.replace(r.coid, qty=whole + done)
                ok = ok and int(r.qty) - done <= whole and r.state is not St.PENDING_REPLACE
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
