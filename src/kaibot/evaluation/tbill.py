"""The Treasury-bill rate file (M11 ticket 2.4): read, write and check the cache of FRED series DTB3.

`scripts/fetch_tbill.py` writes `data/inputs/fred_dtb3.json` on the research machine; reports read it here and
turn it into each session's return with `mtm.tbill_daily`. Nothing here is imported by the engine.

The file:

    {"series": "DTB3", "units": "percent, bank discount basis", "fetched_at": "<UTC time>",
     "observations": [{"date": "2016-01-04", "value": 0.22}, ...]}

Point in time. The rate of a day is fixed at that day's close and published afterwards; `mtm.tbill_daily` gives
session t the last rate dated strictly before t. A daily market rate is not revised later, so the file read
today holds the values that were known then; `fetched_at` records when it was read.

Standard library and pandas only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

SERIES = "DTB3"  # 3-Month Treasury Bill Secondary Market Rate, Discount Basis (daily, percent)
FILE = Path("inputs") / "fred_dtb3.json"  # under the data directory
UNITS = "percent, bank discount basis"


def parse_observations(observations) -> list[dict]:
    """FRED's observations as [{"date", "value"}], sorted, without the days FRED marks "." (market closed).
    A value that is not a number, or a rate outside -1% to 25%, is an error: a wrong series or a unit mix-up
    must not become a cash return."""
    out = []
    for o in observations:
        v = o.get("value")
        if v in (".", "", None):
            continue
        x = float(v)
        if not -1.0 <= x <= 25.0:
            raise ValueError(f"{SERIES} on {o.get('date')}: {x} percent is outside -1 to 25")
        out.append({"date": str(pd.Timestamp(o["date"]).date()), "value": x})
    out.sort(key=lambda r: r["date"])
    if len({r["date"] for r in out}) != len(out):
        raise ValueError(f"{SERIES}: the same date twice")
    return out


def write_cache(path: Path | str, observations, fetched_at: datetime | None = None) -> dict:
    doc = {"series": SERIES, "units": UNITS,
           "fetched_at": (fetched_at or datetime.now(UTC)).isoformat(timespec="seconds"),
           "observations": parse_observations(observations)}
    if not doc["observations"]:
        raise ValueError(f"{SERIES}: no observation to write")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))
    return doc


def read_cache(path: Path | str) -> pd.Series:
    """The rate in percent, indexed by observation date. Raises `FileNotFoundError` when the file is missing."""
    doc = json.loads(Path(path).read_text())
    if doc.get("series") != SERIES:
        raise ValueError(f"{path}: series {doc.get('series')!r}, expected {SERIES}")
    obs = parse_observations(doc["observations"])
    return pd.Series([o["value"] for o in obs], index=pd.DatetimeIndex([o["date"] for o in obs]), name=SERIES)


def find(data: Path | str) -> pd.Series | None:
    """The cached rate under a data directory, or None when it has not been fetched."""
    f = Path(data) / FILE
    return read_cache(f) if f.is_file() else None
