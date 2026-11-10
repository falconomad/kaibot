"""GDELT DOC 2.0 API: macro / geopolitical tone and volume timelines (research 03 and 05), logged for M3.

Endpoint: https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=timelinetone|timelinevolraw&format=json
          &startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS   (UTC; the API covers the last ~3 months)
Response: {"query_details": {...}, "timeline": [{"series": "...", "data": [{"date": "20261109T001500Z",
          "value": -1.23, "norm": 12345 (volraw only)}, ...]}]}  (15-minute buckets for short ranges).
GDELT allows one request every 5 s and answers faster callers with 429 + a plain-text note; we pace at 6 s
(config/sources.yaml gdelt_doc). It sometimes answers a bad query with 200 text/html: treated as an error.

Point-in-time: GDELT revises recent buckets as articles arrive, so a bucket's value is only known as of our fetch:
available_ts = ingested_ts (tech design §2: alt data -> ingest time). Re-running a day appends only buckets whose
value changed, as new versions; unchanged ones are not duplicated.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa

from kaibot.common import http
from kaibot.ingest.alt import store

log = logging.getLogger(__name__)
SOURCE = "gdelt_doc"  # budget name in config/sources.yaml and folder under data/alt/
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MODES = ("timelinetone", "timelinevolraw")
# Macro / geo themes for the "external factor" thesis (research 03). Keep names stable: they are partition keys.
QUERIES = {
    "tariffs": "(tariff OR tariffs OR \"trade war\") sourcelang:english",
    "sanctions": "(sanctions OR embargo) sourcelang:english",
    "fed_rates": "(\"federal reserve\" OR \"interest rates\" OR \"rate cut\" OR \"rate hike\") sourcelang:english",
    "conflict": "(war OR invasion OR airstrike OR missile) sourcelang:english",
    "energy": "(opec OR \"oil prices\" OR \"oil supply\") sourcelang:english",
    "recession": "(recession OR layoffs OR \"jobless claims\") sourcelang:english",
}
MAX_BYTES = 4 * 1024 * 1024
MAX_POINTS = 5000
_DATE = re.compile(r"^\d{8}T\d{6}Z$")
SCHEMA = pa.schema([
    ("query_name", pa.string()),
    ("query", pa.string()),
    ("mode", pa.string()),
    ("series", pa.string()),
    ("bucket_ts", store.TS),
    ("value", pa.float64()),
    ("norm", pa.float64()),
    ("available_ts", store.TS),
    ("ingested_ts", store.TS),
])
KEY = ["query_name", "mode", "series", "bucket_ts"]
VALUES = ["value", "norm"]


class BadPayload(ValueError):
    pass


def _finite(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


Point = tuple[str, datetime, float, float | None]


def parse_timeline(body: bytes, content_type: str = "application/json") -> list[Point]:
    """Validate a timeline response -> [(series, bucket_ts, value, norm)]. Bad points are skipped."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if not text.startswith("{"):
        raise BadPayload(f"not JSON ({content_type or 'no content type'}): {text[:120]!r}")
    try:
        doc = json.loads(text, strict=False)  # GDELT sometimes embeds raw control characters
    except ValueError as e:
        raise BadPayload(f"invalid JSON: {e}") from None
    if not isinstance(doc, dict):
        raise BadPayload("expected a JSON object")
    timeline = doc.get("timeline", [])
    if not isinstance(timeline, list):
        raise BadPayload("timeline is not a list")
    out, bad = [], 0
    for s in timeline:
        if not isinstance(s, dict) or not isinstance(s.get("series"), str) or not isinstance(s.get("data"), list):
            bad += 1
            continue
        name = s["series"][:100]
        for p in s["data"][:MAX_POINTS]:
            d = p.get("date") if isinstance(p, dict) else None
            v = _finite(p.get("value")) if isinstance(p, dict) else None
            if not isinstance(d, str) or not _DATE.match(d) or v is None or abs(v) > 1e12:
                bad += 1
                continue
            try:
                ts = datetime.strptime(d, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            except ValueError:
                bad += 1
                continue
            out.append((name, ts, v, _finite(p.get("norm"))))
    if bad:
        log.warning("gdelt: skipped %d malformed series/points", bad)
    return out


def _window(day: date, now: datetime) -> tuple[datetime, datetime] | None:
    start = datetime.combine(day, time(0), UTC)
    end = min(start + timedelta(days=1), now)
    return (start, end) if end > start else None


def collect(root: Path, day: date, *, now: datetime | None = None, queries: dict[str, str] | None = None,
            **http_kw) -> int:
    """Fetch every query x mode for UTC day `day` (up to `now`). Returns rows written; bad queries are skipped."""
    now = now or datetime.now(UTC)
    w = _window(day, now)
    if w is None:
        return 0
    fmt = "%Y%m%d%H%M%S"
    written = 0
    for qname, q in (queries or QUERIES).items():
        for mode in MODES:
            params = {"query": q, "mode": mode, "format": "json",
                      "startdatetime": w[0].strftime(fmt), "enddatetime": w[1].strftime(fmt)}
            try:
                r = http.get(URL, source=SOURCE, params=params, timeout=60, max_bytes=MAX_BYTES,
                             headers={"User-Agent": "kaibot-alt/0.1"}, **http_kw)
                pts = parse_timeline(r.body, r.content_type)
            except (http.HttpError, BadPayload) as e:
                log.error("gdelt %s %s %s: skipped: %s", day, qname, mode, e)
                continue
            pts = [p for p in pts if p[1].date() == day]
            if not pts:
                continue
            n = len(pts)
            table = pa.table({
                "query_name": [qname] * n, "query": [q] * n, "mode": [mode] * n,
                "series": [p[0] for p in pts],
                "bucket_ts": pa.array([p[1] for p in pts], store.TS),
                "value": pa.array([p[2] for p in pts], pa.float64()),
                "norm": pa.array([p[3] for p in pts], pa.float64()),
                "available_ts": pa.array([now] * n, store.TS),
                "ingested_ts": pa.array([now] * n, store.TS),
            }, schema=SCHEMA)
            written += store.append_versions(root, SOURCE, day, table, KEY, VALUES)
    log.info("gdelt %s: %d rows written", day, written)
    return written
