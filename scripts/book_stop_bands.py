"""The band table of decision 17 for one book at one level (M11 ticket 2.10, issue #461). Research only: reads one
daily CSV of an engine + SimBroker run, writes one JSON table; never touches a broker or the engine.

    python scripts/book_stop_bands.py --daily <report folder>/daily_base_1x.csv --rule index_core \\
        --level aggressive --report <report>.json --out docs/research/results/book_bands/index_core_aggressive.json

The CSV is a run's daily ledger rows (`date`, `start`, `end`; every daily CSV the engine scripts write). Its returns
are cut to the research window of config/gate_v3.yaml (2017-05-01 to 2024-12-31): a session in the hold-out is
dropped and counted, never resampled. The bootstrap is the one of config/book_stop.yaml (stationary, mean block 20,
2,000 paths, seed 17); the table holds the 95% and 99% quantiles of the maximum drawdown at each horizon of
`risk.book_stop.HORIZONS` (and the window's own length). The file records the input's sha256, the report it came
from, the window and the settings, so a table can be traced to its run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import holdout, mtm  # noqa: E402
from kaibot.risk import book_stop as bs  # noqa: E402


def returns_of(daily: pd.DataFrame) -> pd.Series:
    d = pd.to_datetime(daily["date"])
    r = daily["end"].astype(float) / daily["start"].astype(float) - 1.0
    return pd.Series(r.to_numpy(), index=pd.DatetimeIndex(d))


def build(returns: pd.Series, rule: str, level: str, cfg: bs.BookStopConfig, source: dict | None = None,
          horizons=bs.HORIZONS) -> tuple[bs.BandTable, dict]:
    gate = g3.GateV3.load()
    cut, before, after = gate.cut(returns, "book returns")
    if cut.size < 60:
        raise SystemExit(f"{cut.size} sessions on the research window: too few to resample")
    b = cfg.bootstrap
    hs = sorted({h for h in horizons if h <= cut.size} | {int(cut.size)})
    q95, q99 = [], []
    for h in hs:
        dd = mtm.bootstrap_max_drawdowns(cut.to_numpy(), horizon=h, runs=int(b["runs"]),
                                         mean_block=float(b["mean_block_sessions"]), seed=int(b["seed"]))
        q95.append(float(np.quantile(dd, cfg.alert_quantile)))
        q99.append(float(np.quantile(dd, cfg.retire_quantile)))
    # a seeded bootstrap per horizon can wiggle by a hair between neighbours: the line is made non-decreasing
    q99 = list(np.maximum.accumulate(q99))
    q95 = [min(a, c) for a, c in zip(np.maximum.accumulate(q95), q99, strict=True)]
    src = {**(source or {}), "window": [str(cut.index[0].date()), str(cut.index[-1].date())],
           "sessions": int(cut.size), "sessions_in_holdout_dropped": after, "sessions_before_window_dropped": before,
           "bootstrap": dict(b), "quantiles": [cfg.alert_quantile, cfg.retire_quantile],
           "decision": "docs/decisions/17_book_stop.md"}
    t = bs.BandTable(rule, level, tuple(hs), tuple(q95), tuple(q99), src)
    return t, src


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--daily", required=True, help="a run's daily CSV (date, start, end)")
    ap.add_argument("--rule", required=True)
    ap.add_argument("--level", required=True, choices=("conservative", "balanced", "aggressive"))
    ap.add_argument("--report", default=None, help="the run's report JSON (its sha256 is recorded)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="data", help="where the hold-out access log lives")
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    gate = g3.GateV3.load()
    holdout.enforce(script=__file__, end=str(gate.window[1].date()), rule=a.rule, opening=a.open_holdout,
                    data=a.data)
    raw = Path(a.daily).read_bytes()
    src = {"daily_csv": Path(a.daily).name, "daily_sha256": hashlib.sha256(raw).hexdigest()}
    if a.report:
        src.update(report=Path(a.report).name, report_sha256=hashlib.sha256(Path(a.report).read_bytes()).hexdigest())
    table, _ = build(returns_of(pd.read_csv(a.daily)), a.rule, a.level, bs.load(), src)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table.to_dict(), indent=1) + "\n")
    print(f"{a.rule} at {a.level}: 99% line {table.q99[0]:.1%} at {table.horizons[0]} sessions to "
          f"{table.q99[-1]:.1%} at {table.horizons[-1]}; wrote {out}")
    return table.to_dict()


if __name__ == "__main__":
    main()
