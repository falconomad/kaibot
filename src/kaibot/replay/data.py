"""Load 1-minute bars into per-session regular-trading-hours arrays (390 minutes, 09:30-15:59 ET)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ET = "America/New_York"
N_MIN = 390


@dataclass
class SymbolDays:
    symbol: str
    dates: np.ndarray  # datetime64[D], sessions with data
    o: np.ndarray  # (days, 390) float64, NaN where no bar
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    vw: np.ndarray


def load_symbol(data_dir: Path, symbol: str, start: np.datetime64 | None = None,
                end: np.datetime64 | None = None) -> SymbolDays | None:
    """Load monthly files for `symbol`, optionally only sessions in [start, end] (keeps 10 years x 300 symbols
    out of memory when a replay needs one window)."""
    files = sorted((data_dir / "bars_1m" / f"symbol={symbol}").glob("*.parquet"))
    if start is not None:
        files = [f for f in files if f.stem >= str(np.datetime64(start, "M"))]
    if end is not None:
        files = [f for f in files if f.stem <= str(np.datetime64(end, "M"))]
    if not files:
        return None
    sd = from_frame(symbol, pd.concat([pd.read_parquet(f) for f in files], ignore_index=True))
    if sd is None or (start is None and end is None):
        return sd
    keep = np.ones(len(sd.dates), dtype=bool)
    if start is not None:
        keep &= sd.dates >= np.datetime64(start, "D")
    if end is not None:
        keep &= sd.dates <= np.datetime64(end, "D")
    if not keep.any():
        return None
    return SymbolDays(symbol, sd.dates[keep], *(getattr(sd, k)[keep] for k in ("o", "h", "l", "c", "v", "vw")))


def from_frame(symbol: str, df: pd.DataFrame, upto: dict | None = None) -> SymbolDays | None:
    """Bars frame (ts UTC, o, h, l, c, v, vw) -> SymbolDays. `upto` {date: last_minute} stops the forward-fill
    at the last completed minute of a session still in progress, so no future minute is ever filled."""
    if df is None or not len(df):
        return None
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["et"] = df["ts"].dt.tz_convert(ET)
    mins = df["et"].dt.hour * 60 + df["et"].dt.minute - (9 * 60 + 30)
    df = df[(mins >= 0) & (mins < N_MIN)].copy()
    df["m"] = mins[(mins >= 0) & (mins < N_MIN)]
    df["d"] = df["et"].dt.tz_localize(None).dt.normalize()
    dates = np.array(sorted(df["d"].unique()), dtype="datetime64[D]")
    idx = {d: i for i, d in enumerate(dates)}
    shape = (len(dates), N_MIN)
    arrs = {k: np.full(shape, np.nan) for k in ("o", "h", "l", "c", "v", "vw")}
    di = df["d"].values.astype("datetime64[D]")
    rows = np.searchsorted(dates, di)  # `dates` is sorted and unique and holds every value of `di`
    cols = df["m"].to_numpy(dtype=np.int64)
    for k in arrs:
        arrs[k][rows, cols] = df[k].to_numpy(dtype=float)
    # a missing minute means no trades: carry the last close forward, zero volume
    c = pd.DataFrame(arrs["c"]).ffill(axis=1).to_numpy(copy=True)
    if upto:
        for d, last_m in upto.items():
            r = idx.get(np.datetime64(d, "D"))
            if r is not None and last_m + 1 < N_MIN:
                c[r, last_m + 1:] = np.nan
    for k in ("o", "h", "l"):
        arrs[k] = np.where(np.isnan(arrs[k]), c, arrs[k])
    arrs["c"] = c
    arrs["vw"] = np.where(np.isnan(arrs["vw"]), c, arrs["vw"])
    arrs["v"] = np.nan_to_num(arrs["v"])
    return SymbolDays(symbol, dates, **arrs)


def load_news_times(data_dir: Path, symbol: str) -> np.ndarray:
    """Publication times of every stored article (all.parquet from the first download, YYYY.parquet after)."""
    files = sorted((data_dir / "news" / f"symbol={symbol}").glob("*.parquet"))
    if not files:
        return np.array([], dtype="datetime64[s]")
    t = pd.concat([pd.read_parquet(f, columns=["created_at"]) for f in files])["created_at"]
    t = t.dt.tz_convert("UTC").dt.tz_localize(None)
    return np.unique(t.to_numpy().astype("datetime64[s]"))  # sorted; the two layouts may overlap
