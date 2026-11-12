"""The momentum core of decision 15 in the multi-day research harness: `trend_momentum` (Clenow's "Stocks on the
Move" rules) and `mom_12_1_band` (banded 12-1 momentum, blend of 6 / 9 / 12-month lookbacks). Issue #127.

Research only, provisional: nothing here is imported by the engine. The frozen constructions, the neighbours, the
fill model and the pass marks are pre-registered in docs/prereg/trend_momentum.md and docs/prereg/mom_12_1_band.md
(committed before this file); nothing here is tuned. The simulator is the one of #113 (`multiday.Engine`); this
module only adds the two rules, a matched random-entry control and the statistics of the report.

No look-ahead: a decision for session i reads rows <= i - 1 of arrays whose row t uses sessions <= t, plus the
account as it stood before the session. The whole order list of a decision is fixed before any of it fills.
Signals use the harness's bars as they are: split-adjusted, no dividends.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kaibot.evaluation.metrics import max_drawdown, sharpe
from kaibot.replay.multiday import Caps, DailyBars, Engine, Indicators, MBook, closes
from kaibot.replay.multiday_rules import MIN_ADV, MIN_PRICE, SESSIONS_PER_MONTH
from kaibot.risk.gate import RiskPolicy

ATR_SESSIONS = 20
SMA_STOCK, SMA_MARKET = 100, 200
MOVE_WINDOW, MOVE_MAX = 90, 0.15
ANNUAL_SESSIONS = 250  # the published annualisation of the regression slope
RESIZE_BUFFER = 0.10  # no resize trade within 10% of the target
DECISION_WEEKDAY = 2  # Wednesday (Monday = 0)
FILLS = {"base": ("close", 1.0), "stress": ("open", 2.0)}  # where an order fills, and the spread / impact factor
COSTS = (1.0, 2.0, 3.0)
EXPOSURES = (30.0, 60.0, 100.0)
BASE = {"fill": "base", "cost": 1.0, "exposure_pct": 100.0, "earnings": "hold"}


# ---------------------------------------------------------------- the frozen rules and their neighbours


@dataclass(frozen=True)
class CoreSpec:
    rule: str  # trend_momentum | mom_12_1_band
    name: str  # the version in reports (a neighbour is "<version>~<neighbour>")
    max_names: int
    enter_top: float
    exit_top: float = 0.20
    market_filter: bool = True
    atr_risk: float = 0.001  # shares = equity x atr_risk / ATR(20)
    reg_window: int = 90  # trend_momentum: sessions of the log-price regression
    lookbacks: tuple[int, ...] = (126, 189, 252)  # mom_12_1_band: sessions back, each skipping the last 21
    resize: bool = False  # trend_momentum: every second week

    def params(self) -> dict:
        return dataclasses.asdict(self)


VERSIONS: dict[str, CoreSpec] = {
    "trend_momentum": CoreSpec("trend_momentum", "trend_momentum", max_names=30, enter_top=0.20, resize=True),
    "mom_12_1_band": CoreSpec("mom_12_1_band", "mom_12_1_band", max_names=20, enter_top=0.10),
    "mom_12_1_band_nofilter": CoreSpec("mom_12_1_band", "mom_12_1_band_nofilter", max_names=20, enter_top=0.10,
                                       market_filter=False),
}
# At most five per rule, declared in the pre-registrations. A plateau check, not a menu.
NEIGHBOURS: dict[str, dict[str, dict]] = {
    "trend_momentum": {"reg60": {"reg_window": 60}, "reg125": {"reg_window": 125}, "exit30": {"exit_top": 0.30},
                       "nofilter": {"market_filter": False}, "risk15": {"atr_risk": 0.0015}},
    "mom_12_1_band": {"only12": {"lookbacks": (252,)}, "only6": {"lookbacks": (126,)}, "exit30": {"exit_top": 0.30},
                      "names10": {"max_names": 10}, "risk15": {"atr_risk": 0.0015}},
}
assert all(len(v) <= 5 for v in NEIGHBOURS.values())


def neighbours(version: str) -> list[CoreSpec]:
    spec = VERSIONS[version]
    return [dataclasses.replace(spec, name=f"{version}~{n}", **kw) for n, kw in NEIGHBOURS[spec.rule].items()]


# ---------------------------------------------------------------- signals (row t uses sessions <= t)


def regression_score(c: np.ndarray, n: int) -> np.ndarray:
    """Annualised slope of the least-squares line through the last n log closes, times its R-squared. NaN until
    n closes exist (and for a price that did not move). Each row is computed from its own window only."""
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.log(c)
    out = np.full(c.shape, np.nan)
    if len(c) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(y, n, axis=0)  # (T - n + 1, symbols, n), no copy
    x = np.arange(n) - (n - 1) / 2
    sxx = float((x * x).sum())
    mean = w.mean(axis=-1)
    sxy = np.einsum("tsn,n->ts", w, x)
    syy = np.einsum("tsn,tsn->ts", w, w) - n * mean * mean
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        slope = sxy / sxx
        r2 = np.where(syy > 1e-12, sxy * sxy / (sxx * syy), np.nan)
        out[n - 1:] = (np.exp(slope * ANNUAL_SESSIONS) - 1) * np.clip(r2, 0.0, 1.0)
    return out


class Signals:
    """Everything the two rules and the control read, computed once per data set and shared by every run."""

    def __init__(self, bars: DailyBars, ind: Indicators, stocks: np.ndarray, spy: int) -> None:
        c = closes(bars)
        self.c = c.to_numpy()
        mask = np.zeros(c.shape[1], dtype=bool)
        mask[np.asarray(stocks, dtype=int)] = True
        with np.errstate(invalid="ignore"):
            self.ok = np.isfinite(bars.c) & (bars.c >= MIN_PRICE) & (ind.adv >= MIN_ADV) & mask[None, :]
            h, l = pd.DataFrame(bars.h), pd.DataFrame(bars.l)  # noqa: E741
            prev = c.shift(1)
            tr = np.fmax(np.fmax((h - l).to_numpy(), (h - prev).abs().to_numpy()), (l - prev).abs().to_numpy())
            self.atr20 = pd.DataFrame(tr).rolling(ATR_SESSIONS, min_periods=15).mean().to_numpy()
            self.below_avg = ~(c >= c.rolling(SMA_STOCK, min_periods=SMA_STOCK).mean()).to_numpy()
            moves = pd.DataFrame(ind.ret).abs().rolling(MOVE_WINDOW, min_periods=1).max()
            self.big_move = (moves > MOVE_MAX).to_numpy()
            spy_c = c[spy]
            self.market_on = (spy_c > spy_c.rolling(SMA_MARKET, min_periods=SMA_MARKET).mean()).to_numpy()
        days = bars.dates.astype("datetime64[D]").astype(np.int64)
        self.week = (days + 3) // 7  # Monday-based week number since 1970
        wd = (days + 3) % 7  # Monday = 0
        late = wd >= DECISION_WEEKDAY
        # the first session of each week that falls on Wednesday or later (Wednesday itself when it trades)
        self.weekly = late & ~np.r_[False, late[:-1] & (self.week[1:] == self.week[:-1])]
        month = bars.dates.astype("datetime64[M]")
        self.monthly = np.r_[False, month[1:] != month[:-1]]
        self._scores: dict[tuple, np.ndarray] = {}

    def trend_score(self, window: int) -> np.ndarray:
        key = ("trend", window)
        if key not in self._scores:
            self._scores[key] = regression_score(self.c, window)
        return self._scores[key]

    def momentum_score(self, lookbacks: Sequence[int]) -> np.ndarray:
        """Average cross-sectional percentile rank of the returns from each lookback to 21 sessions ago, among the
        stocks that pass the universe filter that day and have every return."""
        key = ("mom", tuple(lookbacks))
        if key not in self._scores:
            c = pd.DataFrame(self.c)
            skip = c.shift(SESSIONS_PER_MONTH)
            rets = [(skip / c.shift(n) - 1).to_numpy() for n in lookbacks]
            have = self.ok & np.all([np.isfinite(r) for r in rets], axis=0)
            ranks = [pd.DataFrame(np.where(have, r, np.nan)).rank(axis=1, pct=True).to_numpy() for r in rets]
            self._scores[key] = np.mean(ranks, axis=0)
        return self._scores[key]

    def score(self, spec: CoreSpec) -> np.ndarray:
        return self.trend_score(spec.reg_window) if spec.rule == "trend_momentum" else self.momentum_score(
            spec.lookbacks)

    def decision_days(self, spec: CoreSpec) -> np.ndarray:
        return self.weekly if spec.rule == "trend_momentum" else self.monthly


def target_shares(sig: Signals, pol: RiskPolicy, spec: CoreSpec, k: int, j: int, eq: float) -> float | None:
    """shares = equity x risk / ATR(20), whole, under the level's per-name cap; None without an ATR or a close."""
    atr, px = sig.atr20[k, j], sig.c[k, j]
    if not (math.isfinite(atr) and atr > 0 and math.isfinite(px) and px > 0):
        return None
    return float(min(math.floor(eq * spec.atr_risk / atr), math.floor(eq * pol.max_position_pct / 100 / px)))


# ---------------------------------------------------------------- the rule (both constructions)


class CoreRule:
    """One run of one frozen construction. `fill` is where orders fill: "close" (base: decided after the close of
    T, filled at the close of T + 1) or "open" (stress: the open of T + 1). The object carries the run's state
    (sales waiting for a bar, the decision log), so it serves ONE engine run."""

    def __init__(self, sig: Signals, pol: RiskPolicy, spec: CoreSpec, fill: str = "close",
                 exposure_pct: float = 100.0) -> None:
        if fill not in ("close", "open"):
            raise ValueError("fill is close or open")
        self.sig, self.pol, self.spec, self.fill = sig, pol, spec, fill
        self.name = spec.rule
        self.caps = Caps(max_positions=spec.max_names, max_exposure_pct=float(exposure_pct))
        self.score_m = sig.score(spec)
        self.days = sig.decision_days(spec)
        self.trend_rules = spec.rule == "trend_momentum"  # the 100-day and 15%-move conditions
        self.waiting: dict[int, str] = {}  # column -> reason: sales that have not filled yet
        self.orders: list[tuple] = []  # every order of every decision: (session, kind, column, shares or reason)
        self.entries: dict[int, int] = {}  # session -> new positions opened (what the random control mirrors)
        self.skipped = 0  # buys left out because one share costs more than the position size

    def at_open(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "open":
            self._session(i, eng, eq)

    def at_close(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "close":
            self._session(i, eng, eq)

    # -- what to trade: reads rows <= i - 1 and the account before the session

    def ranking(self, k: int) -> np.ndarray:
        """Columns of the ranked universe on the data of session k, best first (ties keep the column order)."""
        sc = self.score_m[k]
        js = np.flatnonzero(np.isfinite(sc) & self.sig.ok[k])
        return js[np.argsort(-sc[js], kind="stable")]

    def plan(self, i: int, held: Mapping[int, tuple[float, float]], eq: float) -> dict:
        """The orders of decision session i, from `held` (column -> (shares, last mark)) and equity `eq`, both as
        of the close of i - 1: {"sell": {column: reason}, "resize": [(column, shares)], "buy": [(column, shares)]}.
        Room is counted at the previous close's prices, as if every order of this plan fills."""
        sig, spec, k = self.sig, self.spec, i - 1
        order = self.ranking(k)
        n = len(order)
        keep = set(order[:math.ceil(spec.exit_top * n)].tolist())
        sell: dict[int, str] = {}
        for j in held:
            if j in self.waiting:
                sell[j] = self.waiting[j]  # an earlier sale still waiting for a bar stays an order to sell
            elif self.trend_rules and sig.big_move[k, j]:
                sell[j] = "gap"
            elif self.trend_rules and sig.below_avg[k, j]:
                sell[j] = "trend"
            elif j not in keep:
                sell[j] = "rank"
        cap = self.caps.max_exposure_pct / 100 * eq
        exposure = sum(q * last for j, (q, last) in held.items() if j not in sell)
        names = len(held) - len(sell)
        resize: list[tuple[int, float]] = []
        if spec.resize and sig.week[i] % 2 == 0:
            want = []
            for j, (q, last) in held.items():
                t = None if j in sell else target_shares(sig, self.pol, spec, k, j, eq)
                if t is not None and t >= 1 and abs(q - t) > RESIZE_BUFFER * t:
                    want.append((j, q, last, t))
            for j, q, last, t in sorted(want, key=lambda w: w[3] - w[1]):  # reductions first: they make room
                if t > q:
                    t = q + min(t - q, math.floor(max(cap - exposure, 0.0) / last))
                    if t <= q:
                        continue
                exposure += (t - q) * last
                resize.append((j, float(t)))
        buy: list[tuple[int, float]] = []
        if not spec.market_filter or sig.market_on[k]:
            for j in order[:math.ceil(spec.enter_top * n)].tolist():
                if j in held or (self.trend_rules and (sig.big_move[k, j] or sig.below_avg[k, j])):
                    continue  # already held (a holding being sold is not bought back the same day), or not buyable
                if names >= spec.max_names:
                    break
                t = target_shares(sig, self.pol, spec, k, j, eq)
                if t is None:
                    continue
                if t < 1:
                    self.skipped += 1  # one share is more than the position size (decision 15: skipped, counted)
                    continue
                room = math.floor(max(cap - exposure, 0.0) / sig.c[k, j])
                if room < 1:
                    break  # no room left: buying stops here
                exposure += min(t, room) * sig.c[k, j]
                names += 1
                buy.append((j, float(min(t, room))))
                if room < t:
                    break  # cut to the room left under the exposure cap, and buying stops there (choice 10 / 8)
        return {"sell": sell, "resize": resize, "buy": buy}

    # -- placing the orders

    def _sell(self, i: int, eng: Engine, j: int, reason: str) -> bool:
        return eng.sell_open(i, j, reason) if self.fill == "open" else eng.sell_close(i, j, reason)

    def _session(self, i: int, eng: Engine, eq: float) -> None:
        if i < 1:
            return
        if not self.days[i]:
            for j, reason in list(self.waiting.items()):  # a sale whose stock had no bar: try again
                if j not in eng.pos or self._sell(i, eng, j, reason):
                    del self.waiting[j]
            return
        p = self.plan(i, {j: (x.qty, x.last) for j, x in eng.pos.items()}, eq)
        self.orders += [(i, "sell", j, r) for j, r in p["sell"].items()]
        self.orders += [(i, "resize", j, t) for j, t in p["resize"]] + [(i, "buy", j, t) for j, t in p["buy"]]
        self.waiting = {j: r for j, r in p["sell"].items() if not self._sell(i, eng, j, r)}
        for j, t in p["resize"]:
            eng.adjust(i, j, t, eq, self.caps, at=self.fill)
        for j, t in p["buy"]:
            if eng.enter(i, j, eq, self.spec.name, self.caps, at=self.fill, shares=t, prev_close=True):
                self.entries[i] = self.entries.get(i, 0) + 1


def run_rule(bars: DailyBars, ind: Indicators, pol: RiskPolicy, sig: Signals, spec: CoreSpec, i0: int, i1: int, *,
             fill: str = "base", cost: float = 1.0, exposure_pct: float = 100.0, equity0: float = 100_000.0,
             hold_blk: np.ndarray | None = None, entry_blk: np.ndarray | None = None) -> tuple[MBook, CoreRule]:
    """One continuous account of one frozen configuration over sessions i0..i1, starting flat."""
    where, factor = FILLS[fill]
    rule = CoreRule(sig, pol, spec, where, exposure_pct)
    eng = Engine(bars, ind, pol, rule, lambda i: spec, equity0, hold_blk, entry_blk, cost_mult=cost * factor)
    return eng.run(i0, i1), rule


# ---------------------------------------------------------------- matched random-entry control


class RandomEntries:
    """The control's rule: on each session where the tested rule opened n new positions, open n random stocks of
    the same universe and liquidity filter with the same sizing and caps, and hold each for a number of sessions
    drawn from the tested rule's own holding periods. No ranking, no trend or market filter of its own (the
    market filter is mirrored through the entry dates), no resizing."""

    name = "random_entries"

    def __init__(self, sig: Signals, pol: RiskPolicy, spec: CoreSpec, fill: str, exposure_pct: float,
                 entries: Mapping[int, int], holds: Sequence[int], seed: int) -> None:
        self.sig, self.pol, self.spec, self.fill = sig, pol, spec, fill
        self.caps = Caps(max_positions=spec.max_names, max_exposure_pct=float(exposure_pct))
        self.entries, self.holds = dict(entries), np.asarray(holds, dtype=int)
        self.rng = np.random.default_rng(seed)
        self.due: dict[int, int] = {}

    def at_open(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "open":
            self._session(i, eng, eq)

    def at_close(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "close":
            self._session(i, eng, eq)

    def _session(self, i: int, eng: Engine, eq: float) -> None:
        if i < 1:
            return
        for j in [j for j in eng.pos if i >= self.due.get(j, i)]:
            if eng.sell_open(i, j, "random_hold") if self.fill == "open" else eng.sell_close(i, j, "random_hold"):
                self.due.pop(j, None)
        n = self.entries.get(i, 0)
        if not n or not len(self.holds):
            return
        k = i - 1
        cand = np.flatnonzero(self.sig.ok[k] & np.isfinite(self.sig.atr20[k]))
        cand = cand[~np.isin(cand, list(eng.pos))]
        if not len(cand):
            return
        picks = self.rng.choice(cand, size=min(n, len(cand)), replace=False)
        holds = self.rng.choice(self.holds, size=len(picks))
        for j, hold in zip(picks.tolist(), holds.tolist(), strict=True):
            t = target_shares(self.sig, self.pol, self.spec, k, j, eq)
            if t and eng.enter(i, j, eq, "random", self.caps, at=self.fill, shares=t, prev_close=True):
                self.due[j] = i + max(int(hold), 1)


def matched_random_control(bars: DailyBars, ind: Indicators, pol: RiskPolicy, sig: Signals, spec: CoreSpec,
                           book: MBook, rule: CoreRule, i0: int, i1: int, *, runs: int = 200, seed: int = 127,
                           fill: str = "base", cost: float = 1.0, exposure_pct: float = 100.0,
                           equity0: float = 100_000.0,
                           progress: Callable[[int], None] | None = None) -> dict:
    """The random-entry control of Gate B mark 3 for one account of a rule: `runs` seeded accounts with the same
    decision dates, the same number of new entries per date, the same sizing, caps, fill model and costs, random
    stocks of the same universe filter, exits after holding periods drawn from the rule's own (every closed
    position of the rule, resizes excluded). Returns each run's net Sharpe, CAGR and drawdown and the percentile
    of the rule's Sharpe and CAGR among them (share of runs strictly below the rule).

    This is the one function to swap for the gate-v2 control module of #126 when it lands."""
    holds = [t.hold_days for t in book.trades if t.reason != "resize"]
    where, factor = FILLS[fill]
    mine = curve_stats(daily_returns(book))
    out = {"runs": int(runs), "seed": int(seed), "entries": int(sum(rule.entries.values())),
           "entry_dates": len(rule.entries), "sharpe": [], "cagr_pct": [], "max_drawdown_pct": [],
           "return_pct": [], "avg_exposure_pct": []}
    for n in range(runs):
        ctl = RandomEntries(sig, pol, spec, where, exposure_pct, rule.entries, holds, seed + n)
        b = Engine(bars, ind, pol, ctl, lambda i: None, equity0, cost_mult=cost * factor).run(i0, i1)
        cs = curve_stats(daily_returns(b))
        out["sharpe"].append(cs["sharpe"])
        out["cagr_pct"].append(cs["cagr_pct"])
        out["max_drawdown_pct"].append(cs["max_drawdown_pct"])
        out["return_pct"].append(cs["return_pct"])
        out["avg_exposure_pct"].append(round(float(np.mean([r["exposure"] for r in b.daily])) * 100, 2))
        if progress:
            progress(n + 1)
    out["sharpe_percentile"] = percentile_of(mine["sharpe"], out["sharpe"])
    out["cagr_percentile"] = percentile_of(mine["cagr_pct"], out["cagr_pct"])
    out["median"] = {k: round(float(np.median(out[k])), 2) if out[k] else None
                     for k in ("sharpe", "cagr_pct", "max_drawdown_pct", "return_pct", "avg_exposure_pct")}
    return out


def percentile_of(x: float, sample: Sequence[float]) -> float | None:
    """Share (0-100) of the sample strictly below x."""
    s = np.asarray(sample, dtype=float)
    return round(float((s < x).mean()) * 100, 1) if s.size else None


# ---------------------------------------------------------------- statistics of one account


def daily_returns(book: MBook) -> np.ndarray:
    return np.array([r["pnl"] / r["start"] for r in book.daily], dtype=float)


def curve_stats(rets: np.ndarray) -> dict:
    """Total return, CAGR, annualised daily Sharpe and maximum drawdown of a daily mark-to-market return series
    (252 sessions a year; the Sharpe has no risk-free rate, like every Sharpe of the harness)."""
    rets = np.asarray(rets, dtype=float)
    eq = np.cumprod(1 + rets) if len(rets) else np.array([1.0])
    total = float(eq[-1] - 1)
    cagr = (1 + total) ** (252 / len(rets)) - 1 if len(rets) and total > -1 else -1.0
    return {"return_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2),
            "sharpe": round(sharpe(rets) * math.sqrt(252), 2),
            "max_drawdown_pct": round(max_drawdown(np.r_[1.0, eq]) * 100, 2)}


def year_returns(dates: np.ndarray, rets: np.ndarray) -> dict[str, float]:
    years = np.asarray(dates, dtype="datetime64[Y]").astype(str)
    out = (1 + pd.Series(np.asarray(rets, dtype=float))).groupby(years).prod() - 1
    return {str(y): round(float(v) * 100, 2) for y, v in out.items()}


def series_stats(dates: np.ndarray, rets: np.ndarray) -> dict:
    """The columns a benchmark row shares with a rule row."""
    cs = curve_stats(rets)
    yr = year_returns(dates, rets)
    return {**cs, "sharpe_haircut": round(cs["sharpe"] / 2, 2), "years": yr,
            "positive_years": int(sum(v > 0 for v in yr.values())), "n_years": len(yr)}


def account_stats(book: MBook) -> dict:
    """Everything the report shows for one account. Returns, Sharpe and drawdown are mark-to-market at each
    close; turnover is (bought + sold) / 2 per year over the average equity; cost is spread + impact + fees per
    year in basis points of the average equity; "trades" are closed positions (partial resize sales are counted
    apart); the top-five share is the net profit of the five most profitable stocks over the total net profit."""
    dates = np.array([r["date"] for r in book.daily], dtype="datetime64[D]")
    rets = daily_returns(book)
    out = series_stats(dates, rets)
    years = len(rets) / 252 if len(rets) else math.nan
    avg_eq = float(np.mean([r["start"] for r in book.daily])) if book.daily else math.nan
    expo = np.array([r["exposure"] for r in book.daily], dtype=float)
    closed = [t for t in book.trades if t.reason != "resize"]
    cost = sum(t.cost_spread + t.cost_impact + t.cost_slippage + t.cost_fees for t in book.trades)
    by_symbol: dict[str, float] = {}
    for t in book.trades:
        by_symbol[t.symbol] = by_symbol.get(t.symbol, 0.0) + t.pnl
    net = sum(by_symbol.values())
    top5 = sum(sorted(by_symbol.values(), reverse=True)[:5])
    out.update({
        "avg_exposure_pct": round(float(expo.mean()) * 100, 1) if len(expo) else None,
        "max_exposure_pct": round(float(expo.max()) * 100, 1) if len(expo) else None,
        "avg_names": round(float(np.mean([r["positions"] for r in book.daily])), 1) if book.daily else None,
        "turnover_pct_per_year": round((book.bought + book.sold) / 2 / avg_eq / years * 100, 1) if years else None,
        "cost_bp_per_year": round(cost / avg_eq / years * 1e4, 1) if years else None,
        "trades": len(closed), "resize_sales": len(book.trades) - len(closed),
        "avg_hold_days": round(float(np.mean([t.hold_days for t in closed])), 1) if closed else None,
        "top5_profit_share_pct": round(top5 / net * 100, 1) if net > 0 else None,
        "net_pnl": round(float(net), 2), "final_equity": round(float(book.equity), 2),
        "spread_impact_paid": round(float(sum(t.cost_spread + t.cost_impact for t in book.trades)), 2),
    })
    return out


def break_even_cost(net_pnl: float, pnl_at_zero_cost: float | None, spread_impact: float) -> dict:
    """The cost multiple at which net profit reaches zero, stated from the 0x run as pre-registered (section 5):
    profit falls linearly from the 0x run's to the 1x run's, so the multiple is P0 / (P0 - P1). Beside it, as a
    cross-check, the multiple with the 1x run's own trades held fixed, (P1 + spread and impact paid) / (spread
    and impact paid): the two differ a little because another cost changes whole-share counts and so the trades."""
    fixed = round((net_pnl + spread_impact) / spread_impact, 1) if spread_impact > 0 and \
        net_pnl + spread_impact > 0 else None
    out = {"multiple": None, "note": "", "pnl_at_zero_cost": pnl_at_zero_cost, "trades_fixed_multiple": fixed}
    if pnl_at_zero_cost is None:
        out["note"] = "no 0x run"
    elif pnl_at_zero_cost <= 0:
        out["note"] = "loses before costs"
    elif pnl_at_zero_cost <= net_pnl:
        out["note"] = "costs did not lower the profit"
    else:
        out.update(multiple=round(pnl_at_zero_cost / (pnl_at_zero_cost - net_pnl), 1), note="from the 0x and 1x runs")
    return out


# ---------------------------------------------------------------- Gate B (decision 15, section 7)

CANNOT = "cannot be judged yet (provisional data)"
GATE_B_MARKS = (
    "net return positive overall and in at least 6 of 10 calendar years",
    "either CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's, or CAGR >= SPY + 1 point "
    "with max drawdown <= 1.1 x SPY's",
    "net Sharpe at or above the 90th percentile of matched random entries (same universe, liquidity filter, "
    "exposure and holding-period distribution, >= 500 runs)",
    "marks 1-2 still hold at 2x costs (3x and break-even reported)",
    "holds on the point-in-time universe, not only on today's list",
    "every pre-declared neighbour passes mark 1",
)
MIN_POSITIVE_YEARS, MIN_CONTROL_RUNS, CONTROL_PERCENTILE = 6, 500, 90.0


def mark1(s: Mapping) -> bool:
    return bool(s["return_pct"] > 0 and s["positive_years"] >= MIN_POSITIVE_YEARS)


def mark2_price_only(s: Mapping, spy: Mapping) -> bool:
    """Mark 2 against the price-only SPY of this data set: information, not a verdict."""
    a = s["cagr_pct"] >= spy["cagr_pct"] - 2 and s["max_drawdown_pct"] <= 0.75 * spy["max_drawdown_pct"]
    b = s["cagr_pct"] >= spy["cagr_pct"] + 1 and s["max_drawdown_pct"] <= 1.1 * spy["max_drawdown_pct"]
    return bool(a or b)


def gate_b(base: Mapping, at_2x: Mapping | None, at_3x: Mapping | None, spy: Mapping, control: Mapping | None,
           neighbour_stats: Mapping[str, Mapping], break_even: Mapping | None) -> list[dict]:
    """Each mark of Gate B for the base version of a rule: pass / fail where today's data can say, otherwise
    "cannot be judged yet (provisional data)" with what is known. Nothing here is a Gate B verdict: every result
    of this harness is provisional."""
    word = {True: "pass", False: "fail"}
    yrs = f"{base['positive_years']} of {base['n_years']} calendar years positive"
    out = [{"mark": 1, "verdict": word[mark1(base)], "detail": f"net return {base['return_pct']}%, {yrs}"}]
    out.append({"mark": 2, "verdict": CANNOT,
                "detail": f"SPY is price only until S84. Against price-only SPY (CAGR {spy['cagr_pct']}%, max "
                          f"drawdown {spy['max_drawdown_pct']}%): CAGR {base['cagr_pct']}%, max drawdown "
                          f"{base['max_drawdown_pct']}% would {word[mark2_price_only(base, spy)]}"})
    if control is None or control.get("sharpe_percentile") is None:
        out.append({"mark": 3, "verdict": CANNOT, "detail": "no control runs"})
    else:
        ok = control["sharpe_percentile"] >= CONTROL_PERCENTILE
        few = control["runs"] < MIN_CONTROL_RUNS
        out.append({"mark": 3, "verdict": CANNOT if few else word[ok],
                    "detail": f"net Sharpe at percentile {control['sharpe_percentile']} of {control['runs']} "
                              f"matched random runs (CAGR at percentile {control['cagr_percentile']})"
                              + (f"; the mark needs >= {MIN_CONTROL_RUNS} runs, this many would {word[ok]}"
                                 if few else "")})
    if at_2x is None:
        out.append({"mark": 4, "verdict": CANNOT, "detail": "no 2x run"})
    else:
        be = "" if not break_even else (f"; break-even at {break_even['multiple']}x costs ({break_even['note']}; "
                                        f"{break_even['trades_fixed_multiple']}x with the 1x trades held fixed)"
                                        if break_even.get("multiple") is not None
                                        else f"; break-even: {break_even['note']}")
        x3 = "" if at_3x is None else f"; at 3x: net return {at_3x['return_pct']}%, CAGR {at_3x['cagr_pct']}%"
        m1 = mark1(at_2x)
        out.append({"mark": 4, "verdict": CANNOT if m1 else "fail",
                    "detail": f"at 2x costs mark 1 would {word[m1]} (net return {at_2x['return_pct']}%, "
                              f"{at_2x['positive_years']} of {at_2x['n_years']} years positive) and mark 2 against "
                              f"price-only SPY would {word[mark2_price_only(at_2x, spy)]}" + x3 + be})
    out.append({"mark": 5, "verdict": CANNOT, "detail": "today's stock list only; the point-in-time universe is "
                                                        "S83"})
    if neighbour_stats:
        fails = [n for n, s in neighbour_stats.items() if not mark1(s)]
        out.append({"mark": 6, "verdict": word[not fails],
                    "detail": f"{len(neighbour_stats) - len(fails)} of {len(neighbour_stats)} neighbours pass mark 1"
                              + (f"; failing: {', '.join(fails)}" if fails else "")})
    else:
        out.append({"mark": 6, "verdict": CANNOT, "detail": "neighbours not run"})
    for row, text in zip(out, GATE_B_MARKS, strict=True):
        row["text"] = text
    return out
