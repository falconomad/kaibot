"""`trend_momentum_band` through the ENGINE with SimBroker, judged by gate v3's sleeve test (M11 ticket 2.9, issue
#460; docs/prereg/trend_momentum_band.md with Amendment 1, frozen). Research only.

    python scripts/trend_momentum_band.py --data data --end 2024-12-31                 # the pre-registered run (Mac)
    python scripts/trend_momentum_band.py --fixture --out /tmp/b --trials '' --control-runs 20     # CI, seconds

Each run is one continuous $100k account through `engine.daily.run_daily` at the rule's pre-registered sizing
(`engine.daily.momentum_builder`, Balanced, 100% exposure cap): the rule at 1x, 2x, 3x and 0x costs and the stress
fill, its five neighbours, and `trend_momentum` at 1x for the turnover mark. The exposure-matched random control
(`daily_report.control_for`) has 2,000 runs by default (config/gate_v3.yaml, a family of several rules).
`gate_v3.evaluate_sleeve` judges it under the evidence-based tier; the pre-registration's turnover mark (at most two
thirds of `trend_momentum`'s on the same data) is judged beside it.

Trials: n_trials 1 for the rule and each neighbour (six configurations), 0 for the cost, fill and reference runs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine import daily_report as dr  # noqa: E402
from kaibot.engine.daily import DailyData, daily_returns, run_daily, sessions_window  # noqa: E402
from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import tbill as tbill_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import momentum as mom  # noqa: E402
from kaibot.strategies.momentum_band import TrendMomentumBand  # noqa: E402

RULE = "trend_momentum_band"
LEVEL = "balanced"
COSTS = (1.0, 2.0, 3.0, 0.0)
TURNOVER_MAX = 2.0 / 3.0  # the pre-registration's own mark: at most two thirds of trend_momentum's
CONTROL_SEED = 460
COST_MODEL = "engine_daily_print_v1_impact"  # until #572 (cost model v2 in the daily path) merges


def turnover(run) -> float:
    """Value traded a year over the mean equity (both sides), from the ledger's daily rows."""
    rows = run.book.daily
    traded = sum(float(r["traded"]) for r in rows)
    eq = float(np.mean([float(r["end"]) for r in rows])) if rows else 0.0
    years = len(rows) / 252 if rows else 0.0
    return traded / eq / years if eq > 0 and years > 0 else float("nan")


def series(run) -> dict[str, pd.Series]:
    idx = pd.DatetimeIndex([pd.Timestamp(str(r["date"])) for r in run.book.daily])
    return {"net": pd.Series(daily_returns(run.book), index=idx),
            "exposure": pd.Series([float(r["exposure"]) for r in run.book.daily], index=idx),
            "costs": pd.Series([float(r["cost"]) / float(r["start"]) if r["start"] else 0.0
                                for r in run.book.daily], index=idx)}


def main(argv: list[str] | None = None) -> dict:
    gate = g3.GateV3.load()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--control-runs", type=int, default=None,
                    help="random control runs (default from config/gate_v3.yaml for the rule: 2,000)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default=None)
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    c = gate.cfg["sleeve"]["s3_control"]
    fam = gate.family_of(RULE)  # gate_v3.control_runs_for (#553) does the same; written out for this branch's base
    several = fam is not None and len(gate.families[fam]["rules"]) > 1
    default = int(c.get("random_runs_min_several_rules", c["random_runs_min"]) if several else c["random_runs_min"])
    runs_ctl = default if a.control_runs is None else a.control_runs
    data_dir = Path(a.data)
    trials_mod.guard(RULE)  # #472: registered before any data is read
    ref_sha = trials_mod.guard("trend_momentum")  # the reference run for the turnover mark is registered too
    holdout.enforce(script=__file__, end=a.end, rule=RULE, opening=a.open_holdout, data=data_dir)
    pre = prereg_mod.load(RULE)
    t0 = time.time()
    if a.fixture:
        market, info = dr.load_fixture(dr.FIXTURE)
        universe = "fixture"
    else:
        market, info = dr.load_store(data_dir, Path(a.universe_file), a.universe, a.start, a.end)
        universe = a.universe
    data = DailyData.from_market(market)
    lo, hi = gate.window
    w0 = info.get("window", [None])[0] if a.fixture else str(lo.date())
    i0, i1 = sessions_window(data, w0, min(pd.Timestamp(a.end), hi).date())
    pol = load_profiles()[LEVEL]
    runs: dict[str, tuple] = {}

    def one(label, strat, *, fill="base", cost=1.0, n_trials=0):
        t = time.time()
        r = run_daily(strat, data, pol, i0, i1, fill=fill, cost=cost)
        st = dr.run_stats(r)
        runs[label] = (r, {"label": label, "spec": strat.name, "fill": fill, "cost": cost, "n_trials": n_trials,
                           **st, "turnover": turnover(r), "runtime_s": round(time.time() - t, 1)})
        print(f"  {label}: CAGR {st['cagr_pct']}%, max drawdown {st['max_drawdown_pct']}%, turnover "
              f"{runs[label][1]['turnover']:.2f} a year", flush=True)
        return r

    base = TrendMomentumBand()
    rule_run = None
    for c in COSTS:
        r = one(f"rule|{c:g}x", base.fresh(), cost=c, n_trials=int(c == 1.0))
        rule_run = r if c == 1.0 else rule_run
    one("rule|stress", base.fresh(), fill="stress")
    for n in base.neighbours():
        one(f"neighbour|{n.name.split('~')[1]}", n, n_trials=1)
    one("reference|trend_momentum", mom.VERSIONS["trend_momentum"])
    control = dr.control_for(rule_run, market, "trend_momentum", pol, runs_ctl, CONTROL_SEED, 100_000.0) \
        if runs_ctl > 0 else None
    spy_tr = data.spy_tr[i0:i1 + 1] if data.spy_tr is not None else None
    have_tr = spy_tr is not None and np.isfinite(np.asarray(spy_tr, dtype=float)).all()
    spy_c = data.sig_unfilled_c[:, data.spy]
    s = series(rule_run)
    spy = pd.Series(np.asarray(spy_tr, dtype=float) if have_tr else spy_c[i0:i1 + 1] / spy_c[i0 - 1:i1] - 1,
                    index=s["net"].index)
    uni = "point_in_time" if universe == "pit" else universe

    def account(label, **kw):
        x = series(runs[label][0])
        return g3.Account(x["net"], spy, x["exposure"], x["costs"], universe=uni, **kw)

    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    reg = trials_mod.load(trials_file=trials_path) if trials_path is not None else None
    source = {"engine": "simbroker", "cost_model": COST_MODEL, "data_repairs": universe == "pit",
              "benchmark": "spy_total_return" if have_tr else "spy_price_only"}
    run_at = datetime.now(UTC)
    rep = g3.evaluate_sleeve(gate, rule=RULE, account=account("rule|1x", control=control),
                             neighbours={k.split("|")[1]: account(k) for k in runs if k.startswith("neighbour|")},
                             prereg=pre, source=source, registry=reg, first_run_at=run_at,
                             tbill=tbill_mod.find(data_dir), as_of=run_at)
    mine, ref = runs["rule|1x"][1]["turnover"], runs["reference|trend_momentum"][1]["turnover"]
    turn = {"rule": mine, "trend_momentum": ref, "ratio": mine / ref if ref > 0 else float("nan"),
            "max_ratio": TURNOVER_MAX, "met": bool(ref > 0 and mine <= TURNOVER_MAX * ref)}
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    tag = f"{RULE}_{LEVEL}_{universe}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "rule": RULE,
           "prereg_sha256": pre.sha256, "gate_sha": gate.sha, "holdout": holdout.stamp(None), "universe": universe,
           "data": info, "window": [str(data.dates[i0]), str(data.dates[i1])], "control_runs": runs_ctl,
           "source": source, "rows": [row for _, row in runs.values()], "control": control,
           "turnover_mark": turn, "gate_v3_sleeve": rep, "runtime_s": round(time.time() - t0, 1)}
    out_dir = Path(a.out or data_dir / "reports")
    (out_dir / tag).mkdir(parents=True, exist_ok=True)
    for label, (r, _) in runs.items():
        pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{label.replace('|', '_')}.csv", index=False)
    (out_dir / f"{tag}.json").write_text(json.dumps(out, indent=1, default=str))
    (out_dir / f"{tag}.md").write_text(g3.render_sleeve(rep) + f"\nTurnover: {mine:.2f} a year against "
                                       f"trend_momentum's {ref:.2f} (mark: at most two thirds): "
                                       f"{'holds' if turn['met'] else 'fails its own turnover mark'}.\n")
    if trials_path is not None:
        for label, (_, row) in runs.items():
            trials_mod.append(trials_path, {
                "ts": out["run_at"], "trial_id": f"{tag}_{label}",
                "strategy": RULE if not label.startswith("reference") else "trend_momentum",
                "variant": "engine_simbroker", "label": f"engine {label}", "level": LEVEL, "cost_model": COST_MODEL,
                "universe_mode": universe, "code_sha": code_sha,
                "prereg_sha256": pre.sha256 if not label.startswith("reference") else ref_sha, "gate_sha": gate.sha,
                "n_trials": row["n_trials"], "params": {"spec": row["spec"], "fill": row["fill"],
                                                        "cost_multiple": row["cost"]},
                "window": out["window"],
                "metrics": {"cagr_pct": row["cagr_pct"], "daily_sharpe_ann": row["sharpe"],
                            "max_drawdown_pct": row["max_drawdown_pct"], "turnover": row["turnover"]}})
    print(f"sleeve: {rep['verdict']}; turnover {turn['ratio']:.2f} of trend_momentum's; report {out_dir / tag}.json")
    out["file"] = str(out_dir / f"{tag}.json")
    return out


if __name__ == "__main__":
    main()
