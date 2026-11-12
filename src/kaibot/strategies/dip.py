"""No-news intraday dip reversal, Variant A: the rule the paper engine has traded since M1 (decision 15: retired,
kept as the plumbing soak test until a replacement passes). Moved out of `Engine` unchanged (issue #116).

Every completed 5-minute bar from 09:35 to 15:45 (and only while the regime is on):
- candidates: the market's point-in-time dip scan (`engine/market.LiveMarket.candidates_at`, which wraps
  `replay/features.candidates`): a sector-adjusted down move, a 5-minute higher low, normal turnover;
- the owner's focus (Only / Prefer) filters and ranks them;
- veto: any company headline in the 24 hours before the decision;
- stop 1.5 x ATR(5-minute) below the reference price, target 2R; both re-anchored on the fill;
- trailing stop: once the close is 1R above the fill, the stop follows the highest close minus 1.5 ATR, never
  below the fill.
The engine applies the owner's filters, the extra rules veto and the agent room to these signals
(`owner_filters`, `room`), gates and sizes them and sends the bracket. Nothing here touches an order.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import numpy as np

from kaibot.strategies.base import Context, Event, ExitAction, Holding, Mode, MoveStop, Signal, When, note_scan

NAME = "dip_reversal"
ATR_MULT = 1.5
TARGET_R = 2.0
TRAIL_ATR = 1.5
REASONS = ("no-news dip", "5-min higher low", "sector-adjusted")


class DipReversal:
    name = NAME
    mode = Mode.LIVE
    holding = Holding.INTRADAY
    schedule = (When(Event.BAR_CLOSE, every=5, offset=4),)  # each completed 5-minute bar
    warmup = 20  # sessions: the 20-day turnover and sigma statistics
    owner_filters = True
    room = True

    def on_event(self, ctx: Context) -> Iterator[Signal]:
        """Signals one at a time: the engine decides each before the next is computed, so a position opened by
        an earlier signal in this scan is seen by `ctx.holds`, and no news is read for candidates after the
        day's entry limit is reached."""
        m, sc, mk = ctx.m, ctx.scan, ctx.market
        off, vix = mk.regime_at(m)
        if off:
            if (m + 1) % 5 == 0:
                sc["regime_off"] += 1
            return
        cands = mk.candidates_at(m)
        note_scan(sc, m, getattr(mk, "last_checked", 0), getattr(mk, "last_near", []), len(cands))
        if not len(cands):
            return
        r = ctx.rules
        if r is not None and r.mode != "all":  # M5 focus: Only filters; Prefer ranks focus names first
            cands = cands[[r.tradable(x) for x in cands["symbol"]]]
            if not len(cands):
                return
            cands = cands.assign(_rank=[r.rank(x) for x in cands["symbol"]]).sort_values(
                ["_rank", "z"], kind="stable").drop(columns="_rank")
        ctx.prefetch(list(cands["symbol"]))
        decision = ctx.now()
        for _, c in cands.iterrows():
            s = c["symbol"]
            if ctx.holds(s):
                continue
            news = mk.data.news_count(s, decision - timedelta(hours=24), decision)
            ref = float(c["price"])
            stop = ref - ATR_MULT * float(c["atr5"])
            target = ref + TARGET_R * (ref - stop)
            feats = {k: _num(c[k]) for k in ("z", "resid_z", "turnover", "atr5", "vwap", "sigma_1m")}
            feats.update({"news_24h": news, "vix": vix, "m": m})
            yield Signal(s, ref, stop, target, feats, list(REASONS), "company news in 24 h" if news else None,
                         sigma_1m=float(c["sigma_1m"]), atr=float(c["atr5"]), vix=vix, room=c)

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, fill + TARGET_R * risk_ps

    def manage(self, ctx: Context, pos) -> ExitAction | None:
        """The trailing stop (updates the position's own trail state: highest close, armed)."""
        c = ctx.market.last_close(pos.symbol)
        if c is None:
            return None
        pos.hi_close = max(pos.hi_close, c)
        if not pos.armed and c >= pos.fill + pos.risk_ps:
            pos.armed = True
        if pos.armed:
            new = max(pos.stop, pos.fill, pos.hi_close - TRAIL_ATR * pos.atr)
            if new >= pos.stop + 0.01:
                return MoveStop(new)
        return None

    def close_out(self, ctx: Context, pos) -> ExitAction | None:
        return None  # the bracket's stop and target, or the 15:55 flatten


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)
