"""The 15:50 price of each session, from the minute history (M11 ticket 2.3, issue #454;
docs/prereg/trend_momentum.md Amendment 31). Research only: read by scripts/near_close_1550_rerun.py, never by the
live engine.

A near-close DAY market order is sent at 15:50 ET (Amendment 10, #333). On a real exchange it trades in the
continuous session at that minute's price, not at the closing print the daily runs fill it at (decision 15 section
12, question 1). `session_1550` reads `data/bars_1m/symbol=<key>/<YYYY-MM>.parquet` and gives, per session:

- `c1555`: the **15:50 price**, the open of the first minute bar at or after 15:50 ET and before 16:00 (SimBroker's
  rule for a market order: "fill at the open of the first bar at or after the minute it was submitted in"). NaN
  when no minute from 15:50 to 15:59 traded. The column is named `c1555` only because that is the field
  `DailyStore.panel(intraday=...)` puts back on the store's raw basis. The value is the 15:50 open;
- `v_last`: that minute's volume (the impact term's volume);
- `c`: the session's last regular-hours close, which the store uses to check the split basis (a session more than
  5% away from the stored close is dropped and counted).

Months the full history lacks are read from `data/bars_1550/symbol=<key>/<YYYY-MM>.parquet` (issue #794,
`ingest/minute_pit.py`: the 15:45-15:59 ET minutes of every point-in-time member, former members included, keyed as
the daily store keys them). Their `c` is the window's last close (15:59 when it traded), for the same check.

`arrays` returns the 15:50 price and volume as (sessions x symbols) arrays on the raw basis of a `DailyData`. A key
without minute files (Alpaca has none for it; `scripts/fetch_minute_pit.py --report` flags it) is NaN
throughout, and the run fills it at the closing print, counted.

Hold-out: `end` is required, and no month file after `end`'s month is opened, so nothing from the hold-out is read.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ET = "America/New_York"
OPEN_MIN = 9 * 60 + 30
N_MIN = 390  # 09:30-15:59 ET
M_1550 = 15 * 60 + 50 - OPEN_MIN  # 380


def session_1550(data_dir: Path | str, symbol: str, start=None, end=None) -> pd.DataFrame | None:
    """Per session of `symbol`: date, c (last regular-hours close), c1555 (the 15:50 price), v_last (its volume),
    v_first (NaN), on the minute history's basis. None without minute files in the window."""
    if end is None:
        raise ValueError("end is required: the reader never opens a month after it (the hold-out)")
    last_month = str(np.datetime64(end, "M"))
    full = {f.stem: f for f in (Path(data_dir) / "bars_1m" / f"symbol={symbol}").glob("*.parquet")}
    near = {f.stem: f for f in (Path(data_dir) / "bars_1550" / f"symbol={symbol}").glob("*.parquet")}
    files = [f for _, f in sorted({**near, **full}.items()) if f.stem <= last_month]  # the full month wins
    if start is not None:
        files = [f for f in files if f.stem >= str(np.datetime64(start, "M"))]
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f, columns=["ts", "o", "c", "v"]) for f in files], ignore_index=True)
    et = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df["m"] = et.dt.hour * 60 + et.dt.minute - OPEN_MIN
    df["date"] = et.dt.tz_localize(None).dt.normalize()
    df = df[(df["m"] >= 0) & (df["m"] < N_MIN) & (df["date"] <= pd.Timestamp(np.datetime64(end, "D")))]
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(np.datetime64(start, "D"))]
    if not len(df):
        return None
    df = df.sort_values(["date", "m"], kind="stable")
    out = pd.DataFrame({"c": df.groupby("date", sort=True)["c"].last()})
    late = df[df["m"] >= M_1550].groupby("date", sort=True).first()
    out["c1555"] = late["o"].reindex(out.index)
    out["v_last"] = late["v"].reindex(out.index)
    out["v_first"] = np.nan
    out = out.reset_index()
    out["date"] = out["date"].astype("datetime64[ns]")
    out.insert(0, "symbol", symbol)
    return out[["symbol", "date", "c", "c1555", "v_first", "v_last"]]


def arrays(store_root: Path | str, data, data_dir: Path | str, *, start=None, end) -> tuple[np.ndarray, np.ndarray,
                                                                                             dict]:
    """(15:50 price, its minute's volume, notes) as (sessions x symbols) arrays aligned to `data` (a
    `engine.daily.DailyData`), on the daily store's raw basis (`DailyStore.panel(intraday=...)`). NaN: no 15:50
    price for that session."""
    from kaibot.ingest.daily_store import DailyStore

    frames = [f for s in data.symbols if (f := session_1550(data_dir, s, start, end)) is not None]
    p = np.full(data.raw_c.shape, np.nan)
    v = np.full(data.raw_c.shape, np.nan)
    notes = {"symbols": len(data.symbols), "symbols_with_minutes": len(frames), "intraday_rows": 0,
             "intraday_scale_mismatch": 0}
    if not frames:
        return p, v, notes
    lo = str(data.dates[0]) if start is None else start
    panel = DailyStore(store_root).panel(list(data.symbols), lo, end, intraday=pd.concat(frames, ignore_index=True),
                                         minute_share=None)
    notes.update({k: panel.notes.get(k, 0) for k in ("intraday_rows", "intraday_scale_mismatch")})
    rows = {d: n for n, d in enumerate(np.asarray(panel.dates, dtype="datetime64[D]"))}
    cols = {s: j for j, s in enumerate(panel.symbols)}
    ri = np.array([rows.get(d, -1) for d in np.asarray(data.dates, dtype="datetime64[D]")], dtype=int)
    ci = np.array([cols.get(s, -1) for s in data.symbols], dtype=int)
    oi, oj = np.flatnonzero(ri >= 0), np.flatnonzero(ci >= 0)
    p[np.ix_(oi, oj)] = panel.c1555[np.ix_(ri[oi], ci[oj])]
    v[np.ix_(oi, oj)] = panel.v_last[np.ix_(ri[oi], ci[oj])]
    notes["sessions_with_1550"] = int(np.isfinite(p).any(axis=1).sum())
    return p, v, notes
