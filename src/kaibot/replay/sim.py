"""Portfolio replay with the live risk gate, shadow-cost fills and the PRD v2 exit and halt rules.

Fill model (conservative, research 06): decide at the close of minute m, fill at the OPEN of minute m+1 at the
ask/bid plus impact; stops fill at min(stop, bar open) as a market sell; targets fill only if the bar trades
THROUGH the limit by a cent; time exit at the 15:55 close; if stop and target are both hit in one bar the stop
wins. The agent room is NOT replayed (LLM lookahead), so this is the rules-only strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import QuoteCtx, sell_fees, shadow_fill_price
from kaibot.replay.data import SymbolDays
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate

EXIT_M = 385  # 15:55 ET bar
BLACKOUT_FOMC = (240, 300)  # 13:30-14:30 ET around the 14:00 statement


@dataclass
class Trade:
    symbol: str
    date: np.datetime64
    entry_m: int
    exit_m: int
    qty: float
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    pnl: float  # $ after shadow costs and fees
    risk: float  # $ at risk at entry (1R)


@dataclass
class Book:
    equity: float
    trades: list[Trade] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)


def half_spread(price: float) -> float:
    return max(price * 0.0001, 0.005)  # >= 1 bp and >= half a cent (large caps)


def buy_px(o: float, q: float, sigma: float, vol: float) -> float:
    hs = half_spread(o)
    return shadow_fill_price("buy", q, QuoteCtx(o - hs, o + hs, sigma, max(vol, 1.0)))


def sell_px(p: float, q: float, sigma: float, vol: float) -> float:
    hs = half_spread(p)
    return shadow_fill_price("sell", q, QuoteCtx(p - hs, p + hs, sigma, max(vol, 1.0)))


def first_exit(h, l, o, c, start_m, stop, target):  # noqa: E741
    """Return (exit_m, gross_price, reason) scanning bars start_m..EXIT_M."""
    seg = slice(start_m, EXIT_M + 1)
    hit_stop = l[seg] <= stop
    hit_tgt = h[seg] >= target + 0.01
    i_s = int(np.argmax(hit_stop)) if hit_stop.any() else 10**9
    i_t = int(np.argmax(hit_tgt)) if hit_tgt.any() else 10**9
    if i_s == 10**9 and i_t == 10**9:
        return EXIT_M, float(c[EXIT_M]), "time"
    if i_s <= i_t:
        m = start_m + i_s
        return m, float(min(stop, o[m])), "stop"
    m = start_m + i_t
    return m, float(target), "target"


def first_exit_trail(h, l, o, c, start_m, fill, risk_ps, atr, target_r=2.0, trail_atr=1.5):  # noqa: E741
    """Variant A: fixed 2R target; after a bar CLOSES >= +1R, stop -> breakeven, then trails trail_atr*ATR below
    the highest close. The stop used on bar m only depends on closes up to m-1 (no lookahead)."""
    stop = fill - risk_ps
    target = fill + target_r * risk_ps
    armed = False
    hi_close = -np.inf
    for m in range(start_m, EXIT_M + 1):
        if l[m] <= stop:
            return m, float(min(stop, o[m])), "trail" if armed else "stop"
        if h[m] >= target + 0.01:
            return m, float(target), "target"
        if m == EXIT_M:
            return m, float(c[m]), "time"
        hi_close = max(hi_close, c[m])
        if not armed and c[m] >= fill + risk_ps:
            armed = True
        if armed:
            stop = max(stop, fill, hi_close - trail_atr * atr)
    return EXIT_M, float(c[EXIT_M]), "time"


def run_portfolio(bars: dict[str, SymbolDays], cands: pd.DataFrame, regime: dict, fomc: set, pol: RiskPolicy,
                  equity0: float = 100_000.0, min_reward_r: float = 0.75, atr_mult: float = 1.5,
                  pause_after_losses: int = 3, pause_minutes: int = 60, variant: str = "v1",
                  target_r: float | None = None, fills=None) -> Book:
    """`atr_mult` (stop = atr_mult x 5-minute ATR) and `target_r` (profit target in R; None = the variant's
    pre-registered value, 1.5 for v1 and 2.0 for A) are the walk-forward's tunable exits; defaults are unchanged.
    `fills` (a replay.fills.FillModel) switches on the stricter fill assumptions of issue #75; None (the default)
    is the fill model every earlier replay used, unchanged."""
    buy = buy_px if fills is None else fills.buy
    sell = sell_px if fills is None else fills.sell
    tr = target_r if target_r is not None else (2.0 if variant == "A" else 1.5)
    book = Book(equity0)
    row_of = {s: {d: n for n, d in enumerate(sd.dates)} for s, sd in bars.items()}
    by_day = {d: g.sort_values(["m", "z"]) for d, g in cands.groupby("date")} if len(cands) else {}
    peak = equity0
    for d in sorted(regime):
        day_c = by_day.get(d)
        start_eq = book.equity
        realized = 0.0
        open_pos: list[dict] = []
        entries = 0
        losses_in_row = 0
        pause_until = -1
        halted = False
        cand_by_m = {} if day_c is None else {m: g for m, g in day_c.groupby("m")}
        off, vix = regime[d]
        for m in range(0, EXIT_M + 1):
            # 1) exits scheduled for this minute
            still = []
            for p in open_pos:
                if p["exit_m"] == m:
                    realized += p["pnl"]
                    book.trades.append(p["trade"])
                    losses_in_row = losses_in_row + 1 if p["pnl"] < 0 else 0
                    if losses_in_row >= pause_after_losses:
                        pause_until, losses_in_row = m + pause_minutes, 0
                else:
                    still.append(p)
            open_pos = still
            # 2) mark-to-market and daily stop (flatten at this bar's close, halt for the day)
            unreal = 0.0
            for p in open_pos:
                sd, r = bars[p["symbol"]], row_of[p["symbol"]][d]
                unreal += (sd.c[r, m] - p["fill"]) * p["qty"]
            if not halted and realized + unreal <= -start_eq * pol.max_daily_loss_pct / 100:
                for p in open_pos:
                    sd, r = bars[p["symbol"]], row_of[p["symbol"]][d]
                    px = sell(sd.c[r, m], p["qty"], p["sigma"], sd.v[r, m])
                    pnl = (px - p["fill"]) * p["qty"] - sell_fees(p["qty"], px)
                    realized += pnl
                    t = p["trade"]
                    book.trades.append(Trade(t.symbol, t.date, t.entry_m, m, t.qty, t.entry, px, t.stop, t.target,
                                             "daily_stop", pnl, t.risk))
                open_pos, halted = [], True
            # 3) new decisions at the close of minute m (filled at m+1)
            g = cand_by_m.get(m)
            if g is None or halted or off[m] or m < pause_until or m + 1 > 375:
                continue
            if d in fomc and BLACKOUT_FOMC[0] <= m <= BLACKOUT_FOMC[1]:
                continue
            for _, cnd in g.iterrows():
                if cnd["news_24h"] or any(p["symbol"] == cnd["symbol"] for p in open_pos):
                    continue
                sd, r = bars[cnd["symbol"]], row_of[cnd["symbol"]][d]
                ref = cnd["price"]
                stop = ref - atr_mult * cnd["atr5"]
                risk_ps = ref - stop
                vw = cnd["vwap"]
                target = min(vw, ref + tr * risk_ps) if vw > ref else ref + tr * risk_ps
                if variant == "v1" and target - ref < min_reward_r * risk_ps:
                    continue
                open_risk = sum(p["risk"] for p in open_pos)
                eq_now = start_eq + realized + unreal
                snap = Snapshot(equity=eq_now, day_start_equity=start_eq, open_positions=len(open_pos),
                                symbol_exposure=0.0, open_risk=open_risk, quote_age_s=0.0, kill_switch=False,
                                reconciled=True, market_open=True, entries_today=entries, run_peak_equity=peak)
                v = evaluate(Proposal(cnd["symbol"], Side.BUY, ref, stop), snap, pol)
                if not v.approved:
                    continue
                qty = v.qty * (0.5 if vix > 25 else 1.0)
                if qty <= 0:
                    continue
                fm = m + 1
                if fills is not None:  # the bar after the signal bar: its open and volume only
                    qty = fills.entry_qty(qty, sd.v[r, fm])
                    if qty <= 0:
                        continue
                fill = buy(sd.o[r, fm], qty, cnd["sigma_1m"], sd.v[r, fm])
                stop_f = fill - risk_ps
                tgt_f = fill + (target - ref)
                if variant == "A":
                    xm, gross, why = first_exit_trail(sd.h[r], sd.l[r], sd.o[r], sd.c[r], fm, fill, risk_ps,
                                                      cnd["atr5"], target_r=tr)
                    stop_f, tgt_f = fill - risk_ps, fill + tr * risk_ps
                else:
                    xm, gross, why = first_exit(sd.h[r], sd.l[r], sd.o[r], sd.c[r], fm, stop_f, tgt_f)
                xp = gross if why == "target" else sell(gross, qty, cnd["sigma_1m"], sd.v[r, xm])
                pnl = (xp - fill) * qty - sell_fees(qty, xp)
                t = Trade(cnd["symbol"], d, fm, xm, qty, fill, xp, stop_f, tgt_f, why, pnl, risk_ps * qty)
                open_pos.append({"symbol": cnd["symbol"], "qty": qty, "fill": fill, "exit_m": xm, "pnl": pnl,
                                 "risk": risk_ps * qty, "sigma": cnd["sigma_1m"], "trade": t})
                entries += 1
        for p in open_pos:  # safety: anything left closes at the time exit
            realized += p["pnl"]
            book.trades.append(p["trade"])
        book.equity = start_eq + realized
        peak = max(peak, book.equity)
        book.daily.append({"date": d, "start": start_eq, "end": book.equity, "pnl": realized,
                           "trades": sum(1 for t in book.trades if t.date == d), "halted": halted})
    return book


def random_entry_runs(bars: dict[str, SymbolDays], atr5: dict, vwap: dict, sigma: dict, trades_per_day: pd.Series,
                      pol: RiskPolicy, equity0: float, n_runs: int = 200, seed: int = 7,
                      atr_mult: float = 1.5, variant: str = "v1", fills=None) -> np.ndarray:
    """Total net P&L ($) of n_runs random-entry strategies with the same daily trade count, sizing and exits.
    `fills`: the same optional FillModel as run_portfolio, so the baseline pays the same costs as the strategy."""
    buy = buy_px if fills is None else fills.buy
    sell = sell_px if fills is None else fills.sell
    rng = np.random.default_rng(seed)
    syms = list(bars)
    row_of = {s: {d: n for n, d in enumerate(sd.dates)} for s, sd in bars.items()}
    totals = np.zeros(n_runs)
    for k in range(n_runs):
        tot = 0.0
        for d, n in trades_per_day.items():
            for _ in range(int(n)):
                for _attempt in range(20):
                    s = syms[rng.integers(len(syms))]
                    r = row_of[s].get(d)
                    if r is None:
                        continue
                    m = int(rng.integers(5, 375))
                    a = atr5[s][r, (m - 4) // 5] if m >= 4 else np.nan
                    if np.isfinite(a) and a > 0 and np.isfinite(sigma[s][r]):
                        break
                else:
                    continue
                sd = bars[s]
                ref = sd.c[r, m]
                risk_ps = atr_mult * a
                vw = vwap[s][r, m]
                target = min(vw, ref + 1.5 * risk_ps) if vw > ref else ref + 1.5 * risk_ps
                qty = np.floor(min(equity0 * pol.risk_per_trade_pct / 100 / risk_ps,
                                   equity0 * pol.max_position_pct / 100 / ref))
                if qty <= 0:
                    continue
                if fills is not None:
                    qty = fills.entry_qty(qty, sd.v[r, m + 1])
                    if qty <= 0:
                        continue
                fill = buy(sd.o[r, m + 1], qty, sigma[s][r], sd.v[r, m + 1])
                if variant == "A":
                    xm, gross, why = first_exit_trail(sd.h[r], sd.l[r], sd.o[r], sd.c[r], m + 1, fill, risk_ps, a)
                else:
                    xm, gross, why = first_exit(sd.h[r], sd.l[r], sd.o[r], sd.c[r], m + 1, fill - risk_ps,
                                                fill + (target - ref))
                xp = gross if why == "target" else sell(gross, qty, sigma[s][r], sd.v[r, xm])
                tot += (xp - fill) * qty - sell_fees(qty, xp)
        totals[k] = tot
    return totals
