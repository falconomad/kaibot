"""No-news dip reversal ("loser hunter"), MVP strategy at every aggressiveness level.

Evidence (research 06): no-news drops tend to reverse, news drops continue (Chan 2003; Savor 2012);
high-turnover losers keep falling (Medhat & Schmeling 2022). So we only buy quality, liquid names whose
drop is mostly market/sector beta, on normal turnover, with no company-specific news.
Entry/stop/target here are deterministic; the agent room can only veto/downgrade (agents/contracts.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class DipFeatures:
    symbol: str
    quality_score: float  # 0-100 from EDGAR fundamentals (nightly)
    adv_usd: float  # 20-day average dollar volume
    spread_bp: float
    ret_since_open_z: float  # return since open / intraday sigma (negative = down)
    sector_residual_z: float  # residual after sector-beta adjustment
    turnover_ratio: float  # SIP volume vs 20-day same-time average
    company_news_24h: bool  # any story / 8-K / rating change / earnings in 24h
    higher_low_5m: bool  # 5-min higher low printed after the low
    last: float
    atr_5m: float
    vwap: float
    now_et: time


@dataclass(frozen=True)
class Regime:
    crisis: bool
    spy_below_vwap: bool
    spy_first30_ret: float  # fraction
    vix: float
    event_blackout: bool  # FOMC/CPI +-30 min


@dataclass(frozen=True)
class Signal:
    symbol: str
    entry: float
    stop: float
    target: float
    size_mult: float  # 1.0 or 0.5 (high VIX)
    reasons: tuple[str, ...]


ENTRY_START, ENTRY_END = time(9, 35), time(15, 45)


def rejects(f: DipFeatures, r: Regime, min_quality: float = 70, min_adv: float = 50e6,
            max_spread_bp: float = 5) -> list[str]:
    out = []
    if not (ENTRY_START <= f.now_et <= ENTRY_END):
        out.append("outside_entry_window")
    if r.crisis or (r.spy_below_vwap and r.spy_first30_ret < -0.01):
        out.append("regime_off")
    if r.event_blackout:
        out.append("event_blackout")
    if f.quality_score < min_quality:
        out.append("quality")
    if f.adv_usd < min_adv or f.spread_bp > max_spread_bp:
        out.append("liquidity")
    if f.ret_since_open_z > -1.5:
        out.append("not_a_dip")
    if abs(f.sector_residual_z) > 1.5:
        out.append("idiosyncratic_drop")
    if f.turnover_ratio > 1.5:
        out.append("high_turnover")
    if f.company_news_24h:
        out.append("company_news")
    if not f.higher_low_5m:
        out.append("no_confirmation")
    return out


def signal(f: DipFeatures, r: Regime, **kw) -> Signal | None:
    if rejects(f, r, **kw):
        return None
    stop = f.last - 1.5 * f.atr_5m
    risk = f.last - stop
    target = min(f.vwap, f.last + 1.5 * risk) if f.vwap > f.last else f.last + 1.5 * risk
    return Signal(f.symbol, f.last, round(stop, 2), round(target, 2), 0.5 if r.vix > 25 else 1.0,
                  ("beta_driven_dip", "normal_turnover", "no_company_news", "higher_low_5m"))
