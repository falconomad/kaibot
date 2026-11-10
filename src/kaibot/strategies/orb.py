"""SHADOW ONLY. 5-minute opening-range breakout (ORB) on "Stocks in Play", long side only.

Source: Zarattini, Barbon & Aziz (2024), "A Profitable Day Trading Strategy For The U.S. Equity Market",
Swiss Finance Institute Research Paper 24-98 (https://ideas.repec.org/p/chf/rpseri/rp2498.html). Published rules
(as summarised by CXO Advisory and the QuantConnect reference implementation of the paper):

1. Universe, daily: opening price > $5; average daily volume >= 1,000,000 shares over the prior 14 sessions;
   14-day ATR > $0.50.
2. Relative volume = volume of the first 5 minutes / average first-5-minute volume of the prior 14 sessions.
   Stocks in Play = relative volume >= 100%, and only the top 20 by relative volume that day.
3. Direction from the 5-minute opening candle: up (close > open) -> buy-stop at the opening-range high;
   down -> sell-stop at the opening-range low; doji -> no trade.
4. Stop-loss 10% of the 14-day ATR from the entry. 5. No target: exit at the close if the stop is not hit.
6. Sizing: 1% of capital at risk per trade, leverage <= 4x, $0.0035/share commission. Reported 2016-2023 net
   Sharpe 2.81, but an independent replication found ~zero net after spread/slippage (research 06, refs 15-16).

Kaibot simplifications (pre-registered, not tuned):
- Long only (no shorts in v1). Down-candle names still take one of the 20 Stocks-in-Play slots, as in the paper,
  but are not traded.
- Ranking is over Kaibot's universe (tens to a few hundred liquid large caps), not ~7,000 stocks, so the "in play"
  selection is much weaker than the paper's.
- Entry: instead of a resting buy-stop, a trigger when a 1-minute bar CLOSES above the opening-range high
  (minute >= 5); the fill is the open of the next minute through the shadow cost model (no-lookahead contract,
  same as replay/features.py). One trigger per symbol per day (the first close above the range).
- Exit at Kaibot's 15:55 ET flatten instead of the 16:00 close; entries only 09:35-15:45 ET.
- Sizing comes from risk/gate.py at the run's aggressiveness level, not the paper's 1% / 4x.
- Live: today's volume is IEX rescaled by the SIP/IEX ratio (engine/market.py), so relative volume is noisy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kaibot.replay.data import SymbolDays
from kaibot.strategies.shadow_types import Panel, ShadowCandidate, rolling_before

NAME = "orb_stocks_in_play"


@dataclass(frozen=True)
class OrbParams:
    or_minutes: int = 5
    lookback: int = 14
    min_price: float = 5.0
    min_adv_shares: float = 1_000_000
    min_atr_usd: float = 0.50
    min_rel_vol: float = 1.0
    top_n: int = 20
    stop_atr_frac: float = 0.10
    last_m: int = 374  # decision at 15:44 close -> fill at 15:45


DEFAULT = OrbParams()


def prior_stats(sd: SymbolDays, p: OrbParams = DEFAULT) -> dict[str, np.ndarray]:
    """Per-session statistics known before each session (arrays of len(days) + 1; see rolling_before)."""
    close = sd.c[:, -1]
    hi = np.fmax.reduce(sd.h, axis=1)
    lo = np.fmin.reduce(sd.l, axis=1)
    prev = np.r_[np.nan, close[:-1]]
    tr = np.fmax.reduce(np.c_[hi - lo, np.abs(hi - prev), np.abs(lo - prev)], axis=1)
    return {
        "atr14": rolling_before(tr, p.lookback),
        "adv14_shares": rolling_before(np.nansum(sd.v, axis=1), p.lookback),
        "or_vol14": rolling_before(np.nansum(sd.v[:, : p.or_minutes], axis=1), p.lookback),
    }


def _rank_within(groups: np.ndarray, score: np.ndarray) -> np.ndarray:
    """0-based rank of each row by descending score within its group (NaN scores rank last)."""
    s = np.where(np.isfinite(score), score, -np.inf)
    order = np.lexsort((-s, groups))
    g = groups[order]
    start = np.r_[0, np.flatnonzero(g[1:] != g[:-1]) + 1]
    first = np.repeat(start, np.diff(np.r_[start, len(g)]))
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order)) - first
    return rank


def in_play(panel: Panel, stats: dict[str, np.ndarray], p: OrbParams = DEFAULT,
            groups: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(mask of Stocks in Play, relative volume). Reads minutes 0..or_minutes-1 only."""
    k = p.or_minutes
    rel = np.nansum(panel.v[:, :k], axis=1) / stats["or_vol14"]
    ok = ((panel.o[:, 0] > p.min_price) & (stats["adv14_shares"] >= p.min_adv_shares)
          & (stats["atr14"] > p.min_atr_usd) & (rel >= p.min_rel_vol) & np.isfinite(rel))
    groups = np.zeros(len(panel), dtype=np.int64) if groups is None else groups
    rank = _rank_within(groups, np.where(ok, rel, np.nan))
    return ok & (rank < p.top_n), rel


def decide(panel: Panel, stats: dict[str, np.ndarray], m: int, p: OrbParams = DEFAULT,
           groups: np.ndarray | None = None) -> list[ShadowCandidate]:
    """ORB long candidates decided at the close of minute m, using columns 0..m only."""
    k = p.or_minutes
    if m < k or m > p.last_m or not len(panel):
        return []
    sel, rel = in_play(panel, stats, p, groups)
    or_open, or_close = panel.o[:, 0], panel.c[:, k - 1]
    or_high = np.fmax.reduce(panel.h[:, :k], axis=1)
    c_m = panel.c[:, m]
    before = np.fmax.reduce(panel.c[:, k:m], axis=1) if m > k else np.full(len(panel), -np.inf)
    first_break = (c_m > or_high) & ~(before > or_high)
    rows = np.flatnonzero(sel & (or_close > or_open) & first_break & np.isfinite(c_m))
    out = []
    for r in rows:
        ref, atr = float(c_m[r]), float(stats["atr14"][r])
        out.append(ShadowCandidate(
            NAME, str(panel.symbols[r]), int(r), m, ref, ref - p.stop_atr_frac * atr, None, "time_1555",
            ("stock_in_play", "5m_range_up", "close_above_or_high"),
            {"rel_vol_5m": float(rel[r]), "or_high": float(or_high[r]), "atr14": atr,
             "adv14_shares": float(stats["adv14_shares"][r])},
        ))
    return out
