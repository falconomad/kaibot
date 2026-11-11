"""M3 wiring between the engine, the news desk (S33) and the pump-risk veto (S37).

- `make_pack_fn(desk)`: the room's evidence pack = the deterministic default pack + deduplicated news clusters for
  the symbol and its sector ETF, polled (and triaged once per cluster) just before deliberation.
- `make_rules_extra(desk)`: the extra rules-only veto the engine checks before the room: `pump_risk` on point-in-time
  daily bars plus the news the desk knows. Pure rules: no LLM call happens here.

Both are thread-safe (the room runs in worker threads) and never raise: on failure the pack falls back to the
default and the veto fails closed for the long ("pump risk check failed")."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from kaibot.engine.roomflow import default_pack
from kaibot.features.pump import rules_veto_extra

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
_COLS = ["ts", "o", "h", "l", "c", "v"]


def daily_bars(market, symbol: str, as_of: datetime) -> pd.DataFrame | None:
    """Daily OHLCV from the cached SIP history plus today's completed IEX minutes (volume scaled to SIP), using only
    minutes that ended at or before `as_of`. Today's row is the session so far."""
    frames = []
    hist = market.history.get(symbol)
    if hist is not None and len(hist):
        frames.append(hist[_COLS])
    today = market.today_bars.get(symbol)
    if today is not None and len(today):
        t = today[_COLS].copy()
        t["v"] = t["v"].astype(float) * market.vol_ratio.get(symbol, 1.0)
        frames.append(t)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df[df["ts"] < pd.Timestamp(as_of)]
    if not len(df):
        return None
    df = df.sort_values("ts")
    day = df["ts"].dt.tz_convert(ET).dt.date
    g = df.groupby(day, sort=True)
    out = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(),
                        "v": g["v"].sum().astype(float), "ts": g["ts"].last()})
    return out.reset_index(drop=True)[_COLS]


class LockedDesk:
    """Serialises access to a NewsDesk (its dedup window is not thread-safe). Share ONE between both hooks."""

    def __init__(self, desk):
        self.desk, self.lock = desk, threading.Lock()

    def poll(self, symbols: list[str]) -> None:
        with self.lock:
            self.desk.poll(symbols)

    def pack(self, symbol: str, etf: str | None, as_of: datetime) -> dict:
        with self.lock:
            return self.desk.evidence_pack(symbol, etf, as_of)


def _locked(desk) -> LockedDesk | None:
    return desk if desk is None or isinstance(desk, LockedDesk) else LockedDesk(desk)


def make_pack_fn(desk):
    d = _locked(desk)

    def pack_fn(engine, symbol: str, etf: str | None, as_of, feats: dict) -> dict:
        base = default_pack(engine, symbol, etf, as_of, feats)
        if d is None:
            return base
        try:
            d.poll([symbol] + ([etf] if etf else []))
            news = d.pack(symbol, etf, as_of)
        except Exception:
            log.exception("news evidence pack failed for %s: default pack only", symbol)
            return base
        base["items"] = base["items"] + list(news.get("items") or [])
        base["context"].update({"news_complete": bool(news.get("complete")),
                                "news_truncated": bool(news.get("truncated")),
                                "news_clusters": int(news.get("n_clusters") or 0)})
        return base

    return pack_fn


def make_rules_extra(desk):
    d = _locked(desk)

    def rules_extra(engine, symbol: str, m: int) -> str | None:
        as_of = engine.now()
        bars = daily_bars(engine.market, symbol, as_of)
        news = None
        if d is not None:
            try:
                news = d.pack(symbol, None, as_of).get("items")
            except Exception:
                log.exception("news lookup for the pump check failed for %s", symbol)
                return "pump risk check failed"
        return rules_veto_extra(symbol, as_of, bars=bars, news=news)

    return rules_extra
