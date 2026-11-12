"""Gate B report for one multi-day rule from the files a walk-forward writes (issue #126, decision 15 section 7).

python scripts/gate_v2_report.py --rule trend_highs --daily data/reports/daily_balanced_md_trend_highs_<ts>_default.csv
    --spy spy_daily.csv [--trades data/reports/trades_balanced_md_trend_highs_<ts>_default.csv]
    [--random control.json] [--universe today|point_in_time] [--pit-daily daily.csv [--pit-trades trades.csv]
    [--pit-random control.json]] [--neighbour NAME=daily.csv ...]
    [--engine research_harness|simbroker] [--cost-model v1|v2] [--data-repairs] [--benchmark spy_price_only|
    spy_total_return] [--data data] [--out data/reports]

Reports only: it reads files and writes data/reports/gate_b_<rule>_<ts>.{json,md}. Nothing here trades.

Inputs
- --daily: one row per session with `date` and either `ret` or `pnl` and `start` (the account's daily CSV).
  Optional columns: `cost` ($ of costs charged that session), `traded` ($ bought plus sold), `exposure` (share of
  equity invested at the close).
- --spy: `date` and `ret` (SPY total return per session), or `date` and `close` (the first row is then only
  used as the base). It must cover every session of --daily.
- --trades: used only when --daily has no `cost` column. Each trade's spread and impact are split half on its
  entry and half on its exit session and its fees are put on the exit session: an APPROXIMATION of when the cost
  was charged (the totals are exact). Gap slippage is a price outcome, not a modelled cost, and is not scaled.
- --random: the JSON of a matched random-entry control (kaibot.evaluation.random_control.ControlResult.to_dict()).

The defaults describe the research harness of #113 (cost model v1, today's list, price-only SPY): such a run is
shown as provisional and cannot pass. The report records the sha256 of config/gate_v2.yaml and of the rule's
pre-registration, and deflates the Sharpe by the configurations in the trial registry (data/trials.jsonl).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import gate_v2, prereg, trials  # noqa: E402

COST_COLUMNS = ("cost_spread", "cost_impact")


def read_daily(path: str | Path) -> pd.DataFrame:
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    if "ret" not in d.columns:
        if not {"pnl", "start"} <= set(d.columns):
            sys.exit(f"{path}: needs a `ret` column, or `pnl` and `start`")
        d["ret"] = d["pnl"] / d["start"]
    return d


def read_spy(path: str | Path) -> pd.Series:
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    if "ret" in d.columns:
        return d["ret"].astype(float)
    if "close" in d.columns:
        return d["close"].astype(float).pct_change().dropna()
    sys.exit(f"{path}: needs a `ret` or a `close` column")


def costs_from_trades(trades: pd.DataFrame, daily: pd.DataFrame) -> pd.Series:
    """Daily costs as a share of the session's starting equity, approximated from a trade table (see the top)."""
    if "start" not in daily.columns:
        sys.exit("costs from --trades need the `start` (equity) column of --daily")
    usd = pd.Series(0.0, index=daily.index)
    entry = pd.to_datetime(trades["date"]).dt.normalize()
    exit_ = pd.to_datetime(trades["exit_date"]).dt.normalize()
    half = trades[list(COST_COLUMNS)].sum(axis=1) / 2.0
    for when, amount in ((entry, half), (exit_, half + trades["cost_fees"])):
        usd = usd.add(amount.groupby(when.to_numpy()).sum().reindex(daily.index, fill_value=0.0), fill_value=0.0)
    return usd / daily["start"]


def make_run(daily_f: str, spy: pd.Series, trades_f: str | None, random_f: str | None, universe: str):
    d = read_daily(daily_f)
    costs, approx = None, False
    if "cost" in d.columns:
        if "start" not in d.columns:
            sys.exit(f"{daily_f}: a `cost` column in $ needs the `start` (equity) column beside it")
        costs = d["cost"] / d["start"]
    elif trades_f:
        costs, approx = costs_from_trades(pd.read_csv(trades_f), d), True
    random = json.loads(Path(random_f).read_text()) if random_f else None
    traded = d["traded"] / d["start"] if {"traded", "start"} <= set(d.columns) else None
    exposure = d["exposure"] if "exposure" in d.columns else None
    return gate_v2.Run(d["ret"], spy, costs, random, universe, traded, exposure), approx


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rule", required=True)
    ap.add_argument("--daily", required=True)
    ap.add_argument("--spy", required=True)
    ap.add_argument("--trades")
    ap.add_argument("--random")
    ap.add_argument("--universe", default="today", choices=["today", "point_in_time"])
    ap.add_argument("--pit-daily")
    ap.add_argument("--pit-trades")
    ap.add_argument("--pit-random")
    ap.add_argument("--neighbour", action="append", default=[], metavar="NAME=daily.csv")
    ap.add_argument("--engine", default="research_harness", choices=["research_harness", "simbroker"])
    ap.add_argument("--cost-model", default="v1", choices=["v1", "v2"])
    ap.add_argument("--data-repairs", action="store_true", help="S83-S85 are in the data this run used")
    ap.add_argument("--benchmark", default="spy_price_only", choices=["spy_price_only", "spy_total_return"])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default="")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # tests: another repository root
    a = ap.parse_args(argv)
    root = Path(a.root)
    gate = gate_v2.GateV2.load(root)
    spy = read_spy(a.spy)
    run, approx = make_run(a.daily, spy, a.trades, a.random, a.universe)
    pit = make_run(a.pit_daily, spy, a.pit_trades, a.pit_random, "point_in_time")[0] if a.pit_daily else None
    neighbours = {}
    for item in a.neighbour:
        name, _, path = item.partition("=")
        neighbours[name] = read_daily(path)["ret"]
    try:
        reg = prereg.load(a.rule, root)
    except prereg.PreregError as e:
        print(f"pre-registration: {e}", file=sys.stderr)
        reg = None
    registry = trials.load(None, Path(a.data) / "trials.jsonl")
    stamps = sorted(str(r["ts"]) for r in registry.rows if r.get("strategy") == a.rule and r.get("ts"))
    first = None
    if stamps:
        try:
            first = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
        except ValueError:
            first = None
    source = {"engine": a.engine, "cost_model": a.cost_model, "data_repairs": bool(a.data_repairs),
              "benchmark": a.benchmark, "cost_series": "approximated from the trade table" if approx else
              ("daily" if run.costs is not None else "none")}
    rep = gate_v2.evaluate_gate_b(gate, rule=a.rule, run=run, pit_run=pit, neighbours=neighbours, prereg=reg,
                                  source=source, registry=registry, first_run_at=first)
    rep["inputs"] = {"daily": str(a.daily), "spy": str(a.spy), "trades": a.trades, "random": a.random}
    text = gate_v2.render_gate_b(rep)
    out = Path(a.out or Path(a.data) / "reports")
    out.mkdir(parents=True, exist_ok=True)
    tag = f"gate_b_{a.rule}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1))
    (out / f"{tag}.md").write_text(text)
    print(text)
    return rep


if __name__ == "__main__":
    main()
