"""Stored 1-minute bars for a simulation (issue #115): one indexed store, read by the simulated venue
(`broker.sim.SimBroker` fills on it) and by the simulated data feed (`HistoricalData`, what `LiveMarket` reads in
place of the Alpaca data client). Nothing here reads the wall clock.

This is the historical half of decision 15's `MarketData`. The live half is still `engine.alpaca.AlpacaData`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import half_spread
from kaibot.replay.data import ET, N_MIN

OPEN_MIN = 9 * 60 + 30
NS_MIN = 60_000_000_000
COLUMNS = ["ts", "o", "h", "l", "c", "v", "vw"]


class Bar(NamedTuple):
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float


class _Index(NamedTuple):
    frame: pd.DataFrame
    rows: int
    ts: np.ndarray  # bar start, ns since the epoch (UTC), strictly increasing
    cols: dict[str, np.ndarray]


def ns(t) -> int:
    """A datetime, pandas Timestamp or numpy datetime64 as ns since the epoch (naive values are UTC)."""
    ts = pd.Timestamp(t)
    return (ts.tz_localize(UTC) if ts.tzinfo is None else ts).as_unit("ns").value


class BarHistory:
    """1-minute bars per symbol (frames with ts UTC, o, h, l, c, v, vw), looked up by bar start time.

    `frames` is kept by reference and indexed lazily, so a caller may add or replace a symbol's frame later.
    Frames must be sorted by `ts` with no duplicate times."""

    def __init__(self, frames: Mapping[str, pd.DataFrame]):
        self.frames = frames
        self._idx: dict[str, _Index] = {}
        self._sigma: dict[str, tuple[int, dict[date, float]]] = {}

    def _index(self, symbol: str) -> _Index | None:
        f = self.frames.get(symbol)
        if f is None:
            return None
        ix = self._idx.get(symbol)
        if ix is None or ix.frame is not f or ix.rows != len(f):
            ts = pd.DatetimeIndex(pd.to_datetime(f["ts"], utc=True)).as_unit("ns").asi8
            if len(ts) > 1 and not (np.diff(ts) > 0).all():
                raise ValueError(f"bars for {symbol} must be sorted by ts without duplicates")
            ix = _Index(f, len(f), ts, {k: f[k].to_numpy(dtype=float) for k in ("o", "h", "l", "c", "v")})
            self._idx[symbol] = ix
        return ix

    # ---------- single bars (the venue) ----------
    def bar(self, symbol: str, t) -> Bar | None:
        """The bar that starts at `t`, or None when the symbol printed no trade in that minute."""
        ix = self._index(symbol)
        if ix is None or not len(ix.ts):
            return None
        want = ns(t)
        i = int(np.searchsorted(ix.ts, want))
        if i >= len(ix.ts) or ix.ts[i] != want:
            return None
        c = ix.cols
        return Bar(float(c["o"][i]), float(c["h"][i]), float(c["l"][i]), float(c["c"][i]), float(c["v"][i]))

    def last_close(self, symbol: str, before, since=None) -> float | None:
        """Close of the last bar that started before `before` (and, if given, not before `since`)."""
        ix = self._index(symbol)
        if ix is None:
            return None
        i = int(np.searchsorted(ix.ts, ns(before))) - 1
        if i < 0 or (since is not None and ix.ts[i] < ns(since)):
            return None
        return float(ix.cols["c"][i])

    def bar_or_carried(self, symbol: str, t, session_open) -> Bar | None:
        """The bar at `t`; if the minute has no trades, the session's last close carried forward with zero
        volume (what replay.data.from_frame does); None before the symbol's first print of the session."""
        b = self.bar(symbol, t)
        if b is not None:
            return b
        c = self.last_close(symbol, t, since=session_open)
        return None if c is None else Bar(c, c, c, c, 0.0)

    # ---------- ranges (the feed) ----------
    def between(self, symbol: str, start, end) -> pd.DataFrame | None:
        """Rows with start <= ts < end, re-indexed from 0; None for an unknown symbol."""
        ix = self._index(symbol)
        if ix is None:
            return None
        i0, i1 = np.searchsorted(ix.ts, [ns(start), ns(end)])
        return ix.frame.iloc[int(i0):int(i1)].reset_index(drop=True)

    # ---------- sessions and the cost model's volatility ----------
    def sessions(self) -> list[str]:
        """Every ET date on which any symbol has a regular-hours bar, sorted."""
        days: set[int] = set()
        for s in self.frames:
            ix = self._index(s)
            if ix is None or not len(ix.ts):
                continue
            wall = pd.DatetimeIndex(ix.ts, tz=UTC).tz_convert(ET).tz_localize(None).as_unit("ns").asi8
            mins = (wall // NS_MIN) % 1440 - OPEN_MIN
            days.update(np.unique(wall[(mins >= 0) & (mins < N_MIN)] // (1440 * NS_MIN)).tolist())
        return [str(np.datetime64(int(d), "D")) for d in sorted(days)]

    def sigma_1m(self, symbol: str, day: date) -> float:
        """1-minute return volatility the cost model uses on `day`: the standard deviation of the 20 daily
        close-to-close returns before it, over sqrt(390). Same expression as replay.features (`dvol20`), so
        the simulated venue prices impact exactly like the replay. NaN without 20 earlier returns."""
        ix = self._index(symbol)
        if ix is None:
            return float("nan")
        cached = self._sigma.get(symbol)
        if cached is None or cached[0] != ix.rows:
            wall = pd.DatetimeIndex(ix.ts, tz=UTC).tz_convert(ET).tz_localize(None).as_unit("ns").asi8
            mins = (wall // NS_MIN) % 1440 - OPEN_MIN
            keep = (mins >= 0) & (mins < N_MIN)
            closes = pd.Series(ix.cols["c"][keep]).groupby(wall[keep] // (1440 * NS_MIN)).last()
            close = closes.to_numpy()
            r = np.r_[np.nan, close[1:] / close[:-1] - 1]
            vol = pd.Series(r).shift(1).rolling(20, min_periods=20).std().to_numpy() / np.sqrt(N_MIN)
            last = {np.datetime64(int(d), "D").astype(object): float(v)
                    for d, v in zip(closes.index.to_numpy(), vol, strict=True)}
            cached = (ix.rows, last)
            self._sigma[symbol] = cached
        return cached[1].get(day, float("nan"))


class HistoricalData:
    """The data client `LiveMarket` reads (`bars`, `latest_quotes`, `news_count`), served from stored bars as of
    the simulated clock: no bar that starts at or after `clock()` is ever returned.

    Quotes are synthetic (there is no quote history): the last close plus and minus the cost model's half
    spread, stamped `quote_age` before now (zero by default, as the replay assumes)."""

    def __init__(self, bars: BarHistory | Mapping[str, pd.DataFrame], clock: Callable[[], datetime],
                 news: Mapping[str, np.ndarray] | None = None, quote_age: timedelta = timedelta(0)):
        self.history = bars if isinstance(bars, BarHistory) else BarHistory(bars)
        self.frames = self.history.frames
        self.clock = clock
        self.news_times = dict(news or {})  # symbol -> sorted datetime64[s] (UTC) of its headlines
        self.quote_age = quote_age

    def bars(self, symbols, start, end, feed, timeframe="1Min", adjustment="split") -> dict[str, pd.DataFrame]:
        end = min(end, self.clock())
        out = {}
        for s in symbols:
            g = self.history.between(s, start, end)
            if g is None:
                continue
            if timeframe == "1Day":
                g = (
                    g.assign(day=g["ts"].dt.tz_convert(ET).dt.date)
                    .groupby("day", as_index=False)
                    .agg(
                        ts=("ts", "first"),
                        o=("o", "first"),
                        h=("h", "max"),
                        l=("l", "min"),
                        c=("c", "last"),
                        v=("v", "sum"),
                        vw=("vw", "mean"),
                    )[COLUMNS]
                )
            out[s] = g.reset_index(drop=True)
        return out

    def latest_quotes(self, symbols) -> dict[str, dict]:
        now = self.clock()
        out = {}
        for s in symbols:
            c = self.history.last_close(s, now)
            if c is not None:
                hs = half_spread(c)
                out[s] = {"bp": c - hs, "ap": c + hs, "t": (now - self.quote_age).isoformat()}
        return out

    def news_count(self, symbol, start, end) -> int:
        """Headlines with start <= time <= end, the window replay.features counts."""
        t = self.news_times.get(symbol)
        if t is None or not len(t):
            return 0
        a = np.datetime64(start.astimezone(UTC).replace(tzinfo=None), "s")
        b = np.datetime64(end.astimezone(UTC).replace(tzinfo=None), "s")
        return int(np.searchsorted(t, b, side="right") - np.searchsorted(t, a, side="left"))
