"""Point-in-time fundamental quality score (0-100) from SEC XBRL companyfacts.

For every trading date, each company is scored only on its latest ANNUAL (10-K, fp=FY) figures whose ORIGINAL
filing date is before that date. Components (equal weights, cross-sectional percentile within the universe):
return on assets, net margin, revenue growth (YoY), cash quality = clip(operating cash flow / net income, 0, 2).
A missing component is skipped (e.g. banks without a revenue tag); the score averages the available ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"]


def _annual(facts: dict, tag: str, duration: bool) -> pd.DataFrame:
    try:
        units = facts["facts"]["us-gaap"][tag]["units"]["USD"]
    except KeyError:
        return pd.DataFrame(columns=["end", "filed", "val"])
    rows = []
    for u in units:
        if not str(u.get("form", "")).startswith("10-K") or u.get("fp") != "FY":
            continue
        end = pd.Timestamp(u["end"])
        if duration:
            if "start" not in u:
                continue
            days = (end - pd.Timestamp(u["start"])).days
            if not 350 <= days <= 380:
                continue
        rows.append({"end": end, "filed": pd.Timestamp(u["filed"]), "val": float(u["val"])})
    if not rows:
        return pd.DataFrame(columns=["end", "filed", "val"])
    df = pd.DataFrame(rows).sort_values("filed")
    return df.groupby("end", as_index=False).first()  # the ORIGINAL report of each fiscal year (no restatements)


def _latest_of(facts: dict, tags: list[str]) -> pd.DataFrame:
    """Per fiscal year, take the first tag (in preference order) that reports it; companies switch tags."""
    frames = [_annual(facts, t, True) for t in tags]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame(columns=["end", "filed", "val"])
    out = frames[0]
    for f in frames[1:]:
        out = pd.concat([out, f[~f["end"].isin(out["end"])]], ignore_index=True)
    return out.sort_values("end").reset_index(drop=True)


def company_metrics(path: Path) -> pd.DataFrame:
    facts = json.loads(path.read_text())
    ni = _latest_of(facts, ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"])
    assets = _annual(facts, "Assets", False)
    ocf = _annual(facts, "NetCashProvidedByUsedInOperatingActivities", True)
    rev = _latest_of(facts, REVENUE_TAGS)
    if ni.empty:
        return pd.DataFrame()
    df = ni.rename(columns={"val": "ni"})
    for name, frame in (("assets", assets), ("ocf", ocf), ("rev", rev)):
        df = df.merge(frame[["end", "val"]].rename(columns={"val": name}), on="end", how="left")
    df = df.sort_values("end")
    prev_rev = df["rev"].shift(1)
    prev_end = df["end"].shift(1)
    yearly = (df["end"] - prev_end).dt.days.between(340, 390)
    df["roa"] = df["ni"] / df["assets"]
    df["margin"] = df["ni"] / df["rev"]
    df["growth"] = np.where(yearly, df["rev"] / prev_rev - 1, np.nan)
    df["cashq"] = np.where(df["ni"] > 0, np.clip(df["ocf"] / df["ni"], 0, 2), 0.0)
    return df[["end", "filed", "roa", "margin", "growth", "cashq"]]


def quality_panel(fund_dir: Path, symbols: list[str], dates: np.ndarray) -> pd.DataFrame:
    """DataFrame indexed by date, columns = symbols, values = point-in-time quality score 0-100 (NaN = no data)."""
    comps = ["roa", "margin", "growth", "cashq"]
    per_sym = {}
    for s in symbols:
        f = fund_dir / f"{s}.json"
        if f.exists():
            m = company_metrics(f)
            if len(m):
                per_sym[s] = m.sort_values("filed")
    idx = pd.to_datetime(dates)
    scores = {}
    for comp in comps:
        mat = pd.DataFrame(index=idx, columns=list(per_sym), dtype=float)
        for s, m in per_sym.items():
            # value known on date d = latest row with filed < d
            ser = pd.Series(m[comp].to_numpy(), index=m["filed"]).dropna()
            ser = ser[~ser.index.duplicated(keep="last")]
            known = ser.reindex(idx - pd.Timedelta(days=1), method="ffill").to_numpy()
            mat[s] = known
        scores[comp] = mat.rank(axis=1, pct=True)
    stacked = np.nanmean(np.stack([scores[c].to_numpy() for c in comps]), axis=0) * 100
    return pd.DataFrame(stacked, index=idx, columns=list(per_sym))
