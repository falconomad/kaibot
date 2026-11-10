"""Overfitting report over the trial log (story S08): deflated Sharpe per trial and PBO per window.

Every replay appends a row to data/trials.jsonl and its daily returns to data/trials/<trial_id>.csv.
Trials on the same window (same first and last session) are compared with CSCV to estimate the probability that
choosing the best of them was luck (PBO). The DSR deflates each trial's Sharpe by the number of trials ever run
and the spread of their Sharpes, so running more variants raises the bar for all of them.

python scripts/trial_report.py [--data data] [--blocks 16]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation.metrics import deflated_sharpe, pbo_cscv, sharpe  # noqa: E402


def load_trials(data: Path) -> tuple[list[dict], dict[str, pd.Series]]:
    rows = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines() if x.strip()]
    series = {}
    for r in rows:
        f = data / "trials" / f"{r.get('trial_id', '')}.csv"
        if r.get("trial_id") and f.exists():
            s = pd.read_csv(f, parse_dates=["date"]).set_index("date")["ret"]
            series[r["trial_id"]] = s
    return rows, series


def pick_blocks(t: int, wanted: int) -> int:
    s = wanted
    while s > 2 and t < 2 * s:
        s -= 2
    return s


def report(data: Path, blocks: int = 16) -> dict:
    rows, series = load_trials(data)
    n_trials = len(rows)  # every logged trial counts against the DSR, even without a stored series
    srs = [sharpe(s.to_numpy()) for s in series.values() if len(s) > 2]
    sr_var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    out = {"n_trials_logged": n_trials, "n_series": len(series), "trial_sharpe_var": sr_var, "trials": [],
           "windows": []}
    for r in rows:
        s = series.get(r.get("trial_id"))
        if s is None:
            continue
        x = s.to_numpy()
        out["trials"].append({
            "trial_id": r["trial_id"], "variant": r.get("variant"), "label": r.get("label"),
            "sessions": len(x), "daily_sharpe_ann": round(sharpe(x) * math.sqrt(252), 2),
            "dsr": round(deflated_sharpe(x, n_trials, sr_var), 3),
        })
    by_window: dict[tuple, list[str]] = {}
    for tid, s in series.items():
        by_window.setdefault((str(s.index.min().date()), str(s.index.max().date())), []).append(tid)
    for (lo, hi), tids in sorted(by_window.items()):
        w = {"window": [lo, hi], "trials": tids}
        if len(tids) >= 2:
            m = pd.concat([series[t] for t in tids], axis=1, join="inner").to_numpy()
            s_used = pick_blocks(len(m), blocks)
            if len(m) >= 2 * s_used:
                res = pbo_cscv(m, n_blocks=s_used)
                w.update({"pbo": round(res.pbo, 3), "blocks": s_used, "splits": res.n_splits,
                          "oos_degradation": round(res.oos_degradation, 3)})
        else:
            w["pbo"] = None
            w["note"] = "PBO needs >= 2 trials on the same window"
        out["windows"].append(w)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--blocks", type=int, default=16)
    a = ap.parse_args()
    rep = report(Path(a.data), a.blocks)
    (Path(a.data) / "reports").mkdir(parents=True, exist_ok=True)
    (Path(a.data) / "reports" / "trial_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
