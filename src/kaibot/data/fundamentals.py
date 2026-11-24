"""Point-in-time reader of the SEC fundamentals store (data/fundamentals/, from scripts/fetch_sec_fundamentals.py).

    store = Fundamentals(Path("data/fundamentals"))
    store.as_of(date(2020, 5, 1))                                   # every value known on 1 May 2020
    store.latest(date(2020, 5, 1), "revenues", period="Q")          # newest quarter per company, as known then
    store.cik_for("FB", date(2020, 5, 1))                            # -> "0001326801"

Rule: a version is visible on day `d` only if `available_date <= d` (available_date = filed + 1 day). For each
(cik, metric, period) the reader takes, among the visible versions, the preferred concept (METRICS order) and then
the latest filed version of it (tie: the larger accession number). So a restatement replaces the original only from
the day after the restatement was filed, and never before. Research only; the engine does not read this.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from kaibot.ingest.sec_fundamentals import CIK_SCHEMA, SCHEMA

PERIODS = {"Q": (80, 100), "H": (170, 195), "9M": (260, 285), "FY": (350, 380)}  # duration_days ranges
PERIOD_KEY = ["cik", "metric", "period_start", "period_end"]


def _day(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return pd.Timestamp(d).date()


class Fundamentals:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._facts: pd.DataFrame | None = None
        self._ciks: pd.DataFrame | None = None

    # ------------------------------------------------------------ loading
    @property
    def facts(self) -> pd.DataFrame:
        if self._facts is None:
            files = sorted((self.root / "facts").glob("cik=*.parquet"))
            tables = [pq.read_table(f, schema=SCHEMA).to_pandas() for f in files]
            df = pd.concat(tables, ignore_index=True) if tables else SCHEMA.empty_table().to_pandas()
            for c in ("period_start", "period_end", "filed", "available_date"):
                df[c] = pd.to_datetime(df[c]).dt.date
            self._facts = df
        return self._facts

    @property
    def ciks(self) -> pd.DataFrame:
        if self._ciks is None:
            p = self.root / "ciks.parquet"
            self._ciks = (pq.read_table(p, schema=CIK_SCHEMA) if p.is_file() else CIK_SCHEMA.empty_table()).to_pandas()
        return self._ciks

    # ------------------------------------------------------------ point in time
    def as_of(self, d, ciks: Iterable[str] | None = None, metrics: Iterable[str] | None = None) -> pd.DataFrame:
        """One row per (cik, metric, period_start, period_end): the value as it was known on day `d`."""
        d = _day(d)
        f = self.facts
        f = f[f["available_date"] <= d]
        if ciks is not None:
            f = f[f["cik"].isin(set(ciks))]
        if metrics is not None:
            f = f[f["metric"].isin(set(metrics))]
        if f.empty:
            return f.reset_index(drop=True)
        f = f.assign(_ps=f["period_start"].fillna(date.min))
        f = f.sort_values(["cik", "metric", "_ps", "period_end", "priority", "filed", "accn", "value"],
                          ascending=[True, True, True, True, True, False, False, False], kind="mergesort")
        out = f.drop_duplicates(["cik", "metric", "_ps", "period_end"], keep="first")
        return out.drop(columns="_ps").reset_index(drop=True)

    def latest(self, d, metric: str, period: str = "FY", ciks: Iterable[str] | None = None) -> pd.DataFrame:
        """Per company, the newest period of `metric` known on `d`. `period`: Q, H, 9M, FY (flows, by duration)
        or "instant" (balance-sheet values and share counts)."""
        a = self.as_of(d, ciks, [metric])
        if period == "instant":
            a = a[a["duration_days"].isna()]
        else:
            lo, hi = PERIODS[period]
            a = a[a["duration_days"].between(lo, hi)]
        if a.empty:
            return a
        a = a.sort_values(["cik", "period_end", "filed"], kind="mergesort")
        return a.drop_duplicates("cik", keep="last").reset_index(drop=True)

    def history(self, cik: str, metric: str) -> pd.DataFrame:
        """Every filed version of `metric` for `cik` (all restatements), oldest filing first: for audits."""
        f = self.facts
        f = f[(f["cik"] == cik) & (f["metric"] == metric)]
        return f.sort_values(["period_end", "filed", "accn"], kind="mergesort").reset_index(drop=True)

    # ------------------------------------------------------------ ticker <-> CIK
    def cik_for(self, ticker: str, d) -> str | None:
        """The CIK of the S&P 500 company listed under `ticker` on `d` (None outside its membership)."""
        d = _day(d)
        c = self.ciks
        hit = c[(c["ticker"] == ticker.upper()) & (c["valid_from"] <= d)
                & (c["valid_to"].isna() | (c["valid_to"].map(lambda x: x is None or x > d)))]
        return None if hit.empty else str(hit.iloc[-1]["cik"])

    def ticker_for(self, cik: str, d) -> str | None:
        d = _day(d)
        c = self.ciks
        hit = c[(c["cik"] == cik) & (c["valid_from"] <= d)
                & (c["valid_to"].isna() | (c["valid_to"].map(lambda x: x is None or x > d)))]
        return None if hit.empty else str(hit.iloc[-1]["ticker"])

    def for_ticker(self, ticker: str, d, metrics: Iterable[str] | None = None) -> pd.DataFrame:
        cik = self.cik_for(ticker, d)
        return self.as_of(d, [cik], metrics) if cik else self.facts.iloc[0:0]
