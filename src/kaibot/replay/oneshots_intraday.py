"""The two intraday one-shot rules of issue #112 (story S72): the opening-range breakout on Stocks in Play
(`orb_stocks_in_play`, keep or delete) and `news_continuation`. Research only, PROVISIONAL.

The rules are the shadow strategies as committed (`strategies/orb.py`, `strategies/news_continuation.py`); this
module only replays them on ten years of minute bars with the fill model and statistics pre-registered in
docs/prereg/<rule>.md. Fills and exits reuse `replay/sim.py` (`buy_px`, `sell_px`, `first_exit`) and the fees of
`broker/shadow_costs.py`: no new simulator.

Two passes, so that only one symbol's minutes are in memory at a time:
1. per symbol (`orb_symbol_rows`, `news_symbol_rows`): every session that passes the rule's own filters, with
   what is needed to rank it and the would-be trade's outcome (net R, net return on the position, gross R). The
   outcome of one symbol-day does not depend on any other symbol, so this is exact.
2. across symbols (`orb_select`, `news_select`, `account`, `trade_stats`): the ORB's top-20 relative-volume
   ranking per day, the daily trade limits, the account curve and the keep / pass marks.

Point in time: ORB orders are placed at 09:50 ET (minute 20), when the consolidated minutes 09:30-09:34 behind
the relative volume are 15 minutes old (free SIP); news counts only headlines created at least 15 minutes before
the decision. Every statistic reads the session's minutes up to the decision for the decision, and later minutes
only for the trade's own fill and exit.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import sell_fees
from kaibot.evaluation.metrics import t_stat
from kaibot.replay.data import ET, N_MIN, SymbolDays
from kaibot.replay.sim import EXIT_M, buy_px, first_exit, sell_px
from kaibot.strategies import news_continuation as news
from kaibot.strategies import orb
from kaibot.strategies.shadow_types import Panel, rolling_before

PLACE_M = 20  # 09:50 ET: the first five SIP minutes are 15 minutes old
NOTIONAL = 100_000.0  # the account the order quantities (impact term) are those of
ORB_TOP = {"base": 20, "top10": 10, "top40": 40}
ORB_STOPS = {"base": 0.10, "stop20": 0.20}
KEEP_MIN_R, KEEP_MIN_T, KEEP_DROP_SYMBOLS = 0.05, 3.0, 10
NEWS_SLOTS, NEWS_WEIGHT = 10, 0.10
NEWS_VARIANTS: dict[str, news.NewsParams] = {
    "base": news.DEFAULT,
    "move15": dataclasses.replace(news.DEFAULT, min_move_sigma=1.5),
    "relvol2": dataclasses.replace(news.DEFAULT, min_rel_vol=2.0),
    "relvol5": dataclasses.replace(news.DEFAULT, min_rel_vol=5.0),
    "stop1": dataclasses.replace(news.DEFAULT, atr_mult=1.0),
    "stop2": dataclasses.replace(news.DEFAULT, atr_mult=2.0),
}
NEWS_GRID = tuple(range(news.DEFAULT.first_m, news.DEFAULT.last_m + 1, 5))  # the rule's decision minutes
COMPLETE_SHARE, COMPLETE_MEDIAN_FRAC = 0.90, 0.50
GATE = {"min_trades": 900, "t_min": 3.0, "random_pct_min": 95.0, "random_runs": 1000}


def sigma_1m(sd: SymbolDays) -> np.ndarray:
    """Per session: std of the 20 prior daily close-to-close returns / sqrt(390) (the cost model's sigma)."""
    close = sd.c[:, -1]
    r = np.r_[np.nan, close[1:] / close[:-1] - 1]
    return rolling_before(r, 20, "std")[:-1] / math.sqrt(N_MIN)


def _trade(o, h, l, c, v, entry_m: int, mid: float, risk_ps: float, sig: float, scan_from: int) -> dict:  # noqa: E741
    """Buy `mid` at minute `entry_m` (cost model v1 on top), stop `risk_ps` below the fill, time exit at 15:55."""
    qty = NOTIONAL / 20 / mid
    s = sig if math.isfinite(sig) else 0.0
    fill = buy_px(mid, qty, s, float(v[entry_m]))
    stop = fill - risk_ps
    xm, gross, why = first_exit(h, l, o, c, scan_from, stop, math.inf)
    xp = sell_px(gross, qty, s, float(v[min(xm, N_MIN - 1)]))
    pnl_ps = xp - fill - sell_fees(qty, xp) / qty
    return {"fill": fill, "exit": xp, "exit_m": int(xm), "reason": why, "r_net": pnl_ps / risk_ps,
            "r_gross": (gross - mid) / risk_ps, "ret": pnl_ps / fill}


# ---------------------------------------------------------------- ORB, pass 1


def orb_symbol_rows(sd: SymbolDays, p: orb.OrbParams = orb.DEFAULT, place_m: int = PLACE_M) -> pd.DataFrame:
    """Every session of one symbol that passes the paper's daily filter and has relative volume >= 1 (the ranking
    pool): its relative volume, whether the first candle is up, and for an up candle the outcome of the buy-stop at
    the opening-range high resting from `place_m`, per stop (ORB_STOPS) and per entry price (`pess`: the worse of
    the trigger and the next minute's high; `trig`: the trigger price)."""
    n = len(sd.dates)
    if n < p.lookback + 1:
        return pd.DataFrame()
    stats = {k: v[:-1] for k, v in orb.prior_stats(sd, p).items()}
    panel = Panel(np.array([sd.symbol] * n), sd.o, sd.h, sd.l, sd.c, sd.v, sd.vw)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok, rel = orb.in_play(panel, stats, p, groups=np.arange(n))  # one row per group: only the filter
    sig = sigma_1m(sd)
    k = p.or_minutes
    rows = []
    for i in np.flatnonzero(ok):
        o, h, l, c, v = sd.o[i], sd.h[i], sd.l[i], sd.c[i], sd.v[i]  # noqa: E741
        orh = float(np.nanmax(h[:k]))
        up = bool(c[k - 1] > o[0])
        row = {"date": sd.dates[i], "symbol": sd.symbol, "rel": float(rel[i]), "up": up, "atr14":
               float(stats["atr14"][i]), "or_high": orh, "triggered": False, "trigger_m": -1}
        if up:
            hit = np.flatnonzero(h[place_m:p.last_m + 1] >= orh)
            if len(hit):
                m = place_m + int(hit[0])
                trig = max(orh, float(o[m]))
                pess = max(trig, float(h[m + 1]))
                row.update(triggered=True, trigger_m=m)
                for sname, frac in ORB_STOPS.items():
                    risk = frac * float(stats["atr14"][i])
                    for fname, mid in (("pess", pess), ("trig", trig)):
                        t = _trade(o, h, l, c, v, m + 1 if fname == "pess" else m, mid, risk, sig[i], m + 1)
                        for key in ("r_net", "r_gross", "ret"):
                            row[f"{key}_{sname}_{fname}"] = t[key]
                        row[f"reason_{sname}_{fname}"] = t["reason"]
        rows.append(row)
    return pd.DataFrame(rows)


def orb_select(rows: pd.DataFrame, top_n: int = 20, stop: str = "base", fill: str = "pess") -> pd.DataFrame:
    """The trades: per day the top `top_n` by relative volume of the pool (down candles take a place and are not
    traded, as in the paper), the up candles among them whose stop triggered. Columns r_net, r_gross, ret."""
    if rows is None or not len(rows):
        return pd.DataFrame(columns=["date", "symbol", "rel", "r_net", "r_gross", "ret"])
    r = rows.sort_values(["date", "rel", "symbol"], ascending=[True, False, True])
    top = r.groupby("date", sort=False).head(top_n)
    t = top[top["up"] & top["triggered"]].copy()
    for key in ("r_net", "r_gross", "ret"):
        t[key] = t[f"{key}_{stop}_{fill}"]
    return t[["date", "symbol", "rel", "trigger_m", "r_net", "r_gross", "ret"]].reset_index(drop=True)


# ---------------------------------------------------------------- news continuation, pass 1


def session_opens_utc(dates: np.ndarray) -> np.ndarray:
    """09:30 ET of each date as naive UTC datetime64[s] (daylight saving handled per date)."""
    d = pd.DatetimeIndex(np.asarray(dates, dtype="datetime64[ns]")) + pd.Timedelta(hours=9, minutes=30)
    return d.tz_localize(ET).tz_convert("UTC").tz_localize(None).to_numpy().astype("datetime64[s]")


def news_flags(dates: np.ndarray, news_times: np.ndarray, grid: Sequence[int],
               min_age_min: int = news.DEFAULT.min_news_age_min) -> np.ndarray:
    """(sessions x grid) bool: a headline with created_at in [16:00 ET of the prior session, decision - min_age],
    the decision being the close of minute m (09:30 + m + 1 minutes). Row 0 has no prior session: False."""
    t = np.asarray(news_times, dtype="datetime64[s]")
    out = np.zeros((len(dates), len(grid)), dtype=bool)
    if not len(t) or len(dates) < 2:
        return out
    opens = session_opens_utc(dates)
    lo = np.searchsorted(t, opens[:-1] + np.timedelta64(N_MIN, "m"), side="left")  # prior session's 16:00 ET
    offs = (np.asarray(grid, dtype=np.int64) + 1 - min_age_min) * 60
    ends = opens[1:, None] + offs[None, :].astype("timedelta64[s]")
    hi = np.searchsorted(t, ends.ravel(), side="right").reshape(ends.shape)
    out[1:] = hi > lo[:, None]
    return out


def news_symbol_rows(sd: SymbolDays, news_times: np.ndarray,
                     variants: Mapping[str, news.NewsParams] = NEWS_VARIANTS) -> pd.DataFrame:
    """The first signal of each session per variant, with its trade (entry at the open of m + 1, stop re-anchored
    on the fill, 15:55 exit) and, for the base variant, the net R of the same symbol-day entered at every decision
    minute of the grid with the same stop distance (the random-entry baseline draws from it)."""
    n = len(sd.dates)
    if n < news.DEFAULT.lookback + 2:
        return pd.DataFrame()
    stats = {k: v[:-1] for k, v in news.prior_stats(sd).items()}
    panel = Panel(np.array([sd.symbol] * n), sd.o, sd.h, sd.l, sd.c, sd.v, sd.vw)
    flags = news_flags(sd.dates, news_times, NEWS_GRID)
    sig = stats["sigma_1m"]
    rows = []
    # the variants differ only in the move, volume and stop thresholds: one setup per minute at the loosest
    # thresholds, then each variant's own thresholds on its features (same result as setup with its params)
    loose = dataclasses.replace(news.DEFAULT, min_move_sigma=min(p.min_move_sigma for p in variants.values()),
                                min_rel_vol=min(p.min_rel_vol for p in variants.values()))
    assert all(dataclasses.replace(p, min_move_sigma=loose.min_move_sigma, min_rel_vol=loose.min_rel_vol,
                                   atr_mult=loose.atr_mult) == loose for p in variants.values())
    done = {v: np.zeros(n, dtype=bool) for v in variants}
    per_m = []
    for g, m in enumerate(NEWS_GRID):
        with np.errstate(invalid="ignore", divide="ignore"):
            mask, f = news.setup(panel, stats, m, loose)
        mask &= flags[:, g]
        per_m.append((m, mask, f))
    for vname, p in variants.items():
        for m, mask, f in per_m:
            if not mask.any():
                continue
            with np.errstate(invalid="ignore"):
                vm = mask & (f["move_sigma"] >= p.min_move_sigma) & (f["rel_vol_cum"] >= p.min_rel_vol)
            cand = np.flatnonzero(vm & ~done[vname])
            if not len(cand):
                continue
            with np.errstate(invalid="ignore"):
                atr = news._atr5(panel, (m - 4) // 5, p.atr_bars)
            for i in cand:
                if not (math.isfinite(atr[i]) and atr[i] > 0) or m + 1 > EXIT_M or not math.isfinite(sd.o[i, m + 1]):
                    continue
                done[vname][i] = True
                risk = p.atr_mult * float(atr[i])
                o, h, l, c, v = sd.o[i], sd.h[i], sd.l[i], sd.c[i], sd.v[i]  # noqa: E741
                t = _trade(o, h, l, c, v, m + 1, float(o[m + 1]), risk, float(sig[i]), m + 1)
                row = {"variant": vname, "date": sd.dates[i], "symbol": sd.symbol, "m": int(m), "r_net": t["r_net"],
                       "r_gross": t["r_gross"], "ret": t["ret"], "reason": t["reason"]}
                if vname == "base":
                    row["rand_r"] = [_trade(o, h, l, c, v, mm + 1, float(o[mm + 1]), risk, float(sig[i]),
                                            mm + 1)["r_net"] for mm in NEWS_GRID
                                     if mm + 1 <= EXIT_M and math.isfinite(o[mm + 1])]
                rows.append(row)
    return pd.DataFrame(rows)


def headline_months(times: np.ndarray) -> dict[str, int]:
    """Headline count per calendar month (YYYY-MM) of one symbol's stored headline times."""
    t = np.asarray(times, dtype="datetime64[s]")
    if not len(t):
        return {}
    mon, k = np.unique(t.astype("datetime64[M]").astype(str), return_counts=True)
    return {str(m): int(x) for m, x in zip(mon, k, strict=True)}


def complete_months(headlines: Mapping[str, Mapping[str, int]], bar_months: Mapping[str, Iterable[str]],
                    last_headline: np.datetime64 | None) -> list[str]:
    """The calendar months (YYYY-MM) with complete news history (docs/prereg/news_continuation.md): (a) at least
    90% of the symbols with minute bars that month have a stored headline in it, (b) the month's headline count is
    at least half the median monthly count of the store, (c) the month ends on or before the last headline.
    `headlines`: symbol -> {month: headlines}; `bar_months`: symbol -> months with minute bars."""
    per: dict[str, set[str]] = {}
    count: dict[str, int] = {}
    for s, months in headlines.items():
        for mon, k in months.items():
            if k > 0:
                per.setdefault(mon, set()).add(s)
                count[mon] = count.get(mon, 0) + int(k)
    with_bars: dict[str, set[str]] = {}
    for s, months in bar_months.items():
        for mon in months:
            with_bars.setdefault(mon, set()).add(s)
    if not count or last_headline is None:
        return []
    med = float(np.median(list(count.values())))
    last = np.datetime64(last_headline, "D")
    out = []
    for mon in sorted(with_bars):
        syms = with_bars[mon]
        share = len(per.get(mon, set()) & syms) / len(syms) if syms else 0.0
        end = (np.datetime64(mon, "M") + np.timedelta64(1, "M")).astype("datetime64[D]") - np.timedelta64(1, "D")
        if share >= COMPLETE_SHARE and count.get(mon, 0) >= COMPLETE_MEDIAN_FRAC * med and end <= last:
            out.append(mon)
    return out


def month_ranges(months: Sequence[str]) -> list[str]:
    """["2017-01", "2017-02", "2017-04"] -> ["2017-01 to 2017-02", "2017-04"]."""
    out, run = [], []
    for mon in sorted(months):
        if run and np.datetime64(mon, "M") - np.datetime64(run[-1], "M") != np.timedelta64(1, "M"):
            out.append(run[0] if len(run) == 1 else f"{run[0]} to {run[-1]}")
            run = []
        run.append(mon)
    if run:
        out.append(run[0] if len(run) == 1 else f"{run[0]} to {run[-1]}")
    return out


def news_select(rows: pd.DataFrame, variant: str = "base", months: Sequence[str] | None = None,
                symbol_order: Sequence[str] | None = None) -> pd.DataFrame:
    """The trades of one variant in the complete months, at most NEWS_SLOTS a day (by decision minute, then the
    universe file's order)."""
    if rows is None or not len(rows):
        return pd.DataFrame(columns=["date", "symbol", "m", "r_net", "r_gross", "ret"])
    t = rows[rows["variant"] == variant].copy()
    if months is not None:
        t = t[pd.to_datetime(t["date"]).dt.strftime("%Y-%m").isin(set(months))]
    order = {s: k for k, s in enumerate(symbol_order or sorted(t["symbol"].unique()))}
    t["_o"] = t["symbol"].map(order).fillna(len(order))
    t = t.sort_values(["date", "m", "_o"]).groupby("date", sort=False).head(NEWS_SLOTS)
    return t.drop(columns="_o").reset_index(drop=True)


# ---------------------------------------------------------------- pass 2: account and statistics


def account(trades: pd.DataFrame, sessions: np.ndarray, weight: float) -> np.ndarray:
    """Daily returns over `sessions`: each trade puts `weight` of the start-of-day equity in (never above 1x)."""
    s = pd.Series(0.0, index=pd.DatetimeIndex(np.asarray(sessions, dtype="datetime64[ns]")))
    if len(trades):
        g = trades.groupby(pd.to_datetime(trades["date"]))["ret"].sum() * weight
        s = s.add(g.reindex(s.index).fillna(0.0), fill_value=0.0)
    return s.to_numpy()


def trade_stats(trades: pd.DataFrame, col: str = "r_net") -> dict:
    """Per-trade statistics in R: count, mean, t, total; the total without the ten symbols with the largest total
    R, without the top 1% of trades, and per calendar year."""
    r = trades[col].to_numpy(dtype=float) if len(trades) else np.array([])
    out = {"trades": int(len(r)), "mean_r": round(float(r.mean()), 4) if len(r) else None,
           "t": round(float(t_stat(r)), 2) if len(r) > 2 else None, "total_r": round(float(r.sum()), 2)}
    if not len(r):
        return out
    by_sym = trades.groupby("symbol")[col].sum().sort_values(ascending=False)
    best = list(by_sym.index[:KEEP_DROP_SYMBOLS])
    rest = trades[~trades["symbol"].isin(best)][col]
    out.update({"best_symbols": best, "total_r_without_best10": round(float(rest.sum()), 2),
                "mean_r_without_best10": round(float(rest.mean()), 4) if len(rest) else None})
    k = max(int(math.ceil(0.01 * len(r))), 1)
    out["total_r_without_top1pct"] = round(float(np.sort(r)[:-k].sum()), 2)
    yr = trades.groupby(pd.to_datetime(trades["date"]).dt.year)[col].sum()
    out["years"] = {str(y): round(float(v), 2) for y, v in yr.items()}
    out["positive_years"] = int((yr > 0).sum())
    out["n_years"] = int(len(yr))
    return out


def orb_verdict(st: Mapping) -> dict:
    """Keep only if mean net R >= +0.05 with t >= 3 AND total net R > 0 without the ten best symbols."""
    m1 = st.get("mean_r") is not None and st["mean_r"] >= KEEP_MIN_R and (st.get("t") or -math.inf) >= KEEP_MIN_T
    m2 = (st.get("total_r_without_best10") or 0.0) > 0
    return {"mark_expectancy": bool(m1), "mark_without_best10": bool(m2),
            "verdict": "keep" if m1 and m2 else "propose deletion"}


def random_entry_percentile(trades: pd.DataFrame, rule_mean: float, runs: int = 1000, seed: int = 112) -> dict:
    """Share (%) of `runs` random-entry portfolios whose mean net R is strictly below the rule's: each run enters
    every one of the rule's symbol-days at a uniformly random decision minute of the grid (`rand_r`)."""
    pools = [np.asarray(x, dtype=float) for x in trades.get("rand_r", pd.Series(dtype=object)) if len(x)]
    if not pools:
        return {"runs": 0, "percentile": None}
    rng = np.random.default_rng(seed)
    means = np.array([np.mean([p[rng.integers(len(p))] for p in pools]) for _ in range(runs)])
    return {"runs": int(runs), "seed": seed, "percentile": round(float((means < rule_mean).mean()) * 100, 1),
            "median_mean_r": round(float(np.median(means)), 4)}


def news_marks(st: Mapping, rnd: Mapping, sharpe_rule: float, sharpe_spy: float) -> list[dict]:
    """The intraday bar of config/gate.yaml (decision 15 section 7) as far as this report can judge it."""
    word = {True: "pass", False: "fail"}
    out = [{"mark": "trades >= 900", "verdict": word[st["trades"] >= GATE["min_trades"]],
            "detail": f"{st['trades']} trades"},
           {"mark": "per-trade net t > 3", "verdict": word[(st.get("t") or -math.inf) > GATE["t_min"]],
            "detail": f"t {st.get('t')}, mean net R {st.get('mean_r')}"}]
    pct = rnd.get("percentile")
    out.append({"mark": "random-entry percentile >= 95 (1,000 runs)",
                "verdict": "cannot be judged (no runs)" if pct is None else word[pct >= GATE["random_pct_min"]],
                "detail": f"percentile {pct} of {rnd.get('runs')} runs"})
    out.append({"mark": "daily Sharpe above SPY's", "verdict": word[sharpe_rule > sharpe_spy],
                "detail": f"rule {sharpe_rule}, SPY {sharpe_spy}"})
    out.append({"mark": "deflated Sharpe > 0.5", "verdict": "not computed here (scripts/trial_report.py)",
                "detail": "the report can fail the rule, never pass it"})
    return out
