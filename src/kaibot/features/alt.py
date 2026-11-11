"""Point-in-time alt-data features for logging and the M3 ablation (story S37). NOT for trading.

Tech design §1: "Alt data is unreachable from features, except pump_risk". This module is that single, audited
door: it is the only file under the trading packages allowed to import kaibot.ingest.alt, and no engine, strategy,
risk, broker, agents or replay module may import it (tests/unit/test_alt_isolation.py). The live veto lives in
kaibot.features.pump (re-exported here) and does not read alt data, so the engine can use it without this module.

Every reader goes through kaibot.ingest.alt.store.read_pit: only rows with available_ts <= decision_ts, latest
version per key. Missing data (no files, no rows for the symbol, too little history) gives None, never an error.

- short_volume: FINRA Reg SHO CNMS short volume / total volume for the latest trade date known at decision_ts,
  vs the mean ratio of the previous 20 trade dates (needs >= 10).
- insider_net_buying: Form 4 non-derivative open-market purchases (code P) minus sales (code S) with a
  transaction date in the last 30 days. Zero only for symbols the collector covers (they appear in the filing
  ledger); otherwise None.
- gdelt_tone_z: z-score of the latest 15-minute GDELT average tone of a macro query vs the previous buckets of
  the last 7 days (needs >= 20).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from kaibot.features.pump import pump_risk, rules_veto_extra
from kaibot.ingest.alt import finra, form4, gdelt, store

__all__ = ["alt_features", "gdelt_tone_z", "insider_net_buying", "pump_risk", "rules_veto_extra", "short_volume"]
log = logging.getLogger("kaibot.features.alt")


def _pit(root: Path, source: str, decision_ts: datetime, key: list[str]) -> pd.DataFrame:
    try:
        return store.read_pit(Path(root), source, decision_ts, key)
    except Exception as e:  # unreadable / partial file: the feature is missing, the caller carries on
        log.warning("alt %s unreadable: %s", source, type(e).__name__)
        return pd.DataFrame()


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def short_volume(root: Path, symbol: str, decision_ts: datetime, n: int = 20) -> dict | None:
    df = _pit(root, finra.SOURCE, decision_ts, finra.KEY)
    if df.empty or "symbol" not in df:
        return None
    d = df[(df["symbol"] == symbol) & (df["total_volume"] > 0)].sort_values("trade_date")
    if d.empty:
        return None
    ratio = (d["short_volume"] / d["total_volume"]).astype(float)
    latest = _f(ratio.iloc[-1])
    prior = ratio.iloc[:-1].tail(n)
    mean = _f(prior.mean()) if len(prior) >= max(10, n // 2) else None
    return {"trade_date": str(d["trade_date"].iloc[-1]), "svr": latest, "svr_mean20": mean,
            "svr_rel": (latest / mean) if latest is not None and mean else None}


def insider_net_buying(root: Path, symbol: str, decision_ts: datetime, days: int = 30) -> dict | None:
    ledger = _pit(root, form4.LEDGER, decision_ts, ["accession"])
    if ledger.empty or "symbol" not in ledger or not (ledger["symbol"] == symbol).any():
        return None
    tx = _pit(root, form4.SOURCE, decision_ts, form4.TX_KEY)
    zero = {"net_shares": 0.0, "net_usd": 0.0, "n_buys": 0, "n_sells": 0, "days": days}
    if tx.empty or "symbol" not in tx:
        return zero
    since = (pd.Timestamp(decision_ts) - timedelta(days=days)).date()
    t = tx[(tx["symbol"] == symbol) & (~tx["derivative"].astype(bool)) & tx["code"].isin(["P", "S"])]
    t = t[pd.to_datetime(t["transaction_date"]).dt.date >= since]
    if t.empty:
        return zero
    sign = t["code"].map({"P": 1.0, "S": -1.0})
    shares = t["shares"].astype(float).fillna(0.0)
    usd = (shares * t["price"].astype(float).fillna(0.0) * sign).sum()
    return {"net_shares": float((shares * sign).sum()), "net_usd": float(usd), "n_buys": int((sign > 0).sum()),
            "n_sells": int((sign < 0).sum()), "days": days}


def gdelt_tone_z(root: Path, query_name: str, decision_ts: datetime, days: int = 7, min_n: int = 20,
                 _df: pd.DataFrame | None = None) -> float | None:
    df = _pit(root, gdelt.SOURCE, decision_ts, gdelt.KEY) if _df is None else _df
    if df.empty or "query_name" not in df:
        return None
    ts = pd.Timestamp(decision_ts)
    d = df[(df["query_name"] == query_name) & (df["mode"] == "timelinetone") & (df["bucket_ts"] <= ts)
           & (df["bucket_ts"] > ts - timedelta(days=days))].sort_values("bucket_ts")
    if len(d) < min_n + 1:
        return None
    v = d["value"].astype(float)
    prior = v.iloc[:-1]
    sd = _f(prior.std(ddof=1))
    if not sd:
        return None
    return _f((v.iloc[-1] - prior.mean()) / sd)


def alt_features(root: Path, symbol: str, decision_ts: datetime) -> dict:
    """Everything above for one symbol, for the signal log / ablation. Each value may be None."""
    out: dict = {}
    for name, fn in (("short_volume", lambda: short_volume(root, symbol, decision_ts)),
                     ("insider_30d", lambda: insider_net_buying(root, symbol, decision_ts))):
        try:
            out[name] = fn()
        except Exception as e:
            log.warning("alt feature %s failed for %s: %s", name, symbol, type(e).__name__)
            out[name] = None
    tone = {}
    g = _pit(root, gdelt.SOURCE, decision_ts, gdelt.KEY)  # read once for all queries
    for q in gdelt.QUERIES:
        try:
            tone[q] = gdelt_tone_z(root, q, decision_ts, _df=g)
        except Exception as e:
            log.warning("gdelt tone %s failed: %s", q, type(e).__name__)
            tone[q] = None
    out["gdelt_tone_z"] = tone
    return out
