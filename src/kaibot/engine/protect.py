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
- issue #192: two strategies may hold one symbol. Each sleeve keeps its own stop (per-sleeve allocation), and a
  stop never covers shares that another open sell order there holds (the other sleeves' stops, any sale): its
  resting quantity is at most the venue position, read once per call, less what every other open sell order in
  the symbol still holds. So the stops of a symbol never sum above the venue position. A new stop, a resize up
  and a re-arm are capped that way; a stop above its share is shrunk (or cancelled at 0), and shrinks go out
  before grows in the same call. A failed read creates nothing in a shared symbol and grows nothing anywhere
  (a symbol with one sleeve and no other sell order still gets its first stop at the sleeve: #118). `strategies`
  scopes a call to some strategies' stops, so one strategy's call never cancels another's stops as "gone";
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
the other cancelled, then the kept one is resized. Every stop is capped at the venue's position less the unfilled
remaining quantity of every other open sell order in the symbol, taken from the venue's open orders (#187, #192).
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
        self._pos: dict | None = None  # the venue's positions, read at most once per `ensure` call (#192)
        self._open: tuple[dict[str, tuple[str, float]], set[str]] | None = None  # venue open sells, per call
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
        self._snapshot(orders)
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
               session: str, only: set[str] | None = None, strategies: set[str] | None = None) -> list[str]:
        """`sleeves`: shares per (strategy, symbol); `prices`: the stop price of each. `only`: look at these
        symbols alone (every other stop is left as it is). `strategies`: look at these strategies' stops alone
        (a caller that passes one strategy's sleeves). Returns what it did."""
        done: list[str] = []
        self._pos = self._open = None
        adopted = self.adopt(sleeves, session, only)  # issue #188: fail-safe stops become the sleeves' own first
        if adopted is not None:
            done += adopted
        keys = [k for k in set(sleeves) | {(r.strategy, r.symbol) for r in self.om.working(purposes=("stop",))}
                if (only is None or k[1] in only) and (strategies is None or k[0] in strategies)]
        for key in sorted(keys, key=lambda k: (0 if _whole(sleeves.get(k)) < self._covered(k) else 1, k)):
            strategy, symbol = key  # shrinks first (#192): the shares they free are there for the grows
            if symbol in self.suspended:
                continue
            whole = _whole(sleeves.get(key))
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
                self._pos = self._open = None  # the venue may have moved during the cancel: read again
                placed = self._rearm(r, key, whole, px, session)
                self._pos = self._open = None
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
                free = self._free(key)
                if free is None and self._shared(key, sleeves):
                    done.append(f"wait {strategy} {symbol}: the venue's position could not be read")
                    continue
                qty = whole if free is None else min(whole, free)
                if qty < 1:
                    done.append(f"wait {strategy} {symbol}: no free shares at the venue")
                    continue
                coid = self.om.next_coid(self.run, strategy, symbol, session, "stop")
                self.om.submit(OrderRequest(symbol, "sell", qty, "stop", "gtc", coid, stop_price=round(px, 2)),
                               strategy=strategy, purpose="stop", session=session, override_block=True)
                self._track(key, coid)
                done.append(f"stop {coid}")
            else:
                qty = self._resize_to(r, key, whole)
                if qty == 0:  # nothing of it is free at the venue any more
                    self.om.cancel(r.coid)
                    done.append(f"cancel {r.coid}")
                elif qty is not None or abs(float(r.stop or 0) - round(px, 2)) > 0.004:
                    self.om.replace(r.coid, stop=round(px, 2), qty=qty)
                    done.append(f"replace {r.coid}")
        return done

    def _resize_to(self, r, key: Key, whole: int) -> int | None:
        """The new TOTAL quantity of a working stop (Alpaca's replace takes the order's total: what rests is the
        total less what it filled), None to keep it, 0 to cancel it (QA-Ops review of #187, #192, #197 relay).
        Sized by what still rests: down to the sleeve, or to what the venue leaves free for it (`_free`), always;
        up: never while a stop fill of the sleeve is unbooked (the sleeve is stale), never above what is free,
        and never on a failed read (tried again at the next call)."""
        done = int(math.ceil(float(r.cum) - 1e-9))
        have = int(r.qty) - done  # resting
        free = self._free(key, exclude=r.coid)
        down = whole if free is None else min(whole, free)
        if down < have:
            return 0 if down < 1 else down + done
        if whole <= have or free is None or self._unbooked(key):
            return None
        to = min(whole, free)
        return to + done if to > have else None

    def _venue_qty(self, symbol: str) -> int | None:
        """Whole shares the venue holds in `symbol` (read once per `ensure` call), None when it cannot be read."""
        if self._pos is None:
            try:
                self._pos = dict(self.om.broker.positions())
            except BrokerError:
                return None
        return int(math.floor(float((self._pos.get(symbol) or {}).get("qty") or 0) + 1e-9))

    def _snapshot(self, orders) -> None:
        """Keep the venue's open sell orders (id -> symbol, unfilled remaining qty) and the venue ids the manager's
        open sells had then (QA-Ops review of #195: a sale already filled at the venue but not yet polled must not
        count as held)."""
        left = {}
        for o in orders:
            if o.get("side") == "sell" and o.get("id"):
                left[str(o["id"])] = (str(o.get("symbol") or ""),
                                      max(float(o.get("qty") or 0) - float(o.get("filled_qty") or 0), 0.0))
        known = {str(r.order_id) for r in self.om.working() if r.side == "sell" and r.order_id}
        self._open = (left, known)

    def _held(self, symbol: str, exclude: str | None = None) -> int:
        """Shares every open sell order in `symbol` but `exclude` still holds at the venue: only the unfilled
        remaining quantity counts (stops of every sleeve, sales, fail-safe stops not adopted). From the venue's
        open orders, read once per call: an order the venue lists counts at the smaller of its and the manager's
        remaining; one the manager knew at the read that the venue no longer lists has filled or ended (0); one
        sent (or replaced to a new id) after the read counts at the manager's remaining. Open orders unreadable:
        the manager's view."""
        if self._open is None:
            try:
                self._snapshot(list(self.om.broker.open_orders()))
            except BrokerError:
                self._open = ({}, set())
        left, known = self._open
        if not left and not known:
            return self._held_om(symbol, exclude)
        held, mine = 0.0, set()
        for r in self.om.working(symbol=symbol):
            if r.side != "sell":
                continue
            oid = str(r.order_id) if r.order_id else None
            mine |= {str(i) for i in r.ids if i} | ({oid} if oid else set())
            if r.coid == exclude:
                continue
            om_left = max(float(r.qty) - float(r.cum), 0.0)
            if oid in left:
                held += min(om_left, left[oid][1])
            elif oid is None or oid not in known:  # sent (or replaced to a new id) after the read
                held += om_left
        for oid, (sym, q) in left.items():  # resting at the venue, unknown to the manager (a fail-safe stop)
            if sym == symbol and oid not in mine and oid not in self.om.by_id:
                held += q
        return int(math.ceil(held - 1e-9))

    def _held_om(self, symbol: str, exclude: str | None) -> int:
        return int(math.ceil(sum(max(float(r.qty) - float(r.cum), 0.0) for r in self.om.working(symbol=symbol)
                                 if r.side == "sell" and r.coid != exclude) - 1e-9))

    def _free(self, key: Key, exclude: str | None = None) -> int | None:
        """The most shares a stop of this sleeve may still cover (#192): the venue position less what every
        other open sell order in the symbol holds. None when the position cannot be read."""
        pos = self._venue_qty(key[1])
        return None if pos is None else max(0, pos - self._held(key[1], exclude))

    def _shared(self, key: Key, sleeves: Mapping[Key, float]) -> bool:
        """Another sleeve, or another open sell order, in this symbol."""
        return any(k[1] == key[1] and k != key and _whole(v) >= 1 for k, v in sleeves.items()) or \
            self._held(key[1]) > 0

    def _covered(self, key: Key) -> int:
        return sum(int(r.qty - r.cum) for r in self.stops_of(key))  # resting

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
               "old_filled": old.cum, "sleeve": whole, "broker_qty": None, "held_by_others": None, "qty": 0,
               "placed": None}
        qty = 0
        if old.state in (St.CANCELED, St.EXPIRED):
            try:  # read here, not through the call's cache: the position may have changed during the cancel
                pos = self.om.broker.positions().get(key[1]) or {}
                rec["broker_qty"] = held = int(math.floor(float(pos.get("qty") or 0) + 1e-9))
                rec["held_by_others"] = others = self._held(key[1])  # #192: the other sleeves' stops, sales
                qty = max(0, min(whole - int(math.ceil(old.cum - 1e-9)), held - others))
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


def _whole(qty) -> int:
    return int(math.floor(float(qty or 0.0) + 1e-9))


def _expiring(expires_at: str | None, created: str | None, now: datetime) -> bool:
    if expires_at:
        end = datetime.fromisoformat(expires_at)
    elif created:  # no expiry from the venue: by age, never silently lapsing
        end = datetime.fromisoformat(created) + timedelta(days=GTC_DAYS)
    else:
        return True
    return end - now <= timedelta(days=REARM_DAYS)
