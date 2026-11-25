"""The index core with a momentum sleeve at a fixed budget, tested ONCE as a combined book through the ENGINE with
SimBroker (M11 ticket 2.8, issue #459; docs/prereg/index_core_trend_momentum.md and
docs/prereg/trend_momentum_sleeve.md, frozen; decision 16 sections 4, 5, 14 and 15). Research only.

    python scripts/index_core_book.py --data data --end 2024-12-31                 # the pre-registered run (Mac)
    python scripts/index_core_book.py --fixture --out /tmp/b --trials '' --control-runs 20   # CI, seconds

Two parts, each one continuous $100k account per configuration through `engine.daily.run_daily`:

1. The sleeve test of `trend_momentum_sleeve` (decision 16 section 4): `trend_momentum` alone at its pre-registered
   sizing (`engine.daily.momentum_builder`, 100% exposure cap, Balanced per-name cap) at 1x and 2x costs, its five
   neighbours, and the exposure-matched random control (`daily_report.control_for`, default 2,000 runs: the
   family has several rules, decision 16 section 15). Judged by `gate_v3.evaluate_sleeve`; its tier is
   evidence-based, so S3 and S4 are printed, not judged, and the verdict is `pass`, `evidence_based` or a fail.
2. The book (`OnCore(trend_momentum, IndexCore(), budget 10%)`, `risk.index_core.sleeve_builder`) at Hunter at 1x,
   2x, 3x and 0x costs, the stress fill, its five neighbours, Balanced and Careful; judged by
   `gate_v3.evaluate_book` with the sleeve's verdict for B6 and the budget listed under `books:`.

Before any run, `check_budgets` compares the pre-registered budget under `books:` of config/gate_v3.yaml with the
budget and the scaling the engine is about to run (ticket #547's check), and stops on any difference.

`--index-cap 80` (Amendment 3 of docs/prereg/index_core_trend_momentum.md, Product 25 Nov 2026; research only):
the book with its core allowed up to 80% of the book at Hunter (Balanced 57, Careful 27), as scripts/index_core.py.
The sleeve test is not changed by it. Only the pre-registered values are taken (27, the config; 80).

Trials: n_trials 1 for the judged book and each of its five neighbours (six configurations); 0 for every other row
(the sleeve's runs re-run configurations `trend_momentum` already counted; cost, fill and level reports).
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

import index_core as core_run  # noqa: E402

from kaibot.engine import daily_report as dr  # noqa: E402
from kaibot.engine.daily import DailyData, run_daily, sessions_window  # noqa: E402
from kaibot.evaluation import gate_v3 as g3  # noqa: E402
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import tbill as tbill_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.risk import index_core as ic  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import momentum as mom  # noqa: E402
from kaibot.strategies.index_core import IndexCore, OnCore  # noqa: E402

BOOK = "index_core_trend_momentum"
SLEEVE = "trend_momentum_sleeve"
SLEEVE_RULE = "trend_momentum"
BUDGET = 10.0
JUDGED = "aggressive"
SLEEVE_LEVEL = "balanced"  # the rule's pre-registered sizing (15% per name)
COSTS = (1.0, 2.0, 3.0, 0.0)
CONTROL_SEED = 459


def book_of(budget: float = BUDGET, core: IndexCore | None = None, name: str = BOOK) -> OnCore:
    return OnCore(mom.VERSIONS[SLEEVE_RULE], core or IndexCore(), budget, name=name)


def book_builder(book: OnCore, level: str, cfg, profiles, index_cap: float | None = None):
    name_pct = float(profiles[SLEEVE_LEVEL].max_position_pct) * book.budget_pct / 100
    return ic.sleeve_builder(level, book.budget_pct, book.atr_risk, name_pct, cfg, index_cap_pct=index_cap)


def check_budgets(gate: g3.GateV3, book: OnCore, builder) -> dict:
    """The pre-registered budget of each sleeve (`books:` of config/gate_v3.yaml) against what the engine runs: the
    builder's sleeve budget, the sleeve's k and per-name cap scaled by the same budget. Raises SystemExit on any
    difference, before anything runs."""
    entry = gate.book(BOOK) or {}
    want = dict(entry.get("sleeves") or {})
    ran = {SLEEVE: float(book.budget_pct)}
    k_want = float(book.sleeve.atr_risk) * float(want.get(SLEEVE, 0.0)) / 100
    problems = []
    if set(want) != set(ran):
        problems.append(f"sleeves listed {sorted(want)}, the engine runs {sorted(ran)}")
    for s, pct in ran.items():
        if s in want and abs(float(want[s]) - pct) > 1e-9:
            problems.append(f"{s}: pre-registered {want[s]}% of the book, the engine runs {pct}%")
    if builder.sleeve_budget_pct is None or abs(builder.sleeve_budget_pct - ran[SLEEVE]) > 1e-9:
        problems.append(f"the builder's sleeve budget is {builder.sleeve_budget_pct}, not {ran[SLEEVE]}")
    if abs(builder.k - k_want) > 1e-12:
        problems.append(f"the sleeve's k is {builder.k}, its budget asks for {k_want}")
    if problems:
        raise SystemExit("budgets: " + "; ".join(problems))
    return {"pre_registered": want, "engine": ran, "k": builder.k, "max_name_pct": builder.limits.max_name_pct}


def main(argv: list[str] | None = None) -> dict:
    gate = g3.GateV3.load()
    default_runs = int(gate.cfg["sleeve"]["s3_control"].get("random_runs_min_several_rules",
                                                            gate.cfg["sleeve"]["s3_control"]["random_runs_min"]))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default="data")
    ap.add_argument("--start", default="2016-01-04")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--universe", default="pit", choices=("pit", "today"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--control-runs", type=int, default=default_runs,
                    help=f"random control runs of the sleeve test (default {default_runs}, config/gate_v3.yaml)")
    ap.add_argument("--index-cap", type=float, choices=core_run.INDEX_CAPS, default=None,
                    help="the core's index cap at Hunter, %% of the book (pre-registered: 27, the config; 80)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default=None)
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data_dir = Path(a.data)
    trials_mod.guard(BOOK)  # #472: the book and its sleeve are registered before any data is read
    trials_mod.guard(SLEEVE)
    holdout.enforce(script=__file__, end=a.end, rule=BOOK, opening=a.open_holdout, data=data_dir)
    pre_book, pre_sleeve = prereg_mod.load(BOOK), prereg_mod.load(SLEEVE)
    cfg, profiles = ic.load(), load_profiles()
    override = core_run.cap_override(a.index_cap, cfg)
    index_cap = override if override is not None else cfg.levels[JUDGED].max_index_pct
    base = book_of()
    check = check_budgets(gate, base, book_builder(base, JUDGED, cfg, profiles, override))
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
    runs: dict[str, tuple] = {}

    def one(label, strat, level, builder=None, *, fill="base", cost=1.0, n_trials=0, part="book"):
        t = time.time()
        r = run_daily(strat, data, profiles[level], i0, i1, fill=fill, cost=cost, builder=builder,
                      cost_model=core_run.COST_MODEL)  # v2 (#572; both preregs' amendments)
        st = dr.run_stats(r)
        runs[label] = (r, {"label": label, "part": part, "spec": strat.name, "level": level, "fill": fill,
                           "cost": cost, "n_trials": n_trials,
                           "max_index_pct": builder.limits.max_index_pct if builder is not None else None,
                           **st, **core_run.index_stats(r),
                           "runtime_s": round(time.time() - t, 1)})
        print(f"  {label}: CAGR {st['cagr_pct']}%, max drawdown {st['max_drawdown_pct']}%, "
              f"{runs[label][1]['runtime_s']}s", flush=True)
        return r

    print(f"{BOOK}: engine + SimBroker, {data.dates[i0]} to {data.dates[i1]}, universe {universe}, "
          f"index cap {index_cap:g}% at Hunter", flush=True)
    # 1. the sleeve test
    rule = mom.VERSIONS[SLEEVE_RULE]
    sl = one("sleeve|1x", rule, SLEEVE_LEVEL, part="sleeve")
    one("sleeve|2x", rule, SLEEVE_LEVEL, cost=2.0, part="sleeve")
    for n in mom.neighbours(SLEEVE_RULE):
        one(f"sleeve_neighbour|{n.name.split('~')[1]}", n, SLEEVE_LEVEL, part="sleeve")
    control = dr.control_for(sl, market, SLEEVE_RULE, profiles[SLEEVE_LEVEL], a.control_runs, CONTROL_SEED,
                             100_000.0) if a.control_runs > 0 else None
    # 2. the book
    for c in COSTS:
        one(f"book|{c:g}x", base, JUDGED, book_builder(base, JUDGED, cfg, profiles, override), cost=c,
            n_trials=int(c == 1.0))
    one("book|stress", base, JUDGED, book_builder(base, JUDGED, cfg, profiles, override), fill="stress")
    nb_books = {"budget5": book_of(5.0, name=f"{BOOK}~budget5"),
                **{k: book_of(core=c, name=f"{BOOK}~{k}") for k, c in
                   ((n.name.split("~")[1], n) for n in IndexCore().neighbours()) if k in ("nocap", "vol12", "vol18",
                                                                                      "lookback63")}}
    for k, b in nb_books.items():
        one(f"book_neighbour|{k}", b, JUDGED, book_builder(b, JUDGED, cfg, profiles, override), n_trials=1)
    for level in ("balanced", "conservative"):
        one(f"book_level|{level}", base, level, book_builder(base, level, cfg, profiles, override))

    spy_tr = data.spy_tr[i0:i1 + 1] if data.spy_tr is not None else None
    have_tr = spy_tr is not None and np.isfinite(np.asarray(spy_tr, dtype=float)).all()
    spy_c = data.sig_unfilled_c[:, data.spy]
    first = core_run.series(runs["book|1x"][0], data)
    spy = pd.Series(np.asarray(spy_tr, dtype=float) if have_tr else spy_c[i0:i1 + 1] / spy_c[i0 - 1:i1] - 1,
                    index=first["net"].index)
    uni = "point_in_time" if universe == "pit" else universe
    source = {"engine": "simbroker", "cost_model": core_run.COST_MODEL, "data_repairs": universe == "pit",
              "benchmark": "spy_total_return" if have_tr else "spy_price_only"}
    trials_path = None if a.trials == "" else Path(a.trials or data_dir / "trials.jsonl")
    reg = trials_mod.load(trials_file=trials_path) if trials_path is not None else None
    bills = tbill_mod.find(data_dir)
    run_at = datetime.now(UTC)

    def account(label: str, **kw) -> g3.Account:
        s = core_run.series(runs[label][0], data)
        return g3.Account(s["net"], spy, s["exposure"], s["costs"], universe=uni, **kw)

    sleeve_rep = g3.evaluate_sleeve(
        gate, rule=SLEEVE, account=account("sleeve|1x", control=control),
        neighbours={k.split("|")[1]: account(k) for k in runs if k.startswith("sleeve_neighbour|")},
        prereg=pre_sleeve, source=source, registry=reg, first_run_at=run_at, tbill=bills, as_of=run_at)
    book_rep = g3.evaluate_book(
        gate, book=BOOK, account=account("book|1x"),
        neighbours={k.split("|")[1]: core_run.series(r, data)["net"] for k, (r, _) in runs.items()
                    if k.startswith("book_neighbour|")},
        sleeves={SLEEVE: sleeve_rep["verdict"]}, prereg=pre_book, source=source, registry=reg, first_run_at=run_at,
        tbill=bills, as_of=run_at, ran_budgets=check["engine"])  # #547: the budgets the engine ran
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    cap_tag = "" if override is None else f"_cap{override:g}"
    tag = f"{BOOK}_{JUDGED}_{universe}{cap_tag}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "book": BOOK, "sleeve": SLEEVE,
           "index_cap_pct": index_cap, "prereg_sha256": {BOOK: pre_book.sha256, SLEEVE: pre_sleeve.sha256},
           "gate_sha": gate.sha,
           "holdout": holdout.stamp(None), "budgets": check, "universe": universe, "data": info,
           "window": [str(data.dates[i0]), str(data.dates[i1])], "control_runs": a.control_runs,
           "source": source, "rows": [row for _, row in runs.values()], "control": control,
           "gate_v3_sleeve": sleeve_rep, "gate_v3_book": book_rep, "runtime_s": round(time.time() - t0, 1)}
    out_dir = Path(a.out or data_dir / "reports")
    (out_dir / tag).mkdir(parents=True, exist_ok=True)
    # #456: the benchmark the book was judged against, so scripts/gate_v3_rescore.py re-scores it on the same SPY
    pd.DataFrame({"date": [str(x.date()) for x in spy.index], "ret": spy.to_numpy(dtype=float),
                  "ret_kind": source["benchmark"]}).to_csv(out_dir / tag / "spy_daily.csv", index=False)
    for label, (r, _) in runs.items():
        pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{label.replace('|', '_')}.csv", index=False)
    (out_dir / f"{tag}.json").write_text(json.dumps(out, indent=1, default=str))
    (out_dir / f"{tag}.md").write_text(g3.render_book(book_rep) + "\n" + g3.render_sleeve(sleeve_rep))
    if trials_path is not None:
        for label, (_, row) in runs.items():
            name = SLEEVE if row["part"] == "sleeve" else BOOK
            trials_mod.append(trials_path, {
                "ts": out["run_at"], "trial_id": f"{tag}_{label}", "strategy": name, "variant": "engine_simbroker",
                "label": f"engine {label}", "level": row["level"], "cost_model": core_run.COST_MODEL,
                "universe_mode": universe, "code_sha": code_sha,
                "prereg_sha256": out["prereg_sha256"][name], "gate_sha": gate.sha, "n_trials": row["n_trials"],
                "params": {"spec": row["spec"], "fill": row["fill"], "cost_multiple": row["cost"],
                           "budget_pct": BUDGET if name == BOOK else None,
                           "index_cap_pct": index_cap if name == BOOK else None,
                           "max_index_pct": row["max_index_pct"]},
                "window": out["window"],
                "metrics": {"cagr_pct": row["cagr_pct"], "daily_sharpe_ann": row["sharpe"],
                            "max_drawdown_pct": row["max_drawdown_pct"], "net_return_pct": row["return_pct"]}})
    print(f"sleeve: {sleeve_rep['verdict']}; book: {book_rep['verdict']}; report {out_dir / tag}.json", flush=True)
    out["file"] = str(out_dir / f"{tag}.json")
    return out


if __name__ == "__main__":
    main()
