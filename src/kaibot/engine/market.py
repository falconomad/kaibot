"""Live market state: SIP history (>= 15 min old, cached on disk) + today's IEX 1-minute bars.

Features are computed with the SAME code as the replay (kaibot.replay.features), on a SymbolDays that ends at the
last completed minute, so a live decision at minute m sees exactly what the replay saw at minute m.

Free-plan caveat: today's bars are IEX (a few % of US volume). Prices track SIP closely; volume does not, so
today's volume is rescaled by each symbol's SIP/IEX daily-volume ratio over the prior 20 sessions before the
turnover filter is applied. VWAP is IEX-based.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.engine.clock import session_minutes  # noqa: F401 - its home since M10 1.19; imported from here too
from kaibot.ingest.data_checks import BAD_PRICE, BAR_GAP, NO_BARS, STALE, UNMATCHED_MOVE, Finding, check_all
from kaibot.replay.data import SymbolDays, from_frame
from kaibot.replay.features import candidates, daily_stats, regime

log = logging.getLogger("kaibot.engine.market")

HISTORY_SESSIONS = 70
EMPTY_NEWS = np.array([], dtype="datetime64[s]")
DATA_CHECKS_ENV = "KAIBOT_DATA_CHECKS"  # M12 3.11 (#473): report-only data checks; off unless set to 1/true/yes/on
# M12 3.11 (#473), the blocking half: which checks block ENTRIES. Off (unset, empty, 0/no/off/false) by default: then
# nothing is blocked and the engine trades exactly as without it. 1/true/yes/on: BLOCKING_DEFAULT; or a comma list of
# check names. `bar_gap` is not in the default: on IEX a quiet stock has minutes without a bar and nothing is wrong.
DATA_BLOCK_ENV = "KAIBOT_DATA_CHECKS_BLOCK"
ALL_CHECKS = frozenset({STALE, NO_BARS, BAD_PRICE, BAR_GAP, UNMATCHED_MOVE})
BLOCKING_DEFAULT = frozenset({STALE, NO_BARS, BAD_PRICE, UNMATCHED_MOVE})
# the index's own findings that mean the minute feed cannot be trusted at all (the index has no move check)
INDEX_BLOCKING = frozenset({STALE, NO_BARS, BAD_PRICE, BAR_GAP})


def data_checks_on(env: Mapping[str, str] | None = None) -> bool:
    """Whether the report-only data checks run (M12 3.11). Off by default: only KAIBOT_DATA_CHECKS=1 (or true,
    yes, on) turns them on. Parsed by kaibot.common.switches (M10 1.23), as loaded at start."""
    from kaibot.common import switches

    return bool(switches.get(DATA_CHECKS_ENV, env))


def data_block_checks(env: Mapping[str, str] | None = None) -> frozenset[str]:
    """The checks whose findings block entries (M12 3.11). Empty (nothing blocks) unless KAIBOT_DATA_CHECKS_BLOCK is
    set: 1/true/yes/on gives BLOCKING_DEFAULT, a comma list names the checks. Parsed by kaibot.common.switches
    (M10 1.23): an unknown name is an error (SwitchError), at the engine's start already."""
    from kaibot.common import switches

    return frozenset(switches.value(DATA_BLOCK_ENV, env))


def new_findings(findings: list[Finding], seen: frozenset[tuple[str, str]]) -> list[Finding]:
    """The findings whose (symbol, check) was not in the last report, so a lasting fault is logged once and not
    every minute."""
    return [f for f in findings if (f.symbol, f.check) not in seen]


class LiveMarket:
    def __init__(
        self,
        data,
        symbols: list[str],
        etf_of: dict[str, str],
        spy: str,
        cache_dir: Path,
        vix_loader: Callable[[], pd.Series],
        minute_data=None,
    ):
        self.data = data
        # M9 1.5: the per-minute read (`update`) runs inside the engine's loop, so the engine gives it a client with
        # a deadline (engine/__main__.bounded_data); the history load keeps `data` (long retries, before the open)
        self.minute_data = minute_data if minute_data is not None else data
        self.symbols = symbols
        self.etf_of = etf_of
        self.spy = spy
        self.all = sorted(set(symbols) | set(etf_of.values()) | {spy})
        # S68: the owner's custom tickers ({ticker: sector ETF}). Their history is loaded, but a ticker is only
        # scanned once the engine has accepted it for the session (custom_ok). Empty = exactly the seed universe.
        self._base_symbols, self._base_etf_of = symbols, etf_of
        self.base = frozenset(symbols)
        self.custom: dict[str, str] = {}
        self.custom_ok: frozenset[str] = frozenset()
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
        # M12 3.11 (#473): what the data checks found at the last update. Filled when KAIBOT_DATA_CHECKS or
        # KAIBOT_DATA_CHECKS_BLOCK is on. Only the checks in `block_checks` block entries (`data_block`); with
        # KAIBOT_DATA_CHECKS_BLOCK off (the default) nothing that decides reads them.
        self.last_data_findings: list[Finding] = []
        self.block_checks: frozenset[str] = data_block_checks()
        self._checks_failed: str | None = None  # the checks raised at the last update (blocking: fail closed)

    # ---------- custom tickers (S68) ----------
    def set_custom(self, custom: dict[str, str]) -> bool:
        """Set the custom tickers for the next prepare_day. Returns True when the set changed (history must be
        reloaded). Seed symbols and reference ETFs are never overridden. Nothing is scanned until accept_custom."""
        refs = set(self._base_etf_of.values()) | {self.spy}
        custom = {s: e for s, e in custom.items() if s not in self._base_etf_of and s not in refs and e in refs}
        if custom == self.custom:
            return False
        self.custom, self.custom_ok = custom, frozenset()
        self._refresh()
        return True

    def accept_custom(self, ok) -> None:
        """The custom tickers that passed the engine's point-in-time check for this session."""
        self.custom_ok = frozenset(ok) & frozenset(self.custom)
        self._refresh()

    def _refresh(self) -> None:
        if not self.custom:  # no custom list: the very same objects as before S68
            self.symbols, self.etf_of = self._base_symbols, self._base_etf_of
        else:
            self.symbols = [*self._base_symbols, *sorted(self.custom_ok)]
            self.etf_of = {**self.custom, **self._base_etf_of}
        self.all = sorted(set(self._base_symbols) | set(self.custom) | set(self.etf_of.values()) | {self.spy})

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
        got = self.minute_data.bars(self.all, start, now, feed="iex")
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
        if data_checks_on() or self.block_checks:
            self._report_data_findings(now)

    # ---------- data checks (M12 3.11, #473): report, and block entries when KAIBOT_DATA_CHECKS_BLOCK is on ----------
    def data_findings(self, now: datetime) -> list[Finding]:
        """Stale, missing, zero or negative, gappy or index-unmatched bars among the scanned symbols and SPY at
        `now` (kaibot.ingest.data_checks). Reads today's bars and changes nothing."""
        if self.open_utc is None:
            return []
        return check_all(self.today_bars, self.symbols, self.spy, self.open_utc, now)

    def _report_data_findings(self, now: datetime) -> None:
        """Log what is newly wrong and keep the list. Never raises: a fault in a check must not cost a tick. With
        blocking on, a check that fails blocks every intraday entry until it works again (fail closed)."""
        try:
            found = self.data_findings(now)
            seen = frozenset((f.symbol, f.check) for f in self.last_data_findings)
            for f in new_findings(found, seen):
                how = "blocks entries" if f.check in self.block_checks else "report only"
                log.warning("data check (%s): %s %s: %s", how, f.symbol, f.check, f.detail)
            self.last_data_findings = found
            self._checks_failed = None
        except Exception as e:
            self.last_data_findings = []
            if self.block_checks:
                self._checks_failed = f"{type(e).__name__}: {e}"[:160]
                log.exception("data check failed: every intraday entry is blocked until it works again")
            else:
                log.exception("data check (report only) failed; nothing is blocked")

    def data_block(self, symbol: str, index: bool = True) -> str | None:
        """Why an entry in `symbol` is blocked by the data checks now (None: it is not). Only with
        KAIBOT_DATA_CHECKS_BLOCK on; always None otherwise. A blocking finding on the symbol blocks it; with `index`
        a blocking finding on the index (SPY: the minute feed itself is stale, empty, bad or gappy) or a check that
        failed blocks every symbol. A symbol this market does not scan has no bars to judge: None (the multi-day
        path's buys have their own pre-send quote gate, M9 1.16). Exits, stops and sales never ask."""
        if not self.block_checks:
            return None
        if index and self._checks_failed:
            return f"data checks failed ({self._checks_failed})"
        for f in self.last_data_findings:
            if index and f.symbol == self.spy and f.check in INDEX_BLOCKING and f.check in self.block_checks:
                return f"market data: index {f.symbol} {f.check} ({f.detail})"
        for f in self.last_data_findings:
            if f.symbol == symbol and f.check in self.block_checks:
                return f"market data: {f.symbol} {f.check} ({f.detail})"
        return None

    def data_block_status(self) -> dict | None:
        """For the heartbeat: the blocking findings now (None with blocking off)."""
        if not self.block_checks:
            return None
        hits = [f for f in self.last_data_findings if f.check in self.block_checks]
        out: dict = {"blocking": sorted(self.block_checks), "blocked": len(hits)}
        if hits:
            out["first"] = [f"{f.symbol} {f.check}" for f in hits[:5]]
        if self._checks_failed:
            out["failed"] = self._checks_failed
        return out

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

    def last_bar_end(self, s: str) -> datetime | None:
        """#384: when the last minute bar of `s` ended (its start plus a minute), the time its close is from."""
        df = self.today_bars.get(s)
        if df is None or not len(df):
            return None
        return pd.Timestamp(df["ts"].iloc[-1]).to_pydatetime() + timedelta(minutes=1)

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

