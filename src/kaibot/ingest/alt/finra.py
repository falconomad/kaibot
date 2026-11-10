"""FINRA Reg SHO daily short-sale volume, consolidated NMS file (CNMS), one file per trade date.

Source (FINRA "Daily Short Sale Volume Files", checked 10 Nov 2026):
  https://cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt
  Pipe-delimited; first row is the header Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market; the last
  row is a trailer with the record count. Covers off-exchange (TRF/ADF/ORF) volume in NMS stocks only, regular
  trading hours. Posted no later than 18:00:00 ET on the trade date; "in rare instances" re-posted on a later day.

Point-in-time: available_ts = max(18:00 ET on the trade date, the file's Last-Modified). A re-posted file only adds
the rows whose numbers changed, as new versions dated by Last-Modified (or our ingest time if the CDN has none).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa

from kaibot.common import http
from kaibot.ingest.alt import store

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SOURCE = "finra_regsho"  # budget name in config/sources.yaml and folder under data/alt/
URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
HEADER = ["Date", "Symbol", "ShortVolume", "ShortExemptVolume", "TotalVolume", "Market"]
PUBLISH_ET = time(18, 0)
MAX_BYTES = 32 * 1024 * 1024  # a CNMS file is well under 1 MB; anything near this is not a FINRA file
MAX_BAD_FRACTION = 0.01
_SYMBOL = re.compile(r"^[A-Za-z0-9.\-+/$^=#]{1,14}$")
_MARKET = re.compile(r"^[A-Z](,[A-Z])*$")
_INT = re.compile(r"^\d{1,15}$")
SCHEMA = pa.schema([
    ("trade_date", pa.date32()),
    ("symbol", pa.string()),
    ("short_volume", pa.int64()),
    ("short_exempt_volume", pa.int64()),
    ("total_volume", pa.int64()),
    ("market", pa.string()),
    ("available_ts", store.TS),
    ("ingested_ts", store.TS),
])
KEY = ["trade_date", "symbol"]
VALUES = ["short_volume", "short_exempt_volume", "total_volume", "market"]


class BadFile(ValueError):
    pass


def publication_ts(day: date) -> datetime:
    return datetime.combine(day, PUBLISH_ET, ET).astimezone(UTC)


def parse_cnms(text: str, day: date) -> list[tuple]:
    """Validate and parse one CNMS file. Bad rows are logged and skipped; a file that is mostly bad raises BadFile."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise BadFile("empty file")
    if lines[0].split("|") != HEADER:
        raise BadFile(f"unexpected header {lines[0][:120]!r}")
    body = lines[1:]
    trailer = None
    if body and _INT.match(body[-1]):
        trailer = int(body[-1])
        body = body[:-1]
    want = day.strftime("%Y%m%d")
    rows, bad = [], 0
    for ln in body:
        f = ln.split("|")
        if (
            len(f) != 6
            or f[0] != want
            or not _SYMBOL.match(f[1])
            or not all(_INT.match(x) for x in f[2:5])
            or not _MARKET.match(f[5])
        ):
            bad += 1
            if bad <= 5:
                log.warning("finra %s: skipping bad row %r", day, ln[:120])
            continue
        rows.append((f[1].upper(), int(f[2]), int(f[3]), int(f[4]), f[5]))
    if body and bad > max(10, MAX_BAD_FRACTION * len(body)):
        raise BadFile(f"{bad} of {len(body)} rows invalid: format changed?")
    if trailer is not None and trailer != len(body):
        log.warning("finra %s: trailer says %d records, file has %d", day, trailer, len(body))
    return rows


def collect(root: Path, day: date, *, now: datetime | None = None, **http_kw) -> int:
    """Fetch and store one trade date. Returns rows written (0 = not published / weekend / already stored)."""
    now = now or datetime.now(UTC)
    if now < publication_ts(day):
        log.info("finra %s: not published before 18:00 ET, skipping", day)
        return 0
    url = URL.format(d=day.strftime("%Y%m%d"))
    r = http.get(url, source=SOURCE, timeout=60, max_bytes=MAX_BYTES, ok_statuses=(403, 404),
                 headers={"User-Agent": "kaibot-alt/0.1"}, **http_kw)
    if r.status in (403, 404):
        log.info("finra %s: no file (holiday, weekend or not yet posted)", day)
        return 0
    try:
        rows = parse_cnms(r.body.decode("utf-8", errors="replace"), day)
    except BadFile as e:
        log.error("finra %s: rejected file: %s", day, e)
        return 0
    lm = r.last_modified()
    avail = max(publication_ts(day), lm) if lm else publication_ts(day)
    n = len(rows)
    table = pa.table({
        "trade_date": pa.array([day] * n, pa.date32()),
        "symbol": [x[0] for x in rows],
        "short_volume": pa.array([x[1] for x in rows], pa.int64()),
        "short_exempt_volume": pa.array([x[2] for x in rows], pa.int64()),
        "total_volume": pa.array([x[3] for x in rows], pa.int64()),
        "market": [x[4] for x in rows],
        "available_ts": pa.array([avail] * n, store.TS),
        "ingested_ts": pa.array([now] * n, store.TS),
    }, schema=SCHEMA)
    written = store.append_versions(root, SOURCE, day, table, KEY, VALUES, revision_available_ts=lm or now)
    log.info("finra %s: %d rows parsed, %d written", day, n, written)
    return written


def default_days(now: datetime, lookback: int = 5) -> list[date]:
    """The last `lookback` weekdays whose file should be out by `now` (re-checked to pick up re-posted files)."""
    out, d = [], now.astimezone(ET).date()
    while len(out) < lookback:
        if d.weekday() < 5 and now >= publication_ts(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)
