"""Cost re-run of the SPY noise-band rule with the measured index-fund bucket (M11 ticket 2.3, issue #454).
Research only: it reads data/bars_1m and writes under data/reports and data/trials. It never touches the engine's
journal, the broker or any setting.

.venv/bin/python scripts/spy_cost_rerun.py --end 2024-12-31

ONE engine run: the frozen rule `spy_noise_band` exactly as in `scripts/spy_engine.py` (issue #202: the engine with
SimBroker and SimClock on SPY minute bars, every order through the risk gate), with one thing changed: SPY's fills
pay the index-fund bucket of config/costs_index_bucket.yaml (scenario "v2idx") in place of the 1.75 bp bucket of
every stock over $500M a day. Impact, the tick floor, the venue multipliers and the fees are v2's.

It refuses to run until the bucket has been measured and committed (`scripts/calibrate_index_costs.sh`).

What it is and is not:
- the SAME configuration of the rule (same parameters, same engine): no neighbour, no control and no Gate B
  evaluation is run here;
- a parameter change of the COST MODEL, so it logs one trial row with n_trials 1 (costs_v2: "any change to them is
  a parameter change and logs a trial"), with the bucket's value and the sha256 of its file;
- NOT a verdict. It prints the new row beside the rows of the newest spy_engine report (cost model v2, 0.5 bp,
  1 bp) recomputed on the same sessions from that report's daily files. Restating the rule's verdict is Product's
  step, after reading both, and under the gate in force then.

The window ends on --end. With the hold-out frozen (config/holdout.yaml) the last research session is 2024-12-31;
the run of issue #202 went to 2026-11-06, so the comparison rows are cut to the same sessions.

Writes data/reports/spy_cost_rerun_<ts>.json, a folder of the same name (daily and fills CSV) and
data/reports/experiments_spy_cost_rerun.md. About 40 minutes of CPU (one engine run of ten years of minutes).
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
sys.path.insert(0, str(ROOT / "scripts"))
import spy_engine as se  # noqa: E402

from kaibot.broker import costs_v2  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.replay import spy_noise_band as nb  # noqa: E402
from kaibot.replay.data import from_frame  # noqa: E402

try:  # the hold-out guard arrives with ticket 2.6; this script works before and after that merge
    from kaibot.evaluation import holdout
except ImportError:  # pragma: no cover - only on a tree without ticket 2.6
    holdout = None

SET = "spy_cost_rerun"
SCENARIO = "v2idx"
SUMMARY = "experiments_spy_cost_rerun.md"
KEYS = ("trades", "net_bp_per_trip", "t_net", "mean_cost_bp_per_side", "return_pct", "cagr_pct", "sharpe",
        "max_drawdown_pct")


def earlier_rows(reports: Path, level: str, first: str, last: str) -> tuple[str | None, list[dict]]:
    """The base rows of the newest spy_engine report, recomputed on the sessions `first`..`last` from its daily
    files (no re-run). Round-trip numbers cannot be cut to a window from a daily file, so they are the report's
    own, over its own window, and are labelled so."""
    hit = se.find_report(reports, level)
    if hit is None:
        return None, []
    f, rep = hit
    out = []
    for r in rep.get("rows", []):
        rel = (r.get("files") or {}).get("daily")
        if r.get("spec") != se.RULE or not rel or not (reports / rel).is_file():
            continue
        d = pd.read_csv(reports / rel)
        d = d[(d["date"].astype(str) >= first) & (d["date"].astype(str) <= last)]
        if len(d) < 3:
            continue
        st = nb.series_stats(np.array(d["date"], dtype="datetime64[D]"), (d["pnl"] / d["start"]).to_numpy())
        out.append({"label": r["label"], "cost": r.get("cost"), "sessions": int(len(d)),
                    "window": [str(d["date"].iloc[0]), str(d["date"].iloc[-1])],
                    **{k: st.get(k) for k in ("return_pct", "cagr_pct", "sharpe", "max_drawdown_pct")},
                    "own_window": {k: r.get(k) for k in ("trades", "net_bp_per_trip", "t_net",
                                                         "mean_cost_bp_per_side")}})
    return f.name, out


def page(rep: dict) -> str:
    b, r = rep["bucket"], rep["row"]
    out = ["# SPY noise band: cost re-run with the index-fund bucket", "",
           f"Run {rep['run_at']} at code `{rep['code_sha']}`, {rep['window'][0]} to {rep['window'][1]} "
           f"({r['sessions']} sessions), level {rep['level']}. The engine with SimBroker, the frozen rule, one run.",
           "", f"**Not a verdict.** One thing differs from the run of issue #202: SPY's fills pay "
           f"{b['half_bp']:.2f} bp a side (the measured index-fund bucket, {b['source'].get('method', '?')}, "
           f"{b['source'].get('rows', '?')} rows) in place of cost model v2's 1.75 bp. No neighbour, no control and "
           "no Gate B evaluation was run.", "",
           "| Account | Sessions | Round trips | Net bp a trip | t | Cost bp a side | Return | CAGR | Sharpe | "
           "Max drawdown |", "|---|---|---|---|---|---|---|---|---|---|",
           f"| **this run: v2 with the index-fund bucket** | {r['sessions']} | {r['trades']} | {r['net_bp_per_trip']} "
           f"| {r['t_net']} | {r['mean_cost_bp_per_side']} | {r['return_pct']}% | {r['cagr_pct']}% | {r['sharpe']} | "
           f"{r['max_drawdown_pct']}% |"]
    for e in rep["earlier"]:
        o = e["own_window"]
        out.append(f"| earlier run, {e['cost']} (same sessions, from its daily file) | {e['sessions']} | "
                   f"{o['trades']}* | {o['net_bp_per_trip']}* | {o['t_net']}* | {o['mean_cost_bp_per_side']}* | "
                   f"{e['return_pct']}% | {e['cagr_pct']}% | {e['sharpe']} | {e['max_drawdown_pct']}% |")
    if rep["earlier"]:
        out += ["", f"\\* Round-trip figures of the earlier run are over its own window, as `{rep['earlier_report']}` "
                    "reports them; they cannot be cut to a window from a daily file."]
    else:
        out += ["", "No earlier spy_engine report is on this machine, so there is no row to compare with."]
    out += ["", f"- Gross edge per round trip and the break-even cost: {json.dumps(r.get('break_even'))}.",
            f"- Engine errors {r['errors']}, halts {json.dumps(r['halts'])}, orders refused by the risk gate "
            f"{r['gate_refused']}. Mean position {r['mean_position_pct']}% of equity (the single-name cap applies to "
            "SPY as to any stock).",
            f"- Trial: one row, n_trials 1 (a parameter change of the cost model), `{rep['trial_id']}`.",
            f"- Bucket file sha256 `{b['sha256']}`; pre-registration sha256 `{rep['prereg']['sha256']}`.",
            "- The 2022-23 window and the yearly returns are in the JSON report."]
    return "\n".join(out) + "\n"


def run(a: argparse.Namespace) -> dict:
    data = Path(a.data)
    try:
        bucket = costs_v2.load_index_bucket(ROOT)
    except costs_v2.NotCalibrated as e:
        raise SystemExit(f"no run: {e}") from e
    if "SPY" not in bucket["symbols"]:
        raise SystemExit("no run: the index-fund bucket does not list SPY")
    try:
        sha = prereg_mod.require(se.RULE, ROOT)
    except prereg_mod.PreregError as e:
        raise SystemExit(f"no run: {e}") from e
    t0 = time.time()
    frame = se.load_frame(data, "SPY", a.start, a.end)
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates if a.test_start <= str(d) <= a.end]
    if not days:
        raise SystemExit(f"no SPY session between {a.test_start} and {a.end}")
    se.log(f"SPY: test window {days[0]} to {days[-1]} ({len(days)} sessions); index-fund bucket "
           f"{bucket['half_bp']:.2f} bp a side ({', '.join(bucket['symbols'])}); prereg sha256 {sha[:12]}")
    r = se._job({"data": str(data), "start": a.start, "end": a.end, "test_start": a.test_start, "level": a.level,
                 "equity": a.equity, "version": se.RULE, "scenario": SCENARIO})
    st = se.run_stats(r, sd)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    tag = f"{SET}_{a.level}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    (data / "trials").mkdir(parents=True, exist_ok=True)
    daily = pd.DataFrame(r["daily"])
    daily.to_csv(out / tag / f"daily_{se.RULE}_{SCENARIO}.csv", index=False)
    pd.DataFrame(r["fills"]).to_csv(out / tag / f"fills_{se.RULE}_{SCENARIO}.csv", index=False)
    if r["errors"]:
        (out / tag / "errors.txt").write_text("\n".join(r["errors"]) + "\n")
    st["files"] = {"daily": f"{tag}/daily_{se.RULE}_{SCENARIO}.csv", "fills": f"{tag}/fills_{se.RULE}_{SCENARIO}.csv"}
    tid = f"{tag}_{se.RULE}_{SCENARIO}"
    pd.DataFrame({"date": daily["date"], "ret": daily["pnl"] / daily["start"]}).to_csv(
        data / "trials" / f"{tid}.csv", index=False)
    earlier_name, earlier = earlier_rows(out, a.level, days[0], days[-1])
    rep = {"set": SET, "rule": se.RULE, "status": "cost re-run of the configuration of issue #202; not a verdict",
           "run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "level": a.level,
           "start": a.start, "test_start": a.test_start, "end": a.end, "equity": a.equity,
           "window": [days[0], days[-1]], "scenario": SCENARIO,
           "bucket": bucket, "prereg": {"sha256": sha}, "row": st, "rows": [st], "trial_id": tid,
           "earlier_report": earlier_name, "earlier": earlier, "runtime_s": round(time.time() - t0, 1)}
    trials_mod.append(data / "trials.jsonl", {
        "ts": run_at.isoformat(timespec="seconds"), "trial_id": tid, "strategy": se.RULE, "variant": SET,
        "label": f"spy noise band engine, cost model v2 with the index-fund bucket {bucket['half_bp']:.2f} bp",
        "level": a.level, "code_sha": code_sha, "experiment": SET, "status": "engine_simbroker",
        "cost_model": "v2_index", "prereg_sha256": sha,
        "n_trials": 1,  # a parameter change of the cost model (costs_v2; ticket 2.3: "each re-run logs a trial row")
        "params": {"cost": SCENARIO, "index_half_bp": bucket["half_bp"], "index_symbols": list(bucket["symbols"]),
                   "bucket_sha256": bucket["sha256"]},
        "window": [days[0], days[-1]],
        "metrics": {k: st[k] for k in ("trades", "net_bp_per_trip", "t_net", "return_pct", "cagr_pct", "sharpe",
                                       "max_drawdown_pct")}})
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))  # last: marks the run finished
    text = page(rep)
    (out / SUMMARY).write_text(text)
    print(text)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cost re-run of the SPY noise band with the measured index-fund bucket")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--start", default="2016-01-01", help="first bar read (the history before the test)")
    ap.add_argument("--test-start", default="2017-05-01", help="first test session (decision 15: 2017-05 onward)")
    ap.add_argument("--end", required=True, help="last session (inclusive); 2024-12-31 is the last research session")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    if holdout is not None:
        holdout.add_argument(ap)
    a = ap.parse_args(argv)
    # #472 (QA): registered before the hold-out check too, so an unregistered run never uses a hold-out read
    trials_mod.guard(se.RULE)
    if holdout is not None:
        holdout.enforce(script=__file__, end=a.end, rule=se.RULE, opening=a.open_holdout, data=a.data)
    run(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
