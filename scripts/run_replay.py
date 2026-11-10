"""M0 replay: no-news dip reversal (rules-only) vs random entries and SPY, after shadow costs.

python scripts/run_replay.py --env .env [--level balanced] [--runs 200] [--start 2025-02-01]

The rules are pre-registered from research 06 / quant memo (no parameter was tuned on this data), so the whole
period is out-of-sample for them. Every run appends a trial row to data/trials.jsonl (feeds the deflated Sharpe).
"""

from __future__ import annotations

import argparse
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
from kaibot.replay.features import candidates, five_minute, regime  # noqa: E402
from kaibot.replay.sim import random_entry_runs, run_portfolio  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402


def load_env(p):
    env = {}
    for line in Path(p).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return env


def vix_prev_close(fred_key: str) -> pd.Series:
    url = ("https://api.stlouisfed.org/fred/series/observations?series_id=VIXCLS&observation_start=2024-06-01"
           f"&file_type=json&api_key={fred_key}")
    with urllib.request.urlopen(url, timeout=30) as r:
        obs = json.loads(r.read())["observations"]
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
    a = ap.parse_args()
    env = load_env(a.env)
    data = Path(a.data)
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
    fomc = {np.datetime64(str(d), "D") for d in cal["fomc_statement_1400"]}
    pol = load_profiles()[a.level]

    spy = load_symbol(data, cfg["market"])
    etfs = {e: load_symbol(data, e) for e in cfg["sectors"]}
    bars, cand_frames, atr5, vwap, sigma = {}, [], {}, {}, {}
    for etf, syms in cfg["sectors"].items():
        for s in syms:
            sd = load_symbol(data, s)
            if sd is None or etfs[etf] is None:
                continue
            bars[s] = sd
            cf = candidates(sd, etfs[etf], load_news_times(data, s), z_max=-2.0 if a.variant == "B" else -1.5)
            if len(cf):
                cand_frames.append(cf)
            atr5[s], _ = five_minute(sd.o, sd.h, sd.l, sd.c)
            vwap[s] = np.cumsum(sd.vw * sd.v, axis=1) / np.maximum(np.cumsum(sd.v, axis=1), 1)
            close = sd.c[:, -1]
            r = np.r_[np.nan, close[1:] / close[:-1] - 1]
            sigma[s] = pd.Series(r).shift(1).rolling(20, min_periods=20).std().to_numpy() / np.sqrt(390)
    start = np.datetime64(a.start, "D")
    end = np.datetime64(a.end, "D")
    cands = pd.concat(cand_frames, ignore_index=True) if cand_frames else pd.DataFrame()
    if len(cands):
        cands = cands[(cands["date"] >= start) & (cands["date"] <= end)]
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
    reg_all = regime(spy, vix_prev_close(env["FRED_API_KEY"]))
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
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                   cwd=ROOT).stdout.strip(),
        "level": a.level, "equity": a.equity, "start": a.start, "end": a.end, "variant": a.variant,
        "label": a.label,
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
    with (data / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": rep["run_at"], "strategy": "dip_reversal", "level": a.level,
                             "code_sha": rep["code_sha"], "params": f"pre-registered {a.variant} {a.label}".strip(),
                             "window": [a.start, str(daily["date"].max()) if len(daily) else None],
                             "metrics": {k: rep[k] for k in ("trades", "net_return_pct", "t_stat_per_trade",
                                                             "daily_sharpe_ann")}}, default=str) + "\n")
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
