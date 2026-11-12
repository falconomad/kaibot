"""The one runner (issue #115): drives the UNCHANGED `Engine` over stored bars on simulated time.

It repeats, minute by minute, what engine/__main__.py does on the wall clock: apply dashboard commands, then one
`Engine.tick()` per completed minute a few seconds after the minute ends, with the same error handling. Between
two ticks the simulated venue trades the next bar. Nothing else decides anything: entries, exits, sizing and
halts all come from the engine and risk/gate.py, exactly as on paper.

    for each session:
        pre-open tick                      (the engine loads history)
        for each minute k of the session:
            broker.advance(bar k)          (the venue trades bar k: fills at its open, triggers inside it)
            tick at bar k's end + 7 s      (the engine sees minute k complete)
        after-close tick                   (day end; the tick after the last bar already finalised the day)

`simulation()` wires the engine to `SimBroker` and `HistoricalData` over one `BarHistory` and one `SimClock`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.broker.sim import COSTS_V1, ShadowCosts, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.core import Engine
from kaibot.engine.market import LiveMarket, session_minutes
from kaibot.replay.history import BarHistory, HistoricalData
from kaibot.risk.gate import RiskPolicy

TICK_S = 7  # the live loop ticks at second >= 7 of each minute
PRE_OPEN = timedelta(minutes=30)
AFTER_CLOSE = timedelta(minutes=30)


@dataclass
class RunLog:
    sessions: int = 0
    ticks: int = 0
    errors: list[tuple[datetime, str]] = field(default_factory=list)


def run_sessions(engine: Engine, broker: SimBroker, clock: SimClock, days: Iterable[str], *, tick_s: int = TICK_S,
                 minutes: int | None = None, strict: bool = True,
                 on_tick: Callable[[datetime, dict], None] | None = None) -> RunLog:
    """Run the engine through the sessions `days` (ISO dates; days without a session are skipped).

    `minutes` stops each session after that many minutes (no after-close tick), for tests that need only the
    start of a day. `strict` re-raises an exception from the engine (tests want the failure); with
    `strict=False` the runner does what the live loop does: report it to `Engine.on_tick_error` and carry on
    with the next minute."""
    log = RunLog()

    def tick(t: datetime) -> None:
        clock.set(t)
        try:
            engine.apply_commands()
            status = engine.tick()
            engine.on_tick_ok()
        except Exception as e:
            if strict:
                raise
            engine.on_tick_error(e)
            log.errors.append((t, f"{type(e).__name__}: {e}"[:300]))
            status = {"state": "error"}
        log.ticks += 1
        if on_tick is not None:
            on_tick(t, status)

    for day in days:
        cal = broker.calendar(day)
        if cal is None:
            continue
        open_utc, n_min = session_minutes(cal, date.fromisoformat(day))
        tick(max(open_utc - PRE_OPEN, clock()))
        for k in range(n_min if minutes is None else min(minutes, n_min)):
            bar = open_utc + timedelta(minutes=k)
            broker.advance(bar)
            tick(bar + timedelta(minutes=1, seconds=tick_s))
        if minutes is None:
            tick(open_utc + timedelta(minutes=n_min) + AFTER_CLOSE)
        log.sessions += 1
    return log


@dataclass
class Simulation:
    engine: Engine
    broker: SimBroker
    data: HistoricalData
    clock: SimClock
    store: object

    def run(self, days: Iterable[str], **kw) -> RunLog:
        return run_sessions(self.engine, self.broker, self.clock, days, **kw)


def simulation(frames: Mapping[str, pd.DataFrame], sector_of: Mapping[str, str], market: str, *, store,
               policies: dict[str, RiskPolicy], start: datetime, cache_dir: Path, vix: pd.Series,
               fomc: Iterable = (), news: Mapping[str, np.ndarray] | None = None,
               costs: ShadowCosts | None = COSTS_V1, cash: float | None = 100_000.0,
               calendar: Mapping[str, tuple[str, str]] | None = None,
               strategies: dict[str, list[str]] | None = None, quote_age: timedelta = timedelta(0),
               **engine_kw) -> Simulation:
    """The engine on a simulated venue and feed. `frames`: 1-minute bars per symbol (stocks, their sector ETFs
    and the market ETF); `start`: where simulated time begins (before the first session's open);
    `cache_dir`: the market's history cache; `store`: the engine's store (an in-memory one in tests; the
    journal of S77 later); `quote_age`: how old the synthetic quotes are when the engine reads them."""
    clock = SimClock(start)
    history = BarHistory(frames)
    data = HistoricalData(history, clock, news, quote_age)
    broker = SimBroker(history, clock, costs=costs, cash=cash, calendar=calendar)
    live = LiveMarket(data, list(sector_of), dict(sector_of), market, Path(cache_dir), lambda: vix)
    engine = Engine(broker, live, store, policies, set(fomc), now=clock, strategies=strategies, **engine_kw)
    return Simulation(engine, broker, data, clock, store)
