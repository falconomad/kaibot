"""The four daily one-shot rules of issue #112 (story S72) in the multi-day research harness of #113:
`pullback_uptrend` (RSI-2 type), `ath_breakout` (all-time-high breakout, a distribution over random signal
subsets), `post_earnings_week` (one week after an SEC-dated earnings release) and `weekly_fall_rebound` (the
stakeholder's question: buy a large stock after its worst five sessions); and `news_gap_week` of M8 (S98, #372:
one week after a large up-move on heavy volume with company news, against the same jumps without news).

Research only, PROVISIONAL: nothing here is imported by the engine. The frozen constructions, neighbours, fill
models and pass marks are in docs/prereg/<rule>.md, committed before this file; nothing here is tuned. The
simulator is the one of #113 (`multiday.Engine`), the data bundle, the statistics, the exposure-matched control
and Gate B are those of `multiday_core` (#127, #143). This module only adds the rules: no seventh simulator.

No look-ahead: a decision for session i reads rows <= i - 1 of arrays whose row t uses sessions <= t, plus the
account as it stood before the session. Signals use split-adjusted closes without dividends (`Market.signals.c`);
orders, sizes and the price floor use raw prices (`Signals.px`).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kaibot.replay import multiday_core as mc
from kaibot.replay.multiday import Caps, Engine, MBook
from kaibot.risk.gate import RiskPolicy

CASH_RESERVE = mc.CASH_RESERVE  # 3% of equity (Amendment 3 of the momentum pre-registrations), same here
RULES = ("pullback_uptrend", "ath_breakout", "post_earnings_week", "weekly_fall_rebound", "news_gap_week")
NEWS_LEAD = np.timedelta64(15 * 60, "s")  # news_gap_week: a headline or filing counts up to 15 minutes before the close


@dataclass(frozen=True)
class DailySpec:
    rule: str
    name: str  # the version in reports; a neighbour is "<version>~<neighbour>"
    max_names: int
    weight: float  # share of equity per position before the level's per-name cap
    fill: str  # where the BASE fill is: "open" (pullback, ath) or "close" (earnings, rebound)
    # pullback_uptrend
    rsi_max: float = 10.0
    below_sma5: bool = True
    trend_sma: int = 200
    exit_sma: int = 5
    # ath_breakout
    trail_atr: float = 10.0
    atr_n: int = 42
    min_price: float = 10.0
    min_history: int = 252
    # post_earnings_week
    entry_day: int = 1  # buy at the close of day +entry_day (decided after the close of day +entry_day - 1)
    ear_pct: float = 0.80
    ear_window: int = 252
    ear_min_events: int = 100
    # post_earnings_week, weekly_fall_rebound: sessions held after the fill
    hold: int = 0
    # weekly_fall_rebound
    fall: float = -0.15
    decile: float = 0.10
    market_filter: bool = False
    # news_gap_week (docs/prereg/news_gap_week.md): jump >= move_sigma x the 20-session sigma over SPY, volume >=
    # rel_vol x its 20-session mean; news "headline" (data/news), "filing" (a non-2.02 8-K, `info8k`) or "none"
    # (the `nonews` control: neither a headline nor any 8-K)
    move_sigma: float = 1.0
    rel_vol: float = 3.0
    news: str = "headline"

    def params(self) -> dict:
        return dataclasses.asdict(self)


VERSIONS: dict[str, DailySpec] = {
    "pullback_uptrend": DailySpec("pullback_uptrend", "pullback_uptrend", 4, 0.25, "open"),
    "ath_breakout": DailySpec("ath_breakout", "ath_breakout", 10, 0.10, "open"),
    "ath_breakout_k5": DailySpec("ath_breakout", "ath_breakout_k5", 5, 0.20, "open"),
    "ath_breakout_k20": DailySpec("ath_breakout", "ath_breakout_k20", 20, 0.05, "open"),
    "post_earnings_week": DailySpec("post_earnings_week", "post_earnings_week", 10, 0.10, "close", hold=5),
    "weekly_fall_rebound": DailySpec("weekly_fall_rebound", "weekly_fall_rebound", 10, 0.10, "close", hold=5),
    "weekly_fall_rebound_20": DailySpec("weekly_fall_rebound", "weekly_fall_rebound_20", 10, 0.10, "close",
                                        hold=20),
    "news_gap_week": DailySpec("news_gap_week", "news_gap_week", 10, 0.10, "close", hold=5),
    "news_gap_week_nonews": DailySpec("news_gap_week", "news_gap_week_nonews", 10, 0.10, "close", hold=5,
                                      news="none"),
}
# The base version of each rule (the Gate B row) and the versions a run of the rule reports.
RULE_VERSIONS: dict[str, tuple[str, ...]] = {
    "pullback_uptrend": ("pullback_uptrend",),
    "ath_breakout": ("ath_breakout", "ath_breakout_k5", "ath_breakout_k20"),
    "post_earnings_week": ("post_earnings_week",),
    "weekly_fall_rebound": ("weekly_fall_rebound", "weekly_fall_rebound_20"),
    "news_gap_week": ("news_gap_week", "news_gap_week_nonews"),
}
# Versions that are a control, not a configuration of the rule: run at the base fill and 1x costs only, compared
# with the base version (news_gap_week's `nonews`), never in the plateau check.
CONTROLS = {"news_gap_week_nonews"}
# At most five per rule, as declared in docs/prereg/<rule>.md. A plateau check around the base version.
NEIGHBOURS: dict[str, dict[str, dict]] = {
    "pullback_uptrend": {"rsi5": {"rsi_max": 5.0}, "rsi15": {"rsi_max": 15.0},
                         "slots10": {"max_names": 10, "weight": 0.10}, "no_sma5": {"below_sma5": False},
                         "sma150": {"trend_sma": 150}},
    "ath_breakout": {"trail8": {"trail_atr": 8.0}, "trail12": {"trail_atr": 12.0}, "atr14": {"atr_n": 14},
                     "price5": {"min_price": 0.0}},
    "post_earnings_week": {"day2": {"entry_day": 2}, "hold10": {"hold": 10}, "top10pct": {"ear_pct": 0.90},
                           "slots20": {"max_names": 20, "weight": 0.05}},
    "weekly_fall_rebound": {"fall10": {"fall": -0.10}, "fall20": {"fall": -0.20},
                            "slots5": {"max_names": 5, "weight": 0.20}, "market_filter": {"market_filter": True}},
    "news_gap_week": {"move2": {"move_sigma": 2.0}, "relvol2": {"rel_vol": 2.0}, "hold10": {"hold": 10},
                      "info8k": {"news": "filing"}},
}
assert all(len(v) <= 5 for v in NEIGHBOURS.values())


def neighbours(rule: str) -> list[DailySpec]:
    base = VERSIONS[RULE_VERSIONS[rule][0]]
    return [dataclasses.replace(base, name=f"{base.name}~{n}", **kw) for n, kw in NEIGHBOURS[rule].items()]


def fills(spec: DailySpec) -> dict[str, tuple[str, float]]:
    """Fill name -> (where, factor on spread and impact): base at the rule's own venue, stress at the open x2."""
    return {"base": (spec.fill, 1.0), "stress": ("open", 2.0)}


# ---------------------------------------------------------------- signals (row t uses sessions <= t)


def rsi_wilder(c: np.ndarray, n: int = 2) -> np.ndarray:
    """Wilder's RSI(n) per column: gains and losses of close-to-close changes, each smoothed as
    avg_t = (avg_{t-1} x (n - 1) + x_t) / n, seeded with the simple mean of the first n changes. 100 when the
    average loss is zero. A missing change restarts the column (NaN until n new changes)."""
    c = np.asarray(c, dtype=float)
    d = np.vstack([np.full((1, c.shape[1]), np.nan), np.diff(c, axis=0)])
    out = np.full(c.shape, np.nan)
    up = np.zeros(c.shape[1])
    dn = np.zeros(c.shape[1])
    cnt = np.zeros(c.shape[1], dtype=int)
    for t in range(len(c)):
        x = d[t]
        ok = np.isfinite(x)
        g, lo = np.where(ok, np.maximum(x, 0.0), 0.0), np.where(ok, np.maximum(-x, 0.0), 0.0)
        cnt = np.where(ok, cnt + 1, 0)
        seed = ok & (cnt <= n)
        up = np.where(~ok, 0.0, np.where(seed, up + g / n, (up * (n - 1) + g) / n))
        dn = np.where(~ok, 0.0, np.where(seed, dn + lo / n, (dn * (n - 1) + lo) / n))
        ready = ok & (cnt >= n)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(dn > 0, 100.0 - 100.0 / (1.0 + up / dn), 100.0)
        out[t] = np.where(ready, r, np.nan)
    return out


@dataclass
class NewsInputs:
    """What news_gap_week reads besides the bars (docs/prereg/news_gap_week.md). Times are UTC, datetime64[s],
    sorted per column. `headlines`: column -> stored headline times (data/news; a column with at least one has
    headline coverage). `filings_non202`: column -> acceptance times of form 8-K filings whose items do not include
    2.02; `filings_all`: of every form 8-K. `earnings`: column -> session indexes of day 0 of each earnings report.
    `complete`: per session, True when it lies in a complete news month. `windows`: per session k, (after, upto):
    the close of k - 1 and 15 minutes before the close of k (NYSE times, early closes included)."""

    headlines: Mapping[int, np.ndarray]
    filings_non202: Mapping[int, np.ndarray]
    filings_all: Mapping[int, np.ndarray]
    earnings: Mapping[int, Sequence[int]]
    complete: np.ndarray
    windows: tuple[np.ndarray, np.ndarray]


def news_windows(dates: Sequence) -> tuple[np.ndarray, np.ndarray]:
    """Per session k of `dates`: (the close of k - 1, the close of k minus 15 minutes) in UTC, datetime64[s];
    NaT for k = 0."""
    from kaibot.ingest import nyse_sessions as nyse

    d = [pd.Timestamp(x).date() for x in np.asarray(dates, dtype="datetime64[D]")]
    close = np.array([np.datetime64(nyse.close_utc(x).replace(tzinfo=None), "s") for x in d], dtype="datetime64[s]")
    after = np.r_[np.array(["NaT"], dtype="datetime64[s]"), close[:-1]]
    return after, close - NEWS_LEAD


def _any_in(times: np.ndarray | None, after: np.datetime64, upto: np.datetime64) -> bool:
    """A time t with after < t <= upto (a time after `upto` never counts)."""
    if times is None or not len(times):
        return False
    return bool(np.searchsorted(times, upto, "right") > np.searchsorted(times, after, "right"))


class DailySignals:
    """What the rules read, computed once per `Market` and cached per setting. `events` (post_earnings_week):
    column -> sorted session indexes of day 0 of each announcement (first session that opens after the filing).
    `news` (news_gap_week): `NewsInputs`."""

    def __init__(self, market: mc.Market, events: Mapping[int, Sequence[int]] | None = None,
                 news: NewsInputs | None = None) -> None:
        self.m = market
        self.s = market.signals
        self.c = self.s.c  # split-adjusted closes, a missing bar carried for at most 5 sessions
        self.ok = self.s.ok  # price >= $5, 20-day dollar volume >= $50M, (point in time) index member
        self.px = self.s.px  # raw closes: the price floor and the order sizes
        self.events = {int(j): sorted(int(x) for x in v) for j, v in (events or {}).items()}
        self.news = news
        self._cache: dict[tuple, np.ndarray] = {}

    def _get(self, key: tuple, make) -> np.ndarray:
        if key not in self._cache:
            with np.errstate(invalid="ignore", divide="ignore"):
                self._cache[key] = make()
        return self._cache[key]

    def sma(self, n: int) -> np.ndarray:
        return self._get(("sma", n), lambda: pd.DataFrame(self.c).rolling(n, min_periods=n).mean().to_numpy())

    def rsi2(self) -> np.ndarray:
        return self._get(("rsi2",), lambda: rsi_wilder(self.c, 2))

    def ret5(self) -> np.ndarray:
        return self._get(("ret5",), lambda: self.c / pd.DataFrame(self.c).shift(5).to_numpy() - 1.0)

    def atr(self, n: int) -> np.ndarray:
        """Simple average true range over n sessions on the split-adjusted bars (at least 30 of 42, n - 4 else)."""
        def make():
            b = self.m.sig
            h, lo = pd.DataFrame(b.h), pd.DataFrame(b.l)
            prev = pd.DataFrame(self.c).shift(1)
            tr = np.fmax(np.fmax((h - lo).to_numpy(), (h - prev).abs().to_numpy()), (lo - prev).abs().to_numpy())
            return pd.DataFrame(tr).rolling(n, min_periods=30 if n >= 42 else max(n - 4, 1)).mean().to_numpy()
        return self._get(("atr", n), make)

    def all_time_high(self, min_history: int) -> np.ndarray:
        """True where the close (a real bar) is at or above every earlier close in the data, with at least
        `min_history` earlier sessions that had a close."""
        def make():
            raw = np.asarray(self.m.sig.c, dtype=float)
            prior = pd.DataFrame(raw).shift(1).cummax().to_numpy()
            seen = np.vstack([np.zeros((1, raw.shape[1])), np.cumsum(np.isfinite(raw), axis=0)[:-1]])
            return np.isfinite(raw) & np.isfinite(prior) & (raw >= prior) & (seen >= min_history)
        return self._get(("ath", min_history), make)

    def ear(self) -> dict[int, list[tuple[int, float]]]:
        """day 0 -> [(column, EAR)]: the stock's close-to-close return from day -1 to day 0 minus SPY's."""
        key = ("ear",)
        if key not in self._cache:
            c = self.c
            spy = c[:, self.m.spy]
            out: dict[int, list[tuple[int, float]]] = {}
            for j, days in self.events.items():
                for d0 in days:
                    if d0 < 1 or d0 >= len(c):
                        continue
                    r = c[d0, j] / c[d0 - 1, j] - 1.0
                    rs = spy[d0] / spy[d0 - 1] - 1.0
                    if math.isfinite(r) and math.isfinite(rs):
                        out.setdefault(d0, []).append((j, float(r - rs)))
            self._cache[key] = out  # type: ignore[assignment]
        return self._cache[key]  # type: ignore[return-value]

    def ear_threshold(self, pct: float, window: int, min_events: int) -> np.ndarray:
        """Per session k: the `pct` quantile of the EARs of every announcement with day 0 in (k - window, k], NaN
        with fewer than `min_events` of them. Point in time: an announcement counts from its own day 0 on."""
        def make():
            n = len(self.c)
            by_day = self.ear()
            out = np.full(n, np.nan)
            vals: list[tuple[int, float]] = sorted((d, e) for d, xs in by_day.items() for _, e in xs)
            days = np.array([d for d, _ in vals], dtype=int)
            ears = np.array([e for _, e in vals], dtype=float)
            for k in range(n):
                lo, hi = np.searchsorted(days, k - window + 1, "left"), np.searchsorted(days, k, "right")
                if hi - lo >= min_events:
                    out[k] = float(np.quantile(ears[lo:hi], pct))
            return out
        return self._get(("ear_thr", pct, window, min_events), make)

    def jumps(self, move_sigma: float, rel_vol: float) -> tuple[np.ndarray, np.ndarray]:
        """(mask, excess) (sessions x columns): row k is a jump day on the bars up to the close of k. excess = the
        close-to-close return k - 1 -> k minus SPY's (split-adjusted bars, a missing bar is no return); mask:
        excess > 0, excess >= move_sigma x the ddof-1 std of the stock's returns of k - 20 .. k - 1, volume on k
        >= rel_vol x the mean volume of k - 20 .. k - 1 (all 20 present in both)."""
        def make():
            c = np.asarray(self.m.sig.c, dtype=float)
            v = np.asarray(self.m.sig.v, dtype=float)
            r = np.vstack([np.full((1, c.shape[1]), np.nan), c[1:] / c[:-1] - 1.0])
            ex = r - r[:, [self.m.spy]]
            sd = pd.DataFrame(r).rolling(20, min_periods=20).std(ddof=1).shift(1).to_numpy()
            av = pd.DataFrame(v).rolling(20, min_periods=20).mean().shift(1).to_numpy()
            return np.stack([ex, sd, av, v])
        ex, sd, av, v = self._get(("jump_inputs",), make)
        with np.errstate(invalid="ignore"):
            mask = (ex > 0) & (ex >= move_sigma * sd) & (v >= rel_vol * av) & np.isfinite(sd) & np.isfinite(av)
        return mask, ex

    def news_gap(self, spec: DailySpec) -> dict[int, list[tuple[int, float]]]:
        """Event day k -> [(column, excess return)] of news_gap_week's construction `spec` (point in time: every
        input is known by 15 minutes before the close of k, the decision is after that close)."""
        key = ("news_gap", spec.move_sigma, spec.rel_vol, spec.news)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        nw = self.news
        if nw is None:
            raise ValueError("news_gap_week needs NewsInputs")
        if spec.news not in ("headline", "filing", "none"):
            raise ValueError(f"unknown news test {spec.news}")
        mask, ex = self.jumps(spec.move_sigma, spec.rel_vol)
        earn = {int(j): set(int(x) for x in v) for j, v in nw.earnings.items()}
        after, upto = nw.windows
        covered = {j for j, t in nw.headlines.items() if len(t)}
        out: dict[int, list[tuple[int, float]]] = {}
        for k, j in zip(*np.nonzero(mask), strict=True):
            k, j = int(k), int(j)
            if k < 1 or k in earn.get(j, ()):
                continue
            if spec.news != "filing" and (j not in covered or not nw.complete[k]):
                continue
            a, u = after[k], upto[k]
            if spec.news == "headline":
                hit = _any_in(nw.headlines.get(j), a, u)
            elif spec.news == "filing":
                hit = _any_in(nw.filings_non202.get(j), a, u)
            else:
                hit = not (_any_in(nw.headlines.get(j), a, u) or _any_in(nw.filings_all.get(j), a, u))
            if hit:
                out.setdefault(k, []).append((j, float(ex[k, j])))
        self._cache[key] = out  # type: ignore[assignment]
        return out


# ---------------------------------------------------------------- the rule (all four constructions)


class DailyRule:
    """One run of one frozen construction: exits first, then entries into free slots, on the data of the
    previous session. `where` is the venue of every order ("open": the open of T + 1; "close": the close of
    T + 1, sized from the close of T). `seed` (ath_breakout): when there are more signals than free slots, a
    uniformly random subset is taken. Serves ONE engine run (it carries the run's state)."""

    def __init__(self, sig: DailySignals, pol: RiskPolicy, spec: DailySpec, where: str = "close",
                 exposure_pct: float = 100.0, seed: int | None = None) -> None:
        if where not in ("open", "close"):
            raise ValueError("where is open or close")
        if spec.rule not in RULES:
            raise ValueError(f"unknown rule {spec.rule}")
        self.sig, self.pol, self.spec, self.where = sig, pol, spec, where
        self.name = spec.rule
        self.caps = Caps(max_positions=spec.max_names, max_exposure_pct=float(exposure_pct))
        self.rng = np.random.default_rng(seed) if seed is not None else None
        self.waiting: dict[int, str] = {}
        self.hi: dict[int, float] = {}  # ath: highest close since entry (split-adjusted)
        self.stop: dict[int, float] = {}  # ath: ratcheting stop level (split-adjusted)
        self.entries: dict[int, int] = {}
        self.opened: list[tuple[int, int, float]] = []  # what the exposure-matched control copies
        self.resized: list[tuple[int, int, float]] = []  # never resized: the control reads it
        self.skipped = 0  # buys left out because one share cost more than the position size
        self.signals = 0  # candidates seen on decision days (before slots and cash)
        self._ready()

    def _ready(self) -> None:
        sp, s = self.spec, self.sig
        if sp.rule == "pullback_uptrend":
            self.trend, self.fast, self.rsi = s.sma(sp.trend_sma), s.sma(sp.exit_sma), s.rsi2()
        elif sp.rule == "ath_breakout":
            self.ath, self.atr = s.all_time_high(sp.min_history), s.atr(sp.atr_n)
        elif sp.rule == "post_earnings_week":
            self.thr = s.ear_threshold(sp.ear_pct, sp.ear_window, sp.ear_min_events)
            self.by_day = s.ear()
        elif sp.rule == "news_gap_week":
            self.by_day = s.news_gap(sp)
        else:
            self.r5 = s.ret5()

    def at_open(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.where == "open":
            self._session(i, eng, eq)

    def at_close(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.where == "close":
            self._session(i, eng, eq)

    # -- what to trade: reads rows <= k = i - 1 only

    def exit_reason(self, i: int, j: int, entry_i: int) -> str | None:
        sp, k, c = self.spec, i - 1, self.sig.c
        if sp.rule == "pullback_uptrend":
            return "above_sma5" if c[k, j] > self.fast[k, j] else None
        if sp.rule == "ath_breakout":
            if k < entry_i or not math.isfinite(c[k, j]):
                return None  # bought at this session's open: no close since entry yet
            self.hi[j] = max(self.hi.get(j, -math.inf), float(c[k, j]))
            a = self.atr[k, j]
            if math.isfinite(a):
                self.stop[j] = max(self.stop.get(j, -math.inf), self.hi[j] - sp.trail_atr * float(a))
            return "trail" if c[k, j] < self.stop.get(j, -math.inf) else None
        return "max_hold" if i - entry_i >= sp.hold else None

    def candidates(self, k: int) -> list[int]:
        """Columns that signal on the close of session k, in the rule's priority order."""
        sp, s = self.spec, self.sig
        ok = s.ok[k]
        with np.errstate(invalid="ignore"):
            if sp.rule == "pullback_uptrend":
                c = s.c[k]
                m = ok & (c > self.trend[k]) & (self.rsi[k] < sp.rsi_max)
                if sp.below_sma5:
                    m &= c < self.fast[k]
                js = np.flatnonzero(m)
                return js[np.argsort(self.rsi[k, js], kind="stable")].tolist()
            if sp.rule == "ath_breakout":
                js = np.flatnonzero(ok & self.ath[k] & (s.px[k] >= sp.min_price))
                return (self.rng.permutation(js) if self.rng is not None else js).tolist()
            if sp.rule == "post_earnings_week":
                d0 = k - (sp.entry_day - 1)
                thr = self.thr[k]
                if not math.isfinite(thr):
                    return []
                got = [(j, e) for j, e in self.by_day.get(d0, []) if ok[j] and e >= thr and e > 0]
                return [j for j, _ in sorted(got, key=lambda x: (-x[1], x[0]))]
            if sp.rule == "news_gap_week":  # event day k: decided after its close, bought at the close of k + 1
                got = [(j, e) for j, e in self.by_day.get(k, []) if ok[j]]
                return [j for j, _ in sorted(got, key=lambda x: (-x[1], x[0]))]
            if sp.market_filter and not s.s.market_on[k]:
                return []
            r = self.r5[k]
            have = ok & np.isfinite(r)
            if not have.any():
                return []
            pct = pd.Series(np.where(have, r, np.nan)).rank(pct=True).to_numpy()
            js = np.flatnonzero(have & (pct <= sp.decile) & (r <= sp.fall))
            return js[np.argsort(r[js], kind="stable")].tolist()

    # -- placing the orders

    def _sell(self, i: int, eng: Engine, j: int, reason: str) -> bool:
        return eng.sell_open(i, j, reason) if self.where == "open" else eng.sell_close(i, j, reason)

    def _ref(self, i: int, j: int) -> float:
        return float(self.sig.px[i - 1, j]) / self.sig.s.shares_basis(i, j)

    def _session(self, i: int, eng: Engine, eq: float) -> None:
        if i < 1:
            return
        sold: dict[int, float] = {}
        for j in list(eng.pos):
            p = eng.pos[j]
            reason = self.waiting.get(j) or self.exit_reason(i, j, p.entry_i)
            if reason is None:
                continue
            value = p.qty * p.last
            if self._sell(i, eng, j, reason):
                self.waiting.pop(j, None)
                self.hi.pop(j, None)
                self.stop.pop(j, None)
                sold[j] = value
            else:
                self.waiting[j] = reason  # no bar today: sold on the next session that has one
        for j in list(self.hi):
            if j not in eng.pos:  # closed by the engine (terminal return, forced sale)
                self.hi.pop(j, None)
                self.stop.pop(j, None)
        cands = [j for j in self.candidates(i - 1) if j not in eng.pos and j not in sold]
        self.signals += len(cands)
        free = self.spec.max_names - len(eng.pos)
        if free <= 0 or not cands:
            return
        money = eng.cash - CASH_RESERVE * eq  # sales of this session already filled (same print) are in the cash
        cap = min(self.spec.weight, self.pol.max_position_pct / 100.0) * eq
        for j in cands:
            if free <= 0:
                break
            ref = self._ref(i, j)
            if not (math.isfinite(ref) and ref > 0):
                continue
            t = math.floor(cap / ref)
            if t < 1:
                self.skipped += 1
                continue
            t = min(t, math.floor(max(money, 0.0) / ref))
            if t < 1:
                break  # the cash (less the reserve) is used up
            q = eng.enter(i, j, eq, self.spec.name, self.caps, at=self.where, shares=float(t), prev_close=True)
            if q:
                money -= q * ref
                free -= 1
                self.entries[i] = self.entries.get(i, 0) + 1
                self.opened.append((i, j, q * ref / eq))


def run_daily(market: mc.Market, sig: DailySignals, pol: RiskPolicy, spec: DailySpec, i0: int, i1: int, *,
              fill: str = "base", cost: float = 1.0, exposure_pct: float = 100.0, equity0: float = 100_000.0,
              seed: int | None = None, costs=None) -> tuple[MBook, DailyRule]:
    """One continuous account of one frozen configuration over sessions i0..i1, starting flat."""
    where, factor = fills(spec)[fill]
    rule = DailyRule(sig, pol, spec, where, exposure_pct, seed)
    eng = Engine(market.raw, market.ind, pol, rule, lambda i: spec, equity0, cost_mult=cost * factor, costs=costs,
                 actions=market.actions, terminal=market.terminal, exits=market.exits)
    return eng.run(i0, i1), rule


# ---------------------------------------------------------------- ath_breakout: a distribution, never one curve


def distribution(stats: Sequence[Mapping]) -> dict:
    """Percentiles 5/25/50/75/95 of CAGR, Sharpe and max drawdown over seeded runs, and the share passing mark 1."""
    out: dict = {"runs": len(stats)}
    if not stats:
        return out
    for k in ("cagr_pct", "sharpe", "max_drawdown_pct", "return_pct"):
        x = np.array([s[k] for s in stats], dtype=float)
        out[k] = {f"p{q}": round(float(np.percentile(x, q)), 2) for q in (5, 25, 50, 75, 95)}
    out["share_passing_mark1_pct"] = round(100.0 * float(np.mean([mc.mark1(s) for s in stats])), 1)
    return out


def median_seed(stats_by_seed: Mapping[int, Mapping]) -> int:
    """The seed whose Sharpe is the median: the ((n + 1) // 2)-th of n by Sharpe (ties by seed), the 100th of 200."""
    order = sorted(stats_by_seed, key=lambda s: (stats_by_seed[s]["sharpe"], s))
    return int(order[(len(order) - 1) // 2])


# ---------------------------------------------------------------- news_gap_week: the rule against its control

MIN_CONTROL_TRADES = 30
CONTROL_T = 2.0


def trade_excess(book: MBook, market: mc.Market) -> np.ndarray:
    """Per trade: the net return (P&L after all costs over the entry value) minus SPY's close-to-close return from
    the entry session's close to the exit session's close (split-adjusted)."""
    dates = np.asarray(market.sig.dates, dtype="datetime64[D]")
    spy = np.asarray(market.sig.c, dtype=float)[:, market.spy]
    out = []
    for t in book.trades:
        value = t.qty * t.entry
        a = np.searchsorted(dates, np.datetime64(t.date, "D"))
        b = np.searchsorted(dates, np.datetime64(t.exit_date, "D"))
        if value <= 0 or a >= len(dates) or b >= len(dates):
            continue
        rs = spy[b] / spy[a] - 1.0
        if math.isfinite(rs):
            out.append(t.pnl / value - rs)
    return np.asarray(out, dtype=float)


def welch_t(a: np.ndarray, b: np.ndarray) -> float | None:
    """(mean a - mean b) / sqrt(var a / n_a + var b / n_b), ddof 1; None with fewer than two of either."""
    if len(a) < 2 or len(b) < 2:
        return None
    se = math.sqrt(float(np.var(a, ddof=1)) / len(a) + float(np.var(b, ddof=1)) / len(b))
    return None if se == 0 else (float(np.mean(a)) - float(np.mean(b))) / se


def against_control(base_row: Mapping, ctl_row: Mapping, base_x: np.ndarray, ctl_x: np.ndarray) -> dict:
    """docs/prereg/news_gap_week.md, "Against the control": clearly above = a higher daily Sharpe AND a higher
    mean per-trade excess return with Welch t >= 2.0; fewer than 30 trades in either: cannot be judged (which counts
    as not clearly above)."""
    t = welch_t(base_x, ctl_x)
    out = {"base_sharpe": base_row.get("sharpe"), "control_sharpe": ctl_row.get("sharpe"),
           "base_trades": len(base_x), "control_trades": len(ctl_x),
           "base_mean_excess_pct": round(100 * float(np.mean(base_x)), 3) if len(base_x) else None,
           "control_mean_excess_pct": round(100 * float(np.mean(ctl_x)), 3) if len(ctl_x) else None,
           "welch_t": None if t is None else round(t, 2)}
    if len(base_x) < MIN_CONTROL_TRADES or len(ctl_x) < MIN_CONTROL_TRADES or t is None:
        out["verdict"] = "cannot be judged"
    else:
        sharpe_up = float(base_row.get("sharpe") or 0.0) > float(ctl_row.get("sharpe") or 0.0)
        out["verdict"] = "clearly above" if sharpe_up and t >= CONTROL_T else "not clearly above"
    return out
