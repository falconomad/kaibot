"""Daily snapshot of the free forward earnings calendar (story S85, #125). Research data only.

EDGAR says when a company DID report. Whether a company has announced WHEN it will report is only point-in-time if
somebody wrote it down on that day, so this job archives the calendar once a day from now on: every row carries the
time it was seen (`snapshot_ts`), and readers use only snapshots taken at or before their decision time.

Source: Finnhub `GET /api/v1/calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD` (date, hour bmo/amc/dmh, symbol,
quarter, year, estimates), with the FINNHUB_API_KEY already in the .env, sent as the X-Finnhub-Token header (never in
the URL or a log). Requests count against the `finnhub` budget (config/sources.yaml, 45/min; a snapshot is
10 requests (64 days in 7-day chunks)). Whether the free plan serves this endpoint is checked by the first run: a 401
/ 403 / 429 is reported as "unavailable" and the job exits 0 without writing anything, so a nightly schedule never
fails noisily (see docs/ops/earnings_dates.md for the fallback).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
URL = "https://finnhub.io/api/v1/calendar/earnings"
BUDGET = "finnhub"
SOURCE = "finnhub_calendar"
HORIZON_DAYS, CHUNK_DAYS = 63, 7
COLUMNS = ["snapshot_ts", "symbol", "date", "hour", "year", "quarter", "eps_estimate", "revenue_estimate", "source"]
HOURS = {"bmo", "amc", "dmh"}


class Unavailable(RuntimeError):
    pass


def finnhub_getter(token: str) -> Callable[[str, dict], bytes]:
    from kaibot.common import http

    def get(url: str, params: dict) -> bytes:
        try:
            r = http.get(url, source=BUDGET, params=params, headers={"X-Finnhub-Token": token}, timeout=30,
                         max_bytes=32 * 1024 * 1024, attempts=3)
        except http.HttpError as e:
            if e.status in (0, 401, 403, 429):
                raise Unavailable(f"finnhub calendar: HTTP {e.status}") from None
            raise
        return r.body

    return get


def _num(v) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse(doc: object, symbols: Iterable[str] | None, snapshot_ts: datetime) -> list[dict]:
    """Rows of one response (untrusted JSON), filtered to `symbols` (changes.csv style: BRK.B)."""
    rows = doc.get("earningsCalendar") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError("finnhub calendar: unexpected schema")
    keep = None if symbols is None else set(symbols)
    out = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("symbol"), str):
            continue
        sym = r["symbol"].upper().replace("-", ".")[:12]
        if keep is not None and sym not in keep:
            continue
        try:
            d = date.fromisoformat(str(r.get("date")))
        except ValueError:
            continue
        hour = str(r.get("hour") or "").lower()
        out.append({"snapshot_ts": snapshot_ts, "symbol": sym, "date": d, "hour": hour if hour in HOURS else "",
                    "year": int(r["year"]) if isinstance(r.get("year"), int) else None,
                    "quarter": int(r["quarter"]) if isinstance(r.get("quarter"), int) else None,
                    "eps_estimate": _num(r.get("epsEstimate")), "revenue_estimate": _num(r.get("revenueEstimate")),
                    "source": SOURCE})
    return out


def snapshot(get: Callable[[str, dict], bytes], today: date, symbols: Iterable[str] | None,
             now: datetime | None = None, horizon_days: int = HORIZON_DAYS, chunk_days: int = CHUNK_DAYS):
    """One snapshot (DataFrame, COLUMNS) of the calendar from `today` to `today + horizon_days`."""
    import pandas as pd

    now = (now or datetime.now(UTC)).astimezone(UTC)
    syms = None if symbols is None else sorted(set(symbols))
    rows: list[dict] = []
    d = today
    end = today + timedelta(days=horizon_days)
    while d <= end:
        hi = min(d + timedelta(days=chunk_days - 1), end)
        rows += parse(json.loads(get(URL, {"from": d.isoformat(), "to": hi.isoformat()})), syms, now)
        d = hi + timedelta(days=1)
    df = pd.DataFrame(rows, columns=COLUMNS).drop_duplicates(["symbol", "date", "hour"], ignore_index=True)
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], utc=True)
    return df.sort_values(["date", "symbol"], ignore_index=True)


def snapshot_path(root: Path, day: date) -> Path:
    return Path(root) / f"snapshot={day.isoformat()}.parquet"


def write_snapshot(df, root: Path, day: date) -> Path:
    p = snapshot_path(root, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(p)
    return p


def read_snapshots(root: Path):
    import pandas as pd

    files = sorted(Path(root).glob("snapshot=*.parquet"))
    if not files:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
