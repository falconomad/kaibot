"""The two venues the broker adapter contract runs against (M12 ticket 4.9, #488), behind one small driver each.

A driver owns what the contract may not ask an adapter for, because no real broker offers it: making the venue
execute, cutting the network, losing one answer. Everything the contract asserts goes through `driver.broker`,
which is the adapter exactly as the engine gets it.

- `SimDriver`: `SimBroker` on flat bars at $100, without the cost model, replacing as Alpaca does (a new id).
- `AlpacaPaperDriver`: the real `AlpacaPaper` adapter with its HTTP layer pointed at `FakeAlpacaPaper`
  (fake_alpaca_http.py): no network, fake keys, and a transport that refuses any host but the paper endpoint.

A third adapter (Interactive Brokers, docs/ops/broker_adapters.md) joins the contract by adding a driver here and
its name to `DRIVERS`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from fake_alpaca_http import FakeAlpacaPaper

from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S, AlpacaPaper, _Http
from kaibot.broker.protocol import Broker
from kaibot.broker.sim import NY, SimBroker
from kaibot.common.ratelimit import Budget, BudgetSpec
from kaibot.engine.clock import SimClock
from kaibot.replay.history import BarHistory

DAY, NEXT = "2026-09-15", "2026-09-16"  # a Tuesday and a Wednesday
WEEKEND = "2026-09-19"
PRICE = 100.0
SYMBOLS = ("A", "B")


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


class Driver:
    name: str
    broker: Broker
    clock: SimClock

    def trade(self) -> None:
        """One minute passes and the venue trades: working market orders execute at $100, in the parts planned
        with `partials` or completely; limit orders execute when $100 is through their price."""
        raise NotImplementedError

    def partials(self, symbol: str, parts: list[float]) -> None:
        """The next market order in `symbol` executes in these share counts, one per `trade()`."""
        raise NotImplementedError

    def cut_network(self, down: bool) -> None:
        raise NotImplementedError

    def lose_next_answer(self) -> None:
        """The next submission reaches the venue and its answer never arrives."""
        raise NotImplementedError

    def venue_orders(self) -> list[dict]:
        """Every order the venue holds, whatever its state (the truth the contract counts against)."""
        raise NotImplementedError


class SimDriver(Driver):
    name = "sim"

    def __init__(self) -> None:
        self.clock = SimClock(et(DAY, "10:00", 7))
        ts = [pd.Timestamp(f"{d} 09:30", tz=NY).tz_convert(UTC) + pd.to_timedelta(np.arange(390), unit="m")
              for d in (DAY, NEXT)]
        frame = pd.DataFrame({"ts": ts[0].append(ts[1]), "o": PRICE, "h": PRICE + 0.05, "l": PRICE - 0.05,
                              "c": PRICE, "v": 10_000.0, "vw": PRICE})
        self.sim = SimBroker(BarHistory({s: frame for s in SYMBOLS}), self.clock, costs=None, replace_new_id=True)
        self.broker = self.sim

    def trade(self) -> None:
        self.sim.advance(self.clock().replace(second=0, microsecond=0))
        self.clock.advance(minutes=1)

    def partials(self, symbol: str, parts: list[float]) -> None:
        self.sim.script.fills(symbol, parts)

    def cut_network(self, down: bool) -> None:
        self.sim.down = down

    def lose_next_answer(self) -> None:
        self.sim.script.timeout(accepted=True)

    def venue_orders(self) -> list[dict]:
        return list(self.sim.orders.values())


class _Time:
    """The adapter's monotonic clock and sleep: waiting costs no real time."""

    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class AlpacaPaperDriver(Driver):
    name = "alpaca_paper"

    def __init__(self) -> None:
        self.clock = SimClock(et(DAY, "10:00", 7))
        self.fake = FakeAlpacaPaper(self.clock, sessions=(DAY, NEXT))
        self.broker = AlpacaPaper("contract-test-key", "contract-test-secret")  # never sent anywhere: see the fake
        time = _Time()
        self.broker.http = _Http("contract-test-key", "contract-test-secret",
                                 budget=Budget("alpaca_trading", BudgetSpec(), time), opener=self.fake.opener,
                                 sleep=time.sleep, timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
        self._parts: dict[str, list[float]] = {}
        self._plans: dict[str, list[float]] = {}  # order id -> the parts left

    def trade(self) -> None:
        for o in list(self.fake.orders.values()):
            if o["status"] not in ("new", "accepted", "partially_filled"):
                continue
            left = float(o["qty"]) - float(o["filled_qty"])
            if o["type"] == "market":
                if o["id"] not in self._plans and o["symbol"] in self._parts:
                    self._plans[o["id"]] = self._parts.pop(o["symbol"])
                plan = self._plans.get(o["id"])
                qty = left if plan is None else (min(plan.pop(0), left) if plan else 0.0)
                if qty > 0:
                    self.fake.execute(o["id"], qty, PRICE)
            elif o["type"] == "limit":
                limit = float(o["limit_price"])
                if (o["side"] == "buy" and limit >= PRICE + 0.01) or (o["side"] == "sell" and limit <= PRICE - 0.01):
                    self.fake.execute(o["id"], left, limit)
        self.clock.advance(minutes=1)
        if self.clock() >= et(str(self.clock().astimezone(NY).date()), "16:00"):
            self.fake.expire_day_orders()

    def partials(self, symbol: str, parts: list[float]) -> None:
        self._parts[symbol] = list(parts)

    def cut_network(self, down: bool) -> None:
        self.fake.down = down

    def lose_next_answer(self) -> None:
        self.fake.lose_next_answer = True

    def venue_orders(self) -> list[dict]:
        return list(self.fake.orders.values())


DRIVERS = {"sim": SimDriver, "alpaca_paper": AlpacaPaperDriver}


def later(clock: SimClock, **delta) -> datetime:
    return clock() + timedelta(**delta)
