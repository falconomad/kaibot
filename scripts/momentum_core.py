"""Ten-year test of the momentum core (issue #127, decision 15 rules 1 and 2): `trend_momentum` and
`mom_12_1_band` (with and without the index filter) in the multi-day research harness of #113. Research only:
reads the history under data/, never touches the engine or the broker. PROVISIONAL: today's stock list, cost
model v1, price-only SPY; cannot pass Gate B until S71, S83 and S84 have landed.

python scripts/momentum_core.py --version trend_momentum|mom_12_1_band|mom_12_1_band_nofilter --end 2026-11-06
    [--universe today|pit|config/universe_history.yaml] [--universe-file config/universe_history.yaml]
    [--bars minute|store] [--start 2016-01-01] [--level balanced] [--control-runs 500] [--entries-control-runs 0]
    [--earnings auto|off] [--refresh-daily] [--costs v1|v2]
python scripts/momentum_core.py --has <version> --start ... --end ... --universe ... --control-runs N   exit 0 if done
python scripts/momentum_core.py --summary [--set core|core_pit]   one table, written to
    data/reports/experiments_core_summary.md (core_pit: experiments_core_pit_summary.md, with today vs pit)

--costs v2 (issue #111) prices every fill with kaibot.broker.costs_v2 (2x / 3x then scale v2's spread and impact);
the default v1 reproduces earlier reports. v2 reports carry "costs": "v2", are found only with --costs v2 (by
--has and --summary too) and are summarised in experiments_core[_pit]_summary_costsv2.md. With the daily store
(--bars store, --universe pit) v2 is given raw prices, as it is meant to be (the audit E4 caveat does not apply).

Universe (issue #143). `today` (the default, so earlier reports reproduce): the stocks of --universe-file, daily
bars built from data/bars_1m (`--bars minute`, as in #127). `pit`: the S&P 500 as it was (#123,
`kaibot.ingest.sp500_pit.Membership`): on day T the rule can buy only companies that were members on T and pass
the price and dollar-volume filter on T; a company that leaves the index drops out of the ranked universe and is
sold on the next decision day (choice 6 / 4 of the pre-registrations); a held company whose bars stop is closed at
its last bar at the hole policy's terminal return. `pit` reads every symbol from the daily store (#124, data/daily,
`--bars store`): signals on split-adjusted prices without dividends, orders, costs, share counts and the dollar
filters on raw prices, dividends credited to held positions. `--universe today --bars store` runs today's list on
the store (isolates the data change from the universe change). The base fill is at the close, so the 15:55 price
is not used; the store has no minute data, so the first / last minute volumes (impact at the open / close) are 2%
of the day's volume for every symbol, and the close is the official close (normally the closing auction), not the
last regular-hours minute of the pre-registrations (a few basis points).

Each frozen configuration is ONE continuous $100k account from the first test month (2017-05 with the default
start) to --end, starting flat: no walk-forward tuning. The grid is a set of reports of one frozen rule, declared
in docs/prereg/: fill model (base: next close; stress: next open at doubled spread and impact) x costs (1x, 2x,
3x, and 0x to state the break-even multiple) x exposure cap (30 / 60 / 100%) x earnings (hold through; exit before =
approximate, skipped when the estimate is unreliable), then the five neighbours and the matched random control.

Writes data/reports/core_<level>_<version>[_pit|_store]_<ts>.json, one trades and one daily CSV per run (the
daily CSV carries each session's `cost`, `traded` and `dividends`), `spy_daily.csv` (SPY per session: total return
when the store has it, and price only) and one control JSON per exposure cap (`--random` input of
scripts/gate_v2_report.py) under the folder of the same name, and one trial row per run in data/trials.jsonl. A run
with the base fill at 1x costs counts as one configuration (n_trials 1); its cost and fill stress reports count 0.
Every report and trial row carries the pre-registration's sha256 (`prereg.require`: no committed pre-registration,
no run) and the sha256 of config/gate_v2.yaml.
Gate B mark 3 uses the exposure-matched control (decision 15 section 10; `multiday_core.exposure_matched_control`),
500 runs per exposure cap by default. SPY total return comes from the daily store whenever it holds SPY (also for
`--bars minute`); without it mark 2 is not judged.
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

from kaibot.broker.costs_v2 import MODELS, get_model  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation.gate_v2 import GateV2  # noqa: E402
from kaibot.replay import multiday_core as mc  # noqa: E402
from kaibot.replay.multiday import MTrade, bars_fingerprint  # noqa: E402
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
SUMMARY_PIT = "experiments_core_pit_summary.md"
CONTROL_KIND = "exposure_matched"
STATUS_PIT = ("PROVISIONAL (decision 15): research harness of #113 on the point-in-time S&P 500 (#123) and the daily "
              "store (#124: raw prices, dividends, SPY total return), cost model v1; it can fail Gate B, it cannot "
              "pass it (the go/no-go run is the engine with `SimBroker`, and cost model v2 is S71)")
PIT_HEAD = ("**Point-in-time universe:** on each day the rules could buy only the companies that were in the S&P 500 "
            "that day (#123), including those that later left the index, were taken over or failed; prices come "
            "from the daily store (#124): signals on split-adjusted closes without dividends, orders, costs and "
            "share counts on raw prices, dividends credited, SPY with dividends reinvested.")
PROVISIONAL_PIT = ("**PROVISIONAL (decision 15):** research harness of #113, cost model v1. Gate B marks 1 to 6 are "
                   "judged where their inputs exist, but nothing here can pass Gate B: the go/no-go run is the "
                   "engine with `SimBroker` (S75), on cost model v2 (S71).")


def prereg(rule: str) -> dict:
    """The pre-registration a run belongs to: `kaibot.evaluation.prereg.require` refuses a missing, unfinished
    or uncommitted file (decision 15, Gate A); the run logs the committed file's sha256."""
    try:
        sha = prereg_mod.require(rule, ROOT)
        status = prereg_mod.load(rule, ROOT, committed=False).status
    except prereg_mod.PreregError as e:
        sys.exit(f"no run: {e}")
    return {"file": str(prereg_mod.path_for(rule, ROOT).relative_to(ROOT)), "sha256": sha, "status": status}


def universe_mode(a: argparse.Namespace) -> tuple[str, str, str]:
    """(universe file, "today" | "point_in_time", "minute" | "store") from --universe / --universe-file / --bars."""
    u = a.universe
    yaml_file = a.universe_file if u in ("today", "pit") else u
    mode = "point_in_time" if u == "pit" else "today"
    bars = a.bars or ("store" if mode == "point_in_time" else "minute")
    if mode == "point_in_time" and bars != "store":
        sys.exit("--universe pit reads the daily store: former members have no minute bars (--bars store)")
    return yaml_file, mode, bars


def report_tag(level: str, version: str, mode: str, bars: str) -> str:
    return f"core_{level}_{version}" + ("_pit" if mode == "point_in_time" else "_store" if bars == "store" else "")


# ---------------------------------------------------------------- the data of one run


def store_fingerprint(store_root: Path, keys, extra: str = "") -> str:
    h = hashlib.sha256(extra.encode())
    for k in sorted(keys):
        f = store_root / "bars" / f"symbol={k}.parquet"
        if f.is_file():
            st = f.stat()
            h.update(f"{k}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    f = store_root / "corporate_actions.parquet"
    if f.is_file():
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def spy_total_return_from_store(data: Path, spy: str, dates: np.ndarray) -> tuple[np.ndarray | None, dict | str]:
    """SPY's total return per session of `dates` from the daily store (NaN where the store has no bar), built by
    `multiday_core.spy_total_return` (Alpaca's adjusted factor, or a table with every dividend, or the table
    completed conservatively), or None with the reason."""
    from kaibot.ingest.daily_store import DailyStore

    root = data / "daily"
    if not (root / "bars" / f"symbol={spy}.parquet").is_file():
        return None, f"no {spy} in the daily store ({root}): run ./scripts/fetch_daily.sh"
    try:
        panel = DailyStore(root).panel([spy], minute_share=None)
    except (OSError, ValueError) as e:
        return None, f"daily store unreadable for {spy}: {e}"
    r, info = mc.spy_total_return(panel, spy)
    s = pd.Series(r, index=panel.dates.astype("datetime64[D]"))
    return s.reindex(dates.astype("datetime64[D]")).to_numpy(dtype=float), info


def load_market(a: argparse.Namespace, data: Path, stocks: list[str], etfs: list[str], spy: str, mode: str,
                bars_src: str, t0: float) -> tuple[mc.Market, str, list[str], dict]:
    """The `Market` of one run, its input fingerprint, the symbols it read and what the report says about them."""
    if bars_src == "minute":
        symbols = [*stocks, *etfs, spy]  # the same list as the multiday set, so its daily-bar cache is reused

        def progress(n: int, s: str) -> None:
            if n % 25 == 0:
                print(f"  daily bars: {n} of {len(symbols)} symbols ({s}), {time.time() - t0:.0f}s", flush=True)

        bars, built = wfm.load_or_build_daily(data, symbols, a.start, a.end, refresh=a.refresh_daily,
                                              progress=progress)
        if not np.isfinite(bars.c[:, bars.col(spy)]).any():
            sys.exit(f"no {spy} bars under {data / 'bars_1m'} between {a.start} and {a.end}: nothing to run on")
        bars = bars.keep_sessions(np.isfinite(bars.c[:, bars.col(spy)]))  # the session calendar is SPY's
        print(f"daily bars: {len(bars.dates)} sessions x {len(bars.symbols)} symbols "
              f"({'built and cached' if built else 'from the cache'}), {time.time() - t0:.0f}s", flush=True)
        spy_tr, note = spy_total_return_from_store(data, spy, bars.dates)
        m = mc.Market.from_bars(bars, bars.cols(stocks), bars.col(spy), spy_tr)
        info = {"bars": "minute history (data/bars_1m): split-adjusted as of the download, no dividends",
                "spy_total_return": note}
        if spy_tr is not None:
            print(f"SPY total return: {note['source']}", flush=True)
        return m, bars_fingerprint(data, symbols, a.start, a.end), symbols, info
    from kaibot.ingest.daily_store import DailyStore
    from kaibot.ingest.sp500_pit import Membership, read_outcomes, terminal_return

    root = data / "daily"
    store = DailyStore(root)
    have = set(store.keys())
    if spy not in have:
        sys.exit(f"no {spy} in the daily store {root}: run ./scripts/fetch_daily.sh first (docs/ops/daily_bars.md)")
    member = None
    if mode == "point_in_time":
        ms = Membership.load(universe=Path(a.universe_file))
        cand = [*stocks, *sorted(have - set(stocks) - set(etfs) - {spy})]
        km = ms.key_map(cand)  # company -> column of its first stored key
        chosen = set(km.values())
        names = [cand[j] for j in sorted(chosen)]
        company = {cand[j]: c for c, j in km.items()}
        missing = sorted(c for c in ms.spells if c not in km)
        outcomes = read_outcomes()
        tr = {k: terminal_return(c, outcomes) for k, c in company.items()}
    else:
        names, missing, tr = [s for s in stocks if s in have], sorted(set(stocks) - have), {}
    symbols = [*names, *[e for e in etfs if e in have], spy]
    print(f"daily store: {len(names)} stocks, {len(symbols) - len(names) - 1} ETFs and SPY from {root}, "
          f"{time.time() - t0:.0f}s", flush=True)
    panel = store.panel(symbols, a.start, a.end)
    if mode == "point_in_time":
        member = ms.mask(panel.dates, panel.symbols)
    rep = store.adjustment_report()
    bad = rep[rep.get("split_basis_days", pd.Series(dtype=float)).fillna(0) > 0]["key"].astype(str).tolist() \
        if len(rep) else []
    m = mc.Market.from_panel(panel, names, spy, member=member, terminal_returns=tr, disputed=bad)
    print(f"panel: {len(m.raw.dates)} sessions x {len(m.raw.symbols)} symbols, {time.time() - t0:.0f}s", flush=True)
    print(f"SPY total return: {m.notes['spy_total_return']['source']}", flush=True)
    info = {"bars": "daily store (data/daily): raw bars; signals split-adjusted without dividends; orders, costs and "
                    "share counts raw; dividends credited",
            "stocks": len(names), "companies_without_bars": len(missing),
            "companies_without_bars_sample": missing[:25], "excluded_split_basis": bad, **m.notes}
    extra = "|".join([a.start, a.end, mode, *symbols])
    if mode == "point_in_time":
        from kaibot.ingest.sp500_pit import CHANGES

        extra += "|" + hashlib.sha256(CHANGES.read_bytes()).hexdigest()
    return m, store_fingerprint(root, symbols, extra), symbols, info


def run_label(spec_name: str, fill: str, cost: float, exposure_pct: float, earnings: str) -> str:
    return f"{spec_name}|{fill}|{cost:g}x|cap{exposure_pct:g}|{earnings}"


def summary_name(costs: str = "v1", which: str = "core") -> str:
    name = SUMMARY_PIT if which == "core_pit" else SUMMARY
    return name if costs == "v1" else name.replace(".md", f"_costs{costs}.md")


def find_report(reports: Path, level: str, version: str, costs: str = "v1", mode: str = "today",
                bars: str | None = None, **same) -> tuple[Path, dict] | None:
    """Newest finished report of `version` on universe `mode` (and `bars`: None takes minute bars for today and
    the store for pit; and, when given, with the same start, end, universe file, control runs, control kind)."""
    bars = bars or ("store" if mode == "point_in_time" else "minute")
    for f in sorted(reports.glob(f"core_{level}_{version}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != "core" or rep.get("version") != version or "rows" not in rep:
            continue
        if rep.get("universe_mode", "today") != mode or rep.get("bars", "minute") != bars:
            continue
        if rep.get("costs", "v1") != costs:  # reports from before #111 are v1
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
    gate_sha = GateV2.load(ROOT).sha
    uni_file, mode, bars_src = universe_mode(a)
    print(f"pre-registration {pre['file']} sha256 {pre['sha256'][:12]} ({pre['status']}); config/gate_v2.yaml "
          f"sha256 {gate_sha[:12]}; universe {mode}, bars {bars_src}", flush=True)
    cfg = yaml.safe_load(Path(uni_file).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    spy = cfg["market"]
    pol = load_profiles()[a.level]
    costs = None if a.costs == "v1" else get_model(a.costs)
    m, input_sha, symbols, data_info = load_market(a, data, stocks, list(cfg["sectors"]), spy, mode, bars_src, t0)
    bars = m.raw
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
    stock_names = [bars.symbols[j] for j in m.stocks]
    earn, hold_blk, entry_blk = wfm.earnings_filter(data, bars, stock_names, a.earnings)
    earn_modes = ["hold"] + (["exit_approx"] if hold_blk is not None else [])
    if hold_blk is None:
        print(f"exit-before-earnings variant SKIPPED: {earn['note']}", flush=True)
    else:
        print(f"exit-before-earnings variant runs (APPROXIMATE): {earn['note']}", flush=True)

    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    ctag = "" if a.costs == "v1" else f"_costs{a.costs}"
    tag = f"{report_tag(a.level, a.version, mode, bars_src)}{ctag}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    rows, trials, books = [], [], {}

    def one(sp: mc.CoreSpec, kind: str, fill: str, cost: float, exposure_pct: float, earnings: str) -> dict:
        exposure, earn_mode = exposure_pct, earnings
        blk = (hold_blk, entry_blk) if earn_mode == "exit_approx" else (None, None)
        book, rule = m.run(pol, sp, i0, i1, fill=fill, cost=cost, exposure_pct=exposure, equity0=a.equity,
                           hold_blk=blk[0], entry_blk=blk[1], costs=costs)
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
            "variant": "multiday_core",
            "label": f"momentum core {label}{'' if a.costs == 'v1' else ' costs ' + a.costs}",
            "level": a.level, "cost_model": a.costs,
            "universe": Path(uni_file).name, "universe_mode": mode, "bars": bars_src,
            "events_sha256": row["events_sha256"], "input_sha256": input_sha,
            "code_sha": code_sha, "experiment": a.version, "status": "provisional", "prereg_sha256": pre["sha256"],
            "gate_v2_sha256": gate_sha,
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
    spy_r = m.spy_price_only(i0, i1)
    spy_tr = m.spy_total_return(i0, i1)
    bench = {"spy": mc.series_stats(dates, spy_r),
             "spy_scaled": {**mc.series_stats(dates, spy_r * base["avg_exposure_pct"] / 100),
                            "scale_pct": base["avg_exposure_pct"]},
             "universe_ew": mc.series_stats(dates, m.equal_weight(i0, i1, a.equity, costs))}
    if spy_tr is not None:
        bench["spy_total_return"] = mc.series_stats(dates, spy_tr)
        bench["spy_tr_scaled"] = {**mc.series_stats(dates, spy_tr * base["avg_exposure_pct"] / 100),
                                  "scale_pct": base["avg_exposure_pct"]}
    # `ret` is what scripts/gate_v2_report.py --spy reads: total return when the store has it, else price only
    pd.DataFrame({"date": dates.astype(str), "ret": spy_tr if spy_tr is not None else spy_r,
                  "ret_total": spy_tr if spy_tr is not None else np.nan,
                  "ret_price_only": spy_r}).to_csv(out / tag / "spy_daily.csv", index=False)
    control, diagnostic = {}, {}
    for exposure in mc.EXPOSURES if a.control_runs > 0 else ():
        label = run_label(spec.name, "base", 1.0, exposure, "hold")
        book, rule = books[label]

        def tick(n: int, exposure=exposure) -> None:
            if n % 50 == 0:
                print(f"  exposure-matched control, cap {exposure:g}%: {n} of {a.control_runs} runs, "
                      f"{time.time() - t0:.0f}s", flush=True)

        c = mc.exposure_matched_control(m, pol, spec, book, rule, i0, i1, runs=a.control_runs,
                                        seed=a.control_seed, exposure_pct=exposure, equity0=a.equity, progress=tick,
                                        costs=costs)
        control[label] = c
        if c.get("control_result"):
            safe = label.replace("|", "_")
            (out / tag / f"control_{safe}.json").write_text(json.dumps(c["control_result"], indent=1, default=str))
        by_label[label]["control"] = {k: c.get(k) for k in ("kind", "runs", "sharpe_percentile", "cagr_percentile",
                                                            "median", "matched", "committed_exposure_gap_pts")}
    for exposure in mc.EXPOSURES if a.entries_control_runs > 0 else ():
        label = run_label(spec.name, "base", 1.0, exposure, "hold")
        book, rule = books[label]
        c = mc.matched_random_control(m.raw, m.ind, pol, m.signals, spec, book, rule, i0, i1,
                                      runs=a.entries_control_runs, seed=a.control_seed, exposure_pct=exposure,
                                      equity0=a.equity, costs=costs, actions=m.actions, terminal=m.terminal)
        diagnostic[label] = {k: c[k] for k in ("kind", "runs", "sharpe_percentile", "cagr_percentile", "median")}
    gate = mc.gate_b(base, by_label.get(run_label(spec.name, "base", 2.0, 100.0, "hold")),
                     by_label.get(run_label(spec.name, "base", 3.0, 100.0, "hold")), bench["spy"],
                     control.get(base_label), nb, break_even.get(base_label),
                     spy_tr=bench.get("spy_total_return"), universe=mode)
    status = (STATUS_PIT if mode == "point_in_time" else STATUS).replace("cost model v1", f"cost model {a.costs}")
    rep = {
        "run_at": run_at.isoformat(timespec="seconds"), "code_sha": code_sha, "set": "core", "status": status,
        "costs": a.costs,
        "version": a.version, "rule": spec.rule, "spec": spec.params(), "prereg": pre, "gate_v2_sha256": gate_sha,
        "level": a.level, "universe": Path(uni_file).name, "universe_mode": mode, "bars": bars_src,
        "data": data_info, "start": a.start, "end": a.end, "equity": a.equity,
        "control_runs": a.control_runs, "control_seed": a.control_seed, "control_kind": CONTROL_KIND,
        "entries_control_runs": a.entries_control_runs, "earnings": earn,
        "earnings_variant": "run (approximate)" if hold_blk is not None else "skipped",
        "oos_window": [str(bars.dates[i0]), str(bars.dates[i1])], "oos_sessions": int(i1 - i0 + 1),
        "input_sha256": input_sha, "symbols": len(symbols), "base_label": base_label, "rows": rows,
        "benchmarks": bench, "control": control, "control_entries_matched_diagnostic": diagnostic, "gate_b": gate,
        "warnings": warn, "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak_rss_mb(), 0),
        "files": {"spy_daily": f"{tag}/spy_daily.csv"},
        "caveats": caveats(mode, bars_src, spy_tr is not None, a.costs),
    }
    with (data / "trials.jsonl").open("a") as fh:
        for t in trials:
            fh.write(json.dumps(t, default=str) + "\n")
    # the report is written last: scripts/run_experiments.sh treats it as "this version is finished"
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    print(build_summary({a.version: (out / f"{tag}.json", rep)}))
    print(f"runtime {rep['runtime_s']}s, peak RSS {rep['peak_rss_mb']} MB")


def caveats(mode: str, bars_src: str, total_return: bool, costs: str = "v1") -> list[str]:
    out = []
    if mode == "point_in_time":
        out += ["point-in-time S&P 500 (#123): a company that left the index drops out of the ranked universe and is "
                "sold on the next decision day; a held company whose bars stop is closed at its last bar at the "
                "hole policy's terminal return (-100% for the three bank failures, 0% for known takeover terms, "
                "-30% otherwise); companies the store has no bars for cannot be bought (see data.companies_"
                "without_bars)"]
    else:
        out += ["survivorship bias: today's large caps applied back to 2016"]
    if bars_src == "store":
        out += ["daily store (#124): signals on split-adjusted closes without dividends; orders, costs, share "
                "counts and the dollar filters on raw prices; dividends credited to held positions (their own "
                "`dividends` line beside pnl)",
                "the close is the official close (normally the closing auction), not the last regular-hours minute "
                "of the pre-registrations; the first / last minute volumes are 2% of the day's volume"]
    else:
        out += ["prices are split-adjusted and carry no dividends, for the rule's signals, its holdings and the "
                "equal-weight benchmark" + ("" if total_return else "; SPY is price only"),
                "the close is the last regular-hours minute, not the auction"]
    out += ["SPY total return from the daily store is the mark-2 benchmark; price-only SPY is shown beside it"
            if total_return else "no SPY total return (daily store missing): mark 2 is not judged",
            "cost model v1 (uncalibrated, #111 / S71)" if costs == "v1" else
            f"cost model {costs} (#111: bucket half-spreads from the report, not yet fitted to quotes or paper fills)"
            + ("" if bars_src == "store" else "; prices are split-adjusted, so its half-cent floor still overstates "
               "early costs of heavily split names (audit E4)"),
            "the exit-before-earnings variant uses an estimated earnings window, not known dates (S85)",
            "Amendment 3: purchases are sized against cash minus a 3% of equity reserve and a buy still above the "
            "cash at the fill is cut to whole shares (no leverage ever); split fractions are paid in cash. Reports "
            "written before Amendment 3 had no reserve and kept split fractions",
            "the random control copies the rule's positions (entry and exit sessions, share of equity, resizes) "
            "and draws only the names; the marked exposure still differs because the names' prices differ"]
    return out


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


def _data_note(rep: dict) -> str:
    """Where SPY's total return came from, and the disputed splits (daily store)."""
    d = rep.get("data") or {}
    tr = d.get("spy_total_return")
    if isinstance(tr, dict):
        out = f" SPY total return: {tr.get('source')}"
        if tr.get("points_per_year_over_price") is not None:
            out += f" ({tr['points_per_year_over_price']} points a year over price only)"
        out += "."
    else:
        out = f" SPY total return: {tr}." if tr else ""
    disp = d.get("disputed_splits") or {}
    if disp:
        out += " Disputed splits (share count cannot be carried): " + "; ".join(
            f"{k}: {v}" for k, v in sorted(disp.items())) + "."
    return out


def build_summary(found: dict[str, tuple[Path, dict]], missing: tuple[str, ...] = ()) -> str:
    years = sorted({y for _, rep in found.values() for r in rep["rows"] for y in r["years"]})
    cols = (*HEAD, *years, *TAIL)
    table, gates, notes, windows = [], [], [], set()
    bench_done = False
    pit = any(rep.get("universe_mode") == "point_in_time" for _, rep in found.values())
    for version, (f, rep) in found.items():
        windows.add(tuple(rep["oos_window"]))
        shown = [r for r in rep["rows"] if r["kind"] != "break_even_input"]
        order = {"grid": 0, "neighbour": 1}
        shown.sort(key=lambda r: (r["label"] != rep["base_label"], order[r["kind"]], r["earnings"] != "hold",
                                  r["fill"] != "base", -r["exposure_pct"], r["cost"]))
        table += [_rule_row(r, years) for r in shown]
        b = rep["benchmarks"]
        if not bench_done:
            if "spy_total_return" in b:
                table.append(_cells("BENCHMARK: SPY buy-and-hold (total return, dividends reinvested)",
                                    b["spy_total_return"], years, **{"Avg exposure %": 100.0}))
            table.append(_cells("BENCHMARK: SPY buy-and-hold (price only, no dividends)", b["spy"], years,
                                **{"Avg exposure %": 100.0}))
            ew = ("BENCHMARK: equal-weight index members of each month, rebalanced monthly, after costs" if
                  rep.get("universe_mode") == "point_in_time" else
                  "BENCHMARK: equal-weight universe, rebalanced monthly, after costs")
            table.append(_cells(ew, b["universe_ew"], years, **{"Avg exposure %": 100.0}))
            bench_done = True
        if "spy_tr_scaled" in b:
            sc = b["spy_tr_scaled"]
            table.append(_cells(f"BENCHMARK: SPY (total return) scaled to {version}'s average exposure, rest in "
                                "cash", sc, years, **{"Avg exposure %": sc["scale_pct"]}))
        table.append(_cells(f"BENCHMARK: SPY (price only) scaled to {version}'s average exposure, rest in cash",
                            b["spy_scaled"], years, **{"Avg exposure %": b["spy_scaled"]["scale_pct"]}))
        for label, c in rep["control"].items():
            m = c["median"]
            if not m:
                continue
            cap = label.split("|")[3].removeprefix("cap")
            what = ("exposure-matched random portfolios for" if c.get("kind") == "exposure_matched"
                    else "random entries matched to")
            table.append({"Rule / version": f"BENCHMARK: {what} {version} (median of "
                                            f"{c['runs']} runs)", "Fill": "base", "Costs": "1x", "Cap %": cap,
                          "Earnings": EARN["hold"], "Total return %": m["return_pct"], "CAGR %": m["cagr_pct"],
                          "Sharpe": m["sharpe"], "Sharpe x 0.5": None if m["sharpe"] is None else round(
                              m["sharpe"] / 2, 2), "Max drawdown %": m["max_drawdown_pct"],
                          "Avg exposure %": m["avg_exposure_pct"]})
        gates += [f"### `{version}` (base fill, 1x costs, 100% cap, hold through earnings)", ""]
        gates += [f"- Mark {g['mark']} ({g['text']}): **{g['verdict']}**. {g['detail']}." for g in rep["gate_b"]]
        failed = [g["mark"] for g in rep["gate_b"] if g["verdict"] == "fail"]
        overall = f"fails Gate B (mark {', '.join(map(str, failed))})" if failed else "cannot pass Gate B yet"
        gates += [f"- Overall: {overall}; {mc.NOT_A_PASS}.", ""]
        skipped = max((r["skipped_under_one_share"] for r in rep["rows"]), default=0)
        gsha = rep.get("gate_v2_sha256")
        notes.append(
            f"- `{version}`: pre-registration `{rep['prereg']['file']}` (sha256 `{rep['prereg']['sha256'][:12]}`"
            + (f"; config/gate_v2.yaml sha256 `{gsha[:12]}`" if gsha else "") + "); "
            f"exit-before-earnings variant {rep['earnings_variant']} ({rep['earnings']['note']}); random control "
            f"{rep['control_runs']} runs per exposure cap, seed {rep['control_seed']}; at most {skipped} buys of a "
            f"run skipped because one share cost more than the position size; code `{rep['code_sha']}`; report "
            f"`{f.name}`." + _data_note(rep) + "".join(f" WARNING: {w}." for w in rep.get("warnings", [])))
    if pit:
        out = ["# Momentum core, ten-year test on the point-in-time S&P 500 (issues #127, #143)", "", PIT_HEAD, "",
               PROVISIONAL_PIT, ""]
    else:
        out = ["# Momentum core, ten-year test: trend-filtered momentum and banded 12-1 momentum (issue #127)", "",
               SURVIVORSHIP, "", PROVISIONAL, ""]
    if windows:
        w = sorted(windows)[0]
        out += [f"Out-of-sample {w[0]} to {w[1]}. Each row is ONE continuous $100k account of one frozen, "
                "pre-registered configuration, starting flat; nothing is tuned. "
                + ("Signals use split-adjusted closes without dividends; orders, costs and holdings raw prices with "
                   "dividends credited." if pit else "Signals and holdings use split-adjusted prices without "
                   "dividends; so do the equal-weight benchmark and the price-only SPY."), ""]
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
            "CAGR fall among the exposure-matched random portfolios (each copies the rule's positions: entry and "
            "exit session, share of equity, resizes; only the names are random, drawn from the stocks that pass "
            "the price and volume filter that day); the Sharpe percentile is the lower of the rule's run and its "
            "own names replayed through the control.", "",
            "## Gate B marks (decision 15, section 7)", "", *gates, "## Notes", "", *notes]
    if missing:
        out += ["", f"Not run yet: {', '.join(missing)}."]
    return "\n".join(out) + "\n"


def summary(data: Path, level: str, which: str = "core", costs: str = "v1") -> str:
    """The one table of a set: `core` (today's list) or `core_pit` (the point-in-time universe, plus the today vs
    point-in-time comparison of the base rows)."""
    mode = "point_in_time" if which == "core_pit" else "today"
    found, missing = {}, []
    for version in mc.VERSIONS:
        hit = find_report(data / "reports", level, version, costs, mode)
        if hit is None:
            missing.append(version)
        else:
            found[version] = hit
    text = build_summary(found, tuple(missing))
    if which == "core_pit":
        today = {v: hit for v in mc.VERSIONS if (hit := find_report(data / "reports", level, v, costs, "today"))}
        store = {v: hit for v in mc.VERSIONS if (hit := find_report(data / "reports", level, v, costs, "today",
                                                                     "store"))}
        text += "\n" + comparison(today, found, store)
    return text


CMP = (("Total return %", "return_pct"), ("CAGR %", "cagr_pct"), ("Sharpe", "sharpe"),
       ("Max drawdown %", "max_drawdown_pct"), ("Positive years", None), ("Avg exposure %", "avg_exposure_pct"),
       ("Avg names", "avg_names"), ("Trades", "trades"), ("Cost bp/yr", "cost_bp_per_year"),
       ("Top-5 stocks' share of profit %", "top5_profit_share_pct"))


def _base(rep: dict) -> dict:
    return next(r for r in rep["rows"] if r["label"] == rep["base_label"])


def _cmp_cell(s: dict | None, key: str | None) -> str:
    if s is None:
        return "not run"
    if key is None:
        return f"{s['positive_years']} of {s['n_years']}"
    v = s.get(key)
    return "" if v is None else str(v)


def comparison(today: dict, pit: dict, store: dict | None = None) -> str:
    """Today's list against the point-in-time universe, base row of each version (base fill, 1x costs, 100% cap,
    hold through earnings), with the benchmarks of each side. A third column, today's list on the daily store,
    appears when that run exists (it separates the data change from the universe change)."""
    store = store or {}
    cols = ["Today's list (minute bars)", *(["Today's list (daily store)"] if store else []),
            "Point-in-time S&P 500 (daily store)"]
    out = ["## Today's list against the point-in-time universe (base rows)", "",
           "The same frozen rules and settings; what changes is which stocks could be bought on each day "
           "(and the price data). The gap is the survivorship bias of today's list plus the data repairs.", ""]
    for version in mc.VERSIONS:
        sides = [today.get(version), *([store.get(version)] if store else []), pit.get(version)]
        reps = [None if h is None else h[1] for h in sides]
        out += [f"### `{version}`", "", "| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        bases = [None if r is None else _base(r) for r in reps]
        for name, key in CMP:
            out.append(f"| {name} | " + " | ".join(_cmp_cell(b, key) for b in bases) + " |")
        ctl = [None if b is None else (b.get("control") or {}) for b in bases]
        out.append("| Sharpe pctile vs random | " + " | ".join(
            "not run" if c is None else f"{c.get('sharpe_percentile')} ({c.get('kind') or 'entries_matched'}, "
            f"{c.get('runs')} runs)" for c in ctl) + " |")
        for bname, key in (("SPY total return", "spy_total_return"), ("SPY price only", "spy"),
                           ("Equal-weight (same universe)", "universe_ew")):
            out.append(f"| {bname}: CAGR % / max drawdown % | " + " | ".join(
                "not run" if r is None else ("not in the data" if key not in r["benchmarks"] else
                                             f"{r['benchmarks'][key]['cagr_pct']} / "
                                             f"{r['benchmarks'][key]['max_drawdown_pct']}") for r in reps) + " |")
        out.append("| Gate B marks 1-6 | " + " | ".join(
            "not run" if r is None else ", ".join(
                f"{g['mark']}: {g['verdict'].replace(mc.CANNOT, 'n/j')}" for g in r["gate_b"]) for r in reps) + " |")
        b_today, b_pit = bases[0], bases[-1]
        if b_today is not None and b_pit is not None:
            out += ["", f"Point-in-time minus today: CAGR {b_pit['cagr_pct'] - b_today['cagr_pct']:+.2f} points, "
                        f"Sharpe {b_pit['sharpe'] - b_today['sharpe']:+.2f}, max drawdown "
                        f"{b_pit['max_drawdown_pct'] - b_today['max_drawdown_pct']:+.2f} points."]
        if reps[-1] is not None:
            g5 = next(g for g in reps[-1]["gate_b"] if g["mark"] == 5)
            out += ["", f"Gate B mark 5 (the base row still passes marks 1-2 on the point-in-time universe): "
                        f"**{g5['verdict']}**. {g5['detail']}."]
        out.append("")
    out += ["`n/j` = cannot be judged yet. Every number here is provisional: " + mc.NOT_A_PASS + "."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=list(mc.VERSIONS))
    ap.add_argument("--has", default="", choices=["", *mc.VERSIONS], help="exit 0 if this version has a report")
    ap.add_argument("--summary", action="store_true", help="print the one table and write it")
    ap.add_argument("--set", default="core", choices=["core", "core_pit"], help="which summary (with --summary)")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--universe", default="today",
                    help="today (the default: today's list, as before), pit (the point-in-time S&P 500 on the daily "
                         "store), or a universe file (today's list from that file)")
    ap.add_argument("--universe-file", default=str(ROOT / "config" / "universe_history.yaml"),
                    help="the stocks, sector ETFs and market symbol (with --universe today|pit)")
    ap.add_argument("--bars", choices=["minute", "store"], help="minute (data/bars_1m, the default for today) or "
                    "store (data/daily, #124; the only choice for pit)")
    ap.add_argument("--start", default="2016-01-01", help="first date with bars")
    ap.add_argument("--end", help="last session (inclusive)")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--control-runs", type=int, default=500, help="exposure-matched random runs per exposure "
                    "cap (Gate B mark 3 needs 500; 0 switches the control off)")
    ap.add_argument("--entries-control-runs", type=int, default=0, help="the entries-matched control of #127 as a "
                    "diagnostic (never judged; 0: not run)")
    ap.add_argument("--control-seed", type=int, default=127)
    ap.add_argument("--earnings", default="auto", choices=["auto", "off"],
                    help="auto: run the approximate exit-before-earnings variant when the estimate is reliable")
    ap.add_argument("--refresh-daily", action="store_true", help="rebuild the daily-bar cache")
    ap.add_argument("--costs", default="v1", choices=list(MODELS),
                    help="cost model: v1 (default, reproduces earlier reports) or v2 (issue #111)")
    a = ap.parse_args()
    data = Path(a.data)
    if a.has:
        uni_file, mode, bars = universe_mode(a)
        hit = find_report(data / "reports", a.level, a.has, a.costs, mode, bars, start=a.start, end=a.end,
                          universe=Path(uni_file).name, control_runs=a.control_runs, control_kind=CONTROL_KIND)
        sys.exit(0 if hit else 1)
    if a.summary:
        text = summary(data, a.level, a.set, a.costs)
        (data / "reports").mkdir(parents=True, exist_ok=True)
        (data / "reports" / summary_name(a.costs, a.set)).write_text(text)
        print(text)
        return
    if not a.version or not a.end:
        ap.error("--version and --end are required for a run")
    run_version(a)


if __name__ == "__main__":
    main()
