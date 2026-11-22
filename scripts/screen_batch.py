"""One batch of the tier-one screen (M11 ticket 2.11, issue #462; docs/research/tier_one_screen/design.md). Research
only: never a go/no-go number; a survivor earns a pre-registration and an engine run.

    python scripts/screen_batch.py --batch docs/research/tier_one_screen/batches/<id>.yaml --data data   # the Mac
    python scripts/screen_batch.py --batch <file> --fixture --data /tmp/s --trials ''                    # CI

The batch file (committed BEFORE the run; the run refuses an uncommitted or edited one) names its id, its date, its
family in config/gate_v3.yaml and its candidates (a name, a signal of `evaluation.screen.SIGNALS` and its
parameters). The run reads the point-in-time daily store on the research window only (`holdout.enforce`), takes
the batch's level q_b from the committed budget ledger (docs/research/tier_one_screen/budget.md), computes each
candidate's monthly rank IC, its Newey-West t and p, and runs Benjamini-Hochberg at q_b. It writes the report, one
trial row per candidate (n_trials 1, `variant: screen`; not counted in S4) and prints the ledger line that the
pull request recording the batch appends.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kaibot.engine import daily_report as dr  # noqa: E402
from kaibot.engine.daily import DailyData, sessions_window  # noqa: E402
from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import screen as sc  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402


def load_batch(path: Path, gate: g3.GateV3) -> dict:
    b = yaml.safe_load(path.read_text()) or {}
    for k in ("id", "date", "family", "candidates"):
        if k not in b:
            raise SystemExit(f"{path}: the batch file needs `{k}`")
    if b["family"] not in gate.families:
        raise SystemExit(f"{path}: family {b['family']!r} is not in config/gate_v3.yaml (add it first)")
    names = [c["name"] for c in b["candidates"]]
    if len(set(names)) != len(names) or any(gate.family_of(n) or gate.book(n) for n in names):
        raise SystemExit(f"{path}: candidate names must be unique and must not be a rule's or a book's name")
    for c in b["candidates"]:
        if c.get("signal") not in sc.SIGNALS:
            raise SystemExit(f"{path}: {c['name']}: unknown signal {c.get('signal')!r}")
    return b


def counted_proposals(batch: dict, trials_path: Path | None) -> set[str]:
    """The proposal ids the batch names, each checked against the trial registry: a candidate may log 0 trials only
    for a proposal whose own row (`variant: proposal`, n_trials at least 1, #575's `propose.py`) is already in the
    registry. A proposal id the registry does not hold is refused: else any batch could skip its count by naming one."""
    named = [str(c["proposal_id"]).strip() for c in batch["candidates"] if c.get("proposal_id") is not None]
    if not named:
        return set()
    if any(not n for n in named) or len(set(named)) != len(named):
        raise SystemExit("screen: a proposal_id must be non-empty and named by one candidate only")
    rows, _ = trials_mod.read_jsonl(trials_path)
    counted = {str(r.get("proposal_id")) for r in rows
               if r.get("variant") == "proposal" and trials_mod.configurations_of(r) >= 1}
    screened = {str(r.get("proposal_id")) for r in rows if r.get("variant") == "screen" and r.get("proposal_id")}
    missing = sorted(set(named) - counted)
    if missing:
        raise SystemExit(f"screen: proposal_id {', '.join(missing)} has no counted proposal row in the trial registry "
                         f"({trials_path or 'none given'}): log it with scripts/propose.py first")
    return set(named) - screened  # a proposal screened before counts again in a later batch


def decision_rows(data: DailyData, i0: int, i1: int, horizon: int) -> np.ndarray:
    d = pd.DatetimeIndex(data.dates.astype("datetime64[ns]"))
    rows = [k for k in range(max(i0, 1), i1 + 1) if d[k].month != d[k - 1].month and k + horizon <= i1]
    return np.asarray(rows, dtype=int)


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default=None)
    ap.add_argument("--committed", default="yes", choices=("yes", "no"), help=argparse.SUPPRESS)  # tests only
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data_dir, path = Path(a.data), Path(a.batch)
    gate = g3.GateV3.load()
    batch = load_batch(path, gate)
    holdout.enforce(script=__file__, end=a.end, rule=f"screen:{batch['id']}", opening=a.open_holdout, data=data_dir)
    if a.committed == "yes":
        try:
            prereg_mod._check_committed(path.resolve(), ROOT)
        except prereg_mod.PreregError as e:
            raise SystemExit(f"screen: {e}") from None
        except ValueError:
            raise SystemExit(f"screen: {path} is not in the repository: a batch file is committed before its run")\
                from None
    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    free = counted_proposals(batch, trials_path)
    cfg = sc.load_config()
    cfg_sha = hashlib.sha256(sc.CONFIG.read_bytes()).hexdigest()
    budget = sc.Budget.from_config(cfg)
    led = sc.read_ledger(ROOT / cfg["ledger"], budget)
    if batch["id"] in led["batches"]:
        raise SystemExit(f"screen: batch {batch['id']} is already in the ledger: a batch runs once")
    level = led["next_level"]
    if level <= 0:
        raise SystemExit(f"screen: the budget is spent (wealth {led['wealth']:.4f}): Product resets it by decision "
                         "record")
    st = cfg["statistic"]
    if a.fixture:
        market, info = dr.load_fixture(dr.FIXTURE)
        w0 = info["window"][0]
    else:
        market, info = dr.load_store(data_dir, Path(a.universe_file), "pit", a.start, a.end)
        w0 = str(gate.window[0].date())
    data = DailyData.from_market(market)
    i0, i1 = sessions_window(data, w0, min(pd.Timestamp(a.end), gate.window[1]).date())
    rows = decision_rows(data, i0, i1, int(st["horizon_sessions"]))
    c, ok = data.sig_c, data.ok
    fwd = sc.forward_excess(c, c[:, data.spy], rows, int(st["horizon_sessions"]))
    results = {}
    for cand in batch["candidates"]:
        score = sc.candidate_scores(c, ok, rows, cand["signal"], cand.get("params") or {})
        ics = sc.rank_ics(score, fwd, top_fraction=float(st["top_fraction"]),
                          charge=2 * float(st["charge_bp_a_side"]) / 1e4, min_stocks=int(st["min_stocks"]))
        results[cand["name"]] = {**sc.statistic(ics, int(st["periods_per_year"])), "signal": cand["signal"],
                                 "params": cand.get("params") or {},
                                 "proposal_id": None if cand.get("proposal_id") is None
                                 else str(cand["proposal_id"]).strip()}
    verdict = sc.screen_batch({k: v["p"] for k, v in results.items()}, level)
    for k in results:
        results[k].update(verdict[k])
    survivors = sorted(k for k, v in results.items() if v["survives"])
    run_at = datetime.now(UTC)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    line = sc.ledger_line(run_at.date().isoformat(), batch["id"], led["wealth"], level, len(survivors), budget)
    rep = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "batch": batch["id"],
           "family": batch["family"], "level": level, "wealth_before": led["wealth"], "decisions": int(len(rows)),
           "window": [str(data.dates[i0]), str(data.dates[i1])], "candidates": results, "survivors": survivors,
           "ledger_line": line, "holdout": holdout.stamp(None),
           "note": "a survivor needs a pre-registration and an engine run; no result here is a verdict"}
    out_dir = Path(a.out or data_dir / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"screen_{batch['id']}_{run_at.strftime('%Y%m%dT%H%M%S')}.json"
    f.write_text(json.dumps(rep, indent=1, default=str))
    if trials_path is not None:
        for k, v in results.items():
            trials_mod.append(trials_path, {
                "ts": rep["run_at"], "trial_id": f"screen_{batch['id']}_{k}", "strategy": f"screen:{batch['id']}:{k}",
                "candidate": k, "variant": "screen",
                "family": batch["family"], "batch": batch["id"], "code_sha": code_sha,
                # Product, 22 Nov 2026: a proposal (#575) was counted at its intake; its screen row counts 0
                "n_trials": 0 if v.get("proposal_id") in free else 1, "proposal_id": v.get("proposal_id"),
                "config_sha256": cfg_sha,
                "params": {"signal": v["signal"], **v["params"]}, "window": rep["window"],
                "metrics": {"mean_ic": v["mean_ic"], "t": v["t"], "p": v["p"], "level": level,
                            "survives": v["survives"]}})
    print(f"batch {batch['id']}: level {level:.4f}, survivors {survivors or 'none'}; report {f}")
    print(f"ledger line to append (docs/research/tier_one_screen/budget.md):\n{line}")
    rep["file"] = str(f)
    return rep


if __name__ == "__main__":
    main()
