"""Download historical 1-minute SIP bars and news from Alpaca into Parquet (resumable, rate-limited).

Point-in-time: SIP bars are only requested up to now - 15 min (free plan rule). Each bar's available_ts is
its end time (bar start + 1 min); features may only read bars with available_ts <= decision_ts.
Layout: data/bars_1m/symbol=XYZ/YYYY-MM.parquet ; data/news/symbol=XYZ/YYYY.parquet
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATA = "https://data.alpaca.markets"


class Alpaca:
    def __init__(self, key: str, secret: str, per_min: int = 150):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "User-Agent": "kaibot/0.1"}
        self.min_gap = 60.0 / per_min
        self._last = 0.0

    def get(self, path: str, params: dict) -> dict:
        wait = self._last + self.min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        url = f"{DATA}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(5):
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=self.h), timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
        raise RuntimeError(f"giving up on {path}")


def month_ranges(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=UTC)
        yield max(cur, start), min(nxt, end), cur.strftime("%Y-%m")
        cur = nxt


def fetch_bars(api: Alpaca, symbol: str, start: datetime, end: datetime, out: Path) -> int:
    rows = []
    token = None
    while True:
        p = {"timeframe": "1Min", "start": start.isoformat(), "end": end.isoformat(), "limit": 10000,
             "feed": "sip", "adjustment": "split"}
        if token:
            p["page_token"] = token
        d = api.get(f"/v2/stocks/{symbol}/bars", p)
        for b in d.get("bars") or []:
            rows.append(b)
        token = d.get("next_page_token")
        if not token:
            break
    if not rows:
        return 0
    ts = [datetime.fromisoformat(b["t"].replace("Z", "+00:00")) for b in rows]
    table = pa.table({
        "ts": pa.array(ts, pa.timestamp("s", tz="UTC")),
        "available_ts": pa.array([t + timedelta(minutes=1) for t in ts], pa.timestamp("s", tz="UTC")),
        "o": [b["o"] for b in rows], "h": [b["h"] for b in rows], "l": [b["l"] for b in rows],
        "c": [b["c"] for b in rows], "v": [float(b["v"]) for b in rows],
        "n": [int(b.get("n", 0)) for b in rows], "vw": [float(b.get("vw", b["c"])) for b in rows],
    })
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out)
    return table.num_rows


def fetch_news(api: Alpaca, symbol: str, start: datetime, end: datetime, out: Path) -> int:
    rows, token = [], None
    while True:
        p = {"symbols": symbol, "start": start.isoformat(), "end": end.isoformat(), "limit": 50, "sort": "asc"}
        if token:
            p["page_token"] = token
        d = api.get("/v1beta1/news", p)
        rows += d.get("news") or []
        token = d.get("next_page_token")
        if not token:
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "created_at": pa.array([datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")) for n in rows],
                               pa.timestamp("s", tz="UTC")),
        "headline": [n.get("headline", "") for n in rows],
        "source": [n.get("source", "") for n in rows],
        "symbols": [",".join(n.get("symbols", [])) for n in rows],
    }), out)
    return len(rows)
