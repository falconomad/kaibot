"""M0 replay: no-news dip reversal (rules-only) vs random entries and SPY, after shadow costs.

python scripts/run_replay.py --env .env [--level balanced] [--runs 200] [--start 2025-02-01]

The rules are pre-registered from research 06 / quant memo (no parameter was tuned on this data), so the whole
period is out-of-sample for them. Every run appends a trial row to data/trials.jsonl (feeds the deflated Sharpe).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation.metrics import max_drawdown, profit_factor, random_entry_percentile, sharpe, t_stat  # noqa: E402
from kaibot.replay.data import load_news_times, load_symbol  # noqa: E402
from kaibot.replay.events import events, events_digest, input_digest  # noqa: E402
from kaibot.replay.features import regime  # noqa: E402
from kaibot.replay.pipeline import prepare  # noqa: E402
from kaibot.replay.sim import random_entry_runs, run_portfolio  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402


def load_env(p):
    env = {}
    for line in Path(p).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def vix_prev_close(fred_key: str, cache: Path, refresh: bool = False) -> pd.Series:
    """VIXCLS from FRED, cached under data/inputs so a re-run reads the same series (event-sourced input)."""
    if refresh or not cache.exists():
        url = ("https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&observation_start=2015-06-01"
               f"&file_type=json&api_key={fred_key}")
        with urllib.request.urlopen(url, timeout=30) as r:
            obs = json.loads(r.read())["observations"]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([{"date": o["date"], "value": o["value"]} for o in obs]))
    obs = json.loads(cache.read_text())
    s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o["value"] != "."}).sort_index()
    # value dated d is d's close -> available for session d+1 only
    return s.shift(1, freq="D").resample("D").ffill()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--start", default="2025-02-01", help="first traded session (earlier data warms up stats)")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--end", default="2099-12-31", help="last traded session (inclusive)")
    ap.add_argument("--variant", default="v1", choices=["v1", "A", "B"])
    ap.add_argument("--label", default="", help="e.g. design / holdout")
    ap.add_argument("--universe", default=str(ROOT / "config" / "universe_seed.yaml"))
    ap.add_argument("--data-from", default="", help="first bar date to load (default: start - 120 days)")
    ap.add_argument("--refresh-vix", action="store_true", help="re-download VIXCLS instead of the cached copy")
    a = ap.parse_args()
    env = load_env(a.env)
    data = Path(a.data)
    cfg = yaml.safe_load(Path(a.universe).read_text())
    cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
    fomc = {np.datetime64(str(d), "D") for d in cal["fomc_statement_1400"]}
    pol = load_profiles()[a.level]

    start = np.datetime64(a.start, "D")
    end = np.datetime64(a.end, "D")
    # Warm-up: the longest feature window is the 60-session beta, so ~120 calendar days before the first session.
    # --data-from pins it explicitly (replays 001-003 had data from 2025-01-01 only; pass that to reproduce them).
    lo = np.datetime64(a.data_from, "D") if a.data_from else start - np.timedelta64(120, "D")
    spy = load_symbol(data, cfg["market"], lo, end)
    etfs = {e: load_symbol(data, e, lo, end) for e in cfg["sectors"]}
    sector_of = {s: e for e, syms in cfg["sectors"].items() for s in syms}
    stocks = {s: load_symbol(data, s, lo, end) for s in sector_of}
    stocks = {s: sd for s, sd in stocks.items() if sd is not None}
    news = {s: load_news_times(data, s) for s in stocks}
    prep = prepare(stocks, etfs, sector_of, news, start, end, z_max=-2.0 if a.variant == "B" else -1.5)
    bars, cands, atr5, vwap, sigma = prep.bars, prep.cands, prep.atr5, prep.vwap, prep.sigma
    if a.variant == "B" and len(cands):
        # Pre-registered B: point-in-time quality score >= 70 (10-K data filed before that date only).
        from kaibot.replay.quality import quality_panel

        qdates = np.arange(start, end + np.timedelta64(1, "D"))
        qp = quality_panel(Path("data/fundamentals/companyfacts"), sorted(bars), qdates)
        ql = qp.stack().rename("quality").reset_index()
        ql.columns = ["date", "symbol", "quality"]
        ql["date"] = pd.to_datetime(ql["date"]).values.astype("datetime64[D]")
        c2 = cands.copy()
        c2["date_key"] = pd.to_datetime(c2["date"]).values.astype("datetime64[D]")
        c2 = c2.merge(ql.rename(columns={"date": "date_key"}), on=["date_key", "symbol"], how="left")
        n_before = len(c2)
        cands = c2[c2["quality"] >= 70].drop(columns=["date_key"]).reset_index(drop=True)
        print(f"quality filter: {n_before} -> {len(cands)} candidates")
    sim_variant = "A" if a.variant == "B" else a.variant
    vix = vix_prev_close(env.get("FRED_API_KEY", ""), data / "inputs" / "fred_vixcls.json", a.refresh_vix)
    reg_all = regime(spy, vix)
    reg = {d: v for d, v in reg_all.items() if start <= d <= end}

    book = run_portfolio(bars, cands, reg, fomc, pol, equity0=a.equity, variant=sim_variant)
    daily = pd.DataFrame(book.daily)
    trades = pd.DataFrame([t.__dict__ for t in book.trades])
    n = len(trades)
    per_trade_ret = (trades["pnl"] / a.equity).to_numpy() if n else np.array([])
    daily_ret = (daily["pnl"] / daily["start"]).to_numpy()
    tpd = trades.groupby("date").size() if n else pd.Series(dtype=int)
    rand = random_entry_runs(bars, atr5, vwap, sigma, tpd, pol, a.equity, n_runs=a.runs,
                             variant=sim_variant) if n else np.array([])
    spy_close = pd.Series(spy.c[:, -1], index=spy.dates)
    spy_close = spy_close[(spy_close.index >= start) & (spy_close.index <= end)]
    spy_ret = spy_close.pct_change().dropna().to_numpy()

    total = float(trades["pnl"].sum()) if n else 0.0
    vix_used = {str(k.date()): float(v) for k, v in vix.items() if lo <= np.datetime64(k.date(), "D") <= end}
    params = {"level": a.level, "policy": dataclasses.asdict(pol),
              "equity": a.equity, "start": a.start, "end": a.end, "variant": a.variant, "runs": a.runs,
              "data_from": str(lo), "fomc": sorted(str(d) for d in fomc),
              "universe_order": list(bars), "quality_filter": a.variant == "B"}
    in_sha = input_digest({**bars, "SPY": spy, **{f"ETF:{e}": sd for e, sd in etfs.items() if sd is not None}},
                          news={s: news[s] for s in bars}, series={"vix": vix_used}, params=params)
    ev_sha = events_digest(book)
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                   cwd=ROOT).stdout.strip(),
        "level": a.level, "equity": a.equity, "start": a.start, "end": a.end, "variant": a.variant,
        "label": a.label, "universe": Path(a.universe).name, "data_from": str(lo),
        "input_sha256": in_sha, "events_sha256": ev_sha,
        "random_totals_sha256": hashlib.sha256(np.round(rand, 6).tobytes()).hexdigest(),
        "sessions": int(len(daily)), "symbols": len(bars), "candidates_raw": int(len(cands)),
        "candidates_no_news": int((~cands["news_24h"]).sum()) if len(cands) else 0,
        "trades": n,
        "net_pnl_usd": round(total, 2), "net_return_pct": round(total / a.equity * 100, 3),
        "mean_bp_per_trade": round(float(per_trade_ret.mean() * 1e4), 2) if n else None,
        "t_stat_per_trade": round(t_stat(per_trade_ret), 2) if n > 1 else None,
        "hit_rate": round(float((trades["pnl"] > 0).mean()), 3) if n else None,
        "profit_factor": round(profit_factor(trades["pnl"].to_numpy()), 2) if n else None,
        "avg_R": round(float((trades["pnl"] / trades["risk"]).mean()), 3) if n else None,
        "exit_reasons": trades["reason"].value_counts().to_dict() if n else {},
        "daily_sharpe_ann": round(sharpe(daily_ret) * np.sqrt(252), 2),
        "losing_day_share_of_trading_days": round(float((daily.loc[daily["trades"] > 0, "pnl"] < 0).mean()), 3)
        if (daily["trades"] > 0).any() else None,
        "days_with_trades": int((daily["trades"] > 0).sum()),
        "max_drawdown_pct": round(max_drawdown(np.r_[a.equity, daily["end"].to_numpy()]) * 100, 2),
        "spy_return_pct": round(float(spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 2),
        "spy_daily_sharpe_ann": round(sharpe(spy_ret) * np.sqrt(252), 2),
        "random_runs": int(len(rand)),
        "random_mean_pnl_usd": round(float(rand.mean()), 2) if len(rand) else None,
        "random_p95_pnl_usd": round(float(np.percentile(rand, 95)), 2) if len(rand) else None,
        "strategy_percentile_vs_random": round(random_entry_percentile(total, rand), 1) if len(rand) else None,
        "min_trades_for_claim": 900,
        "caveats": [
            "survivorship bias: today's large caps applied to the past",
            ("point-in-time quality >= 70 applied (10-K data filed before each date)" if a.variant == "B"
             else "no point-in-time quality filter (variant B only)"),
            "earnings-day filter relies on Alpaca news only (no historical earnings calendar)",
            "agent room not replayed (LLM lookahead); rules-only",
            "fills: next-bar open at modelled bid/ask + impact, no queue model; target needs trade-through",
        ],
    }
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    tag = f"{a.variant}_{a.label}_{tag}" if a.label else f"{a.variant}_{tag}"
    (out / f"replay_{a.level}_{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    trades.to_csv(out / f"trades_{a.level}_{tag}.csv", index=False)
    daily.to_csv(out / f"daily_{a.level}_{tag}.csv", index=False)
    with (out / f"events_{a.level}_{tag}.jsonl").open("w") as fh:
        for e in events(book):
            fh.write(json.dumps(e, default=str) + "\n")
    # Per-trial daily return series: the input to PBO and to the cross-trial Sharpe variance used by the DSR.
    trial_id = f"{a.level}_{tag}"
    tdir = data / "trials"
    tdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": daily["date"].astype(str), "ret": daily_ret}).to_csv(tdir / f"{trial_id}.csv", index=False)
    with (data / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": rep["run_at"], "trial_id": trial_id, "strategy": "dip_reversal",
                             "variant": a.variant, "label": a.label, "level": a.level,
                             "universe": rep["universe"], "events_sha256": ev_sha, "input_sha256": in_sha,
                             "code_sha": rep["code_sha"], "params": f"pre-registered {a.variant} {a.label}".strip(),
                             "window": [a.start, str(daily["date"].max()) if len(daily) else None],
                             "metrics": {k: rep[k] for k in ("trades", "net_return_pct", "t_stat_per_trade",
                                                             "daily_sharpe_ann")}}, default=str) + "\n")
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
