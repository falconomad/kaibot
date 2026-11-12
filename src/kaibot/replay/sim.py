"""Portfolio replay with the live risk gate, shadow-cost fills and the PRD v2 exit and halt rules.

Fill model (conservative, research 06): decide at the close of minute m, fill at the OPEN of minute m+1 at the
ask/bid plus impact; stops fill at min(stop, bar open) as a market sell; targets fill only if the bar trades
THROUGH the limit by a cent; time exit at the 15:55 close; if stop and target are both hit in one bar the stop
wins. The agent room is NOT replayed (LLM lookahead), so this is the rules-only strategy.

Costs: model v1 (`shadow_costs`) unless `run_portfolio(costs=get_model("v2"))` (issue #111, research opt-in). Under
v2 every intraday fill is a continuous-session marketable order priced from the stock's 20-session ADV; the
target stays a limit that fills when the bar trades through it by a cent (no fill-probability haircut here: the
exit scan would have to continue past a missed target), and the bars are split-adjusted (audit E4 caveat).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kaibot.broker.costs_v2 import CostModel, FillCtx
from kaibot.broker.shadow_costs import buy_px, cost_parts, half_spread, sell_fees, sell_px  # noqa: F401
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
    # Cost split (issue #109), all in $: pnl == gross_pnl - cost_spread - cost_impact - cost_slippage - cost_fees.
    # Research columns only: they are not part of the event stream or its hash.
    gross_pnl: float = 0.0  # bought at the fill bar's open, sold at the stop / target level or the exit bar's close
    cost_spread: float = 0.0  # half spread paid on the buy and on every market sell
    cost_impact: float = 0.0  # modelled impact on the buy and on every market sell
    cost_slippage: float = 0.0  # stop bar opened below the stop: stop level minus that open
    cost_fees: float = 0.0  # SEC + TAF on the sell
    vix: float = math.nan  # previous day's VIX close (what the regime rule saw)


@dataclass
class Book:
    equity: float
    trades: list[Trade] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)


def adv20(sd: SymbolDays) -> np.ndarray:
    """20-session average dollar volume per row of `sd`, from the sessions before it (NaN with < 15)."""
    dv = pd.Series(np.nansum(sd.c * sd.v, axis=1))
    return dv.rolling(20, min_periods=15).mean().shift(1).to_numpy()


def v2_fill(model: CostModel, side: str, mid: float, qty: float, sigma: float, vol: float,
            adv: float) -> tuple[float, float, float]:
    """(fill price, half spread, impact) per share of a continuous marketable order under a protocol model."""
    f = model.fill(side, FillCtx(float(mid), float(qty), float(sigma), float(vol), float(adv), "continuous"))
    return f.price, f.half, f.impact


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
    return trail_exit_level(h, l, o, c, start_m, fill, risk_ps, atr, target_r, trail_atr)[:3]


def trail_exit_level(h, l, o, c, start_m, fill, risk_ps, atr, target_r=2.0, trail_atr=1.5):  # noqa: E741
    """`first_exit_trail` plus the no-cost exit level (the stop in force, the target, or the close), so the replay
    can separate a gap through the stop from the signal's own result. Same scan, same first three values."""
    stop = fill - risk_ps
    target = fill + target_r * risk_ps
    armed = False
    hi_close = -np.inf
    for m in range(start_m, EXIT_M + 1):
        if l[m] <= stop:
            return m, float(min(stop, o[m])), "trail" if armed else "stop", float(stop)
        if h[m] >= target + 0.01:
            return m, float(target), "target", float(target)
        if m == EXIT_M:
            return m, float(c[m]), "time", float(c[m])
        hi_close = max(hi_close, c[m])
        if not armed and c[m] >= fill + risk_ps:
            armed = True
        if armed:
            stop = max(stop, fill, hi_close - trail_atr * atr)
    return EXIT_M, float(c[EXIT_M]), "time", float(c[EXIT_M])


def run_portfolio(bars: dict[str, SymbolDays], cands: pd.DataFrame, regime: dict, fomc: set, pol: RiskPolicy,
                  equity0: float = 100_000.0, min_reward_r: float = 0.75, atr_mult: float = 1.5,
                  pause_after_losses: int = 3, pause_minutes: int = 60, variant: str = "v1",
                  target_r: float | None = None, size_atr: float | None = None,
                  costs: CostModel | None = None) -> Book:
    """`atr_mult` (stop = atr_mult x 5-minute ATR) and `target_r` (profit target in R; None = the variant's
    pre-registered value, 1.5 for v1 and 2.0 for A) are the walk-forward's tunable exits; defaults are unchanged.
    `size_atr` (experiments only, None = off) keeps the dollar risk of a stop `size_atr` ATRs away: the quantity
    is what the gate allows at that reference stop, scaled by size_atr / atr_mult, and never more than the gate
    allows for the real stop. `costs`: None or v1 = the unchanged v1 path; v2 = `kaibot.broker.costs_v2`."""
    v2 = None if costs is None or costs.name == "v1" else costs
    advs: dict[str, np.ndarray] = {}

    def adv_at(sym: str, r: int) -> float:
        if sym not in advs:
            advs[sym] = adv20(bars[sym])
        return float(advs[sym][r])

    fees_of = sell_fees if v2 is None else v2.sell_fees
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
                    mid = float(sd.c[r, m])
                    if v2 is None:
                        px = sell_px(mid, p["qty"], p["sigma"], sd.v[r, m])
                        x_half, x_imp = cost_parts(mid, px)
                    else:
                        px, x_half, x_imp = v2_fill(v2, "sell", mid, p["qty"], p["sigma"], sd.v[r, m],
                                                    adv_at(p["symbol"], r))
                    fees = fees_of(p["qty"], px)
                    pnl = (px - p["fill"]) * p["qty"] - fees
                    realized += pnl
                    t = p["trade"]
                    e_half, e_imp = p["entry_costs"]
                    book.trades.append(Trade(t.symbol, t.date, t.entry_m, m, t.qty, t.entry, px, t.stop, t.target,
                                             "daily_stop", pnl, t.risk, (mid - p["mid"]) * t.qty,
                                             (e_half + x_half) * t.qty, (e_imp + x_imp) * t.qty, 0.0, fees, t.vix))
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
                qty = v.qty
                if size_atr is not None:  # same $ risk as a stop size_atr ATRs away (never above the gate's qty)
                    v0 = evaluate(Proposal(cnd["symbol"], Side.BUY, ref, ref - size_atr * cnd["atr5"]), snap, pol)
                    if not v0.approved:
                        continue
                    qty = min(qty, math.floor(v0.qty * size_atr / atr_mult))
                qty = qty * (0.5 if vix > 25 else 1.0)
                if qty <= 0:
                    continue
                fm = m + 1
                mid = float(sd.o[r, fm])
                if v2 is None:
                    fill = buy_px(mid, qty, cnd["sigma_1m"], sd.v[r, fm])
                    e_half, e_imp = cost_parts(mid, fill)
                else:
                    adv = adv_at(cnd["symbol"], r)
                    fill, e_half, e_imp = v2_fill(v2, "buy", mid, qty, cnd["sigma_1m"], sd.v[r, fm], adv)
                stop_f = fill - risk_ps
                tgt_f = fill + (target - ref)
                if variant == "A":
                    xm, gross, why, level = trail_exit_level(sd.h[r], sd.l[r], sd.o[r], sd.c[r], fm, fill, risk_ps,
                                                             cnd["atr5"], target_r=tr)
                    stop_f, tgt_f = fill - risk_ps, fill + tr * risk_ps
                else:
                    xm, gross, why = first_exit(sd.h[r], sd.l[r], sd.o[r], sd.c[r], fm, stop_f, tgt_f)
                    level = stop_f if why == "stop" else gross
                if why == "target":  # a target is a limit fill
                    xp, x_half, x_imp = gross, 0.0, 0.0
                elif v2 is None:
                    xp = sell_px(gross, qty, cnd["sigma_1m"], sd.v[r, xm])
                    x_half, x_imp = cost_parts(gross, xp)
                else:
                    xp, x_half, x_imp = v2_fill(v2, "sell", gross, qty, cnd["sigma_1m"], sd.v[r, xm], adv)
                fees = fees_of(qty, xp)
                pnl = (xp - fill) * qty - fees
                t = Trade(cnd["symbol"], d, fm, xm, qty, fill, xp, stop_f, tgt_f, why, pnl, risk_ps * qty,
                          (level - mid) * qty, (e_half + x_half) * qty, (e_imp + x_imp) * qty,
                          (level - gross) * qty, fees, float(vix))
                open_pos.append({"symbol": cnd["symbol"], "qty": qty, "fill": fill, "exit_m": xm, "pnl": pnl,
                                 "risk": risk_ps * qty, "sigma": cnd["sigma_1m"], "trade": t, "mid": mid,
                                 "entry_costs": (e_half, e_imp)})
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
                      atr_mult: float = 1.5, variant: str = "v1") -> np.ndarray:
    """Total net P&L ($) of n_runs random-entry strategies with the same daily trade count, sizing and exits."""
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
                fill = buy_px(sd.o[r, m + 1], qty, sigma[s][r], sd.v[r, m + 1])
                if variant == "A":
                    xm, gross, why = first_exit_trail(sd.h[r], sd.l[r], sd.o[r], sd.c[r], m + 1, fill, risk_ps, a)
                else:
                    xm, gross, why = first_exit(sd.h[r], sd.l[r], sd.o[r], sd.c[r], m + 1, fill - risk_ps,
                                                fill + (target - ref))
                xp = gross if why == "target" else sell_px(gross, qty, sigma[s][r], sd.v[r, xm])
                tot += (xp - fill) * qty - sell_fees(qty, xp)
        totals[k] = tot
    return totals
