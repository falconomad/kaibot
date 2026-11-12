"""The committed engine-spine bar fixture (issue #115): tests/fixtures/engine_spine, written by
scripts/make_engine_fixture.py. Five stocks in two sectors, two sector ETFs and SPY; 61 thin warm-up sessions and
three full 1-minute sessions (15-17 Sep 2026). `replay()` is today's replay of the dip rule on it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.replay.data import SymbolDays, from_frame
from kaibot.replay.features import regime
from kaibot.replay.pipeline import Prepared, prepare
from kaibot.replay.sim import Book, run_portfolio
from kaibot.risk.gate import RiskPolicy

DIR = Path(__file__).resolve().parent / "fixtures" / "engine_spine"
EQUITY = 100_000.0


@dataclass(frozen=True)
class SpineFixture:
    frames: dict[str, pd.DataFrame]  # symbol -> ts (UTC), o, h, l, c, v, vw in dollars and shares
    sessions: tuple[str, ...]
    test_days: tuple[str, ...]
    sector_of: dict[str, str]
    market: str
    fomc: frozenset  # numpy datetime64[D]
    vix: pd.Series  # previous close, indexed by session date
    news: dict[str, np.ndarray]  # symbol -> sorted datetime64[s] (UTC) of its headlines

    @property
    def stocks(self) -> list[str]:
        return list(self.sector_of)

    def days(self) -> dict[str, SymbolDays]:
        return {s: from_frame(s, f) for s, f in self.frames.items()}


@cache
def _raw() -> tuple[pd.DataFrame, dict]:
    return pd.read_parquet(DIR / "bars.parquet"), json.loads((DIR / "meta.json").read_text())


def decode(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Integer cents (vw: 1/100 cent) -> the bars frames every loader in the repo uses."""
    out = {}
    for sym, g in raw.groupby("symbol", sort=True):
        f = pd.DataFrame({"ts": pd.to_datetime(g["ts"], utc=True)})
        for k in ("o", "h", "l", "c"):
            f[k] = g[k].to_numpy(dtype=float) / 100.0
        f["v"] = g["v"].to_numpy(dtype=float)
        f["vw"] = g["vw"].to_numpy(dtype=float) / 10_000.0
        out[str(sym)] = f.sort_values("ts").reset_index(drop=True)
    return out


def load() -> SpineFixture:
    """A fresh copy on every call (frames are mutable), parsed from disk once."""
    raw, meta = _raw()
    vix = pd.Series({pd.Timestamp(d): float(v) for d, v in meta["vix_prev_close"].items()}).sort_index()
    news = {s: np.sort(np.array([np.datetime64(t.replace("Z", ""), "s") for t in ts], dtype="datetime64[s]"))
            for s, ts in meta["news"].items()}
    return SpineFixture(decode(raw), tuple(meta["sessions"]), tuple(meta["test_days"]), dict(meta["sector_of"]),
                        meta["market"], frozenset(np.datetime64(d, "D") for d in meta["fomc"]), vix, news)


def prepared(fx: SpineFixture, days: tuple[str, ...] | None = None) -> tuple[Prepared, dict]:
    """Candidates and the regime per test session (or only `days`), as scripts/run_replay.py builds them."""
    sd = fx.days()
    want = days or fx.test_days
    start, end = np.datetime64(want[0], "D"), np.datetime64(want[-1], "D")
    etfs = {e: sd[e] for e in set(fx.sector_of.values())}
    prep = prepare({s: sd[s] for s in fx.stocks}, etfs, fx.sector_of, fx.news, start, end)
    reg = {d: v for d, v in regime(sd[fx.market], fx.vix).items() if start <= d <= end}
    return prep, reg


def replay(fx: SpineFixture, pol: RiskPolicy, equity0: float = EQUITY, variant: str = "A",
           days: tuple[str, ...] | None = None) -> Book:
    prep, reg = prepared(fx, days)
    return run_portfolio(prep.bars, prep.cands, reg, set(fx.fomc), pol, equity0=equity0, variant=variant)
