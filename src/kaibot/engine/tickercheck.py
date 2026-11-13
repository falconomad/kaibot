"""S68 (#96): may Kaibot trade this ticker? One pure verdict, used in two places.

1. The check worker (`process`, run by scripts/run_ticker_checks.py) answers the dashboard's `kb_check_ticker`
   requests: it fills the pending rows of kaibot.ticker_check from the broker's asset record, completed daily SIP
   bars and Finnhub's industry (for the sector ETF). The result is cached for the day.
2. The engine (`Engine._vet_custom`) runs the SAME verdict again at every session open on the history it actually
   loaded, so a ticker that stopped passing is not scanned that day whatever the cache says. The owner cannot
   override it.

Point-in-time: only completed sessions before the decision are read (SIP data at least 15 minutes old).
The spread and quote-freshness filters need a live quote, so they stay where they are: at every entry."""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from kaibot.engine.userconfig import LEVEL_MIN_ADV, LEVEL_MIN_PRICE, REFERENCE_ETFS, TICKER_RE
from kaibot.replay.data import ET

log = logging.getLogger("kaibot.tickercheck")

MIN_SESSIONS = 60  # the beta and volatility windows need about three months of sessions
ADV_SESSIONS = 20
MAJOR_EXCHANGES = frozenset({"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"})
FALLBACK_ETF = "SPY"
MAX_ATTEMPTS = 3

# Finnhub `finnhubIndustry` (lower case) -> sector ETF. Anything else falls back to SPY (the market).
INDUSTRY_ETF = {
    **dict.fromkeys(("technology", "semiconductors", "software", "electronic equipment", "it services",
                     "communications"), "XLK"),
    **dict.fromkeys(("media", "telecommunication", "entertainment", "interactive media & services"), "XLC"),
    **dict.fromkeys(("retail", "automobiles", "auto components", "hotels, restaurants & leisure",
                     "textiles, apparel & luxury goods", "leisure products", "diversified consumer services",
                     "distributors"), "XLY"),
    **dict.fromkeys(("beverages", "food products", "tobacco", "food & staples retailing", "consumer products"), "XLP"),
    **dict.fromkeys(("health care", "pharmaceuticals", "biotechnology", "life sciences tools & services"), "XLV"),
    **dict.fromkeys(("banking", "financial services", "insurance"), "XLF"),
    **dict.fromkeys(("aerospace & defense", "machinery", "industrial conglomerates", "logistics & transportation",
                     "airlines", "road & rail", "marine", "building", "construction", "electrical equipment",
                     "commercial services & supplies", "professional services",
                     "trading companies & distributors", "transportation infrastructure"), "XLI"),
    "energy": "XLE",
    "utilities": "XLU",
    "real estate": "XLRE",
    **dict.fromkeys(("chemicals", "metals & mining", "packaging", "paper & forest"), "XLB"),
}


def sector_etf(industry: str | None) -> str:
    """The sector ETF used for the sector-adjusted features; SPY when the industry is unknown."""
    etf = INDUSTRY_ETF.get((industry or "").strip().lower(), FALLBACK_ETF)
    return etf if etf in REFERENCE_ETFS else FALLBACK_ETF


def stats(bars: pd.DataFrame | None, before: date) -> dict:
    """Sessions, 20-session average $ volume and last close from bars (1-minute or daily) of sessions BEFORE
    `before`. Nothing from the decision day itself is read."""
    if bars is None or not len(bars):
        return {"sessions": 0, "adv20_usd": 0.0, "last_close": None}
    ts = pd.to_datetime(bars["ts"], utc=True)
    day = ts.dt.tz_convert(ET).dt.date
    keep = (day < before).to_numpy()
    if not keep.any():
        return {"sessions": 0, "adv20_usd": 0.0, "last_close": None}
    b, day = bars[keep], day[keep]
    dollars = (b["c"].astype(float) * b["v"].astype(float)).groupby(day.to_numpy()).sum().sort_index()
    order = ts[keep].to_numpy().argmax()
    tail = dollars.tail(ADV_SESSIONS)
    return {"sessions": int(len(dollars)),
            "adv20_usd": float(tail.mean()) if len(tail) == ADV_SESSIONS else 0.0,
            "last_close": float(b["c"].iloc[order])}


def verdict(symbol: str, asset: dict | None, st: dict, *, min_adv: float = LEVEL_MIN_ADV,
            min_price: float = LEVEL_MIN_PRICE) -> tuple[bool, str | None]:
    """(accepted, plain reason when refused). The floors may only be raised above the level's values."""
    min_adv, min_price = max(min_adv, LEVEL_MIN_ADV), max(min_price, LEVEL_MIN_PRICE)
    if not isinstance(symbol, str) or not TICKER_RE.fullmatch(symbol):
        return False, "That does not look like a US stock ticker (1 to 5 letters, for example PLTR)."
    if symbol in REFERENCE_ETFS:
        return False, f"{symbol} is a reference fund Kaibot compares stocks against, so it cannot be traded."
    if not asset or asset.get("symbol") != symbol:
        return False, f"We could not find {symbol} at the broker. Check the spelling."
    if asset.get("class") != "us_equity" or asset.get("exchange") not in MAJOR_EXCHANGES:
        return False, f"{symbol} is not a stock listed on a major US exchange."
    if asset.get("status") != "active" or asset.get("tradable") is not True:
        return False, f"{symbol} cannot be traded at the broker right now."
    n, adv, px = int(st.get("sessions") or 0), float(st.get("adv20_usd") or 0.0), st.get("last_close")
    if n < MIN_SESSIONS:
        return False, f"{symbol} has only {n} trading days of price history; Kaibot needs at least {MIN_SESSIONS}."
    known = px is not None and math.isfinite(float(px))
    if not known or float(px) < min_price:
        shown = f"${float(px):,.2f}" if known else "an unknown price"
        return False, f"{symbol} closed at {shown}; Kaibot only trades stocks above ${min_price:g}."
    if not adv > min_adv:
        return False, (f"{symbol} trades about ${adv / 1e6:,.0f}M a day; Kaibot needs more than "
                       f"${min_adv / 1e6:,.0f}M so orders fill at a fair price.")
    return True, None


def check_symbol(symbol: str, *, asset_fn, bars_fn, industry_fn, now: datetime) -> dict:
    """One full check. `asset_fn(symbol) -> dict | None`, `bars_fn(symbol, start, end) -> daily SIP frame`,
    `industry_fn(symbol) -> (name, industry) | None`. Raises if the broker or the bars cannot be read (the caller
    retries); an unknown industry is not an error (the sector ETF falls back to SPY)."""
    today = pd.Timestamp(now).tz_convert(ET).date()
    asset = asset_fn(symbol)
    end = now - timedelta(minutes=16)  # SIP only when at least 15 minutes old
    bars = bars_fn(symbol, end - timedelta(days=150), end) if asset else None
    st = stats(bars, today)
    ok, reason = verdict(symbol, asset, st)
    name, industry, note = (asset or {}).get("name"), None, None
    if ok:
        try:
            got = industry_fn(symbol)
            if got:
                name, industry = got[0] or name, got[1]
        except Exception as e:  # noqa: BLE001 - the industry is a nicety: SPY stands in
            note = f"industry lookup failed ({type(e).__name__}): using the market fund"
    return {"status": "accepted" if ok else "refused", "reason": reason,
            "etf": sector_etf(industry) if ok else None, "name": name, "industry": industry,
            "detail": {**st, "exchange": (asset or {}).get("exchange"), "as_of": str(today),
                       "min_adv_usd": LEVEL_MIN_ADV, "min_price_usd": LEVEL_MIN_PRICE, "min_sessions": MIN_SESSIONS,
                       **({"note": note} if note else {})}}


def process(store, *, asset_fn, bars_fn, industry_fn, now=lambda: datetime.now(UTC), limit: int = 5) -> list[dict]:
    """Answer up to `limit` pending rows of kaibot.ticker_check (oldest first). Never raises for one bad ticker:
    after MAX_ATTEMPTS failed reads the row becomes `error`, which the dashboard may ask to retry."""
    out = []
    rows = store.select("ticker_check", {"status": "eq.pending", "order": "updated_at.asc", "limit": str(limit)})
    for r in rows[:limit]:
        t = now()
        key = {"symbol": r["symbol"], "checked_on": r["checked_on"]}
        try:
            res = check_symbol(r["symbol"], asset_fn=asset_fn, bars_fn=bars_fn, industry_fn=industry_fn, now=t)
        except Exception as e:  # noqa: BLE001 - one unreadable ticker must not stop the queue
            n = int(r.get("attempts") or 0) + 1
            log.warning("ticker check %s failed (%s), attempt %d", r["symbol"], type(e).__name__, n)
            res = {"attempts": n, "detail": {"error": type(e).__name__}}
            if n >= MAX_ATTEMPTS:
                res.update(status="error", reason=f"We could not check {r['symbol']} right now. Try again later.")
        store.update("ticker_check", key, {**res, "updated_at": t.isoformat()})
        out.append({"symbol": r["symbol"], "status": res.get("status", "pending")})
    return out
