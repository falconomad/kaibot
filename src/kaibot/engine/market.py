"""Live market state: SIP history (>= 15 min old, cached on disk) + today's IEX 1-minute bars.

Features are computed with the SAME code as the replay (kaibot.replay.features), on a SymbolDays that ends at the
last completed minute, so a live decision at minute m sees exactly what the replay saw at minute m.

Free-plan caveat: today's bars are IEX (a few % of US volume). Prices track SIP closely; volume does not, so
today's volume is rescaled by each symbol's SIP/IEX daily-volume ratio over the prior 20 sessions before the
turnover filter is applied. VWAP is IEX-based.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.replay.data import ET, N_MIN, SymbolDays, from_frame
from kaibot.replay.features import candidates, daily_stats, regime

HISTORY_SESSIONS = 70
EMPTY_NEWS = np.array([], dtype="datetime64[s]")


class LiveMarket:
    def __init__(
        self,
        data,
        symbols: list[str],
        etf_of: dict[str, str],
        spy: str,
        cache_dir: Path,
        vix_loader: Callable[[], pd.Series],
    ):
        self.data = data
        self.symbols = symbols
        self.etf_of = etf_of
        self.spy = spy
        self.all = sorted(set(symbols) | set(etf_of.values()) | {spy})
        self.cache = cache_dir
        self.vix_loader = vix_loader
        self.history: dict[str, pd.DataFrame] = {}
        self._hist_days: dict[str, tuple[pd.DataFrame, int, SymbolDays | None]] = {}
        self._stats: dict[str, tuple] = {}
        self.last_checked, self.last_near = 0, []
        self.min_adv = 50e6  # M5: the owner may only raise the $-volume floor
        self.today_bars: dict[str, pd.DataFrame] = {}
        self.vol_ratio: dict[str, float] = {}
        self.vix: pd.Series = pd.Series(dtype=float)
        self.day: date | None = None
        self.open_utc: datetime | None = None
        self._last_fetch: datetime | None = None

    # ---------- once per session ----------
    def prepare_day(self, day: date, open_utc: datetime, progress=None) -> None:
        self.day, self.open_utc = day, open_utc
        self.today_bars = {s: pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v", "vw"]) for s in self.all}
        self._last_fetch = None
        end = open_utc - timedelta(hours=7)  # previous evening: history never touches today
        start = end - timedelta(days=int(HISTORY_SESSIONS * 1.5))
        self.cache.mkdir(parents=True, exist_ok=True)
        for n, s in enumerate(self.all):
            if progress and n % 5 == 0:
                progress(n, len(self.all))
            f = self.cache / f"{s}.parquet"
            old = pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v", "vw"])
            have_to = old["ts"].max() if len(old) else None
            fetch_from = start if have_to is None or have_to < start else have_to + timedelta(minutes=1)
            if fetch_from < end:
                new = self.data.bars([s], fetch_from, end, feed="sip").get(s)
                if new is not None and len(new):
                    old = pd.concat([old, new], ignore_index=True).drop_duplicates("ts")
            old["ts"] = pd.to_datetime(old["ts"], utc=True)
            old = old[(old["ts"] >= start) & (old["ts"] < end)].sort_values("ts").reset_index(drop=True)
            old.to_parquet(f)
            self.history[s] = old
        self.vol_ratio = self._volume_ratios(end)
        self.vix = self.vix_loader()

    def _volume_ratios(self, end: datetime) -> dict[str, float]:
        start = end - timedelta(days=35)
        sip = self.data.bars(self.all, start, end, feed="sip", timeframe="1Day")
        iex = self.data.bars(self.all, start, end, feed="iex", timeframe="1Day")
        out = {}
        for s in self.all:
            a, b = sip.get(s), iex.get(s)
            if a is None or b is None or not len(a) or not len(b):
                out[s] = 1.0
                continue
            m = a.merge(b, on="ts", suffixes=("_s", "_i")).tail(20)
            vi = m["v_i"].sum()
            out[s] = float(m["v_s"].sum() / vi) if vi > 0 else 1.0
        return out

    # ---------- every minute ----------
    def last_complete_minute(self, now: datetime) -> int:
        return int((now - self.open_utc).total_seconds() // 60) - 1

    def update(self, now: datetime) -> None:
        start = self._last_fetch or self.open_utc
        got = self.data.bars(self.all, start, now, feed="iex")
        for s, df in got.items():
            if df is None or not len(df):
                continue
            cur = self.today_bars.get(s)
            df = df[df["ts"] < now.replace(second=0, microsecond=0)]  # only completed minutes
            self.today_bars[s] = (
                pd.concat([cur, df], ignore_index=True).drop_duplicates("ts", keep="last")
                if cur is not None and len(cur)
                else df.reset_index(drop=True)
            )
        self._last_fetch = now.replace(second=0, microsecond=0) - timedelta(minutes=2)  # re-read late bars

    def symbol_days(self, s: str, upto_m: int) -> SymbolDays | None:
        """History plus today's bars up to minute `upto_m`, as per-session arrays.

        The history does not change during a session, so its arrays are built once (`_history_days`) and only
        today's row is rebuilt on each call: every session is forward-filled on its own, so stacking the two
        gives exactly what one pass over both frames gives (`_symbol_days_one_pass`, kept as the reference
        and used whenever the history is not strictly older than today)."""
        today = self._today_scaled(s)
        hist = self._history_days(s)
        if hist is None or today is None or self.day is None:
            return self._symbol_days_one_pass(s, upto_m)
        td = from_frame(s, today, upto={self.day: upto_m})
        day = np.datetime64(self.day, "D")
        if (len(hist.dates) and hist.dates[-1] >= day) or (len(td.dates) and td.dates[0] < day):
            return self._symbol_days_one_pass(s, upto_m)
        return SymbolDays(s, np.concatenate([hist.dates, td.dates]),
                          *(np.vstack([getattr(hist, k), getattr(td, k)]) for k in ("o", "h", "l", "c", "v", "vw")))

    def _today_scaled(self, s: str) -> pd.DataFrame | None:
        today = self.today_bars.get(s)
        if today is None or not len(today):
            return None
        today = today.copy()
        today["v"] = today["v"].astype(float) * self.vol_ratio.get(s, 1.0)
        return today

    def _history_days(self, s: str) -> SymbolDays | None:
        """The stored history as arrays, cached until `history[s]` is replaced (once per session)."""
        f = self.history.get(s)
        if f is None or not len(f):
            return None
        hit = self._hist_days.get(s)
        if hit is None or hit[0] is not f or hit[1] != len(f):
            hit = (f, len(f), from_frame(s, f))
            self._hist_days[s] = hit
        return hit[2]

    def _session_stats(self, s: str, sd: SymbolDays, ed: SymbolDays):
        """`daily_stats(sd, ed)` for this session, computed once per symbol. Each statistic is shifted by one
        session (point-in-time), so today's bars cannot change it. Reused only while `sd` and `ed` are exactly
        this session's cached history plus today; otherwise None and `candidates` computes it itself."""
        hs, he = self._history_days(s), self._history_days(self.etf_of[s])
        today = np.datetime64(self.day, "D") if self.day is not None else None
        if hs is None or he is None or today is None or not (
                len(sd.dates) == len(hs.dates) + 1 and len(ed.dates) == len(he.dates) + 1
                and sd.dates[-1] == today and ed.dates[-1] == today):
            return None
        hit = self._stats.get(s)
        if hit is None or hit[0] is not hs or hit[1] is not he or hit[2] != self.day:
            hit = (hs, he, self.day, daily_stats(sd, ed))
            self._stats[s] = hit
        return hit[3]

    def _symbol_days_one_pass(self, s: str, upto_m: int) -> SymbolDays | None:
        frames = [f for f in (self.history.get(s), self._today_scaled(s)) if f is not None and len(f)]
        if not frames:
            return None
        return from_frame(s, pd.concat(frames, ignore_index=True), upto={self.day: upto_m})

    def has_today(self, sd: SymbolDays | None) -> int | None:
        if sd is None or not len(sd.dates) or sd.dates[-1] != np.datetime64(self.day, "D"):
            return None
        return len(sd.dates) - 1

    def candidates_at(self, m: int) -> pd.DataFrame:
        """Dip-reversal candidates whose 5-minute bar completed at minute m (news is checked separately, live).

        Also records, for the dashboard only, how many symbols were checked (`last_checked`) and the near-misses
        (`last_near`): a 5-minute higher low formed, but the dip was shallower, more sector-driven or on heavier
        volume than the rules allow (within NEAR). The strict set is exactly what candidates() returns with its
        defaults: the near pass is a superset filtered back down, so trading is unchanged."""
        out, near, checked = [], [], 0
        etf_days = {e: self.symbol_days(e, m) for e in set(self.etf_of.values())}
        for s in self.symbols:
            sd, ed = self.symbol_days(s, m), etf_days.get(self.etf_of[s])
            if self.has_today(sd) is None or self.has_today(ed) is None:
                continue
            checked += 1
            c = candidates(sd, ed, EMPTY_NEWS, min_adv=max(self.min_adv, 50e6), stats=self._session_stats(s, sd, ed),
                           **NEAR)
            if len(c):
                c = c[(c["date"] == np.datetime64(self.day, "D")) & (c["m"] == m)]
            if not len(c):
                continue
            strict = (c["z"] <= STRICT["z_max"]) & (c["resid_z"].abs() <= STRICT["resid_abs_max"]) & (
                c["turnover"] <= STRICT["turnover_max"])
            if strict.any():
                out.append(c[strict])
            for _, r in c[~strict].iterrows():
                near.append({"symbol": s, "m": m, "z": round(float(r["z"]), 2), "why": _near_why(r)})
        self.last_checked, self.last_near = checked, near
        return pd.concat(out, ignore_index=True).sort_values("z") if out else pd.DataFrame()

    def regime_at(self, m: int) -> tuple[bool, float]:
        sd = self.symbol_days(self.spy, m)
        if self.has_today(sd) is None:
            return True, float("nan")  # no SPY data: treat as OFF
        off, vix = regime(sd, self.vix)[np.datetime64(self.day, "D")]
        return bool(off[m]), float(vix)

    def last_close(self, s: str) -> float | None:
        df = self.today_bars.get(s)
        return float(df["c"].iloc[-1]) if df is not None and len(df) else None

    def last_bar_minute(self, s: str) -> int | None:
        df = self.today_bars.get(s)
        if df is None or not len(df):
            return None
        return int((df["ts"].iloc[-1] - self.open_utc).total_seconds() // 60)

    def bar_volume(self, s: str) -> float:
        df = self.today_bars.get(s)
        return float(df["v"].iloc[-1]) * self.vol_ratio.get(s, 1.0) if df is not None and len(df) else 1.0

    def spy_return(self) -> float | None:
        df = self.today_bars.get(self.spy)
        h = self.history.get(self.spy)
        if df is None or not len(df) or h is None or not len(h):
            return None
        return float(df["c"].iloc[-1] / h["c"].iloc[-1] - 1)


def session_minutes(cal: dict, day: date) -> tuple[datetime, int]:
    """(open as UTC datetime, number of regular-session minutes) from an Alpaca calendar row."""
    o = pd.Timestamp(f"{day} {cal['open']}", tz=ET)
    c = pd.Timestamp(f"{day} {cal['close']}", tz=ET)
    return o.tz_convert(UTC).to_pydatetime(), min(int((c - o).total_seconds() // 60), N_MIN)


# Strict = the strategy's rules (candidates() defaults). NEAR only widens what is reported as a near-miss.
STRICT = {"z_max": -1.5, "resid_abs_max": 1.5, "turnover_max": 1.5}
NEAR = {"z_max": -1.0, "resid_abs_max": 2.0, "turnover_max": 2.0}


def _near_why(r) -> str:
    why = []
    if r["z"] > STRICT["z_max"]:
        why.append(f"dip only {r['z']:.2f} sigma (needs <= {STRICT['z_max']})")
    if abs(r["resid_z"]) > STRICT["resid_abs_max"]:
        why.append("move explained by its sector")
    if r["turnover"] > STRICT["turnover_max"]:
        why.append("volume too heavy")
    return "; ".join(why)

