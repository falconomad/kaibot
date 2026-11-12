"""The order manager (S78, issue #118; decision 15 section 3; audit D3-D8 and D11). Every order the daily path
sends goes through it, whatever created it; it never decides what to trade, only how an order's life is kept.

One record per logical order, keyed by its client order id (deterministic: `client_order_id`). Its state is a
pure fold over the local journal (engine/journal.py), so a restart rebuilds it exactly:

    om_intent  {coid, strategy, symbol, side, qty, type, tif, limit, stop, purpose, session, meta}  BEFORE the call
    om_terms   {coid, qty?, stop?, limit?}                       the new terms of a replace the venue accepted
    om_state   {coid, state, order_id?, venue?, cum?, avg?, reason?, ids?}                          every transition
    om_fill    {coid, activity, strategy, symbol, side, qty, px, fees, ts}  a fill read from the account activities
               (or the ledger's own `fill` entry with the same fields plus run_id, when the manager books the ledger)

States (Alpaca's statuses mapped in `VENUE`):

    intent -> submitted -> accepted -> partially_filled -> filled | canceled | expired | rejected
                           pending_cancel, pending_replace (not terminal)        unknown (no answer: see below)

Rules, each from Alpaca's documentation or NautilusTrader's reconciliation (docs/research/engine_v3/
notes_engine_architecture.md section 4):
- the intent is journalled before the venue is called (D5); a crash in between leaves an intent without a state;
- a submission without an answer (timeout, network cut, crash) is `unknown`: never re-sent blind, and no new order
  in that symbol is accepted (`blocked`) until a lookup BY CLIENT ORDER ID resolves it. A lookup that finds the
  order adopts it; an order not found on `LOST_LOOKUPS` lookups at least `LOST_AFTER_S` after the intent never
  reached the venue (`rejected`, reason `lost`), and only then may a new attempt (a new client id) go out;
- `recover()` runs those lookups for every open record before anything else, at start-up;
- fills come from the account activities (FILL rows), each booked once by its activity id: a duplicate event
  is ignored, a partial fill is booked at its own quantity and price (D3), a fill on an order the manager does
  not know is a reconciliation difference (engine/reconcile.py), never a silent position;
- a 403 or 422 rejects that one order (D4); nothing else is stopped;
- cancel and replace are transitions (`pending_cancel`, `pending_replace`) confirmed by the venue's next answer;
  a replace may answer with a new order id (Alpaca): the record follows it and keeps every id it ever had, so a
  fill on a replaced bracket leg is found whether or not Alpaca lists that leg under its parent (D11, issue #11).

Nothing here reads the wall clock: `now` is injected (the engine's clock, or `SimClock`).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from kaibot.broker.protocol import Broker, BrokerError, OrderRequest

OM_KINDS = ("om_intent", "om_terms", "om_state", "om_fill", "fill")
COID_MAX = 128  # Alpaca's limit on client_order_id
LOST_LOOKUPS, LOST_AFTER_S = 2, 60.0
REFUSED = (403, 422)
FILL_LOOKBACK = timedelta(minutes=10)  # activities are read from a little before the last one seen (ids dedupe)


class St(StrEnum):
    INTENT = "intent"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIAL = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    UNKNOWN = "unknown"


TERMINAL = frozenset({St.FILLED, St.CANCELED, St.EXPIRED, St.REJECTED})
UNCONFIRMED = frozenset({St.INTENT, St.SUBMITTED, St.UNKNOWN})  # the venue has not told us yet: blocks the symbol
VENUE = {  # Alpaca order status -> state
    "new": St.ACCEPTED, "accepted": St.ACCEPTED, "pending_new": St.ACCEPTED, "accepted_for_bidding": St.ACCEPTED,
    "calculated": St.ACCEPTED, "done_for_day": St.ACCEPTED, "stopped": St.ACCEPTED, "suspended": St.ACCEPTED,
    "held": St.ACCEPTED, "partially_filled": St.PARTIAL, "filled": St.FILLED, "canceled": St.CANCELED,
    "expired": St.EXPIRED, "rejected": St.REJECTED, "pending_cancel": St.PENDING_CANCEL,
    "pending_replace": St.PENDING_REPLACE,
}


class OrderBlocked(RuntimeError):
    """The manager refused to send: an unresolved order or reconciliation difference blocks the symbol."""


def client_order_id(run: int | str, strategy: str, symbol: str, session: str, purpose: str, attempt: int = 1) -> str:
    """kb<run>-<strategy>-<symbol>-<yyyymmdd>-<purpose>-<attempt>: the same decision always gives the same id, so
    the venue itself refuses a second copy (Alpaca: client order ids are unique per account)."""
    def clean(x, n: int) -> str:
        return re.sub(r"[^A-Za-z0-9_.]", "", str(x))[:n]

    coid = (f"kb{clean(run, 12)}-{clean(strategy, 40)}-{clean(symbol, 12)}-{str(session).replace('-', '')[:8]}-"
            f"{clean(purpose, 16)}-{int(attempt)}")
    assert len(coid) <= COID_MAX
    return coid


@dataclass
class OrderRecord:
    coid: str
    strategy: str
    symbol: str
    side: str
    qty: float
    type: str = "market"
    tif: str = "day"
    limit: float | None = None
    stop: float | None = None
    order_class: str = "simple"
    take_profit: float | None = None
    stop_loss: float | None = None
    purpose: str = "target"  # target | buy | sell | resize | stop | entry | exit ...
    session: str = ""
    meta: dict = field(default_factory=dict)
    state: St = St.INTENT
    order_id: str | None = None
    ids: list[str] = field(default_factory=list)  # every venue id this order had (replacements, bracket legs)
    venue: str | None = None  # the venue's last status word
    cum: float = 0.0  # filled quantity the venue reported on the order
    booked: float = 0.0  # filled quantity booked from activities
    value: float = 0.0  # $ of the booked fills
    reason: str | None = None
    created: str = ""
    misses: int = 0  # lookups by client id that found nothing
    expires_at: str | None = None

    @property
    def open(self) -> bool:
        return self.state not in TERMINAL

    @property
    def avg(self) -> float | None:
        return self.value / self.booked if self.booked else None

    @property
    def left(self) -> float:
        """Shares still to trade (0 once terminal)."""
        return max(self.qty - self.booked, 0.0) if self.open else 0.0

    def request(self) -> OrderRequest:
        return OrderRequest(self.symbol, self.side, self.qty, self.type, self.tif, self.coid, self.limit, self.stop,
                            self.order_class, self.take_profit, self.stop_loss)


@dataclass(frozen=True)
class Fill:
    coid: str
    activity: str
    strategy: str
    symbol: str
    side: str
    qty: float
    px: float
    fees: float
    ts: str | None
    purpose: str


class OrderManager:
    """`journal`: engine.journal.Journal (append, entries). `run_id`: with it, every fill is also the ledger's
    own `fill` entry (one append: the ledger and the manager can never disagree); without it (research) the
    manager writes `om_fill` and the caller books its own ledger from `on_fill`."""

    def __init__(self, broker: Broker, journal, now: Callable[[], datetime], *, run_id: int | None = None,
                 on_fill: Callable[[Fill, OrderRecord], None] | None = None) -> None:
        self.broker, self.journal, self.now, self.run_id = broker, journal, now, run_id
        self.on_fill = on_fill
        self.orders: dict[str, OrderRecord] = {}
        self.by_id: dict[str, str] = {}  # venue id -> coid
        self.seen: set[str] = set()  # activity ids booked
        self.foreign: dict[str, dict] = {}  # activity id -> a fill on an order this manager does not know
        self.last_fill: datetime | None = None
        self.errors: list[tuple[str, str]] = []  # (coid, what failed) of the last calls, for the caller's events
        for _, _t, kind, body in journal.entries(kinds=OM_KINDS):
            self._fold(kind, body)

    # ---------------------------------------------------------------- the fold
    def _fold(self, kind: str, b: dict) -> None:
        if kind == "om_intent":
            r = OrderRecord(**{k: b[k] for k in OrderRecord.__dataclass_fields__ if k in b and k not in ("state",)})
            self.orders[r.coid] = r
        elif kind == "om_terms":
            r = self.orders.get(b["coid"])
            for k in ("stop", "limit", "qty"):
                if r is not None and b.get(k) is not None:
                    setattr(r, k, float(b[k]))
        elif kind == "om_state":
            r = self.orders.get(b["coid"])
            if r is None:
                return
            r.state = St(b["state"])
            for k in ("venue", "reason", "expires_at"):
                if k in b:
                    setattr(r, k, b[k])
            if b.get("cum") is not None:
                r.cum = float(b["cum"])
            if "misses" in b:
                r.misses = int(b["misses"])
            for i in [*(b.get("ids") or []), b.get("order_id")]:
                if i and i not in r.ids:
                    r.ids.append(i)
                    self.by_id[i] = r.coid
            if b.get("order_id"):
                r.order_id = b["order_id"]
        elif kind in ("om_fill", "fill") and b.get("activity"):
            self.seen.add(b["activity"])
            r = self.orders.get(b.get("coid") or "")
            if r is not None:
                q = float(b["qty"])
                r.booked += q
                r.value += q * float(b["px"])
            if b.get("ts"):
                t = _ts(b["ts"])
                self.last_fill = t if self.last_fill is None else max(self.last_fill, t)

    def _append(self, kind: str, body: dict) -> None:
        body = {k: v for k, v in body.items() if v is not None}
        self.journal.append(kind, body)
        self._fold(kind, body)

    def _state(self, r: OrderRecord, state: St, **kw) -> None:
        self._append("om_state", {"coid": r.coid, "state": str(state), **kw})

    # ---------------------------------------------------------------- reads
    def get(self, coid: str) -> OrderRecord | None:
        return self.orders.get(coid)

    def working(self, symbol: str | None = None, strategy: str | None = None,
                purposes: Iterable[str] | None = None) -> list[OrderRecord]:
        ps = set(purposes) if purposes is not None else None
        return [r for r in self.orders.values() if r.open and (symbol is None or r.symbol == symbol)
                and (strategy is None or r.strategy == strategy) and (ps is None or r.purpose in ps)]

    def blocked(self, symbol: str) -> str | None:
        """Why no new order may go out in `symbol` (None: it may): an order the venue has not confirmed yet."""
        for r in self.orders.values():
            if r.symbol == symbol and r.state in UNCONFIRMED:
                return f"order {r.coid} is {r.state}: look it up by its client order id first"
        return None

    def attempts(self, base: str) -> int:
        """How many orders were already made for this decision (the client id without its attempt number)."""
        return sum(1 for c in self.orders if c.rsplit("-", 1)[0] == base)

    def next_coid(self, run, strategy: str, symbol: str, session: str, purpose: str) -> str:
        base = client_order_id(run, strategy, symbol, session, purpose, 1).rsplit("-", 1)[0]
        for r in self.orders.values():  # an open order of this decision is reused, never doubled
            if r.coid.rsplit("-", 1)[0] == base and r.open:
                return r.coid
        return client_order_id(run, strategy, symbol, session, purpose, self.attempts(base) + 1)

    # ---------------------------------------------------------------- writes
    def submit(self, req: OrderRequest, *, strategy: str, purpose: str, session: str, meta: dict | None = None,
               override_block: bool = False) -> OrderRecord:
        """Journal the intent, then send. Returns the record in its new state (rejected and unknown included: a
        refusal is that order's answer, not an exception). The same client id twice is the same order: an open
        or filled record is returned as it is, nothing is sent again."""
        coid = req.client_order_id
        if not coid:
            raise ValueError("the order manager sends only orders with a client order id")
        have = self.orders.get(coid)
        if have is not None and have.state is not St.REJECTED and have.state is not St.CANCELED \
                and have.state is not St.EXPIRED:
            return have
        if have is not None:
            raise ValueError(f"{coid} already ended ({have.state}); a new attempt needs a new client order id")
        why = None if override_block else self.blocked(req.symbol)
        if why:
            raise OrderBlocked(f"{req.symbol}: {why}")
        self._append("om_intent", {
            "coid": coid, "strategy": strategy, "symbol": req.symbol, "side": req.side, "qty": req.qty,
            "type": req.type, "tif": req.time_in_force, "limit": req.limit_price, "stop": req.stop_price,
            "order_class": req.order_class, "take_profit": req.take_profit, "stop_loss": req.stop_loss,
            "purpose": purpose, "session": session, "meta": dict(meta or {}), "created": self.now().isoformat()})
        r = self.orders[coid]
        try:
            o = self.broker.submit(req)
        except BrokerError as e:
            if e.status in REFUSED and "client_order_id" not in (e.body or ""):
                self._state(r, St.REJECTED, reason=f"{e.status}: {_reason(e)}")
            else:  # no answer, 5xx, or "client order id must be unique" without the order read back
                self._state(r, St.UNKNOWN, reason=f"{e.status}: {_reason(e)}")
            return r
        except Exception as e:  # anything else: the order may be at the venue
            self._state(r, St.UNKNOWN, reason=f"{type(e).__name__}: {str(e)[:160]}")
            return r
        self._adopt(r, o)
        return r

    def cancel(self, coid: str) -> OrderRecord:
        r = self.orders[coid]
        if not r.open or r.state is St.PENDING_CANCEL:
            return r
        if r.order_id is None:
            raise OrderBlocked(f"{coid} is {r.state}: resolve it before cancelling")
        self._state(r, St.PENDING_CANCEL)
        try:
            self.broker.cancel(r.order_id)
        except BrokerError as e:
            self.errors.append((coid, f"cancel: {e.status}: {_reason(e)}"))
        self._poll(r)
        return r

    def replace(self, coid: str, *, stop: float | None = None, limit: float | None = None,
                qty: float | None = None) -> OrderRecord:
        """Change a working order's price or quantity. The answer may be a new order id (Alpaca): the record
        follows it. A refused replace leaves the order as it was (polled again)."""
        r = self.orders[coid]
        if not r.open or r.order_id is None or r.state in (St.PENDING_CANCEL, St.PENDING_REPLACE):
            raise OrderBlocked(f"{coid} is {r.state}: it cannot be replaced now")
        self._state(r, St.PENDING_REPLACE)
        try:
            o = self.broker.replace(r.order_id, stop_price=stop, limit_price=limit, qty=qty)
        except BrokerError as e:
            self.errors.append((coid, f"replace: {e.status}: {_reason(e)}"))
            self._poll(r)
            return r
        self._append("om_terms", {"coid": coid, "stop": stop, "limit": limit, "qty": qty})
        self._adopt(r, o)
        return r

    def replace_leg(self, coid: str, leg_id: str, *, stop: float | None = None,
                    limit: float | None = None) -> str | None:
        """Re-price one leg of a bracket or OTO order. Returns the id the leg has now (Alpaca: a new one); the
        record keeps both, so a fill on the replaced leg is booked whether Alpaca lists it under the parent or
        on its own (issue #11)."""
        r = self.orders[coid]
        try:
            o = self.broker.replace(leg_id, stop_price=stop, limit_price=limit)
        except BrokerError as e:
            self.errors.append((coid, f"replace leg {leg_id}: {e.status}: {_reason(e)}"))
            return None
        new = o.get("id") if isinstance(o, dict) else None
        self._state(r, r.state, ids=[leg_id, new])
        return new

    # ---------------------------------------------------------------- the venue's answers
    def _adopt(self, r: OrderRecord, o: dict) -> None:
        """Take the venue's view of the order (and follow a replacement)."""
        if o.get("replaced_by"):
            with contextlib.suppress(BrokerError):
                o = self.broker.order(o["replaced_by"])
        ids = [o.get("id")] + [x.get("id") for x in o.get("legs") or []]
        state = VENUE.get(str(o.get("status")), St.ACCEPTED)
        if state is St.ACCEPTED and float(o.get("filled_qty") or 0) > 0:
            state = St.PARTIAL
        self._state(r, state, order_id=o.get("id"), venue=o.get("status"), cum=float(o.get("filled_qty") or 0),
                    ids=[i for i in ids if i], expires_at=o.get("expires_at"), misses=0)

    def _poll(self, r: OrderRecord) -> None:
        try:
            o = self.broker.order(r.order_id) if r.order_id else self.broker.order_by_client_id(r.coid)
        except BrokerError as e:
            if e.status == 404:
                o = None
            else:
                self.errors.append((r.coid, f"poll: {e.status}: {_reason(e)}"))
                return
        if o is None:
            self._miss(r)
            return
        if o.get("status") == "replaced" and o.get("replaced_by"):
            self._adopt(r, o)
            return
        if r.state is St.PENDING_CANCEL and o.get("status") not in ("canceled", "filled", "expired", "rejected"):
            return  # still being cancelled
        self._adopt(r, o)

    def _miss(self, r: OrderRecord) -> None:
        """A lookup by client id found nothing."""
        misses = r.misses + 1
        age = (self.now() - _ts(r.created)).total_seconds() if r.created else 1e9
        if r.state in UNCONFIRMED and misses >= LOST_LOOKUPS and age >= LOST_AFTER_S:
            self._state(r, St.REJECTED, reason="lost: never reached the venue", misses=misses)
        else:
            self._state(r, r.state, misses=misses)

    def resolve(self) -> list[OrderRecord]:
        """Look up every order the venue has not confirmed, BY CLIENT ORDER ID, before anything else."""
        out = []
        for r in list(self.orders.values()):
            if r.state in UNCONFIRMED:
                try:
                    o = self.broker.order_by_client_id(r.coid)
                except BrokerError as e:
                    self.errors.append((r.coid, f"lookup: {e.status}: {_reason(e)}"))
                    continue
                if o is None:
                    self._miss(r)
                else:
                    self._adopt(r, o)
                out.append(r)
        return out

    def recover(self) -> list[OrderRecord]:
        """Start-up: unconfirmed orders are looked up by client id, then every open one is polled and the
        activities are read, so the book is the venue's before any decision."""
        out = self.resolve()
        self.sync()
        return out

    def sync(self) -> list[Fill]:
        """Book new fills from the account activities, then poll every open order the venue knows."""
        self.resolve()
        fills = self.read_fills()
        for r in list(self.orders.values()):
            if r.open and r.order_id and r.state not in UNCONFIRMED:
                self._poll(r)
        return fills

    def read_fills(self) -> list[Fill]:
        after = None if self.last_fill is None else self.last_fill - FILL_LOOKBACK
        pending = [_ts(a["transaction_time"]) for a in self.foreign.values() if a.get("transaction_time")]
        if after is not None and pending:  # a fill not yet attributed is read again until it is
            after = min(after, min(pending) - FILL_LOOKBACK)
        try:
            rows = self.broker.activities(after, types=("FILL",))
        except BrokerError as e:
            self.errors.append(("", f"activities: {e.status}: {_reason(e)}"))
            return []
        out = []
        for a in rows:
            aid = str(a.get("id"))
            if aid in self.seen:
                continue  # a duplicate event: booked once
            coid = self.by_id.get(str(a.get("order_id")))
            r = self.orders.get(coid) if coid else None
            if r is None:  # not ours, or not yet known to be ours (an unknown order is resolved later)
                self.foreign[aid] = dict(a)
                continue
            self.foreign.pop(aid, None)
            f = Fill(r.coid, aid, r.strategy, str(a["symbol"]), str(a["side"]), float(a["qty"]), float(a["price"]),
                     float(a.get("fees") or 0.0), a.get("transaction_time"), r.purpose)
            body = {"coid": f.coid, "activity": aid, "strategy": f.strategy, "symbol": f.symbol, "side": f.side,
                    "qty": f.qty, "px": f.px, "fees": f.fees, "ts": f.ts, "purpose": f.purpose,
                    "session": r.session}
            if self.run_id is not None:
                self._append("fill", {"run_id": self.run_id, **body})
            else:
                self._append("om_fill", body)
            out.append(f)
            if self.on_fill is not None:
                self.on_fill(f, r)
        return out

    def snapshot(self) -> dict:
        return {c: {**asdict(r), "state": str(r.state)} for c, r in self.orders.items()}


def _reason(e: BrokerError) -> str:
    return str(e.body or e)[:160]


def _ts(v) -> datetime:
    t = v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=UTC)
