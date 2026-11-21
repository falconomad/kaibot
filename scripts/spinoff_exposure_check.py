#!/usr/bin/env python3
"""How much of Gate B could the uncredited spin-offs move? (issue #262). Read-only: nothing is written.

    cd ~/Projects/kaibot && .venv/bin/python scripts/spinoff_exposure_check.py

The committed form of the check posted on #262 (16 Nov 2026). It reads `data/reports/*pit*/trades_*.csv` and
`daily_*.csv` (the ten-year point-in-time runs of every rule and label), the daily store's raw closes and
`data/daily/check/adjustment_steps.csv` (from `./scripts/fetch_daily.sh --check`). For every position held across
a spin-off-sized step it estimates the value of the spun-off shares the run never booked:
qty x raw close before x (1 - Alpaca's close factor), an upper bound of the P&L error per run.

Two fixes against the pasted version: a former member's trades carry its store key (`DD@2019-06-03`), so a trade
matches a step by key, or by plain symbol when neither side has a date suffix; and a step with no close before it
is reported instead of adding NaN to the credit.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def spin_steps(steps: pd.DataFrame) -> pd.DataFrame:
    """The spin-off-sized steps: class spin_off, or an Alpaca close factor below 0.95 that is not a dropped
    duplicate; factors above 1 (Alpaca's mis-coded reverse splits, #262) are left out."""
    ev = steps[(steps["class"] == "spin_off")
               | ((steps["alpaca_factor"] < 0.95) & (steps["class"] != "dropped_duplicate"))]
    ev = ev[ev["alpaca_factor"] < 1.0].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    return ev


def held_across(trades: pd.DataFrame, key: str, day: pd.Timestamp) -> pd.DataFrame:
    sym = key.split("@")[0]
    match = (trades["symbol"] == key) | ((trades["symbol"] == sym) & ("@" not in key))
    return trades[match & (trades["date"] < day) & (trades["exit_date"] >= day)]


def check_run(trades: pd.DataFrame, mean_equity: float, ev: pd.DataFrame, prev_close) -> dict:
    credit, hits, no_close = 0.0, [], []
    for e in ev.itertuples():
        for x in held_across(trades, e.key, e.date).itertuples():
            px = prev_close(e.key, e.date)
            if not np.isfinite(px):
                no_close.append(f"{e.key} {e.date.date()}")
                continue
            c = float(x.qty) * px * (1.0 - float(e.alpaca_factor))
            credit += c
            hits.append(f"{e.key} {e.date.date()} qty {x.qty:g} credit {c:,.0f}")
    return {"hits": len(hits), "missing_credit": round(credit),
            "pct_of_mean_equity": round(100 * credit / mean_equity, 2) if np.isfinite(mean_equity) else None,
            "which": "; ".join(hits[:6]), "no_close": "; ".join(no_close)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    a = ap.parse_args(argv)
    from kaibot.ingest.daily_store import DailyStore

    steps_file = a.data / "daily" / "check" / "adjustment_steps.csv"
    if not steps_file.exists():
        sys.exit(f"no {steps_file}: run ./scripts/fetch_daily.sh --check first")
    store = DailyStore(a.data / "daily")
    ev = spin_steps(pd.read_csv(steps_file))

    def prev_close(key: str, d: pd.Timestamp) -> float:
        b = store.bars(key, end=(d - pd.Timedelta(days=1)).date())
        return float(b["c"].iloc[-1]) if len(b) else float("nan")

    rows = []
    for f in sorted(glob.glob(str(a.data / "reports" / "*pit*" / "trades_*.csv"))):
        t = pd.read_csv(f, parse_dates=["date", "exit_date"])
        dfile = Path(f).with_name(Path(f).name.replace("trades_", "daily_", 1))
        eq = float(pd.read_csv(dfile)["start"].mean()) if dfile.exists() else float("nan")
        rows.append({"run": f.split("/reports/")[1], **check_run(t, eq, ev, prev_close)})
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_colwidth", 120)
    print(out.to_string(index=False) if len(out) else "no data/reports/*pit*/trades_*.csv found")
    print(f"\n{len(ev)} spin-off-sized steps. missing_credit: the value of the spun-off shares the run never booked "
          "(qty x raw close before x (1 - Alpaca's close factor)). Upper bound of the P&L error per run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
