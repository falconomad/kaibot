"""The Gate B report of a multi-day rule from the engine's own fills (issue #161; decision 15 sections 5, 7, 10).

    python -m kaibot.engine.research --strategy trend_momentum --universe pit
    python -m kaibot.engine.research --strategy mom_12_1_band --universe pit --control-runs 500
    python -m kaibot.engine.research --strategy trend_momentum            # the committed fixture (seconds)

Runtime (measured 12 Nov 2026 on 2 shared CPUs, a synthetic store of 730 stocks x 2,730 sessions, 2017-05 to
2026-11): about 80 s for the nine engine runs (about 8 s each for trend_momentum, 5 s for mom_12_1_band) plus about
85 s for the 500-run control: under 3 minutes per rule, plus loading the store. `--control-runs 0` skips mark 3.

Each run is one continuous $100k account of one frozen configuration through `engine/daily.run_daily`: the
strategy plug-in decides after each close, the shared ATR sizing rule sizes, `SimBroker` fills at the next
closing auction (cost model v1 with impact, as pre-registered), the ledger stand-in books. The Gate B marks
(`replay.multiday_core.gate_b`, the one implementation of the pass marks) are judged on those accounts:

- marks 1, 2: the base version (base fill, 1x costs, 100% exposure cap, hold through earnings) against SPY total
  return from the daily store (price-only SPY beside it);
- mark 3: the exposure-matched random control of decision 15 section 10 (>= 500 runs). The rule's positions are
  the engine's; the random portfolios that copy them are replayed in the research harness (the only replay of
  that control), and the lower of the rule's own percentile and that of its names replayed there counts;
- mark 4: the same account at 2x costs (3x and the break-even multiple reported);
- mark 5: on `--universe pit` the base row itself;
- mark 6: the five pre-declared neighbours at the base settings.

`--grid full` adds the rest of the pre-registered reporting grid (stress fill, 30 / 60% caps). The approximate
exit-before-earnings variant is not run here (S85). Writes data/reports/engine_<level>_<version>_<universe>_<ts>
.json (+ one daily CSV per run) and one trial row per run in data/trials.jsonl (a base configuration counts 1,
its cost and fill reports 0, as scripts/momentum_core.py counts them).

Sectors (Amendment 4's 40% sector cap, the same in the engine and the harness): a stock's sector is the sector it
is listed under in the universe file (`--universe-file`, config/universe_history.yaml). On `--universe pit` a
company the file does not list (a former member, keyed SYMBOL@ASOF in the daily store, which holds no sector) is
its own sector, as the amendment says; the report's `sectors` counts both. The fixture's sectors are in its
fixture.json.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from kaibot.engine.config import ROOT
from kaibot.engine.daily import DailyData, DailyRun, daily_returns, run_daily, sessions_window
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import momentum as mom

FIXTURE = ROOT / "tests" / "fixtures" / "daily_momentum"
COSTS = (1.0, 2.0, 3.0, 0.0)
EXPOSURES = (100.0, 60.0, 30.0)
NEED_HISTORY = 252 + 21
STATUS = ("engine + SimBroker (decision 15): strategy plug-ins, closing-auction orders, cost model v1 with impact as "
          "pre-registered; the ledger is the documented stand-in until #117 lands")


# ---------------------------------------------------------------- data


def load_fixture(directory: Path = FIXTURE):
    """The committed fixture: a daily store in #124's layout plus `fixture.json` (membership blocks, terminal
    returns, forced exits, the test window). Returns (Market, info)."""
    from kaibot.ingest.daily_store import DailyStore
    from kaibot.replay import multiday_core as mc

    directory = Path(directory)
    meta = json.loads((directory / "fixture.json").read_text())
    store = DailyStore(directory)
    panel = store.panel(meta["symbols"], minute_share=meta.get("minute_share", 0.02))
    member = np.ones(panel.c.shape, dtype=bool)
    for sym, spans in meta.get("not_member", {}).items():
        for a, b in spans:
            member[a:b, panel.col(sym)] = False
    m = mc.Market.from_panel(panel, meta["stocks"], meta["market"], member=member,
                             terminal_returns=meta.get("terminal_returns", {}), sectors=meta.get("sectors", {}))
    for sym, sessions in meta.get("exits", {}).items():
        m.exits[m.raw.col(sym)] = list(sessions)
    where = directory.relative_to(ROOT) if directory.is_relative_to(ROOT) else directory
    return m, {"bars": f"committed fixture {where}", "window": meta["window"]}


def load_store(data: Path, universe_file: Path, mode: str, start: str, end: str):
    """The daily store (#124) under `data/daily`: `mode` "pit" (the S&P 500 as it was, #123) or "today" (the stocks
    of `universe_file`). The same preparation as scripts/momentum_core.py (`--bars store`)."""
    import yaml

    from kaibot.ingest.daily_store import DailyStore
    from kaibot.ingest.sp500_pit import Membership, read_outcomes, terminal_return
    from kaibot.replay import multiday_core as mc

    cfg = yaml.safe_load(Path(universe_file).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    etfs, spy = list(cfg["sectors"]), cfg["market"]
    root = Path(data) / "daily"
    store = DailyStore(root)
    have = set(store.keys())
    if spy not in have:
        raise SystemExit(f"no {spy} in the daily store {root}: run ./scripts/fetch_daily.sh first "
                         "(docs/ops/daily_bars.md)")
    member, tr = None, {}
    if mode == "pit":
        ms = Membership.load(universe=Path(universe_file))
        cand = [*stocks, *sorted(have - set(stocks) - set(etfs) - {spy})]
        km = ms.key_map(cand)
        names = [cand[j] for j in sorted(set(km.values()))]
        company = {cand[j]: c for c, j in km.items()}
        outcomes = read_outcomes()
        tr = {k: terminal_return(c, outcomes) for k, c in company.items()}
    else:
        names = [s for s in stocks if s in have]
    symbols = [*names, *[e for e in etfs if e in have], spy]
    panel = store.panel(symbols, start, end)
    if mode == "pit":
        member = ms.mask(panel.dates, panel.symbols)
    rep = store.adjustment_report()
    bad = rep[rep.get("split_basis_days", pd.Series(dtype=float)).fillna(0) > 0]["key"].astype(str).tolist() \
        if len(rep) else []
    # Amendment 4's 40% sector cap: a stock's sector is the one the universe file lists it under; a stock it does
    # not list (point-in-time: a former member, whose store key is not a universe ticker) is its own sector
    sectors = mc.universe_sectors(cfg)
    m = mc.Market.from_panel(panel, names, spy, member=member, terminal_returns=tr, disputed=bad, sectors=sectors)
    m.notes["sectors"] = {"from_universe_file": sum(s in sectors for s in names),
                          "own_sector": sum(s not in sectors for s in names)}
    return m, {"bars": "daily store (data/daily): raw bars; signals split-adjusted without dividends; orders, costs "
                       "and share counts raw; dividends credited", "stocks": len(names), "excluded_split_basis": bad,
               "sectors": m.notes["sectors"]}


# ---------------------------------------------------------------- one account and its statistics


def run_stats(run: DailyRun) -> dict:
    from kaibot.replay import multiday_core as mc

    st = mc.account_stats(run.book)
    od = run.ledger.overdrafts
    eq = {str(r["date"]): r["end"] for r in run.book.daily}
    st.update({"new_entries": len(run.opened), "skipped_under_one_share": run.skipped,
               "orders_rejected": len(run.rejected), "fills": len(run.fills),
               # cuts by the outer guards (gap scenario, cash floor): any > 0 is not the pre-registered sizing
               "guard_cuts": len(run.guards), "basis_gaps": len(run.basis_gaps),
               "overdraft_days": len(od),
               "max_overdraft_pct": round(max((-c / eq[d] * 100 for d, c in od if eq.get(d)), default=0.0), 3)})
    return st


def control_for(run: DailyRun, market, spec_name: str, pol, runs: int, seed: int, equity0: float,
                progress=None) -> dict:
    """Gate B mark 3 on the engine's positions (see the module docstring)."""
    from kaibot.replay import multiday_core as mc

    rule = SimpleNamespace(opened=run.opened, resized=run.resized)
    return mc.exposure_matched_control(market, pol, mc.VERSIONS[spec_name], run.book, rule, run.i0, run.i1,
                                       runs=runs, seed=seed, equity0=equity0, progress=progress)


def label(name: str, fill: str, cost: float, cap: float) -> str:
    return f"{name}|{fill}|{cost:g}x|cap{cap:g}|hold"


def report(version: str, market, info: dict, *, universe: str, level: str = "balanced", start: str | None = None,
           end: str | None = None, equity0: float = 100_000.0, control_runs: int = 500, control_seed: int = 127,
           grid: str = "gate", out_dir: Path | None = None, trials: Path | None = None, prereg: dict | None = None,
           log=print) -> dict:
    """Every run Gate B needs (or the full pre-registered grid), the marks, the benchmarks. Writes the report,
    the daily CSVs and the trial rows when `out_dir` / `trials` are given."""
    from kaibot.replay import multiday_core as mc

    t0 = time.time()
    pol = load_profiles()[level]
    data = DailyData.from_market(market)
    w = info.get("window") or [start or "2017-05-01", end or str(data.dates[-1])]
    i0, i1 = sessions_window(data, w[0], w[1])
    if i0 > i1:
        raise SystemExit(f"no session in the test window {w}")
    strategy = mom.VERSIONS[version]
    runs: dict[str, tuple[DailyRun, dict, dict]] = {}

    def one(strat, kind: str, fill: str, cost: float, cap: float) -> dict:
        t = time.time()
        r = run_daily(strat, data, pol, i0, i1, fill=fill, cost=cost, exposure_pct=cap, equity0=equity0)
        st = run_stats(r)
        lab = label(strat.name, fill, cost, cap)
        row = {"label": lab, "kind": kind, "spec": strat.name, "fill": fill, "cost": cost, "exposure_pct": cap,
               "earnings": "hold", "runtime_s": round(time.time() - t, 1), **st}
        runs[lab] = (r, row, st)
        log(f"  {lab}: CAGR {st['cagr_pct']}%, max drawdown {st['max_drawdown_pct']}%, {st['trades']} trades, "
            f"{row['runtime_s']}s")
        if st["guard_cuts"]:
            log(f"  {lab}: WARNING {st['guard_cuts']} purchases cut by the outer guards: this run is not the "
                "pre-registered sizing (#168)")
        return row

    log(f"{version}: engine + SimBroker, sessions {data.dates[i0]} to {data.dates[i1]} ({i1 - i0 + 1}), "
        f"{len(data.symbols)} symbols, universe {universe}")
    fills = ("base", "stress") if grid == "full" else ("base",)
    caps = EXPOSURES if grid == "full" else (100.0,)
    break_even = {}
    for fill in fills:
        for cap in caps:
            got = {c: one(strategy, "grid" if c else "break_even_input", fill, c, cap) for c in COSTS}
            break_even[got[1.0]["label"]] = mc.break_even_cost(got[1.0]["net_pnl"], got[0.0]["net_pnl"],
                                                               got[1.0]["spread_impact_paid"])
    rows = [row for _, row, _ in runs.values()]
    for r in rows:
        r["break_even"] = break_even.get(r["label"])
    nb = {s.name.split("~")[1]: one(s, "neighbour", "base", 1.0, 100.0) for s in mom.neighbours(version)}
    base_label = label(version, "base", 1.0, 100.0)
    by = {r["label"]: r for r in [*rows, *nb.values()]}
    base = by[base_label]
    dates = data.dates[i0:i1 + 1]
    spy_c = data.sig_unfilled_c[:, data.spy]
    spy_r = spy_c[i0:i1 + 1] / spy_c[i0 - 1:i1] - 1
    bench = {"spy": mc.series_stats(dates, spy_r)}
    if data.spy_tr is not None:
        r = np.asarray(data.spy_tr[i0:i1 + 1], dtype=float)
        if np.isfinite(r).all():
            bench["spy_total_return"] = mc.series_stats(dates, r)
    control = None
    if control_runs > 0:
        def tick(n: int) -> None:
            if n % 50 == 0:
                log(f"  exposure-matched control: {n} of {control_runs} runs, {time.time() - t0:.0f}s")

        control = control_for(runs[base_label][0], market, version, pol, control_runs, control_seed, equity0, tick)
        base["control"] = {k: control.get(k) for k in ("kind", "runs", "sharpe_percentile", "cagr_percentile",
                                                       "median", "matched", "committed_exposure_gap_pts")}
    gate = mc.gate_b(base, by.get(label(version, "base", 2.0, 100.0)), by.get(label(version, "base", 3.0, 100.0)),
                     bench["spy"], control, nb, break_even.get(base_label), spy_tr=bench.get("spy_total_return"),
                     universe="point_in_time" if universe == "pit" else "today")
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    tag = f"engine_{level}_{version}_{universe}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    rep = {"run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "set": "engine_core",
           "status": STATUS, "version": version, "rule": strategy.rule, "prereg": prereg, "level": level,
           "universe": universe, "data": info, "oos_window": [str(dates[0]), str(dates[-1])],
           "oos_sessions": int(i1 - i0 + 1), "symbols": len(data.symbols), "equity": equity0, "grid": grid,
           "base_label": base_label, "rows": rows, "neighbours": nb, "benchmarks": bench, "control": control,
           "gate_b": gate, "all_marks_pass": all(m["verdict"] == "pass" for m in gate),
           "warnings": ([f"only {i0} sessions before the first test session ({NEED_HISTORY} are needed)"]
                        if i0 < NEED_HISTORY else []),
           "caveats": [
               "mark 3: the random portfolios are replayed in the research harness (the rule's positions are the "
               "engine's); the lower of the two percentiles counts",
               "the exit-before-earnings variant is not run (approximate earnings window; S85)",
               "the ledger is the stand-in of engine/daily.py until #117 merges",
               "Amendment 3: buys are sized against cash minus 3% of equity, a buy above the cash at the print is "
               "cut to whole shares (the venue's cash book never goes below zero), split fractions are paid in "
               "cash; the same in the research harness"],
           "runtime_s": round(time.time() - t0, 1)}
    if out_dir is not None:
        out_dir = Path(out_dir)
        (out_dir / tag).mkdir(parents=True, exist_ok=True)
        for lab, (r, _, _) in runs.items():
            safe = lab.replace("|", "_").replace("~", "-")
            pd.DataFrame(r.book.daily).to_csv(out_dir / tag / f"daily_{safe}.csv", index=False)
            pd.DataFrame([asdict(t) for t in r.book.trades]).to_csv(out_dir / tag / f"trades_{safe}.csv", index=False)
        (out_dir / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
        rep["file"] = str(out_dir / f"{tag}.json")
    if trials is not None:
        Path(trials).parent.mkdir(parents=True, exist_ok=True)
        with Path(trials).open("a") as fh:
            for lab, (r, row, st) in runs.items():
                fh.write(json.dumps({
                    "ts": run_at.isoformat(timespec="seconds"), "trial_id": f"{tag}_{lab}", "strategy": strategy.rule,
                    "variant": "engine_simbroker", "label": f"engine {lab}", "level": level, "cost_model": "v1",
                    "universe_mode": universe, "code_sha": code_sha, "status": "engine",
                    "prereg_sha256": (prereg or {}).get("sha256"),
                    "n_trials": int(row["fill"] == "base" and row["cost"] == 1.0),
                    "params": {"spec": row["spec"], "fill": row["fill"], "cost_multiple": row["cost"],
                               "exposure_cap_pct": row["exposure_pct"], "kind": row["kind"]},
                    "window": rep["oos_window"],
                    "metrics": {"trades": st["trades"], "net_return_pct": st["return_pct"], "cagr_pct": st["cagr_pct"],
                                "daily_sharpe_ann": st["sharpe"], "max_drawdown_pct": st["max_drawdown_pct"]}},
                    default=str) + "\n")
                pd.DataFrame({"date": [str(x["date"]) for x in r.book.daily], "ret": daily_returns(r.book)}).to_csv(
                    Path(trials).parent / "trials" / f"{tag}_{lab.replace('|', '_').replace('~', '-')}.csv",
                    index=False) if (Path(trials).parent / "trials").is_dir() else None
    return rep


def summary(rep: dict) -> str:
    """The report as a short markdown table."""
    b = rep["benchmarks"]
    lines = [f"## {rep['version']} through the engine ({rep['universe']}), {rep['oos_window'][0]} to "
             f"{rep['oos_window'][1]}", "", rep["status"], "",
             "| Run | Total % | CAGR % | Sharpe | Max DD % | Positive years | Trades | Max overdraft % |",
             "|---|---|---|---|---|---|---|---|"]
    for r in [*rep["rows"], *rep["neighbours"].values()]:
        lines.append(f"| {r['label']} | {r['return_pct']} | {r['cagr_pct']} | {r['sharpe']} | {r['max_drawdown_pct']} "
                     f"| {r['positive_years']} of {r['n_years']} | {r['trades']} | {r['max_overdraft_pct']} |")
    for k, s in b.items():
        lines.append(f"| {k} | {s['return_pct']} | {s['cagr_pct']} | {s['sharpe']} | {s['max_drawdown_pct']} | "
                     f"{s['positive_years']} of {s['n_years']} | | |")
    lines += ["", "| Gate B mark | Verdict | Detail |", "|---|---|---|"]
    lines += [f"| {m['mark']} | {m['verdict']} | {m['detail']} |" for m in rep["gate_b"]]
    lines += ["", f"All marks pass: {'yes' if rep['all_marks_pass'] else 'no'}. Runtime {rep['runtime_s']}s."]
    return "\n".join(lines)

