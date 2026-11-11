"""`pump_risk`: a deterministic pump-and-dump red-flag score that blocks new longs (story S37).

Tech design §1 makes pump_risk the one alt-data-style feature allowed to act before the ablation (PRD v2: "traded
only after a 3-month ablation, except the pump_risk long-block"). It reads only what the engine already has: daily
bars and the point-in-time news/filings list. It never reads the alt-data Parquet, never calls an LLM, and it can only
veto a long; it never sets price, size, stop or exit.

Red flags, from the regulators' investor alerts:
- SEC/investor.gov, "Investor Alert: Fraudulent Stock Promotions"
  (https://www.investor.gov/news-alerts/investor-alerts/investor-alert-fraudulent-stock-promotions):
  aggressive stock promotion; false press releases and announcements of events that never happen; a rise in price
  or volume that follows promotional activity; microcap and penny stocks, "the very lowest priced stocks", are
  more susceptible to manipulation; information is often scarce, so read the company's recent SEC filings.
- FINRA, "Avoiding Pump-and-Dump Scams" (https://www.finra.org/investors/insights/pump-and-dump-scams):
  low-priced (microcap) stocks; extremely volatile shares and sudden interest in a normally illiquid stock;
  short-term upward price/volume data used to lure buyers; little publicly available information.
- FINRA, "Low-Priced Stocks Can Spell Big Problems"
  (https://www.finra.org/investors/insights/nothing-micro-about-microcap-fraud): aggressive press-release
  campaigns of information that cannot be reliably confirmed; a lack of current financial information in SEC
  filings; significant price spikes; low-priced securities trade in low volumes, so small trades move them.

How they map to a score in [0, 1] (weights are judgement, not fitted; changing them is a parameter change):
  quality   price < $1 +0.30, else < $5 (penny-stock level) +0.20; market cap < $300M (microcap) +0.15;
            20-day median dollar volume < $5M (thin) +0.15
  spike     relative volume (today so far vs 20-day median, pro-rated by session time) >= 5x +0.20, >= 3x +0.10;
            max(gap up, move vs previous close) >= 30% +0.20, >= 15% +0.10
  news      in the last 24 h only press-release wires (PRN/GNW/Accesswire/Business Wire/Newsfile), no
            independent story and no 8-K +0.20; a spike with no news at all +0.10; an 8-K in 24 h -0.15
flag = score >= 0.5 AND at least one quality flag AND at least one spike flag. A liquid large cap has no quality
flag, so it cannot flag however wild its day (tests/test_pump_risk.py shows both cases).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger("kaibot.features.pump")
ET = ZoneInfo("America/New_York")
PROMO_WIRES = frozenset({"prnewswire", "globenewswire", "accesswire", "businesswire", "newsfile"})
FLAG_SCORE = 0.5
PENNY = 5.0
MICROCAP = 300e6
THIN_DOLLAR_VOLUME = 5e6
VETO_REASON = "pump risk"


def _ts(v) -> datetime | None:
    try:
        t = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(t):
        return None
    return (t.tz_localize(UTC) if t.tzinfo is None else t.tz_convert(UTC)).to_pydatetime()


def _session_fraction(as_of: datetime) -> float:
    et = as_of.astimezone(ET)
    mins = (et.hour * 60 + et.minute) - (9 * 60 + 30)
    return min(1.0, max(0.1, mins / 390.0))


def pump_risk(symbol: str, as_of: datetime, bars: pd.DataFrame | None, news: list[dict] | None, *,
              market_cap: float | None = None, has_8k: bool | None = None) -> dict:
    """{"flag": bool, "score": 0-1, "reasons": [...]} for a long in `symbol` at `as_of`.

    bars: daily bars (ts, o, h, l, c, v), point-in-time: rows with ts > as_of are ignored, and if the last row is
      as_of's session it must be that session so far (c = last price, v = volume so far), never the full day.
    news: dicts with "source" and "available_ts" (or "published_at"), optionally "kind" ("news" | "filing") and
      "form"; evidence_pack items work as is. Items after as_of or older than 24 h are ignored.
    market_cap / has_8k: optional; unknown means no contribution (an 8-K in the news list also counts)."""
    reasons: list[str] = []
    if bars is None or len(bars) == 0 or not {"ts", "o", "c", "v"} <= set(bars.columns):
        return {"flag": False, "score": 0.0, "reasons": ["insufficient bars"]}
    b = bars.copy()
    b["ts"] = pd.to_datetime(b["ts"], utc=True, format="mixed")
    b = b[b["ts"] <= pd.Timestamp(as_of)].sort_values("ts")
    if len(b) < 6:
        return {"flag": False, "score": 0.0, "reasons": ["insufficient bars"]}
    last = b.iloc[-1]
    live = last["ts"].tz_convert(ET).date() == as_of.astimezone(ET).date()
    hist = b.iloc[:-1] if live else b
    hist = hist.tail(20)
    price = float(last["c"])
    prev_close = float(hist["c"].iloc[-1])
    adv = float(hist["v"].median())
    dollar_vol = float((hist["v"] * hist["c"]).median())
    score, quality, spike = 0.0, False, False

    if price < 1.0:
        score, quality = score + 0.30, True
        reasons.append(f"sub-$1 price ({price:.2f})")
    elif price < PENNY:
        score, quality = score + 0.20, True
        reasons.append(f"penny-stock price ({price:.2f} < {PENNY:.0f})")
    if market_cap is not None and market_cap < MICROCAP:
        score, quality = score + 0.15, True
        reasons.append(f"microcap (${market_cap / 1e6:.0f}M)")
    if dollar_vol < THIN_DOLLAR_VOLUME:
        score, quality = score + 0.15, True
        reasons.append(f"thin dollar volume (${dollar_vol / 1e6:.1f}M/day)")

    if live and adv > 0:
        rvol = float(last["v"]) / (adv * _session_fraction(as_of))
        if rvol >= 5:
            score, spike = score + 0.20, True
            reasons.append(f"abnormal volume ({rvol:.1f}x)")
        elif rvol >= 3:
            score, spike = score + 0.10, True
            reasons.append(f"high volume ({rvol:.1f}x)")
        if prev_close > 0:
            move = max(float(last["o"]) / prev_close - 1, price / prev_close - 1)
            if move >= 0.30:
                score, spike = score + 0.20, True
                reasons.append(f"price spike (+{move:.0%})")
            elif move >= 0.15:
                score, spike = score + 0.10, True
                reasons.append(f"big gap up (+{move:.0%})")

    recent = []
    for n in news or []:
        t = _ts(n.get("available_ts") or n.get("published_at"))
        if t is not None and as_of - timedelta(hours=24) <= t <= as_of:
            recent.append(n)
    filing_8k = bool(has_8k) or any(n.get("kind") == "filing" and str(n.get("form", "8-K")).upper().startswith("8-K")
                                    for n in recent)
    stories = [n for n in recent if n.get("kind", "news") == "news"]
    promo = [n for n in stories if str(n.get("source", "")).lower() in PROMO_WIRES]
    if promo and len(promo) == len(stories) and not filing_8k:
        score += 0.20
        reasons.append(f"only promotional wire releases ({len(promo)}), no independent news or 8-K")
    elif not recent and spike:
        score += 0.10
        reasons.append("spike with no news or filing")
    if filing_8k:
        score -= 0.15
        reasons.append("8-K filed in the last 24 h")

    score = round(min(1.0, max(0.0, score)), 3)
    flag = bool(score >= FLAG_SCORE and quality and spike)
    return {"flag": flag, "score": score, "reasons": reasons}


def rules_veto_extra(symbol: str, as_of: datetime, *, bars: pd.DataFrame | None = None,
                     news: list[dict] | None = None, side: str = "buy", market_cap: float | None = None,
                     has_8k: bool | None = None) -> str | None:
    """Extra rules-only veto for a NEW position: "pump risk" when pump_risk flags a long, else None.

    Pure (no I/O). Never blocks a sell or an exit. If the check itself fails it fails closed for the long:
    "pump risk check failed"."""
    if str(side).lower() not in ("buy", "long"):
        return None
    try:
        r = pump_risk(symbol, as_of, bars, news, market_cap=market_cap, has_8k=has_8k)
    except Exception:
        log.exception("pump_risk failed for %s", symbol)
        return f"{VETO_REASON} check failed"
    return VETO_REASON if r["flag"] else None
