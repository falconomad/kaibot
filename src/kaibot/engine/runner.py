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

`simulation()` wires the engine to `SimBroker` and `HistoricalData` over one `BarHistory` and one `SimClock`. The
engine reads the market through `SimMarket` (issue #220): `LiveMarket` with the stored bars read once per session
instead of fetched and merged every minute, the same values about five times faster; `fast=False` gives the plain
`LiveMarket` (the test compares the two journals byte for byte).
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
from kaibot.engine.market import HISTORY_SESSIONS, LiveMarket, session_minutes
from kaibot.replay.data import ET, N_MIN, SymbolDays, from_frame
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


class SimMarket(LiveMarket):
    """`LiveMarket` on the simulated feed (issue #220): the same state, the same feature code, the same values, read
    faster. Only `simulation()` builds it (and only with `fast=True`, the default); the live engine
    (engine/__main__.py) builds `LiveMarket` itself and never reaches this class.

    Where a ten-year SimBroker run spent its time (profile in the PR of #220) and what is read differently here:

    - `update`, every minute: `LiveMarket` asks the feed for the bars since two minutes before its last read (late
      bars), concatenates and de-duplicates them, about 2.7 ms of pandas a symbol a tick and two thirds of the run.
      Stored bars are never late and never change, so the union of those reads is exactly the session's stored bars
      that started before the current minute (and before `clock()`, the feed's own cut-off). Here that slice is cut
      from the session's bars, read once per session and symbol.
    - `symbol_days`, at every check: `from_frame` over today's bars (about 18 ms each). Here today's row is built
      once per session from the whole session's bars and then cut back to what the bars so far show (`_today_row`):
      minutes after the last bar so far carry its close with zero volume, minutes after `upto_m` are NaN, exactly
      what `from_frame` makes of the partial frame. The future part of the row is overwritten before it is
      returned; tests/unit/test_sim_market.py perturbs every later bar and checks that nothing moves.
    - `prepare_day`, once per session: the history window is read from the stored bars directly (`LiveMarket` reads
      and rewrites a Parquet cache in the run's temporary folder, then fetches the missing part from the same
      store), and the SIP/IEX volume ratio is 1.0 (`HistoricalData` serves the same bars for both feeds, so the
      ratio of two equal sums is exactly 1.0, as `LiveMarket` computes it).

    The test runs the engine both ways and compares the journal entry by entry, and every `symbol_days` call."""

    def __init__(self, data: HistoricalData, *a, **kw):
        if not isinstance(data, HistoricalData):
            raise TypeError("SimMarket reads stored bars only (HistoricalData)")
        super().__init__(data, *a, **kw)
        self._session_bars: dict[str, tuple[pd.DataFrame, np.ndarray, np.ndarray]] = {}
        self._rows: dict[str, tuple[float, SymbolDays | None]] = {}
        self._session_key: datetime | None = None

    # ---------- once per session ----------
    def prepare_day(self, day, open_utc: datetime, progress=None) -> None:
        self.day, self.open_utc = day, open_utc
        self.today_bars = {s: pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v", "vw"]) for s in self.all}
        self._last_fetch = None
        end = open_utc - timedelta(hours=7)  # previous evening: history never touches today (as LiveMarket)
        start = end - timedelta(days=int(HISTORY_SESSIONS * 1.5))
        for n, s in enumerate(self.all):
            if progress and n % 5 == 0:
                progress(n, len(self.all))
            f = self.data.history.between(s, start, end)
            if f is None:
                f = pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v", "vw"])
            f["ts"] = pd.to_datetime(f["ts"], utc=True)
            self.history[s] = f
        self.vol_ratio = {s: 1.0 for s in self.all}  # the same bars for SIP and IEX: sum / sum = 1.0
        self.vix = self.vix_loader()
        self._reset()

    def _reset(self) -> None:
        self._session_bars, self._rows, self._session_key = {}, {}, self.open_utc

    def _session(self, s: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray] | None:
        """The session's stored bars (from the open, a day's worth), their start times (ns) and their session
        minutes (NaN outside 09:30-15:59 ET), read once."""
        if self._session_key != self.open_utc:
            self._reset()
        hit = self._session_bars.get(s)
        if hit is None:
            f = self.data.history.between(s, self.open_utc, self.open_utc + timedelta(days=1))
            if f is None:
                return None
            ts = pd.DatetimeIndex(pd.to_datetime(f["ts"], utc=True))
            et = ts.tz_convert(ET)
            mins = np.asarray(et.hour * 60 + et.minute - (9 * 60 + 30), dtype=float)
            mins[(mins < 0) | (mins >= N_MIN) | (et.date != self.day)] = np.nan
            hit = (f, ts.as_unit("ns").asi8, mins)
            self._session_bars[s] = hit
        return hit

    # ---------- every minute ----------
    def update(self, now: datetime) -> None:
        cut = min(now.replace(second=0, microsecond=0), self.data.clock())
        want = pd.Timestamp(cut).as_unit("ns").value
        for s in self.all:
            got = self._session(s)
            if got is None:
                continue
            f, ts, _ = got
            n = int(np.searchsorted(ts, want))  # bars that started before the cut
            cur = self.today_bars.get(s)
            if n == 0 or (cur is not None and len(cur) == n):
                continue  # nothing yet (the empty frame of prepare_day stays), or no new bar since the last tick
            self.today_bars[s] = f.iloc[:n]
        self._last_fetch = now.replace(second=0, microsecond=0) - timedelta(minutes=2)

    def symbol_days(self, s: str, upto_m: int) -> SymbolDays | None:
        """`LiveMarket.symbol_days` with today's row from `_today_row` (falls back to it wherever that is not
        exactly the same: a custom history, no bar yet, a bar outside the session's minutes)."""
        hist = self._history_days(s)
        td = self._today_row(s, upto_m)
        if hist is None or td is None or self.day is None:
            return super().symbol_days(s, upto_m)
        day = np.datetime64(self.day, "D")
        if len(hist.dates) and hist.dates[-1] >= day:
            return super().symbol_days(s, upto_m)
        return SymbolDays(s, np.concatenate([hist.dates, td.dates]),
                          *(np.vstack([getattr(hist, k), getattr(td, k)]) for k in ("o", "h", "l", "c", "v", "vw")))

    def _today_row(self, s: str, upto_m: int) -> SymbolDays | None:
        """What `from_frame(s, today_bars[s] scaled, upto={day: upto_m})` returns, from the whole session's row.

        With L the minute of the last bar so far: minutes <= L are the whole row's (a bar there is a bar so far;
        the close carried forward reads only earlier minutes); after L there is no bar so far, so the close is
        that of L carried forward with zero volume and o / h / l / vw equal to it; after upto_m the close is NaN.
        None (the caller falls back to from_frame) when the bars so far are not a clean prefix of the session."""
        got, today = self._session(s), self.today_bars.get(s)
        if got is None or today is None or not len(today):
            return None
        f, ts, mins = got
        n = len(today)
        if n > len(f) or ts[n - 1] != pd.Timestamp(today["ts"].iloc[-1]).as_unit("ns").value:
            return None
        if np.isnan(mins[:n]).any():
            return None
        ratio = float(self.vol_ratio.get(s, 1.0))
        hit = self._rows.get(s)
        if hit is None or hit[0] != ratio:
            whole = f.copy()
            whole["v"] = whole["v"].astype(float) * ratio
            hit = (ratio, from_frame(s, whole))
            self._rows[s] = hit
        full = hit[1]
        if full is None or len(full.dates) != 1:
            return None
        last = int(np.nanmax(mins[:n]))
        c = full.c[0].copy()
        c[last + 1:] = c[last]
        if upto_m + 1 < N_MIN:
            c[upto_m + 1:] = np.nan
        row = {}
        for k in ("o", "h", "l", "vw"):
            a = getattr(full, k)[0].copy()
            a[last + 1:] = c[last + 1:]
            row[k] = a
        v = full.v[0].copy()
        v[last + 1:] = 0.0
        row["c"], row["v"] = c, v
        if upto_m < last:  # from_frame keeps the bars after upto_m with a NaN close; not a case the engine makes
            return None
        return SymbolDays(s, full.dates.copy(), *(row[k][None, :] for k in ("o", "h", "l", "c", "v", "vw")))


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
               fast: bool = True, **engine_kw) -> Simulation:
    """The engine on a simulated venue and feed. `frames`: 1-minute bars per symbol (stocks, their sector ETFs
    and the market ETF); `start`: where simulated time begins (before the first session's open);
    `cache_dir`: the market's history cache; `store`: the engine's store (an in-memory one in tests, the
    local journal in research: engine/journal.JournalStore); `quote_age`: how old the synthetic quotes are when
    the engine reads them."""
    clock = SimClock(start)
    history = BarHistory(frames)
    data = HistoricalData(history, clock, news, quote_age)
    broker = SimBroker(history, clock, costs=costs, cash=cash, calendar=calendar)
    live = (SimMarket if fast else LiveMarket)(data, list(sector_of), dict(sector_of), market, Path(cache_dir),
                                               lambda: vix)
    engine = Engine(broker, live, store, policies, set(fomc), now=clock, strategies=strategies, **engine_kw)
    return Simulation(engine, broker, data, clock, store)
