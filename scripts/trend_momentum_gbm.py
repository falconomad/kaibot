"""`trend_momentum_gbm` through the ENGINE with SimBroker, judged by gate v3's full sleeve test and its own mark
against `trend_momentum` (M12 ticket 3.12, issue #474; docs/prereg/trend_momentum_gbm.md with Amendment 1, frozen).
Research only; needs the `ml` extra (requirements-ml.txt: scikit-learn), which the engine image never installs.

    python scripts/trend_momentum_gbm.py --data data --end 2024-12-31                   # the pre-registered run (Mac)
    python scripts/trend_momentum_gbm.py --fixture --out /tmp/g --trials '' --control-runs 20   # CI, seconds

Order of checks, before any data is read: the rule's pre-registration (`trials.guard`, #472), then the hold-out
(`holdout.enforce`: an --end on or after 2025-01-01 is refused). Then one continuous $100k account per run through
`engine.daily.run_daily` at the rule's pre-registered sizing (Balanced, 100% exposure cap): the rule at 1x, 2x, 3x
and 0x costs and the stress fill, its five neighbours, and `trend_momentum` itself at 1x for the `vs_rule` mark.
The random control (default 2,000 runs, config/gate_v3.yaml) draws from the rule's own candidate pool: the names
`trend_momentum` could buy for that fill (its top 20%, buyable, market filter on), so S3 asks whether the model's
ORDER beats random order among the same names. `--cost-model` (default v1) is passed to the runner once it takes
one (#572); v2 is refused until then.

Judged: `gate_v3.evaluate_sleeve` (full tier: S1 to S6), the `vs_rule` mark (the annualised mean of the daily return
difference to `trend_momentum` above zero), and the leak rule: a Sharpe above 1.0 makes the report
`suspect: data leak` and no verdict is given.

Trials: n_trials 1 for the rule and each neighbour (six configurations); 0 for the cost, fill and reference runs.
Every run of this script on real data writes them (the pre-registration: no tuning, every run counts).
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
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
from kaibot.strategies.momentum_gbm import TrendMomentumGbm  # noqa: E402

RULE = "trend_momentum_gbm"
LEVEL = "balanced"
COSTS = (1.0, 2.0, 3.0, 0.0)
CONTROL_SEED = 474
LEAK_SHARPE = 1.0


def series(run) -> dict[str, pd.Series]:
    idx = pd.DatetimeIndex([pd.Timestamp(str(r["date"])) for r in run.book.daily])
    return {"net": pd.Series(daily_returns(run.book), index=idx),
            "exposure": pd.Series([float(r["exposure"]) for r in run.book.daily], index=idx),
            "costs": pd.Series([float(r["cost"]) / float(r["start"]) if r["start"] else 0.0
                                for r in run.book.daily], index=idx)}


def candidate_pool(data: DailyData):
    """The columns `trend_momentum` could buy for a fill on session e: its top 20% at the close of e - 1, not
    blocked by its trend or 15% conditions, and nothing while its market filter is off (rows <= e - 1 only)."""
    base = mom.VERSIONS["trend_momentum"]
    cache: dict[int, np.ndarray] = {}

    def pool(e: int) -> np.ndarray:
        if e not in cache:
            v = data.view(e - 1)
            if not mom.market_on(v.close(mom.SMA_MARKET)[:, v.spy]):
                cache[e] = np.array([], dtype=int)
            else:
                score, ok, sell_if = base.signals(v)
                order = mom.ranking(score, ok)
                top = order[:math.ceil(base.enter_top * len(order))]
                cache[e] = np.array([j for j in top.tolist() if sell_if(j) is None], dtype=int)
        return cache[e]

    return pool


def judge(gate_verdict: str, vs_rule_met: bool, sharpe: float) -> tuple[bool, str]:
    """(leak, verdict): a Sharpe above 1.0 is a data leak and no verdict is given (decision 15 section 7); a gate
    pass that does not beat `trend_momentum` (the `vs_rule` mark) is a fail."""
    leak = bool(math.isfinite(sharpe) and sharpe > LEAK_SHARPE)
    if leak:
        return True, "suspect: data leak"
    return False, gate_verdict if vs_rule_met or gate_verdict != "pass" else "fail"


def runner_kwargs(cost_model: str) -> dict:
    """`cost_model` for run_daily once it takes one (#572); v1 means the runner's default either way."""
    if "cost_model" in inspect.signature(run_daily).parameters:
        return {"cost_model": cost_model}
    if cost_model != "v1":
        raise SystemExit("--cost-model v2 needs the daily runner of #572 (cost model v2 in the engine's daily path)")
    return {}


def main(argv: list[str] | None = None) -> dict:
    gate = g3.GateV3.load()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--cost-model", default="v1", choices=("v1", "v2"))
    ap.add_argument("--control-runs", type=int, default=None,
                    help="random control runs (default from config/gate_v3.yaml for the rule: 2,000)")
    ap.add_argument("--min-decision-days", type=int, default=None, help=argparse.SUPPRESS)  # fixture tests only
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default=None)
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data_dir = Path(a.data)
    pre_sha = trials_mod.guard(RULE)  # #472: registered before any data is read
    ref_sha = trials_mod.guard("trend_momentum")  # the vs_rule reference run is registered too
    holdout.enforce(script=__file__, end=a.end, rule=RULE, opening=a.open_holdout, data=data_dir)
    if a.min_decision_days is not None and not a.fixture:
        raise SystemExit("--min-decision-days is for the committed fixture only: the pre-registration fixes 52")
    kw = runner_kwargs(a.cost_model)
    runs_ctl = g3.control_runs_for(gate, RULE) if a.control_runs is None else a.control_runs
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
    base = TrendMomentumGbm() if a.min_decision_days is None else TrendMomentumGbm(
        min_decision_days=a.min_decision_days)
    runs: dict[str, tuple] = {}

    def one(label, strat, *, fill="base", cost=1.0, n_trials=0):
        t = time.time()
        r = run_daily(strat, data, pol, i0, i1, fill=fill, cost=cost, **kw)
        st = dr.run_stats(r)
        runs[label] = (r, {"label": label, "spec": strat.name, "fill": fill, "cost": cost, "n_trials": n_trials,
                           "refits": list(getattr(strat, "refits", [])),
                           "order_days": dict(getattr(strat, "order_days", {})), **st,
                           "runtime_s": round(time.time() - t, 1)})
        print(f"  {label}: CAGR {st['cagr_pct']}%, Sharpe {st['sharpe']}, max drawdown {st['max_drawdown_pct']}%",
              flush=True)
        return r

    rule_run = None
    for c in COSTS:
        r = one(f"rule|{c:g}x", base.fresh(), cost=c, n_trials=int(c == 1.0))
        rule_run = r if c == 1.0 else rule_run
    one("rule|stress", base.fresh(), fill="stress")
    for n in base.neighbours():
        one(f"neighbour|{n.name.split('~')[1]}", n, n_trials=1)
    one("reference|trend_momentum", mom.VERSIONS["trend_momentum"])
    control = None
    if runs_ctl > 0:
        from types import SimpleNamespace

        from kaibot.replay import multiday_core as mc

        rule = SimpleNamespace(opened=rule_run.opened, resized=rule_run.resized)
        control = mc.exposure_matched_control(market, pol, mc.VERSIONS["trend_momentum"], rule_run.book, rule,
                                              rule_run.i0, rule_run.i1, runs=runs_ctl, seed=CONTROL_SEED,
                                              equity0=100_000.0, pool=candidate_pool(data))
    spy_tr = data.spy_tr[i0:i1 + 1] if data.spy_tr is not None else None
    have_tr = spy_tr is not None and np.isfinite(np.asarray(spy_tr, dtype=float)).all()
    spy_c = data.sig_unfilled_c[:, data.spy]
    s = series(rule_run)
    spy = pd.Series(np.asarray(spy_tr, dtype=float) if have_tr else spy_c[i0:i1 + 1] / spy_c[i0 - 1:i1] - 1,
                    index=s["net"].index)
    uni = "point_in_time" if universe == "pit" else universe

    def account(label, **kw2):
        x = series(runs[label][0])
        return g3.Account(x["net"], spy, x["exposure"], x["costs"], universe=uni, **kw2)

    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    reg = trials_mod.load(trials_file=trials_path) if trials_path is not None else None
    source = {"engine": "simbroker", "cost_model": a.cost_model, "data_repairs": universe == "pit",
              "benchmark": "spy_total_return" if have_tr else "spy_price_only"}
    run_at = datetime.now(UTC)
    rep = g3.evaluate_sleeve(gate, rule=RULE, account=account("rule|1x", control=control),
                             neighbours={k.split("|")[1]: account(k) for k in runs if k.startswith("neighbour|")},
                             prereg=pre, source=source, registry=reg, first_run_at=run_at,
                             tbill=tbill_mod.find(data_dir), as_of=run_at)
    diff = series(rule_run)["net"] - series(runs["reference|trend_momentum"][0])["net"]
    vs_rule = {"annualised": float(diff.mean() * 252), "met": bool(diff.mean() > 0)}
    sharpe = float(runs["rule|1x"][1]["sharpe"])
    leak, verdict = judge(rep["verdict"], vs_rule["met"], sharpe)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    tag = f"{RULE}_{LEVEL}_{universe}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "rule": RULE,
           "prereg_sha256": pre.sha256, "gate_sha": gate.sha, "holdout": holdout.stamp(None), "universe": universe,
           "data": info, "window": [str(data.dates[i0]), str(data.dates[i1])], "control_runs": runs_ctl,
           "cost_model": a.cost_model, "source": source, "rows": [row for _, row in runs.values()],
           "control": control, "vs_rule": vs_rule, "leak_check": {"sharpe": sharpe, "above": LEAK_SHARPE,
                                                                  "suspect": leak},
           "verdict": verdict, "gate_v3_sleeve": rep, "runtime_s": round(time.time() - t0, 1)}
    out_dir = Path(a.out or data_dir / "reports")
    (out_dir / tag).mkdir(parents=True, exist_ok=True)
    for label, (r, _) in runs.items():
        pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{label.replace('|', '_')}.csv", index=False)
    (out_dir / f"{tag}.json").write_text(json.dumps(out, indent=1, default=str))
    (out_dir / f"{tag}.md").write_text(g3.render_sleeve(rep) + f"\nAgainst trend_momentum: {vs_rule['annualised']:.2%}"
                                       f" a year ({'holds' if vs_rule['met'] else 'fails'}). Verdict: {verdict}.\n")
    if trials_path is not None:
        for label, (_, row) in runs.items():
            ref = label.startswith("reference")
            trials_mod.append(trials_path, json.loads(json.dumps({
                "ts": out["run_at"], "trial_id": f"{tag}_{label}", "strategy": "trend_momentum" if ref else RULE,
                "variant": "engine_simbroker", "label": f"engine {label}", "level": LEVEL,
                "cost_model": a.cost_model, "universe_mode": universe, "code_sha": code_sha,
                "prereg_sha256": ref_sha if ref else pre_sha, "gate_sha": gate.sha, "n_trials": row["n_trials"],
                "params": {"spec": row["spec"], "fill": row["fill"], "cost_multiple": row["cost"]},
                "window": out["window"],
                "metrics": {"cagr_pct": row["cagr_pct"], "daily_sharpe_ann": row["sharpe"],
                            "max_drawdown_pct": row["max_drawdown_pct"]}}, default=str)))
    print(f"verdict: {verdict}; vs trend_momentum {vs_rule['annualised']:.2%} a year; report {out_dir / tag}.json")
    out["file"] = str(out_dir / f"{tag}.json")
    return out


if __name__ == "__main__":
    main()
