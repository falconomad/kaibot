"""Replay SPY last-30-minute momentum (S13) on stored 1-minute bars and log the trial.

python scripts/run_spy_momentum_replay.py --start 2017-01-01 --end 2026-06-30 --level balanced [--label design]
Needs data/bars_1m/symbol=SPY (scripts/fetch_history.sh). Writes data/reports/ and appends data/trials.jsonl,
plus data/trials/<trial_id>.csv (daily returns, for the DSR/PBO report).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation.metrics import max_drawdown, sharpe, t_stat  # noqa: E402
from kaibot.replay.data import load_symbol  # noqa: E402
from kaibot.replay.spy_momentum import run  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--level", default="balanced", choices=["balanced", "aggressive"])
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    data = Path(a.data)
    start, end = np.datetime64(a.start, "D"), np.datetime64(a.end, "D")
    spy = load_symbol(data, "SPY")  # SPY alone fits in memory for 10 years (~2,700 x 390 x 6 floats)
    if spy is None:
        raise SystemExit("no SPY bars: run scripts/fetch_history.sh first")
    trades, daily = run(spy, start, end, load_profiles()[a.level], a.equity)
    d = pd.DataFrame(daily)
    t = pd.DataFrame(trades)
    ret = (d["pnl"] / d["start"]).to_numpy()
    spy_close = pd.Series(spy.c[:, -1], index=spy.dates)
    spy_close = spy_close[(spy_close.index >= start) & (spy_close.index <= end)]
    per_trade = (t["pnl"] / a.equity).to_numpy() if len(t) else np.array([])
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                   cwd=ROOT).stdout.strip(),
        "strategy": "spy_late_momentum", "level": a.level, "start": a.start, "end": a.end, "label": a.label,
        "sessions": len(d), "trades": len(t),
        "net_return_pct": round(float(d["end"].iloc[-1] / a.equity - 1) * 100, 3) if len(d) else 0.0,
        "t_stat_per_trade": round(t_stat(per_trade), 2) if len(t) > 1 else None,
        "hit_rate": round(float((t["pnl"] > 0).mean()), 3) if len(t) else None,
        "exit_reasons": t["reason"].value_counts().to_dict() if len(t) else {},
        "daily_sharpe_ann": round(sharpe(ret) * np.sqrt(252), 2),
        "max_drawdown_pct": round(max_drawdown(np.r_[a.equity, d["end"].to_numpy()]) * 100, 2),
        "spy_return_pct": round(float(spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 2),
        "caveats": ["MOC exit priced at the last 1-min close minus half-spread",
                    "no event blackout applied (FOMC 14:00 +-30 min ends before 15:30)"],
    }
    tag = f"spymom_{a.level}_{a.label + '_' if a.label else ''}{datetime.now(UTC):%Y%m%dT%H%M%S}"
    (data / "reports").mkdir(parents=True, exist_ok=True)
    (data / "reports" / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    t.to_csv(data / "reports" / f"{tag}_trades.csv", index=False)
    (data / "trials").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": d["date"].astype(str), "ret": ret}).to_csv(data / "trials" / f"{tag}.csv", index=False)
    with (data / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": rep["run_at"], "trial_id": tag, "strategy": "spy_late_momentum",
                             "level": a.level, "label": a.label, "code_sha": rep["code_sha"],
                             "params": "pre-registered z>0.5, 15:30 entry, MOC exit, 3xATR5 stop",
                             "window": [a.start, a.end],
                             "metrics": {k: rep[k] for k in ("trades", "net_return_pct", "t_stat_per_trade",
                                                             "daily_sharpe_ann")}}) + "\n")
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
