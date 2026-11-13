"""SPY noise band, long only, as an engine strategy (issue #202; decision 15 rule 3; docs/prereg/spy_noise_band.md).

RESEARCH ONLY. The rule of the pre-registration, unchanged, as a `Strategy` plug-in, so the go/no-go run of Gate B
comes from the engine with `SimBroker` (decision 15 sections 3 and 7): every decision, order, fill, halt and size
goes through `Engine.tick`, the risk gate and the order path exactly as on paper. It is registered in
`registry.RESEARCH_INTRADAY` only: in no level of config/profiles.yaml, no owner toggle, not in the SQL registry,
and `registry.live` never returns it, so the live engine cannot trade it. The research run (scripts/spy_engine.py)
hands it to one `Engine` built on `SimBroker` + `SimClock`.

The rule (Zarattini, Aziz and Barbon 2024, frozen settings of the prereg section 2): sigma(m) = mean over the
previous 14 sessions of |close(m) / open - 1|; UB(m) = max(today's open, yesterday's close) x (1 + sigma(m));
checks at HH:00 and HH:30 from 10:00 to 15:30; when flat, buy if the 1-minute close is above UB(m); when long, sell
if it is below max(UB(m), session VWAP(m)), otherwise at the close. Decided at the close of minute m, filled at the
next bar's open (the engine's market orders on `SimBroker`); the close exit is a market-on-close order (the
session's last print). The inputs are the harness's own functions (`replay.spy_noise_band`), computed on the
engine's point-in-time view of the market (`LiveMarket.symbol_days(spy, m)`: earlier sessions plus today's bars up
to m), so the rule cannot read a later minute.

What the engine adds and the harness has not (engine behaviour on paper, not choices of this rule):
- size: the risk gate sizes every entry (risk per trade, the level's per-position cap, buying power, whole shares),
  halved when VIX is above 25; the prereg's "whole account in SPY" is not reachable through the gate (the
  per-position cap is at most 20% of equity under `HARD_CEILINGS`), so the account runs at the level's cap and the
  per-trip numbers, not the account's size, are what compares with the harness;
- a protective stop at the broker for every entry (the engine requires one): STOP_FRAC below the entry, far enough
  to be insurance only (it binds the size at the Balanced cap: 0.5% risk / 3% = 16.7% > 15%), placed as an OTO leg;
- the engine's halts: the event blackout (no entry +-30 minutes around a 14:00 FOMC statement), the pause after
  consecutive losses, the daily loss stop, the run drawdown end and the entry window 09:35-15:45.
The report counts what each of them changed against the harness's own round trips.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np

from kaibot.replay import spy_noise_band as nb
from kaibot.replay.data import N_MIN
from kaibot.strategies.base import CloseOut, Context, Event, ExitAction, Holding, Mode, Signal, When

NAME = "spy_noise_band"
STOP_FRAC = 0.03  # protective stop 3% below the entry: insurance only (see the module docstring)
MOC_FROM_BEFORE_CLOSE = 15  # the close exit is sent 15 minutes before the close (15:45; 12:45 on an early close)
MOC_UNTIL_BEFORE_CLOSE = 10  # Alpaca refuses new MOC orders from 15:50 ET; after that the 15:55 flatten closes it


class NoiseBand:
    """One frozen version of the rule (`spec`: the prereg's base rule or one of its five neighbours)."""

    mode = Mode.LIVE
    holding = Holding.INTRADAY
    owner_filters = False  # SPY is not a focus stock; the rule has no owner filter of its own
    room = False

    def __init__(self, spec: nb.Spec = nb.BASE, stop_frac: float = STOP_FRAC):
        if spec.carry:
            raise ValueError("the carry version holds overnight: it is a multi-day rule, not this intraday plug-in")
        self.spec, self.stop_frac = spec, float(stop_frac)
        self.name = spec.name
        self.schedule = (When(Event.BAR_CLOSE, every=spec.check_every, offset=nb.FIRST_CHECK_M % spec.check_every),)
        self.warmup = spec.lookback + 1
        self._cache: dict = {}

    # ---------------------------------------------------------------- point-in-time inputs

    def _session_inputs(self, sd, n_min: int) -> tuple[np.ndarray, float] | None:
        """(sigma row of today, reference price max(today's open, yesterday's close)), once per session: both use
        only earlier sessions and today's first bar."""
        today = len(sd.dates) - 1
        key = (sd.dates[-1], len(sd.dates))
        hit = self._cache.get("session")
        if hit is not None and hit[0] == key:
            return hit[1]
        out = None
        if today >= 1:
            cm = nb.close_minute(sd)
            cm[today] = n_min - 1  # today's close is the calendar's (known in advance), not today's last bar so far
            sig = nb.sigma_table(sd, self.spec.lookback, self.spec.min_obs, cm)[today]
            op = nb.session_open(sd)
            ref = max(float(op[today]), float(sd.c[today - 1, cm[today - 1]]))
            out = (sig, ref) if math.isfinite(ref) else None
        self._cache["session"] = (key, out)
        return out

    def _band(self, ctx: Context):
        """(1-minute close, UB(m), VWAP(m)) at the close of minute m, or None without today's bars."""
        sd = ctx.market.symbol_days(ctx.market.spy, ctx.m)
        if sd is None or not len(sd.dates) or sd.dates[-1] != np.datetime64(ctx.session, "D"):
            return None
        got = self._session_inputs(sd, ctx.n_min)
        if got is None:
            return None
        sig, ref = got
        today, m = len(sd.dates) - 1, ctx.m
        return float(sd.c[today, m]), ref * (1 + float(sig[m])), _vwap(sd, today, m)

    def is_check(self, m: int, n_min: int) -> bool:
        """A decision minute of the rule that can still fill inside the session (prereg choice 3)."""
        return m in self._checks and m + 1 <= n_min - 1

    @property
    def _checks(self) -> frozenset[int]:
        c = self._cache.get("checks")
        if c is None:
            c = self._cache["checks"] = frozenset(self.spec.checks())
        return c

    # ---------------------------------------------------------------- the Strategy protocol

    def on_event(self, ctx: Context) -> Iterator[Signal]:
        if not self.is_check(ctx.m, ctx.n_min) or ctx.holds(ctx.market.spy):
            return  # a check evaluates only the entry when flat; the exit is close_out's (prereg choice 5)
        got = self._band(ctx)
        if got is None:
            return
        px, ub, vwap = got
        if not (math.isfinite(ub) and px > ub):
            return  # sigma missing (fewer than min_obs sessions) or no break: no entry at this check
        feats = {"m": ctx.m, "close": round(px, 4), "ub": round(ub, 4), "vwap": round(vwap, 4),
                 "lookback": self.spec.lookback, "rule": self.spec.name}
        yield Signal(ctx.market.spy, px, px * (1 - self.stop_frac), None, feats,
                     [f"SPY close above the noise band at {nb._clock(ctx.m)}", "intraday momentum"], None,
                     sigma_1m=0.0, atr=0.0)

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None  # the protective stop keeps its distance; no target (exits by rule)

    def manage(self, ctx: Context, pos) -> ExitAction | None:
        return None

    def close_out(self, ctx: Context, pos) -> ExitAction | None:
        m, n = ctx.m, ctx.n_min
        if self.spec.exit_m is not None and n == N_MIN and m == self.spec.exit_m - 1:
            return CloseOut("market", "exit1555")  # neighbour exit1555: sold at the open of the 15:55 bar
        if self.is_check(m, n):
            got = self._band(ctx)
            if got is not None:
                px, ub, vwap = got
                stop = max(ub, vwap) if math.isfinite(ub) else vwap
                if px < stop:
                    return CloseOut("market", "band_vwap")
        if (self.spec.exit_m is None or n != N_MIN) and n - MOC_FROM_BEFORE_CLOSE <= m < n - MOC_UNTIL_BEFORE_CLOSE:
            return CloseOut("moc", "close")
        return None


def _vwap(sd, today: int, m: int) -> float:
    """Session VWAP through minute m (`replay.spy_noise_band.vwap_table` on today's row only)."""
    v = np.nan_to_num(sd.v[today, : m + 1])
    cv = v.sum()
    if cv <= 0:
        return float(sd.c[today, m])
    return float(np.nansum(sd.vw[today, : m + 1] * v) / cv)


def versions() -> dict[str, NoiseBand]:
    """The frozen rule and its five pre-declared neighbours (prereg section 3), by name."""
    out = {nb.BASE.name: NoiseBand(nb.BASE)}
    out.update({sp.name: NoiseBand(sp) for sp in nb.NEIGHBOURS})
    return out

