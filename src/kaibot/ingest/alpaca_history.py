"""Download historical 1-minute SIP bars and news from Alpaca into Parquet (resumable, budgeted).

Point-in-time: SIP bars are only requested up to now - 15 min (free plan rule). Each bar's available_ts is
its end time (bar start + 1 min); features may only read bars with available_ts <= decision_ts.
Layout: data/bars_1m/symbol=XYZ/YYYY-MM.parquet (.empty = no bars that month) ; data/news/symbol=XYZ/YYYY.parquet
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from kaibot.common import http
from kaibot.common.ratelimit import REGISTRY, Budget, BudgetSpec, get_budget

DATA = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")
SIP_DELAY = timedelta(minutes=20)  # free plan: SIP only when >= 15 min old; 20 leaves a margin
SYMBOL_RE = re.compile(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?")  # US tickers as Alpaca writes them: NBIS, BRK.B


def parse_symbols(symbols: str | Iterable[str]) -> list[str]:
    """Explicit download list ("NBIS,CRWV" or a list) -> upper-case tickers, order kept, duplicates dropped.

    A symbol becomes a URL path segment and a folder name (data/bars_1m/symbol=XYZ), so anything that is not a
    plain ticker is refused here, before any request or file. Raises ValueError naming every bad entry."""
    raw = symbols.split(",") if isinstance(symbols, str) else list(symbols)
    out = list(dict.fromkeys(str(s).strip().upper() for s in raw if str(s).strip()))
    bad = [s for s in out if not SYMBOL_RE.fullmatch(s)]
    if bad:
        raise ValueError(f"not a ticker symbol: {', '.join(repr(s) for s in bad)} (expected e.g. NBIS or BRK.B)")
    if not out:
        raise ValueError("empty symbol list")
    return out


class Alpaca:
    """Market-data GETs under the shared `alpaca` budget (config/sources.yaml), so the downloader and the engine's
    AlpacaData never add up past it within one process. `per_min` optionally ADDS a tighter per-minute cap for
    this process (parallel workers are separate processes: give each its share)."""

    def __init__(self, key: str, secret: str, per_min: int | None = None, budget: Budget | None = None):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "User-Agent": "kaibot/0.1"}
        if budget is None and per_min:
            budget = REGISTRY.tighten("alpaca", BudgetSpec(((int(per_min), 60.0),)))
        self.budget = budget or get_budget("alpaca")

    def get(self, path: str, params: dict) -> dict:
        return http.get_json(f"{DATA}{path}", source="alpaca", params=params, headers=self.h, timeout=60,
                             budget=self.budget, max_bytes=64 * 1024 * 1024)


def month_ranges(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=UTC)
        yield max(cur, start), min(nxt, end), cur.strftime("%Y-%m")
        cur = nxt


def is_rth(t: datetime) -> bool:
    """09:30-15:59 ET bar start (the only minutes the replay reads); DST handled via zoneinfo."""
    et = t.astimezone(ET)
    m = et.hour * 60 + et.minute
    return 570 <= m < 960


def fetch_bars(api: Alpaca, symbol: str, start: datetime, end: datetime, out: Path, rth_only: bool = False) -> int:
    """Write one month of bars to `out`. A month with no bars (before a listing) leaves an `.empty` marker so a
    resumed download skips it."""
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
    ts = [datetime.fromisoformat(b["t"].replace("Z", "+00:00")) for b in rows]
    if rth_only:
        keep = [i for i, t in enumerate(ts) if is_rth(t)]
        rows, ts = [rows[i] for i in keep], [ts[i] for i in keep]
    if not rows:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.with_suffix(".empty").touch()
        return 0
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
