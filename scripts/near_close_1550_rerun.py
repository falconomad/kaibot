"""The 15:50-price re-run of the daily rule (M11 ticket 2.3, issue #454; docs/prereg/trend_momentum.md Amendment 31;
decision 15 section 12, question 1). Research only: reads data/daily and data/bars_1m, writes under --out and
--trials, never touches a broker account or a setting.

    .venv/bin/python scripts/near_close_1550_rerun.py --end 2024-12-31                 # the Mac (the runner)
    .venv/bin/python scripts/near_close_1550_rerun.py --fixture --data /tmp/x --trials ''   # tests

The live path sends `trend_momentum`'s orders as DAY market orders at 15:50 ET (Amendment 10, #333). Every daily
run so far filled them at the closing print. This script runs the SAME configuration (the base version, the
pre-registered sizing at the 100% exposure cap, Balanced, the point-in-time universe, cost model v2) twice
through `engine.daily.run_daily` with SimBroker:

- `close|1x`, `close|2x`: as every Gate B run, the near-close orders filled at the closing print;
- `at_1550|1x`, `at_1550|2x`: the same orders filled at the 15:50 price (the open of the first minute at or after
  15:50, `replay/minute_1550.py`) at v2's continuous venue. A session without a 15:50 minute for a name (a former
  member has no minute history) fills at the closing print, at the continuous venue's cost, and is counted.

It prints and writes the two accounts side by side, the share of fills (and of dollars) priced at a real 15:50
price, and the mean move from 15:50 to the close against the order (bp; positive means 15:50 was the worse price).
It is NOT a verdict: no neighbour, control or gate is run. Restating the rule's verdict is Product's step.

Trials: the 15:50 execution is a change to the fill model of a counted configuration, so `at_1550|1x` counts
n_trials 1. `close|1x` re-runs the counted base configuration, and the 2x rows are cost reports: all n_trials 0.

The window ends on --end. `holdout.enforce` refuses an --end on or after 2025-01-01, and the minute reader opens
no month after --end.
"""

from __future__ import annotations

import argparse
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
from kaibot.engine.daily import DailyData, run_daily, sessions_window  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.replay import minute_1550 as m1550  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import momentum as mom  # noqa: E402

RULE = "trend_momentum"
LEVEL = "balanced"  # the rule's pre-registered sizing (daily_report's default level)
CAP = 100.0  # the base version's exposure cap
COST_MODEL = "v2"
RUNS = (("close", "near_close", 1.0), ("close", "near_close", 2.0), ("at_1550", "at_1550", 1.0),
        ("at_1550", "at_1550", 2.0))
SET = "near_close_1550"
NOT_VENUE = ("cash_in_lieu", "delisted", "end_of_data", "protective_stop")  # ledger entries and stops, not orders


def fill_stats(run, data: DailyData) -> dict:
    """How the 15:50 run's fills were priced, and the 15:50-to-close move against each order (bp)."""
    n = {"at_1550": 0, "fallback": 0}
    usd = {"at_1550": 0.0, "fallback": 0.0}
    move: dict[str, list[tuple[float, float]]] = {"buy": [], "sell": []}
    for f in run.fills:
        if f["reason"] in NOT_VENUE:
            continue
        i, j = int(f["i"]), data.col(f["symbol"])
        p, c = float(data.raw_1550[i, j]), float(data.raw_c[i, j])
        dollars = abs(float(f["qty"]) * float(f["px"]))
        if math.isfinite(p) and p > 0:
            n["at_1550"] += 1
            usd["at_1550"] += dollars
            if math.isfinite(c) and c > 0:
                worse = (p - c) / c if f["side"] == "buy" else (c - p) / c
                move[f["side"]].append((worse * 1e4, dollars))
        else:
            n["fallback"] += 1
            usd["fallback"] += dollars

    def wmean(xs):
        w = sum(d for _, d in xs)
        return round(sum(x * d for x, d in xs) / w, 2) if w > 0 else None

    total, total_usd = sum(n.values()), sum(usd.values())
    return {"fills_at_1550": n["at_1550"], "fills_fallback_close": n["fallback"],
            "share_of_fills_at_1550": round(n["at_1550"] / total, 4) if total else None,
            "share_of_dollars_at_1550": round(usd["at_1550"] / total_usd, 4) if total_usd else None,
            "move_1550_to_close_bp": {"buy": wmean(move["buy"]), "sell": wmean(move["sell"]),
                                      "all": wmean(move["buy"] + move["sell"])}}


def cost_bp(run) -> float | None:
    paid = sum(float(r.get("cost") or 0.0) for r in run.book.daily)
    traded = sum(float(r.get("traded") or 0.0) for r in run.book.daily)
    return round(paid / traded * 1e4, 3) if traded > 0 else None


def render(out: dict) -> str:
    lines = [f"# {RULE}: near-close orders at the closing print and at the 15:50 price (#454)", "",
             "**This is a re-run of one configuration, not a verdict.** No neighbour, control or gate was run; "
             "restating the rule's verdict is Product's step (docs/prereg/trend_momentum.md Amendment 31).", "",
             f"Engine + SimBroker, cost model {COST_MODEL}, {out['window'][0]} to {out['window'][1]} "
             f"({out['sessions']} sessions), universe {out['universe']}, level {LEVEL}, exposure cap {CAP:g}%. "
             f"Code {out['code_sha']}.", "",
             "| Run | Return | CAGR | Sharpe | Max drawdown | Fills | Cost bp a side | n_trials |",
             "|---|---|---|---|---|---|---|---|"]
    for r in out["rows"]:
        name = r["label"].replace("|", ", ")  # a "|" would end the table cell
        lines.append(f"| {name} | {r.get('return_pct')}% | {r.get('cagr_pct')}% | {r.get('sharpe')} | "
                     f"{r.get('max_drawdown_pct')}% | {r.get('fills')} | {r.get('cost_bp_per_side')} | "
                     f"{r['n_trials']} |")
    s = out["at_1550"]
    lines += ["", f"- Fills of `at_1550|1x` priced at a real 15:50 minute: {s['fills_at_1550']} of "
              f"{s['fills_at_1550'] + s['fills_fallback_close']} ({s['share_of_fills_at_1550']}; by dollars "
              f"{s['share_of_dollars_at_1550']}). The rest had no 15:50 minute and filled at the closing print.",
              f"- 15:50 against the close, against the order, dollar-weighted (bp, positive = 15:50 was worse): "
              f"buys {s['move_1550_to_close_bp']['buy']}, sales {s['move_1550_to_close_bp']['sell']}, all "
              f"{s['move_1550_to_close_bp']['all']}.",
              f"- Minute history: {out['minutes']['symbols_with_minutes']} of {out['minutes']['symbols']} symbols; "
              f"{out['minutes']['intraday_rows']} sessions merged, {out['minutes']['intraday_scale_mismatch']} "
              "dropped by the store's split-basis check."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data", help="holds daily/ (the store), bars_1m/, reports/, trials.jsonl")
    ap.add_argument("--start", default="2016-01-04", help="first session loaded (the warm-up)")
    ap.add_argument("--end", default="2024-12-31", help="last session (the research window ends 2024-12-31)")
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true", help="the committed fixture's store (tests)")
    ap.add_argument("--out", default=None, help="report directory (default <data>/reports)")
    ap.add_argument("--trials", default=None, help="trial registry file (default <data>/trials.jsonl; '' = none)")
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data_dir = Path(a.data)
    trials_mod.guard(RULE)  # #472: registered before any data is read
    holdout.enforce(script=__file__, end=a.end, rule=RULE, opening=a.open_holdout, data=data_dir)
    pre = prereg_mod.load(RULE)
    t0 = time.time()
    if a.fixture:
        market, info = dr.load_fixture(dr.FIXTURE)
        universe, store_root, w = "fixture", dr.FIXTURE, info["window"]
    else:
        market, info = dr.load_store(data_dir, Path(a.universe_file), a.universe, a.start, a.end)
        universe, store_root, w = a.universe, data_dir / "daily", ["2017-05-01", a.end]
    data = DailyData.from_market(market)
    i0, i1 = sessions_window(data, w[0], min(pd.Timestamp(w[1]), pd.Timestamp(a.end)).date())
    if i0 > i1:
        raise SystemExit("no session in the test window")
    p, v, minutes = m1550.arrays(store_root, data, data_dir, end=str(data.dates[i1]))
    if not minutes["symbols_with_minutes"]:
        raise SystemExit(f"no minute history under {data_dir / 'bars_1m'}: the 15:50 price needs it "
                         "(scripts/fetch_history.py, docs/m0/reproduce.md)")
    data5 = data.with_1550(p, v)
    pol = load_profiles()[LEVEL]
    strategy = mom.VERSIONS[RULE]
    print(f"{RULE}: engine + SimBroker, {data.dates[i0]} to {data.dates[i1]} ({i1 - i0 + 1} sessions), universe "
          f"{universe}; 15:50 minutes for {minutes['symbols_with_minutes']} of {minutes['symbols']} symbols",
          flush=True)
    runs: dict[str, tuple] = {}
    for name, execution, cost in RUNS:
        t = time.time()
        r = run_daily(strategy, data5, pol, i0, i1, cost=cost, exposure_pct=CAP, execution=execution,
                      cost_model=COST_MODEL)
        st = dr.run_stats(r)
        label = f"{name}|{cost:g}x"
        runs[label] = (r, {"label": label, "spec": strategy.name, "execution": execution, "cost": cost,
                           "n_trials": int(label == "at_1550|1x"), "cost_bp_per_side": cost_bp(r), **st,
                           "runtime_s": round(time.time() - t, 1)})
        print(f"  {label}: CAGR {st['cagr_pct']}%, Sharpe {st['sharpe']}, max drawdown {st['max_drawdown_pct']}%, "
              f"{st['fills']} fills, {runs[label][1]['runtime_s']}s", flush=True)
    stats = fill_stats(runs["at_1550|1x"][0], data5)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    tag = f"{SET}_{RULE}_{LEVEL}_{universe}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = {"set": SET, "run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "rule": RULE,
           "prereg_sha256": pre.sha256, "holdout": holdout.stamp(None), "universe": universe,
           "universe_mode": "point_in_time" if universe == "pit" else universe, "data": info, "costs": COST_MODEL,
           "window": [str(data.dates[i0]), str(data.dates[i1])], "sessions": int(i1 - i0 + 1), "level": LEVEL,
           "exposure_pct": CAP, "minutes": minutes, "at_1550": stats, "rows": [row for _, row in runs.values()],
           "verdict": None, "runtime_s": round(time.time() - t0, 1)}
    out_dir = Path(a.out or data_dir / "reports")
    (out_dir / tag).mkdir(parents=True, exist_ok=True)
    for label, (r, _) in runs.items():
        pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{label.replace('|', '_')}.csv", index=False)
    pd.DataFrame(runs["at_1550|1x"][0].fills).to_csv(out_dir / tag / "fills_at_1550_1x.csv", index=False)
    (out_dir / f"{tag}.json").write_text(json.dumps(out, indent=1, default=str))
    text = render(out)
    (out_dir / f"{tag}.md").write_text(text)
    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    if trials_path is not None:
        for label, (_, row) in runs.items():
            trials_mod.append(trials_path, {
                "ts": out["run_at"], "trial_id": f"{tag}_{label}", "strategy": RULE,
                "variant": "engine_simbroker_near_close_1550", "label": f"engine {label}", "level": LEVEL,
                "cost_model": COST_MODEL, "universe_mode": universe, "code_sha": code_sha, "status": "engine",
                "prereg_sha256": pre.sha256, "n_trials": row["n_trials"],
                "params": {"spec": row["spec"], "execution": row["execution"], "cost_multiple": row["cost"],
                           "exposure_pct": CAP},
                "window": out["window"],
                "metrics": {"cagr_pct": row["cagr_pct"], "daily_sharpe_ann": row["sharpe"],
                            "max_drawdown_pct": row["max_drawdown_pct"], "net_return_pct": row["return_pct"]}})
    print(text, flush=True)
    print(f"report {out_dir / tag}.json ({out['runtime_s']}s); not a verdict", flush=True)
    out["file"] = str(out_dir / f"{tag}.json")
    return out


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
