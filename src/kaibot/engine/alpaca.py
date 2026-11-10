"""Alpaca paper trading + market data over plain HTTPS (stdlib only, rate-limited, retried).

Paper only: the trading base URL is fixed to the paper endpoint (config.PAPER_TRADING_URL).
Free market-data plan: real-time bars and quotes come from the IEX feed; SIP is used only for history >= 15 min old.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

import pandas as pd

from kaibot.engine.config import DATA_URL, PAPER_TRADING_URL


class AlpacaError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Alpaca {status}: {body[:300]}")
        self.status = status
        self.body = body


class _Http:
    def __init__(self, key: str, secret: str, per_min: int = 180):
        self.h = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "kaibot-engine/0.2",
            "Content-Type": "application/json",
        }
        self.gap = 60.0 / per_min
        self._last = 0.0
        self._lock = threading.Lock()

    def call(self, method: str, url: str, params: dict | None = None, body: dict | None = None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            with self._lock:
                wait = self._last + self.gap - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, data=data, headers=self.h, method=method)
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code == 429 or e.code >= 500:
                    time.sleep(min(2**attempt, 20))
                    continue
                raise AlpacaError(e.code, text) from None
            except (urllib.error.URLError, TimeoutError):
                time.sleep(min(2**attempt, 20))
        raise AlpacaError(0, f"giving up on {method} {url}")


class AlpacaPaper:
    """Trading endpoints (paper account)."""

    name = "alpaca_paper"

    def __init__(self, key: str, secret: str):
        self.http = _Http(key, secret)
        self.base = PAPER_TRADING_URL

    def _t(self, method, path, params=None, body=None):
        return self.http.call(method, f"{self.base}{path}", params, body)

    def account(self) -> dict:
        return self._t("GET", "/v2/account")

    def clock(self) -> dict:
        return self._t("GET", "/v2/clock")

    def calendar(self, day: str) -> dict | None:
        rows = self._t("GET", "/v2/calendar", {"start": day, "end": day})
        return rows[0] if rows else None

    def positions(self) -> dict[str, dict]:
        return {p["symbol"]: p for p in self._t("GET", "/v2/positions")}

    def open_orders(self) -> list[dict]:
        return self._t("GET", "/v2/orders", {"status": "open", "nested": "true", "limit": 500})

    def order(self, order_id: str) -> dict:
        return self._t("GET", f"/v2/orders/{order_id}", {"nested": "true"})

    def order_by_client_id(self, coid: str) -> dict | None:
        try:
            return self._t("GET", "/v2/orders:by_client_order_id", {"client_order_id": coid, "nested": "true"})
        except AlpacaError as e:
            if e.status == 404:
                return None
            raise

    def submit_bracket(self, coid: str, symbol: str, qty: int, stop: float, target: float) -> dict:
        """Market buy with a broker-held stop and take-profit (both GTC-day legs). Adopts on duplicate id."""
        body = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "client_order_id": coid,
            "take_profit": {"limit_price": f"{target:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"},
        }
        try:
            return self._t("POST", "/v2/orders", body=body)
        except AlpacaError as e:
            if e.status == 422 and "client_order_id" in e.body:
                existing = self.order_by_client_id(coid)
                if existing:
                    return existing
            raise

    def replace_stop(self, order_id: str, stop: float) -> dict:
        return self._t("PATCH", f"/v2/orders/{order_id}", body={"stop_price": f"{stop:.2f}"})

    def replace_limit(self, order_id: str, limit: float) -> dict:
        return self._t("PATCH", f"/v2/orders/{order_id}", body={"limit_price": f"{limit:.2f}"})

    def cancel_order(self, order_id: str) -> None:
        try:
            self._t("DELETE", f"/v2/orders/{order_id}")
        except AlpacaError as e:
            if e.status not in (404, 422):
                raise

    def close_position(self, symbol: str) -> dict | None:
        """Cancel the symbol's open orders, then sell the whole position at market."""
        for o in self.open_orders():
            if o["symbol"] == symbol:
                self.cancel_order(o["id"])
        try:
            return self._t("DELETE", f"/v2/positions/{symbol}")
        except AlpacaError as e:
            if e.status == 404:
                return None
            raise


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

    def latest_quotes(self, symbols: list[str]) -> dict[str, dict]:
        d = self._d("/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": "iex"})
        return d.get("quotes") or {}

    def news_count(self, symbol: str, start: datetime, end: datetime) -> int:
        d = self._d("/v1beta1/news", {"symbols": symbol, "start": _iso(start), "end": _iso(end), "limit": 50})
        return len(d.get("news") or [])


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
