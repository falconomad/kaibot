"""Scheduled macro events and their entry blackouts (story S15: no new entries +-30 min of FOMC or CPI).

config/macro_calendar.yaml lists dates per event type; each type has a fixed ET release time. A blackout is
expressed in session minutes (0 = 09:30 ET). CPI is released at 08:30, before the open, so it never blocks a
session minute today, but the rule is generic: if a release time ever moves into the session it is covered.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

WINDOW_MIN = 30
OPEN_MIN = 9 * 60 + 30
EVENT_TIMES = {"fomc_statement_1400": (14, 0), "cpi_0830": (8, 30)}


def load_calendar(path: Path) -> tuple[dict[date, list[tuple[str, int]]], date | None]:
    """-> ({day: [(event, minute_of_session)]}, covers_until). Unknown keys are rejected (typos fail loudly)."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    covers = raw.pop("covers_until", None)
    out: dict[date, list[tuple[str, int]]] = {}
    for key, days in raw.items():
        if key not in EVENT_TIMES:
            raise ValueError(f"unknown event type in macro calendar: {key}")
        hh, mm = EVENT_TIMES[key]
        for d in days or []:
            day = d if isinstance(d, date) else date.fromisoformat(str(d))
            out.setdefault(day, []).append((key, hh * 60 + mm - OPEN_MIN))
    if isinstance(covers, str):
        covers = date.fromisoformat(covers)
    return out, covers


def in_blackout(events: dict[date, list[tuple[str, int]]], day: date, m: int) -> str | None:
    """The event blacking out decision minute m on `day`, or None."""
    for name, em in events.get(day, ()):
        if em - WINDOW_MIN <= m <= em + WINDOW_MIN:
            return name
    return None


def from_fomc_dates(fomc) -> dict[date, list[tuple[str, int]]]:
    """Back-compat: a set of FOMC dates (numpy datetime64 or date) -> events at 14:00."""
    out: dict[date, list[tuple[str, int]]] = {}
    for d in fomc or ():
        day = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        out.setdefault(day, []).append(("fomc_statement_1400", 14 * 60 - OPEN_MIN))
    return out
