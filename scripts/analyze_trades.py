"""Break a replay's trades CSV down by year, exit type, entry time, VIX level and sector (issue #109).

python scripts/analyze_trades.py data/reports/trades_balanced_wf_A_<ts>_tuned.csv
    [--equity 100000] [--universe config/universe_history.yaml] [--vix data/inputs/fred_vixcls.json]

Per group: trades, share, win rate, mean R (P&L / risk at entry), total R, and mean bp of equity per trade gross
(no-cost prices) and net. Files written before the cost split have no gross columns: gross shows n/a. The VIX is
the previous day's close: the CSV's `vix` column, or for older files the cached FRED series. Pandas only, no network.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TIME_EDGES = (0, 30, 60, 120, 240, 391)  # minutes after 09:30 ET
TIME_LABELS = ("09:30-10:00", "10:00-10:30", "10:30-11:30", "11:30-13:30", "after 13:30")
VIX_EDGES = (-np.inf, 15, 25, 35, np.inf)
VIX_LABELS = ("<15", "15-25", "25-35", ">=35")


def load_trades(path: Path) -> pd.DataFrame:
    t = pd.read_csv(path)
    t["date"] = pd.to_datetime(t["date"])
    t["r"] = np.where(t["risk"] > 0, t["pnl"] / t["risk"].where(t["risk"] > 0), np.nan)
    return t


def sectors(universe: Path) -> dict[str, str]:
    import yaml

    cfg = yaml.safe_load(universe.read_text())
    return {s: e for e, syms in cfg["sectors"].items() for s in syms}


def vix_prev_close(cache: Path, dates: pd.Series) -> pd.Series:
    """Previous day's VIX close for each trade date from the cached FRED file (the same rule as the replay)."""
    obs = json.loads(cache.read_text())
    s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o["value"] != "."}).sort_index()
    prev = s.shift(1, freq="D").resample("D").ffill()
    return prev.reindex(pd.DatetimeIndex(dates)).ffill().set_axis(dates.index)


def add_groups(t: pd.DataFrame, sector_of: dict[str, str] | None = None, vix: pd.Series | None = None) -> pd.DataFrame:
    t = t.copy()
    t["year"] = t["date"].dt.year
    t["entry_time"] = pd.cut(t["entry_m"], TIME_EDGES, right=False, labels=TIME_LABELS)
    if "vix" not in t.columns and vix is not None:
        t["vix"] = vix
    if "vix" in t.columns:
        t["vix_bucket"] = pd.cut(t["vix"], VIX_EDGES, right=False, labels=VIX_LABELS)
    if sector_of is not None:
        t["sector"] = t["symbol"].map(sector_of).fillna("unknown")
    return t


def breakdown(t: pd.DataFrame, by: str | None, equity: float) -> pd.DataFrame:
    """One row per group (plus `all`): trades, share %, win rate %, mean R, total R, gross bp, net bp."""
    has_gross = "gross_pnl" in t.columns

    def row(g: pd.DataFrame) -> dict:
        n = len(g)
        return {"trades": n, "share_pct": round(100 * n / max(len(t), 1), 1),
                "win_pct": round(100 * float((g["pnl"] > 0).mean()), 1) if n else np.nan,
                "mean_r": round(float(g["r"].mean()), 3) if n else np.nan,
                "total_r": round(float(g["r"].sum()), 1),
                "gross_bp": round(float(g["gross_pnl"].mean()) / equity * 1e4, 3) if has_gross and n else np.nan,
                "net_bp": round(float(g["pnl"].mean()) / equity * 1e4, 3) if n else np.nan}

    rows = {}
    if by is not None:
        for key, g in t.groupby(by, observed=True, sort=True):
            rows[str(key)] = row(g)
    rows["all"] = row(t)
    return pd.DataFrame.from_dict(rows, orient="index")


def cost_summary(t: pd.DataFrame, equity: float) -> pd.DataFrame | None:
    """Gross, each cost and net: $ total and mean bp of equity per trade. None for files without the split."""
    if "gross_pnl" not in t.columns or not len(t):
        return None
    parts = {"gross (no-cost prices)": t["gross_pnl"].sum(), "spread": -t["cost_spread"].sum(),
             "impact": -t["cost_impact"].sum(), "slippage past stops": -t["cost_slippage"].sum(),
             "fees": -t["cost_fees"].sum(), "net": t["pnl"].sum()}
    return pd.DataFrame({"total_usd": {k: round(float(v), 2) + 0.0 for k, v in parts.items()},  # + 0.0: no "-0.0"
                         "bp_per_trade": {k: round(float(v) / len(t) / equity * 1e4, 3) + 0.0
                                          for k, v in parts.items()}})


def report(t: pd.DataFrame, equity: float) -> str:
    if not len(t):
        return "0 trades in this file"
    out = [f"{len(t)} trades, {t['date'].min().date()} to {t['date'].max().date()}; bp are of ${equity:,.0f} equity"]
    cs = cost_summary(t, equity)
    out += ["", "== Gross vs net ==",
            cs.to_string() if cs is not None else "no cost columns in this file (written before the cost split)"]
    for title, col in (("By year", "year"), ("By exit", "reason"), ("By entry time (ET)", "entry_time"),
                       ("By VIX (previous close)", "vix_bucket"), ("By sector", "sector")):
        out += ["", f"== {title} =="]
        if col not in t.columns:
            out.append("not available (pass --vix / --universe, or use a file with the column)")
            continue
        out.append(breakdown(t, col, equity).to_string(na_rep="n/a"))
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("trades", help="trades CSV written by scripts/walk_forward.py or scripts/run_replay.py")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--vix", default=str(ROOT / "data" / "inputs" / "fred_vixcls.json"))
    a = ap.parse_args()
    t = load_trades(Path(a.trades))
    vix = None
    if "vix" not in t.columns and Path(a.vix).is_file():
        vix = vix_prev_close(Path(a.vix), t["date"])
    sector_of = sectors(Path(a.universe)) if Path(a.universe).is_file() else None
    print(report(add_groups(t, sector_of, vix), a.equity))


if __name__ == "__main__":
    main()
