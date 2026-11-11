"""M5 S48: replay one owner config on the design window (CLAUDE.md: every parameter change runs a replay that logs a
trial). Uses the same pipeline and simulator as scripts/run_replay.py (Variant A exits), with the config's rules:
risk limits, focus (Only keeps the focus symbols), entry window, pause rule and VIX limit.

Caveats recorded with every result: Prefer mode ranks only when slots compete, which the replay does not model
(it replays as All); SPY momentum is not part of this replay; the spread and price filters need quotes the bar data
lacks, so the universe filter (>= $5, >= $50M ADV) stands in for them."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.engine.userconfig import Rules
from kaibot.evaluation.metrics import max_drawdown, sharpe, t_stat
from kaibot.replay.data import load_news_times, load_symbol
from kaibot.replay.features import regime
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import run_portfolio

DESIGN = ("2025-02-01", "2026-06-30")


class NoData(RuntimeError):
    """The minute-bar history (M0 #3) or the VIX cache is not on this machine yet."""


def have_data(data_dir: Path, symbols: list[str]) -> bool:
    return all(any((data_dir / "bars_1m" / f"symbol={s}").glob("*.parquet")) for s in symbols) and (
        data_dir / "inputs" / "fred_vixcls.json").exists()


def load_vix(data_dir: Path) -> pd.Series:
    obs = json.loads((data_dir / "inputs" / "fred_vixcls.json").read_text())
    obs = obs.get("observations", obs) if isinstance(obs, dict) else obs
    s = pd.Series({pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o.get("value") not in (".", None)})
    return s.sort_index().shift(1)  # previous close: point-in-time


def replay_config(rules: Rules, cfg: dict, data_dir: Path, fomc: set, equity0: float = 100_000.0,
                  window: tuple[str, str] = DESIGN) -> dict:
    sector_of = {s: e for e, syms in cfg["sectors"].items() for s in syms}
    if not have_data(data_dir, [cfg["market"], *cfg["sectors"], *sector_of]):
        raise NoData("minute-bar history or the VIX cache is missing (run ./scripts/fetch_history.sh, M0 #3)")
    start, end = np.datetime64(window[0], "D"), np.datetime64(window[1], "D")
    lo = start - np.timedelta64(120, "D")
    spy = load_symbol(data_dir, cfg["market"], lo, end)
    etfs = {e: load_symbol(data_dir, e, lo, end) for e in cfg["sectors"]}
    tradable = [s for s in sector_of if rules.tradable(s)]
    stocks = {s: load_symbol(data_dir, s, lo, end) for s in tradable}
    stocks = {s: sd for s, sd in stocks.items() if sd is not None}
    news = {s: load_news_times(data_dir, s) for s in stocks}
    prep = prepare(stocks, etfs, sector_of, news, start, end, arrays=False)
    cands = prep.cands
    if len(cands):
        cands = cands[(cands["m"] + 1 >= rules.first_entry_m) & (cands["m"] + 1 <= rules.last_entry_m)]
    vix = load_vix(data_dir)
    reg = {}
    for d, (off, vx) in regime(spy, vix).items():
        if start <= d <= end:
            reg[d] = (off | (np.isfinite(vx) and vx > rules.vix_max), vx)
    book = run_portfolio(prep.bars, cands, reg, fomc, rules.policy, equity0=equity0, variant="A",
                         pause_after_losses=rules.pause_losses, pause_minutes=rules.pause_minutes)
    trades = pd.DataFrame([t.__dict__ for t in book.trades])
    daily = pd.DataFrame(book.daily)
    n = len(trades)
    per_trade = (trades["pnl"] / equity0).to_numpy() if n else np.array([])
    daily_ret = (daily["pnl"] / daily["start"]).to_numpy() if len(daily) else np.array([])
    eq = equity0 + np.cumsum(daily["pnl"].to_numpy()) if len(daily) else np.array([equity0])
    months = (end - start).astype(int) / 30.44
    return {
        "window": list(window), "symbols": len(stocks), "trades": n,
        "trades_per_month": round(n / months, 1) if months else None,
        "net_pnl_usd": round(float(trades["pnl"].sum()), 2) if n else 0.0,
        "net_return_pct": round(float(trades["pnl"].sum()) / equity0 * 100, 3) if n else 0.0,
        "t_stat_per_trade": round(t_stat(per_trade), 3), "daily_sharpe_ann": round(sharpe(daily_ret) * np.sqrt(252), 3),
        "max_drawdown": round(max_drawdown(np.r_[equity0, eq]), 4),
        "policy": asdict(rules.policy), "config_sha": rules.config_sha,
        "caveats": ["Prefer mode replays as All (ranking only matters when slots compete)",
                    "SPY momentum not included", "spread/price filters approximated by the universe filter",
                    "survivorship: today's large caps applied to the past"],
    }
