"""Ten-year test of the momentum core (issue #127, decision 15 rules 1 and 2): `trend_momentum` and
`mom_12_1_band` (with and without the index filter) in the multi-day research harness of #113. Research only:
reads the history under data/, never touches the engine or the broker. PROVISIONAL: today's stock list, cost
model v1, price-only SPY; cannot pass Gate B until S71, S83 and S84 have landed.

python scripts/momentum_core.py --version trend_momentum|mom_12_1_band|mom_12_1_band_nofilter --end 2026-11-06
    [--universe config/universe_history.yaml] [--start 2016-01-01] [--level balanced] [--control-runs 200]
    [--earnings auto|off] [--refresh-daily]
python scripts/momentum_core.py --has <version> --start ... --end ... --universe ... --control-runs N   exit 0 if done
python scripts/momentum_core.py --summary     one table, written to data/reports/experiments_core_summary.md

Each frozen configuration is ONE continuous $100k account from the first test month (2017-05 with the default
start) to --end, starting flat: no walk-forward tuning. The grid is a set of reports of one frozen rule, declared
in docs/prereg/: fill model (base: next close; stress: next open at doubled spread and impact) x costs (1x, 2x,
3x, and 0x to state the break-even multiple) x exposure cap (30 / 60 / 100%) x earnings (hold through; exit before =
approximate, skipped when the estimate is unreliable), then the five neighbours and the matched random control.

Writes data/reports/core_<level>_<version>_<ts>.json, one trades and one daily CSV per run under
data/reports/core_<level>_<version>_<ts>/, and one trial row per run in data/trials.jsonl. A run with the base
fill at 1x costs counts as one configuration (n_trials 1); its cost and fill stress reports count 0.
The daily bars come from data/multiday/daily_bars.parquet (built from data/bars_1m when missing or stale).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import walk_forward_multiday as wfm  # noqa: E402
from walk_forward import peak_rss_mb  # noqa: E402

from kaibot.replay import multiday_core as mc  # noqa: E402
from kaibot.replay.multiday import (  # noqa: E402
    MTrade,
    bars_fingerprint,
    equal_weight_benchmark,
    indicators,
    spy_buy_and_hold,
)
from kaibot.replay.multiday_rules import book_digest  # noqa: E402
from kaibot.replay.walkforward import WARMUP_DAYS, first_train_month, make_folds  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

STATUS = ("PROVISIONAL (decision 15): research harness of #113 on today's stock list, cost model v1, price-only "
          "SPY; cannot pass Gate B until S71, S83 and S84 have landed and the test is rerun")
SURVIVORSHIP = ("**Survivorship bias:** the universe is today's 300 large caps applied back to 2016, so every rule "
                "that buys strength in these stocks is flattered by hindsight; read each result against the "
                "equal-weight universe and the matched random entries, not against zero.")
PROVISIONAL = ("**PROVISIONAL (decision 15):** today's stock list, cost model v1 and a price-only SPY. Nothing here "
               "is a Gate B verdict until S71 (cost model v2), S83 (point-in-time universe) and S84 (total "
               "return) have landed and the test has been rerun; go/no-go numbers come only from the engine with "
               "`SimBroker`.")
NEED_HISTORY = 252 + 21  # sessions before the first test session for every signal to exist
SUMMARY = "experiments_core_summary.md"


def prereg(rule: str) -> dict:
    """The pre-registration a run belongs to. No pre-registration, no run (decision 15, Gate A)."""
    f = ROOT / "docs" / "prereg" / f"{rule}.md"
    if not f.is_file():
        sys.exit(f"no pre-registration at {f}: a rule needs one before its first run (decision 15)")
    return {"file": str(f.relative_to(ROOT)), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}


def run_label(spec_name: str, fill: str, cost: float, exposure_pct: float, earnings: str) -> str:
    return f"{spec_name}|{fill}|{cost:g}x|cap{exposure_pct:g}|{earnings}"


def find_report(reports: Path, level: str, version: str, **same) -> tuple[Path, dict] | None:
    """Newest finished report of `version` (and, when given, with the same start, end, universe, control runs)."""
    for f in sorted(reports.glob(f"core_{level}_{version}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != "core" or rep.get("version") != version or "rows" not in rep:
            continue
        if any(want is not None and rep.get(k) != want for k, want in same.items()):
            continue
        return f, rep
    return None


# ---------------------------------------------------------------- one version


def run_version(a: argparse.Namespace) -> None:
    t0 = time.time()
    data, spec = Path(a.data), mc.VERSIONS[a.version]
    pre = prereg(spec.rule)
    cfg = yaml.safe_load(Path(a.universe).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    spy = cfg["market"]
    symbols = [*stocks, *cfg["sectors"], spy]  # the same list as the multiday set, so its daily-bar cache is reused
    pol = load_profiles()[a.level]

    def progress(n: int, s: str) -> None:
        if n % 25 == 0:
            print(f"  daily bars: {n} of {len(symbols)} symbols ({s}), {time.time() - t0:.0f}s", flush=True)

    bars, built = wfm.load_or_build_daily(data, symbols, a.start, a.end, refresh=a.refresh_daily, progress=progress)
    if not np.isfinite(bars.c[:, bars.col(spy)]).any():
        sys.exit(f"no {spy} bars under {data / 'bars_1m'} between {a.start} and {a.end}: nothing to run on")
    bars = bars.keep_sessions(np.isfinite(bars.c[:, bars.col(spy)]))  # the session calendar is SPY's
    print(f"daily bars: {len(bars.dates)} sessions x {len(bars.symbols)} symbols "
          f"({'built and cached' if built else 'from the cache'}), {time.time() - t0:.0f}s", flush=True)
    folds = make_folds(first_train_month(a.start, WARMUP_DAYS), np.datetime64(a.end, "D"))
    if not folds:
        sys.exit("the range is shorter than 12 months of history plus one test month")
    i0, i1 = max(bars.index_from(folds[0].test_start), 1), bars.index_upto(np.datetime64(a.end, "D"))
    if i0 > i1:
        sys.exit(f"no session in the test window: the bars end {bars.dates[-1]}, before {folds[0].test_month}")
    warn = []
    if i0 < NEED_HISTORY:
        warn.append(f"only {i0} sessions of history before the first test session ({NEED_HISTORY} are needed for "
                    "every signal): the first months trade less")
    ind = indicators(bars)
    sig = mc.Signals(bars, ind, bars.cols(stocks), bars.col(spy))
    earn, hold_blk, entry_blk = wfm.earnings_filter(data, bars, stocks, a.earnings)
    earn_modes = ["hold"] + (["exit_approx"] if hold_blk is not None else [])
    if hold_blk is None:
        print(f"exit-before-earnings variant SKIPPED: {earn['note']}", flush=True)
    else:
        print(f"exit-before-earnings variant runs (APPROXIMATE): {earn['note']}", flush=True)

    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    input_sha = bars_fingerprint(data, symbols, a.start, a.end)
    run_at = datetime.now(UTC)
    tag = f"core_{a.level}_{a.version}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    rows, trials, books = [], [], {}

    def one(sp: mc.CoreSpec, kind: str, fill: str, cost: float, exposure_pct: float, earnings: str) -> dict:
        exposure, earn_mode = exposure_pct, earnings
        blk = (hold_blk, entry_blk) if earn_mode == "exit_approx" else (None, None)
        book, rule = mc.run_rule(bars, ind, pol, sig, sp, i0, i1, fill=fill, cost=cost, exposure_pct=exposure,
                                 equity0=a.equity, hold_blk=blk[0], entry_blk=blk[1])
        label = run_label(sp.name, fill, cost, exposure, earn_mode)
        safe = label.replace("|", "_").replace("~", "-")
        trades = pd.DataFrame([dataclasses.asdict(t) for t in book.trades],
                              columns=[f.name for f in dataclasses.fields(MTrade)])
        daily = pd.DataFrame(book.daily)
        trades.to_csv(out / tag / f"trades_{safe}.csv", index=False)
        daily.to_csv(out / tag / f"daily_{safe}.csv", index=False)
        st = mc.account_stats(book)
        row = {"label": label, "kind": kind, "spec": sp.name, "fill": fill, "cost": cost, "exposure_pct": exposure,
               "earnings": earn_mode, "events_sha256": book_digest(book), "new_entries": sum(rule.entries.values()),
               "decisions_with_entries": len(rule.entries), "skipped_under_one_share": rule.skipped, **st,
               "files": {"trades": f"{tag}/trades_{safe}.csv", "daily": f"{tag}/daily_{safe}.csv"}}
        rows.append(row)
        books[label] = (book, rule)
        tid = f"{tag}_{safe}"
        pd.DataFrame({"date": daily["date"].astype(str), "ret": daily["pnl"] / daily["start"]}).to_csv(
            tdir / f"{tid}.csv", index=False)
        trials.append({
            "ts": run_at.isoformat(timespec="seconds"), "trial_id": tid, "strategy": sp.rule,
            "variant": "multiday_core", "label": f"momentum core {label}", "level": a.level,
            "universe": Path(a.universe).name, "events_sha256": row["events_sha256"], "input_sha256": input_sha,
            "code_sha": code_sha, "experiment": a.version, "status": "provisional", "prereg_sha256": pre["sha256"],
            # one configuration = one frozen rule at one exposure cap and earnings version; the same configuration
            # under higher costs or the stress fill is a report of it, not another configuration
            "n_trials": int(fill == "base" and cost == 1.0),
            "params": {**sp.params(), "fill": fill, "cost_multiple": cost, "exposure_cap_pct": exposure,
                       "earnings": earn_mode, "kind": kind},
            "window": [str(bars.dates[i0]), str(bars.dates[i1])],
            "metrics": {"trades": st["trades"], "net_return_pct": st["return_pct"], "cagr_pct": st["cagr_pct"],
                        "daily_sharpe_ann": st["sharpe"], "max_drawdown_pct": st["max_drawdown_pct"]}})
        return row

    n_grid = len(earn_modes) * len(mc.FILLS) * len(mc.EXPOSURES) * (len(mc.COSTS) + 1)
    print(f"{a.version}: {n_grid} grid runs + {len(mc.neighbours(a.version))} neighbours, sessions "
          f"{bars.dates[i0]} to {bars.dates[i1]}", flush=True)
    break_even = {}
    for earn_mode in earn_modes:
        for fill in mc.FILLS:
            for exposure in mc.EXPOSURES:
                got = {cost: one(spec, "grid" if cost else "break_even_input", fill, cost, exposure, earn_mode)
                       for cost in (0.0, *mc.COSTS)}
                break_even[got[1.0]["label"]] = mc.break_even_cost(got[1.0]["net_pnl"], got[0.0]["net_pnl"],
                                                                   got[1.0]["spread_impact_paid"])
        print(f"  grid, earnings {earn_mode}: done, {time.time() - t0:.0f}s", flush=True)
    for r in rows:
        r["break_even"] = break_even.get(r["label"])
    base_label = run_label(spec.name, **mc.BASE)
    by_label = {r["label"]: r for r in rows}
    base = by_label[base_label]
    nb = {sp.name.split("~")[1]: one(sp, "neighbour", **mc.BASE) for sp in mc.neighbours(a.version)}

    dates = bars.dates[i0:i1 + 1]
    spy_r = spy_buy_and_hold(bars, spy, i0, i1)
    bench = {"spy": mc.series_stats(dates, spy_r),
             "spy_scaled": {**mc.series_stats(dates, spy_r * base["avg_exposure_pct"] / 100),
                            "scale_pct": base["avg_exposure_pct"]},
             "universe_ew": mc.series_stats(dates, equal_weight_benchmark(bars, ind, bars.cols(stocks), i0, i1,
                                                                          a.equity))}
    control = {}
    for exposure in mc.EXPOSURES if a.control_runs > 0 else ():
        label = run_label(spec.name, "base", 1.0, exposure, "hold")
        book, rule = books[label]

        def tick(n: int, exposure=exposure) -> None:
            if n % 50 == 0:
                print(f"  random control, cap {exposure:g}%: {n} of {a.control_runs} runs, "
                      f"{time.time() - t0:.0f}s", flush=True)

        c = mc.matched_random_control(bars, ind, pol, sig, spec, book, rule, i0, i1, runs=a.control_runs,
                                      seed=a.control_seed, exposure_pct=exposure, equity0=a.equity, progress=tick)
        control[label] = c
        by_label[label]["control"] = {k: c[k] for k in ("runs", "sharpe_percentile", "cagr_percentile", "median")}
    gate = mc.gate_b(base, by_label.get(run_label(spec.name, "base", 2.0, 100.0, "hold")),
                     by_label.get(run_label(spec.name, "base", 3.0, 100.0, "hold")), bench["spy"],
                     control.get(base_label), nb, break_even.get(base_label))
    rep = {
        "run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "set": "core", "status": STATUS,
        "version": a.version, "rule": spec.rule, "spec": spec.params(), "prereg": pre, "level": a.level,
        "universe": Path(a.universe).name, "start": a.start, "end": a.end, "equity": a.equity,
        "control_runs": a.control_runs, "control_seed": a.control_seed, "earnings": earn,
        "earnings_variant": "run (approximate)" if hold_blk is not None else "skipped",
        "oos_window": [str(bars.dates[i0]), str(bars.dates[i1])], "oos_sessions": int(i1 - i0 + 1),
        "input_sha256": input_sha, "base_label": base_label, "rows": rows, "benchmarks": bench,
        "control": control, "gate_b": gate, "warnings": warn,
        "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak_rss_mb(), 0),
        "caveats": [
            "survivorship bias: today's large caps applied back to 2016",
            "prices are split-adjusted and carry no dividends, for the rule's signals, its holdings and every "
            "benchmark; SPY is price only",
            "cost model v1 (uncalibrated, #111 / S71); the close is the last regular-hours minute, not the auction",
            "the exit-before-earnings variant uses an estimated earnings window, not known dates (S85)",
            "at a 100% exposure cap a purchase can be cut to the cash left on the fill day (no leverage ever)",
            "the random control mirrors entry dates, entry counts, sizing and holding periods; its average "
            "exposure can still differ from the rule's and is printed beside it",
        ],
    }
    with (data / "trials.jsonl").open("a") as fh:
        for t in trials:
            fh.write(json.dumps(t, default=str) + "\n")
    # the report is written last: scripts/run_experiments.sh treats it as "this version is finished"
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    print(build_summary({a.version: (out / f"{tag}.json", rep)}))
    print(f"runtime {rep['runtime_s']}s, peak RSS {rep['peak_rss_mb']} MB")


# ---------------------------------------------------------------- the one table

HEAD = ("Rule / version", "Fill", "Costs", "Cap %", "Earnings", "Total return %", "CAGR %", "Sharpe",
        "Sharpe x 0.5", "Max drawdown %")
TAIL = ("Positive years", "Avg exposure %", "Max exposure %", "Avg names", "Turnover %/yr", "Cost bp/yr", "Trades",
        "Avg holding days", "Top-5 stocks' share of profit %", "Break-even cost x", "Sharpe pctile vs random",
        "CAGR pctile vs random")
EARN = {"hold": "hold through", "exit_approx": "exit before (APPROXIMATE)"}


def _cells(name: str, s: dict, years: list[str], **extra) -> dict:
    out = {"Rule / version": name, "Total return %": s["return_pct"], "CAGR %": s["cagr_pct"], "Sharpe": s["sharpe"],
           "Sharpe x 0.5": s.get("sharpe_haircut"), "Max drawdown %": s["max_drawdown_pct"],
           "Positive years": f"{s['positive_years']} of {s['n_years']}" if "positive_years" in s else None}
    out.update({y: s.get("years", {}).get(y) for y in years})
    out.update(extra)
    return out


def _rule_row(r: dict, years: list[str]) -> dict:
    be, c = r.get("break_even"), r.get("control") or {}
    name = r["spec"].replace("~", " ~ neighbour ")
    return _cells(name, r, years, **{
        "Fill": r["fill"], "Costs": f"{r['cost']:g}x", "Cap %": f"{r['exposure_pct']:g}",
        "Earnings": EARN[r["earnings"]],
        "Avg exposure %": r["avg_exposure_pct"], "Max exposure %": r["max_exposure_pct"], "Avg names": r["avg_names"],
        "Turnover %/yr": r["turnover_pct_per_year"], "Cost bp/yr": r["cost_bp_per_year"], "Trades": r["trades"],
        "Avg holding days": r["avg_hold_days"], "Top-5 stocks' share of profit %": r["top5_profit_share_pct"],
        "Break-even cost x": None if not be else (be["multiple"] if be["multiple"] is not None else be["note"]),
        "Sharpe pctile vs random": c.get("sharpe_percentile"), "CAGR pctile vs random": c.get("cagr_percentile")})


def build_summary(found: dict[str, tuple[Path, dict]], missing: tuple[str, ...] = ()) -> str:
    years = sorted({y for _, rep in found.values() for r in rep["rows"] for y in r["years"]})
    cols = (*HEAD, *years, *TAIL)
    table, gates, notes, windows = [], [], [], set()
    bench_done = False
    for version, (f, rep) in found.items():
        windows.add(tuple(rep["oos_window"]))
        shown = [r for r in rep["rows"] if r["kind"] != "break_even_input"]
        order = {"grid": 0, "neighbour": 1}
        shown.sort(key=lambda r: (r["label"] != rep["base_label"], order[r["kind"]], r["earnings"] != "hold",
                                  r["fill"] != "base", -r["exposure_pct"], r["cost"]))
        table += [_rule_row(r, years) for r in shown]
        b = rep["benchmarks"]
        if not bench_done:
            table.append(_cells("BENCHMARK: SPY buy-and-hold (price only, no dividends)", b["spy"], years,
                                **{"Avg exposure %": 100.0}))
            table.append(_cells("BENCHMARK: equal-weight universe, rebalanced monthly, after costs",
                                b["universe_ew"], years, **{"Avg exposure %": 100.0}))
            bench_done = True
        table.append(_cells(f"BENCHMARK: SPY (price only) scaled to {version}'s average exposure, rest in cash",
                            b["spy_scaled"], years, **{"Avg exposure %": b["spy_scaled"]["scale_pct"]}))
        for label, c in rep["control"].items():
            m = c["median"]
            cap = label.split("|")[3].removeprefix("cap")
            table.append({"Rule / version": f"BENCHMARK: random entries matched to {version} (median of "
                                            f"{c['runs']} runs)", "Fill": "base", "Costs": "1x", "Cap %": cap,
                          "Earnings": EARN["hold"], "Total return %": m["return_pct"], "CAGR %": m["cagr_pct"],
                          "Sharpe": m["sharpe"], "Sharpe x 0.5": None if m["sharpe"] is None else round(
                              m["sharpe"] / 2, 2), "Max drawdown %": m["max_drawdown_pct"],
                          "Avg exposure %": m["avg_exposure_pct"]})
        gates += [f"### `{version}` (base fill, 1x costs, 100% cap, hold through earnings)", ""]
        gates += [f"- Mark {g['mark']} ({g['text']}): **{g['verdict']}**. {g['detail']}." for g in rep["gate_b"]]
        gates += ["- Overall: cannot pass Gate B yet; every result here is provisional.", ""]
        skipped = max((r["skipped_under_one_share"] for r in rep["rows"]), default=0)
        notes.append(
            f"- `{version}`: pre-registration `{rep['prereg']['file']}` (sha256 `{rep['prereg']['sha256'][:12]}`); "
            f"exit-before-earnings variant {rep['earnings_variant']} ({rep['earnings']['note']}); random control "
            f"{rep['control_runs']} runs per exposure cap, seed {rep['control_seed']}; at most {skipped} buys of a "
            f"run skipped because one share cost more than the position size; code `{rep['code_sha']}`; report "
            f"`{f.name}`." + "".join(f" WARNING: {w}." for w in rep.get("warnings", [])))
    out = ["# Momentum core, ten-year test: trend-filtered momentum and banded 12-1 momentum (issue #127)", "",
           SURVIVORSHIP, "", PROVISIONAL, ""]
    if windows:
        w = sorted(windows)[0]
        out += [f"Out-of-sample {w[0]} to {w[1]}. Each row is ONE continuous $100k account of one frozen, "
                "pre-registered configuration, starting flat; nothing is tuned. Signals and holdings use "
                "split-adjusted prices without dividends; so do the benchmarks.", ""]
        if len(windows) > 1:
            out += ["**The versions were run over different windows: rerun them with the same dates.**", ""]
    out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join("" if r.get(c) is None else str(r[c]) for c in cols) + " |" for r in table]
    out += ["",
            "How to read it: fill `base` = decided after the close of T, bought and sold at the close of T+1 at "
            "half a spread a side plus impact; `stress` = the open of T+1 with spread and impact doubled. Costs "
            "`2x` / `3x` multiply spread and impact of cost model v1 (regulatory fees stay). Break-even is the "
            "multiple of spread and impact at which net profit is zero, from the 0x and 1x runs of that row's fill, "
            "cap and earnings version (pre-registered); the report JSON also keeps the multiple with the 1x trades "
            "held fixed as a cross-check. "
            "Sharpe is annualised from daily mark-to-market returns (no "
            "risk-free rate); the 50% haircut is beside it; a backtested Sharpe above about 1.0 is a warning sign. "
            "Max drawdown is mark-to-market at the close. Turnover is (bought + sold) / 2 a year over the average "
            "equity; cost is spread, impact and fees a year in bp of the average equity. Trades are closed "
            "positions (resize sales are not counted). The percentile columns say where the rule's net Sharpe and "
            "CAGR fall among the matched random entries (same dates, entries per date, sizing and holding "
            "periods; random stocks of the same universe and liquidity filter).", "",
            "## Gate B marks (decision 15, section 7)", "", *gates, "## Notes", "", *notes]
    if missing:
        out += ["", f"Not run yet: {', '.join(missing)}."]
    return "\n".join(out) + "\n"


def summary(data: Path, level: str) -> str:
    found, missing = {}, []
    for version in mc.VERSIONS:
        hit = find_report(data / "reports", level, version)
        if hit is None:
            missing.append(version)
        else:
            found[version] = hit
    return build_summary(found, tuple(missing))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=list(mc.VERSIONS))
    ap.add_argument("--has", default="", choices=["", *mc.VERSIONS], help="exit 0 if this version has a report")
    ap.add_argument("--summary", action="store_true", help="print the one table and write it")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", default="2016-01-01", help="first date with bars")
    ap.add_argument("--end", help="last session (inclusive)")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--control-runs", type=int, default=200, help="matched random runs per exposure cap "
                    "(Gate B mark 3 needs 500; 0 switches the control off)")
    ap.add_argument("--control-seed", type=int, default=127)
    ap.add_argument("--earnings", default="auto", choices=["auto", "off"],
                    help="auto: run the approximate exit-before-earnings variant when the estimate is reliable")
    ap.add_argument("--refresh-daily", action="store_true", help="rebuild the daily-bar cache")
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        hit = find_report(data / "reports", a.level, a.has, start=a.start, end=a.end,
                          universe=Path(a.universe).name, control_runs=a.control_runs)
        sys.exit(0 if hit else 1)
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
