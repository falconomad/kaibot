"""S14 shadow-strategy replay: ORB on Stocks in Play or news continuation, vs random entries and SPY, after shadow
costs. Uses the SAME per-minute code as the live hook (kaibot.engine.shadow.step / ShadowBook), so a replay day and
a live day see identical decisions on identical bars.

python scripts/run_shadow_replay.py --strategy orb|news_continuation [--level balanced] [--runs 200]
    [--start 2025-02-01] [--end 2099-12-31] [--label design]

Rules are pre-registered (strategies/orb.py, strategies/news_continuation.py); nothing is tuned here. Each run
appends a trial row to data/trials.jsonl (feeds the deflated Sharpe). Offline: needs data/bars_1m and data/news
from scripts/fetch_history.py; no network calls. The book is per strategy and per day (start-of-day equity,
compounded across days), sized by risk/gate.py at --level, as the live shadow does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.engine.shadow import ShadowBook, merged_stats, step  # noqa: E402
from kaibot.evaluation.metrics import max_drawdown, profit_factor, random_entry_percentile, sharpe, t_stat  # noqa: E402
from kaibot.replay.data import ET, N_MIN, load_news_times, load_symbol  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import news_continuation as news  # noqa: E402
from kaibot.strategies import orb  # noqa: E402
from kaibot.strategies.shadow_types import Panel, ShadowCandidate  # noqa: E402

NAMES = {"orb": orb.NAME, "news_continuation": news.NAME}


def to_utc64(day: np.datetime64, minutes_after_0930: int) -> np.datetime64:
    t = pd.Timestamp(f"{day} 09:30", tz=ET) + pd.Timedelta(minutes=minutes_after_0930)
    return np.datetime64(t.tz_convert("UTC").tz_localize(None), "s")


class Universe:
    def __init__(self, data: Path, symbols: list[str]):
        self.sd, self.news, self.stats, self.row = {}, {}, {}, {}
        for s in symbols:
            sd = load_symbol(data, s)
            if sd is None:
                continue
            self.sd[s] = sd
            self.news[s] = load_news_times(data, s)
            self.stats[s] = (orb.prior_stats(sd), news.prior_stats(sd))
            self.row[s] = {d: i for i, d in enumerate(sd.dates)}

    def day(self, d: np.datetime64):
        syms = [s for s in self.sd if d in self.row[s] and self.row[s][d] > 0]
        idx = [self.row[s][d] for s in syms]
        arr = {k: np.stack([getattr(self.sd[s], k)[i] for s, i in zip(syms, idx, strict=True)])
               for k in ("o", "h", "l", "c", "v", "vw")} if syms else {}
        panel = Panel(np.array(syms), **arr) if syms else None
        rows = [{orb.NAME: {k: v[i] for k, v in self.stats[s][0].items()},
                 news.NAME: {k: v[i] for k, v in self.stats[s][1].items()}} for s, i in zip(syms, idx, strict=True)]
        prev_close = [to_utc64(self.sd[s].dates[i - 1], 390) for s, i in zip(syms, idx, strict=True)]
        return syms, panel, merged_stats(rows), prev_close

    def news_flags(self, syms, prev_close, d):
        def flags(rows: np.ndarray, m: int) -> np.ndarray:
            end = to_utc64(d, m + 1) - np.timedelta64(news.NewsParams().min_news_age_min, "m")
            out = np.zeros(len(rows), dtype=bool)
            for j, r in enumerate(rows):
                t = self.news[syms[r]]
                lo = np.searchsorted(t, prev_close[r], side="left")
                hi = np.searchsorted(t, end, side="right")
                out[j] = hi > lo
            return out

        return flags


def random_runs(strategy, days_n, uni: Universe, pol, equity_by_day, fomc, n_runs, seed=7):
    """Same number of sized trades per day, random symbol and minute, the strategy's own stop rule, gate sizing and
    exit (stop or 15:55)."""
    rng = np.random.default_rng(seed)
    totals = np.zeros(n_runs)
    cache = {d: uni.day(d) for d in days_n}
    for k in range(n_runs):
        tot = 0.0
        for d, n in days_n.items():
            syms, p, st, _ = cache[d]
            if p is None:
                continue
            book = ShadowBook(strategy, pol, equity_by_day[d], d in fomc)
            picks = []
            for _ in range(int(n)):
                r = int(rng.integers(len(syms)))
                m = int(rng.integers(1, 75)) * 5 + 4 if strategy == news.NAME else int(rng.integers(5, 375))
                if strategy == orb.NAME:
                    stop_d = orb.DEFAULT.stop_atr_frac * st[orb.NAME]["atr14"][r]
                else:
                    with np.errstate(invalid="ignore"):
                        stop_d = news.DEFAULT.atr_mult * news._atr5(p, (m - 4) // 5, news.DEFAULT.atr_bars)[r]
                if np.isfinite(stop_d) and stop_d > 0:
                    picks.append((m, r, stop_d))
            for m, r, stop_d in sorted(picks):
                ref = float(p.c[r, m])
                e = book.consider(ShadowCandidate(strategy, syms[r], r, m, ref, ref - stop_d, None, "time_1555",
                                                  ("random",)), p, m)
                res = book.counterfactual(e, p, float(st[news.NAME]["sigma_1m"][r]))
                tot += res["pnl_usd"] if res else 0.0
        totals[k] = tot
    return totals


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=sorted(NAMES))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--start", default="2025-02-01")
    ap.add_argument("--end", default="2099-12-31")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--symbols", default="", help="comma-separated override of config/universe_seed.yaml")
    a = ap.parse_args(argv)
    strategy = NAMES[a.strategy]
    data = Path(a.data)
    cfg = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
    cal = yaml.safe_load((ROOT / "config" / "macro_calendar.yaml").read_text())
    fomc = {np.datetime64(str(d), "D") for d in cal["fomc_statement_1400"]}
    pol = load_profiles()[a.level]
    symbols = a.symbols.split(",") if a.symbols else sorted({s for ss in cfg["sectors"].values() for s in ss})
    uni = Universe(data, symbols)
    spy = load_symbol(data, cfg["market"])
    start, end = np.datetime64(a.start, "D"), np.datetime64(a.end, "D")
    dates = [d for d in spy.dates if start <= d <= end]

    equity, trades, daily, eq_by_day = a.equity, [], [], {}
    n_signals = 0
    for d in dates:
        syms, p, st, prev_close = uni.day(d)
        eq_by_day[d] = equity
        pnl = 0.0
        if p is not None:
            books = {strategy: ShadowBook(strategy, pol, equity, d in fomc)}
            seen: set = set()
            flags = uni.news_flags(syms, prev_close, d)
            for m in range(N_MIN):
                step(books, p, st, m, flags, seen)
            for e in books[strategy].entries:
                n_signals += 1
                res = books[strategy].counterfactual(e, p, float(st[news.NAME]["sigma_1m"][e.cand.row]))
                if res:
                    trades.append({"date": d, "symbol": e.cand.symbol, "m": e.cand.m, "risk": e.risk_ps * e.qty,
                                   **res})
                    pnl += res["pnl_usd"]
        daily.append({"date": d, "start": equity, "pnl": pnl, "end": equity + pnl,
                      "trades": sum(1 for t in trades if t["date"] == d)})
        equity += pnl

    tr, dl = pd.DataFrame(trades), pd.DataFrame(daily)
    n = len(tr)
    per_trade = (tr["pnl_usd"] / a.equity).to_numpy() if n else np.array([])
    daily_ret = (dl["pnl"] / dl["start"]).to_numpy() if len(dl) else np.array([])
    tpd = tr.groupby("date").size() if n else pd.Series(dtype=int)
    rand = random_runs(strategy, tpd.to_dict(), uni, pol, eq_by_day, fomc, a.runs) if n else np.array([])
    spy_close = pd.Series(spy.c[:, -1], index=spy.dates)
    spy_close = spy_close[(spy_close.index >= start) & (spy_close.index <= end)]
    total = float(tr["pnl_usd"].sum()) if n else 0.0
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout
    rep = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "code_sha": sha.strip(), "mode": "shadow",
        "strategy": strategy, "level": a.level, "equity": a.equity, "start": a.start, "end": a.end, "label": a.label,
        "sessions": len(dl), "symbols": len(uni.sd), "signals": n_signals, "trades": n,
        "net_pnl_usd": round(total, 2), "net_return_pct": round(total / a.equity * 100, 3),
        "mean_bp_per_trade": round(float(per_trade.mean() * 1e4), 2) if n else None,
        "t_stat_per_trade": round(t_stat(per_trade), 2) if n > 1 else None,
        "hit_rate": round(float((tr["pnl_usd"] > 0).mean()), 3) if n else None,
        "profit_factor": round(profit_factor(tr["pnl_usd"].to_numpy()), 2) if n else None,
        "avg_R": round(float(tr["r_multiple"].mean()), 3) if n else None,
        "exit_reasons": tr["reason"].value_counts().to_dict() if n else {},
        "daily_sharpe_ann": round(sharpe(daily_ret) * np.sqrt(252), 2) if len(dl) > 1 else None,
        "max_drawdown_pct": round(max_drawdown(np.r_[a.equity, dl["end"].to_numpy()]) * 100, 2) if len(dl) else None,
        "spy_return_pct": round(float(spy_close.iloc[-1] / spy_close.iloc[0] - 1) * 100, 2) if len(spy_close) else None,
        "spy_daily_sharpe_ann": round(sharpe(spy_close.pct_change().dropna().to_numpy()) * np.sqrt(252), 2)
        if len(spy_close) > 2 else None,
        "random_runs": len(rand),
        "random_mean_pnl_usd": round(float(rand.mean()), 2) if len(rand) else None,
        "random_p95_pnl_usd": round(float(np.percentile(rand, 95)), 2) if len(rand) else None,
        "strategy_percentile_vs_random": round(random_entry_percentile(total, rand), 1) if len(rand) else None,
        "caveats": [
            "survivorship bias: today's large caps applied to the past",
            "Stocks in Play ranked within Kaibot's universe, not ~7,000 US stocks (much weaker selection)",
            "entry on a 1-minute close above the range, filled at the next open (paper: resting buy-stop)",
            "news = any Alpaca/Benzinga headline (no materiality filter); direction from price only",
            "fills: next-bar open at modelled bid/ask + impact; exit at 15:55; long only",
        ],
    }
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    tag = "_".join(x for x in (a.strategy, a.label, datetime.now(UTC).strftime("%Y%m%dT%H%M%S")) if x)
    (out / f"shadow_replay_{a.level}_{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    tr.to_csv(out / f"shadow_trades_{a.level}_{tag}.csv", index=False)
    with (data / "trials.jsonl").open("a") as fh:
        fh.write(json.dumps({"ts": rep["run_at"], "strategy": strategy, "mode": "shadow", "level": a.level,
                             "code_sha": rep["code_sha"], "params": f"pre-registered {a.label}".strip(),
                             "window": [a.start, str(dl["date"].max()) if len(dl) else None],
                             "metrics": {k: rep[k] for k in ("trades", "net_return_pct", "t_stat_per_trade",
                                                             "daily_sharpe_ann")}}, default=str) + "\n")
    print(json.dumps(rep, indent=1, default=str))
    return rep


if __name__ == "__main__":
    main()
