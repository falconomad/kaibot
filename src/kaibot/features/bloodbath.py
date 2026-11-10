"""Indicators ported from bloodbath (story S02), kept bit-for-bit identical to the original.

Source: github.com/falconomad/bloodbath @ e8da3b2
- technical_score      <- ai_engines/technical_analyst.py::evaluate_technicals
- market_regime        <- ai_engines/candidate_ranker.py::detect_market_regime
- daily_features       <- ai_engines/candidate_ranker.py::build_features
tests/unit/test_bloodbath_parity.py checks them against outputs recorded from bloodbath's own code
(tests/fixtures/bloodbath_parity.json, made by scripts/make_bloodbath_fixtures.py). Do not "improve" the
arithmetic here: `statistics.mean` and the order of operations are what make the outputs match exactly.

Per the tech spec these are inputs for research and the room's context, never for sizing (that comes from the
stop distance in risk/gate.py). bloodbath's LLM-% sizing, Gemini picks and auto-submit are not ported.
Bars are bloodbath's shape: a list of {"c": close, "v": volume, ...} daily bars, oldest first.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import mean
from typing import Any

Bar = dict[str, Any]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return ((a - b) / b) * 100.0


def technical_score(symbol: str, history: Sequence[Bar], spy_context: Sequence[Bar] | None) -> dict:
    """0-100 technical grade (trend, momentum, relative strength vs SPY, volume) and a one-line rationale."""
    if not history or len(history) < 2:
        return {"score": 0, "rationale": "Insufficient price history for technical analysis."}
    closes = [float(x.get("c", 0.0)) for x in history if float(x.get("c", 0.0)) > 0]
    vols = [float(x.get("v", 0.0)) for x in history if float(x.get("v", 0.0)) >= 0]
    if len(closes) < 2:
        return {"score": 0, "rationale": "Insufficient close-price points."}

    asset_ret = ((closes[-1] - closes[0]) / closes[0]) * 100.0
    spy_ret = 0.0
    spy_closes = [float(x.get("c", 0.0)) for x in (spy_context or []) if float(x.get("c", 0.0)) > 0]
    if len(spy_closes) >= 2:
        spy_ret = ((spy_closes[-1] - spy_closes[0]) / spy_closes[0]) * 100.0
    rel_strength = asset_ret - spy_ret
    avg_vol = mean(vols) if vols else 0.0
    vol_spike = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0

    sma_bonus = 0.0  # 3-day SMA trend confirmation
    if len(closes) >= 3:
        sma_bonus = 15.0 if closes[-1] > mean(closes[-3:]) else -15.0

    vol_acceleration_bonus = 0.0
    if len(vols) >= 3:
        if vols[-1] > vols[-2] and vols[-2] > vols[-3]:
            vol_acceleration_bonus = 10.0
        elif vols[-1] > vols[-2]:
            vol_acceleration_bonus = 5.0
        else:
            vol_acceleration_bonus = -5.0

    price_acceleration = 0.0
    if len(closes) >= 3:
        day1_ret = ((closes[-1] - closes[-2]) / closes[-2]) * 100.0
        day2_ret = ((closes[-2] - closes[-3]) / closes[-3]) * 100.0
        price_acceleration = day1_ret - day2_ret

    score = 50.0
    score += sma_bonus  # trend: +-15
    momentum_factor = (asset_ret * 4.0) + (price_acceleration * 3.0)
    score += max(min(momentum_factor, 25.0), -25.0)  # momentum: +-25
    score += max(min(rel_strength * 4.0, 20.0), -20.0)  # relative strength: +-20
    vol_factor = ((vol_spike - 1.0) * 10.0) + vol_acceleration_bonus
    score += max(min(vol_factor, 20.0), -20.0)  # volume: +-20
    score = max(0, min(100, int(round(score))))

    rationale = (
        f"5-day return {asset_ret:.2f}%, relative strength vs SPY {rel_strength:.2f}%, "
        f"SMA3 position {'above' if sma_bonus > 0 else 'below'}, volume spike {vol_spike:.2f}x."
    )
    return {"score": score, "rationale": rationale}


def market_regime(spy_history: Sequence[Bar]) -> str:
    """risk_on / risk_off / neutral from SPY's last 1- and 3-day returns."""
    if not spy_history or len(spy_history) < 3:
        return "neutral"
    closes = [float(x.get("c", 0.0)) for x in spy_history if float(x.get("c", 0.0)) > 0]
    if len(closes) < 3:
        return "neutral"
    ret_3d = _pct(closes[-1], closes[-3])
    ret_1d = _pct(closes[-1], closes[-2])
    if ret_3d > 1.5 and ret_1d >= -0.5:
        return "risk_on"
    if ret_3d < -1.5 or ret_1d < -1.2:
        return "risk_off"
    return "neutral"


def daily_features(symbol: str, bars: Sequence[Bar], spy_history: Sequence[Bar],
                   news_items: Sequence[dict] | None) -> dict | None:
    """Returns, trend gap, volume multiple/stability, relative strength and news context from thin daily history."""
    if not bars:
        return None
    closes = [float(x.get("c", 0.0)) for x in bars if float(x.get("c", 0.0)) > 0]
    vols = [float(x.get("v", 0.0)) for x in bars if float(x.get("v", 0.0)) >= 0]
    if len(closes) < 1:
        return None

    ret_1d = _pct(closes[-1], closes[-2]) if len(closes) >= 2 else 0.0
    if len(closes) >= 4:
        ret_3d = _pct(closes[-1], closes[-4])
    else:
        ret_3d = _pct(closes[-1], closes[0]) if len(closes) >= 2 else 0.0
    ret_5d = _pct(closes[-1], closes[0])

    ema3 = mean(closes[-3:]) if len(closes) >= 3 else mean(closes)  # bloodbath calls these EMAs; they are SMAs
    ema5 = mean(closes[-5:]) if len(closes) >= 5 else mean(closes)
    trend_gap = _pct(ema3, ema5)

    vol_window = vols[-5:] if len(vols) >= 5 else vols
    avg_vol = mean(vol_window) if vol_window else 0.0
    vol_mult = (vols[-1] / avg_vol) if avg_vol > 0 else 1.0
    vol_stability = 1.0
    if len(vols) >= 2 and avg_vol > 0:
        dev = mean(abs(v - avg_vol) / avg_vol for v in vol_window)
        vol_stability = _clamp(1.0 - dev, 0.0, 1.0)

    spy_closes = [float(x.get("c", 0.0)) for x in spy_history if float(x.get("c", 0.0)) > 0]
    spy_ret_5d = _pct(spy_closes[-1], spy_closes[0]) if len(spy_closes) >= 2 else 0.0
    rel_strength_5d = ret_5d - spy_ret_5d

    news_count = len(news_items or [])
    news_density = _clamp(news_count / 3.0, 0.0, 1.0)
    sources = {str(x.get("source", "")).lower() for x in (news_items or []) if str(x.get("source", "")).strip()}
    source_diversity = _clamp(len(sources) / 3.0, 0.0, 1.0)

    return {
        "symbol": symbol, "history_points": len(closes), "ret_1d": ret_1d, "ret_3d": ret_3d, "ret_5d": ret_5d,
        "trend_gap": trend_gap, "vol_mult": vol_mult, "vol_stability": vol_stability,
        "rel_strength_5d": rel_strength_5d, "news_count": news_count, "news_density": news_density,
        "source_diversity": source_diversity, "last_close": closes[-1],
    }
