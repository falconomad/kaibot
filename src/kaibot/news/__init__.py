"""News: ingest, echo dedup into clusters, one Haiku triage per cluster, point-in-time evidence (story S33).

The engine builds one `NewsDesk(store, source=AlpacaData, gateway=Gateway)`, calls `desk.load_window()` at start
and `desk.poll(symbols)` each news cycle, and may register it with `configure(desk)` so the agent room can call the
module-level `evidence_pack` / `clusters_for` without holding a reference. See docs/ops/news.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kaibot.news.desk import NewsDesk, normalise

__all__ = ["NewsDesk", "clusters_for", "configure", "evidence_pack", "normalise"]
_DESK: NewsDesk | None = None


def configure(desk: NewsDesk | None) -> None:
    global _DESK
    _DESK = desk


def clusters_for(symbol: str, as_of: datetime, hours: float = 24.0) -> list[dict]:
    return _DESK.clusters_for(symbol, as_of, hours) if _DESK else []


def evidence_pack(symbol: str, sector_etf: str | None, as_of: datetime, hours: float = 24) -> dict:
    """Same as NewsDesk.evidence_pack on the configured desk. With no desk: an empty pack with complete=False."""
    if _DESK is None:
        return {"symbol": symbol, "sector_etf": sector_etf, "as_of": as_of.astimezone(UTC).isoformat(),
                "hours": hours, "items": [], "n_clusters": 0, "truncated": False, "complete": False}
    return _DESK.evidence_pack(symbol, sector_etf, as_of, hours)
