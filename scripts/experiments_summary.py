"""One comparison table for the walk-forward experiments of issue #109 (control + four experiments).

python scripts/experiments_summary.py [--data data] [--level balanced]      print the table, write the .md
python scripts/experiments_summary.py --has mornings --start ... --end ... --universe ...   exit 0 if finished

Reads the newest data/reports/walkforward_<level>_*.json of each experiment (written by
scripts/walk_forward.py --experiment <name>). No network. The deflated Sharpe is recomputed for every row with the
same trial count, the whole trial log (data/trials.jsonl), so the order the experiments ran in does not matter.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from walk_forward import logged_trials  # noqa: E402

from kaibot.evaluation.metrics import deflated_sharpe  # noqa: E402
from kaibot.replay.walkforward import EXPERIMENTS  # noqa: E402

COLUMNS = ("Experiment", "Trades", "Trades/month", "Net return %", "Mean bp gross", "Mean bp net", "t-stat",
           "Hit rate", "Sharpe", "Max drawdown %", "Months positive", "Deflated Sharpe")


def find_report(reports: Path, level: str, name: str, start: str | None = None, end: str | None = None,
                universe: str | None = None) -> tuple[Path, dict] | None:
    """Newest finished report of experiment `name` (and, when given, of the same window and universe)."""
    for f in sorted(reports.glob(f"walkforward_{level}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("experiment") != name or "summary" not in rep:
            continue
        if any(want is not None and rep.get(k) != want
               for k, want in (("start", start), ("end", end), ("universe", universe))):
            continue
        return f, rep
    return None


def fixed_setting(params: dict, axes) -> str:
    """The experiment's pre-declared setting in words, e.g. `cutoff 10:30, stop 3 ATR`."""
    words = {"entry_cutoff_m": lambda v: f"cutoff {(570 + int(v)) // 60}:{(570 + int(v)) % 60:02d}",
             "atr_mult": lambda v: f"stop {v:g} ATR", "vix_min": lambda v: f"VIX > {v:g}"}
    return ", ".join(words.get(k, lambda v, k=k: f"{k} {v:g}")(params[k]) for k in axes)


def rows_of(name: str, rep: dict, reports: Path, n_trials: int) -> list[dict]:
    """Two rows per experiment: tuned each month (the honest out-of-sample result) and its fixed default."""
    s = rep["summary"]
    fixed = "A defaults" if name == "control" else "fixed " + fixed_setting(rep["default_params"], rep["grid_axes"])
    out = []
    for which, tag in (("tuned", "tuned monthly"), ("default", fixed)):
        m = s[which]
        dsr = s["dsr"][which]
        daily = reports / rep.get("files", {}).get(which, {}).get("daily", "")
        if daily.is_file():  # same trial count for every row: everything in the trial log
            d = pd.read_csv(daily)
            dsr = round(deflated_sharpe((d["pnl"] / d["start"]).to_numpy(), n_trials, s["dsr"]["trial_sharpe_var"]), 3)
        if dsr is None or not math.isfinite(dsr):  # no trades: nothing to deflate
            dsr = None
        costs = m.get("costs", {})
        out.append({
            "Experiment": f"{name} ({tag})", "Trades": m["trades"], "Trades/month": m["trades_per_month"],
            "Net return %": m["net_return_pct"], "Mean bp gross": costs.get("mean_bp_gross"),
            "Mean bp net": m["mean_bp_per_trade"], "t-stat": m["t_stat_per_trade"], "Hit rate": m["hit_rate"],
            "Sharpe": m["daily_sharpe_ann"], "Max drawdown %": m["max_drawdown_pct"],
            "Months positive": f"{m.get('months_positive')} of {m.get('months')}", "Deflated Sharpe": dsr})
    return out


def table(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    lines += ["| " + " | ".join("n/a" if r[c] is None else str(r[c]) for c in COLUMNS) + " |" for r in rows]
    return "\n".join(lines)


def build(data: Path, level: str) -> str:
    reports = data / "reports"
    n_trials = max(logged_trials(data / "trials.jsonl"), 1)
    rows, notes, missing, window = [], [], [], None
    for name, exp in EXPERIMENTS.items():
        found = find_report(reports, level, name)
        if found is None:
            missing.append(name)
            continue
        f, rep = found
        rows += rows_of(name, rep, reports, n_trials)
        window = window or rep["summary"]["oos_window"]
        t = rep["summary"]["tuned"]
        notes.append(f"- `{name}`: {exp.what}. {t.get('months_with_trades')} of {t.get('months')} months traded. "
                     f"Report `{f.name}`.")
    out = ["# Walk-forward experiments for variant A (issue #109)", ""]
    if window:
        out += [f"Out-of-sample {window[0]} to {window[1]}; train 12 months, test the next month, roll monthly; "
                "selection by train Sharpe after costs. \"Tuned monthly\" is the honest result; the second row of "
                "each pair is the experiment's fixed, pre-declared setting.", ""]
    out += [table(rows), "",
            f"Mean bp are basis points of the $ equity per trade; gross is before spread, impact, slippage past "
            f"stops and fees. Deflated Sharpe uses all {n_trials} configurations in the trial log.", ""]
    out += notes
    if missing:
        out += ["", f"Not run yet: {', '.join(missing)}."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--has", default="", choices=["", *EXPERIMENTS], help="exit 0 if this experiment has a report")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--universe", help="universe file (compared by name)")
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        uni = Path(a.universe).name if a.universe else None
        sys.exit(0 if find_report(data / "reports", a.level, a.has, a.start, a.end, uni) else 1)
    text = build(data, a.level)
    (data / "reports").mkdir(parents=True, exist_ok=True)
    (data / "reports" / "experiments_summary.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
