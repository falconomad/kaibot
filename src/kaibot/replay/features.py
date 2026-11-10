"""Point-in-time features for the no-news dip reversal, computed per symbol-session.

Every daily statistic uses ONLY prior sessions (shifted by one day). Every intraday value at minute m uses
bars 0..m only. A candidate is evaluated when a 5-minute bar completes (minute m = 5k + 4) and can only be
filled at the open of minute m + 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from kaibot.replay.data import ET, N_MIN, SymbolDays


def _align(a: SymbolDays, b: SymbolDays) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    common, ia, ib = np.intersect1d(a.dates, b.dates, return_indices=True)
    return common, ia, ib


def _rolling_prior(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over the n values strictly BEFORE each index (NaN until n are available)."""
    s = pd.Series(x)
    return fn(s.shift(1).rolling(n, min_periods=n)).to_numpy()


def daily_stats(sd: SymbolDays, etf: SymbolDays):
    dates, i, j = _align(sd, etf)
    close = sd.c[i, -1]
    eclose = etf.c[j, -1]
    r = np.r_[np.nan, close[1:] / close[:-1] - 1]
    re = np.r_[np.nan, eclose[1:] / eclose[:-1] - 1]
    dvol20 = _rolling_prior(r, 20, lambda w: w.std())
    adv20 = _rolling_prior((sd.c[i] * sd.v[i]).sum(axis=1), 20, lambda w: w.mean())
    sr, se = pd.Series(r), pd.Series(re)
    cov = sr.shift(1).rolling(60, min_periods=40).cov(se.shift(1))
    var = se.shift(1).rolling(60, min_periods=40).var()
    beta = (cov / var).to_numpy()
    resid = r - np.nan_to_num(beta) * re
    residvol20 = _rolling_prior(resid, 20, lambda w: w.std())
    cum = np.cumsum(sd.v[i], axis=1)
    avgcum = pd.DataFrame(cum).shift(1).rolling(20, min_periods=20).mean().to_numpy()
    return dates, i, j, dvol20, adv20, beta, residvol20, avgcum


def five_minute(o, h, l, c):  # noqa: E741
    k = N_MIN // 5
    o5 = o[:, 0::5][:, :k]
    h5 = h.reshape(len(h), k, 5).max(axis=2)
    l5 = l.reshape(len(l), k, 5).min(axis=2)
    c5 = c[:, 4::5][:, :k]
    prev_c = np.c_[o5[:, :1], c5[:, :-1]]
    tr = np.maximum.reduce([h5 - l5, np.abs(h5 - prev_c), np.abs(l5 - prev_c)])
    atr = pd.DataFrame(tr.T).rolling(14, min_periods=3).mean().to_numpy().T
    run_min_prev = np.minimum.accumulate(l5, axis=1)
    higher_low = np.zeros_like(l5, dtype=bool)
    higher_low[:, 1:] = (l5[:, 1:] > l5[:, :-1]) & (c5[:, 1:] > o5[:, 1:]) & (l5[:, :-1] <= run_min_prev[:, :-1])
    return atr, higher_low


def candidates(sd: SymbolDays, etf: SymbolDays, news_times: np.ndarray, z_max=-1.5, resid_abs_max=1.5,
               turnover_max=1.5, min_adv=50e6, first_m=5, last_m=375) -> pd.DataFrame:
    dates, i, j, dvol20, adv20, beta, residvol20, avgcum = daily_stats(sd, etf)
    o, h, l, c, v, vw = (getattr(sd, k)[i] for k in ("o", "h", "l", "c", "v", "vw"))  # noqa: E741
    eo, ec = etf.o[j], etf.c[j]
    t_frac = np.sqrt(np.arange(1, N_MIN + 1) / N_MIN)[None, :]
    ret = c / o[:, :1] - 1
    eret = ec / eo[:, :1] - 1
    z = ret / (dvol20[:, None] * t_frac)
    rz = (ret - np.nan_to_num(beta)[:, None] * eret) / (residvol20[:, None] * t_frac)
    turnover = np.cumsum(v, axis=1) / avgcum
    vwap = np.cumsum(vw * v, axis=1) / np.maximum(np.cumsum(v, axis=1), 1)
    atr5, hl5 = five_minute(o, h, l, c)
    ms = np.arange(4, N_MIN, 5)  # minute at which 5m bar k completes
    k_idx = np.arange(len(ms))
    keep = (ms >= first_m) & (ms <= last_m)
    ms, k_idx = ms[keep], k_idx[keep]
    zz, rr, tt = z[:, ms], rz[:, ms], turnover[:, ms]
    mask = (hl5[:, k_idx] & (zz <= z_max) & (np.abs(rr) <= resid_abs_max) & (tt <= turnover_max)
            & (adv20[:, None] > min_adv) & np.isfinite(atr5[:, k_idx]))
    di, ki = np.nonzero(mask)
    if len(di) == 0:
        return pd.DataFrame()
    m = ms[ki]
    local = pd.to_datetime(dates[di]) + pd.to_timedelta(9 * 60 + 30 + m + 1, unit="m")
    decision_utc = local.tz_localize(ET).tz_convert("UTC").tz_localize(None).to_numpy().astype("datetime64[s]")
    lo = np.searchsorted(news_times, decision_utc - np.timedelta64(24, "h"), side="left")
    hi = np.searchsorted(news_times, decision_utc, side="right")
    no_news = (hi - lo) == 0
    df = pd.DataFrame({
        "symbol": sd.symbol, "date": dates[di], "m": m, "price": c[di, m], "atr5": atr5[di, k_idx[ki]],
        "vwap": vwap[di, m], "z": zz[di, ki], "resid_z": rr[di, ki], "turnover": tt[di, ki],
        "sigma_1m": dvol20[di] / np.sqrt(N_MIN), "news_24h": ~no_news,
    })
    return df


def regime(spy: SymbolDays, vix_prev_close: pd.Series):
    """Per (date, minute): strategy OFF when SPY < VWAP and its first-30-min return < -1%, or VIX > 35."""
    vwap = np.cumsum(spy.vw * spy.v, axis=1) / np.maximum(np.cumsum(spy.v, axis=1), 1)
    first30 = spy.c[:, min(29, N_MIN - 1)] / spy.o[:, 0] - 1
    ret_now = spy.c / spy.o[:, :1] - 1
    m_idx = np.arange(N_MIN)[None, :]
    f30 = np.where(m_idx >= 29, first30[:, None], ret_now)  # before 10:00 only what is known so far
    off = (spy.c < vwap) & (f30 < -0.01)
    vix = vix_prev_close.reindex(pd.to_datetime(spy.dates)).ffill().to_numpy()
    crisis = vix > 35
    return {d: (off[n] | crisis[n], vix[n]) for n, d in enumerate(spy.dates)}
