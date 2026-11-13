#!/usr/bin/env python3
"""S68 (#96): how long does one scan take with N extra (custom) symbols? Synthetic bars, no network.

    python scripts/bench_scan.py [--extra 40] [--repeat 5]

Builds the live scanner (engine/market.LiveMarket) over the 59 seed symbols + their ETFs + SPY with 70 sessions of
1-minute history, then times candidates_at() at a mid-session 5-minute boundary, with and without the extra
symbols. The engine runs this once per completed minute; entries are only possible on 5-minute boundaries."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine.market import LiveMarket  # noqa: E402
from kaibot.replay.data import ET  # noqa: E402

DAY, SESSIONS, MINUTE = "2026-08-04", 70, 204


def frame(seed: int, days) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(days) * 390
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    o = np.r_[100.0, c[:-1]]
    ts = np.concatenate([(pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert(UTC)
                          + pd.to_timedelta(np.arange(390), unit="m")).to_numpy() for d in days])
    return pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "o": o, "h": np.maximum(o, c) * 1.0003,
                         "l": np.minimum(o, c) * 0.9997, "c": c, "v": 2e5, "vw": (o + c) / 2})


def build(extra: int) -> LiveMarket:
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    etf_of = {s: e for e, syms in cfg["sectors"].items() for s in syms}
    mk = LiveMarket(None, sorted(etf_of), etf_of, cfg["market"], Path("/nonexistent"), lambda: pd.Series(dtype=float))
    etfs = sorted(cfg["sectors"])
    custom = {f"Q{chr(65 + i // 26)}{chr(65 + i % 26)}": etfs[i % len(etfs)] for i in range(extra)}
    mk.set_custom(custom)
    mk.accept_custom(custom)
    days = pd.bdate_range(end=DAY, periods=SESSIONS + 1)
    open_utc = pd.Timestamp(f"{DAY} 09:30", tz=ET).tz_convert(UTC)
    for k, s in enumerate(mk.all):
        f = frame(k, days)
        mk.history[s] = f[f["ts"] < open_utc].reset_index(drop=True)
        mk.today_bars[s] = f[(f["ts"] >= open_utc) & (f["ts"] < open_utc + pd.Timedelta(minutes=MINUTE + 1))]
    mk.day, mk.open_utc = pd.Timestamp(DAY).date(), open_utc.to_pydatetime()
    return mk


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extra", type=int, default=40)
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args()
    for extra in (0, a.extra):
        mk = build(extra)
        wall = cpu = float("inf")
        for _ in range(a.repeat):
            t, c = time.perf_counter(), time.process_time()
            mk.candidates_at(MINUTE)
            wall, cpu = min(wall, time.perf_counter() - t), min(cpu, time.process_time() - c)
        print(f"{len(mk.symbols):3d} symbols ({extra} custom): scan {cpu:.2f} s CPU, {wall:.2f} s wall "
              f"({cpu / mk.last_checked * 1000:.0f} ms CPU per symbol, {mk.last_checked} checked)")


if __name__ == "__main__":
    main()
