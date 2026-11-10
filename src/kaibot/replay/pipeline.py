"""The replay pipeline shared by scripts/run_replay.py and the determinism test: bars -> candidates and the
per-symbol arrays the simulator needs. Pure: no I/O, no clock, no randomness."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kaibot.replay.data import SymbolDays
from kaibot.replay.features import candidates, five_minute


@dataclass
class Prepared:
    bars: dict[str, SymbolDays]
    cands: pd.DataFrame
    atr5: dict[str, np.ndarray]
    vwap: dict[str, np.ndarray]
    sigma: dict[str, np.ndarray]


def prepare(stocks: Mapping[str, SymbolDays], etfs: Mapping[str, SymbolDays], sector_of: Mapping[str, str],
            news: Mapping[str, np.ndarray], start: np.datetime64, end: np.datetime64, z_max: float = -1.5,
            arrays: bool = True) -> Prepared:
    """`arrays=False` skips the ATR / VWAP / sigma arrays only the random-entry baseline needs (the walk-forward
    holds 300 symbols in memory and does not run it)."""
    bars, frames, atr5, vwap, sigma = {}, [], {}, {}, {}
    # Iterates in the caller's order (the universe file's order). That order feeds the seeded random-entry
    # baseline, so it is part of the replay's input and is hashed with it.
    for s in stocks:
        sd, etf = stocks[s], etfs.get(sector_of[s])
        if sd is None or etf is None:
            continue
        bars[s] = sd
        cf = candidates(sd, etf, news.get(s, np.array([], dtype="datetime64[s]")), z_max=z_max)
        if len(cf):
            frames.append(cf)
        if not arrays:
            continue
        atr5[s], _ = five_minute(sd.o, sd.h, sd.l, sd.c)
        vwap[s] = np.cumsum(sd.vw * sd.v, axis=1) / np.maximum(np.cumsum(sd.v, axis=1), 1)
        close = sd.c[:, -1]
        r = np.r_[np.nan, close[1:] / close[:-1] - 1]
        sigma[s] = pd.Series(r).shift(1).rolling(20, min_periods=20).std().to_numpy() / np.sqrt(390)
    cands = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(cands):
        cands = cands[(cands["date"] >= start) & (cands["date"] <= end)]
    return Prepared(bars, cands, atr5, vwap, sigma)
