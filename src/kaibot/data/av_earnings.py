"""Point-in-time reader of the Alpha Vantage EARNINGS cache (data/earnings/alphavantage/, from
scripts/fetch_av_earnings.py). Research only: no trading package may import this
(tests/unit/test_research_data_isolation.py), and nothing here is used for a live decision.

    store = AVEarnings(Path("data/earnings/alphavantage"))
    store.rows("AAPL")                                  # every quarterly row of the cache, oldest first
    store.known_as_of("AAPL", date(2020, 5, 1))         # only the rows usable at the 09:30 ET open of 1 May 2020

One row per fiscal quarter: fiscal_date_ending, reported_date, report_time ("pre-market", "post-market" or ""),
reported_eps, estimated_eps, surprise, surprise_pct. Alpha Vantage writes missing numbers as the string "None";
they become None here (never 0).

When a row is known (`usable_from`):
- post-market (after the close on reported_date): from the open of the first session AFTER reported_date;
- pre-market (before the open on reported_date): nominally from reported_date's own session; this reader takes
  the CLOSE of that session (decisions after its close), not its open, because Alpha Vantage gives no time of day;
- unknown or blank report time: treated as post-market (the later of the two).
`known_as_of(symbol, when, rule="conservative")` (the default, and the rule the earnings-surprise drift test of
decision 20 uses): a row is usable only from the OPEN of the first session strictly after reported_date, whatever
the report time. That is later than (or equal to) the nominal rule for every row, so it can never use a report
before it was public. `rule="nominal"` applies the two-case rule above.

Point in time, two caveats (documented in decision 20):
- reported_date and the EPS values are as Alpha Vantage shows them today, not as first published. A late
  restatement of reported EPS would be invisible. The cache keeps the raw answer with its download time.
- reported_eps and estimated_eps are split-adjusted (a later split rescales the history), so they are not the
  numbers printed on the day. surprise_pct (surprise / |estimate|) is a ratio of two numbers on the same basis, so
  a split does not change it: use surprise_pct, not the EPS levels, in any test.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kaibot.ingest import nyse_sessions as nyse

RAW_DIR = "raw"
REPORT_TIMES = ("pre-market", "post-market")


def _num(v) -> float | None:
    """AV numbers arrive as strings; "None", "", "-" and non-finite values are missing."""
    if v is None:
        return None
    if isinstance(v, int | float) and not isinstance(v, bool):
        f = float(v)
    else:
        s = str(v).strip()
        if s in ("", "None", "none", "null", "-", "N/A"):
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    return f if math.isfinite(f) else None


def _date(v) -> date | None:
    s = str(v or "").strip()
    if not s or s == "None":
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class EarningsRow:
    symbol: str
    fiscal_date_ending: date
    reported_date: date
    report_time: str            # "pre-market", "post-market" or "" (unknown)
    reported_eps: float | None  # split-adjusted
    estimated_eps: float | None  # split-adjusted
    surprise: float | None      # split-adjusted
    surprise_pct: float | None  # unaffected by splits

    # ------------------------------------------------------------ when the row is known
    def next_session_after_report(self) -> date:
        """The first regular NYSE session strictly after reported_date (the conservative entry session)."""
        d = self.reported_date + timedelta(days=1)
        while not nyse.is_session(d):
            d += timedelta(days=1)
        return d

    def usable_from(self, rule: str = "conservative") -> datetime:
        """The first moment (UTC) a decision may use this row."""
        if rule == "conservative":
            return nyse.open_utc(self.next_session_after_report())
        if rule != "nominal":
            raise ValueError(f"rule must be 'conservative' or 'nominal', got {rule!r}")
        if self.report_time == "pre-market" and nyse.is_session(self.reported_date):
            return nyse.close_utc(self.reported_date)
        return nyse.open_utc(self.next_session_after_report())


def parse(symbol: str, payload: dict) -> list[EarningsRow]:
    """The quarterly rows of one raw EARNINGS answer, oldest first. Rows without a fiscal date or a reported date
    are dropped (they cannot be placed in time)."""
    out = []
    for q in (payload or {}).get("quarterlyEarnings") or []:
        if not isinstance(q, dict):
            continue
        fde, rd = _date(q.get("fiscalDateEnding")), _date(q.get("reportedDate"))
        if fde is None or rd is None:
            continue
        rt = str(q.get("reportTime") or "").strip().lower()
        out.append(EarningsRow(symbol, fde, rd, rt if rt in REPORT_TIMES else "",
                               _num(q.get("reportedEPS")), _num(q.get("estimatedEPS")), _num(q.get("surprise")),
                               _num(q.get("surprisePercentage"))))
    out.sort(key=lambda r: (r.reported_date, r.fiscal_date_ending))
    return out


def _as_utc(when) -> datetime:
    """A date means that day's 09:30 ET open (a decision before the session trades); a datetime must be aware."""
    if isinstance(when, datetime):
        if when.tzinfo is None:
            raise ValueError("a datetime must be timezone-aware")
        return when.astimezone(UTC)
    if isinstance(when, date):
        return nyse.open_utc(when)
    raise TypeError(f"expected a date or datetime, got {type(when).__name__}")


def known(rows: list[EarningsRow], when, rule: str = "conservative") -> list[EarningsRow]:
    t = _as_utc(when)
    return [r for r in rows if r.usable_from(rule) <= t]


class AVEarnings:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, list[EarningsRow]] = {}

    def path(self, symbol: str) -> Path:
        return self.root / RAW_DIR / f"{symbol}.json"

    def symbols(self) -> list[str]:
        d = self.root / RAW_DIR
        return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []

    def rows(self, symbol: str) -> list[EarningsRow]:
        if symbol not in self._cache:
            p = self.path(symbol)
            self._cache[symbol] = parse(symbol, json.loads(p.read_text())) if p.is_file() else []
        return self._cache[symbol]

    def known_as_of(self, symbol: str, when, rule: str = "conservative") -> list[EarningsRow]:
        """The rows usable at `when` (a date: its 09:30 ET open), oldest first."""
        return known(self.rows(symbol), when, rule)

    def latest_as_of(self, symbol: str, when, rule: str = "conservative") -> EarningsRow | None:
        rows = self.known_as_of(symbol, when, rule)
        return rows[-1] if rows else None
