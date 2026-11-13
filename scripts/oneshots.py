"""One-shot ten-year tests of issue #112 (story S72, decision 15 candidate 5): six rules, one frozen run each.
Research only: reads the history under data/, never touches the engine or the broker. PROVISIONAL: the research
harness of #113 with cost model v1; a rule can fail here, it cannot pass (the go/no-go run is the engine with
`SimBroker`).

python scripts/oneshots.py --rule <rule> --end 2026-11-06 [--start 2016-01-01] [--level balanced]
    [--control-runs 500] [--seeds 200] [--universe-file config/universe_history.yaml]
python scripts/oneshots.py --has <rule> --start ... --end ...      exit 0 if that rule has a finished report
python scripts/oneshots.py --summary    the one table, data/reports/experiments_oneshots_summary.md

Rules (pre-registered in docs/prereg/<rule>.md, committed before this file; `prereg.require` refuses a run
without one):
- daily, on the point-in-time S&P 500 (#123) and the daily store (#124), one continuous $100k account from 2017-05:
  `pullback_uptrend`, `ath_breakout` (5, 10 and 20 positions, a distribution over --seeds random signal subsets),
  `post_earnings_week` (needs data/earnings from S85), `weekly_fall_rebound` (5 and 20 sessions held).
  Grid: fill base / stress x costs 0x (break-even input), 1x, 2x, 3x at a 100% cap, and the 30% and 60% caps at
  the base fill; the pre-declared neighbours; the exposure-matched random control (Gate B mark 3, --control-runs);
  SPY total return, price-only SPY and the equal-weight members as benchmarks; Gate B marks 1-6.
- intraday, on today's list with minute bars (former members have none): `orb_stocks_in_play` (keep or delete)
  and `news_continuation` (complete news months only). Pass 1 per symbol is cached in data/oneshots/<rule>/, so
  an interrupted run resumes there.

Exit codes: 0 done, 3 skipped because its data is missing (the message says which; nothing is written but a
`oneshots_<rule>_skipped.json` note for the summary), anything else an error. Every run appends one trial row
per configuration to data/trials.jsonl (n_trials 1 for a configuration at the base fill and 1x costs, 0 for its
cost and fill stress reports) with the pre-registration's sha256 and the sha256 of config/gate_v2.yaml.
"""

from __future__ import annotations

import argparse
import dataclasses
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
import momentum_core as core  # noqa: E402
from walk_forward import peak_rss_mb  # noqa: E402

from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation.gate_v2 import GateV2  # noqa: E402
from kaibot.replay import multiday_core as mc  # noqa: E402
from kaibot.replay import oneshots_daily as od  # noqa: E402
from kaibot.replay import oneshots_intraday as oi  # noqa: E402
from kaibot.replay.data import load_news_times, load_symbol  # noqa: E402
from kaibot.replay.multiday_rules import book_digest  # noqa: E402
from kaibot.replay.walkforward import WARMUP_DAYS, first_train_month, make_folds  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

DAILY = od.RULES
INTRADAY = ("orb_stocks_in_play", "news_continuation")
RULES = (*INTRADAY, *DAILY)
SKIPPED = 3
SUMMARY = "experiments_oneshots_summary.md"
CACHE_VERSION = "1"  # bump when pass 1 changes, so cached symbol rows are rebuilt
STATUS = ("PROVISIONAL (decision 15): research harness of #113, cost model v1; a one-shot can fail here, it cannot "
          "pass (the go/no-go run is the engine with `SimBroker`)")
GRID_COSTS = (0.0, 1.0, 2.0, 3.0)
GRID_CAPS = (30.0, 60.0)


class Skip(Exception):
    """The rule's data is missing: the run is skipped with this message."""


def prereg(rule: str) -> dict:
    try:
        sha = prereg_mod.require(rule, ROOT)
        status = prereg_mod.load(rule, ROOT, committed=False).status
    except prereg_mod.PreregError as e:
        sys.exit(f"no run: {e}")
    return {"file": str(prereg_mod.path_for(rule, ROOT).relative_to(ROOT)), "sha256": sha, "status": status}


def code_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()


def find_report(reports: Path, rule: str, **same) -> tuple[Path, dict] | None:
    for f in sorted(reports.glob(f"oneshots_{rule}_2*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != "oneshots" or rep.get("rule") != rule:
            continue
        if any(want is not None and rep.get(k) != want for k, want in same.items()):
            continue
        return f, rep
    return None


def trial_row(ctx: dict, label: str, version: str, params: dict, fill: str, cost: float, book_or_rets, metrics: dict,
              events_sha: str | None = None) -> dict:
    """One trial row (and its daily-returns CSV under data/trials/)."""
    tid = f"{ctx['tag']}_{label}".replace("|", "_").replace("~", "-")
    dates, rets = book_or_rets
    pd.DataFrame({"date": pd.Series(dates).astype(str), "ret": rets}).to_csv(ctx["tdir"] / f"{tid}.csv", index=False)
    return {"ts": ctx["run_at"].isoformat(timespec="seconds"), "trial_id": tid, "strategy": ctx["rule"],
            "variant": "oneshots", "label": f"one-shot {label}", "level": ctx["level"], "cost_model": "v1",
            "universe": ctx["universe"], "universe_mode": ctx["mode"], "events_sha256": events_sha,
            "input_sha256": ctx["input_sha"], "code_sha": ctx["code_sha"], "experiment": version,
            "status": "provisional", "prereg_sha256": ctx["prereg"]["sha256"], "gate_v2_sha256": ctx["gate_sha"],
            "n_trials": int(fill == "base" and cost == 1.0), "params": {**params, "fill": fill, "cost_multiple": cost},
            "window": ctx["window"], "metrics": metrics}


def write_trials(data: Path, trials: list[dict]) -> None:
    with (data / "trials.jsonl").open("a") as fh:
        for t in trials:
            fh.write(json.dumps(t, default=str) + "\n")


# ---------------------------------------------------------------- daily rules


def daily_market(a: argparse.Namespace, data: Path, t0: float):
    if not (data / "daily" / "bars" / "symbol=SPY.parquet").is_file():
        raise Skip("no daily store at data/daily: run ./scripts/fetch_daily.sh first (docs/m0/reproduce.md)")
    cfg = yaml.safe_load(Path(a.universe_file).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    ns = argparse.Namespace(universe_file=a.universe_file, start=a.start, end=a.end, refresh_daily=False)
    m, input_sha, symbols, info = core.load_market(ns, data, stocks, list(cfg["sectors"]), cfg["market"],
                                                   "point_in_time", "store", t0)
    return m, input_sha, symbols, info


def earnings_events(a: argparse.Namespace, data: Path, m: mc.Market) -> tuple[dict[int, list[int]], dict]:
    f = data / "earnings" / "announcements.parquet"
    if not f.is_file():
        raise Skip(f"no SEC earnings dates at {f}: run ./scripts/fetch_earnings_dates.sh first (S85, #125)")
    from kaibot.ingest.earnings_pit import report_events
    from kaibot.ingest.sp500_pit import Membership

    table = pd.read_parquet(f)
    ms = Membership.load(universe=Path(a.universe_file))
    ev = report_events(table, m.raw.dates, list(m.raw.symbols), ms)
    stocks = set(m.stocks.tolist())
    out = {m.raw.col(k): v for k, v in ev.items() if k in m.raw._col and m.raw.col(k) in stocks}
    return out, {"file": str(f.relative_to(data.parent)) if f.is_relative_to(data.parent) else str(f),
                 "report_rows": int(table["report"].astype(bool).sum()), "columns_with_events": len(out),
                 "events": int(sum(len(v) for v in out.values()))}


def run_daily_rule(a: argparse.Namespace, rule: str) -> None:
    t0 = time.time()
    data = Path(a.data)
    pre = prereg(rule)
    gate_sha = GateV2.load(ROOT).sha
    print(f"pre-registration {pre['file']} sha256 {pre['sha256'][:12]}; gate_v2 {gate_sha[:12]}", flush=True)
    m, input_sha, symbols, info = daily_market(a, data, t0)
    events, ev_info = ({}, None)
    if rule == "post_earnings_week":
        events, ev_info = earnings_events(a, data, m)
        print(f"SEC announcements: {ev_info}", flush=True)
    sig = od.DailySignals(m, events)
    pol = load_profiles()[a.level]
    bars = m.raw
    folds = make_folds(first_train_month(a.start, WARMUP_DAYS), np.datetime64(a.end, "D"))
    if not folds:
        sys.exit("the range is shorter than 12 months of history plus one test month")
    i0, i1 = max(bars.index_from(folds[0].test_start), 1), bars.index_upto(np.datetime64(a.end, "D"))
    run_at = datetime.now(UTC)
    tag = f"oneshots_{rule}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    ctx = {"tag": tag, "tdir": tdir, "run_at": run_at, "rule": rule, "level": a.level,
           "universe": Path(a.universe_file).name, "mode": "point_in_time", "input_sha": input_sha,
           "code_sha": code_sha(), "prereg": pre, "gate_sha": gate_sha,
           "window": [str(bars.dates[i0]), str(bars.dates[i1])]}
    trials: list[dict] = []
    dates = bars.dates[i0:i1 + 1]

    def one(spec: od.DailySpec, fill: str, cost: float, cap: float, seed: int | None, kind: str,
            log: bool = True, files: bool = True):
        book, r = od.run_daily(m, sig, pol, spec, i0, i1, fill=fill, cost=cost, exposure_pct=cap,
                               equity0=a.equity, seed=seed)
        st = mc.account_stats(book)
        label = f"{spec.name}|{fill}|{cost:g}x|cap{cap:g}" + ("" if seed is None else f"|seed{seed}")
        row = {"label": label, "kind": kind, "spec": spec.name, "fill": fill, "cost": cost, "exposure_pct": cap,
               "seed": seed, "events_sha256": book_digest(book), "new_entries": sum(r.entries.values()),
               "signals": r.signals, "skipped_under_one_share": r.skipped, **st}
        if files:
            safe = label.replace("|", "_").replace("~", "-")
            pd.DataFrame([dataclasses.asdict(t) for t in book.trades]).to_csv(out / tag / f"trades_{safe}.csv",
                                                                               index=False)
            pd.DataFrame(book.daily).to_csv(out / tag / f"daily_{safe}.csv", index=False)
            row["files"] = {"trades": f"{tag}/trades_{safe}.csv", "daily": f"{tag}/daily_{safe}.csv"}
        if log:
            trials.append(trial_row(ctx, label, spec.name, spec.params(), fill, cost,
                                    (dates, mc.daily_returns(book)), {
                                        "trades": st["trades"], "net_return_pct": st["return_pct"],
                                        "cagr_pct": st["cagr_pct"], "daily_sharpe_ann": st["sharpe"],
                                        "max_drawdown_pct": st["max_drawdown_pct"]}, row["events_sha256"]))
        return row, book, r

    spy_r, spy_tr = m.spy_price_only(i0, i1), m.spy_total_return(i0, i1)
    bench = {"spy": mc.series_stats(dates, spy_r), "universe_ew": mc.series_stats(dates, m.equal_weight(i0, i1))}
    if spy_tr is not None:
        bench["spy_total_return"] = mc.series_stats(dates, spy_tr)
    pd.DataFrame({"date": dates.astype(str), "ret": spy_tr if spy_tr is not None else spy_r,
                  "ret_price_only": spy_r}).to_csv(out / tag / "spy_daily.csv", index=False)
    versions: dict[str, dict] = {}
    seeded = rule == "ath_breakout"
    seeds = list(range(a.seeds)) if seeded else [None]
    for vname in od.RULE_VERSIONS[rule]:
        spec = od.VERSIONS[vname]
        is_base = vname == od.RULE_VERSIONS[rule][0]
        rows: list[dict] = []
        dist: dict[str, dict] = {}
        if seeded:
            runs = {s: one(spec, "base", 1.0, 100.0, s, "seed", log=False, files=False) for s in seeds}
            stats = {s: r[0] for s, r in runs.items()}
            med = od.median_seed(stats)
            dist["base|1x"] = od.distribution(list(stats.values()))
            print(f"  {vname}: {len(seeds)} seeds, median seed {med}, {time.time() - t0:.0f}s", flush=True)
        else:
            med = None
        base_row, book, r = one(spec, "base", 1.0, 100.0, med, "grid")
        rows.append(base_row)
        if not is_base and seeded:
            versions[vname] = {"rows": rows, "distribution": dist, "median_seed": med}
            continue
        for fill in ("base", "stress"):
            for cost in GRID_COSTS:
                if fill == "base" and cost == 1.0:
                    continue
                if seeded:
                    st = [one(spec, fill, cost, 100.0, s, "seed", log=False, files=False)[0] for s in seeds]
                    dist[f"{fill}|{cost:g}x"] = od.distribution(st)
                rows.append(one(spec, fill, cost, 100.0, med, "grid" if cost else "break_even_input")[0])
        for cap in GRID_CAPS:
            rows.append(one(spec, "base", 1.0, cap, med, "grid")[0])
        by = {x["label"]: x for x in rows}
        suffix = "" if med is None else f"|seed{med}"

        def lab(f: str, c: float, cap: float = 100.0, name: str = spec.name, suffix: str = suffix) -> str:
            return f"{name}|{f}|{c:g}x|cap{cap:g}{suffix}"

        be = mc.break_even_cost(base_row["net_pnl"], by[lab("base", 0.0)]["net_pnl"], base_row["spread_impact_paid"])
        control = None
        if a.control_runs > 0:
            def tick(n: int, vname: str = vname) -> None:
                if n % 100 == 0:
                    print(f"  control {vname}: {n} of {a.control_runs}, {time.time() - t0:.0f}s", flush=True)
            control = mc.exposure_matched_control(m, pol, spec, book, r, i0, i1, runs=a.control_runs, seed=112,
                                                  equity0=a.equity, progress=tick, fills=od.fills(spec))
            if control.get("control_result"):
                (out / tag / f"control_{vname}.json").write_text(json.dumps(control["control_result"], default=str))
            base_row["control"] = {k: control.get(k) for k in ("kind", "runs", "sharpe_percentile",
                                                               "cagr_percentile", "median", "matched")}
        nb: dict[str, dict] = {}
        nb_dist: dict[str, dict] = {}
        if is_base:
            for nspec in od.neighbours(rule):
                if seeded:
                    st = {s: one(nspec, "base", 1.0, 100.0, s, "seed", log=False, files=False)[0] for s in seeds}
                    nm = od.median_seed(st)
                    nb_dist[nspec.name] = od.distribution(list(st.values()))
                    nrow = one(nspec, "base", 1.0, 100.0, nm, "neighbour")[0]
                else:
                    nrow = one(nspec, "base", 1.0, 100.0, None, "neighbour")[0]
                rows.append(nrow)
                nb[nspec.name.split("~")[1]] = nrow
        gate = mc.gate_b(base_row, by.get(lab("base", 2.0)), by.get(lab("base", 3.0)), bench["spy"], control, nb, be,
                         spy_tr=bench.get("spy_total_return"), universe="point_in_time")
        extra = []
        if seeded:
            share = dist["base|1x"].get("share_passing_mark1_pct", 0.0)
            extra.append({"mark": "ath-1", "text": "at least half of the seeds at 10 positions pass mark 1",
                          "verdict": "pass" if share >= 50.0 else "fail",
                          "detail": f"{share}% of {len(seeds)} seeds pass mark 1"})
        versions[vname] = {"rows": rows, "gate_b": gate, "extra_marks": extra, "break_even": be,
                           "control": control and {k: v for k, v in control.items()
                                                   if k not in ("sharpe", "cagr_pct", "max_drawdown_pct",
                                                                "return_pct", "avg_exposure_pct")},
                           "distribution": dist, "neighbour_distribution": nb_dist, "median_seed": med}
        print(f"  {vname}: done, {time.time() - t0:.0f}s", flush=True)
    rep = {"set": "oneshots", "kind": "daily", "rule": rule, "status": STATUS, "run_at": run_at.isoformat(),
           "code_sha": ctx["code_sha"], "prereg": pre, "gate_v2_sha256": gate_sha, "level": a.level,
           "universe": ctx["universe"], "universe_mode": "point_in_time", "bars": "store", "data": info,
           "earnings": ev_info, "start": a.start, "end": a.end, "equity": a.equity, "control_runs": a.control_runs,
           "seeds": a.seeds if seeded else None, "window": ctx["window"], "input_sha256": input_sha,
           "symbols": len(symbols), "benchmarks": bench, "versions": versions,
           "verdict": daily_verdict(versions, rule), "runtime_s": round(time.time() - t0, 1),
           "peak_rss_mb": round(peak_rss_mb(), 0)}
    write_trials(data, trials)
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))  # last: "this rule is finished"
    print(f"{rule}: {rep['verdict']}; runtime {rep['runtime_s']}s")


def daily_verdict(versions: dict, rule: str) -> str:
    parts = []
    for v, x in versions.items():
        if "gate_b" not in x:
            continue
        marks = [*x["gate_b"], *x.get("extra_marks", [])]
        failed = [str(g["mark"]) for g in marks if g["verdict"] == "fail"]
        parts.append(f"{v}: " + (f"FAIL (mark {', '.join(failed)})" if failed else
                                 "no mark failed (provisional: cannot pass here)"))
    return "; ".join(parts)


# ---------------------------------------------------------------- intraday rules


def file_fp(*dirs: Path) -> str:
    import hashlib

    h = hashlib.sha256(CACHE_VERSION.encode())
    for d in dirs:
        for f in sorted(d.glob("*.parquet")) if d.is_dir() else ():
            st = f.stat()
            h.update(f"{d.name}/{f.name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def pass_one(rule: str, data: Path, symbols: list[str], start: str, end: str, t0: float) -> dict[str, dict]:
    """Per-symbol rows of pass 1, cached in data/oneshots/<rule>/ (resumable). symbol -> {"rows", "meta"}."""
    cache = data / "oneshots" / rule
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    lo, hi = np.datetime64(start, "D"), np.datetime64(end, "D")
    for n, s in enumerate(symbols):
        bdir, ndir = data / "bars_1m" / f"symbol={s}", data / "news" / f"symbol={s}"
        fp = file_fp(bdir, *((ndir,) if rule == "news_continuation" else ())) + f"|{start}|{end}"
        meta_f, rows_f = cache / f"{s}.json", cache / f"{s}.parquet"
        if meta_f.is_file() and rows_f.is_file():
            meta = json.loads(meta_f.read_text())
            if meta.get("fingerprint") == fp:
                out[s] = {"rows": pd.read_parquet(rows_f), "meta": meta}
                continue
        sd = load_symbol(data, s, lo, hi)
        if sd is None:
            continue
        months = sorted({str(d) for d in sd.dates.astype("datetime64[M]")})
        if rule == "orb_stocks_in_play":
            rows, extra = oi.orb_symbol_rows(sd), {}
        else:
            nt = load_news_times(data, s)
            rows = oi.news_symbol_rows(sd, nt)
            extra = {"headline_months": oi.headline_months(nt), "last_headline": str(nt[-1]) if len(nt) else None}
        meta = {"fingerprint": fp, "bar_months": months, **extra}
        rows.to_parquet(rows_f, index=False)
        meta_f.write_text(json.dumps(meta))
        out[s] = {"rows": rows, "meta": meta}
        if n % 10 == 0:
            print(f"  pass 1: {n + 1} of {len(symbols)} symbols ({s}), {time.time() - t0:.0f}s", flush=True)
    return out


def spy_sessions(data: Path, spy: str, start: str, end: str) -> tuple[np.ndarray, np.ndarray]:
    sd = load_symbol(data, spy, np.datetime64(start, "D"), np.datetime64(end, "D"))
    if sd is None:
        raise Skip(f"no {spy} minute bars under {data / 'bars_1m'}: run ./scripts/fetch_history.sh first")
    c = sd.c[:, -1]
    return sd.dates, np.r_[np.nan, c[1:] / c[:-1] - 1]


def run_intraday_rule(a: argparse.Namespace, rule: str) -> None:
    t0 = time.time()
    data = Path(a.data)
    if not (data / "bars_1m").is_dir():
        raise Skip(f"no minute history at {data / 'bars_1m'}: run ./scripts/fetch_history.sh first")
    if rule == "news_continuation" and not (data / "news").is_dir():
        raise Skip(f"no news history at {data / 'news'}: run scripts/fetch_history.py --news first")
    pre = prereg(rule)
    gate_sha = GateV2.load(ROOT).sha
    cfg = yaml.safe_load(Path(a.universe_file).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    folds = make_folds(first_train_month(a.start, WARMUP_DAYS), np.datetime64(a.end, "D"))
    test_start = str(folds[0].test_start) if folds else a.start
    sessions, spy_ret = spy_sessions(data, cfg["market"], a.start, a.end)
    keep = sessions >= np.datetime64(test_start, "D")
    sessions, spy_ret = sessions[keep], spy_ret[keep]
    if not len(sessions):
        raise Skip("no SPY session in the test window")
    print(f"pre-registration {pre['file']} sha256 {pre['sha256'][:12]}; {len(stocks)} stocks; sessions "
          f"{sessions[0]} to {sessions[-1]}", flush=True)
    got = pass_one(rule, data, stocks, a.start, a.end, t0)
    if not got:
        raise Skip("no minute bars for any stock of the universe")
    frames = [g["rows"] for g in got.values() if len(g["rows"])]
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(rows):
        rows = rows[pd.to_datetime(rows["date"]) >= pd.Timestamp(test_start)]
    run_at = datetime.now(UTC)
    tag = f"oneshots_{rule}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    ctx = {"tag": tag, "tdir": tdir, "run_at": run_at, "rule": rule, "level": a.level,
           "universe": Path(a.universe_file).name, "mode": "today", "input_sha": file_fp(*(
               data / "bars_1m" / f"symbol={s}" for s in got)), "code_sha": code_sha(), "prereg": pre,
           "gate_sha": gate_sha, "window": [str(sessions[0]), str(sessions[-1])]}
    trials = []
    spy_stats = mc.series_stats(sessions, np.nan_to_num(spy_ret))
    rep = {"set": "oneshots", "kind": "intraday", "rule": rule, "status": STATUS.replace(
        "research harness of #113", "research harness of #113 on today's stock list (survivorship bias)"),
        "run_at": run_at.isoformat(), "code_sha": ctx["code_sha"], "prereg": pre, "gate_v2_sha256": gate_sha,
        "level": a.level, "universe": ctx["universe"], "universe_mode": "today", "start": a.start, "end": a.end,
        "control_runs": a.control_runs, "seeds": None, "window": ctx["window"], "symbols": len(got),
        "benchmarks": {"spy": spy_stats}}

    def curve(name: str, trades: pd.DataFrame, weight: float, params: dict, fill: str = "base") -> dict:
        rets = oi.account(trades, sessions, weight)
        st = mc.series_stats(sessions, rets)
        pd.DataFrame({"date": sessions.astype(str), "ret": rets}).to_csv(out / tag / f"daily_{name}.csv", index=False)
        trades.drop(columns=[c for c in ("rand_r",) if c in trades]).to_csv(out / tag / f"trades_{name}.csv",
                                                                            index=False)
        trials.append(trial_row(ctx, name, name, params, fill, 1.0, (sessions, rets), {
            "trades": int(len(trades)), "net_return_pct": st["return_pct"], "cagr_pct": st["cagr_pct"],
            "daily_sharpe_ann": st["sharpe"], "max_drawdown_pct": st["max_drawdown_pct"]}))
        return st

    if rule == "orb_stocks_in_play":
        base = oi.orb_select(rows)
        st = oi.trade_stats(base)
        variants = {"base": {"trades": st, "account": curve("orb_stocks_in_play", base, 1 / 20,
                                                             {"top_n": 20, "stop_atr": 0.10, "fill": "pessimistic"})}}
        trig = oi.orb_select(rows, fill="trig")
        variants["trigger_fill"] = {"trades": oi.trade_stats(trig), "note": "same trades at the trigger price"}
        variants["gross"] = {"trades": oi.trade_stats(base, "r_gross"), "note": "base trades before any cost"}
        for nb, kw in (("top10", {"top_n": 10}), ("top40", {"top_n": 40}), ("stop20", {"stop": "stop20"})):
            t = oi.orb_select(rows, **kw)
            variants[nb] = {"trades": oi.trade_stats(t), "account": curve(
                f"orb_stocks_in_play~{nb}", t, 1 / kw.get("top_n", 20), kw)}
        rep.update(variants=variants, verdict_detail=oi.orb_verdict(st))
        rep["verdict"] = rep["verdict_detail"]["verdict"]
    else:
        months = oi.complete_months({s: g["meta"].get("headline_months", {}) for s, g in got.items()},
                                    {s: g["meta"]["bar_months"] for s, g in got.items()},
                                    max((np.datetime64(g["meta"]["last_headline"]) for g in got.values()
                                         if g["meta"].get("last_headline")), default=None))
        months = [x for x in months if x >= test_start[:7]]
        rep["complete_news_months"] = oi.month_ranges(months)
        rep["complete_news_month_count"] = len(months)
        in_months = np.isin(sessions.astype("datetime64[M]").astype(str), months)
        rep["sessions_in_complete_months"] = int(in_months.sum())
        base = oi.news_select(rows, "base", months, stocks)
        st = oi.trade_stats(base)
        acc = curve("news_continuation", base, oi.NEWS_WEIGHT, dataclasses.asdict(oi.NEWS_VARIANTS["base"]))
        sess_m = sessions[in_months]
        rule_m = mc.series_stats(sess_m, oi.account(base, sess_m, oi.NEWS_WEIGHT)) if len(sess_m) else None
        spy_m = mc.series_stats(sess_m, np.nan_to_num(spy_ret[in_months])) if len(sess_m) else None
        rnd = oi.random_entry_percentile(base, st["mean_r"] if st["mean_r"] is not None else 0.0, oi.GATE[
            "random_runs"]) if len(base) else {"runs": 0, "percentile": None}
        marks = oi.news_marks(st, rnd, rule_m["sharpe"] if rule_m else 0.0, spy_m["sharpe"] if spy_m else 0.0)
        variants = {"base": {"trades": st, "account": acc, "account_complete_months": rule_m, "random": rnd}}
        for nb in oi.NEWS_VARIANTS:
            if nb != "base":
                t = oi.news_select(rows, nb, months, stocks)
                variants[nb] = {"trades": oi.trade_stats(t), "account": curve(
                    f"news_continuation~{nb}", t, oi.NEWS_WEIGHT, dataclasses.asdict(oi.NEWS_VARIANTS[nb]))}
        rep.update(variants=variants, marks=marks, spy_complete_months=spy_m)
        failed = [x["mark"] for x in marks if x["verdict"] == "fail"]
        rep["verdict"] = (f"FAIL ({'; '.join(failed)})" if failed else
                          "no mark failed (provisional: cannot pass here without the deflated Sharpe)")
    rep["runtime_s"] = round(time.time() - t0, 1)
    rep["peak_rss_mb"] = round(peak_rss_mb(), 0)
    write_trials(data, trials)
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    print(f"{rule}: {rep['verdict']}; runtime {rep['runtime_s']}s")


# ---------------------------------------------------------------- the one table


def _row_daily(rep: dict) -> list[list]:
    out = []
    for v, x in rep["versions"].items():
        base = x["rows"][0]
        d = x.get("distribution", {}).get("base|1x")
        what = (f"CAGR {base['cagr_pct']}%, Sharpe {base['sharpe']}, max DD {base['max_drawdown_pct']}%, "
                f"{base['trades']} trades, avg exposure {base['avg_exposure_pct']}%")
        if d:
            what = (f"median seed {x['median_seed']} of {d['runs']}: " + what + f"; CAGR p5/p50/p95 "
                    f"{d['cagr_pct']['p5']}/{d['cagr_pct']['p50']}/{d['cagr_pct']['p95']}%, "
                    f"{d['share_passing_mark1_pct']}% of seeds pass mark 1")
        ctl = (base.get("control") or {}).get("sharpe_percentile")
        if ctl is not None:
            what += f"; Sharpe pctile vs exposure-matched random {ctl}"
        if "gate_b" in x:
            marks = [*x["gate_b"], *x.get("extra_marks", [])]
            failed = [str(g["mark"]) for g in marks if g["verdict"] == "fail"]
            verdict = f"**FAIL** (mark {', '.join(failed)})" if failed else "not failed (provisional, cannot pass)"
        else:
            verdict = "reported (distribution only)"
        out.append([f"`{v}`", "point-in-time S&P 500, daily store", f"{rep['window'][0]} to {rep['window'][1]}",
                    what, verdict])
    return out


def _row_intraday(rep: dict) -> list[list]:
    st = rep["variants"]["base"]["trades"]
    acc = rep["variants"]["base"].get("account") or {}
    what = (f"{st['trades']} trades, mean net R {st['mean_r']}, t {st['t']}, total R without the 10 best symbols "
            f"{st.get('total_r_without_best10')}; account CAGR {acc.get('cagr_pct')}%, Sharpe {acc.get('sharpe')}")
    uni = "today's list, minute bars (survivorship bias)"
    if rep["rule"] == "orb_stocks_in_play":
        verdict = "**KEEP**" if rep["verdict"] == "keep" else "**FAIL: propose deletion**"
    else:
        verdict = "**FAIL**" if rep["verdict"].startswith("FAIL") else "not failed (provisional, cannot pass)"
        uni += f"; complete news months: {', '.join(rep.get('complete_news_months') or ['none'])}"
        rnd = rep["variants"]["base"].get("random") or {}
        what += f"; random-entry pctile {rnd.get('percentile')}"
    return [[f"`{rep['rule']}`", uni, f"{rep['window'][0]} to {rep['window'][1]}", what, verdict]]


def summary(data: Path) -> str:
    reports = data / "reports"
    table, notes = [], []
    for rule in RULES:
        hit = find_report(reports, rule)
        if hit is None:
            skip = reports / f"oneshots_{rule}_skipped.json"
            why = json.loads(skip.read_text()).get("reason") if skip.is_file() else "not run yet"
            table.append([f"`{rule}`", "", "", f"skipped: {why}" if skip.is_file() else why, "not judged"])
            continue
        f, rep = hit
        table += _row_daily(rep) if rep["kind"] == "daily" else _row_intraday(rep)
        notes.append(f"- `{rule}`: pre-registration `{rep['prereg']['file']}` (sha256 "
                     f"`{rep['prereg']['sha256'][:12]}`), report `{f.name}`, code `{rep['code_sha']}`, runtime "
                     f"{rep['runtime_s']} s.")
    spy = None
    for rule in DAILY:
        hit = find_report(reports, rule)
        if hit and "spy_total_return" in hit[1]["benchmarks"]:
            spy = hit[1]["benchmarks"]["spy_total_return"]
            break
    head = ["Rule / version", "Universe and data", "Window", "Result (base version)", "Pass / fail"]
    out = ["# One-shot ten-year tests (issue #112, story S72)", "",
           "Six low-prior rules, one frozen pre-registered run each (`docs/prereg/<rule>.md`); nothing tuned. "
           "**PROVISIONAL (decision 15):** the research harness of #113 with cost model v1. A rule can FAIL here; "
           "none can pass here (the go/no-go run is the engine with `SimBroker`). The ORB verdict is keep or "
           "propose deletion (issue #112).", ""]
    if spy:
        out += [f"SPY total return over the daily rules' window: CAGR {spy['cagr_pct']}%, Sharpe {spy['sharpe']}, "
                f"max drawdown {spy['max_drawdown_pct']}%.", ""]
    out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in table]
    out += ["", "Daily rules: Gate B marks 1-6 of decision 15 section 7 on the base version (base fill, 1x costs, "
            "100% cap), SPY total return, the exposure-matched random control (section 10); `ath_breakout` is a "
            "distribution over random signal subsets, judged on its median-Sharpe seed at 10 positions. ORB: kept "
            "only if mean net R >= +0.05 with t >= 3 and still positive without its ten best symbols. News "
            "continuation: the intraday bar of config/gate.yaml over the complete news months only.", "",
            "## Notes", "", *notes, ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", choices=RULES)
    ap.add_argument("--has", choices=RULES)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--control-runs", type=int, default=500)
    ap.add_argument("--seeds", type=int, default=200, help="ath_breakout: random signal subsets per setting")
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        same = {"start": a.start, "end": a.end, "universe": Path(a.universe_file).name}
        if a.has in DAILY:
            same["control_runs"] = a.control_runs
        if a.has == "ath_breakout":
            same["seeds"] = a.seeds
        sys.exit(0 if find_report(data / "reports", a.has, **same) else 1)
    if a.summary:
        text = summary(data)
        (data / "reports").mkdir(parents=True, exist_ok=True)
        (data / "reports" / SUMMARY).write_text(text)
        print(text)
        return
    if not a.rule or not a.end:
        ap.error("--rule and --end are required for a run")
    skip_f = data / "reports" / f"oneshots_{a.rule}_skipped.json"
    try:
        (run_intraday_rule if a.rule in INTRADAY else run_daily_rule)(a, a.rule)
    except Skip as e:
        print(f"{a.rule}: SKIPPED: {e}")
        skip_f.parent.mkdir(parents=True, exist_ok=True)
        skip_f.write_text(json.dumps({"rule": a.rule, "reason": str(e), "at": datetime.now(UTC).isoformat()}))
        sys.exit(SKIPPED)
    skip_f.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
