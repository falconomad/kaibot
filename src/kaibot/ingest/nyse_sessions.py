"""NYSE regular sessions from rules, offline (story S85, #125). Research data only.

Full-day closures: New Year's Day, Martin Luther King Jr. Day, Washington's Birthday, Good Friday, Memorial Day,
Juneteenth (from 2022), Independence Day, Labor Day, Thanksgiving, Christmas, with NYSE's observance rule (a
Saturday holiday is taken the Friday before, except New Year's Day, which is not moved into December; a Sunday
holiday the Monday after), plus the unscheduled closures since 2016 (`SPECIAL_CLOSURES`). Every regular session
opens at 09:30 ET; it closes at 16:00 ET, or 13:00 ET on the early-close days (`early_close`).

The research daily store (#124) has Alpaca's real session dates; `scripts/fetch_earnings_dates.py --coverage`
compares the two and lists any difference.
"""

from __future__ import annotations

import bisect
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
SPECIAL_CLOSURES = {
    date(2018, 12, 5): "national day of mourning, George H. W. Bush",
    date(2025, 1, 9): "national day of mourning, Jimmy Carter",
}


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, weekday: int) -> date:
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(y: int) -> date:  # anonymous Gregorian algorithm
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    j = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * j) // 451
    month = (h + j - 7 * m + 114) // 31
    day = (h + j - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def _observed(d: date) -> date | None:
    if d.weekday() == 5:
        return None if (d.month, d.day) == (1, 1) else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(y: int) -> frozenset[date]:
    fixed = [date(y, 1, 1), date(y, 7, 4), date(y, 12, 25)] + ([date(y, 6, 19)] if y >= 2022 else [])
    out = {o for d in fixed if (o := _observed(d)) is not None}
    out |= {_nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _easter(y) - timedelta(days=2),
            _last_weekday(y, 5, 0), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4)}
    out |= {d for d in SPECIAL_CLOSURES if d.year == y}
    return frozenset(out)


def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def early_close(d: date) -> bool:
    """13:00 ET close: the day after Thanksgiving, and 24 Dec / 3 Jul when they are sessions."""
    if not is_session(d):
        return False
    return (d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1)
            or (d.month, d.day) in {(12, 24), (7, 3)})


@lru_cache(maxsize=8)
def _sessions(lo: int, hi: int) -> tuple[date, ...]:
    d, end, out = date(lo, 1, 1), date(hi, 12, 31), []
    while d <= end:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def sessions(start: date, end: date) -> list[date]:
    """Regular sessions with start <= d <= end."""
    allv = _sessions(min(start.year, 2010), max(end.year, 2030))
    return list(allv[bisect.bisect_left(allv, start):bisect.bisect_right(allv, end)])


def open_utc(d: date) -> datetime:
    return datetime.combine(d, OPEN, ET).astimezone(UTC)


def close_utc(d: date) -> datetime:
    return datetime.combine(d, EARLY_CLOSE if early_close(d) else CLOSE, ET).astimezone(UTC)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(UTC)


def first_session_opening_after(ts: datetime) -> date:
    """The first regular session whose 09:30 ET open is strictly after `ts`: the first session in which a market
    participant could trade on news released at `ts` from the opening bell on."""
    ts = _utc(ts)
    d = ts.astimezone(ET).date()
    while not (is_session(d) and open_utc(d) > ts):
        d += timedelta(days=1)
    return d


def last_session_closed_by(ts: datetime) -> date:
    """The last regular session whose close is at or before `ts`: a position must be flat after this session's
    close to be out of the market when the news at `ts` lands."""
    ts = _utc(ts)
    d = ts.astimezone(ET).date()
    while not (is_session(d) and close_utc(d) <= ts):
        d -= timedelta(days=1)
    return d
