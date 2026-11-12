"""Shadow strategies (story S14): ORB on Stocks in Play and news continuation, evaluated every minute next to the
live engine. They NEVER trade: this module holds no reference to the broker and only writes `signal` rows with
mode="shadow", plus a counterfactual P&L per signal at the end of the session.

What a shadow signal records:
- the strategy's rule decision at the close of minute m: every registered strategy in shadow mode
  (strategies/registry.py: ORB, news continuation) is asked through the same `Strategy` interface as the live
  ones (strategies/base.py), with the session panel as its context,
- the would-be qty, binding constraint and failed checks from risk/gate.py `evaluate` (read-only), sized on an
  isolated per-strategy book: the run's start-of-day equity, the strategy's own open shadow positions and entries
  (as if it ran alone at the run's level; the live book's positions do not crowd it out),
- at day end: fill at the open of m + 1 through the shadow cost model, the 0.1 ATR / 1.5 ATR5 stop and the 15:55
  time exit via replay/sim.py `first_exit`, sell fees; P&L in $ and R.

Isolation: Engine.tick and Engine._finalize_day call `on_minute` / `on_day_end`; both catch every exception, log
it (and one `shadow_error` event per kind per day) and return, so shadow code can never affect live trading.
The same `ShadowBook` and `step` run in scripts/run_shadow_replay.py, so live and replay share the rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import sell_fees
from kaibot.replay.data import ET, N_MIN, SymbolDays
from kaibot.replay.sim import BLACKOUT_FOMC, EXIT_M, buy_px, first_exit, sell_px
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate
from kaibot.strategies import news_continuation as news
from kaibot.strategies import registry
from kaibot.strategies.base import Event, ShadowContext, due
from kaibot.strategies.shadow_types import Panel, ShadowCandidate, ffill_rows

log = logging.getLogger("kaibot.engine.shadow")

STRATEGIES = registry.names(registry.Mode.SHADOW)  # config/profiles.yaml shadow_strategies (a test keeps them equal)
FIRST_ENTRY_M = 5
MAX_NEWS_CALLS_PER_MIN = 10  # Alpaca budget is 150/min shared with the live engine (config/sources.yaml)
NEWS_MIN_AGE = timedelta(minutes=news.NewsParams().min_news_age_min)


# ======================= shared by live and replay =======================
@dataclass
class Entry:
    cand: ShadowCandidate
    qty: int
    binding: str
    failures: list[str]
    signal_id: int | None = None
    result: dict | None = None

    @property
    def risk_ps(self) -> float:
        return self.cand.entry_ref - self.cand.stop


@dataclass
class ShadowBook:
    """One strategy's would-be trades for one session, sized by the live risk gate (never sent)."""

    strategy: str
    policy: RiskPolicy
    equity: float
    fomc_day: bool = False
    n_min: int = N_MIN
    entries: list[Entry] = field(default_factory=list)

    @property
    def last_entry_m(self) -> int:
        return self.n_min - 15

    @property
    def flatten_m(self) -> int:
        return min(self.n_min - 5, EXIT_M)

    def _open_at(self, e: Entry, panel: Panel, m: int) -> bool:
        """Still open at the close of m? Uses bars <= m only (pending entries count as open, like the live book)."""
        fm = e.cand.m + 1
        if fm > m:
            return True
        r = e.cand.row
        stop_f = panel.o[r, fm] - e.risk_ps  # stop is re-anchored on the modelled fill, as in replay/sim.py
        return bool(np.nanmin(panel.l[r, fm:m + 1]) > stop_f) and m < self.flatten_m

    def consider(self, cand: ShadowCandidate, panel: Panel, m: int) -> Entry:
        live = [e for e in self.entries if e.qty > 0 and self._open_at(e, panel, m)]
        exposure = sum(e.qty * e.cand.entry_ref for e in live)
        snap = Snapshot(
            equity=self.equity, day_start_equity=self.equity, open_positions=len(live), symbol_exposure=0.0,
            open_risk=sum(e.qty * e.risk_ps for e in live), quote_age_s=0.0, kill_switch=False, reconciled=True,
            market_open=True, data_ok=True, in_entry_window=FIRST_ENTRY_M <= m + 1 <= self.last_entry_m,
            event_blackout=self.fomc_day and BLACKOUT_FOMC[0] <= m <= BLACKOUT_FOMC[1],
            has_open_position_in_symbol=any(e.cand.symbol == cand.symbol for e in live),
            entries_today=sum(e.qty > 0 for e in self.entries), run_peak_equity=None,
            buying_power=self.equity - exposure,
        )
        v = evaluate(Proposal(cand.symbol, Side.BUY, cand.entry_ref, cand.stop, cand.target), snap, self.policy)
        e = Entry(cand, v.qty if v.approved else 0, v.binding, [c.rule for c in v.failures])
        self.entries.append(e)
        return e

    def counterfactual(self, e: Entry, panel: Panel, sigma_1m: float) -> dict | None:
        """Would-be result: fill at the open of m + 1 (shadow costs), stop / 15:55 time exit, fees."""
        fm, r = e.cand.m + 1, e.cand.row
        if e.qty <= 0 or fm > self.flatten_m or not np.isfinite(panel.o[r, fm]):
            return None
        sig = sigma_1m if np.isfinite(sigma_1m) else 0.0
        o, h, l, c = (np.array(x[r], dtype=float) for x in (panel.o, panel.h, panel.l, panel.c))  # noqa: E741
        if self.flatten_m < EXIT_M:  # early close: freeze the bars after the flatten so first_exit's time exit
            for x in (o, h, l, c):  # at EXIT_M prices at the flatten minute's close
                x[self.flatten_m + 1:] = c[self.flatten_m]
        fill = buy_px(o[fm], e.qty, sig, panel.v[r, fm])
        stop_f = fill - e.risk_ps
        tgt = np.inf if e.cand.target is None else fill + (e.cand.target - e.cand.entry_ref)
        xm, gross, why = first_exit(h, l, o, c, fm, stop_f, tgt)
        xp = gross if why == "target" else sell_px(gross, e.qty, sig, panel.v[r, min(xm, N_MIN - 1)])
        pnl = (xp - fill) * e.qty - sell_fees(e.qty, xp)
        e.result = {"fill_m": fm, "fill": round(fill, 4), "stop": round(stop_f, 4), "exit_m": int(xm),
                    "exit": round(float(xp), 4), "reason": why, "qty": e.qty, "pnl_usd": round(float(pnl), 2),
                    "r_multiple": round(float(pnl / (e.risk_ps * e.qty)), 3)}
        return e.result


def step(books: dict[str, ShadowBook], panel: Panel, stats: dict, m: int,
         news_flags: Callable[[np.ndarray, int], np.ndarray], seen: set) -> list[Entry]:
    """Ask every registered shadow strategy with a book that is due at the close of minute m, in registry order.
    `news_flags(rows, m)` -> bool per row for the rows passing a strategy's price/volume setup (so the live engine
    only queries news for a handful of symbols)."""
    out: list[Entry] = []
    cands: list[ShadowCandidate] = []
    for strat in registry.shadow(books):
        if due(strat.schedule, Event.BAR_CLOSE, m, books[strat.name].n_min):
            cands += strat.on_event(ShadowContext(m, panel, stats[strat.name], news_flags, seen))
    for cand in cands:
        key = (cand.strategy, cand.symbol)
        if key in seen:  # one signal per strategy, symbol and day
            continue
        seen.add(key)
        out.append(books[cand.strategy].consider(cand, panel, m))
    return out


def merged_stats(rows: list[dict]) -> dict:
    """Stack per-symbol stats (one dict per row: {strategy: {name: value}}) into per-strategy arrays."""
    out: dict = {}
    for strat in STRATEGIES:
        keys = rows[0][strat].keys() if rows else ()
        out[strat] = {k: np.array([r[strat][k] for r in rows], dtype=float) for k in keys}
    return out


def stats_for_session(sd, i: int) -> dict:
    """Shadow statistics for session index i of a SymbolDays (i == len(dates): the next, unseen session)."""
    return {st.name: {k: v[i] for k, v in st.prior_stats(sd).items()} for st in registry.shadow()}


# ======================= live hook =======================
def on_minute(engine, m: int) -> None:
    """Called once per completed minute from Engine.tick. Never raises."""
    try:
        _runner(engine).minute(m)
    except Exception as e:  # shadow code must never affect live trading
        _contain(engine, "minute", e)


def on_day_end(engine) -> None:
    """Called from Engine._finalize_day: counterfactual P&L for today's shadow signals. Never raises."""
    try:
        r = getattr(engine, "_shadow", None)
        if r is not None and engine.day is not None and r.day == engine.day.day:
            r.finalize()
    except Exception as e:
        _contain(engine, "day_end", e)


def _contain(engine, where: str, e: Exception) -> None:
    log.exception("shadow %s failed (live trading unaffected)", where)
    try:
        seen = engine.__dict__.setdefault("_shadow_errors", set())
        key = (str(getattr(engine.day, "day", "")), where, type(e).__name__)
        if key not in seen:
            seen.add(key)
            engine.store.event("shadow_error", {"where": where, "error": f"{type(e).__name__}: {e}"[:300]},
                               engine._rid())
    except Exception:
        log.exception("shadow error event failed")


def _runner(engine) -> ShadowRunner:
    r = getattr(engine, "_shadow", None)
    if r is None or r.day != engine.day.day:
        r = ShadowRunner(engine)
        engine._shadow = r
    return r


class ShadowRunner:
    """Live state for one session. Reads the engine's market/state; writes only `signal` rows and one event."""

    def __init__(self, engine):
        t0 = _time.perf_counter()
        self.eng = engine
        mk, d = engine.market, engine.day
        self.day, self.open_utc = d.day, mk.open_utc
        self.symbols: list[str] = []
        rows, self.prev_close_utc = [], []
        for s in mk.symbols:
            h = mk.history.get(s)
            sd = fast_days(s, h) if h is not None and len(h) else None
            if sd is None or not len(sd.dates):
                continue
            self.symbols.append(s)
            rows.append(stats_for_session(sd, len(sd.dates)))
            prev = pd.Timestamp(f"{sd.dates[-1]} 16:00", tz=ET).tz_convert("UTC").to_pydatetime()
            self.prev_close_utc.append(prev)
        self.stats = merged_stats(rows)
        n = len(self.symbols)
        self.raw = {k: np.full((n, N_MIN), np.nan) for k in ("o", "h", "l", "c", "v", "vw")}
        self.seen_rows = dict.fromkeys(self.symbols, 0)
        self.vol_ratio = np.array([mk.vol_ratio.get(s, 1.0) for s in self.symbols])
        pol, fomc = engine.policy(), np.datetime64(d.day, "D") in engine.fomc
        only = getattr(engine, "shadow_only", None)  # the research command runs one strategy
        self.books = {k: ShadowBook(k, pol, d.start_equity, fomc, d.n_min) for k in STRATEGIES
                      if only is None or k in only}
        self.seen: set = set()
        self.news_pos: set[str] = set()
        self.news_checked: dict[str, int] = {}
        self.news_calls_min: tuple[int, int] = (-1, 0)
        self.minutes_ms: list[float] = []
        self.panel_m = -1
        self.finalized = False
        self.setup_ms = (_time.perf_counter() - t0) * 1000

    # ---------- data ----------
    def _ingest(self) -> None:
        bars = self.eng.market.today_bars
        open_ns = pd.Timestamp(self.open_utc).value
        for i, s in enumerate(self.symbols):
            df = bars.get(s)
            if df is None or not len(df):
                continue
            k0 = max(0, self.seen_rows[s] - 5)  # the market re-reads the last 2 minutes (late bars)
            self.seen_rows[s] = len(df)
            mins = (_utc_ns(df["ts"])[k0:] - open_ns) // 60_000_000_000
            ok = (mins >= 0) & (mins < N_MIN)
            mins = mins[ok]
            for k in ("o", "h", "l", "c", "vw"):
                self.raw[k][i, mins] = np.asarray(df[k].values[k0:], dtype=float)[ok]
            self.raw["v"][i, mins] = np.asarray(df["v"].values[k0:], dtype=float)[ok] * self.vol_ratio[i]

    def panel(self, m: int) -> Panel:
        """Bars 0..m (missing minute = no trades: last close carried, zero volume, as replay/data.from_frame);
        everything after m is NaN."""
        n = len(self.symbols)
        b = m + 1
        c = np.full((n, N_MIN), np.nan)
        c[:, :b] = ffill_rows(self.raw["c"][:, :b])
        out = {"c": c}
        for k in ("o", "h", "l", "vw"):
            x = np.full((n, N_MIN), np.nan)
            x[:, :b] = np.where(np.isnan(self.raw[k][:, :b]), c[:, :b], self.raw[k][:, :b])
            out[k] = x
        v = np.full((n, N_MIN), np.nan)
        v[:, :b] = np.nan_to_num(self.raw["v"][:, :b])
        return Panel(np.array(self.symbols), out["o"], out["h"], out["l"], c, v, out["vw"])

    def _news_flags(self, rows: np.ndarray, m: int) -> np.ndarray:
        """Point-in-time: a headline with created_at in [prior session 16:00 ET, decision - 15 min]."""
        decision = self.open_utc + timedelta(minutes=m + 1)
        out = np.zeros(len(rows), dtype=bool)
        for j, r in enumerate(rows):
            s = self.symbols[r]
            if s in self.news_pos:
                out[j] = True
                continue
            if self.news_checked.get(s) == m:
                continue
            mm, used = self.news_calls_min
            used = used if mm == m else 0
            if used >= MAX_NEWS_CALLS_PER_MIN:
                break
            self.news_calls_min = (m, used + 1)
            self.news_checked[s] = m
            try:
                n = self.eng.market.data.news_count(s, self.prev_close_utc[r], decision - NEWS_MIN_AGE)
            except Exception:
                log.exception("shadow news lookup failed for %s", s)
                n = 0
            if n:
                self.news_pos.add(s)
                out[j] = True
        return out

    # ---------- per minute ----------
    def minute(self, m: int) -> None:
        if self.finalized or not self.symbols:
            return
        t0 = _time.perf_counter()
        self._ingest()
        p = self.panel(m)
        self.panel_m = m
        decision = self.eng.now()
        for e in step(self.books, p, self.stats, m, self._news_flags, self.seen):
            e.signal_id = self._write_signal(e, decision)
        self.minutes_ms.append((_time.perf_counter() - t0) * 1000)

    def _write_signal(self, e: Entry, decision: datetime) -> int | None:
        c = e.cand
        feats = {k: _num(v) for k, v in c.features.items()}
        feats.update({"m": c.m, "exit_rule": c.exit_rule, "would_be_qty": e.qty, "gate_binding": e.binding,
                      "gate_failures": e.failures, "never_sent": True})
        veto = None if e.qty > 0 else ("gate: " + ",".join(e.failures) if e.failures else "gate: qty 0")
        row = self.eng.store.insert("signal", {
            "run_id": self.eng._rid(), "strategy": c.strategy, "mode": "shadow", "symbol": c.symbol,
            "ts": decision,
            "features_hash": hashlib.sha256(json.dumps(feats, sort_keys=True, default=str).encode()).hexdigest()[:16],
            "features": feats, "entry": c.entry_ref, "stop": c.stop, "target": c.target,
            "reasons": list(c.reasons), "rules_veto": veto,
        })
        return (row or {}).get("id")

    # ---------- day end ----------
    def finalize(self) -> None:
        if self.finalized:
            return
        self.finalized = True
        self._ingest()
        p = self.panel(N_MIN - 1)
        sigma = self.stats[news.NAME].get("sigma_1m", np.full(len(self.symbols), np.nan))
        summary = {}
        for name, book in self.books.items():
            res = []
            for e in book.entries:
                r = book.counterfactual(e, p, float(sigma[e.cand.row]))
                if e.signal_id is not None:
                    feats = {k: _num(v) for k, v in e.cand.features.items()}
                    feats.update({"m": e.cand.m, "exit_rule": e.cand.exit_rule, "would_be_qty": e.qty,
                                  "gate_binding": e.binding, "gate_failures": e.failures, "never_sent": True,
                                  "counterfactual": r})
                    self.eng.store.update("signal", {"id": e.signal_id}, {"features": feats})
                if r:
                    res.append(r)
            summary[name] = {"signals": len(book.entries), "sized": len(res),
                             "pnl_usd": round(sum(r["pnl_usd"] for r in res), 2),
                             "avg_r": round(float(np.mean([r["r_multiple"] for r in res])), 3) if res else None}
        ms = np.array(self.minutes_ms) if self.minutes_ms else np.array([0.0])
        summary["cost_ms"] = {"setup": round(self.setup_ms, 1), "p50": round(float(np.median(ms)), 2),
                              "max": round(float(ms.max()), 2), "symbols": len(self.symbols)}
        self.eng.store.event("shadow_session_end", {"day": str(self.day), **summary}, self.eng._rid())


def _utc_ns(ts: pd.Series) -> np.ndarray:
    v = ts.values  # tz-aware -> naive UTC datetime64
    if v.dtype.kind != "M":
        v = pd.to_datetime(ts, utc=True).values
    return v.astype("datetime64[ns]").astype(np.int64)


def fast_days(symbol: str, df: pd.DataFrame) -> SymbolDays | None:
    """Same result as replay.data.from_frame (completed sessions only), vectorised: ~10x faster, so the
    once-a-day shadow setup over ~300 symbols stays around a second."""
    if df is None or not len(df):
        return None
    idx = pd.DatetimeIndex(df["ts"])
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    wall = idx.tz_convert(ET).tz_localize(None).as_unit("ns").asi8  # ET wall clock, ns
    mins = (wall // 60_000_000_000) % 1440 - (9 * 60 + 30)
    keep = (mins >= 0) & (mins < N_MIN)
    if not keep.any():
        return None
    dates, rows = np.unique(wall[keep] // 86_400_000_000_000, return_inverse=True)
    dates = dates.astype("datetime64[D]")
    cols = mins[keep].astype(np.int64)
    vals = df.loc[keep, ["o", "h", "l", "c", "v", "vw"]].to_numpy(dtype=float)
    arrs = {}
    for j, k in enumerate(("o", "h", "l", "c", "v", "vw")):
        a = np.full((len(dates), N_MIN), np.nan)
        a[rows, cols] = vals[:, j]
        arrs[k] = a
    c = ffill_rows(arrs["c"])
    for k in ("o", "h", "l", "vw"):
        arrs[k] = np.where(np.isnan(arrs[k]), c, arrs[k])
    arrs["c"], arrs["v"] = c, np.nan_to_num(arrs["v"])
    return SymbolDays(symbol, dates, **arrs)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(f, 6) if np.isfinite(f) else None
