"""The index core through the ENGINE with SimBroker, judged by gate v3's book test (M11 ticket 2.7, issue #458;
docs/prereg/index_core.md, frozen; decision 16 section 5). Research only: reads data/, never touches a broker
account, writes only under --out and --trials.

    python scripts/index_core.py --data data --end 2024-12-31            # the pre-registered run (the Mac)
    python scripts/index_core.py --fixture --out /tmp/ic --trials ''     # the committed fixture (seconds, CI)

What runs (each ONE continuous $100k account through `engine.daily.run_daily`, `SimBroker` near-close orders
filled at the closing print, cost model v2 (`broker.sim.AuctionCostsV2`, prereg Amendment 2), the ledger booking
SPY's dividends):

- the judged configuration: `IndexCore()` at Hunter (27% index cap, #600; 100% exposure cap), at 1x, 2x, 3x and 0x
  costs (0x for the break-even), and the stress fill (the opening print at twice the cost);
- the five pre-declared neighbours at Hunter, 1x (mark B5);
- Balanced (27% / 60%) and Careful (27% / 30%) at 1x, reported, not judged.

What is judged: `evaluation.gate_v3.evaluate_book` on the research window (2017-05-01 to 2024-12-31; the hold-out
is never read: `holdout.enforce` refuses an --end on or after 2025-01-01). B6 with no sleeves. B4: the run is on
the point-in-time daily store (`--universe pit`); the core holds SPY only, so the run is its own point-in-time run.

Trials: n_trials 1 for the judged run and for each neighbour (six configurations), 0 for every cost, fill or
level report of the same configuration (decision 15 section 10), each row with the pre-registration's sha256 and
config/gate_v3.yaml's.

Writes <out>/index_core_<level>_<universe>_<ts>.json, the same name .md (the book test as Markdown) and a folder
with one daily CSV per run.
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
from kaibot.risk import index_core as ic  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies.index_core import IndexCore  # noqa: E402

RULE = "index_core"
JUDGED = "aggressive"
COSTS = (1.0, 2.0, 3.0, 0.0)
COST_MODEL = "v2"  # broker.sim.AuctionCostsV2 in engine/daily.run_daily (#559, #572; prereg Amendment 2)


def series(run, data: DailyData) -> dict[str, pd.Series]:
    idx = pd.DatetimeIndex([pd.Timestamp(str(r["date"])) for r in run.book.daily])
    net = pd.Series(daily_returns(run.book), index=idx)
    exp = pd.Series([float(r["exposure"]) for r in run.book.daily], index=idx)
    cost = pd.Series([float(r["cost"]) / float(r["start"]) if r["start"] else 0.0 for r in run.book.daily],
                     index=idx)
    return {"net": net, "exposure": exp, "costs": cost}


def index_stats(run) -> dict:
    """How the volatility cap acted: the weight asked at each decision (reported, not judged)."""
    ws = [float(t.features.get("index_weight", 1.0)) for t in run.targets.values()]
    return {"decisions": len(ws), "mean_index_weight": float(np.mean(ws)) if ws else None,
            "share_of_decisions_capped": float(np.mean([w < 1.0 for w in ws])) if ws else None,
            "spy_orders": sum(1 for f in run.fills if f["symbol"] == "SPY"),
            "guard_cuts": len(run.guards), "overdraft_days": len(run.ledger.overdrafts),
            "orders_rejected": len(run.rejected)}


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data", help="the data directory (data/daily: the daily store)")
    ap.add_argument("--start", default="2016-01-04", help="first session loaded (the warm-up)")
    ap.add_argument("--end", default="2024-12-31", help="last session (the research window ends 2024-12-31)")
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true", help="the committed fixture instead of the store (CI)")
    ap.add_argument("--out", default=None, help="report directory (default <data>/reports)")
    ap.add_argument("--trials", default=None, help="trial registry file (default <data>/trials.jsonl; '' = none)")
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data_dir = Path(a.data)
    trials_mod.guard(RULE)  # #472: registered before any data is read
    holdout.enforce(script=__file__, end=a.end, rule=RULE, opening=a.open_holdout, data=data_dir)
    pre = prereg_mod.load(RULE)  # refuses to run while the pre-registration is missing or not committed
    gate = g3.GateV3.load()
    cfg = ic.load()
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
    if i0 > i1:
        raise SystemExit("no session in the test window")
    profiles = load_profiles()
    runs: dict[str, tuple] = {}

    def one(label: str, strat: IndexCore, level: str, *, fill: str = "base", cost: float = 1.0, n_trials: int = 0):
        t = time.time()
        r = run_daily(strat, data, profiles[level], i0, i1, fill=fill, cost=cost, builder=ic.builder(level, cfg),
                      cost_model=COST_MODEL)
        st = dr.run_stats(r)
        runs[label] = (r, {"label": label, "spec": strat.name, "level": level, "fill": fill, "cost": cost,
                           "n_trials": n_trials, **st, **index_stats(r), "runtime_s": round(time.time() - t, 1)})
        print(f"  {label}: CAGR {st['cagr_pct']}%, max drawdown {st['max_drawdown_pct']}%, "
              f"{runs[label][1]['spy_orders']} SPY orders, {runs[label][1]['runtime_s']}s", flush=True)
        return r

    print(f"{RULE}: engine + SimBroker, {data.dates[i0]} to {data.dates[i1]} ({i1 - i0 + 1} sessions), "
          f"universe {universe}", flush=True)
    base = IndexCore()
    for c in COSTS:
        one(f"base|{c:g}x", base, JUDGED, cost=c, n_trials=int(c == 1.0))
    one("base|stress", base, JUDGED, fill="stress")
    for n in base.neighbours():
        one(f"neighbour|{n.name.split('~')[1]}", n, JUDGED, n_trials=1)
    for level in ("balanced", "conservative"):
        one(f"level|{level}", base, level)

    s = series(runs["base|1x"][0], data)
    spy_tr = data.spy_tr[i0:i1 + 1] if data.spy_tr is not None else None
    have_tr = spy_tr is not None and np.isfinite(np.asarray(spy_tr, dtype=float)).all()
    spy_c = data.sig_unfilled_c[:, data.spy]
    spy_r = np.asarray(spy_tr, dtype=float) if have_tr else spy_c[i0:i1 + 1] / spy_c[i0 - 1:i1] - 1
    spy = pd.Series(spy_r, index=s["net"].index)
    acc = g3.Account(s["net"], spy, s["exposure"], s["costs"],
                     universe="point_in_time" if universe == "pit" else universe)
    nb = {k.split("|")[1]: series(r, data)["net"] for k, (r, _) in runs.items() if k.startswith("neighbour|")}
    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    reg = trials_mod.load(trials_file=trials_path) if trials_path is not None else None
    bills = tbill_mod.find(data_dir)
    source = {"engine": "simbroker", "cost_model": COST_MODEL, "data_repairs": universe == "pit",
              "benchmark": "spy_total_return" if have_tr else "spy_price_only"}
    run_at = datetime.now(UTC)
    rep = g3.evaluate_book(gate, book=RULE, account=acc, neighbours=nb, sleeves={}, prereg=pre, source=source,
                           registry=reg, first_run_at=run_at, tbill=bills, as_of=run_at) \
        if len(s["net"]) >= 30 else None
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    tag = f"{RULE}_{JUDGED}_{universe}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "rule": RULE,
           "prereg_sha256": pre.sha256, "gate_sha": gate.sha, "holdout": holdout.stamp(None),
           "index_core_config": str(cfg.path.relative_to(ROOT)) if cfg.path.is_relative_to(ROOT) else str(cfg.path),
           "universe": universe, "data": info, "window": [str(data.dates[i0]), str(data.dates[i1])],
           "sessions": int(i1 - i0 + 1), "source": source, "rows": [row for _, row in runs.values()],
           "gate_v3_book": rep, "runtime_s": round(time.time() - t0, 1)}
    out_dir = Path(a.out or data_dir / "reports")
    (out_dir / tag).mkdir(parents=True, exist_ok=True)
    for label, (r, _) in runs.items():
        pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{label.replace('|', '_')}.csv", index=False)
    (out_dir / f"{tag}.json").write_text(json.dumps(out, indent=1, default=str))
    if rep is not None:
        (out_dir / f"{tag}.md").write_text(g3.render_book(rep))
    if trials_path is not None:
        for label, (_, row) in runs.items():
            trials_mod.append(trials_path, {
                "ts": out["run_at"], "trial_id": f"{tag}_{label}", "strategy": RULE, "variant": "engine_simbroker",
                "label": f"engine {label}", "level": row["level"], "cost_model": COST_MODEL,
                "universe_mode": universe, "code_sha": code_sha, "status": "engine", "prereg_sha256": pre.sha256,
                "gate_sha": gate.sha, "n_trials": row["n_trials"],
                "params": {"spec": row["spec"], "fill": row["fill"], "cost_multiple": row["cost"]},
                "window": out["window"],
                "metrics": {"cagr_pct": row["cagr_pct"], "daily_sharpe_ann": row["sharpe"],
                            "max_drawdown_pct": row["max_drawdown_pct"], "net_return_pct": row["return_pct"]}})
    print(f"verdict: {rep['verdict'] if rep else 'not judged (fewer than 30 sessions)'}; report "
          f"{out_dir / tag}.json ({out['runtime_s']}s)", flush=True)
    out["file"] = str(out_dir / f"{tag}.json")
    return out


if __name__ == "__main__":
    main()
