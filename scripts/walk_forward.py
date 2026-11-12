"""Walk-forward replay (story S08): tune <= 3 parameters on 12 months, freeze, test on the next month, roll monthly.

python scripts/walk_forward.py --env .env --universe config/universe_history.yaml --start 2016-01-01 \\
    --end 2026-11-06 --variant A --level balanced [--grid default] [--min-train-trades 100] [--block-months 3]
    [--experiment control|mornings|wide_stop|high_vix|mornings_wide_stop]

--experiment runs one of the pre-declared experiments of issue #109 (kaibot.replay.walkforward.EXPERIMENTS): its
own small grid and fixed default, the same folds and selection. `control` is variant A with the default grid.
scripts/run_experiments.sh runs all of them and prints one comparison table.

--start is the first date with bars; the first train month starts after the 120-day feature warm-up (2016-05),
so the first test month is 2017-05. Every month is simulated for every grid configuration from flat equity; each
fold picks the configuration with the best train daily Sharpe after costs (>= --min-train-trades train trades,
else the default) and runs it on the test month. The pre-registered default is run on every test month too.

Writes data/reports/walkforward_<level>_<tag>.{json,md} plus OOS trades/daily CSVs, and appends two trial rows
(tuned, default) to data/trials.jsonl with their daily returns in data/trials/<trial_id>.csv. The tuned row
carries n_trials = grid size x folds so scripts/trial_report.py deflates later Sharpes by the whole search.
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

from kaibot.evaluation.metrics import deflated_sharpe, max_drawdown, pbo_cscv, sharpe, t_stat  # noqa: E402
from kaibot.evaluation.trials import count_file  # noqa: E402
from kaibot.ingest.certs import ensure_ca_bundle  # noqa: E402
from kaibot.replay.data import load_news_times, load_symbol  # noqa: E402
from kaibot.replay.features import regime  # noqa: E402
from kaibot.replay.sim import Trade  # noqa: E402
from kaibot.replay.walkforward import (  # noqa: E402
    DEFAULT,
    EXPERIMENTS,
    GRIDS,
    WARMUP_DAYS,
    Window,
    first_train_month,
    make_folds,
    make_grid,
    pbo_blocks,
    run_walk_forward,
)
from kaibot.risk.profiles import load_profiles  # noqa: E402


def peak_rss_mb() -> float:
    try:
        import resource

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / 1024 / (1024 if sys.platform == "darwin" else 1)  # bytes on macOS, KiB on Linux
    except Exception:  # noqa: BLE001
        return float("nan")


COST_KEYS = ("gross_pnl", "cost_spread", "cost_impact", "cost_slippage", "cost_fees")


def logged_trials(trials_f: Path) -> int:
    """Configurations already tried, from the trial log: a walk-forward row counts its whole search (n_trials).
    The count is the trial registry's (kaibot.evaluation.trials), the same one the gate reports use."""
    return count_file(trials_f)


def cost_split(trades, equity0: float) -> dict:
    """Gross (no-cost prices) vs net, and each cost the model charged: $ totals and mean bp of equity per trade."""
    n = len(trades)
    tot = {k: float(sum(getattr(t, k) for t in trades)) for k in COST_KEYS}
    net = float(sum(t.pnl for t in trades))

    def bp(x):
        return round(x / n / equity0 * 1e4, 3) if n else None

    return {"gross_pnl": round(tot["gross_pnl"], 2), "net_pnl": round(net, 2),
            "spread": round(tot["cost_spread"], 2), "impact": round(tot["cost_impact"], 2),
            "slippage_past_stop": round(tot["cost_slippage"], 2), "fees": round(tot["cost_fees"], 2),
            "mean_bp_gross": bp(tot["gross_pnl"]), "mean_bp_net": bp(net), "mean_bp_spread": bp(tot["cost_spread"]),
            "mean_bp_impact": bp(tot["cost_impact"]), "mean_bp_slippage_past_stop": bp(tot["cost_slippage"]),
            "mean_bp_fees": bp(tot["cost_fees"])}


def summarize(wf, equity0: float, n_logged: int, blocks: int = 16, default=DEFAULT) -> dict:
    folds = wf.folds
    dates = np.concatenate([f.tuned_stats.dates for f in folds])
    r_t = np.concatenate([f.tuned_stats.rets for f in folds])
    r_d = np.concatenate([f.default_stats.rets for f in folds])
    tr_t = [t for f in folds for t in f.tuned.trades]
    tr_d = [t for f in folds for t in f.default.trades]

    def side(rets, trades, month_stats):
        pnl = np.array([t.pnl for t in trades])
        m_ret = [float(np.prod(1 + ms.rets) - 1) for ms in month_stats]
        eq = equity0 * np.cumprod(1 + rets)
        months = len(folds)
        return {
            "trades": len(trades), "trades_per_month": round(len(trades) / months, 1) if months else None,
            "net_return_pct": round(float(eq[-1] / equity0 - 1) * 100, 2) if len(eq) else 0.0,
            "mean_bp_per_trade": round(float((pnl / equity0).mean() * 1e4), 2) if len(pnl) else None,
            "t_stat_per_trade": round(t_stat(pnl / equity0), 2) if len(pnl) > 1 else None,
            "hit_rate": round(float((pnl > 0).mean()), 3) if len(pnl) else None,
            "daily_sharpe_ann": round(sharpe(rets) * math.sqrt(252), 2),
            "max_drawdown_pct": round(max_drawdown(np.r_[equity0, eq]) * 100, 2),
            "months": months, "months_positive": int(sum(r > 0 for r in m_ret)),
            "months_with_trades": int(sum(ms.trades > 0 for ms in month_stats)),
            "costs": cost_split(trades, equity0),
        }

    oos_dates, mat = wf.oos_matrix()
    srs = [sharpe(mat[:, k]) for k in range(mat.shape[1])]
    sr_var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    n_trials = len(wf.grid) * len(folds) + n_logged
    pbo = None
    if mat.shape[1] >= 2:
        s = pbo_blocks(len(mat), blocks)
        if len(mat) >= 2 * s:
            res = pbo_cscv(mat, n_blocks=s)
            pbo = {"pbo": round(res.pbo, 3), "blocks": s, "splits": res.n_splits,
                   "oos_degradation": round(res.oos_degradation, 3)}
    spy = pd.Series(wf.spy_close).sort_index()
    spy_r = spy.pct_change().reindex(pd.to_datetime(dates)).dropna() if len(spy) else pd.Series(dtype=float)
    spy_r = spy_r[np.isfinite(spy_r)]
    beat = [float(f.tuned_stats.rets.sum()) > float(f.default_stats.rets.sum()) for f in folds]
    changed = [f.selection.params != default for f in folds]
    return {
        "folds": len(folds), "oos_sessions": int(len(dates)),
        "oos_window": [str(dates.min()), str(dates.max())] if len(dates) else None,
        "tuned": side(r_t, tr_t, [f.tuned_stats for f in folds]),
        "default": side(r_d, tr_d, [f.default_stats for f in folds]),
        "spy": {"return_pct": round(float(np.prod(1 + spy_r.to_numpy()) - 1) * 100, 2),
                "daily_sharpe_ann": round(sharpe(spy_r.to_numpy()) * math.sqrt(252), 2)},
        "dsr": {"n_trials": n_trials, "grid": len(wf.grid), "logged_trials_before": n_logged,
                "trial_sharpe_var": sr_var,
                "tuned": round(deflated_sharpe(r_t, n_trials, sr_var), 3),
                "default": round(deflated_sharpe(r_d, n_trials, sr_var), 3)},
        "pbo_across_grid": pbo,
        "tuned_beat_default_share": round(float(np.mean(beat)), 3) if beat else None,
        "folds_tuned_differs_from_default": int(sum(changed)),
        "tuned_beat_default_share_when_different": (
            round(float(np.mean([b for b, c in zip(beat, changed, strict=True) if c])), 3) if any(changed) else None),
        "fallback_folds": int(sum(f.selection.fallback for f in folds)),
        "choices": pd.Series([f.selection.params.label for f in folds]).value_counts().to_dict(),
        "per_config_oos_sharpe_ann": {p.label: round(s * math.sqrt(252), 2) for p, s in zip(wf.grid, srs,
                                                                                            strict=True)},
        "per_fold": [{"test_month": str(f.fold.test_month),
                      "train": [str(f.fold.train_start), str(f.fold.train_end)],
                      "chosen": f.selection.params.label, "train_sharpe_ann": round(f.selection.train_sharpe_ann, 2),
                      "train_trades": f.selection.train_trades, "eligible": f.selection.eligible,
                      "fallback": f.selection.fallback, "test_trades": f.tuned_stats.trades,
                      "test_ret_pct": round(float(np.prod(1 + f.tuned_stats.rets) - 1) * 100, 3),
                      "default_test_ret_pct": round(float(np.prod(1 + f.default_stats.rets) - 1) * 100, 3)}
                     for f in folds],
    }


def markdown(rep: dict) -> str:
    s = rep["summary"]
    t, d = s["tuned"], s["default"]
    pbo = s["pbo_across_grid"] or {}
    rows = [("Trades", "trades"), ("Trades per month", "trades_per_month"), ("Net return %", "net_return_pct"),
            ("Mean bp per trade", "mean_bp_per_trade"), ("t-stat per trade", "t_stat_per_trade"),
            ("Hit rate", "hit_rate"), ("Daily Sharpe (ann.)", "daily_sharpe_ann"),
            ("Max drawdown %", "max_drawdown_pct")]
    out = [f"# Walk-forward {rep['variant']} / {rep['level']} ({s['oos_window'][0]} to {s['oos_window'][1]})", "",
           f"{s['folds']} folds: train {rep.get('train_months', 12)} months, test the next month, roll monthly. "
           f"Grid `{rep['grid_name']}` "
           f"({s['dsr']['grid']} configs: {', '.join(f'{k} {v}' for k, v in rep['grid_axes'].items())}); "
           f"selection = best train daily Sharpe after costs with >= {rep['min_train_trades']} train trades.", "",
           "| OOS metric | Tuned | Default (no tuning) |", "|---|---|---|"]
    out += [f"| {label} | {t[k]} | {d[k]} |" for label, k in rows]
    if "costs" in t:  # issue #109: what the signal made at no-cost prices, and what each cost took
        out += [f"| Months positive | {t['months_positive']} of {t['months']} | {d['months_positive']} of "
                f"{d['months']} |",
                f"| Months with trades | {t['months_with_trades']} | {d['months_with_trades']} |"]
        out += [f"| {label} | {t['costs'][k]} | {d['costs'][k]} |" for label, k in (
            ("Gross P&L $ (no-cost prices)", "gross_pnl"), ("Spread $", "spread"), ("Impact $", "impact"),
            ("Slippage past stops $", "slippage_past_stop"), ("Fees $", "fees"), ("Net P&L $", "net_pnl"),
            ("Mean bp per trade, gross", "mean_bp_gross"), ("Mean bp per trade, spread", "mean_bp_spread"),
            ("Mean bp per trade, impact", "mean_bp_impact"),
            ("Mean bp per trade, slippage past stops", "mean_bp_slippage_past_stop"),
            ("Mean bp per trade, fees", "mean_bp_fees"), ("Mean bp per trade, net", "mean_bp_net"))]
    out += [f"| Deflated Sharpe (n_trials {s['dsr']['n_trials']}) | {s['dsr']['tuned']} | {s['dsr']['default']} |",
            "", f"- SPY over the same sessions: {s['spy']['return_pct']}%, "
            f"daily Sharpe {s['spy']['daily_sharpe_ann']}.",
            f"- PBO across the grid (CSCV, {pbo.get('blocks')} blocks): {pbo.get('pbo')}.",
            f"- Tuned beat default in {s['tuned_beat_default_share']} of folds "
            f"({s['folds_tuned_differs_from_default']} folds chose a non-default config; share when different: "
            f"{s['tuned_beat_default_share_when_different']}); {s['fallback_folds']} folds fell back to the default.",
            f"- Choices: {s['choices']}.", "",
            f"input_sha256 `{rep['input_sha256']}`, events_sha256 tuned `{rep['events_sha256_tuned']}`, default "
            f"`{rep['events_sha256_default']}`, code `{rep['code_sha']}`.", "", "Caveats:"]
    out += [f"- {c}" for c in rep["caveats"]]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_history.yaml"))
    ap.add_argument("--start", default="2016-01-01", help="first date with bars (train starts after warm-up)")
    ap.add_argument("--end", required=True, help="last session (inclusive); the last test month may be partial")
    ap.add_argument("--variant", default="A", choices=["A"], help="the grid is built around A's exits")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--grid", default="default", choices=sorted(GRIDS))
    ap.add_argument("--experiment", default="", choices=["", *EXPERIMENTS],
                    help="a pre-declared experiment (its own grid and default; overrides --grid)")
    ap.add_argument("--min-train-trades", type=int, default=100)
    ap.add_argument("--block-months", type=int, default=3, help="months loaded at once (memory vs re-reads)")
    ap.add_argument("--train-months", type=int, default=12, help="train window; 12 is the pre-declared scheme")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--refresh-vix", action="store_true")
    a = ap.parse_args()
    ensure_ca_bundle()  # python.org's macOS Python has no root certificates; before the first request (FRED VIX)
    t0 = time.time()
    env = load_env(a.env)
    data = Path(a.data)
    cfg = yaml.safe_load(Path(a.universe).read_text())
    cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
    fomc = {np.datetime64(str(d), "D") for d in cal["fomc_statement_1400"]}
    pol = load_profiles()[a.level]
    sector_of = {s: e for e, syms in cfg["sectors"].items() for s in syms}
    news = {s: load_news_times(data, s) for s in sector_of}
    vix = vix_prev_close(env.get("FRED_API_KEY", ""), data / "inputs" / "fred_vixcls.json", a.refresh_vix)
    end = np.datetime64(a.end, "D")
    exp = EXPERIMENTS.get(a.experiment)
    default = exp.default if exp else DEFAULT
    grid = exp.grid if exp else make_grid(GRIDS[a.grid])
    grid_name, grid_axes = (a.experiment, dict(exp.axes)) if exp else (a.grid, GRIDS[a.grid])
    if exp and not a.label:
        a.label = f"exp-{a.experiment}"
    folds = make_folds(first_train_month(a.start, WARMUP_DAYS), end, train_months=a.train_months)
    print(f"{len(folds)} folds x {len(grid)} configs; first test {folds[0].test_month}, last {folds[-1].test_month}")

    def load(lo, hi):
        tb = time.time()
        w = Window({s: load_symbol(data, s, lo, hi) for s in sector_of},
                   {e: load_symbol(data, e, lo, hi) for e in cfg["sectors"]},
                   load_symbol(data, cfg["market"], lo, hi))
        print(f"  loaded {lo}..{hi} in {time.time() - tb:.0f}s, peak RSS {peak_rss_mb():.0f} MB", flush=True)
        return w

    digest_params = {"level": a.level, "policy": dataclasses.asdict(pol), "variant": a.variant,
                     "equity": a.equity, "grid": [p.label for p in grid], "min_train_trades": a.min_train_trades,
                     "fomc": sorted(str(d) for d in fomc), "warmup_days": WARMUP_DAYS,
                     "vix": {str(k.date()): float(v) for k, v in vix.items()
                             if np.datetime64(a.start, "D") <= np.datetime64(k.date(), "D") <= end}}
    wf = run_walk_forward(load, sector_of, news, lambda spy: regime(spy, vix), fomc, pol, folds, grid,
                          variant=a.variant, default=default, equity0=a.equity,
                          min_train_trades=a.min_train_trades, block_months=a.block_months, end=end,
                          digest_params=digest_params)
    trials_f = data / "trials.jsonl"
    summary = summarize(wf, a.equity, logged_trials(trials_f), default=default)
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "code_sha": code_sha,
        "variant": a.variant, "level": a.level, "label": a.label, "universe": Path(a.universe).name,
        "start": a.start, "end": a.end, "grid_name": grid_name, "grid_axes": grid_axes,
        "experiment": a.experiment or None, "experiment_what": exp.what if exp else None,
        "default_params": dataclasses.asdict(default), "min_train_trades": a.min_train_trades,
        "block_months": a.block_months, "train_months": a.train_months, "equity": a.equity,
        "input_sha256": wf.input_sha256, "events_sha256_tuned": wf.events_sha256("tuned"),
        "events_sha256_default": wf.events_sha256("default"), "candidates_raw": wf.candidates,
        "runtime_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak_rss_mb(), 0),
        "summary": summary,
        "caveats": [
            "each month is simulated from flat equity; the run-drawdown stop therefore resets monthly",
            "survivorship bias: today's large caps applied back to 2016",
            "FOMC blackout only where config/macro_calendar.yaml has dates (2025-2026)",
            "earnings-day filter relies on Alpaca news only; agent room not replayed (rules-only)",
            "fills: next-bar open at modelled bid/ask + impact, no queue model",
            "cost split: gross = bought at the fill bar's open, sold at the stop / target level or the exit bar's "
            "close; spread, impact, slippage past stops and fees add up to gross minus net",
        ],
    }
    if default.size_atr:
        rep["caveats"].append(f"sizing: quantity keeps the dollar risk of a {default.size_atr:g} ATR stop (the "
                              "position cap usually binds there, so the wider stop means fewer shares); the "
                              "trailing stop still trails 1.5 ATR after +1R")
    if any(p.vix_min for p in grid):
        rep["caveats"].append("VIX filter: previous day's close (FRED VIXCLS); size is still halved above 25 and "
                              "the strategy is still off above 35")
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    tag = f"wf_{a.variant}_{a.label}_{tag}" if a.label else f"wf_{a.variant}_{tag}"
    rep["files"] = {w: {"trades": f"trades_{a.level}_{tag}_{w}.csv", "daily": f"daily_{a.level}_{tag}_{w}.csv"}
                    for w in ("tuned", "default")}
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    with trials_f.open("a") as fh:
        for which, n_tr in (("tuned", len(grid) * len(folds)), ("default", 1)):
            books = [f.tuned if which == "tuned" else f.default for f in wf.folds]
            trades = pd.DataFrame([t.__dict__ for b in books for t in b.trades],
                                  columns=[f.name for f in dataclasses.fields(Trade)])  # header even with 0 trades
            daily = pd.DataFrame([r for b in books for r in b.daily])
            trades.to_csv(out / f"trades_{a.level}_{tag}_{which}.csv", index=False)
            daily.to_csv(out / f"daily_{a.level}_{tag}_{which}.csv", index=False)
            tid = f"{a.level}_{tag}_{which}"
            pd.DataFrame({"date": daily["date"].astype(str), "ret": daily["pnl"] / daily["start"]}).to_csv(
                tdir / f"{tid}.csv", index=False)
            s = summary[which]
            fh.write(json.dumps({
                "ts": rep["run_at"], "trial_id": tid, "strategy": "dip_reversal", "variant": a.variant,
                "label": f"walkforward {which} {a.label}".strip(), "level": a.level, "universe": rep["universe"],
                "events_sha256": rep[f"events_sha256_{which}"], "input_sha256": wf.input_sha256,
                "code_sha": code_sha, "n_trials": n_tr,
                "experiment": a.experiment or None,
                "params": (f"walk-forward grid {grid_name} ({len(grid)}) x {len(folds)} folds" if which == "tuned"
                           else f"pre-registered {default.label}"),
                "window": summary["oos_window"],
                "metrics": {"trades": s["trades"], "net_return_pct": s["net_return_pct"],
                            "t_stat_per_trade": s["t_stat_per_trade"], "daily_sharpe_ann": s["daily_sharpe_ann"]},
            }, default=str) + "\n")
    # the report is written last: scripts/run_experiments.sh treats it as "this experiment is finished"
    (out / f"walkforward_{a.level}_{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / f"walkforward_{a.level}_{tag}.md").write_text(markdown(rep))
    print(markdown(rep))
    print(f"runtime {rep['runtime_s']}s, peak RSS {rep['peak_rss_mb']} MB")


if __name__ == "__main__":
    main()
