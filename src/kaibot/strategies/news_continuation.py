"""SHADOW ONLY. News continuation, long side only: buy the first VWAP-holding pullback after fresh company news
with a strong up-move on heavy volume, hold to the 15:55 flatten.

Spec: quant memo (docs/debate/v2_eng_quant.md section 1): "Material 8-K or wire item, price above VWAP, relative
volume >= 3. Enter on the first pullback that holds VWAP. Hold to close." Evidence (research 06): news drops
continue rather than reverse (Chan 2003); LLM-scored headline drift runs from +15 minutes after release to the
close, while returns 1-15 minutes after release are insignificant (ref 19), so news must be >= 15 minutes old.

Pre-registered rule (no parameter tuned on data), evaluated when a 5-minute bar completes (minute m = 5k + 4):
1. News: >= 1 company headline with created_at in [16:00 ET of the prior session, decision_ts - 15 min]
   (point-in-time: decision_ts = close of minute m; the caller supplies the flag from news known at created_at).
2. Same-direction move: return from the prior close to close[m] >= +1.0 x the 20-session daily sigma (long only;
   the direction comes from price, not headline polarity: no LLM may set entries).
3. Volume: cumulative volume since the open / 20-session average at the same minute >= 3.
4. Price above session VWAP at m.
5. Pullback that holds VWAP: this 5-minute bar's low < the previous 5-minute bar's low, its low >= VWAP, and it
   closes above its open.
6. Stop 1.5 x ATR(5-minute, 14 bars) below the entry reference (same as the live dip strategy); no target;
   time exit at 15:55 ET. One signal per symbol per day (the caller keeps the first).
Simplifications: no materiality classification of the headline (any tagged story counts); "material 8-K" is
not separated from wire items; live volume is IEX rescaled to SIP (engine/market.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.strategies.base import Event, ExitAction, Holding, Mode, ShadowContext, When
from kaibot.strategies.shadow_types import Panel, ShadowCandidate, rolling_before, rolling_before_rows

NAME = "news_continuation"


@dataclass(frozen=True)
class NewsParams:
    min_news_age_min: int = 15
    min_move_sigma: float = 1.0
    min_rel_vol: float = 3.0
    atr_mult: float = 1.5
    atr_bars: int = 14
    lookback: int = 20
    first_m: int = 9  # second 5-minute bar (a pullback needs a previous bar)
    last_m: int = 374


DEFAULT = NewsParams()


def prior_stats(sd: SymbolDays, p: NewsParams = DEFAULT) -> dict[str, np.ndarray]:
    """Statistics known before each session (len(days) + 1 rows; the last one is for the next session)."""
    close = sd.c[:, -1]
    r = np.r_[np.nan, close[1:] / close[:-1] - 1]
    cum = np.cumsum(sd.v, axis=1)
    avgcum = rolling_before_rows(cum, p.lookback)
    dvol = rolling_before(r, p.lookback, "std")
    return {"prev_close": np.r_[np.nan, close], "dvol20": dvol, "sigma_1m": dvol / np.sqrt(N_MIN),
            "avgcum20": avgcum}


def _atr5(panel: Panel, k: int, n: int) -> np.ndarray:
    """Mean true range of the last n completed 5-minute bars up to bar k (needs >= 3), minutes 0..5k+4 only."""
    rows, b = len(panel), 5 * (k + 1)
    h5 = np.fmax.reduce(panel.h[:, :b].reshape(rows, k + 1, 5), axis=2)
    l5 = np.fmin.reduce(panel.l[:, :b].reshape(rows, k + 1, 5), axis=2)
    c5 = panel.c[:, 4:b:5]
    prev_c = np.c_[panel.o[:, :1], c5[:, :-1]]
    tr = np.fmax.reduce(np.stack([h5 - l5, np.abs(h5 - prev_c), np.abs(l5 - prev_c)]), axis=0)[:, -n:]
    if tr.shape[1] < 3:
        return np.full(rows, np.nan)
    return np.nanmean(tr, axis=1)


def setup(panel: Panel, stats: dict[str, np.ndarray], m: int, p: NewsParams = DEFAULT):
    """Price/volume part of the rule at minute m (columns 0..m only): (mask, feature arrays)."""
    n = len(panel)
    if (m - 4) % 5 or m < p.first_m or m > p.last_m or not n:
        return np.zeros(n, dtype=bool), {}
    k = (m - 4) // 5
    a, b = 5 * k, m + 1
    o5 = panel.o[:, a]
    l5 = np.fmin.reduce(panel.l[:, a:b], axis=1)
    l5_prev = np.fmin.reduce(panel.l[:, a - 5:a], axis=1)
    c_m = panel.c[:, m]
    v = np.nan_to_num(panel.v[:, :b])
    vwap = (np.nan_to_num(panel.vw[:, :b]) * v).sum(axis=1) / np.maximum(v.sum(axis=1), 1)
    ret = c_m / stats["prev_close"] - 1
    with np.errstate(invalid="ignore", divide="ignore"):
        move = ret / stats["dvol20"]
        rel = v.sum(axis=1) / stats["avgcum20"][:, m]
    mask = ((move >= p.min_move_sigma) & (ret > 0) & (rel >= p.min_rel_vol) & (c_m > vwap)
            & (l5 < l5_prev) & (l5 >= vwap) & (c_m > o5) & np.isfinite(c_m))
    return mask, {"ret_since_prev_close": ret, "move_sigma": move, "rel_vol_cum": rel, "vwap": vwap}


def decide(panel: Panel, stats: dict[str, np.ndarray], m: int, has_news: np.ndarray,
           p: NewsParams = DEFAULT) -> list[ShadowCandidate]:
    """has_news[r]: a headline for row r with created_at in [prior 16:00 ET, decision_ts - 15 min]."""
    mask, f = setup(panel, stats, m, p)
    rows = np.flatnonzero(mask & np.asarray(has_news, dtype=bool))
    if not len(rows):
        return []
    with np.errstate(invalid="ignore"):
        atr = _atr5(panel, (m - 4) // 5, p.atr_bars)
    out = []
    for r in rows:
        if not (np.isfinite(atr[r]) and atr[r] > 0):
            continue
        ref = float(panel.c[r, m])
        out.append(ShadowCandidate(
            NAME, str(panel.symbols[r]), int(r), m, ref, ref - p.atr_mult * float(atr[r]), None, "time_1555",
            ("fresh_company_news", "up_move_on_volume", "pullback_held_vwap"),
            {k: float(x[r]) for k, x in f.items()} | {"atr5": float(atr[r]),
                                                      "sigma_1m": float(stats["sigma_1m"][r])},
        ))
    return out


class NewsContinuation:
    """The rule above as an ordinary `Strategy` in shadow mode (issue #116). The news lookup costs API calls, so
    it is asked only for the rows that pass the price/volume setup and have not signalled today."""

    name = NAME
    mode = Mode.SHADOW
    holding = Holding.INTRADAY
    schedule = (When(Event.BAR_CLOSE, every=5, offset=4),)
    warmup = DEFAULT.lookback + 1
    owner_filters = False
    room = False

    def prior_stats(self, sd: SymbolDays) -> dict[str, np.ndarray]:
        return prior_stats(sd)

    def on_event(self, ctx: ShadowContext) -> list[ShadowCandidate]:
        panel, m = ctx.panel, ctx.m
        mask, _ = setup(panel, ctx.stats, m)
        rows = np.flatnonzero(mask)
        rows = np.array([r for r in rows if (NAME, panel.symbols[r]) not in ctx.seen], dtype=np.int64)
        if not len(rows):
            return []
        has = np.zeros(len(panel), dtype=bool)
        has[rows] = ctx.news_flags(rows, m)
        return decide(panel, ctx.stats, m, has)

    def on_fill(self, fill: float, risk_ps: float) -> tuple[float, float | None]:
        return fill - risk_ps, None

    def manage(self, ctx, pos) -> ExitAction | None:
        return None

    def close_out(self, ctx, pos) -> ExitAction | None:
        return None  # the 15:55 flatten
