"""Deterministic checks on today's minute bars (M12 ticket 3.11, issue #473).

The engine audit found that the prices used for risk during the day come from one thin source with no staleness
limit and no second opinion. These are the checks the ticket names, as pure functions: the same input gives the same
findings, nothing is fetched, nothing is written, the clock is an argument.

1. Staleness (`stale_seconds`): the last completed bar ended too long ago, or there is no bar at all today.
2. Zero or negative prices (`bad_price_rows`): an open, high, low or close that is not a finite number above zero.
3. Bar-count gaps (`bar_gaps`): too few bars in the last minutes, or too long a run of minutes without one.
4. A move without a matching index move (`unmatched_move`): the symbol moved a lot over a few minutes while the
   index hardly moved, which is what a bad print looks like (and, more rarely, real single-stock news).

`check_symbol` and `check_all` run the four on bar frames shaped like `LiveMarket.today_bars` (columns ts, o, h, l,
c; ts in UTC, the start of the minute) and return `Finding` rows.

Nothing here blocks anything by itself: `engine/market.py` calls `check_all` when KAIBOT_DATA_CHECKS is set (it
then only logs) or when KAIBOT_DATA_CHECKS_BLOCK is set (then the named checks block ENTRIES: `LiveMarket.data_block`,
docs/ops/failsafe.md). Both are off by default. The limits below are first guesses, not calibrated: today's bars are
IEX, where a quiet symbol has no bar in a minute without anything being wrong (so `bar_gap` is not in the blocking
default). Turn the block on only after the report has shown them quiet on real sessions.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

STALE, NO_BARS, BAD_PRICE, BAR_GAP, UNMATCHED_MOVE = "stale", "no_bars", "bad_price", "bar_gap", "unmatched_move"
PRICE_COLUMNS = ("o", "h", "l", "c")


@dataclass(frozen=True)
class Limits:
    """First guesses (see the module text). Minutes are minutes of the regular session, 0 = 09:30 ET."""

    max_age_s: float = 300.0  # the last completed bar may have ended at most this long ago
    lookback_min: int = 30  # the window of the bar-count check
    min_coverage: float = 0.5  # bars present / minutes in the window
    max_gap_min: int = 15  # longest run of minutes without a bar inside the window
    move_window_min: int = 5  # the window of the move check
    move_pct: float = 0.05  # a move at least this large ...
    index_pct: float = 0.01  # ... while the index moved less than this


DEFAULT = Limits()


@dataclass(frozen=True)
class Finding:
    symbol: str
    check: str
    value: float
    limit: float
    detail: str


# ---------------------------------------------------------------- the four checks


def stale_seconds(last_bar_end: datetime | None, now: datetime, max_age_s: float) -> float | None:
    """How old the last completed bar is, in seconds, when that is more than `max_age_s`; None when it is fresh.
    No bar at all is infinitely old. A bar that ends after `now` (a clock that disagrees) is not stale."""
    if last_bar_end is None:
        return math.inf
    age = (now - last_bar_end).total_seconds()
    return age if age > max_age_s else None


def bad_price_rows(bars: pd.DataFrame | None, columns: Sequence[str] = PRICE_COLUMNS) -> list[int]:
    """Positions (0-based, in the frame's order) of the bars with a price that is zero, negative, missing or not
    finite. Only the columns the frame has are looked at."""
    if bars is None or not len(bars):
        return []
    cols = [c for c in columns if c in bars.columns]
    if not cols:
        return []
    px = bars[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    ok = (px > 0) & (px < math.inf)  # NaN compares false
    return [int(i) for i in (~ok.all(axis=1)).nonzero()[0]]


def bar_gaps(minutes: Iterable[int], upto_m: int, lookback_min: int) -> tuple[int, int, int]:
    """(bars present, minutes expected, longest run of minutes without a bar) over the last `lookback_min`
    minutes that end with minute `upto_m`, never reaching before the open (minute 0). A minute counts once
    however many bars carry it. Before the first completed minute (`upto_m` < 0) everything is 0."""
    if upto_m < 0 or lookback_min <= 0:
        return 0, 0, 0
    first = max(0, upto_m - lookback_min + 1)
    have = {int(m) for m in minutes if first <= int(m) <= upto_m}
    longest = run = 0
    for m in range(first, upto_m + 1):
        run = 0 if m in have else run + 1
        longest = max(longest, run)
    return len(have), upto_m - first + 1, longest


def window_return(closes: Mapping[int, float], upto_m: int, window_min: int) -> float | None:
    """The return from the last close at or before minute `upto_m - window_min` to the last close at or before
    `upto_m`. None when either end is missing or not a price above zero."""
    def last_at(m: int) -> float | None:
        ms = [k for k in closes if k <= m]
        return float(closes[max(ms)]) if ms else None

    a, b = last_at(upto_m - window_min), last_at(upto_m)
    if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)) or a <= 0 or b <= 0:
        return None
    return b / a - 1.0


def unmatched_move(symbol_ret: float | None, index_ret: float | None, move_pct: float,
                   index_pct: float) -> bool:
    """True when the symbol moved at least `move_pct` (either way) while the index moved less than `index_pct`.
    Without both returns there is nothing to compare, so False: a missing index is the staleness check's finding."""
    if symbol_ret is None or index_ret is None:
        return False
    return abs(symbol_ret) >= move_pct and abs(index_ret) < index_pct


# ---------------------------------------------------------------- on bar frames


def _minutes(bars: pd.DataFrame | None, open_utc: datetime) -> list[int]:
    if bars is None or not len(bars):
        return []
    ts = pd.to_datetime(bars["ts"], utc=True)
    return [int(x) for x in ((ts - pd.Timestamp(open_utc)).dt.total_seconds() // 60)]


def _closes(bars: pd.DataFrame | None, open_utc: datetime) -> dict[int, float]:
    if bars is None or not len(bars):
        return {}
    c = pd.to_numeric(bars["c"], errors="coerce")
    return {m: float(v) for m, v in zip(_minutes(bars, open_utc), c, strict=True)}  # a later bar of a minute wins


def check_symbol(symbol: str, bars: pd.DataFrame | None, index_bars: pd.DataFrame | None, open_utc: datetime,
                 now: datetime, limits: Limits = DEFAULT) -> list[Finding]:
    """The findings for one symbol at `now`. Before the first minute of the session has completed there is
    nothing to check. A symbol without a bar today gets the one finding `no_bars`."""
    upto_m = int((now - open_utc).total_seconds() // 60) - 1
    if upto_m < 0:
        return []
    minutes = _minutes(bars, open_utc)
    if not minutes:
        return [Finding(symbol, NO_BARS, math.inf, limits.max_age_s, f"no bar today by minute {upto_m}")]
    out: list[Finding] = []
    last_end = pd.Timestamp(open_utc) + pd.Timedelta(minutes=max(minutes) + 1)
    age = stale_seconds(last_end.to_pydatetime(), now, limits.max_age_s)
    if age is not None:
        out.append(Finding(symbol, STALE, age, limits.max_age_s,
                           f"last bar ended {age:.0f} s ago (limit {limits.max_age_s:.0f} s)"))
    rows = bad_price_rows(bars)
    if rows:
        out.append(Finding(symbol, BAD_PRICE, float(len(rows)), 0.0,
                           f"{len(rows)} bar(s) with a zero, negative or missing price; first at row {rows[0]}"))
    have, expected, longest = bar_gaps(minutes, upto_m, limits.lookback_min)
    if expected and have / expected < limits.min_coverage:
        out.append(Finding(symbol, BAR_GAP, have / expected, limits.min_coverage,
                           f"{have} of {expected} minute bars in the last {expected} minutes"))
    elif longest > limits.max_gap_min:
        out.append(Finding(symbol, BAR_GAP, float(longest), float(limits.max_gap_min),
                           f"{longest} minutes in a row without a bar (limit {limits.max_gap_min})"))
    sym_ret = window_return(_closes(bars, open_utc), upto_m, limits.move_window_min)
    idx_ret = window_return(_closes(index_bars, open_utc), upto_m, limits.move_window_min)
    if unmatched_move(sym_ret, idx_ret, limits.move_pct, limits.index_pct):
        out.append(Finding(symbol, UNMATCHED_MOVE, float(sym_ret), limits.move_pct,
                           f"moved {sym_ret:+.2%} in {limits.move_window_min} minutes while the index moved "
                           f"{idx_ret:+.2%}"))
    return out


def check_all(today_bars: Mapping[str, pd.DataFrame], symbols: Iterable[str], index: str, open_utc: datetime,
              now: datetime, limits: Limits = DEFAULT) -> list[Finding]:
    """`check_symbol` for every symbol and for the index itself (which has no move check against itself), in a
    fixed order: the index first, then the symbols sorted."""
    index_bars = today_bars.get(index)
    out = [f for f in check_symbol(index, index_bars, None, open_utc, now, limits)]
    for s in sorted(set(symbols) - {index}):
        out.extend(check_symbol(s, today_bars.get(s), index_bars, open_utc, now, limits))
    return out
