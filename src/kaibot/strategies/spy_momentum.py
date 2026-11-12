"""SPY last-30-minute momentum. Gao, Han, Li & Zhou (2018), JFE.

If SPY's return from the prior close to 10:00 ET is above +0.5 sigma (sigma = trailing std of that
same-window return), buy SPY at 15:30 ET and exit market-on-close. Long only, no LLM, at most one trade a day.

RETIRED from every level on 12 Nov 2026 (Product decision on #128: its own ten-year replay is -4.94 bp per trip,
t -5.42, 0 of 10 years positive). It stays registered (strategies/registry.py), so the research command can still
run it on the engine with `SimBroker`, but no level in config/profiles.yaml lists it, so it never trades.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from kaibot.replay.data import SymbolDays
from kaibot.replay.features import five_minute
from kaibot.strategies.base import CloseOut, Context, Event, ExitAction, Holding, Mode, Signal, When

NAME = "spy_late_momentum"

ENTRY_M = 359  # decide at the close of the 15:29 bar (15:30:00), fill at the 15:30 bar's open
EXIT_SUBMIT_M = 375  # 15:45: send the market-on-close sell
MOC_CUTOFF_M = 380  # Alpaca refuses new MOC orders from 15:50 ET; after that the 15:55 flatten takes over
TEN_AM_M = 29  # the 09:59 bar closes at 10:00
LOOKBACK = 60  # sessions for sigma
STOP_ATR = 3.0  # protective stop below entry (5-min ATR), held at the broker until the MOC goes in


@dataclass(frozen=True)
class MomentumInputs:
    ret_close_to_10: float  # fraction
    sigma_close_to_10: float  # trailing std (e.g. 60 sessions) of the same window
    event_blackout: bool
    already_traded_today: bool


def should_buy(x: MomentumInputs, z_threshold: float = 0.5) -> bool:
    if x.already_traded_today or x.event_blackout or x.sigma_close_to_10 <= 0:
        return False
    return x.ret_close_to_10 / x.sigma_close_to_10 > z_threshold


def inputs_from_days(sd: SymbolDays, today: int, lookback: int = LOOKBACK) -> tuple[float, float]:
    """(today's prior-close -> 10:00 return, trailing std of that same return over `lookback` earlier sessions).

    Reads only today's bars up to 10:00 and earlier sessions' closes, so it is point-in-time at 10:00 or later.
    """
    c = np.asarray(sd.c, dtype=float)
    if today < 1:
        return float("nan"), float("nan")
    ret_today = c[today, TEN_AM_M] / c[today - 1, -1] - 1
    lo = max(1, today - lookback)
    past = c[lo:today, TEN_AM_M] / c[lo - 1:today - 1, -1] - 1
    past = past[np.isfinite(past)]
    sigma = float(np.std(past, ddof=1)) if len(past) >= 20 else float("nan")
    return float(ret_today), sigma


class SpyLateMomentum:
    """The rule above as a `Strategy`, moved out of `Engine` unchanged (issue #116). One decision at 15:30 on full
    sessions; a stop 3 ATR(5-minute) below, held at the broker; the exit is a market-on-close sell sent from 15:45
    (the 15:55 flatten closes it if the MOC is refused or Alpaca's 15:50 cutoff has passed)."""

    name = NAME
    mode = Mode.LIVE
    holding = Holding.INTRADAY
    schedule = (When(Event.BAR_CLOSE, minute=ENTRY_M, full_session_only=True),)
    warmup = LOOKBACK + 1
    owner_filters = False  # SPY is not a focus stock; the owner's VIX limit is applied below
    room = False

    def on_event(self, ctx: Context) -> Iterator[Signal]:
        mk, m = ctx.market, ctx.m
        spy = mk.spy
        if ctx.holds(spy):
            return
        sd = mk.symbol_days(spy, m)
        if sd is None or not len(sd.dates) or sd.dates[-1] != np.datetime64(ctx.session, "D"):
            return
        today = len(sd.dates) - 1
        ret, sigma = inputs_from_days(sd, today)
        z = ret / sigma if sigma and np.isfinite(sigma) and sigma > 0 else float("nan")
        atr = five_minute(sd.o[today:], sd.h[today:], sd.l[today:], sd.c[today:])[0][0, (m - 4) // 5]
        ref = float(sd.c[today, m])
        go = should_buy(MomentumInputs(ret, sigma if np.isfinite(sigma) else 0.0, False, False))
        if not (np.isfinite(atr) and atr > 0):
            go = False
        off, vix = mk.regime_at(m)  # S15: the regime gate (incl. VIX > 35 crisis) applies here too
        r = ctx.rules
        if not off and r is not None and np.isfinite(vix) and vix > r.vix_max:
            off = True  # M5: the owner's VIX limit (tighter than the crisis gate)
        stop = ref - STOP_ATR * float(atr if np.isfinite(atr) else 0)
        feats = {"ret_close_to_10": _num(ret), "sigma": _num(sigma), "z": _num(z), "atr5": _num(atr), "m": m}
        yield Signal(spy, ref, stop, None, feats, ["SPY +0.5 sigma by 10:00", "last-30-minute momentum"],
                     "regime off" if off else (None if go else "momentum below threshold"),
                     sigma_1m=float(sigma or 0) / np.sqrt(390), atr=float(atr if np.isfinite(atr) else 0), vix=vix)

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx: Context, pos) -> ExitAction | None:
        return None  # the protective stop stays where it was placed

    def close_out(self, ctx: Context, pos) -> ExitAction | None:
        if EXIT_SUBMIT_M <= ctx.m < MOC_CUTOFF_M:
            return CloseOut("moc", "moc")
        return None


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)
