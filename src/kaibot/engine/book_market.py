"""The marks of the multi-day book (M10 1.19, #444): today's minute bars of the holdings, and nothing else.

The book's engine needs one thing from the market during a session: the last price of each stock it holds, for the
minute row, the daily stop and the book's run end (M9 1.1: a bar counts only when it is fresh or the broker's own
price agrees). engine/market.LiveMarket gave it that as a by-product of the intraday scan: the minute bars of the
whole intraday universe, a 70-session minute history per symbol loaded before the open, the SIP/IEX volume ratios
and the VIX. `BookMarket` reads the bars of the holdings only and loads no history, so a session starts in seconds
and no step of the book's loop waits for an intraday read.

Which price a holding is measured at is unchanged. A holding inside `universe` (the symbols LiveMarket read: the
seed universe, its sector funds and the market fund) is measured at its IEX minute bars; a holding outside it has
no bars here either and is measured at the broker's own price, as before (core.Engine._md_price). A symbol's
first read of a session starts at the open, so its last bar is the one LiveMarket would hold.

The engine's minute read stays bounded (`minute_data`: engine/__main__.bounded_data) and a failed read leaves the
bars as they were (`Engine._market_update`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

import pandas as pd

log = logging.getLogger("kaibot.engine.book_market")
COLS = ["ts", "o", "h", "l", "c", "v", "vw"]


class BookMarket:
    def __init__(self, data, universe: Iterable[str], spy: str, minute_data=None,
                 holdings: Callable[[], Iterable[str]] = lambda: ()):
        self.data = data  # quotes for the pre-send gate (live_daily.presend_quotes), the previous close of `spy`
        self.minute_data = minute_data if minute_data is not None else data
        self.universe = frozenset(universe) | {spy}
        self.spy = spy
        self.all = sorted(self.universe)  # what the news poller watches (ingest/news_poll.engine_scope): unchanged
        self.holdings = holdings  # the symbols the book holds or has a working order in, now (set by the engine)
        self.today_bars: dict[str, pd.DataFrame] = {}
        self.day: date | None = None
        self.open_utc: datetime | None = None
        self._last_fetch: datetime | None = None
        self._read: set[str] = set()  # symbols whose bars were read from the open today
        self._spy_prev: float | None = None
        # The intraday universe's settings, kept so the engine's session start reads the same on both markets:
        # the owner's custom tickers and dollar-volume floor apply to intraday scans only.
        self.custom: dict[str, str] = {}
        self.min_adv = 50e6

    # ---------- custom tickers: none (they are scanned by intraday rules only) ----------
    def set_custom(self, custom) -> bool:
        return False

    def accept_custom(self, ok) -> None:
        return None

    # ---------- once per session ----------
    def prepare_day(self, day: date, open_utc: datetime, progress=None) -> None:
        """No history to load: the book's rules decide on the daily store, never on these bars."""
        self.day, self.open_utc = day, open_utc
        self.today_bars, self._read, self._last_fetch = {}, set(), None
        self._spy_prev = self._previous_close(open_utc)

    def _previous_close(self, open_utc: datetime) -> float | None:
        """The market fund's last daily close before this session, for the day's `spy_ret` (a report). Never
        raises: without it the day's row carries no SPY return."""
        try:
            got = self.data.bars([self.spy], open_utc - timedelta(days=10), open_utc - timedelta(hours=7),
                                 feed="sip", timeframe="1Day").get(self.spy)
            return float(got["c"].iloc[-1]) if got is not None and len(got) else None
        except Exception as e:
            log.warning("previous close of %s unreadable (%s): the day's SPY return is left empty", self.spy,
                        f"{type(e).__name__}: {e}"[:160])
            return None

    # ---------- every minute ----------
    def last_complete_minute(self, now: datetime) -> int:
        return int((now - self.open_utc).total_seconds() // 60) - 1

    def watched(self) -> list[str]:
        return sorted(({self.spy} | {str(s) for s in self.holdings()}) & self.universe)

    def update(self, now: datetime) -> None:
        want = self.watched()
        again = [s for s in want if s in self._read]
        first = [s for s in want if s not in self._read]
        cut = now.replace(second=0, microsecond=0)
        for symbols, start in ((again, self._last_fetch or self.open_utc), (first, self.open_utc)):
            if not symbols:
                continue
            got = self.minute_data.bars(symbols, start, now, feed="iex")
            for s, df in got.items():
                if df is None or not len(df):
                    continue
                cur = self.today_bars.get(s)
                df = df[df["ts"] < cut]  # only completed minutes
                self.today_bars[s] = (
                    pd.concat([cur, df], ignore_index=True).drop_duplicates("ts", keep="last")
                    if cur is not None and len(cur)
                    else df.reset_index(drop=True)
                )
        self._read.update(first)
        self._last_fetch = cut - timedelta(minutes=2)  # re-read late bars

    def last_close(self, s: str) -> float | None:
        df = self.today_bars.get(s)
        return float(df["c"].iloc[-1]) if df is not None and len(df) else None

    def last_bar_end(self, s: str) -> datetime | None:
        """#384: when the last minute bar of `s` ended (its start plus a minute), the time its close is from."""
        df = self.today_bars.get(s)
        if df is None or not len(df):
            return None
        return pd.Timestamp(df["ts"].iloc[-1]).to_pydatetime() + timedelta(minutes=1)

    def spy_return(self) -> float | None:
        df = self.today_bars.get(self.spy)
        if df is None or not len(df) or not self._spy_prev:
            return None
        return float(df["c"].iloc[-1] / self._spy_prev - 1)
