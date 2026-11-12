"""Ten-year test of the SPY noise-band rule, long only, 1x, and its overnight carry (issue #128, decision 15 rules 3
and 4). Research only: reads data/bars_1m/symbol=SPY, never touches the engine or the broker. PROVISIONAL: a small
SPY-only research routine (kaibot.replay.spy_noise_band) with the cost functions of kaibot.replay.sim, not the
engine with SimBroker.

python scripts/spy_noise_band.py --version spy_noise_band|spy_noise_band_carry --end 2026-11-06 [--start 2016-01-01]
python scripts/spy_noise_band.py --has <version> --start ... --end ...     exit 0 if that version has a report
python scripts/spy_noise_band.py --summary     one table, written to data/reports/experiments_spy_summary.md
    [--control-runs 500] [--control-seed 149]   Gate B mark 3: the random-timing control (issue #149)

Each version is ONE continuous $100k account from the first test month (2017-05 with the default start) to --end;
nothing is tuned. Cost scenarios 0, 0.5, 1, 2 bp a side and cost model v1; version 1 adds its five neighbours,
its 15:30-check round trips and a replay of the live last-30-minutes SPY trade; version 2 adds carry nights
against other nights. Writes data/reports/spy_<level>_<version>_<ts>.json, trades and daily CSVs in the folder of
the same name, and one trial row per run in data/trials.jsonl (n_trials 1 for each configuration at 0.5 bp a side).

Gate B mark 3 for these SPY-only rules (decision 15 section 10, issue #149; prereg amendment 1): each version at
0.5 bp a side against --control-runs (default 500) seeded random-timing accounts with the same number of SPY round
trips, the rule's own holding durations, entries at the rule's check times and the same costs
(`spy_noise_band.random_timing_control`). The control is a report of the configuration, not a trial. Its per-run
numbers go to control_timing.csv in the report folder; the verdict is printed under the table.
"""

from __future__ import annotations

import argparse
import hashlib
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
from walk_forward import peak_rss_mb  # noqa: E402

from kaibot.evaluation.gate_v2 import GateV2, mark_3  # noqa: E402
from kaibot.replay import spy_momentum  # noqa: E402
from kaibot.replay import spy_noise_band as nb  # noqa: E402
from kaibot.replay.data import SymbolDays, load_symbol  # noqa: E402
from kaibot.replay.walkforward import WARMUP_DAYS, first_train_month, make_folds  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

STATUS = ("PROVISIONAL (decision 15): SPY-only research routine with cost model v1 functions, price-only SPY, "
          "the close is the last regular-hours minute; go/no-go numbers come only from the engine with SimBroker")
SUMMARY = "experiments_spy_summary.md"
PREREG = ROOT / "docs" / "prereg" / "spy_noise_band.md"


def prereg() -> dict:
    if not PREREG.is_file():
        sys.exit(f"no pre-registration at {PREREG}: a rule needs one before its first run (decision 15)")
    return {"file": str(PREREG.relative_to(ROOT)), "sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest()}


def cost_label(cost) -> str:
    return "0 (gross)" if cost == 0 else ("model v1" if cost == "v1" else f"{cost:g} bp")


def find_report(reports: Path, level: str, version: str, **same) -> tuple[Path, dict] | None:
    for f in sorted(reports.glob(f"spy_{level}_{version}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != "spy" or rep.get("version") != version or "rows" not in rep:
            continue
        if any(want is not None and rep.get(k) != want for k, want in same.items()):
            continue
        return f, rep
    return None


def window(spy: SymbolDays, start: str, end: str) -> tuple[int, int]:
    folds = make_folds(first_train_month(start, WARMUP_DAYS), np.datetime64(end, "D"))
    if not folds:
        sys.exit("the range is shorter than 12 months of history plus one test month")
    i0 = int(np.searchsorted(spy.dates, folds[0].test_start))
    i1 = int(np.searchsorted(spy.dates, np.datetime64(end, "D"), side="right")) - 1
    if i0 > i1 or i0 >= len(spy.dates):
        sys.exit(f"no SPY session in the test window starting {folds[0].test_month}")
    return i0, i1


def timing_mark(spy: SymbolDays, spec: nb.Spec, trips, i0: int, i1: int, runs: int, seed: int,
                equity0: float) -> dict:
    """Gate B mark 3 (decision 15 section 10) from the random-timing control at the base cost, judged by
    `gate_v2.mark_3` against config/gate_v2.yaml (90th percentile, at least 500 runs)."""
    gate = GateV2.load(ROOT)
    if runs < 1:
        return {"control": None, "mark": mark_3(gate, None), "gate_sha": gate.sha}
    try:
        ctl = nb.random_timing_control(spy, spec, trips, i0, i1, nb.BASE_COST, runs=runs, seed=seed,
                                       equity0=equity0)
    except ValueError as e:  # no round trip to match
        m = mark_3(gate, None)
        m["why"] = str(e)
        return {"control": None, "mark": m, "gate_sha": gate.sha}
    return {"control": ctl, "mark": mark_3(gate, ctl), "gate_sha": gate.sha}


def evaluate(spy: SymbolDays, version: str, i0: int, i1: int, equity0: float = 100_000.0, level: str = "balanced",
             neighbours: bool = True, control_runs: int = nb.CONTROL_RUNS, control_seed: int = nb.CONTROL_SEED,
             ) -> dict:
    """Every run of one version, in memory (the script writes it out; the tests call this on synthetic bars)."""
    spec = nb.VERSIONS[version]
    runs: dict[str, tuple[nb.Book, dict, dict]] = {}

    def one(sp: nb.Spec, cost, kind: str, trips=None) -> dict:
        trips = nb.signals(spy, sp, i0, i1) if trips is None else trips
        book = nb.run_account(spy, trips, i0, i1, cost, equity0)
        st = nb.account_stats(book)
        row = {"label": f"{sp.name}|{cost_label(cost)}", "spec": sp.name, "cost": cost_label(cost), "kind": kind,
               "params": sp.params(), **st}
        runs[row["label"]] = (book, row, {"trips": trips})
        return row

    trips = nb.signals(spy, spec, i0, i1)
    rows = [one(spec, c, "grid" if c else "gross", trips) for c in (0, *nb.COSTS)]
    by_cost = {str(c): r for c, r in zip(("0", *map(str, nb.COSTS)), rows, strict=True)}
    be = nb.break_even(np.array([t["gross_ret"] for t in runs[rows[0]["label"]][0].trades]))
    for r in rows:
        r["break_even"] = be
    out: dict = {"rows": rows, "runs": runs, "break_even": be,
                 "timing_control": timing_mark(spy, spec, trips, i0, i1, control_runs, control_seed, equity0)}
    if version == "spy_noise_band":
        nbr = {sp.name.split("~")[1]: one(sp, nb.BASE_COST, "neighbour") for sp in nb.NEIGHBOURS} if neighbours \
            else {}
        rows += nbr.values()
        marks = nb.marks_v1(by_cost, nbr)
        sub = {}
        for c in (nb.BASE_COST, nb.MARK2_COST):
            book = runs[f"{spec.name}|{cost_label(c)}"][0]
            sub[c] = nb.trip_stats(t for t in book.trades if t["decide_m"] == nb.CHECK_1530_M)
        live_trades, live_daily = spy_momentum.run(spy, spy.dates[i0], spy.dates[i1], load_profiles()[level], equity0)
        lt = [{"net_ret": t["pnl"] / (t["qty"] * t["entry"]), "gross_ret": np.nan} for t in live_trades]
        ld = pd.DataFrame(live_daily)
        live = {**nb.trip_stats(lt), **nb.series_stats(ld["date"].to_numpy(), (ld["pnl"] / ld["start"]).to_numpy()),
                "days_invested_pct": round(float((ld["trades"] > 0).mean()) * 100, 1)}
        live["gross_bp_per_trip"] = None
        out.update(marks=marks, check_1530=sub, live_trade=live,
                   live_statement=nb.live_trade_statement(marks, sub[nb.BASE_COST], sub[nb.MARK2_COST]))
    else:
        nights = nb.carry_nights(spy, trips, i0, i1)
        base = nb.account_stats(nb.run_account(spy, nb.signals(spy, nb.BASE, i0, i1), i0, i1, nb.BASE_COST, equity0))
        out.update(marks=nb.marks_carry(nights), nights=nights,
                   carry_adds_cagr_points=round(by_cost[str(nb.BASE_COST)]["cagr_pct"] - base["cagr_pct"], 2),
                   base_cagr_pct=base["cagr_pct"])
    d, r = nb.spy_buy_and_hold(spy, i0, i1)
    out["spy"] = {**nb.series_stats(d, r), "window_2022_23_pct": nb.window_return(d, r)}
    return out


def run_version(a: argparse.Namespace) -> None:
    t0 = time.time()
    pre = prereg()
    data = Path(a.data)
    spy = load_symbol(data, a.symbol, np.datetime64(a.start, "D"), np.datetime64(a.end, "D"))
    if spy is None:
        sys.exit(f"no {a.symbol} bars under {data / 'bars_1m'}: run scripts/fetch_history.sh first")
    print(f"{a.symbol}: {len(spy.dates)} sessions loaded, {time.time() - t0:.0f}s", flush=True)
    i0, i1 = window(spy, a.start, a.end)
    res = evaluate(spy, a.version, i0, i1, a.equity, a.level, control_runs=a.control_runs,
                   control_seed=a.control_seed)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    tag = f"spy_{a.level}_{a.version}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    (data / "trials").mkdir(parents=True, exist_ok=True)
    ctl = res["timing_control"]["control"]
    if ctl is not None:
        pd.DataFrame(ctl.pop("per_run")).rename_axis("run").to_csv(out / tag / "control_timing.csv")
        ctl["file"] = f"{tag}/control_timing.csv"
    trials = []
    for label, (book, row, _) in res["runs"].items():
        safe = label.replace("|", "_").replace("~", "-").replace(" ", "")
        pd.DataFrame(book.trades).to_csv(out / tag / f"trades_{safe}.csv", index=False)
        daily = pd.DataFrame(book.daily)
        daily.to_csv(out / tag / f"daily_{safe}.csv", index=False)
        row["files"] = {"trades": f"{tag}/trades_{safe}.csv", "daily": f"{tag}/daily_{safe}.csv"}
        tid = f"{tag}_{safe}"
        pd.DataFrame({"date": daily["date"].astype(str), "ret": daily["pnl"] / daily["start"]}).to_csv(
            data / "trials" / f"{tid}.csv", index=False)
        trials.append({
            "ts": run_at.isoformat(timespec="seconds"), "trial_id": tid, "strategy": row["spec"].split("~")[0],
            "variant": "spy_noise_band", "label": f"spy noise band {label}", "level": a.level,
            "code_sha": code_sha, "experiment": a.version, "status": "provisional", "prereg_sha256": pre["sha256"],
            "n_trials": int(row["cost"] == cost_label(nb.BASE_COST)),
            "params": {**row["params"], "cost": row["cost"], "kind": row["kind"]},
            "window": [str(spy.dates[i0]), str(spy.dates[i1])],
            "metrics": {k: row[k] for k in ("trades", "net_bp_per_trip", "t_net", "return_pct", "cagr_pct", "sharpe",
                                            "max_drawdown_pct")}})
    rep = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "set": "spy", "status": STATUS,
           "version": a.version, "symbol": a.symbol, "prereg": pre, "level": a.level, "start": a.start,
           "end": a.end, "equity": a.equity, "control_runs": a.control_runs, "control_seed": a.control_seed,
           "oos_window": [str(spy.dates[i0]), str(spy.dates[i1])],
           "oos_sessions": int(i1 - i0 + 1),
           **{k: v for k, v in res.items() if k != "runs"},
           "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak_rss_mb(), 0),
           "caveats": ["price-only SPY (split-adjusted, no dividends), for the rule and for buy-and-hold",
                       "the close is the last regular-hours 1-minute bar, not the closing auction",
                       "fixed bp scenarios are all-in per side; model v1 is the uncalibrated cost model v1 (S71)",
                       "a small SPY-only research routine, not the engine with SimBroker (decision 15)"]}
    with (data / "trials.jsonl").open("a") as fh:
        for t in trials:
            fh.write(json.dumps(t, default=str) + "\n")
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))  # last: marks the version finished
    print(build_summary({a.version: (out / f"{tag}.json", json.loads(json.dumps(rep, default=str)))}))
    print(f"runtime {rep['runtime_s']}s, peak RSS {rep['peak_rss_mb']} MB")


# ---------------------------------------------------------------- the one table

HEAD = ("Rule / version", "Costs a side", "Round trips", "Net bp / trip", "t", "Gross bp / trip", "Hit rate",
        "Total return %", "CAGR %", "Sharpe", "Sharpe x 0.5", "Max drawdown %", "Days invested %", "Positive years")
TAIL = ("Jun 2022-Jul 2023 %", "Break-even bp a side (per trip / account)")


def _row(name: str, s: dict, years: list[str], **extra) -> dict:
    out = {"Rule / version": name, "Round trips": s.get("trades"), "Net bp / trip": s.get("net_bp_per_trip"),
           "t": s.get("t_net"), "Gross bp / trip": s.get("gross_bp_per_trip"), "Hit rate": s.get("hit_rate"),
           "Total return %": s.get("return_pct"), "CAGR %": s.get("cagr_pct"), "Sharpe": s.get("sharpe"),
           "Sharpe x 0.5": s.get("sharpe_haircut"), "Max drawdown %": s.get("max_drawdown_pct"),
           "Days invested %": s.get("days_invested_pct"),
           "Positive years": f"{s['positive_years']} of {s['n_years']}" if "positive_years" in s else None,
           "Jun 2022-Jul 2023 %": s.get("window_2022_23_pct")}
    out.update({y: s.get("years", {}).get(y) for y in years})
    out.update(extra)
    return out


def build_summary(found: dict[str, tuple[Path, dict]], missing: tuple[str, ...] = ()) -> str:
    years = sorted({y for _, rep in found.values() for r in rep["rows"] for y in r["years"]})
    cols = (*HEAD, *years, *TAIL)
    table, marks, notes, windows, spy, mark3 = [], [], [], set(), None, []
    for version, (f, rep) in found.items():
        windows.add(tuple(rep["oos_window"]))
        be = rep["break_even"]
        for r in rep["rows"]:
            name = r["spec"].replace("~", " ~ neighbour ")
            extra = {"Costs a side": r["cost"]}
            if r["kind"] == "gross":
                extra["Break-even bp a side (per trip / account)"] = f"{be['per_trip_bp']} / {be['account_bp']}"
            table.append(_row(name, r, years, **extra))
        if version == "spy_noise_band":
            for c, s in rep["check_1530"].items():
                table.append(_row("context: spy_noise_band round trips entered at the 15:30 check", s, years,
                                  **{"Costs a side": f"{float(c):g} bp", "Total return %": None, "CAGR %": None,
                                     "Sharpe": None, "Sharpe x 0.5": None, "Max drawdown %": None,
                                     "Jun 2022-Jul 2023 %": None,
                                     **{y: None for y in years}, "Positive years": None}))
            table.append(_row("context: live last-30-minutes SPY trade (S13 rules, gate sizing)", rep["live_trade"],
                              years, **{"Costs a side": "model v1"}))
            st = rep["live_statement"]
            notes.append(f"- **Live last-30-minutes SPY trade: {st['verdict'].upper()}** ({st['why']}; prereg "
                         "section 6, PROVISIONAL; a recommendation to Product, nothing live changes here).")
        else:
            n = rep["nights"]
            notes.append(f"- Carry nights (long at the close, SPY close to next open, gross): {n['carry_nights']} "
                         f"nights, mean {n['carry_mean_bp']} bp (t {n['carry_t']}); other nights "
                         f"{n['other_nights']}, mean {n['other_mean_bp']} bp (t {n['other_t']}); difference "
                         f"{n['diff_bp']} bp, Welch t {n['welch_t']}. June 2022-July 2023: "
                         f"{n['carry_nights_2022_23_pct']}% over {n['carry_nights_2022_23']} carry nights. The carry "
                         f"adds {rep['carry_adds_cagr_points']} CAGR points at 0.5 bp a side (expected about +2).")
        mark3.append(mark3_line(version, rep.get("timing_control")))
        marks += [f"### `{version}` (0.5 bp a side unless stated)", ""]
        marks += [f"- Mark {m['mark']} ({m['text']}): **{m['verdict']}**. {m['detail']}." for m in rep["marks"]]
        marks += ["- Overall: PROVISIONAL; a pass here only earns the engine run with `SimBroker`.", ""]
        notes.append(f"- `{version}`: pre-registration `{rep['prereg']['file']}` (sha256 "
                     f"`{rep['prereg']['sha256'][:12]}`); code `{rep['code_sha']}`; report `{f.name}`; runtime "
                     f"{rep['runtime_s']}s.")
        spy = spy or rep["spy"]
    if spy:
        table.append(_row("BENCHMARK: SPY buy-and-hold (price only, no dividends)", spy, years,
                          **{"Costs a side": "none", "Days invested %": 100.0}))
    out = ["# SPY noise band, long only, 1x, and the overnight carry: ten-year test (issue #128)", "",
           f"**{STATUS}.**", ""]
    if windows:
        w = sorted(windows)[0]
        out += [f"Out-of-sample {w[0]} to {w[1]}. Each row is ONE continuous $100k account of one frozen, "
                "pre-registered configuration (`docs/prereg/spy_noise_band.md`); nothing is tuned.", ""]
        if len(windows) > 1:
            out += ["**The versions were run over different windows: rerun them with the same dates.**", ""]
    out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if r.get(c) is None else str(r[c]) for c in cols) + " |" for r in table]
    out += ["",
            "How to read it: costs are all-in per side on every buy and sell (0.5 / 1 / 2 bp), or cost model v1 "
            "(half spread of at least 1 bp plus impact, plus fees). Net and gross bp are per round trip (a carry "
            "counts once), t is the mean over its standard error. Returns, Sharpe (annualised, no risk-free rate, "
            "50% haircut beside it) and drawdown are daily mark-to-market. Break-even: the cost a side at which the "
            "mean round trip (half the gross mean) and the account's total return reach zero. Days invested: "
            "sessions with any position. Context rows are not configurations.", "",
            "## Gate B mark 3: random-timing control (decision 15 section 10, issue #149)", "", *mark3, "",
            "Each random account makes the rule's number of SPY round trips with the rule's own holding durations "
            "(in a random order), entered at random sessions and check times (:00/:30 from 10:00), one at a time and "
            "fully invested when long, at the same 0.5 bp a side. Percentile: share of random accounts strictly "
            "below the rule (ties count against it). The mark needs the net Sharpe at or above the 90th percentile "
            "over at least 500 runs. PROVISIONAL like every verdict here.", "",
            "## Pass marks (issue #128, pre-registered)", "", *marks, "## Notes", "", *notes]
    if missing:
        out += ["", f"Not run yet: {', '.join(missing)}."]
    return "\n".join(out) + "\n"


def mark3_line(version: str, tc: dict | None) -> str:
    if not tc:
        return f"- `{version}`: **not run** (the report predates the control: run this version again)."
    m, c = tc["mark"], tc.get("control")
    verdict = "cannot be judged" if m["met"] is None else ("pass" if m["met"] else "fail")
    if c is None:
        return f"- `{version}`: **{verdict}** ({m['why']})."
    return (f"- `{version}`: **{verdict}**. Net Sharpe {c['strategy_sharpe']:.2f} at the {c['percentile']:.1f}th "
            f"percentile of {c['runs']} random-timing runs (random median {c['random_median']:.2f}, 90th "
            f"{c['random_p90']:.2f}); total return {c['strategy_return_pct']}% at the {c['return_percentile']:.1f}th "
            f"percentile (random median {c['random_return_pct_median']}%). {c['round_trips']} round trips"
            + (f" ({c['carried_trips']} carried overnight)" if c["carried_trips"] else "")
            + f", median hold {c['median_hold_min']:g} min, seed {c['seed']}"
            + ("" if c["matched"] else f", NOT matched: {c['unfilled']} trips could not be placed")
            + (f"; {m['why']}" if m["why"] else "") + ".")


def summary(data: Path, level: str) -> str:
    found, missing = {}, []
    for version in nb.VERSIONS:
        hit = find_report(data / "reports", level, version)
        if hit is None:
            missing.append(version)
        else:
            found[version] = hit
    return build_summary(found, tuple(missing))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=list(nb.VERSIONS))
    ap.add_argument("--has", default="", choices=["", *nb.VERSIONS], help="exit 0 if this version has a report")
    ap.add_argument("--summary", action="store_true", help="print the one table and write it")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--start", default="2016-01-01", help="first date with bars")
    ap.add_argument("--end", help="last session (inclusive)")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--control-runs", type=int, default=nb.CONTROL_RUNS,
                    help="random-timing runs for Gate B mark 3 (needs 500; 0 switches the control off)")
    ap.add_argument("--control-seed", type=int, default=nb.CONTROL_SEED)
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        sys.exit(0 if find_report(data / "reports", a.level, a.has, start=a.start, end=a.end,
                                  control_runs=a.control_runs, control_seed=a.control_seed) else 1)
    if a.summary:
        text = summary(data, a.level)
        (data / "reports").mkdir(parents=True, exist_ok=True)
        (data / "reports" / SUMMARY).write_text(text)
        print(text)
        return
    if not a.version or not a.end:
        ap.error("--version and --end are required for a run")
    run_version(a)


if __name__ == "__main__":
    main()
