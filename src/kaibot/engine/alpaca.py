"""Alpaca market data over plain HTTPS (stdlib only, rate-limited, retried).

Free market-data plan: real-time bars and quotes come from the IEX feed; SIP is used only for history >= 15 min old.

The trading client moved to kaibot.broker.alpaca (issue #115: one Broker protocol). `AlpacaPaper`, `AlpacaError`
and `_Http` are re-exported here so that existing imports and tests keep working.
"""

from __future__ import annotations

import urllib.request  # noqa: F401  (kept: tests reach urllib through this module)
from datetime import UTC, datetime, timedelta

import pandas as pd

from kaibot.broker.alpaca import AlpacaError, AlpacaPaper, _Http  # noqa: F401  (re-exported)
from kaibot.engine.config import DATA_URL


class AlpacaData:
    """Market data endpoints."""

    def __init__(self, key: str, secret: str):
        self.http = _Http(key, secret)

    def _d(self, path, params):
        return self.http.call("GET", f"{DATA_URL}{path}", params)

    def bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        feed: str,
        timeframe: str = "1Min",
        adjustment: str = "split",
    ) -> dict[str, pd.DataFrame]:
        out: dict[str, list] = {s: [] for s in symbols}
        for i in range(0, len(symbols), 50):
            chunk = symbols[i : i + 50]
            token = None
            while True:
                p = {
                    "symbols": ",".join(chunk),
                    "timeframe": timeframe,
                    "start": _iso(start),
                    "end": _iso(end),
                    "limit": 10000,
                    "feed": feed,
                    "adjustment": adjustment,
                }
                if token:
                    p["page_token"] = token
                d = self._d("/v2/stocks/bars", p)
                for s, rows in (d.get("bars") or {}).items():
                    out.setdefault(s, []).extend(rows)
                token = d.get("next_page_token")
                if not token:
                    break
        return {s: _frame(rows) for s, rows in out.items()}

    def movers(self, top: int = 15) -> list[dict]:
        """Today's top gainers then losers (ported from bloodbath's get_top_movers; free screener endpoint)."""
        return parse_movers(self._d("/v1beta1/screener/stocks/movers", {"top": top}))

    def latest_quotes(self, symbols: list[str]) -> dict[str, dict]:
        d = self._d("/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": "iex"})
        return d.get("quotes") or {}

    def news_count(self, symbol: str, start: datetime, end: datetime) -> int:
        d = self._d("/v1beta1/news", {"symbols": symbol, "start": _iso(start), "end": _iso(end), "limit": 50})
        return len(d.get("news") or [])

    def news(self, symbols: list[str], start: datetime, end: datetime, max_pages: int = 10) -> list[dict]:
        """Raw Benzinga articles (headline, summary, created_at, url, symbols, author, source) for `symbols` in
        [start, end], newest first. Paginated (50 per page, at most `max_pages` requests, all under the shared
        `alpaca` budget). The payload is untrusted: kaibot.news.desk normalises and sanitises it."""
        out: list[dict] = []
        token = None
        for _ in range(max_pages):
            p = {"symbols": ",".join(symbols), "start": _iso(start), "end": _iso(end), "limit": 50, "sort": "desc",
                 "include_content": "false"}
            if token:
                p["page_token"] = token
            d = self._d("/v1beta1/news", p) or {}
            rows = d.get("news") or []
            out.extend(r for r in rows if isinstance(r, dict))
            token = d.get("next_page_token")
            if not token or not rows:
                break
        return out


def parse_movers(raw: dict) -> list[dict]:
    """Screener movers response -> [{symbol, price, change_pct}], gainers first, same as bloodbath."""
    return [{"symbol": str(m["symbol"]), "price": float(m["price"]), "change_pct": float(m["percent_change"])}
            for m in (raw.get("gainers") or []) + (raw.get("losers") or [])]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v", "vw"])
    df = pd.DataFrame(rows).rename(columns={"t": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if "vw" not in df:
        df["vw"] = df["c"]
    return df[["ts", "o", "h", "l", "c", "v", "vw"]].astype({"v": float})


def sip_safe_end(now: datetime) -> datetime:
    """Free plan: SIP data only when at least 15 minutes old."""
    return now - timedelta(minutes=16)
