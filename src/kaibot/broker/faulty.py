"""Seeded fault injection at the engine's two outside boundaries (M12 ticket 3.2, #464): the venue and the store.

Not another simulator: `FaultyBroker` wraps `SimBroker` (broker/sim.py), which goes on deciding what an order is and
when it fills. The wrapper only decides what the ENGINE gets to see of it, and when. `FaultyData` does the same for
the market-data client's latest quotes, `FaultyStore` for Supabase. Used by the deterministic simulation tests
(tests/dst/); nothing in the engine imports this module, and it can never wrap the paper adapter (it reads the
simulator's own books).

Alpaca documents no ordering or redelivery guarantee for its fill stream, so the menu is fine-grained (the lesson of
Jepsen's TigerBeetle analysis: coarse faults leave the interesting paths unreached). One call, one order or one fill
is hit, not "the broker is down":

venue (FaultyBroker)
    submit_timeout_accepted   the order is in, the answer never comes (BrokerError status 0)
    submit_timeout_lost       the request never reached the venue (status 0)
    lookup_lag                after such a timeout the order cannot be found by its client order id for some reads,
                              while its fill is already in the account activities: the fill before the acknowledgement
    listing_lag               ... and for as long it is missing from the open-orders list too
    cancel_lost               the cancel request never reached the venue (status 0); the order works on
    cancel_late               the venue has the request (`pending_cancel`) and acts on it one bar later; the order can
                              fill in that bar: the fill after the cancel request
    fill_duplicate            an account activity is delivered twice (same id), at this read and at later ones
    fill_dropped              an activity is missing from the reads of the next minutes and appears on a later poll
    partial_fill              a market order fills in part (a quarter, half or three quarters); the rest never fills
    hold                      the venue does not trade the order for some bars (a slow or halted venue)
    rate_limit                every call of one kind is answered 429 for one minute
    clock_skew                the venue's clock reads minutes off the host's for a spell
    mark_bad                  a position's `current_price` is missing, zero, or stale by a large move
    submit_refused            the venue refuses the order (403), nothing is taken. Only when a hand-written scenario
                              names it (`Forced`, never drawn by the dice): `param` refusals of that symbol and side
                              (default 1), whatever the attempt (audit 2, 5.9)
    sale_refused              the venue refuses a market SALE (403, nothing taken), drawn per client order id, so each
                              attempt of the sale is drawn again (audit 2, 5.18; the seeded form of submit_refused)
    endpoint_down             a partial outage: one read endpoint (positions, activities, open orders, the account or
                              one order) answers 503 for a spell of minutes while every other call works (5.18)

corporate actions (FaultyBroker.advance, before the first bar of a session; audit 2, 5.20). Drawn per (held symbol,
session), at most one per symbol and session, applied to the account as Alpaca applies them before the open:
    ca_split                  a forward split (2 or 3 for 1): the shares multiply, the cost per share and every bar of
                              the symbol divide (the feed is split-adjusted), a SPLIT activity is posted
    ca_spinoff                a spin-off: whole new shares of `<symbol>W` (one per two, or one per one, parent shares)
                              with a tenth of the parent's cost basis, a SPIN activity, and bars of the new symbol
    ca_merger                 a cash merger at a premium (5 or 20% over the last close): the position is removed, the
                              cash posted (Alpaca's two MA rows), and the venue refuses every later buy of the symbol
                              (the asset is no longer active)
    Each is in `actions` (the sourced record a test serves the engine as Alpaca's corporate actions) and goes to
    `on_action(action)`, for the test's own data (the daily store).

market data (FaultyData.quotes_within)
    quote_stale / quote_missing / quote_zero / quote_crossed
market data (FaultyData.bars, today's IEX minute bars only; M12 3.11, #473; never in DEFAULT_FAULTS)
    bar_zero                  one bar's prices are zero (a bad print)
    bar_spike                 one bar's prices are 8 % off, the index unmoved (a bad print the index does not share)
    bar_stale                 the feed has no new bar of the symbol for a ten-minute block (a stale symbol)

store (FaultyStore)
    write_fail                the write is refused (503): nothing was written
    write_timeout             the write was applied and the answer never came
    write_twice               the request arrives twice (the client sees one answer)
    read_fail                 a read is refused (503)
    lease_renew_fail          every renewal of the engine's lease fails (503, nothing renewed) for a spell of one or
                              two minutes; the lease itself is not lost (audit 2, 5.18)

Determinism. A fault is never drawn from a stream in call order: every decision is a hash of (seed, fault, what it
is about: the client order id, the order id, the activity id, the minute). So the faults of a seed do not depend on
the order in which the engine happens to make its calls (a set's iteration order, an extra read after a restart),
and a failing seed runs again the same way.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta

import pandas as pd

from kaibot.broker.protocol import BrokerBase, BrokerError, OrderRequest
from kaibot.broker.sim import WORKING, SimBroker

VENUE_FAULTS = ("submit_timeout_accepted", "submit_timeout_lost", "lookup_lag", "listing_lag", "cancel_lost",
                "cancel_late", "fill_duplicate", "fill_dropped", "partial_fill", "hold", "rate_limit", "clock_skew",
                "mark_bad", "sale_refused", "endpoint_down")
DATA_FAULTS = ("quote_stale", "quote_missing", "quote_zero", "quote_crossed")
BAR_FAULTS = ("bar_zero", "bar_spike", "bar_stale")
BAR_STALE_BLOCK_MIN = 10
BAR_SPIKE = 1.08
STORE_FAULTS = ("write_fail", "write_timeout", "write_twice", "read_fail", "lease_renew_fail")
# audit 2, 5.18: the kinds of the second audit (the DST world draws them in its new weeks only, so the summer week's
# seeds run as they did)
AUDIT2_FAULTS = ("sale_refused", "endpoint_down", "lease_renew_fail")
# audit 2, 5.20: corporate actions (the DST world draws them in its new weeks only, as AUDIT2_FAULTS)
CA_FAULTS = ("ca_split", "ca_spinoff", "ca_merger")
SPLIT_RATIOS = (2, 3)
SPINOFF_RATIOS = (0.5, 1.0)  # new shares per parent share
SPINOFF_BASIS = 0.1  # the part of the parent's cost basis (and price) the new shares get
MERGER_PREMIUMS = (1.05, 1.2)
DOWN_ENDPOINTS = ("positions", "activities", "open_orders", "account", "order")  # endpoint_down: the reads it hits
DOWN_MINUTES = (5, 15, 40)  # endpoint_down: how long a spell lasts
LEASE_FAIL_MINUTES = (1, 2)  # lease_renew_fail: how long a spell lasts


@dataclass(frozen=True)
class FaultProfile:
    """The probability of each fault, per thing it can hit (an order, a cancel request, an activity, a minute).
    All zero: the wrappers pass everything through."""

    submit_timeout_accepted: float = 0.0
    submit_timeout_lost: float = 0.0
    lookup_lag: float = 0.0  # given submit_timeout_accepted
    listing_lag: float = 0.0  # given lookup_lag
    cancel_lost: float = 0.0
    cancel_late: float = 0.0
    fill_duplicate: float = 0.0
    fill_dropped: float = 0.0
    partial_fill: float = 0.0
    hold: float = 0.0
    rate_limit: float = 0.0  # per (kind of call, minute)
    clock_skew: float = 0.0  # per minute: a spell of SKEW_MINUTES starts
    mark_bad: float = 0.0  # per (symbol, minute)
    quote_stale: float = 0.0  # per (symbol, minute)
    quote_missing: float = 0.0
    quote_zero: float = 0.0
    quote_crossed: float = 0.0
    write_fail: float = 0.0  # per write
    write_timeout: float = 0.0
    write_twice: float = 0.0
    read_fail: float = 0.0  # per (table, minute)
    bar_zero: float = 0.0  # per (symbol, bar minute)
    bar_spike: float = 0.0  # per (symbol, bar minute)
    bar_stale: float = 0.0  # per (symbol, ten-minute block)
    sale_refused: float = 0.0  # per market sale (client order id: each attempt)
    endpoint_down: float = 0.0  # per (endpoint, minute): a spell of DOWN_MINUTES starts
    lease_renew_fail: float = 0.0  # per minute: a spell of LEASE_FAIL_MINUTES starts
    ca_split: float = 0.0  # per (held symbol, session)
    ca_spinoff: float = 0.0
    ca_merger: float = 0.0

    def scaled(self, k: float) -> FaultProfile:
        return replace(self, **{f.name: min(getattr(self, f.name) * k, 1.0) for f in fields(self)})

    def without(self, *names: str) -> FaultProfile:
        """This profile with `names` switched off."""
        unknown = set(names) - {f.name for f in fields(self)}
        if unknown:
            raise ValueError(f"unknown faults: {sorted(unknown)}")
        return replace(self, **dict.fromkeys(names, 0.0))

    def only(self, *names: str) -> FaultProfile:
        """This profile with every fault but `names` switched off."""
        unknown = set(names) - {f.name for f in fields(self)}
        if unknown:
            raise ValueError(f"unknown faults: {sorted(unknown)}")
        return replace(self, **{f.name: 0.0 for f in fields(self) if f.name not in names})


NO_FAULTS = FaultProfile()
# The pull-request profile: each fault is rare enough that most sessions meet two or three, not all at once.
DEFAULT_FAULTS = FaultProfile(
    submit_timeout_accepted=0.10, submit_timeout_lost=0.05, lookup_lag=0.5, listing_lag=0.5, cancel_lost=0.25,
    cancel_late=0.25,
    fill_duplicate=0.10, fill_dropped=0.12, partial_fill=0.10, hold=0.10, rate_limit=0.004, clock_skew=0.002,
    mark_bad=0.004, quote_stale=0.03, quote_missing=0.03, quote_zero=0.02, quote_crossed=0.02,
    write_fail=0.02, write_timeout=0.02, write_twice=0.02, read_fail=0.01,
    sale_refused=0.25, endpoint_down=0.0006, lease_renew_fail=0.01,
    ca_split=0.04, ca_spinoff=0.03, ca_merger=0.02)

SKEW_MINUTES = 6  # a clock-skew spell
LAG_READS = (1, 4)  # lookup_lag: reads by client order id that find nothing
# fill_dropped: how long an activity stays invisible. 300 is past the close and the evening's decision; 1500 is over
# the next session's window (#529; drawn since audit 2, 5.13, #693, fixed both halves)
DROP_MINUTES = (1, 3, 8, 45, 300, 1500)
HOLD_BARS = (1, 2, 4, None)  # hold: bars the venue does not trade the order (None: never)


class Dice:
    """Hash-based draws: the same (seed, fault, key) always gives the same answer, whatever was asked before."""

    def __init__(self, seed: int) -> None:
        self.seed = int(seed)

    def u(self, *key) -> float:
        h = hashlib.blake2b(f"{self.seed}|{'|'.join(str(k) for k in key)}".encode(), digest_size=8).digest()
        return int.from_bytes(h, "big") / 2 ** 64

    def hit(self, p: float, *key) -> bool:
        return p > 0.0 and self.u(*key) < p

    def pick(self, options, *key):
        return options[int(self.u("pick", *key) * len(options)) % len(options)]


@dataclass(frozen=True)
class Forced:
    """A fault a hand-written scenario asks for by name, whatever the dice say: `fault` hits everything whose key
    contains `match` (the empty string: everything). The key of an order is "<client order id>|<symbol>|<side>",
    of an activity "<activity id>|<symbol>|<side>", of a cancel the venue's order id. `param` stands in
    place of the draw that fault would make: the minutes an activity stays hidden, the bars an order is held
    ("never": it does not trade at all), the part of a market order that fills, the reads a lookup lags, and for
    `cancel_lost` how many cancel requests of the order are lost (default 1)."""

    fault: str
    match: str = ""
    param: object = None


def _minute(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M")


def _spell(dice: Dice, p: float, fault: str, key: str, now: datetime, lengths: tuple[int, ...]) -> bool:
    """Is `now` inside a spell of `fault` (about `key`)? A spell starts in a minute with probability `p` and lasts one
    of `lengths` minutes (a hash of its start): the answer depends on the minute only, never on the calls made."""
    if p <= 0.0:
        return False
    t = now.astimezone(UTC).replace(second=0, microsecond=0)
    for back in range(max(lengths)):
        start = _minute(t - timedelta(minutes=back))
        if dice.hit(p, fault, key, start) and back < dice.pick(lengths, fault, key, start):
            return True
    return False


class FaultyBroker(BrokerBase):
    """`SimBroker` as the engine reaches it through a faulty network and a venue that reports late, twice or not
    at all. Everything not overridden here (the calendar, the asset record, `advance`, the simulator's books for
    the test's own checks) is the simulator's.

    `armed`: False passes every call through (the test heals the world before it checks that the engine converges).
    `log`: every fault injected, (minute, fault, what), in order. `on_write(phase, what)`: called before and after
    every write that reaches this wrapper (submit, cancel, replace), for a test's crash points.
    """

    def __init__(self, sim: SimBroker, seed: int, profile: FaultProfile = NO_FAULTS, *,
                 on_write: Callable[[str, str], None] | None = None, forced: tuple[Forced, ...] = ()) -> None:
        if not isinstance(sim, SimBroker):
            raise TypeError("FaultyBroker wraps the simulator only (never the paper adapter)")
        self.sim, self.dice, self.profile, self.forced = sim, Dice(seed), profile, tuple(forced)
        self.name = sim.name
        self.armed = True
        self.on_write = on_write or (lambda phase, what: None)
        self.on_fault: Callable[[str, str], None] = lambda fault, what: None  # a test's trace
        self.log: list[tuple[str, str, str]] = []
        self._lag: dict[str, int] = {}  # client order id -> reads by client id that still find nothing
        self._unlisted: set[str] = set()  # client order ids missing from the open-orders list while they lag
        self._late_cancels: dict[str, datetime] = {}  # order id -> when the cancel was asked
        self._cancel_n: dict[str, int] = {}  # order id -> cancel requests seen
        self._refused_n: dict[tuple[str, str], int] = {}  # (symbol, side) -> forced refusals given (submit_refused)
        self._seen_at: dict[str, datetime] = {}  # activity id -> when it could first have been read
        self._skew: tuple[datetime, float] | None = None  # (until, seconds)
        self.actions: list[dict] = []  # corporate actions applied (audit 2, 5.20), the venue's sourced record
        self.on_action: Callable[[dict], None] = lambda action: None
        self.merged: set[str] = set()  # symbols a cash merger removed: no longer tradable
        self._ca_day: str | None = None  # the last session the corporate actions were drawn for

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.sim, name)

    # ---------------------------------------------------------------- helpers
    def _now(self) -> datetime:
        return self.sim.clk().astimezone(UTC)

    def _note(self, fault: str, what: str) -> None:
        self.log.append((_minute(self._now()), fault, what))
        self.on_fault(fault, what)

    def _forced(self, fault: str, *key) -> Forced | None:
        text = "|".join(str(k) for k in key)
        return next((f for f in self.forced if f.fault == fault and f.match in text), None)

    def _hit(self, fault: str, *key, also: tuple = ()) -> bool:
        """`key`: what the dice are asked about. `also`: more a forced fault may name the thing by."""
        if not self.armed:
            return False
        return self._forced(fault, *key, *also) is not None \
            or self.dice.hit(getattr(self.profile, fault), fault, *key)

    def _param(self, fault: str, options, *key, also: tuple = ()):
        """The fault's own draw (how long, how many), or what a forced fault names."""
        f = self._forced(fault, *key, *also)
        return f.param if f is not None and f.param is not None else self.dice.pick(options, fault, *key)

    def _limited(self, call: str) -> None:
        """A 429 for every call of this kind in this minute; a 503 while this endpoint is in a partial outage."""
        if self._hit("rate_limit", call, _minute(self._now())):
            self._note("rate_limit", call)
            raise BrokerError(429, '{"code":42910000,"message":"rate limit exceeded"}')
        if call in DOWN_ENDPOINTS and self.armed and (
                self._forced("endpoint_down", call, _minute(self._now())) is not None
                or _spell(self.dice, self.profile.endpoint_down, "endpoint_down", call, self._now(), DOWN_MINUTES)):
            self._note("endpoint_down", call)
            raise BrokerError(503, '{"message":"faulty: service unavailable (this endpoint only)"}')

    # ---------------------------------------------------------------- orders: writes
    def submit(self, order: OrderRequest) -> dict:
        coid = str(order.client_order_id)
        what = f"venue.submit {order.symbol} {order.side} {coid}"
        self.on_write("before", what)
        try:
            self._limited("submit")
            also = (order.symbol, order.side)
            if order.symbol in self.merged and order.side == "buy":  # (not a fault: what a merger leaves)
                raise BrokerError(422, f'{{"code":42210000,"message":"asset {order.symbol} is not active"}}')
            refuse = self._forced("submit_refused", coid, *also) if self.armed else None
            if refuse is not None:
                k = (order.symbol, order.side)
                n = self._refused_n[k] = self._refused_n.get(k, 0) + 1
                if n <= int(refuse.param or 1):
                    self._note("submit_refused", f"{coid} refusal {n}")
                    raise BrokerError(403, '{"code":40310000,"message":"faulty: the venue refused the order"}')
            if order.side == "sell" and order.type == "market" and self._hit("sale_refused", coid, also=also):
                self._note("sale_refused", coid)
                raise BrokerError(403, '{"code":40310000,"message":"faulty: the venue refused the sale"}')
            if self.sim.order_by_client_id(coid) is None:  # the faults of an order are decided once, at its first try
                if self._hit("submit_timeout_lost", coid, also=also):
                    self._note("submit_timeout_lost", coid)
                    raise BrokerError(0, f"faulty: the order for {order.symbol} never reached the venue")
                if order.type == "market" and self._hit("partial_fill", coid, also=also):
                    share = self._param("partial_fill", (0.25, 0.5, 0.75), coid, also=also)
                    part = max(1.0, float(int(float(order.qty) * share)))
                    if part < float(order.qty):
                        self._note("partial_fill", f"{coid} {part:g} of {order.qty:g}")
                        self.sim.script.fills(order.symbol, [part])
                elif self._hit("hold", coid, also=also):
                    bars = self._param("hold", HOLD_BARS, coid, also=also)
                    bars = None if bars == "never" else bars
                    self._note("hold", f"{coid} {bars} bars")
                    self.sim.script.hold(order.symbol, bars)
            try:
                o = self.sim.submit(order)
            finally:
                self.sim.script._plans.clear()  # a plan the venue refused the order before taking must not leak
            if self._hit("submit_timeout_accepted", coid, also=also):
                self._note("submit_timeout_accepted", coid)
                if self._hit("lookup_lag", coid, also=also):
                    self._lag[coid] = self._param("lookup_lag", LAG_READS, coid, also=also)
                    self._note("lookup_lag", f"{coid} {self._lag[coid]} reads")
                    if self._hit("listing_lag", coid, also=also):
                        self._note("listing_lag", coid)
                        self._unlisted.add(coid)
                raise BrokerError(0, f"faulty: no answer to the order for {order.symbol}")
            return o
        finally:
            self.on_write("after", what)

    def cancel(self, order_id: str) -> None:
        what = f"venue.cancel {order_id}"
        self.on_write("before", what)
        try:
            self._limited("cancel")
            n = self._cancel_n[order_id] = self._cancel_n.get(order_id, 0) + 1
            f = self._forced("cancel_lost", order_id)
            if (f is not None and n <= int(f.param or 1)) if f is not None and self.armed \
                    else self._hit("cancel_lost", order_id, n):
                self._note("cancel_lost", f"{order_id} request {n}")
                raise BrokerError(0, "faulty: the cancel never reached the venue")
            o = self.sim.orders.get(order_id)
            if o is not None and o["status"] in WORKING and order_id not in self._late_cancels \
                    and self._hit("cancel_late", order_id):
                self._note("cancel_late", order_id)
                self._late_cancels[order_id] = self._now()
                return
            if order_id in self._late_cancels:
                return  # the venue has the request already
            self.sim.cancel(order_id)
        finally:
            self.on_write("after", what)

    def replace(self, order_id: str, **kw) -> dict:
        what = f"venue.replace {order_id}"
        self.on_write("before", what)
        try:
            self._limited("replace")
            return self.sim.replace(order_id, **kw)
        finally:
            self.on_write("after", what)

    def close_position(self, symbol: str) -> dict | None:
        what = f"venue.close_position {symbol}"
        self.on_write("before", what)
        try:
            self._limited("close_position")
            return self.sim.close_position(symbol)
        finally:
            self.on_write("after", what)

    # ---------------------------------------------------------------- orders: reads
    def _shown(self, o: dict | None) -> dict | None:
        """The order as the venue reports it: `pending_cancel` while a late cancel is on its way."""
        if o is not None and o.get("id") in self._late_cancels and o.get("status") in WORKING:
            return {**o, "status": "pending_cancel"}
        return o

    def order(self, order_id: str) -> dict:
        self._limited("order")
        return self._shown(self.sim.order(order_id))

    def order_by_client_id(self, coid: str) -> dict | None:
        self._limited("order_by_client_id")
        left = self._lag.get(coid, 0)
        if left > 0 and self.armed:
            self._lag[coid] = left - 1
            return None
        self._lag.pop(coid, None)
        return self._shown(self.sim.order_by_client_id(coid))

    def open_orders(self) -> list[dict]:
        self._limited("open_orders")
        lagging = {c for c in self._unlisted if self._lag.get(c, 0) > 0} if self.armed else set()
        return [self._shown(o) for o in self.sim.open_orders() if o.get("client_order_id") not in lagging]

    def activities(self, after: datetime | None = None, types: tuple[str, ...] = ("FILL",)) -> list[dict]:
        self._limited("activities")
        rows = self.sim.activities(after, types=types)
        if not self.armed:
            return rows
        now, out = self._now(), []
        for a in rows:
            aid = str(a.get("id"))
            if a.get("activity_type") != "FILL":
                out.append(a)
                continue
            first = self._seen_at.setdefault(aid, now)
            also = (a.get("symbol"), a.get("side"))
            if self._hit("fill_dropped", aid, also=also):
                wait = self._param("fill_dropped", DROP_MINUTES, aid, also=also)
                if now < first + timedelta(minutes=wait):
                    if first == now:
                        self._note("fill_dropped", f"{aid} for {wait} min")
                    continue
            out.append(a)
            if self._hit("fill_duplicate", aid):
                if first == now:
                    self._note("fill_duplicate", aid)
                out.append(dict(a))
        return out

    # ---------------------------------------------------------------- the book and the clock
    def positions(self) -> dict[str, dict]:
        self._limited("positions")
        out = {}
        minute = _minute(self._now())
        for s, p in self.sim.positions().items():
            if "current_price" in p and self._hit("mark_bad", s, minute):
                how = self.dice.pick(("missing", "zero", "far"), "mark", s, minute)
                self._note("mark_bad", f"{s} {how}")
                px = p.get("current_price")
                p = {**p, "current_price": None if how == "missing" else "0" if how == "zero"
                     else None if px is None else str(round(float(px) * 0.6, 2))}
            out[s] = p
        return out

    def account(self) -> dict:
        self._limited("account")
        return self.sim.account()

    def clock(self) -> dict:
        self._limited("clock")
        clk = dict(self.sim.clock())
        now = self._now()
        if self._skew is not None and now >= self._skew[0]:
            self._skew = None
        if self._skew is None and self._hit("clock_skew", _minute(now)):
            secs = self.dice.pick((-240.0, -45.0, 30.0, 400.0), "skew", _minute(now))
            self._skew = (now + timedelta(minutes=SKEW_MINUTES), secs)
            self._note("clock_skew", f"{secs:+.0f} s for {SKEW_MINUTES} min")
        if self._skew is not None and self.armed and clk.get("timestamp"):
            t = datetime.fromisoformat(str(clk["timestamp"])) + timedelta(seconds=self._skew[1])
            clk["timestamp"] = t.isoformat()
        return clk

    # ---------------------------------------------------------------- the venue trades a bar
    def advance(self, t) -> None:
        """One bar at the simulator; then the cancels the venue was slow to act on (asked before this bar began)
        take effect, on what the bar left of the order. Before the first bar of a session: its corporate actions."""
        bar = t if isinstance(t, datetime) else t.to_pydatetime()
        bar = bar.astimezone(UTC) if bar.tzinfo else bar.replace(tzinfo=UTC)
        day = str(pd.Timestamp(bar).tz_convert("America/New_York").date())
        if day != self._ca_day:
            self._ca_day = day
            self._corporate_actions(day, bar)
        self.sim.advance(t)
        for oid, asked in list(self._late_cancels.items()):
            if asked <= bar + timedelta(minutes=1):
                self.sim.cancel(oid)
                del self._late_cancels[oid]

    # ---------------------------------------------------------------- corporate actions (audit 2, 5.20)
    def _corporate_actions(self, day: str, bar: datetime) -> None:
        """Alpaca's corporate actions of `day`, applied before its first bar to the positions held then: at most one
        per symbol, decided by the dice on (fault, symbol, day) and in this order: merger, spin-off, split."""
        if not self.armed:
            return
        for s in sorted(self.sim.pos):
            if float(self.sim.pos[s].get("qty") or 0) <= 0 or s in self.merged:
                continue
            px = self.sim.bars.last_close(s, bar)
            if px is None:
                continue
            if self._hit("ca_merger", s, day):
                price = round(px * self._param("ca_merger", MERGER_PREMIUMS, s, day), 2)
                self.sim.cash_merger(s, price=price, day=day)
                self.merged.add(s)
                self._action({"kind": "cash_merger", "symbol": s, "new_symbol": "", "on": day, "ratio": price})
            elif self._hit("ca_spinoff", s, day) and f"{s}W" not in self.sim.pos:
                ratio = self._param("ca_spinoff", SPINOFF_RATIOS, s, day)
                new = f"{s}W"
                f = self.sim.bars.frames.get(s)
                if f is not None and new not in self.sim.bars.frames:
                    k = SPINOFF_BASIS / ratio
                    self.sim.bars.frames[new] = f.assign(**{c: f[c] * k for c in ("o", "h", "l", "c", "vw")})
                self.sim.spin_off(s, new, ratio=ratio, basis_share=SPINOFF_BASIS, day=day)
                self._action({"kind": "spin_off", "symbol": s, "new_symbol": new, "on": day, "ratio": ratio})
            elif self._hit("ca_split", s, day):
                r = self._param("ca_split", SPLIT_RATIOS, s, day)
                p = self.sim.pos[s]
                before = float(p["qty"])
                p["qty"] = _whole(before * r)
                p["avg_entry_price"] = float(p["avg_entry_price"]) / r
                if p.get("cost_basis") not in (None, ""):
                    p["cost_basis"] = str(float(p["cost_basis"]))  # unchanged: the same money in more shares
                f = self.sim.bars.frames.get(s)
                if f is not None:  # the feed and the venue are split-adjusted from here on (every bar)
                    self.sim.bars.frames[s] = f.assign(**{c: f[c] / r for c in ("o", "h", "l", "c", "vw")},
                                                       v=f["v"] * r)
                self.sim.non_trade.append({"activity_type": "SPLIT", "id": f"{day}::split{len(self.sim.non_trade) + 1}",
                                           "date": day, "symbol": s, "qty": str(_whole(before * (r - 1)))})
                self._action({"kind": "split", "symbol": s, "new_symbol": "", "on": day, "ratio": r})

    def _action(self, a: dict) -> None:
        a["ref"] = f"faulty-ca:{a['kind']}:{a['symbol']}:{a['on']}"
        self.actions.append(a)
        self._note({"cash_merger": "ca_merger", "spin_off": "ca_spinoff", "split": "ca_split"}[a["kind"]],
                   f"{a['symbol']} {a['ratio']:g}" + (f" {a['new_symbol']}" if a["new_symbol"] else ""))
        self.on_action(a)

    def heal(self) -> None:
        """Faults off, and what was pending arrives: late cancels take effect, lagging lookups answer."""
        self.armed = False
        for oid in list(self._late_cancels):
            self.sim.cancel(oid)
        self._late_cancels.clear()
        self._lag.clear()
        self._unlisted.clear()
        self._skew = None


def _whole(x: float):
    return int(round(x)) if abs(x - round(x)) < 1e-9 else x


class FaultyData:
    """The market-data client with faulty latest quotes. `quotes_within(symbols, deadline_s)` is the read the
    pre-send gate uses (engine/live_daily.presend_quotes): with this wrapper the engine has a gate, as live.
    Everything else (bars, news) is the wrapped client's: a simulated venue has no bar from the future to leak."""

    def __init__(self, data, seed: int, profile: FaultProfile = NO_FAULTS) -> None:
        self.data, self.dice, self.profile = data, Dice(seed), profile
        self.armed = True
        self.log: list[tuple[str, str, str]] = []
        # a hand-written scenario's bar faults: (symbol, fault, from, until) in UTC, on bars that start in [from, until)
        self.bar_faults: list[tuple[str, str, datetime, datetime]] = []

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.data, name)

    def _faulty(self, symbols) -> dict:
        out = dict(self.data.latest_quotes(list(symbols)))
        if not self.armed:
            return out
        now = self.data.clock().astimezone(UTC)
        minute = _minute(now)
        for s in sorted(out):
            for fault in DATA_FAULTS:
                if not self.dice.hit(getattr(self.profile, fault), fault, s, minute):
                    continue
                self.log.append((minute, fault, s))
                q = dict(out[s])
                if fault == "quote_missing":
                    out.pop(s)
                    break
                if fault == "quote_stale":
                    q["t"] = (now - timedelta(minutes=45)).isoformat()
                elif fault == "quote_zero":
                    q["bp"], q["ap"] = 0.0, 0.0
                else:  # crossed: the bid above the ask
                    q["bp"], q["ap"] = q["ap"] * 1.01, q["bp"] * 0.99
                out[s] = q
                break
        return out

    def latest_quotes(self, symbols):
        return self._faulty(symbols)

    def _bar_fault(self, fault: str, s: str, ts: datetime, key: str) -> bool:
        if any(sym == s and f == fault and a <= ts < b for sym, f, a, b in self.bar_faults):
            return True
        return self.dice.hit(getattr(self.profile, fault), fault, s, key)

    def bars(self, symbols, start, end, feed="iex", timeframe="1Min", adjustment="split"):
        """Today's minute bars (feed IEX, 1Min) with the bar faults; anything else is the wrapped client's as it is.
        A bar is faulted by its own start minute, so a re-read of late bars sees the same fault."""
        out = self.data.bars(symbols, start, end, feed=feed, timeframe=timeframe, adjustment=adjustment)
        if not self.armed or feed != "iex" or timeframe != "1Min" or not (
                self.bar_faults or any(getattr(self.profile, f) for f in BAR_FAULTS)):
            return out
        for s in sorted(out):
            df = out[s]
            if df is None or not len(df):
                continue
            df = df.copy()
            keep, cols = [], [c for c in ("o", "h", "l", "c", "vw") if c in df.columns]
            for i, t in enumerate(pd.to_datetime(df["ts"], utc=True)):
                ts = t.to_pydatetime()
                block = _minute(ts.replace(minute=ts.minute - ts.minute % BAR_STALE_BLOCK_MIN))
                if self._bar_fault("bar_stale", s, ts, block):
                    self.log.append((_minute(ts), "bar_stale", s))
                    continue
                keep.append(i)
                if self._bar_fault("bar_zero", s, ts, _minute(ts)):
                    self.log.append((_minute(ts), "bar_zero", s))
                    df.loc[df.index[i], cols] = 0.0
                elif self._bar_fault("bar_spike", s, ts, _minute(ts)):
                    self.log.append((_minute(ts), "bar_spike", s))
                    df.loc[df.index[i], cols] = df.loc[df.index[i], cols] * BAR_SPIKE
            out[s] = df.iloc[keep].reset_index(drop=True)
        return out

    def quotes_within(self, symbols, deadline_s: float):
        return self._faulty(symbols)


class FaultyStore:
    """Supabase as the engine reaches it: a write can fail, time out after it was applied, or arrive twice; a read
    can fail. `inner` is the test's replica (any object with the engine/store.Store interface); `error` builds the
    exception a failed call raises (default: engine/store.StoreError, imported late so that this module has no
    engine import of its own). The lease calls and everything else pass through."""

    WRITES = ("insert", "upsert", "update")

    def __init__(self, inner, seed: int, profile: FaultProfile = NO_FAULTS, *,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 on_write: Callable[[str, str], None] | None = None, error=None) -> None:
        self.inner, self.dice, self.profile, self.now = inner, Dice(seed), profile, now
        self.armed = True
        self.on_write = on_write or (lambda phase, what: None)
        self.log: list[tuple[str, str, str]] = []
        self.writes = 0
        if error is None:
            from kaibot.engine.store import StoreError as error
        self.error = error

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self.inner, name)

    def _write(self, op: str, table: str, call):
        self.writes += 1
        n, minute = self.writes, _minute(self.now())
        what = f"store.{op} {table}"
        self.on_write("before", what)
        try:
            if self.armed and self.dice.hit(self.profile.write_fail, "write_fail", n):
                self.log.append((minute, "write_fail", what))
                raise self.error(f"faulty: {op} {table} -> 503 service unavailable")
            out = call()
            if self.armed and self.dice.hit(self.profile.write_twice, "write_twice", n):
                self.log.append((minute, "write_twice", what))
                with contextlib.suppress(Exception):  # refused the second time (a unique key): nobody hears of it
                    call()  # the same request again; the client reads the first answer only
            if self.armed and self.dice.hit(self.profile.write_timeout, "write_timeout", n):
                self.log.append((minute, "write_timeout", what))
                raise self.error(f"faulty: {op} {table} -> timed out (the write was applied)")
            return out
        finally:
            self.on_write("after", what)

    def insert(self, table, row, returning=True):
        return self._write("insert", table, lambda: self.inner.insert(table, row, returning=returning))

    def upsert(self, table, row, on_conflict):
        return self._write("upsert", table, lambda: self.inner.upsert(table, row, on_conflict))

    def update(self, table, match, values):
        return self._write("update", table, lambda: self.inner.update(table, match, values))

    def select(self, table, params):
        if self.armed and self.dice.hit(self.profile.read_fail, "read_fail", table, _minute(self.now())):
            self.log.append((_minute(self.now()), "read_fail", table))
            raise self.error(f"faulty: GET {table} -> 503 service unavailable")
        return self.inner.select(table, params)

    def heartbeat(self, process, detail):
        return self._write("heartbeat", "heartbeat", lambda: self.inner.heartbeat(process, detail))

    def pending_commands(self):
        if self.armed and self.dice.hit(self.profile.read_fail, "read_fail", "command", _minute(self.now())):
            self.log.append((_minute(self.now()), "read_fail", "command"))
            raise self.error("faulty: GET command -> 503 service unavailable")
        return self.inner.pending_commands()

    def lease_renew(self, *a, **kw):
        """The engine lease's renewal (engine/lease.py): fails, nothing renewed, through a lease_renew_fail spell."""
        now = self.now()
        if self.armed and _spell(self.dice, self.profile.lease_renew_fail, "lease_renew_fail", "", now,
                                 LEASE_FAIL_MINUTES):
            self.log.append((_minute(now), "lease_renew_fail", "lease_renew"))
            raise self.error("faulty: rpc engine_lease_renew -> 503 service unavailable")
        return self.inner.lease_renew(*a, **kw)

    def quick(self, timeout: float = 5.0):
        return self
