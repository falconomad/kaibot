"""SimBroker: the simulated venue behind the `Broker` protocol (decision 15, section 3; issue #115).

Promoted from tests/fakes.FakeBroker. Orders fill on stored 1-minute bars by the replay's rules (replay/sim.py),
priced by the cost model (broker/shadow_costs.py), and every submission is checked against Alpaca's real order
rules, so an order the paper account would refuse is refused in simulation too.

Fill rules. One `advance(t)` trades the bar that starts at `t`:
- a market order fills at the open of the first bar that starts at or after the minute it was submitted in:
  "decide at the close of minute m, fill at the open of m + 1". Buys pay, sells give up, half the modelled
  spread plus impact;
- a limit order fills at its limit once a bar trades THROUGH it by a cent (no price improvement, no cost);
- a stop is elected when a bar touches it and is then a market order: a sell stop fills at
  min(stop, bar open), so a stop that is gapped through fills at the open, less market costs;
- bracket and OTO legs are `held` until the entry is COMPLETELY filled; from that bar on they work, and the stop
  is checked before the target (the stop wins a tie);
- `opg` orders fill at the session's first print and `cls` orders at its last, at half a spread;
- a minute without trades is the session's last close carried forward with zero volume (as
  replay.data.from_frame); before a symbol's first print of the session nothing fills.

Alpaca's rules, checked when an order is submitted (docs/research/engine_v3/notes_engine_architecture.md):
- `opg` orders are accepted only from 19:00 to 09:28 ET, `cls` orders are rejected from 15:50 to 19:00 ET;
- fractional orders are day orders only; bracket and OTO orders are day or GTC only;
- a sell needs shares that no other working sell order holds (Kaibot never shorts);
- with a cash book, a buy needs the cash;
and as time passes: day orders (and day legs) expire at the session's close, GTC orders and legs are cancelled
90 days after creation at 16:15 ET.

Deterministic: time comes from the injected clock, prices from the bars, ids from a counter; no randomness.
`script` lets a test make the venue reject, time out, fill in parts, sit on an order or report a fill twice;
`down` cuts the network (every call raises BrokerError 0). `replace_new_id` makes `replace` behave as Alpaca's
PATCH does (the old order becomes `replaced`, a new order id works on); `replaced_leg` says where a replaced
bracket leg is listed then, under its parent ("nested") or as an order of its own ("detached"): which one Alpaca
does is the open question of issue #11, so the order manager is tested against both (#118).
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kaibot.broker.protocol import (
    ORDER_CLASSES,
    ORDER_TYPES,
    SIDES,
    TIMES_IN_FORCE,
    BrokerBase,
    BrokerError,
    OrderRequest,
)
from kaibot.broker.shadow_costs import buy_px, half_spread, sell_fees, sell_px
from kaibot.replay.history import Bar, BarHistory

NY = ZoneInfo("America/New_York")
WORKING = ("new", "accepted", "pending_new", "partially_filled")
REGULAR_HOURS = ("09:30", "16:00")
OPG_FROM, OPG_UNTIL = time(19, 0), time(9, 28)  # accepted from 19:00 until 09:28 ET
CLS_UNTIL, CLS_FROM = time(15, 50), time(19, 0)  # rejected from 15:50 until 19:00 ET
GTC_DAYS, GTC_CANCEL_AT = 90, time(16, 15)
THROUGH = 0.01  # a limit fills only when the bar trades through it by a cent


@dataclass(frozen=True)
class ShadowCosts:
    """Cost model v1, applied to simulated fills exactly as replay/sim.py applies it."""

    def market(self, side: str, price: float, qty: float, sigma_1m: float, vol_1m: float) -> float:
        return (buy_px if side == "buy" else sell_px)(price, qty, sigma_1m, vol_1m)

    def auction(self, side: str, price: float) -> float:
        """Opening and closing prints: half the modelled spread, no impact (as replay/spy_momentum.py)."""
        return price + half_spread(price) if side == "buy" else price - half_spread(price)

    def fees(self, side: str, qty: float, price: float) -> float:
        return sell_fees(qty, price) if side == "sell" else 0.0


COSTS_V1 = ShadowCosts()


@dataclass(frozen=True)
class AuctionImpactCosts(ShadowCosts):
    """Cost model v1 at the opening and closing prints as the momentum pre-registrations price a daily rule's fill
    (docs/prereg/trend_momentum.md section 4: half a spread a side PLUS the model's impact, `sim.buy_px` /
    `sim.sell_px`), with `mult` scaling spread and impact (2x / 3x cost runs; regulatory fees are not scaled).
    Issue #161. Opt-in: `COSTS_V1` keeps pricing the prints at half a spread, so every other run is unchanged.

    `sigma(symbol, day)`: the 1-minute volatility the impact term uses for a fill on `day` (the daily path gives
    the 20 split-adjusted daily returns before it, as the research harness does); the volume is the print's bar
    volume (the first or the last minute)."""

    sigma: Callable[[str, date], float] | None = None
    mult: float = 1.0

    def price(self, side: str, mid: float, qty: float, sigma: float, vol: float) -> float:
        px = (buy_px if side == "buy" else sell_px)(mid, qty, sigma, vol)
        return px if self.mult == 1.0 else mid + (px - mid) * self.mult

    def auction_fill(self, side: str, price: float, qty: float, symbol: str, day: date, vol: float) -> float:
        sig = self.sigma(symbol, day) if self.sigma is not None else 0.0
        vol = float(vol) if math.isfinite(vol) and vol > 0 else 1.0
        return self.price(side, price, qty, sig, vol)


@dataclass
class _Plan:
    kind: str  # reject | timeout | fills | hold | duplicate
    symbol: str | None
    times: int = 1
    status: int = 403
    message: str = ""
    accepted: bool = True
    parts: list[float] = field(default_factory=list)
    bars: int | None = None


class Script:
    """What a test tells the venue to do with the next order (for `symbol`, or any symbol when None)."""

    def __init__(self):
        self._plans: list[_Plan] = []

    def reject(self, symbol: str | None = None, status: int = 403, message: str = "insufficient buying power",
               times: int = 1) -> None:
        """The next submission is refused with `status` (403 or 422) and nothing reaches the book."""
        self._plans.append(_Plan("reject", symbol, times, status=status, message=message))

    def timeout(self, symbol: str | None = None, accepted: bool = True, times: int = 1) -> None:
        """The next submission gets no answer (BrokerError status 0). With `accepted` the order IS at the venue,
        as Alpaca warns: "the order may have been sent to the market for execution"."""
        self._plans.append(_Plan("timeout", symbol, times, accepted=accepted))

    def fills(self, symbol: str | None, parts: list[float]) -> None:
        """The next market order fills in these share counts, one per bar; what is left over never fills."""
        self._plans.append(_Plan("fills", symbol, parts=list(parts)))

    def hold(self, symbol: str | None = None, bars: int | None = None) -> None:
        """The next order does not trade for `bars` bars (None: never), like a slow or halted venue."""
        self._plans.append(_Plan("hold", symbol, bars=bars))

    def duplicate(self, symbol: str | None = None) -> None:
        """Every fill of the next order is reported twice in the account activities (same activity id), as a
        replayed websocket or a repeated page would."""
        self._plans.append(_Plan("duplicate", symbol))

    def _take(self, symbol: str, kinds: tuple[str, ...]) -> _Plan | None:
        for p in self._plans:
            if p.kind in kinds and p.symbol in (None, symbol):
                p.times -= 1
                if p.times <= 0:
                    self._plans.remove(p)
                return p
        return None


class SimBroker(BrokerBase):
    name = "sim"
    strict_time = True  # bars trade once, in order, and never fill an order submitted after they began

    def __init__(self, bars: BarHistory, clock: Callable[[], datetime], *, costs: ShadowCosts | None = COSTS_V1,
                 cash: float | None = 100_000.0, calendar: Mapping[str, tuple[str, str]] | None = None,
                 cash_at: str = "submit", replace_new_id: bool = False, replaced_leg: str = "nested"):
        """`costs=None` fills at raw bar prices; `cash=None` keeps no cash book (the account then reports no
        buying power); `calendar` {date: (open, close)} in ET, by default every date with bars, 09:30-16:00.

        `cash_at`: when a buy must fit in the cash. "submit" (default): at submission, at the last close.
        "fill" (the daily path, #161 / Amendment 3 of the momentum pre-registrations): an opening or closing
        auction buy is checked at its print, after the print's earlier fills (sales first, as sent); a buy
        that does not fit is cut to the whole shares that fit at the print's price, re-priced for that quantity
        (the rest is cancelled), so the cash book never goes below zero. Every other buy is still checked at
        submission (review of #164)."""
        if cash_at not in ("submit", "fill"):
            raise ValueError("cash_at is submit or fill")
        if replaced_leg not in ("nested", "detached"):
            raise ValueError("replaced_leg is nested or detached")
        self.replace_new_id, self.replaced_leg = replace_new_id, replaced_leg
        self.down = False  # a network cut: every call raises BrokerError(0) until it is False again
        self.calls: list[str] = []  # every write the venue received (submit / cancel / replace), for drills
        self._dup: set[str] = set()
        self._fill_ts: list[datetime] = []  # transaction time of each row of `fills`
        self._gone: dict[str, dict] = {}  # legs a nested replace took out of their parent's list
        self.cash_at = cash_at
        self.bars, self.clk = bars, clock
        self.costs = costs
        self.cash = cash
        self.orders: dict[str, dict] = {}  # entry / standalone orders by id; exit legs are nested under "legs"
        self.pos: dict[str, dict] = {}
        self.fills: list[dict] = []
        self.non_trade: list[dict] = []  # dividends and splits a test books on the account (Alpaca's shape)
        self.script = Script()
        self.ids = itertools.count(1)
        self._calendar = dict(calendar) if calendar is not None else None
        self._sessions: list[str] | None = None
        self._plans: dict[str, _Plan] = {}
        self._last_bar: datetime | None = None
        self._live: dict[str, dict] = {}
        self._seen = 0

    def _net(self) -> None:
        if self.down:
            raise BrokerError(0, "sim: network down")

    # ================= time =================
    def _days(self) -> list[str]:
        if self._sessions is None:
            self._sessions = sorted(self._calendar) if self._calendar is not None else self.bars.sessions()
        return self._sessions

    def _hours(self, day: str) -> tuple[str, str] | None:
        if self._calendar is not None:
            return self._calendar.get(day)
        days = self._days()
        i = bisect.bisect_left(days, day)
        return REGULAR_HOURS if i < len(days) and days[i] == day else None

    def session(self, day: str) -> tuple[datetime, datetime] | None:
        """(open, close) of a session as UTC datetimes; None when the market is closed that day."""
        h = self._hours(day)
        if h is None:
            return None
        d = date.fromisoformat(day)
        return tuple(datetime.combine(d, time.fromisoformat(x), NY).astimezone(UTC) for x in h)

    def calendar(self, day: str) -> dict | None:
        h = self._hours(day)
        return None if h is None else {"date": day, "open": h[0], "close": h[1]}

    def _next_session(self, now: datetime, still_open: bool) -> tuple[datetime, datetime] | None:
        """The first session that has not closed yet (`still_open`) or has not opened yet."""
        days = self._days()
        for day in days[bisect.bisect_left(days, str(now.astimezone(NY).date())):]:
            s = self.session(day)
            if s is not None and (now < s[1] if still_open else now < s[0]):
                return s
        return None

    def clock(self) -> dict:
        self._net()
        now = _utc(self.clk())
        self._expire(now)
        cur = self._next_session(now, still_open=True)
        is_open = cur is not None and cur[0] <= now
        nxt = self._next_session(now, still_open=False)
        if nxt is None:  # past the last stored session: the next weekday, so callers still get a time
            d = now.astimezone(NY).date() + timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            nxt = (datetime.combine(d, time(9, 30), NY).astimezone(UTC),
                   datetime.combine(d, time(16), NY).astimezone(UTC))
        close = cur[1] if is_open else nxt[1]
        return {"timestamp": now.astimezone(NY).isoformat(), "is_open": bool(is_open),
                "next_open": nxt[0].astimezone(NY).isoformat(), "next_close": close.astimezone(NY).isoformat()}

    # ================= account =================
    def account(self) -> dict:
        self._net()
        if self.cash is None:
            return {"status": "ACTIVE"}
        now = _utc(self.clk())
        value = sum(float(p["qty"]) * (self.bars.last_close(s, now) or float(p["avg_entry_price"]))
                    for s, p in self.pos.items())
        return {"status": "ACTIVE", "cash": str(self.cash), "equity": str(self.cash + value),
                "buying_power": str(max(self.cash, 0.0))}

    def positions(self) -> dict[str, dict]:
        self._net()
        return dict(self.pos)

    def asset(self, symbol: str) -> dict | None:
        """S68: the simulator has no listing data, so every custom ticker is unknown and refused (fail closed)."""
        return None

    def activities(self, after: datetime | None = None, types: tuple[str, ...] = ("FILL",)) -> list[dict]:
        """Fills, plus the non-trade activities a test puts in `non_trade` (dividends, splits) of the types asked."""
        self._net()
        # fills are booked in time order: start after `after` (unless a test wrote rows into `fills` itself)
        if after is not None and "FILL" in types and len(self._fill_ts) == len(self.fills):
            k = bisect.bisect_right(self._fill_ts, _utc(after))
            rows = self.fills[k:] + [a for a in self.non_trade if a.get("activity_type") in types]
        else:
            rows = (list(self.fills) if "FILL" in types else []) + [
                a for a in self.non_trade if a.get("activity_type") in types]
        if after is None:
            return rows
        def when(f: dict) -> datetime:
            t = datetime.fromisoformat(f.get("transaction_time") or f["date"])
            return t if t.tzinfo else t.replace(tzinfo=UTC)

        return [f for f in rows if when(f) > after]

    # ================= orders: reads =================
    def open_orders(self) -> list[dict]:
        self._net()
        self._expire(_utc(self.clk()))
        return [o for o in self.orders.values() if o["status"] in WORKING]

    def order(self, order_id: str) -> dict:
        self._net()
        self._expire(_utc(self.clk()))
        o = self.orders.get(order_id)
        if o is None:
            _, o = self._leg(order_id)
        if o is None:
            o = self._gone.get(order_id)
        if o is None:
            raise BrokerError(404, f"order not found: {order_id}")
        return o

    def order_by_client_id(self, coid: str) -> dict | None:
        self._net()
        return next((o for o in self.orders.values() if o.get("client_order_id") == coid), None)

    def _leg(self, leg_id: str) -> tuple[dict | None, dict | None]:
        for o in self.orders.values():
            for leg in o.get("legs") or []:
                if leg["id"] == leg_id:
                    return o, leg
        return None, None

    # ================= orders: writes =================
    def submit(self, order: OrderRequest) -> dict:
        self._net()
        self.calls.append(f"submit {order.client_order_id}")
        now = _utc(self.clk())
        self._expire(now)
        plan = self.script._take(order.symbol, ("reject", "timeout"))
        if plan is not None and plan.kind == "reject":
            raise BrokerError(plan.status, plan.message)
        if order.client_order_id:
            existing = self.order_by_client_id(order.client_order_id)
            if existing is not None:
                return self._duplicate(existing, order)
        self.check(order, now)
        if plan is not None and plan.kind == "timeout":
            if plan.accepted:
                self._place(order, now)
            raise BrokerError(0, f"sim: no answer to the order for {order.symbol}")
        return self._place(order, now)

    def _duplicate(self, existing: dict, order: OrderRequest) -> dict:
        """Alpaca refuses a reused client_order_id (422) and the paper adapter then returns the order that is
        already there. The simulator does the same in one step."""
        return existing

    def check(self, r: OrderRequest, now: datetime) -> None:
        """Alpaca's submission rules. Raises BrokerError (422 invalid, 403 not enough shares or cash)."""
        if r.side not in SIDES or r.type not in ORDER_TYPES or r.time_in_force not in TIMES_IN_FORCE \
                or r.order_class not in ORDER_CLASSES:
            raise BrokerError(422, f"invalid order: {r}")
        if r.type == "stop_limit" or r.order_class == "oco" or r.time_in_force in ("ioc", "fok"):
            raise BrokerError(422, "sim: stop-limit, OCO, IOC and FOK orders are not modelled")
        if not (r.qty > 0 and math.isfinite(r.qty)):
            raise BrokerError(422, "qty must be > 0")
        if (r.type == "limit") != (r.limit_price is not None) or (r.type == "stop") != (r.stop_price is not None):
            raise BrokerError(422, f"{r.type} order with the wrong price fields")
        t = now.astimezone(NY).time()
        if r.time_in_force == "opg" and OPG_UNTIL <= t < OPG_FROM:
            raise BrokerError(422, "opg orders must be submitted after 7:00pm and before 9:28am")
        if r.time_in_force == "cls" and CLS_UNTIL <= t < CLS_FROM:
            raise BrokerError(422, "cls orders must be submitted before 3:50pm or after 7:00pm")
        if r.fractional and r.time_in_force != "day":
            raise BrokerError(422, "fractional orders must be DAY orders")
        legs = (r.take_profit is not None, r.stop_loss is not None)
        if r.order_class == "simple" and any(legs):
            raise BrokerError(422, "take_profit / stop_loss need order_class bracket or oto")
        if r.order_class != "simple":
            if r.time_in_force not in ("day", "gtc"):
                raise BrokerError(422, "bracket orders must be DAY or GTC")
            if r.order_class == "bracket" and not all(legs) or r.order_class == "oto" and sum(legs) != 1:
                raise BrokerError(422, f"{r.order_class} order with the wrong legs")
        if r.side == "sell":
            free = self._free_shares(r.symbol)
            if r.qty > free + 1e-9:
                raise BrokerError(403, f"insufficient qty available for order (requested: {r.qty:g}, "
                                       f"available: {free:g})")
        elif self.cash is not None and (self.cash_at == "submit" or r.time_in_force not in ("cls", "opg")):
            px = r.limit_price or r.stop_price or self.bars.last_close(r.symbol, now)
            if px is not None and r.qty * px > self.cash + 1e-9:
                raise BrokerError(403, "insufficient buying power")

    def _free_shares(self, symbol: str) -> float:
        """Shares of the position that no working sell order or leg holds."""
        held = 0.0
        for o in self._live_orders():
            if o["symbol"] != symbol:
                continue
            if o["side"] == "sell" and o["status"] in WORKING:
                held += float(o["qty"]) - float(o.get("filled_qty") or 0)
            if o["side"] == "buy" and any(x["status"] == "new" for x in o.get("legs") or []):
                held += float(o.get("filled_qty") or o["qty"])
        p = self.pos.get(symbol)
        return max((float(p["qty"]) if p else 0.0) - held, 0.0)

    def _place(self, r: OrderRequest, now: datetime) -> dict:
        oid = f"o{next(self.ids)}"
        legs = []
        if r.stop_loss is not None:
            legs.append({"id": f"{oid}s", "type": "stop", "stop_price": f"{r.stop_loss:.2f}", "status": "held"})
        if r.take_profit is not None:
            legs.append({"id": f"{oid}t", "type": "limit", "limit_price": f"{r.take_profit:.2f}", "status": "held"})
        for leg in legs:
            leg.update(symbol=r.symbol, side="sell" if r.side == "buy" else "buy", qty=r.qty)
        o = {"id": oid, "client_order_id": r.client_order_id, "symbol": r.symbol, "qty": r.qty, "side": r.side,
             "type": r.type, "status": "new", "legs": legs, "time_in_force": r.time_in_force,
             "order_class": r.order_class, "filled_qty": 0, "filled_avg_price": None, "filled_at": None,
             "created_at": now.isoformat(), "submitted_at": now.isoformat(),
             "expires_at": _iso(self._expiry(r.time_in_force, now))}
        if r.limit_price is not None:
            o["limit_price"] = f"{r.limit_price:.2f}"
        if r.stop_price is not None:
            o["stop_price"] = f"{r.stop_price:.2f}"
        plan = self.script._take(r.symbol, ("fills", "hold"))
        if plan is not None:
            self._plans[oid] = plan
        if self.script._take(r.symbol, ("duplicate",)) is not None:
            self._dup.add(oid)
        self.orders[oid] = o
        return o

    def _expiry(self, tif: str, now: datetime) -> datetime | None:
        if tif == "gtc":
            d = now.astimezone(NY).date() + timedelta(days=GTC_DAYS)
            return datetime.combine(d, GTC_CANCEL_AT, NY).astimezone(UTC)
        s = self._next_session(now, still_open=True)  # day, opg, cls: good for the session it can trade in
        return None if s is None else s[1]

    def replace(self, order_id: str, *, stop_price: float | None = None, limit_price: float | None = None,
                qty: float | None = None, client_order_id: str | None = None) -> dict:
        """Change a working order's or leg's price (and an order's quantity). By default in place, keeping the id.
        `client_order_id` (issue #197): the order works under it from now on (in place too); one the account
        already used is refused (422), as Alpaca does. Without it a new order gets `sim-<id>`, as Alpaca
        generates one.
        With `replace_new_id` as Alpaca's PATCH does: the old order turns `replaced` (`replaced_by` names the new
        one) and a new order with a new id and a new client order id works on (`replaces` names the old one); a
        replaced bracket leg is then listed under its parent (`replaced_leg="nested"`) or as an order of its own
        (`"detached"`, still cancelled when its sibling fills, and cancelling it when the stop fills)."""
        self._net()
        self.calls.append(f"replace {order_id}")
        self._expire(_utc(self.clk()))
        parent = None
        o = self.orders.get(order_id)
        if o is None:
            parent, o = self._leg(order_id)
        if o is None:
            raise BrokerError(404, f"order not found: {order_id}")
        if o["status"] not in (*WORKING, "held"):
            raise BrokerError(422, f"order is not open: {order_id} is {o['status']}")
        if qty is not None:
            done = float(o.get("filled_qty") or 0)
            if parent is not None or not (float(qty).is_integer() and qty > done):
                raise BrokerError(422, f"qty {qty:g} cannot replace {order_id}")
            if o["side"] == "sell" and qty - float(o["qty"]) > self._free_shares(o["symbol"]) + 1e-9:
                raise BrokerError(403, "insufficient qty available for order")
        if client_order_id and self.order_by_client_id(client_order_id) is not None:
            raise BrokerError(422, "client_order_id must be unique")
        if not self.replace_new_id:
            _reprice(o, stop_price, limit_price, qty)
            if client_order_id:
                o["client_order_id"] = client_order_id
            return o
        now = _utc(self.clk())
        nid = f"o{next(self.ids)}"
        if parent is None:
            if float(o.get("filled_qty") or 0):
                raise BrokerError(422, "sim: replacing a partly filled order is not modelled")
            new = {**o, "id": nid, "client_order_id": client_order_id or f"sim-{nid}", "replaces": o["id"],
                   "status": "new", "created_at": now.isoformat(), "submitted_at": now.isoformat(),
                   "expires_at": _iso(self._expiry(o["time_in_force"], now)),
                   "legs": [dict(x) for x in o.get("legs") or []]}
            _reprice(new, stop_price, limit_price, qty)
            o.update(status="replaced", replaced_by=nid)
            if o["id"] in self._plans:
                self._plans[nid] = self._plans.pop(o["id"])
            self.orders[nid] = new
            return new
        if o["status"] != "new":
            raise BrokerError(422, "sim: replacing a leg that is not armed is not modelled")
        new = {**o, "id": nid, "replaces": o["id"], "status": "new"}
        if client_order_id:
            new["client_order_id"] = client_order_id
        _reprice(new, stop_price, limit_price, None)
        o.update(status="replaced", replaced_by=nid)
        if self.replaced_leg == "nested":
            parent["legs"] = [new if x is o else x for x in parent["legs"]]
            self._gone[o["id"]] = o
        else:  # an order of its own: Alpaca's answer to issue #11 may be this
            new.update(client_order_id=client_order_id or f"sim-{nid}", time_in_force="gtc", order_class="simple",
                       legs=[], filled_qty=0, filled_avg_price=None, filled_at=None, parent_id=parent["id"],
                       created_at=now.isoformat(), submitted_at=now.isoformat(),
                       expires_at=_iso(self._expiry("gtc", now)))
            self.orders[nid] = new
        return new

    def cancel(self, order_id: str) -> None:
        self._net()
        self.calls.append(f"cancel {order_id}")
        o = self.orders.get(order_id)
        if o is not None:
            if o["status"] in WORKING:  # also drops the exit legs: they only arm on a complete fill
                self._end(o, "canceled")
            return
        parent, leg = self._leg(order_id)
        if leg is None or leg["status"] not in ("new", "held"):
            return  # unknown or already gone: not an error, as with the paper adapter
        leg["status"] = "canceled"
        if parent.get("order_class", "bracket") == "bracket":  # cancelling one bracket leg cancels the other
            for other in parent["legs"]:
                if other["status"] in ("new", "held"):
                    other["status"] = "canceled"

    def close_position(self, symbol: str) -> dict | None:
        """Cancel the symbol's open orders and legs, then sell the whole position at market (a day order)."""
        if symbol not in self.pos:
            return None
        for o in list(self.orders.values()):
            if o["symbol"] != symbol:
                continue
            if o["status"] in WORKING:
                self._end(o, "canceled")
            for leg in o.get("legs") or []:
                if leg["status"] in ("new", "held"):
                    leg["status"] = "canceled"
        return self._place(OrderRequest(symbol, "sell", self.pos[symbol]["qty"], "market", "day"), _utc(self.clk()))

    # ================= the venue trades one bar =================
    def advance(self, t) -> None:
        """Trade the bar that starts at `t`. Bars are processed once and in order: an earlier or repeated `t`
        is ignored, so nothing ever fills in the past."""
        t = _utc(t)
        if self.strict_time:
            if self._last_bar is not None and t <= self._last_bar:
                return
            self._last_bar = t
        self._expire(t)
        day = str(t.astimezone(NY).date())
        sess = self.session(day)
        open_utc = sess[0] if sess else datetime.combine(t.astimezone(NY).date(), time(9, 30), NY).astimezone(UTC)
        for o in self._live_orders():
            bar = self.bars.bar_or_carried(o["symbol"], t, open_utc)
            if bar is None:
                continue
            if o["status"] in WORKING and (not self.strict_time or _minute(o.get("submitted_at"), t) <= t):
                self._work(o, bar, t, sess)
            if o["status"] == "filled":
                self._work_legs(o, bar, t)

    def _live_orders(self) -> list[dict]:
        """Orders that can still trade: working entries and filled entries with a working or held leg."""
        if not self.strict_time or self._seen != len(self.orders):  # tests put raw orders straight into `orders`
            self._live = {k: o for k, o in self.orders.items() if _alive(o)}
            self._seen = len(self.orders)
        else:
            self._live = {k: o for k, o in self._live.items() if _alive(o)}
        return list(self._live.values())

    def _work(self, o: dict, bar: Bar, t: datetime, sess) -> None:
        plan = self._plans.get(o["id"])
        if plan is not None and plan.kind == "hold":
            if plan.bars is None:
                return
            plan.bars -= 1
            if plan.bars >= 0:
                return
            del self._plans[o["id"]]
            plan = None
        side, left = o["side"], float(o["qty"]) - float(o.get("filled_qty") or 0)
        tif = o.get("time_in_force")
        if tif in ("cls", "opg"):
            at = None if sess is None else sess[1] - timedelta(minutes=1) if tif == "cls" else sess[0]
            if at is not None and t == at:
                price = bar.c if tif == "cls" else bar.o
                px = self._auction(side, price, left, o["symbol"], bar, t)
                if side == "buy" and self.cash is not None and self.cash_at == "fill" and px * left > self.cash:
                    left = float(math.floor(self.cash / px))  # cut to the whole shares that fit at the print
                    if left < 1:
                        self._end(o, "canceled")
                        return
                    px = self._auction(side, price, left, o["symbol"], bar, t)
                self._fill(o, o, left, px, t)
                if o["status"] == "partially_filled":
                    self._end(o, "canceled")  # the rest of a cut auction order does not trade
        elif o["type"] == "market":
            qty = left
            if plan is not None:  # scripted partial fills: one part per bar, the rest never
                qty = min(plan.parts.pop(0), left) if plan.parts else 0.0
            if qty > 0:
                self._fill(o, o, qty, self._market(side, bar.o, qty, o["symbol"], bar, t), t)
        elif o["type"] == "limit":
            limit = float(o["limit_price"])
            if (bar.l <= limit - THROUGH) if side == "buy" else (bar.h >= limit + THROUGH):
                self._fill(o, o, left, limit, t)
        elif o["type"] == "stop":
            stop = float(o["stop_price"])
            if side == "sell" and bar.l <= stop:
                self._fill(o, o, left, self._market(side, min(stop, bar.o), left, o["symbol"], bar, t), t)
            elif side == "buy" and bar.h >= stop:
                self._fill(o, o, left, self._market(side, max(stop, bar.o), left, o["symbol"], bar, t), t)
        if o["status"] == "filled":
            for leg in o.get("legs") or []:
                if leg["status"] == "held":
                    leg["status"] = "new"  # the exit legs arm only now
            parent = self.orders.get(o.get("parent_id") or "")
            for leg in parent.get("legs") or [] if parent is not None else []:
                if leg["status"] in ("new", "held"):
                    leg["status"] = "canceled"  # a detached replaced leg filled: its sibling goes (one-cancels-other)

    def _work_legs(self, o: dict, bar: Bar, t: datetime) -> None:
        legs = o.get("legs") or []
        stop = next((x for x in legs if x["type"] == "stop" and x["status"] == "new"), None)
        tgt = next((x for x in legs if x["type"] == "limit" and x["status"] == "new"), None)
        qty = float(o.get("filled_qty") or o["qty"])
        if stop is not None and bar.l <= float(stop["stop_price"]):
            gross = min(float(stop["stop_price"]), bar.o)  # elected: a market order; gapped through: the open
            self._fill(o, stop, qty, self._market("sell", gross, qty, o["symbol"], bar, t), t)
            if tgt is not None:
                tgt["status"] = "canceled"
        elif tgt is not None and bar.h >= float(tgt["limit_price"]) + THROUGH:
            self._fill(o, tgt, qty, float(tgt["limit_price"]), t)
            if stop is not None:
                stop["status"] = "canceled"
            for child in self.orders.values():
                if child.get("parent_id") == o["id"] and child["status"] in WORKING:
                    child["status"] = "canceled"

    # ---------- prices ----------
    def _market(self, side: str, price: float, qty: float, symbol: str, bar: Bar, t: datetime) -> float:
        if self.costs is None:
            return price
        sigma = self.bars.sigma_1m(symbol, t.astimezone(NY).date())
        return self.costs.market(side, price, qty, sigma if math.isfinite(sigma) else 0.0, bar.v)

    def _auction(self, side: str, price: float, qty: float, symbol: str, bar: Bar, t: datetime) -> float:
        if self.costs is None:
            return price
        full = getattr(self.costs, "auction_fill", None)  # AuctionImpactCosts: spread plus impact (#161)
        if full is not None:
            return full(side, price, qty, symbol, t.astimezone(NY).date(), bar.v)
        return self.costs.auction(side, price)

    # ---------- booking ----------
    def _fill(self, parent: dict, o: dict, qty: float, px: float, t: datetime) -> None:
        """Book `qty` shares of order or leg `o` at `px`. `filled_at` is set by the fill that completes it."""
        side = o.get("side") or ("sell" if parent["side"] == "buy" else "buy")
        done = float(o.get("filled_qty") or 0)
        total = float(o.get("qty") or parent["qty"])
        avg = px if not done else (float(o["filled_avg_price"]) * done + px * qty) / (done + qty)
        complete = done + qty >= total - 1e-9
        o.update(status="filled" if complete else "partially_filled", filled_qty=_num(done + qty),
                 filled_avg_price=str(avg), filled_at=t.isoformat() if complete else None)
        symbol = parent["symbol"]
        p = self.pos.get(symbol)
        if side == "buy":
            if p is None:
                self.pos[symbol] = {"qty": _num(qty), "avg_entry_price": px}
            else:
                have = float(p["qty"])
                p["avg_entry_price"] = (float(p["avg_entry_price"]) * have + px * qty) / (have + qty)
                p["qty"] = _num(have + qty)
        elif p is not None:
            left = float(p["qty"]) - qty
            if left <= 1e-9:
                self.pos.pop(symbol)
            else:
                p["qty"] = _num(left)
        fees = self.costs.fees(side, qty, px) if self.costs is not None else 0.0
        if self.cash is not None:
            self.cash += (-qty * px) if side == "buy" else (qty * px - fees)
        self.fills.append({"id": f"{t.strftime('%Y%m%d%H%M%S')}::{len(self.fills) + 1}", "activity_type": "FILL",
                           "transaction_time": t.isoformat(), "type": "fill" if complete else "partial_fill",
                           "price": str(px), "qty": str(_num(qty)), "side": side, "symbol": symbol,
                           "leaves_qty": str(_num(max(total - done - qty, 0.0))), "cum_qty": str(_num(done + qty)),
                           "order_id": o["id"], "order_status": o["status"], "fees": fees})
        self._fill_ts.append(t)
        if parent["id"] in self._dup or o["id"] in self._dup:
            self.fills.append(dict(self.fills[-1]))  # the same activity reported twice (same id)
            self._fill_ts.append(t)

    def _end(self, o: dict, status: str) -> None:
        o["status"] = status
        for leg in o.get("legs") or []:
            if leg["status"] in ("new", "held"):
                leg["status"] = status if leg["status"] == "new" else "canceled"

    def _expire(self, now: datetime) -> None:
        """Day orders and day legs end at their session's close; GTC orders and legs 90 days after creation."""
        for o in self._live_orders():
            at = o.get("expires_at")
            if not at or now < datetime.fromisoformat(at):
                continue
            status = "canceled" if o.get("time_in_force") == "gtc" else "expired"
            if o["status"] in WORKING:
                self._end(o, status)
            else:
                for leg in o.get("legs") or []:
                    if leg["status"] in ("new", "held"):
                        leg["status"] = status


def _reprice(o: dict, stop: float | None, limit: float | None, qty: float | None) -> None:
    if stop is not None:
        o["stop_price"] = f"{stop:.2f}"
    if limit is not None:
        o["limit_price"] = f"{limit:.2f}"
    if qty is not None:
        o["qty"] = _num(qty)


def _alive(o: dict) -> bool:
    return o["status"] in WORKING or any(x["status"] in ("new", "held") for x in o.get("legs") or [])


def _utc(t) -> datetime:
    """A python datetime in UTC from a datetime or pandas Timestamp (naive values are UTC)."""
    if hasattr(t, "to_pydatetime"):
        t = t.to_pydatetime()
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _minute(iso: str | None, default: datetime) -> datetime:
    """The start of the minute an order was submitted in (raw test orders have no time: always eligible)."""
    return default if not iso else datetime.fromisoformat(iso).replace(second=0, microsecond=0)


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


def _num(x: float):
    return int(x) if float(x).is_integer() else float(x)
