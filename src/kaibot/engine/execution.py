"""Execution styles and holding policy (S78, issue #118; decision 15 section 3, "Architecture decisions"). Pure:
the time is passed in, nothing is sent from here; the order manager (engine/orders.py) sends what this builds.

Styles, with Alpaca's rules (docs/research/engine_v3/notes_engine_architecture.md section 4):
- `close`: the closing auction (`cls`). Whole shares (fractional orders are DAY only). Submitted before 15:50 ET,
  or from 19:00 ET for the next session's close; refused in between.
- `near_close`: a DAY market order in the session's last ten minutes (from the close minus 10 minutes, 15:50 ET on
  a normal day, until the close), whole shares; refused at any other time. The daily rules' style since issue #333:
  Alpaca paper does not run an auction, it works a `cls` order as a market order in the last seconds before 16:00
  and lets the rest expire (on 18 Nov 2026, 23 of 30 got nothing). The research venue fills it at the closing
  print at cost model v1, as it did the `cls` order (broker/sim.SimBroker `near_close`; prereg amendments).
- `marketable_limit`: a DAY limit order at the ask plus `offset_bp` (a buy) or the bid less it (a sell), whole
  shares, only from 09:45 ET (the opening is the most expensive time to trade) until the close; cancelled by
  `timed_out` after `timeout_s` if it has not filled.
- `limit`: a DAY or GTC limit order at a price the caller gives.
- `bracket`: intraday rules keep their entry bracket (market buy, GTC stop and target legs at the broker).
- no market-on-open: an `opg` order is refused (the open is the most expensive venue). The research stress fill
  of decision 15 ("fill at the open of T+1 at twice the half-spread") is a cost model, not an order the engine
  sends live: only `ExecutionPolicy(research_open=True)` builds it, and the live path never sets that.

The pre-send gate (M9 ticket 1.16, issue #441; `PreSendGate`, pure like the rest of this module): the last check
before a near-close market order goes out. The share count was sized at the previous close, so the gate reads the
stock's latest quote and WITHHOLDS the order when the stock is halted, when its quote is not fresh (no update for
`max_age_s`: halted or not trading), when both sides of the quote are further than `collar` from the sizing close,
or when a buy is worth more than the caller's cap. A missing quote falls back to the prior close: the order is
checked at that price and goes out (a market-data outage must not become one more way to miss a rebalance). The
gate never changes a quantity or a price. The caller carries a withheld order to the next session
(engine/live_daily.py); the research venue has no quotes and no gate (the pre-registrations' amendments).

Holding policy per rule: intraday sleeves are flattened at the close minus 5 minutes, multi-day sleeves are not
(`flatten_at_close`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from kaibot.broker.protocol import OrderRequest

NY = ZoneInfo("America/New_York")
CLS_UNTIL, CLS_FROM = time(15, 50), time(19, 0)
MLIMIT_FROM, MLIMIT_UNTIL = time(9, 45), time(16, 0)
NEAR_CLOSE_MIN = 10  # near_close: DAY market orders from the close minus 10 minutes until the close
REGULAR_CLOSE = time(16, 0)
STYLES = ("close", "near_close", "marketable_limit", "limit", "bracket", "open")


def minus(t: time, minutes: int) -> time:
    """`t` less `minutes` (wall time, same day)."""
    m = t.hour * 60 + t.minute - minutes
    return time(m // 60, m % 60, t.second)


class ExecutionRefused(ValueError):
    """The style cannot be used for this order now (window, fractional shares, missing price)."""


@dataclass(frozen=True)
class ExecutionPolicy:
    style: str = "close"
    offset_bp: float = 5.0  # marketable limit: ask + 0.05% (the approved v2 design, audit D1)
    timeout_s: float = 120.0  # marketable limit: cancelled after 2 minutes
    research_open: bool = False  # the stress fill model only (opg at the open); never live
    close: time = REGULAR_CLOSE  # near_close: the session's close (ET), earlier on an early-close day

    def __post_init__(self) -> None:
        if self.style not in STYLES:
            raise ValueError(f"unknown execution style {self.style!r}")
        if self.style == "open" and not self.research_open:
            raise ExecutionRefused("no market-on-open orders (decision 15): use the close or a marketable limit")

    def request(self, symbol: str, side: str, qty: float, now: datetime, coid: str, *,
                bid: float | None = None, ask: float | None = None, limit: float | None = None,
                stop: float | None = None, target: float | None = None, tif: str = "day") -> OrderRequest:
        t = now.astimezone(NY).time()
        if not float(qty).is_integer() or qty < 1:
            raise ExecutionRefused(f"{symbol}: whole shares only ({qty:g})")
        q = int(qty)
        if self.style == "close":
            if CLS_UNTIL <= t < CLS_FROM:
                raise ExecutionRefused(f"{symbol}: closing-auction orders go in before 15:50 or after 19:00 ET")
            return OrderRequest(symbol, side, q, "market", "cls", coid)
        if self.style == "near_close":
            start = minus(self.close, NEAR_CLOSE_MIN)
            if not (start <= t < self.close):
                raise ExecutionRefused(f"{symbol}: near-close orders go in from {start:%H:%M} until the close "
                                       f"({self.close:%H:%M} ET)")
            return OrderRequest(symbol, side, q, "market", "day", coid)
        if self.style == "open":
            return OrderRequest(symbol, side, q, "market", "opg", coid)
        if self.style == "marketable_limit":
            if not (MLIMIT_FROM <= t < MLIMIT_UNTIL):
                raise ExecutionRefused(f"{symbol}: marketable limits only from 09:45 ET until the close")
            ref = ask if side == "buy" else bid
            if not (ref is not None and math.isfinite(ref) and ref > 0):
                raise ExecutionRefused(f"{symbol}: no {'ask' if side == 'buy' else 'bid'} for a marketable limit")
            px = ref * (1 + self.offset_bp / 1e4) if side == "buy" else ref * (1 - self.offset_bp / 1e4)
            px = math.ceil(px * 100 - 1e-6) / 100 if side == "buy" else math.floor(px * 100 + 1e-6) / 100
            return OrderRequest(symbol, side, q, "limit", "day", coid, limit_price=px)
        if self.style == "limit":
            if not (limit is not None and limit > 0):
                raise ExecutionRefused(f"{symbol}: a limit order needs a price")
            return OrderRequest(symbol, side, q, "limit", tif, coid, limit_price=float(limit))
        if stop is None or target is None or side != "buy":
            raise ExecutionRefused(f"{symbol}: a bracket is a buy with a stop and a target")
        return OrderRequest(symbol, "buy", q, "market", "gtc", coid, order_class="bracket", take_profit=target,
                            stop_loss=stop)

    def timed_out(self, created: str, now: datetime) -> bool:
        """A marketable limit older than the timeout is cancelled (it chased nothing; the next run decides)."""
        t = datetime.fromisoformat(created)
        return self.style == "marketable_limit" and (now - t).total_seconds() >= self.timeout_s


# ---------------------------------------------------------------- the pre-send gate (M9 1.16, issue #441)

WITHHELD = "withheld: "  # every reason the gate gives starts with this


@dataclass(frozen=True)
class Quote:
    """A stock's latest quote: bid, ask (0 or less: that side is absent) and when the venue stamped it."""

    bid: float
    ask: float
    ts: datetime

    def sides(self) -> list[float]:
        return [float(x) for x in (self.bid, self.ask) if x is not None and math.isfinite(x) and x > 0]


def quote_from_alpaca(row) -> Quote | None:
    """Alpaca's latest-quote row ({"bp", "ap", "t": RFC 3339 with nanoseconds}) as a `Quote`; None when it is not
    one (no row, no number, no time: a quote whose age is unknown is no quote)."""
    try:
        m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", str(row["t"]))
        frac, zone = (m.group(2) or "0")[:6], (m.group(3) or "Z").replace("Z", "+00:00")
        ts = datetime.fromisoformat(f"{m.group(1)}.{frac:0<6}{zone}")
        return Quote(float(row.get("bp") or 0.0), float(row.get("ap") or 0.0), ts.astimezone(UTC))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PreSendGate:
    """`check` gives the reason an order is withheld, or None (it may go out). Frozen constants, not tuned:
    `max_age_s` 300 s (a limit-up/limit-down pause lasts five minutes; a liquid stock's quote changes every few
    seconds), `collar` 10% from the sizing close (the rule's sizing assumed that close), and for a buy the caller's
    value cap (the live path: the book's per-name cap with the collar's head-room, so a price inside the collar
    never trips it and a wrong quantity does)."""

    max_age_s: float = 300.0
    collar: float = 0.10

    def check(self, side: str, qty: float, *, close: float | None, quote: Quote | None, now: datetime,
              max_value: float | None = None, halted: bool = False) -> str | None:
        if halted:
            return f"{WITHHELD}the stock is halted"
        close = float(close) if close is not None and math.isfinite(close) and close > 0 else None
        sides = quote.sides() if quote is not None else []
        if sides:
            age = (now - quote.ts).total_seconds()
            if age > self.max_age_s:
                return (f"{WITHHELD}no fresh quote ({age:.0f} s old, limit {self.max_age_s:.0f} s): halted or not "
                        "trading")
            # the side nearest the sizing close: one exchange's book can show one far side (IEX), a gap moves both
            ref = min(sides, key=lambda x: abs(x - close)) if close is not None else sum(sides) / len(sides)
            if close is not None and abs(ref / close - 1) > self.collar + 1e-12:
                return (f"{WITHHELD}moved {100 * (ref / close - 1):+.1f}% since the sizing close "
                        f"({close:.2f} -> {ref:.2f}; collar {100 * self.collar:.0f}%)")
        else:
            ref = close  # no quote: the prior close
        if side == "buy" and max_value is not None and ref is not None and float(qty) * ref > max_value + 1e-6:
            return f"{WITHHELD}order value ${float(qty) * ref:,.0f} is above the cap ${max_value:,.0f}"
        return None


def flatten_at_close(holding) -> bool:
    """The pre-send gate (M9 ticket 1.16, issue #441; `PreSendGate`, pure like the rest of this module): the last check
before a near-close market order goes out. The share count was sized at the previous close, so the gate reads the
stock's latest quote and WITHHOLDS the order when the stock is halted, when its quote is not fresh (no update for
`max_age_s`: halted or not trading), when both sides of the quote are further than `collar` from the sizing close,
or when a buy is worth more than the caller's cap. A missing quote falls back to the prior close: the order is
checked at that price and goes out (a market-data outage must not become one more way to miss a rebalance). The
gate never changes a quantity or a price. The caller carries a withheld order to the next session
(engine/live_daily.py); the research venue has no quotes and no gate (the pre-registrations' amendments).

Holding policy per rule: an intraday sleeve is flattened at the close minus 5 minutes, a multi-day one is
    not (it keeps its protective stop at the broker overnight)."""
    return str(getattr(holding, "value", holding)) != "multi_day"
