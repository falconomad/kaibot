"""Ten-year test of the SPY trend filter (issue #201): hold SPY when its month-end close is above its 10-month
average, otherwise cash. Pre-registered in docs/prereg/spy_trend_filter.md (committed before this script).
Research only: reads data/daily (SPY with dividends), never touches the engine or the broker, needs no .env.
PROVISIONAL: a SPY-only research routine (kaibot.replay.spy_trend), not the engine with SimBroker.

python scripts/spy_trend.py [--start 2016-01-01] [--end 2026-11-06] [--control-runs 500] [--control-seed 201]
python scripts/spy_trend.py --has [--start ... --end ... --control-runs N]   exit 0 if that report exists
python scripts/spy_trend.py --summary      one table, written to data/reports/experiments_spy_trend_summary.md

One run does everything the pre-registration declares: the base configuration and its five neighbours, each ONE
continuous $100k account over 2017-05-01 to --end, under cost model v1 and v2 (0x, 1x, 2x, 3x of spread and impact),
the stress fill, the T-bill variant when data/daily holds BIL, the random-timing control of Gate B mark 3 per cost
model, and Gate B judged by kaibot.evaluation.gate_v2.evaluate_gate_b per cost model. Writes
data/reports/spy_trend_<ts>.json with a folder of the same name (daily CSVs, spy_daily.csv, the control's per-run
numbers, one gate_b_<model>.md / .json per cost model) and appends one trial row per run to data/trials.jsonl:
n_trials 1 for the base and for each neighbour at cost model v1, base fill, 1x; 0 for every other row.
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

from kaibot.evaluation import gate_v2  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.replay import spy_trend as st  # noqa: E402

SUMMARY = "experiments_spy_trend_summary.md"
SET = "spy_trend"
MODELS = ("v1", "v2")
MULTS = (0.0, 1.0, 2.0, 3.0)
OOS_FROM = "2017-05-01"
STATUS = ("PROVISIONAL (decision 15): SPY-only research routine on the daily store (raw SPY bars, dividends, SPY "
          "total return), cost models v1 and v2; it can fail Gate B, it cannot pass it (the go/no-go run is the "
          "engine with `SimBroker`)")
CASH_ETF = "BIL"


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()


def prereg(root: Path = ROOT) -> dict:
    """No committed pre-registration, no run (decision 15, Gate A)."""
    try:
        sha = prereg_mod.require(st.RULE, root)
    except prereg_mod.PreregError as e:
        sys.exit(f"no run: {e}")
    return {"file": str(prereg_mod.path_for(st.RULE, root).relative_to(root)), "sha256": sha}


# ---------------------------------------------------------------- data


def load(data: Path, start: str, end: str):
    """(SpyData, SPY total return per session, its note, cash returns or None, the T-bill note)."""
    from kaibot.ingest.daily_store import DailyStore
    from kaibot.replay.multiday_core import spy_total_return

    root = data / "daily"
    if not (root / "bars" / "symbol=SPY.parquet").is_file():
        sys.exit(f"no SPY in the daily store {root}: run ./scripts/fetch_daily.sh first (docs/m0/reproduce.md)")
    store = DailyStore(root)
    panel = store.panel(["SPY"], start, end)
    d = st.SpyData.from_panel(panel)
    tr, info = spy_total_return(panel, "SPY")
    cash, note = None, f"skipped: {CASH_ETF} is not in the daily store (python scripts/fetch_daily.py --symbols BIL)"
    if (root / "bars" / f"symbol={CASH_ETF}.parquet").is_file():
        try:
            p = store.panel([CASH_ETF], start, end)
            s = pd.Series(p.total_return(CASH_ETF), index=p.dates.astype("datetime64[D]"))
            cash = s.reindex(d.dates).to_numpy(dtype=float)
            note = f"{CASH_ETF} total return from the daily store"
        except (OSError, ValueError) as e:
            cash, note = None, f"skipped: {CASH_ETF} unreadable ({e})"
    return d, np.asarray(tr, dtype=float), info, cash, note


def window(d: st.SpyData, end: str) -> tuple[int, int]:
    i0, i1 = max(d.index_from(OOS_FROM), 1), d.index_upto(end)
    if i0 > i1:
        sys.exit(f"no SPY session between {OOS_FROM} and {end} in the daily store")
    return i0, i1


# ---------------------------------------------------------------- one run, in memory


def evaluate(d: st.SpyData, spy_tr: np.ndarray, i0: int, i1: int, *, cash: np.ndarray | None = None,
             control_runs: int = st.CONTROL_RUNS, control_seed: int = st.CONTROL_SEED, equity0: float = 100_000.0,
             prereg_obj=None, registry=None, first_run_at=None) -> dict:
    """Every account the pre-registration declares, the controls and Gate B per cost model (the script writes it
    out; the tests call this on synthetic bars)."""
    dates = d.dates[i0:i1 + 1]
    rows, books = [], {}
    tg = {"base": st.targets(st.states(d, st.BASE), i0, i1)}
    for k, sp in st.NEIGHBOURS.items():
        tg[k] = st.targets(st.states(d, sp), i0, i1)

    def one(name: str, kind: str, costs: str, mult: float, fill: str = "base", cash_r=None) -> dict:
        b = st.run_account(d, tg[name], i0, i1, costs=costs, mult=mult, fill=fill, equity0=equity0,
                           cash_returns=cash_r)
        spec = st.BASE if name == "base" else st.NEIGHBOURS[name]
        label = f"{spec.name}|{costs}|{fill}|{mult:g}x" + ("|tbill" if cash_r is not None else "")
        row = {"label": label, "spec": spec.name, "kind": kind, "costs": costs, "fill": fill, "mult": mult,
               "cash": "tbill" if cash_r is not None else "zero", "params": spec.params(),
               "n_trials": int(costs == "v1" and fill == "base" and mult == 1.0 and cash_r is None
                               and kind in ("base", "neighbour")),
               **st.account_stats(b)}
        rows.append(row)
        books[label] = b
        return row

    for m in MODELS:
        got = {x: one("base", "base" if x == 1.0 else "cost", m, x) for x in MULTS}
        got[1.0]["break_even"] = st.break_even(got[1.0]["net_pnl"], got[0.0]["net_pnl"])
        one("base", "stress", m, 1.0, "stress")
        for k in st.NEIGHBOURS:
            one(k, "neighbour", m, 1.0)
    if cash is not None:
        if np.isfinite(cash[i0:i1 + 1]).all():
            one("base", "tbill", "v1", 1.0, cash_r=cash)
        else:
            cash = None
    spy = pd.Series(spy_tr[i0:i1 + 1], index=pd.to_datetime(dates))
    spy_po = pd.Series(d.c[i0:i1 + 1] * d.split[i0:i1 + 1] / d.c[i0 - 1:i1] - 1, index=pd.to_datetime(dates))
    exposure = float(tg["base"].mean())
    bench = {"spy_total_return": st.series_stats(dates, spy.to_numpy()),
             "spy_price_only": st.series_stats(dates, spy_po.to_numpy()),
             "spy_tr_scaled": {**st.series_stats(dates, spy.to_numpy() * exposure),
                               "scale_pct": round(exposure * 100, 1)}}
    gate = gate_v2.GateV2.load(ROOT)
    control, gates = {}, {}
    for m in MODELS:
        base = books[f"{st.RULE}|{m}|base|1x"]
        c = None
        if control_runs > 0:
            c = st.random_timing_control(d, tg["base"], i0, i1, costs=m, runs=control_runs, seed=control_seed,
                                         equity0=equity0)
            control[m] = c
        run = gate_v2.Run(net=st.returns(base), spy=spy, costs=st.cost_share(base), random=c,
                          universe="point_in_time",
                          traded=pd.Series([r["traded"] / r["start"] for r in base.daily], index=spy.index),
                          exposure=pd.Series([r["exposure"] for r in base.daily], index=spy.index))
        nb = {k: st.returns(books[f"{st.NEIGHBOURS[k].name}|{m}|base|1x"]) for k in st.NEIGHBOURS}
        gates[m] = gate_v2.evaluate_gate_b(
            gate, rule=st.RULE, run=run, neighbours=nb, prereg=prereg_obj, registry=registry,
            first_run_at=first_run_at,
            source={"engine": "research_harness", "cost_model": m, "data_repairs": True,
                    "benchmark": "spy_total_return"})
    return {"rows": rows, "books": books, "benchmarks": bench, "control": control, "gate_b": gates,
            "gate_sha": gate.sha, "tbill": cash is not None, "exposure_base": exposure,
            "spells": st.spells(tg["base"]), "window": [str(dates[0]), str(dates[-1])], "sessions": len(dates)}


# ---------------------------------------------------------------- the script


def find_report(reports: Path, **same) -> tuple[Path, dict] | None:
    for f in sorted(reports.glob("spy_trend_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != SET or "rows" not in rep:
            continue
        if any(want is not None and rep.get(k) != want for k, want in same.items()):
            continue
        return f, rep
    return None


def trial_row(r: dict, tag: str, run_at: str, pre: dict, gate_sha: str, input_sha: str, code: str,
              window_: list) -> dict:
    safe = r["label"].replace("|", "_").replace("~", "-")
    return {"ts": run_at, "trial_id": f"{tag}_{safe}", "strategy": st.RULE, "variant": SET,
            "label": f"spy trend filter {r['label']}", "cost_model": r["costs"], "universe": "SPY",
            "universe_mode": "point_in_time", "bars": "store", "input_sha256": input_sha, "code_sha": code,
            "experiment": r["spec"], "status": "provisional", "prereg_sha256": pre["sha256"],
            "gate_v2_sha256": gate_sha, "n_trials": r["n_trials"],
            "params": {**r["params"], "costs": r["costs"], "fill": r["fill"], "cost_multiple": r["mult"],
                       "cash": r["cash"], "kind": r["kind"]},
            "window": window_,
            "metrics": {"round_trips": r["round_trips"], "net_return_pct": r["return_pct"], "cagr_pct": r["cagr_pct"],
                        "daily_sharpe_ann": r["sharpe"], "max_drawdown_pct": r["max_drawdown_pct"]}}


def run(a: argparse.Namespace) -> Path:
    t0 = time.time()
    data = Path(a.data)
    pre = prereg()
    d, tr, tr_info, cash, cash_note = load(data, a.start, a.end)
    i0, i1 = window(d, a.end)
    print(f"pre-registration {pre['file']} sha256 {pre['sha256'][:12]}; SPY {len(d.dates)} sessions, window "
          f"{d.dates[i0]} to {d.dates[i1]}; SPY total return: {tr_info.get('source')}; T-bill: {cash_note}",
          flush=True)
    if not np.isfinite(tr[i0:i1 + 1]).all():
        sys.exit("SPY total return has gaps in the window: mark 2 cannot be judged (run ./scripts/fetch_daily.sh)")
    run_at = datetime.now(UTC)
    ts = run_at.isoformat(timespec="seconds")
    tag = f"spy_trend_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    # trial rows first (the gate's Gate A check reads them: first run, logged hashes); the report is written last
    pobj = prereg_mod.load(st.RULE, ROOT)
    f_spy = data / "daily" / "bars" / "symbol=SPY.parquet"
    input_sha = hashlib.sha256(f_spy.read_bytes() + f"|{a.start}|{a.end}".encode()).hexdigest()
    code = git_sha()
    res = evaluate(d, tr, i0, i1, cash=cash, control_runs=0, prereg_obj=pobj)  # accounts only, to log the trials
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    with (data / "trials.jsonl").open("a") as fh:
        for r in res["rows"]:
            row = trial_row(r, tag, ts, pre, res["gate_sha"], input_sha, code, res["window"])
            fh.write(json.dumps(row, default=str) + "\n")
            if r["n_trials"]:
                b = res["books"][r["label"]]
                rr = st.returns(b)
                pd.DataFrame({"date": rr.index.strftime("%Y-%m-%d"), "ret": rr.to_numpy()}).to_csv(
                    tdir / f"{row['trial_id']}.csv", index=False)
    print(f"accounts done ({len(res['rows'])} runs), {time.time() - t0:.0f}s; random-timing control "
          f"{a.control_runs} runs per cost model ...", flush=True)
    registry = trials_mod.load(None, data / "trials.jsonl")
    res = evaluate(d, tr, i0, i1, cash=cash, control_runs=a.control_runs, control_seed=a.control_seed,
                   prereg_obj=pobj, registry=registry, first_run_at=run_at)
    for label, b in res["books"].items():
        st.daily_frame(b).to_csv(out / tag / f"daily_{label.replace('|', '_').replace('~', '-')}.csv", index=False)
    dates = d.dates[i0:i1 + 1].astype(str)
    pd.DataFrame({"date": dates, "ret": tr[i0:i1 + 1]}).to_csv(out / tag / "spy_daily.csv", index=False)
    for m, c in res["control"].items():
        pd.DataFrame(c.pop("per_run")).to_csv(out / tag / f"control_timing_{m}.csv", index_label="run")
    for m, g in res["gate_b"].items():
        (out / tag / f"gate_b_{m}.json").write_text(json.dumps(g, indent=1, default=str))
        (out / tag / f"gate_b_{m}.md").write_text(gate_v2.render_gate_b(g))
    rep = {"run_at": ts, "code_sha": code, "set": SET, "status": STATUS, "rule": st.RULE, "prereg": pre,
           "gate_v2_sha256": res["gate_sha"], "start": a.start, "end": a.end, "control_runs": a.control_runs,
           "control_seed": a.control_seed, "oos_window": res["window"], "oos_sessions": res["sessions"],
           "input_sha256": input_sha, "spy_total_return": tr_info, "tbill": cash_note if res["tbill"] else
           (cash_note if cash is None else f"skipped: {CASH_ETF} has gaps in the window"),
           "rows": res["rows"], "benchmarks": res["benchmarks"], "control": res["control"],
           "gate_b": {m: {"verdict": g["verdict"], "marks": g["marks"], "provisional_reasons": g["provisional_reasons"]}
                      for m, g in res["gate_b"].items()},
           "spells": res["spells"], "trips": res["books"][f"{st.RULE}|v1|base|1x"].trips,
           "runtime_s": round(time.time() - t0, 1)}
    f = out / f"{tag}.json"
    f.write_text(json.dumps(gate_v2._clean(rep), indent=1, default=str))
    print(build_summary(f, json.loads(f.read_text())))
    print(f"runtime {rep['runtime_s']}s")
    return f


# ---------------------------------------------------------------- the one table

HEAD = ("Rule / version", "Costs", "Fill", "Cash", "Total return %", "CAGR %", "Sharpe", "Sharpe x 0.5",
        "Max drawdown %")
TAIL = ("Positive years", "Sessions in %", "Round trips", "Avg holding sessions", "Turnover %/yr", "Cost bp/yr",
        "Break-even cost x")
MARK_TEXT = {"mark_1": "net return positive overall and in at least 6 of 10 calendar years",
             "mark_2": "CAGR >= SPY total return - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 "
                       "point with max drawdown <= 1.1 x SPY's",
             "mark_3": "net Sharpe at or above the 90th percentile of >= 500 random-timing accounts (section 10)",
             "mark_4": "marks 1-2 still hold at 2x costs",
             "mark_5": "holds on the point-in-time universe (SPY only: this run)",
             "mark_6": "every pre-declared neighbour passes mark 1"}


def _cells(name: str, s: dict, years: list[str], **extra) -> dict:
    out = {"Rule / version": name, "Total return %": s["return_pct"], "CAGR %": s["cagr_pct"], "Sharpe": s["sharpe"],
           "Sharpe x 0.5": s.get("sharpe_haircut"), "Max drawdown %": s["max_drawdown_pct"],
           "Positive years": f"{s['positive_years']} of {s['n_years']}"}
    out.update({y: s["years"].get(y) for y in years})
    out.update(extra)
    return out


def _pct(x) -> str:
    return "n/a" if x is None or isinstance(x, str) else f"{x * 100:.2f}%"


def _mult(x) -> str:
    return f"{x:.1f}x" if isinstance(x, int | float) else str(x)


def mark_line(key: str, m: dict) -> str:
    state = {True: "pass", False: "FAIL", None: "unknown (counts as not met)"}[m["met"]]
    n = m.get("numbers") or {}
    if key == "mark_1":
        det = f"net return {_pct(n.get('total_return'))}, {n.get('positive_years')} of {n.get('years')} years positive"
    elif key == "mark_2":
        det = (f"CAGR {_pct(n.get('cagr'))} against SPY total return {_pct(n.get('spy_cagr'))} "
               f"({n.get('cagr_vs_spy_points', 0):+.2f} points); max drawdown {_pct(n.get('max_drawdown'))} = "
               f"{n.get('max_drawdown_vs_spy', 0):.2f} x SPY's {_pct(n.get('spy_max_drawdown'))}")
    elif key == "mark_3":
        pct = n.get("percentile")
        det = (f"net Sharpe {n.get('strategy_sharpe')} at percentile "
               f"{'n/a' if pct is None else round(float(pct), 1)} of {n.get('runs')} "
               f"random-timing accounts (median {n.get('random_median')}, 90th {n.get('random_p90')}); "
               f"matched {n.get('matched')}")
    elif key == "mark_4":
        det = (f"at 2x: mark 1 {n.get('mark_1')}, mark 2 {n.get('mark_2')}; CAGR {_pct(n.get('cagr'))}, max drawdown "
               f"{_pct(n.get('max_drawdown'))}; break-even {_mult(n.get('breakeven_multiple'))} the daily costs")
    elif key == "mark_5":
        det = f"source: {n.get('source')}"
    else:
        det = ", ".join(f"{k} {'pass' if v else 'FAIL' if v is False else 'unknown'}"
                        for k, v in (n.get("results") or {}).items())
    why = f" ({m['why']})" if m.get("why") else ""
    return f"- Mark {key[-1]} ({MARK_TEXT[key]}): **{state}**. {det}{why}."


def build_summary(f: Path, rep: dict) -> str:
    rows = [r for r in rep["rows"] if r["mult"] != 0.0]
    years = sorted({y for r in rep["rows"] for y in r["years"]})
    cols = (*HEAD, *years, *TAIL)
    order = {"base": 0, "cost": 1, "stress": 2, "tbill": 3, "neighbour": 4}
    rows.sort(key=lambda r: (r["costs"], order[r["kind"]], r["spec"], r["mult"]))
    table = []
    for r in rows:
        be = r.get("break_even")
        name = r["spec"].replace("~", " ~ neighbour ")
        table.append(_cells(name, r, years, **{
            "Costs": f"{r['costs']} {r['mult']:g}x", "Fill": r["fill"], "Cash": {"zero": "0%", "tbill": "BIL"}[
                r["cash"]], "Sessions in %": r["sessions_in_pct"], "Round trips": r["round_trips"],
            "Avg holding sessions": r["avg_hold_sessions"], "Turnover %/yr": r["turnover_pct_per_year"],
            "Cost bp/yr": r["cost_bp_per_year"],
            "Break-even cost x": None if not be else (be["multiple"] if be["multiple"] is not None else be["note"])}))
    b = rep["benchmarks"]
    table.append(_cells("BENCHMARK: SPY buy-and-hold (total return, dividends reinvested)", b["spy_total_return"],
                        years, **{"Sessions in %": 100.0}))
    table.append(_cells("BENCHMARK: SPY buy-and-hold (price only, no dividends)", b["spy_price_only"], years,
                        **{"Sessions in %": 100.0}))
    sc = b["spy_tr_scaled"]
    table.append(_cells(f"BENCHMARK: SPY (total return) at the rule's time in the market ({sc['scale_pct']}%), rest "
                        "in cash", sc, years, **{"Sessions in %": sc["scale_pct"]}))
    for m, c in rep["control"].items():
        table.append({"Rule / version": f"BENCHMARK: random-timing accounts, median of {c['runs']} (same round "
                                        "trips and holding durations)", "Costs": f"{m} 1x", "Fill": "base",
                      "Cash": "0%", "Total return %": c["random_return_pct_median"], "Sharpe": c["random_median"],
                      "Round trips": c["round_trips"]})
    w = rep["oos_window"]
    out = [f"# SPY trend filter (`{rep['rule']}`), ten-year test (issue #201)", "",
           f"**{rep['status']}.**", "",
           f"Out-of-sample {w[0]} to {w[1]} ({rep['oos_sessions']} sessions). Each row is ONE continuous $100k account "
           "of a frozen, pre-registered configuration, starting in cash; nothing is tuned. Signal: SPY's split-"
           "adjusted close; holdings and costs on raw prices with dividends credited and reinvested at the ex-date "
           "close; cash earns 0 (the `BIL` row, when present, earns the T-bill ETF's total return and is not "
           "judged).", "",
           "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if r.get(c) is None else str(r[c]) for c in cols) + " |" for r in table]
    out += ["", "How to read it: fill `base` = decided after the close of the check session, bought or sold at the "
            "next session's close; `stress` = the next open with spread and impact doubled (v1) or the opening "
            "auction (v2). `2x` / `3x` multiply spread and impact, never fees. Sharpe is annualised from daily "
            "mark-to-market returns with no risk-free rate, its 50% haircut beside it. Max drawdown is "
            "mark-to-market at the close. Round trips include a position still open at the end. Break-even is the "
            "multiple of spread and impact at which net profit reaches zero (0x and 1x runs).", "",
            "## Gate B marks (decision 15, sections 7 and 10), base configuration", ""]
    for m, g in rep["gate_b"].items():
        out += [f"### Cost model {m}", ""]
        out += [mark_line(k, g["marks"][k]) for k in MARK_TEXT]
        failed = [k[-1] for k in MARK_TEXT if g["marks"][k]["met"] is False]
        verdict = (f"fails Gate B (mark {', '.join(failed)})" if failed else
                   f"verdict `{g['verdict']}`: it cannot pass here")
        out += [f"- Overall: {verdict}; " + "; ".join(g["provisional_reasons"]) + ".", ""]
    c1 = (rep["control"].get("v1") or {})
    tr = rep.get("spy_total_return") or {}
    out += ["## Notes", "",
            f"- Pre-registration `{rep['prereg']['file']}` (sha256 `{rep['prereg']['sha256'][:12]}`); "
            f"config/gate_v2.yaml sha256 `{rep['gate_v2_sha256'][:12]}`; code `{rep['code_sha']}`; report "
            f"`{f.name}`.",
            f"- SPY total return: {tr.get('source')}"
            + (f" ({tr['points_per_year_over_price']} points a year over price only)"
               if tr.get("points_per_year_over_price") is not None else "") + ".",
            f"- T-bill variant: {rep['tbill']}.",
            f"- Random-timing control: {rep['control_runs']} runs per cost model, seed ({rep['control_seed']}, k); "
            f"{c1.get('round_trips')} round trips, durations {c1.get('durations_sessions')} sessions; per-run "
            "numbers in control_timing_<model>.csv. Trades of the base account (v1) are in the report JSON "
            "(`trips`); the full gate v2 reports in gate_b_<model>.md.",
            "- Trials: the base and each neighbour at cost model v1, base fill, 1x count one configuration each "
            "(data/trials.jsonl, `python scripts/trial_registry.py count`); every other row counts 0."]
    return "\n".join(out) + "\n"


def summary(data: Path, **same) -> str:
    hit = find_report(data / "reports", **same)
    if hit is None:
        return "No spy_trend report yet: run ./scripts/run_experiments.sh spy_trend\n"
    text = build_summary(*hit)
    (data / "reports" / SUMMARY).write_text(text)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-11-06")
    ap.add_argument("--control-runs", type=int, default=st.CONTROL_RUNS)
    ap.add_argument("--control-seed", type=int, default=st.CONTROL_SEED)
    ap.add_argument("--has", action="store_true", help="exit 0 if a report for these dates and control runs exists")
    ap.add_argument("--summary", action="store_true", help="print and write the one table")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if a.has:
        return 0 if find_report(data / "reports", start=a.start, end=a.end, control_runs=a.control_runs) else 1
    if a.summary:
        print(summary(data))
        return 0
    run(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
