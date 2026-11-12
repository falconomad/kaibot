"""Ten-year walk-forward of one pre-declared multi-day rule (issue #113, decision 14). Research only: reads the
history under data/, never touches the engine or the broker. Under decision 15 this is the first look (candidate
0): its numbers are provisional and cannot pass Gate B.

python scripts/walk_forward_multiday.py --rule trend_highs|rel_strength|winners_run|overnight_spy
    [--env .env] [--universe config/universe_history.yaml] [--start 2016-01-01] --end 2026-11-06
    [--level balanced] [--earnings auto|on|off] [--min-train-trades 20] [--refresh-daily] [--costs v1|v2]

Daily bars are built once from data/bars_1m and cached in data/multiday/daily_bars.parquet (rebuilt when a month
file of the minute history changes). `winners_run` also needs variant A's intraday trades: one pass over the
minute bars, cached in data/multiday/. The folds are those of scripts/walk_forward.py (train 12 months, test the
next month, roll monthly; first test month 2017-05). How the tuned account is stitched is described in
kaibot.replay.multiday_rules.

Writes data/reports/walkforward_<level>_md_<rule>_<ts>.{json,md}, the trades and daily CSVs of the tuned and
the fixed account, and two trial rows in data/trials.jsonl (tuned carries n_trials = grid x folds).
--env is only read when the FRED VIX cache (data/inputs/fred_vixcls.json) is missing.
No committed pre-registration (docs/prereg/<rule>.md), no run (`kaibot.evaluation.prereg.require`, decision 15
Gate A; the four first-look rules are registered retroactively). The report and both trial rows carry the
pre-registration's sha256 and the sha256 of config/gate_v2.yaml.
--costs v2 (issue #111) prices every fill with kaibot.broker.costs_v2; the default v1 reproduces earlier reports.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
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
from run_replay import load_env, vix_prev_close  # noqa: E402
from walk_forward import logged_trials, peak_rss_mb  # noqa: E402

from kaibot.broker.costs_v2 import MODELS, get_model  # noqa: E402
from kaibot.evaluation import prereg  # noqa: E402
from kaibot.evaluation.gate_v2 import GateV2  # noqa: E402
from kaibot.evaluation.metrics import deflated_sharpe, max_drawdown, pbo_cscv, sharpe, t_stat  # noqa: E402
from kaibot.ingest.certs import ensure_ca_bundle  # noqa: E402
from kaibot.replay import earnings  # noqa: E402
from kaibot.replay.data import load_news_times, load_symbol  # noqa: E402
from kaibot.replay.features import regime  # noqa: E402
from kaibot.replay.multiday import (  # noqa: E402
    DailyBars,
    Indicators,
    MBook,
    MTrade,
    bars_fingerprint,
    equal_weight_benchmark,
    indicators,
    load_or_build_daily,
    spy_buy_and_hold,
)
from kaibot.replay.multiday_rules import (  # noqa: E402
    EXPERIMENTS,
    MultidayWF,
    book_digest,
    make_rule,
    variant_a_trades,
    walk_forward,
)
from kaibot.replay.walkforward import WARMUP_DAYS, Window, first_train_month, make_folds, pbo_blocks  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

COST_KEYS = ("gross_pnl", "cost_spread", "cost_impact", "cost_slippage", "cost_fees")
SURVIVORSHIP = ("Survivorship bias: the universe is today's 300 large caps applied back to 2016, so every rule that "
                "holds these stocks is flattered by hindsight (they are the survivors and the winners); read each "
                "result against the equal-weight universe row, not against zero.")
STATUS = ("provisional (decision 15): a first look from the research harness of #113, on today's stock list and with "
          "cost model {costs}; not a verdict, cannot pass Gate B")


def curve_stats(rets: np.ndarray) -> dict:
    """Return, CAGR, Sharpe and drawdown of a daily return series (252 sessions a year)."""
    rets = np.asarray(rets, dtype=float)
    eq = np.cumprod(1 + rets) if len(rets) else np.array([1.0])
    total = float(eq[-1] - 1)
    cagr = (1 + total) ** (252 / len(rets)) - 1 if len(rets) and total > -1 else -1.0
    return {"return_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2),
            "daily_sharpe_ann": round(sharpe(rets) * math.sqrt(252), 2),
            "max_drawdown_pct": round(max_drawdown(np.r_[1.0, eq]) * 100, 2)}


def side(book: MBook, benchmarks: dict[str, dict]) -> dict:
    """Everything the comparison table shows for one account (tuned or fixed)."""
    daily = pd.DataFrame(book.daily)
    rets = (daily["pnl"] / daily["start"]).to_numpy()
    cs = curve_stats(rets)
    tr = book.trades
    n = len(tr)
    net = np.array([t.pnl / t.equity for t in tr])
    gross = np.array([t.gross_pnl / t.equity for t in tr])
    m_ret = (1 + pd.Series(rets)).groupby(daily["date"].to_numpy().astype("datetime64[M]").astype(str)).prod() - 1
    tot = {k: float(sum(getattr(t, k) for t in tr)) for k in COST_KEYS}
    pos_value = float(sum(t.qty * t.entry for t in tr))

    def bp_pos(x: float) -> float | None:  # basis points of the money actually put into the trades
        return round(x / pos_value * 1e4, 2) if pos_value else None

    return {
        "trades": n, "trades_per_month": round(n / len(m_ret), 1) if len(m_ret) else None,
        "avg_hold_days": round(float(np.mean([t.hold_days for t in tr])), 2) if n else None,
        "net_return_pct": cs["return_pct"], "cagr_pct": cs["cagr_pct"],
        "mean_bp_gross": round(float(gross.mean()) * 1e4, 2) if n else None,
        "mean_bp_net": round(float(net.mean()) * 1e4, 2) if n else None,
        "t_stat_per_trade": round(t_stat(net), 2) if n > 1 else None,
        "hit_rate": round(float((net > 0).mean()), 3) if n else None,
        "daily_sharpe_ann": cs["daily_sharpe_ann"], "max_drawdown_pct": cs["max_drawdown_pct"],
        "months": int(len(m_ret)), "months_positive": int((m_ret > 0).sum()),
        "time_invested_pct": round(float((daily["positions"] > 0).mean()) * 100, 1),
        "avg_exposure_pct": round(float(daily["exposure"].mean()) * 100, 1),
        "costs": {"gross_pnl": round(tot["gross_pnl"], 2), "spread": round(tot["cost_spread"], 2),
                  "impact": round(tot["cost_impact"], 2), "gap_slippage": round(tot["cost_slippage"], 2),
                  "fees": round(tot["cost_fees"], 2), "net_pnl": round(float(sum(t.pnl for t in tr)), 2),
                  "gapped_stops": int(sum(t.gap for t in tr)),
                  "avg_position_usd": round(pos_value / n, 0) if n else None,
                  "bp_of_position_gross": bp_pos(tot["gross_pnl"]),
                  "bp_of_position_costs": bp_pos(sum(tot[k] for k in COST_KEYS[1:])),
                  "bp_of_position_gap_slippage": bp_pos(tot["cost_slippage"])},
        "vs": {name: {"excess_return_pp": round(cs["return_pct"] - b["return_pct"], 2),
                      "excess_cagr_pp": round(cs["cagr_pct"] - b["cagr_pct"], 2),
                      "sharpe_minus_benchmark": round(cs["daily_sharpe_ann"] - b["daily_sharpe_ann"], 2)}
               for name, b in benchmarks.items()},
    }


def summarize(wf: MultidayWF, bars: DailyBars, ind: Indicators, stocks: list[str], spy: str, equity0: float,
              n_logged: int, blocks: int = 16, costs=None) -> dict:
    dates = np.array([r["date"] for r in wf.tuned.daily], dtype="datetime64[D]")
    bench = {"spy": curve_stats(spy_buy_and_hold(bars, spy, wf.i_test0, wf.i1)),
             "universe_ew": curve_stats(equal_weight_benchmark(bars, ind, bars.cols(stocks), wf.i_test0, wf.i1,
                                                               equity0, costs=costs))}
    r_t = np.array([r["pnl"] / r["start"] for r in wf.tuned.daily])
    r_d = np.array([r["pnl"] / r["start"] for r in wf.fixed.daily])
    _, mat = wf.oos_matrix()
    srs = [sharpe(mat[:, k]) for k in range(mat.shape[1])]
    sr_var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    n_trials = len(wf.grid) * len(wf.folds) + n_logged
    pbo = None
    if mat.shape[1] >= 2:
        s = pbo_blocks(len(mat), blocks)
        if len(mat) >= 2 * s:
            res = pbo_cscv(mat, n_blocks=s)
            pbo = {"pbo": round(res.pbo, 3), "blocks": s, "splits": res.n_splits}
    by_month = (1 + pd.Series(r_t)).groupby(dates.astype("datetime64[M]").astype(str)).prod() - 1  # "2017-05": ret
    return {
        "folds": len(wf.folds), "oos_sessions": int(len(dates)), "oos_window": [str(dates.min()), str(dates.max())],
        "tuned": side(wf.tuned, bench), "default": side(wf.fixed, bench), "spy": bench["spy"],
        "universe_ew": bench["universe_ew"],
        "dsr": {"n_trials": n_trials, "grid": len(wf.grid), "logged_trials_before": n_logged,
                "trial_sharpe_var": sr_var, "tuned": round(deflated_sharpe(r_t, n_trials, sr_var), 3),
                "default": round(deflated_sharpe(r_d, n_trials, sr_var), 3)},
        "pbo_across_grid": pbo,
        "fallback_folds": int(sum(sel.fallback for _, sel in wf.folds)),
        "choices": pd.Series([sel.params.label for _, sel in wf.folds]).value_counts().to_dict(),
        "per_config_oos_sharpe_ann": {p.label: round(s * math.sqrt(252), 2) for p, s in zip(wf.grid, srs,
                                                                                            strict=True)},
        "per_fold": [{"test_month": str(f.test_month), "chosen": sel.params.label,
                      "train_sharpe_ann": round(sel.train_sharpe_ann, 2), "train_trades": sel.train_trades,
                      "fallback": sel.fallback,
                      "test_ret_pct": round(float(by_month.get(str(f.test_month), 0.0)) * 100, 3)}
                     for f, sel in wf.folds],
    }


def markdown(rep: dict) -> str:
    s = rep["summary"]
    t, d = s["tuned"], s["default"]
    rows = [("Trades", "trades"), ("Trades per month", "trades_per_month"), ("Average holding (sessions)",
            "avg_hold_days"), ("Net return %", "net_return_pct"), ("CAGR %", "cagr_pct"),
            ("Mean bp of equity per trade, gross", "mean_bp_gross"), ("Mean bp of equity per trade, net",
            "mean_bp_net"), ("t-stat per trade", "t_stat_per_trade"), ("Hit rate", "hit_rate"),
            ("Daily Sharpe (ann.)", "daily_sharpe_ann"), ("Max drawdown %", "max_drawdown_pct"),
            ("% of sessions with a position at the close", "time_invested_pct"),
            ("Average % of equity held at the close", "avg_exposure_pct")]
    costs = [("Gross P&L $ (no-cost prices)", "gross_pnl"), ("Spread $", "spread"), ("Impact $", "impact"),
             ("Gap slippage $ (opens below the stop)", "gap_slippage"), ("Fees $", "fees"), ("Net P&L $", "net_pnl"),
             ("Stops jumped by the open", "gapped_stops"), ("Average position $", "avg_position_usd"),
             ("Gross, bp of the position", "bp_of_position_gross"),
             ("All costs, bp of the position", "bp_of_position_costs"),
             ("Gap slippage, bp of the position", "bp_of_position_gap_slippage")]
    out = [f"# Multi-day walk-forward: {rep['experiment']} / {rep['level']} ({s['oos_window'][0]} to "
           f"{s['oos_window'][1]})", "", SURVIVORSHIP, "", f"Status: {rep['status']}.", "",
           f"Rule: {rep['experiment_what']}.", "",
           f"{s['folds']} folds: train 12 months, test the next month, roll monthly; {s['dsr']['grid']} configs "
           f"({', '.join(f'{k} {v}' for k, v in rep['grid_axes'].items())}); selection = best train daily Sharpe "
           f"after costs with >= {rep['min_train_trades']} train trades.", "",
           f"| OOS metric | Tuned monthly | Fixed ({rep['fixed_setting']}) |", "|---|---|---|"]
    out += [f"| {label} | {t[k]} | {d[k]} |" for label, k in rows]
    out += [f"| Months positive | {t['months_positive']} of {t['months']} | {d['months_positive']} of {d['months']} |"]
    out += [f"| {label} | {t['costs'][k]} | {d['costs'][k]} |" for label, k in costs]
    out += [f"| Deflated Sharpe (n_trials {s['dsr']['n_trials']}) | {s['dsr']['tuned']} | {s['dsr']['default']} |"]
    for name, label in (("spy", "SPY buy-and-hold"), ("universe_ew", "the universe equal-weight")):
        b = s[name]
        out += [f"| Return minus {label}, points | {t['vs'][name]['excess_return_pp']} | "
                f"{d['vs'][name]['excess_return_pp']} |",
                f"| Sharpe minus {label} | {t['vs'][name]['sharpe_minus_benchmark']} | "
                f"{d['vs'][name]['sharpe_minus_benchmark']} |"]
        out_b = (f"- {label[0].upper() + label[1:]} over the same sessions: {b['return_pct']}%, CAGR "
                 f"{b['cagr_pct']}%, Sharpe {b['daily_sharpe_ann']}, max drawdown {b['max_drawdown_pct']}%.")
        rep.setdefault("_bench_lines", []).append(out_b)
    out += ["", *rep.pop("_bench_lines"),
            f"- PBO across the grid: {(s['pbo_across_grid'] or {}).get('pbo')}; {s['fallback_folds']} folds fell "
            f"back to the fixed setting; choices: {s['choices']}.",
            f"- Earnings blackout: {rep['earnings']['mode']} ({rep['earnings']['note']}).", "",
            f"daily bars `{rep['input_sha256']}`, events tuned `{rep['events_sha256_tuned']}`, fixed "
            f"`{rep['events_sha256_default']}`, code `{rep['code_sha']}`.", "", "Caveats:"]
    out += [f"- {c}" for c in rep["caveats"]]
    return "\n".join(out) + "\n"


def earnings_filter(data: Path, bars: DailyBars, stocks: list[str], mode: str) -> tuple[dict, np.ndarray | None,
                                                                                     np.ndarray | None]:
    """The blackout matrices and what the report says about them. `auto` uses the filter only when the
    pre-declared reliability bar of kaibot.replay.earnings is met on this history."""
    if mode == "off":
        return {"mode": "off", "note": "switched off with --earnings off"}, None, None
    events = {s: earnings.event_sessions(earnings.load_headlines(data, s), bars.dates) for s in stocks}
    hold, entry = earnings.calendar(bars.dates, bars.symbols, events)
    cov = earnings.coverage(bars.dates, bars.symbols, events, hold, np.isfinite(bars.c), stocks)
    numbers = (f"{cov['stocks_with_reports']} of {cov['stocks']} stocks have detected reports, "
               f"{cov['stocks_plausible_share']:.0%} at a plausible 3-5 a year (median "
               f"{cov['median_reports_per_year']}); flat the night before {cov['flat_before_report_share']:.0%} of "
               f"{cov['reports_tested']} detected reports; {cov['blackout_share_of_sessions']:.0%} of stock-sessions "
               "blacked out")
    use = mode == "on" or cov["reliable"]
    if use:
        note = ("window around an estimated date from past results headlines, not a known date: " + numbers
                + ("" if cov["reliable"] else "; BELOW the reliability bar, forced with --earnings on"))
        return {"mode": "on", "note": note, "coverage": cov}, hold, entry
    note = ("NOT applied: the next earnings date cannot be established reliably from the history on disk (" + numbers
            + f"; the bar is {earnings.RELIABLE['min_symbols_share']:.0%} of stocks and "
            f"{earnings.RELIABLE['min_recall']:.0%} of reports), so positions are held through earnings")
    return {"mode": "off (not reliable)", "note": note, "coverage": cov}, None, None


def intraday_cache(data: Path, cfg: dict, level: str, start: str, end: str, universe: str, pol, vix, fomc,
                   equity: float, block_months: int, costs: str = "v1") -> pd.DataFrame:
    """Variant A's trades at its defaults for every month from the first train month (one pass over the minute
    bars, about as long as one walk-forward of scripts/walk_forward.py), cached for the next run."""
    sector_of = {s: e for e, syms in cfg["sectors"].items() for s in syms}
    symbols = [*sector_of, *cfg["sectors"], cfg["market"]]
    fp = bars_fingerprint(data, symbols, start, end)
    tag = "" if costs == "v1" else f"_costs{costs}"
    f = data / "multiday" / f"intraday_A_{level}_{Path(universe).stem}_{start}_{end}{tag}.parquet"
    meta = f.with_suffix(".json")
    if f.is_file() and meta.is_file() and json.loads(meta.read_text()).get("fingerprint") == fp:
        return pd.read_parquet(f)
    t0 = time.time()
    print("winners_run: replaying variant A intraday once (cached afterwards)", flush=True)
    first, last = first_train_month(start, WARMUP_DAYS), np.datetime64(end, "M")
    months = [first + np.timedelta64(k, "M") for k in range(int((last - first).astype(int)) + 1)]
    news = {s: load_news_times(data, s) for s in sector_of}

    def load(lo, hi):
        w = Window({s: load_symbol(data, s, lo, hi) for s in sector_of},
                   {e: load_symbol(data, e, lo, hi) for e in cfg["sectors"]}, load_symbol(data, cfg["market"], lo, hi))
        print(f"  loaded {lo}..{hi}, {time.time() - t0:.0f}s, peak RSS {peak_rss_mb():.0f} MB", flush=True)
        return w

    df = variant_a_trades(load, sector_of, news, lambda spy: regime(spy, vix), fomc, pol, months, equity0=equity,
                          block_months=block_months, end=np.datetime64(end, "D"),
                          costs=None if costs == "v1" else get_model(costs))
    df["date"] = df["date"].astype("datetime64[ns]")
    f.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(f, index=False)
    meta.write_text(json.dumps({"fingerprint": fp, "trades": len(df), "runtime_s": round(time.time() - t0, 1)}))
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", required=True, choices=list(EXPERIMENTS))
    ap.add_argument("--env", default="", help="only read when the FRED VIX cache is missing")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", default="2016-01-01", help="first date with bars")
    ap.add_argument("--end", required=True, help="last session (inclusive); the last test month may be partial")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--earnings", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--min-train-trades", type=int, default=20)
    ap.add_argument("--block-months", type=int, default=3, help="winners_run: months of minute bars loaded at once")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--refresh-daily", action="store_true", help="rebuild the daily-bar cache")
    ap.add_argument("--refresh-vix", action="store_true")
    ap.add_argument("--costs", default="v1", choices=list(MODELS),
                    help="cost model: v1 (default, reproduces earlier reports) or v2 (issue #111)")
    a = ap.parse_args()
    try:
        prereg_sha = prereg.require(a.rule, ROOT)
    except prereg.PreregError as e:
        sys.exit(f"no run: {e}")
    gate_sha = GateV2.load(ROOT).sha
    print(f"pre-registration docs/prereg/{a.rule}.md sha256 {prereg_sha[:12]}; config/gate_v2.yaml sha256 "
          f"{gate_sha[:12]}", flush=True)
    costs = None if a.costs == "v1" else get_model(a.costs)
    t0 = time.time()
    data, exp = Path(a.data), EXPERIMENTS[a.rule]
    cfg = yaml.safe_load(Path(a.universe).read_text())
    stocks = [s for syms in cfg["sectors"].values() for s in syms]
    spy = cfg["market"]
    symbols = [*stocks, *cfg["sectors"], spy]
    pol = load_profiles()[a.level]

    def progress(n: int, s: str) -> None:
        if n % 25 == 0:
            print(f"  daily bars: {n} of {len(symbols)} symbols ({s}), {time.time() - t0:.0f}s", flush=True)

    bars, built = load_or_build_daily(data, symbols, a.start, a.end, refresh=a.refresh_daily, progress=progress)
    if not np.isfinite(bars.c[:, bars.col(spy)]).any():
        sys.exit(f"no {spy} bars under {data / 'bars_1m'} between {a.start} and {a.end}: nothing to run on")
    bars = bars.keep_sessions(np.isfinite(bars.c[:, bars.col(spy)]))  # the session calendar is SPY's
    print(f"daily bars: {len(bars.dates)} sessions x {len(bars.symbols)} symbols "
          f"({'built and cached' if built else 'from the cache'}), {time.time() - t0:.0f}s", flush=True)
    ind = indicators(bars)
    vix = fomc = None
    if a.rule in ("overnight_spy", "winners_run"):
        cache = data / "inputs" / "fred_vixcls.json"
        key = ""
        if a.refresh_vix or not cache.exists():
            ensure_ca_bundle()
            key = load_env(a.env).get("FRED_API_KEY", "") if a.env and Path(a.env).is_file() else ""
        vix = vix_prev_close(key, cache, a.refresh_vix)
    intraday = None
    if a.rule == "winners_run":
        cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
        fomc = {np.datetime64(str(d), "D") for d in cal["fomc_statement_1400"]}
        intraday = intraday_cache(data, cfg, a.level, a.start, a.end, a.universe, pol, vix, fomc, a.equity,
                                  a.block_months, a.costs)
    if a.rule == "overnight_spy":
        earn, hold, entry = {"mode": "not applicable", "note": "SPY has no earnings date"}, None, None
    else:
        earn, hold, entry = earnings_filter(data, bars, stocks, a.earnings)
    print(f"earnings blackout: {earn['mode']} ({earn['note']})", flush=True)
    vix_arr = None if vix is None else vix.reindex(pd.to_datetime(bars.dates)).ffill().to_numpy()
    rule = make_rule(a.rule, bars, ind, stocks, spy, vix_arr, intraday, intraday_equity0=a.equity)
    folds = make_folds(first_train_month(a.start, WARMUP_DAYS), np.datetime64(a.end, "D"))
    print(f"{len(folds)} folds x {len(exp.grid)} configs; first test {folds[0].test_month}, last "
          f"{folds[-1].test_month}", flush=True)
    wf = walk_forward(bars, ind, pol, rule, exp, folds, equity0=a.equity, min_train_trades=a.min_train_trades,
                      hold_blk=hold, entry_blk=entry, end=a.end, costs=costs)
    trials_f = data / "trials.jsonl"
    summary = summarize(wf, bars, ind, stocks, spy, a.equity, logged_trials(trials_f), costs=costs)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "code_sha": code_sha, "set": "multiday",
        "status": STATUS.format(costs=a.costs), "costs": a.costs, "prereg_sha256": prereg_sha,
        "gate_v2_sha256": gate_sha,
        "experiment": a.rule, "experiment_what": exp.what, "fixed_setting": exp.fixed, "level": a.level,
        "universe": Path(a.universe).name, "start": a.start, "end": a.end,
        "grid_axes": {k: list(v) for k, v in exp.axes.items()}, "default_params": dict(exp.default.params),
        "min_train_trades": a.min_train_trades, "equity": a.equity, "earnings": earn,
        "input_sha256": bars_fingerprint(data, symbols, a.start, a.end),
        "events_sha256_tuned": book_digest(wf.tuned), "events_sha256_default": book_digest(wf.fixed),
        "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak_rss_mb(), 0), "summary": summary,
        "caveats": [
            "survivorship bias: today's large caps applied back to 2016; the equal-weight universe row carries the "
            "same bias and is the fair comparison",
            "prices are split-adjusted but carry no dividends: a held stock or SPY loses its dividend on the "
            "ex-date and is never paid it (understates every long holding, the benchmarks included)",
            "fills: entries at the open print (first regular-hours minute) or the close print (last regular-hours "
            "minute, not the closing auction) plus half spread and impact; a stop fills at the stop when the day's "
            "low reaches it and at the OPEN when the open is below it; within a day the low is assumed to come "
            "after an entry at the open",
            "sizing: a gap of 3 daily ATRs below the sizing price loses at most the level's per-trade risk; "
            "position, number of positions and total held are capped; long only, never more than the cash",
            "one continuous account: equity and positions are carried across months and nothing is force-closed "
            "at a fold boundary, so the months are not independent draws; a configuration change applies to new "
            "entries only",
            "the first train months have fewer trades: the 200-day average and the 6 / 9-month returns need "
            "history from January 2016",
            "pre-market and after-hours moves are only seen as the next open's gap; halts and delistings inside "
            "the history show as missing bars (a position waits for the next bar)",
        ],
    }
    if a.rule == "winners_run":
        rep["caveats"].append(
            "the intraday leg is variant A exactly as in the control experiment (replayed each month from flat "
            "equity), scaled in proportion to this account, so without a carry each day's return is the control's; "
            "it does not see the carried positions (its position count and daily stop are its own); only the part "
            "of a winner that fits the gap sizing is kept, the rest is sold at 15:55 pro rata (a partly kept trade "
            "is two rows); carried positions are capped at 3 and 25% of equity, not 5 and 60%, because the "
            "intraday leg can use the other 75%; the time-invested columns count carried positions only")
    if a.rule == "overnight_spy":
        rep["caveats"].append("the position is at most 15% of equity (the level's position cap; less when SPY's "
                              "ATR is high), so returns and costs are those of that slice in SPY overnight, not of "
                              "the whole account: read the bp-of-position columns")
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"md_{a.rule}{'' if a.costs == 'v1' else '_costs' + a.costs}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    rep["files"] = {w: {"trades": f"trades_{a.level}_{tag}_{w}.csv", "daily": f"daily_{a.level}_{tag}_{w}.csv"}
                    for w in ("tuned", "default")}
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    with trials_f.open("a") as fh:
        for which, book, n_tr in (("tuned", wf.tuned, len(wf.grid) * len(wf.folds)), ("default", wf.fixed, 1)):
            trades = pd.DataFrame([dataclasses.asdict(t) for t in book.trades],
                                  columns=[f.name for f in dataclasses.fields(MTrade)])
            daily = pd.DataFrame(book.daily)
            trades.to_csv(out / rep["files"][which]["trades"], index=False)
            daily.to_csv(out / rep["files"][which]["daily"], index=False)
            tid = f"{a.level}_{tag}_{which}"
            pd.DataFrame({"date": daily["date"].astype(str), "ret": daily["pnl"] / daily["start"]}).to_csv(
                tdir / f"{tid}.csv", index=False)
            s = summary[which]
            fh.write(json.dumps({
                "ts": rep["run_at"], "trial_id": tid, "strategy": a.rule, "variant": "multiday",
                "label": f"multiday walkforward {which} {a.rule}{'' if a.costs == 'v1' else ' costs ' + a.costs}",
                "level": a.level, "cost_model": a.costs, "universe": rep["universe"],
                "events_sha256": rep[f"events_sha256_{which}"], "input_sha256": rep["input_sha256"],
                "code_sha": code_sha, "n_trials": n_tr, "experiment": a.rule, "prereg_sha256": prereg_sha,
                "gate_v2_sha256": gate_sha,
                "params": (f"walk-forward grid ({len(wf.grid)}) x {len(wf.folds)} folds" if which == "tuned"
                           else f"pre-registered {exp.default.label}"),
                "window": summary["oos_window"],
                "metrics": {"trades": s["trades"], "net_return_pct": s["net_return_pct"],
                            "t_stat_per_trade": s["t_stat_per_trade"], "daily_sharpe_ann": s["daily_sharpe_ann"]},
            }, default=str) + "\n")
    # the report is written last: scripts/run_experiments.sh treats it as "this experiment is finished"
    (out / f"walkforward_{a.level}_{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    md = markdown(rep)
    (out / f"walkforward_{a.level}_{tag}.md").write_text(md)
    print(md)
    print(f"runtime {rep['runtime_s']}s, peak RSS {rep['peak_rss_mb']} MB")


if __name__ == "__main__":
    main()
