"""Replay of SPY last-30-minute momentum (story S13), same rules as the live engine.

Each session: at 10:00 compute z = (prior close -> 10:00 return) / its 60-session std; if z > 0.5 buy SPY at the
15:30 bar's open (shadow costs), stop 3 x 5-min ATR below the fill, exit at the closing print (MOC: half-spread
and fees, no impact model beyond the half-spread). Sizing via the live risk gate on the running equity.
"""

from __future__ import annotations

import numpy as np

from kaibot.broker.shadow_costs import sell_fees
from kaibot.replay.data import SymbolDays
from kaibot.replay.features import five_minute
from kaibot.replay.sim import buy_px, half_spread
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate
from kaibot.strategies import spy_momentum as sm


def run(spy: SymbolDays, start: np.datetime64, end: np.datetime64, pol: RiskPolicy, equity0: float = 100_000.0,
        z_threshold: float = 0.5, blackout_days: set | None = None) -> tuple[list[dict], list[dict]]:
    """-> (trades, daily rows). blackout_days: sessions where 15:00-16:00 is blacked out (none by default)."""
    atr, _ = five_minute(spy.o, spy.h, spy.l, spy.c)
    k = (sm.ENTRY_M - 4) // 5
    eq, trades, daily = equity0, [], []
    for i, d in enumerate(spy.dates):
        if d < start or d > end:
            continue
        start_eq = eq
        ret, sigma = sm.inputs_from_days(spy, i)
        a = atr[i, k]
        took = None
        if (np.isfinite(sigma) and sigma > 0 and np.isfinite(a) and a > 0 and ret / sigma > z_threshold
                and not (blackout_days and d in blackout_days)):
            ref = float(spy.c[i, sm.ENTRY_M])
            stop = ref - sm.STOP_ATR * float(a)
            snap = Snapshot(equity=eq, day_start_equity=eq, open_positions=0, symbol_exposure=0.0, open_risk=0.0,
                            quote_age_s=0.0, kill_switch=False, reconciled=True, market_open=True,
                            buying_power=eq)
            v = evaluate(Proposal("SPY", Side.BUY, ref, stop), snap, pol)
            if v.approved and v.qty > 0:
                q = float(v.qty)
                fm = sm.ENTRY_M + 1
                fill = buy_px(float(spy.o[i, fm]), q, float(sigma) / np.sqrt(390), float(spy.v[i, fm]))
                lo = float(np.min(spy.l[i, fm:]))
                stop_f = fill - (ref - stop)
                if lo <= stop_f:  # the protective stop fired before the close
                    mm = fm + int(np.argmax(spy.l[i, fm:] <= stop_f))
                    xp, why = min(stop_f, float(spy.o[i, mm])) - half_spread(stop_f), "stop"
                else:
                    close = float(spy.c[i, -1])
                    xp, why = close - half_spread(close), "moc"
                pnl = (xp - fill) * q - sell_fees(q, xp)
                eq += pnl
                took = {"date": d, "qty": q, "entry": fill, "exit": xp, "reason": why, "pnl": pnl, "z": ret / sigma,
                        "risk": (ref - stop) * q}
                trades.append(took)
        daily.append({"date": d, "start": start_eq, "end": eq, "pnl": eq - start_eq, "trades": int(took is not None),
                      "halted": False})
    return trades, daily
