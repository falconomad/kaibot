"""Quoted-spread calibration for cost model v2 (issue #111, the Data part): offline, research data only.

Cost model v2 (research only, in `kaibot.broker`) charges a half-spread per side by the stock's 20-session average
dollar volume (ADV) bucket, taken from the research report's table. This module measures the real quoted half-spread
from Databento `bbo-1m` minutes that `scripts/fetch_databento.py` has already cached, and compares it with the model,
bucket by bucket. It never downloads anything and never changes the model: a change to `V2Params` is a parameter
change that Backend-quant makes with a replay that logs a trial.

The live code never imports cost model v2 (a guard test pins that), so this module does not either: the caller
passes the model's table as a `SpreadTable` (`scripts/fetch_spread_calibration.py` builds it from `V2Params`).

Two steps (`scripts/fetch_spread_calibration.py`):

1. `plan_sample`: from the daily bar store, the ADV of every key over the 20 sessions before the calibration window
   (raw close x volume, point in time), its v2 bucket, and a small spread of symbols per bucket (evenly spaced by
   ADV inside the bucket, deterministic). The script prints one DRY-RUN `fetch_databento.py` command per session,
   regular hours only, so the cost estimate is seen before any credit is spent (per-request cap and total credit
   are enforced by `kaibot.ingest.databento_hist`).
2. `calibrate`: reads the cached `bbo-1m` Parquet files, keeps two-sided, uncrossed quotes inside each NYSE
   session (early closes included), and measures the half-spread in bp of the mid per minute:

       half_bp = (ask - bid) / 2 / ((ask + bid) / 2) x 10,000

   Windows: `open` = the first 5 minutes after 09:30 ET, `close` = the last 5 minutes before the close,
   `midday` = 10:00 ET to 30 minutes before the close (what v2's bucket table means), `other` = the rest.
   Per symbol: the median and 75th percentile midday half-spread, the open / midday and close / midday ratios, and
   the v2 model's quoted half-spread for its bucket (tick floor of half a cent on the median raw mid, as v2 applies
   it). Per bucket: the median of the symbols' midday medians against the model.

Verdicts (model / measured, per symbol and per bucket; the model must stay conservative, so `under` is the one that
matters):

    under          the model charges LESS than the measured midday median: costs are understated
    ok             the model charges 1x to 2x the measured median
    conservative   the model charges more than 2x the measured median
    no_data        no valid midday minutes

Output under <data>/databento/calibration/: `spreads_by_symbol.csv`, `spreads_by_bucket.csv`, `calibration.txt`.

Quote timestamps: a `bbo-1m` record is the best bid and offer at the END of its minute (`ts_recv`), so the minute it
describes starts one minute earlier. Files written by `databento_hist` also carry `ts_event` and `available_ts`;
`ts_recv` is preferred, then `ts_event` (taken as the minute start).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest.daily_store import DailyStore

DATASET = "EQUS.MINI"
SCHEMA = "bbo-1m"
ADV_SESSIONS = 20
EDGE_MIN = 5  # open / close windows: the first and last 5 minutes of the session
MIDDAY_START_MIN = 30  # midday starts 30 minutes after the open (10:00 ET)
MIDDAY_END_MIN = 30  # ... and ends 30 minutes before the close
MAX_HALF_BP = 1000.0  # a quote wider than 10% each side of the mid is a bad print, not a spread
FIXED_SCALE = 1e-9  # Databento fixed-point prices (int64, 1e-9 units) when a frame was saved without conversion
OK_MAX_RATIO = 2.0  # model / measured above this: conservative
WINDOWS = ("open", "midday", "close", "other")
SYMBOL_COLUMNS = ("symbol", "adv_usd", "bucket", "model_half_bp", "minutes", "rejected", "sessions", "median_mid",
                  "midday_median_bp", "midday_p75_bp", "open_median_bp", "close_median_bp", "open_ratio",
                  "close_ratio", "model_over_measured", "verdict")
BUCKET_COLUMNS = ("bucket", "model_half_bp", "symbols", "measured_median_bp", "measured_p75_bp",
                  "open_ratio_median", "close_ratio_median", "model_over_measured", "verdict")


# ---------------------------------------------------------------- buckets


@dataclass(frozen=True)
class SpreadTable:
    """The half-spread table of the cost model under test, copied from its parameters by the caller."""

    buckets: tuple[tuple[float, float], ...]  # (ADV lower bound $, half-spread bp per side), most liquid first
    floor_bp: float  # below the last bound or unknown ADV
    tick_half: float = 0.005  # $ per share on the raw price
    open_mult: float = 2.0  # what the model assumes for the opening auction, x the midday half-spread
    close_mult: float = 1.0  # ... and for the closing auction

    def __post_init__(self) -> None:
        bounds = [b for b, _ in self.buckets]
        if not bounds or bounds != sorted(bounds, reverse=True):
            raise ValueError("buckets must be non-empty and ordered from the most liquid down")

    def half_bp(self, adv: float) -> float:
        if isinstance(adv, int | float) and math.isfinite(adv) and adv > 0:
            for lo, bp in self.buckets:
                if adv >= lo:
                    return bp
        return self.floor_bp


def bucket_labels(p: SpreadTable) -> list[str]:
    """Bucket names from the most liquid down, e.g. ['>=500M', '100M-500M', '50M-100M', '<50M']."""
    bounds = [b for b, _ in p.buckets]
    out = [f">={_m(bounds[0])}"]
    out += [f"{_m(lo)}-{_m(hi)}" for hi, lo in zip(bounds, bounds[1:], strict=False)]
    out.append(f"<{_m(bounds[-1])}")
    return out


def _m(x: float) -> str:
    return f"{x / 1e9:g}B" if x >= 1e9 else f"{x / 1e6:g}M"


def bucket_of(adv: float, p: SpreadTable) -> str:
    """The bucket of a 20-session ADV in $; unknown or non-positive ADV is the last (least liquid) bucket, as
    v2 charges it."""
    labels = bucket_labels(p)
    if isinstance(adv, int | float) and math.isfinite(adv) and adv > 0:
        for i, (lo, _) in enumerate(p.buckets):
            if adv >= lo:
                return labels[i]
    return labels[-1]


def model_half_bp(adv: float, p: SpreadTable, mid: float | None = None) -> float:
    """The model's quoted half-spread in bp for this ADV, with the tick floor on the raw `mid` when given."""
    bp = p.half_bp(adv)
    if mid is not None and math.isfinite(mid) and mid > 0:
        bp = max(bp, p.tick_half / mid * 1e4)
    return bp


# ---------------------------------------------------------------- ADV and the sample


def adv_table(store: DailyStore, before: date, p: SpreadTable, keys: Iterable[str] | None = None,
              sessions: int = ADV_SESSIONS) -> pd.DataFrame:
    """Per key: mean raw dollar volume (close x volume) over the last `sessions` bars strictly before `before`
    (point in time for a window that starts on `before`), and its v2 bucket. Keys with fewer bars have NaN ADV
    (bucket: the least liquid). Columns: symbol, adv_usd, bars, bucket."""
    rows = []
    last = before - timedelta(days=1)
    for k in (keys if keys is not None else store.keys()):
        try:
            b = store.bars(k, end=last)
        except FileNotFoundError:
            b = pd.DataFrame(columns=["c", "v"])
        b = b.tail(sessions)
        dv = (pd.to_numeric(b["c"], errors="coerce") * pd.to_numeric(b["v"], errors="coerce")).to_numpy(float)
        ok = len(b) == sessions and np.isfinite(dv).all() and (dv >= 0).all()
        adv = float(dv.mean()) if ok else math.nan
        rows.append({"symbol": k, "adv_usd": adv, "bars": len(b), "bucket": bucket_of(adv, p)})
    return pd.DataFrame(rows, columns=["symbol", "adv_usd", "bars", "bucket"])


def plan_sample(advs: pd.DataFrame, p: SpreadTable, per_bucket: int = 4,
                exclude: Sequence[str] = ()) -> pd.DataFrame:
    """`per_bucket` symbols per v2 bucket, evenly spaced by ADV inside the bucket (both ends included), so the
    sample covers the bucket rather than its most liquid names. Keys with unknown ADV are never picked.
    Deterministic. Columns: those of `adv_table`."""
    if per_bucket < 1:
        raise ValueError("per_bucket must be at least 1")
    drop = {s.upper() for s in exclude}
    known = advs[np.isfinite(advs["adv_usd"].to_numpy(float)) & ~advs["symbol"].str.upper().isin(drop)]
    parts = []
    for label in bucket_labels(p):
        b = known[known["bucket"] == label].sort_values(["adv_usd", "symbol"], ascending=[False, True])
        if len(b) == 0:
            continue
        idx = np.unique(np.round(np.linspace(0, len(b) - 1, min(per_bucket, len(b)))).astype(int))
        parts.append(b.iloc[idx])
    if not parts:
        return advs.iloc[0:0].copy()
    return pd.concat(parts).reset_index(drop=True)


def fetch_commands(symbols: Sequence[str], days: Sequence[date], env: str = ".env") -> list[str]:
    """One dry-run `fetch_databento.py` command per session, regular hours only (the cheapest request that holds
    every window this module measures). Add --confirm only after reading the estimate."""
    syms = ",".join(sorted({s.upper() for s in symbols}))
    out = []
    for d in days:
        a, b = nyse.open_utc(d), nyse.close_utc(d)
        out.append(f"python scripts/fetch_databento.py --env {env} --schema {SCHEMA} --dataset {DATASET} "
                   f"--symbols {syms} --start {a.strftime('%Y-%m-%dT%H:%MZ')} --end {b.strftime('%Y-%m-%dT%H:%MZ')}")
    return out


# ---------------------------------------------------------------- measuring


def _prices(s: pd.Series) -> np.ndarray:
    a = pd.to_numeric(s, errors="coerce")
    if pd.api.types.is_integer_dtype(s.dtype):
        a = a.where(a != np.iinfo(np.int64).max) * FIXED_SCALE  # UNDEF_PRICE -> NaN, then fixed-point -> $
    return a.to_numpy(float)


def _minute_start(df: pd.DataFrame) -> pd.Series:
    if "ts_recv" in df.columns:
        return (pd.to_datetime(df["ts_recv"], utc=True) - pd.Timedelta(minutes=1)).dt.floor("min")
    if "ts_event" in df.columns:
        return pd.to_datetime(df["ts_event"], utc=True).dt.floor("min")
    raise KeyError("a bbo-1m frame needs ts_recv or ts_event")


def minute_spreads(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Valid regular-session minutes of one symbol's bbo-1m frame: minute (UTC start), session, window, mid,
    half_bp; and the number of in-session rows rejected (one-sided, crossed, locked or wider than MAX_HALF_BP).
    Minutes outside NYSE sessions (pre-market, after hours, holidays) are dropped without counting."""
    cols = ["minute", "session", "window", "mid", "half_bp"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols), 0
    t = _minute_start(df)
    bid, ask = _prices(df["bid_px_00"]), _prices(df["ask_px_00"])
    et = t.dt.tz_convert(nyse.ET)
    day = et.dt.date
    rows = []
    rejected = 0
    opens: dict[date, tuple] = {}
    for i, (m, d) in enumerate(zip(t, day, strict=True)):
        if d not in opens:
            opens[d] = (nyse.open_utc(d), nyse.close_utc(d)) if nyse.is_session(d) else None
        oc = opens[d]
        if oc is None or not (oc[0] <= m.to_pydatetime() < oc[1]):
            continue
        b, a = bid[i], ask[i]
        mid = (a + b) / 2
        half = (a - b) / 2 / mid * 1e4 if math.isfinite(mid) and mid > 0 else math.nan
        if not (math.isfinite(b) and math.isfinite(a) and b > 0 and a > b and half <= MAX_HALF_BP):
            rejected += 1
            continue
        since = (m.to_pydatetime() - oc[0]).total_seconds() / 60
        until = (oc[1] - m.to_pydatetime()).total_seconds() / 60
        if since < EDGE_MIN:
            w = "open"
        elif until <= EDGE_MIN:
            w = "close"
        elif since >= MIDDAY_START_MIN and until > MIDDAY_END_MIN:
            w = "midday"
        else:
            w = "other"
        rows.append((m, d, w, mid, half))
    out = pd.DataFrame(rows, columns=cols).drop_duplicates("minute", keep="last")
    return out.sort_values("minute").reset_index(drop=True), rejected


def cache_files(root: Path, symbol: str, dataset: str = DATASET) -> list[Path]:
    """Every cached bbo-1m Parquet file of one symbol (`.empty` markers have no rows and are skipped)."""
    d = Path(root) / "databento" / dataset / SCHEMA / f"symbol={symbol}"
    return sorted(d.glob("*.parquet")) if d.is_dir() else []


def cached_symbols(root: Path, dataset: str = DATASET) -> list[str]:
    d = Path(root) / "databento" / dataset / SCHEMA
    if not d.is_dir():
        return []
    return sorted(p.name[len("symbol="):] for p in d.glob("symbol=*") if p.is_dir() and any(p.glob("*.parquet")))


def load_symbol(root: Path, symbol: str, dataset: str = DATASET) -> pd.DataFrame:
    parts = [pd.read_parquet(f) for f in cache_files(root, symbol, dataset)]
    parts = [x for x in parts if len(x)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _verdict(model: float, measured: float) -> tuple[float, str]:
    if not (math.isfinite(measured) and measured > 0):
        return math.nan, "no_data"
    r = model / measured
    return r, "under" if r < 1.0 else "ok" if r <= OK_MAX_RATIO else "conservative"


def _med(x: pd.Series) -> float:
    return float(np.median(x)) if len(x) else math.nan


@dataclass
class Calibration:
    by_symbol: pd.DataFrame
    by_bucket: pd.DataFrame

    table: SpreadTable

    def text(self) -> str:
        lines = ["Quoted half-spread calibration for cost model v2 (bbo-1m, regular hours, bp of the mid per side)",
                 "Model = V2Params bucket half-spread (tick floor on the raw mid). Verdict 'under' = the model charges",
                 "less than the measured midday median, i.e. costs are understated. Nothing here changes the model.",
                 ""]
        if len(self.by_bucket) == 0:
            lines.append("No cached bbo-1m minutes: run the plan's dry-run commands first.")
            return "\n".join(lines) + "\n"
        for r in self.by_bucket.itertuples(index=False):
            lines.append(f"{r.bucket:>10}: model {r.model_half_bp:.2f} bp, measured median {r.measured_median_bp:.2f}"
                         f" bp (p75 {r.measured_p75_bp:.2f}) over {r.symbols} symbols, open x{r.open_ratio_median:.2f}"
                         f" / close x{r.close_ratio_median:.2f} of midday -> {r.verdict}")
        under = self.by_symbol[self.by_symbol["verdict"] == "under"]["symbol"].tolist()
        lines += ["", f"Symbols the model under-charges: {', '.join(under) if under else 'none'}",
                  f"The model assumes open x{self.table.open_mult:g} and close x{self.table.close_mult:g} of the "
                  "midday "
                  "half-spread; compare with the ratios above.",
                  "A change to V2Params is a parameter change for Backend-quant (replay + trial row)."]
        return "\n".join(lines) + "\n"

    def write(self, out_dir: Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [out_dir / "spreads_by_symbol.csv", out_dir / "spreads_by_bucket.csv", out_dir / "calibration.txt"]
        self.by_symbol.to_csv(paths[0], index=False, float_format="%.4f")
        self.by_bucket.to_csv(paths[1], index=False, float_format="%.4f")
        paths[2].write_text(self.text())
        return paths


def calibrate(root: Path, p: SpreadTable, advs: pd.DataFrame | dict[str, float] | None = None,
              symbols: Sequence[str] | None = None, dataset: str = DATASET) -> Calibration:
    """Measured against modelled half-spreads for every cached symbol (or `symbols`). `advs`: an `adv_table`
    frame or {symbol: ADV $}; a symbol without one is measured against the least liquid bucket."""
    if isinstance(advs, pd.DataFrame):
        adv_of = dict(zip(advs["symbol"], advs["adv_usd"].astype(float), strict=True))
    else:
        adv_of = dict(advs or {})
    rows = []
    for s in (symbols if symbols is not None else cached_symbols(root, dataset)):
        m, rejected = minute_spreads(load_symbol(root, s, dataset))
        adv = float(adv_of.get(s, math.nan))
        mid = _med(m["mid"])
        model = model_half_bp(adv, p, mid)
        by = {w: m.loc[m["window"] == w, "half_bp"] for w in WINDOWS}
        midday = _med(by["midday"])
        o, c = _med(by["open"]), _med(by["close"])
        ratio, verdict = _verdict(model, midday)
        rows.append({"symbol": s, "adv_usd": adv, "bucket": bucket_of(adv, p), "model_half_bp": model,
                     "minutes": len(m), "rejected": rejected, "sessions": int(m["session"].nunique()),
                     "median_mid": mid, "midday_median_bp": midday,
                     "midday_p75_bp": float(np.percentile(by["midday"], 75)) if len(by["midday"]) else math.nan,
                     "open_median_bp": o, "close_median_bp": c,
                     "open_ratio": o / midday if midday > 0 else math.nan,
                     "close_ratio": c / midday if midday > 0 else math.nan,
                     "model_over_measured": ratio, "verdict": verdict})
    sym = pd.DataFrame(rows, columns=list(SYMBOL_COLUMNS))
    brows = []
    for label, (_, bp) in zip(bucket_labels(p), [*p.buckets, (0.0, p.floor_bp)], strict=True):
        g = sym[(sym["bucket"] == label) & (sym["verdict"] != "no_data")]
        if len(g) == 0:
            continue
        meas = _med(g["midday_median_bp"])
        ratio, verdict = _verdict(bp, meas)
        brows.append({"bucket": label, "model_half_bp": bp, "symbols": len(g), "measured_median_bp": meas,
                      "measured_p75_bp": float(np.percentile(g["midday_median_bp"], 75)),
                      "open_ratio_median": float(np.nanmedian(g["open_ratio"])) if g["open_ratio"].notna().any()
                      else math.nan,
                      "close_ratio_median": float(np.nanmedian(g["close_ratio"])) if g["close_ratio"].notna().any()
                      else math.nan,
                      "model_over_measured": ratio, "verdict": verdict})
    return Calibration(sym, pd.DataFrame(brows, columns=list(BUCKET_COLUMNS)), p)
