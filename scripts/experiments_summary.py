"""One comparison table for the walk-forward experiments of issue #109 (control + four experiments), and with
`--set multiday` for the four multi-day rules of issue #113 (plus SPY and the equal-weight universe).

python scripts/experiments_summary.py [--data data] [--level balanced]      print the table, write the .md
python scripts/experiments_summary.py --set multiday                        data/reports/experiments_multiday_summary.md
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
from kaibot.replay.multiday_rules import EXPERIMENTS as MULTIDAY  # noqa: E402
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


# ---------------------------------------------------------------- multi-day rules (issue #113)

MD_COLUMNS = ("Experiment", "Trades", "Trades/month", "Avg holding days", "Net return %", "CAGR %", "Gross bp/trade",
              "Net bp/trade", "t-stat", "Hit rate", "Sharpe", "Max drawdown %", "Months positive", "% time invested",
              "Deflated Sharpe")
VS_COLUMNS = ("Experiment", "Net return %", "Minus SPY (points)", "Minus equal-weight universe (points)",
              "CAGR minus SPY", "CAGR minus equal-weight", "Sharpe", "Sharpe minus SPY", "Sharpe minus equal-weight")
COST_COLUMNS = ("Experiment", "Avg position $", "Gross $", "Spread $", "Impact $", "Gap slippage $", "Fees $",
                "Net $", "Gross bp of position", "Costs bp of position", "Stops jumped by the open")
SURVIVORSHIP = ("**Survivorship bias:** the universe is today's 300 large caps applied back to 2016, so every rule "
                "that holds these stocks is flattered by hindsight (they are the survivors and the winners); read "
                "each result against the equal-weight universe row, not against zero.")
PROVISIONAL = ("**Provisional (decision 15):** a first look from the research harness of #113, on today's stock "
               "list and with cost model v1. It is not a verdict and cannot pass Gate B; go/no-go numbers come only "
               "from the engine with `SimBroker`.")
BENCHMARKS = (("spy", "SPY buy-and-hold (benchmark, price only)"),
              ("universe_ew", "Universe equal-weight, rebalanced monthly, after costs (benchmark)"))


def _table(columns: tuple[str, ...], rows: list[dict]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join("n/a" if r.get(c) is None else str(r[c]) for c in columns) + " |" for r in rows]
    return "\n".join(lines)


def md_rows(name: str, rep: dict, reports: Path, n_trials: int) -> tuple[list[dict], list[dict], list[dict]]:
    """(main, versus-benchmarks, costs) rows: tuned each month (the honest result) and the fixed setting."""
    s = rep["summary"]
    main, vs, costs = [], [], []
    for which, tag in (("tuned", "tuned monthly"), ("default", "fixed " + rep["fixed_setting"])):
        m, label = s[which], f"{name} ({tag})"
        dsr = s["dsr"][which]
        daily = reports / rep.get("files", {}).get(which, {}).get("daily", "")
        if daily.is_file():  # the same trial count for every row: everything in the trial log
            d = pd.read_csv(daily)
            dsr = round(deflated_sharpe((d["pnl"] / d["start"]).to_numpy(), n_trials, s["dsr"]["trial_sharpe_var"]), 3)
        if dsr is None or not math.isfinite(dsr) or not m["trades"]:
            dsr = None
        main.append({
            "Experiment": label, "Trades": m["trades"], "Trades/month": m["trades_per_month"],
            "Avg holding days": m["avg_hold_days"], "Net return %": m["net_return_pct"], "CAGR %": m["cagr_pct"],
            "Gross bp/trade": m["mean_bp_gross"], "Net bp/trade": m["mean_bp_net"], "t-stat": m["t_stat_per_trade"],
            "Hit rate": m["hit_rate"], "Sharpe": m["daily_sharpe_ann"], "Max drawdown %": m["max_drawdown_pct"],
            "Months positive": f"{m['months_positive']} of {m['months']}", "% time invested": m["time_invested_pct"],
            "Deflated Sharpe": dsr})
        a, b = m["vs"]["spy"], m["vs"]["universe_ew"]
        vs.append({
            "Experiment": label, "Net return %": m["net_return_pct"], "Minus SPY (points)": a["excess_return_pp"],
            "Minus equal-weight universe (points)": b["excess_return_pp"], "CAGR minus SPY": a["excess_cagr_pp"],
            "CAGR minus equal-weight": b["excess_cagr_pp"], "Sharpe": m["daily_sharpe_ann"],
            "Sharpe minus SPY": a["sharpe_minus_benchmark"], "Sharpe minus equal-weight": b["sharpe_minus_benchmark"]})
        c = m["costs"]
        costs.append({
            "Experiment": label, "Avg position $": c["avg_position_usd"], "Gross $": c["gross_pnl"],
            "Spread $": c["spread"], "Impact $": c["impact"], "Gap slippage $": c["gap_slippage"], "Fees $": c["fees"],
            "Net $": c["net_pnl"], "Gross bp of position": c["bp_of_position_gross"],
            "Costs bp of position": c["bp_of_position_costs"], "Stops jumped by the open": c["gapped_stops"]})
    return main, vs, costs


def build_multiday(data: Path, level: str) -> str:
    reports = data / "reports"
    n_trials = max(logged_trials(data / "trials.jsonl"), 1)
    main, vs, costs, notes, missing = [], [], [], [], []
    window, bench, other_windows = None, None, []
    earnings: dict[str, list[str]] = {}  # what each run said about the earnings blackout -> the rules it holds for
    for name, exp in MULTIDAY.items():
        found = find_report(reports, level, name)
        if found is None or found[1].get("set") != "multiday":
            missing.append(name)
            continue
        f, rep = found
        a, b, c = md_rows(name, rep, reports, n_trials)
        main, vs, costs = main + a, vs + b, costs + c
        s = rep["summary"]
        if window is None:
            window, bench = s["oos_window"], {k: s[k] for k, _ in BENCHMARKS}
        elif s["oos_window"] != window:
            other_windows.append(name)
        earn = rep.get("earnings", {})
        notes.append(f"- `{name}`: {exp.what}. Earnings blackout: {earn.get('mode')}. Report `{f.name}`.")
        earnings.setdefault(f"{earn.get('mode')}: {earn.get('note')}", []).append(f"`{name}`")
    for key, label in BENCHMARKS if bench else ():
        b = bench[key]
        main.append({"Experiment": label, "Net return %": b["return_pct"], "CAGR %": b["cagr_pct"],
                     "Sharpe": b["daily_sharpe_ann"], "Max drawdown %": b["max_drawdown_pct"],
                     "% time invested": 100.0})
    out = ["# Multi-day walk-forward: four momentum rules that are allowed to hold (issue #113)", "", SURVIVORSHIP, "",
           PROVISIONAL, ""]
    if window:
        out += [f"Out-of-sample {window[0]} to {window[1]}; train 12 months, test the next month, roll monthly; "
                "selection by train Sharpe after costs. One continuous $100k account per row: equity and positions "
                "are carried across months, nothing is force-closed at a month end. \"Tuned monthly\" is the "
                "honest result; the second row of each pair is the rule's fixed, pre-declared setting.", ""]
    out += [_table(MD_COLUMNS, main), "",
            "Basis points per trade are of the account's equity on the entry day; gross is before spread, impact, "
            "gap slippage and fees. Holding days are sessions between entry and exit (0 = bought and sold the same "
            "session). % time invested is the share of sessions with a position held at the close. Deflated Sharpe "
            f"uses all {n_trials} configurations in the trial log. The t-stat treats trades as independent; "
            "positions held at the same time are not, so read it as an upper bound.", "",
            "## Against the benchmarks (same sessions)", "", _table(VS_COLUMNS, vs), "",
            "Points are percentage points of total return (and of CAGR per year). A rule that is mostly in cash can "
            "have a decent Sharpe and still make far less than holding the universe: read both columns.", "",
            "## Costs", "", _table(COST_COLUMNS, costs), "",
            "bp of position = per $ put into the trades. `overnight_spy` lives or dies on this table: one night in "
            "SPY earns a few bp gross, and a round trip costs spread and impact twice.", "",
            "## Rules and notes", "",
            "- Known limits of every row: entries fill at the next session's open (the close is the last "
            "regular-hours minute, not the auction); the cost model is the uncalibrated one under review in #111; "
            "prices carry no dividends, for the rules and for both benchmarks; the universe is today's list (first "
            "line)."]
    out += notes
    out += [f"- Earnings blackout, {', '.join(rules)}: {text}." for text, rules in earnings.items()]
    if other_windows:
        out += ["", f"Different out-of-sample window than the benchmark rows: {', '.join(other_windows)} (re-run "
                "them with the same dates)."]
    if missing:
        out += ["", f"Not run yet: {', '.join(missing)}."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--set", default="variant_a", choices=["variant_a", "multiday"],
                    help="which table: the variant A experiments (issue #109) or the multi-day rules (issue #113)")
    ap.add_argument("--has", default="", choices=["", *EXPERIMENTS, *MULTIDAY],
                    help="exit 0 if this experiment has a report")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--universe", help="universe file (compared by name)")
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        uni = Path(a.universe).name if a.universe else None
        sys.exit(0 if find_report(data / "reports", a.level, a.has, a.start, a.end, uni) else 1)
    text = build_multiday(data, a.level) if a.set == "multiday" else build(data, a.level)
    (data / "reports").mkdir(parents=True, exist_ok=True)
    name = "experiments_multiday_summary.md" if a.set == "multiday" else "experiments_summary.md"
    (data / "reports" / name).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
