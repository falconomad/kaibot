"""The momentum core of decision 15 in the multi-day research harness: `trend_momentum` (Clenow's "Stocks on the
Move" rules) and `mom_12_1_band` (banded 12-1 momentum, blend of 6 / 9 / 12-month lookbacks). Issue #127.

Research only, provisional: nothing here is imported by the engine. The frozen constructions, the neighbours, the
fill model and the pass marks are pre-registered in docs/prereg/trend_momentum.md and docs/prereg/mom_12_1_band.md
(committed before this file); nothing here is tuned. The simulator is the one of #113 (`multiday.Engine`); this
module only adds the two rules, a matched random-entry control and the statistics of the report.

No look-ahead: a decision for session i reads rows <= i - 1 of arrays whose row t uses sessions <= t, plus the
account as it stood before the session. The whole order list of a decision is fixed before any of it fills.
Signals use the harness's bars as they are: split-adjusted, no dividends.

Issue #143 (the honest rerun), each part off by default so that today's results are unchanged (a test pins them):
- `Market`: one bundle of what a run reads. `Market.from_bars` is today's harness (one set of bars for
  everything). `Market.from_panel` reads a raw panel of the daily store (#124): signals on split-adjusted prices
  without dividends (as the pre-registrations freeze), orders, costs, share counts and the dollar filters on raw
  prices, splits and dividends carried on held positions, SPY total return; with `member` (#123) only index
  members on the decision's data can be bought, and a company whose bars stop is closed at its terminal return.
- `exposure_matched_control`: Gate B mark 3 as settled in decision 15 section 10. Each random portfolio copies
  the rule's positions (entry session, exit session, share of equity, every resize); only the names are random,
  drawn from the stocks that pass the price and volume filter (and, point-in-time, are members) that day.
  The entries-matched control of #127 stays as `matched_random_control`, a diagnostic.
- `gate_b` judges marks 2, 3, 4 and 5 for real once their inputs exist (SPY total return, the exposure-matched
  control with >= 500 runs, a point-in-time run). Every result stays provisional (research harness, cost model
  v1): it can fail Gate B, it cannot pass it.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from kaibot.evaluation import random_control as rc
from kaibot.evaluation.metrics import max_drawdown, sharpe
from kaibot.replay.multiday import FIELDS, Caps, DailyBars, Engine, Indicators, MBook, closes, indicators
from kaibot.replay.multiday_rules import MIN_ADV, MIN_PRICE, SESSIONS_PER_MONTH
from kaibot.risk.gate import RiskPolicy

ATR_SESSIONS = 20
SMA_STOCK, SMA_MARKET = 100, 200
MOVE_WINDOW, MOVE_MAX = 90, 0.15
ANNUAL_SESSIONS = 250  # the published annualisation of the regression slope
RESIZE_BUFFER = 0.10  # no resize trade within 10% of the target
CASH_RESERVE = 0.03  # Amendment 3: every decision's buys are sized against cash minus 3% of equity
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
    """Everything the two rules and the control read, computed once per data set and shared by every run.

    `bars` / `ind` are the signal side. With the daily store (#143) the market side is given apart: `raw` (raw
    prices: the price filter and every dollar amount), `factor` (adjusted level / factor = raw level of that
    session), `adv` (raw dollar volume), `split` (shares per share at each session's open), and `member`
    (sessions x columns, True where the column's company was an index member that session). Without them the
    signal bars are the market bars too, as in #127."""

    def __init__(self, bars: DailyBars, ind: Indicators, stocks: np.ndarray, spy: int, *,
                 raw: DailyBars | None = None, factor: np.ndarray | None = None, adv: np.ndarray | None = None,
                 split: np.ndarray | None = None, member: np.ndarray | None = None) -> None:
        c = closes(bars)
        self.c = c.to_numpy()
        self.px = self.c if raw is None else closes(raw).to_numpy()  # tradable closes (raw dollars of that session)
        self.split = split
        mask = np.zeros(c.shape[1], dtype=bool)
        mask[np.asarray(stocks, dtype=int)] = True
        mkt = bars if raw is None else raw
        with np.errstate(invalid="ignore"):
            self.ok = (np.isfinite(mkt.c) & (mkt.c >= MIN_PRICE) & ((ind.adv if adv is None else adv) >= MIN_ADV)
                       & mask[None, :])
            if member is not None:
                self.ok &= member
            h, l = pd.DataFrame(bars.h), pd.DataFrame(bars.l)  # noqa: E741
            prev = c.shift(1)
            tr = np.fmax(np.fmax((h - l).to_numpy(), (h - prev).abs().to_numpy()), (l - prev).abs().to_numpy())
            self.atr20 = pd.DataFrame(tr).rolling(ATR_SESSIONS, min_periods=15).mean().to_numpy()
            if factor is not None:
                self.atr20 = self.atr20 / factor  # raw dollars of that session
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

    def shares_basis(self, i: int, j: int) -> float:
        """Shares of session i per share of the close of i - 1 (a split that goes ex on session i; 1.0 without)."""
        if self.split is None:
            return 1.0
        s = float(self.split[i, j])
        return s if math.isfinite(s) and s > 0 else 1.0


def target_shares(sig: Signals, pol: RiskPolicy, spec: CoreSpec, k: int, j: int, eq: float,
                  s: float = 1.0) -> float | None:
    """shares = equity x risk / ATR(20), whole, under the level's per-name cap; None without an ATR or a close.
    Read on the close of session k, counted in the shares of session k + 1 (`s`: a split between them)."""
    atr, px = sig.atr20[k, j] / s, sig.px[k, j] / s
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
        # what the exposure-matched control copies: (session, column, value / equity) of every fill
        self.opened: list[tuple[int, int, float]] = []
        self.resized: list[tuple[int, int, float]] = []  # the position's value / equity after the resize

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

    def plan(self, i: int, held: Mapping[int, tuple[float, float]], eq: float, cash: float | None = None) -> dict:
        """The orders of decision session i, from `held` (column -> (shares, last mark)) and equity `eq`, both as
        of the close of i - 1: {"sell": {column: reason}, "resize": [(column, shares)], "buy": [(column, shares)]}.
        Room is counted at the previous close's prices, as if every order of this plan fills.

        `cash` (the account's cash before the session's fills): Amendment 3 of both pre-registrations. Purchases
        (new buys and resize increases) also fit in the cash plus the plan's sales and reductions at those prices,
        less a reserve of `CASH_RESERVE` x equity. None: no cash limit (unit tests of the ranking only)."""
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
        money = None if cash is None else (cash + sum(q * last for j, (q, last) in held.items() if j in sell)
                                           - CASH_RESERVE * eq)

        def fits(px: float) -> float:  # whole shares that fit under the cap and (Amendment 3) in the cash
            n = math.floor(max(cap - exposure, 0.0) / px)
            return n if money is None else min(n, math.floor(max(money, 0.0) / px))

        resize: list[tuple[int, float]] = []
        if spec.resize and sig.week[i] % 2 == 0:
            want = []
            for j, (q, last) in held.items():
                t = None if j in sell else target_shares(sig, self.pol, spec, k, j, eq, sig.shares_basis(i, j))
                if t is not None and t >= 1 and abs(q - t) > RESIZE_BUFFER * t:
                    want.append((j, q, last, t))
            for j, q, last, t in sorted(want, key=lambda w: w[3] - w[1]):  # reductions first: they make room
                if t > q:
                    t = q + min(t - q, fits(last))
                    if t <= q:
                        continue
                exposure += (t - q) * last
                if money is not None:
                    money -= (t - q) * last
                resize.append((j, float(t)))
        buy: list[tuple[int, float]] = []
        if not spec.market_filter or sig.market_on[k]:
            for j in order[:math.ceil(spec.enter_top * n)].tolist():
                if j in held or (self.trend_rules and (sig.big_move[k, j] or sig.below_avg[k, j])):
                    continue  # already held (a holding being sold is not bought back the same day), or not buyable
                if names >= spec.max_names:
                    break
                sj = sig.shares_basis(i, j)
                t = target_shares(sig, self.pol, spec, k, j, eq, sj)
                if t is None:
                    continue
                if t < 1:
                    self.skipped += 1  # one share is more than the position size (decision 15: skipped, counted)
                    continue
                px = sig.px[k, j] / sj
                room = fits(px)
                if room < 1:
                    break  # no room left: buying stops here
                exposure += min(t, room) * px
                if money is not None:
                    money -= min(t, room) * px
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
        p = self.plan(i, {j: (x.qty, x.last) for j, x in eng.pos.items()}, eq, eng.cash)
        self.orders += [(i, "sell", j, r) for j, r in p["sell"].items()]
        self.orders += [(i, "resize", j, t) for j, t in p["resize"]] + [(i, "buy", j, t) for j, t in p["buy"]]
        self.waiting = {j: r for j, r in p["sell"].items() if not self._sell(i, eng, j, r)}
        for j, t in p["resize"]:
            if eng.adjust(i, j, t, eq, self.caps, at=self.fill) and j in eng.pos:
                self.resized.append((i, j, eng.pos[j].qty * self._ref(i, j) / eq))
        for j, t in p["buy"]:
            q = eng.enter(i, j, eq, self.spec.name, self.caps, at=self.fill, shares=t, prev_close=True)
            if q:
                self.entries[i] = self.entries.get(i, 0) + 1
                self.opened.append((i, j, q * self._ref(i, j) / eq))

    def _ref(self, i: int, j: int) -> float:
        """The sizing price of an order for session i: the close of i - 1 in the shares of session i."""
        return float(self.sig.px[i - 1, j]) / self.sig.shares_basis(i, j)


def run_rule(bars: DailyBars, ind: Indicators, pol: RiskPolicy, sig: Signals, spec: CoreSpec, i0: int, i1: int, *,
             fill: str = "base", cost: float = 1.0, exposure_pct: float = 100.0, equity0: float = 100_000.0,
             hold_blk: np.ndarray | None = None, entry_blk: np.ndarray | None = None,
             costs=None, actions: tuple[np.ndarray, np.ndarray] | None = None,
             terminal: Mapping[int, tuple[int, float]] | None = None,
             exits: Mapping[int, Sequence[int]] | None = None) -> tuple[MBook, CoreRule]:
    """One continuous account of one frozen configuration over sessions i0..i1, starting flat. `bars` and `ind`
    are the market side (raw prices with the daily store); `costs` (None: cost model v1), `actions` and
    `terminal` see `multiday.Engine`."""
    where, factor = FILLS[fill]
    rule = CoreRule(sig, pol, spec, where, exposure_pct)
    eng = Engine(bars, ind, pol, rule, lambda i: spec, equity0, hold_blk, entry_blk, cost_mult=cost * factor,
                 costs=costs, actions=actions, terminal=terminal, exits=exits)
    return eng.run(i0, i1), rule


# ---------------------------------------------------------------- what one run reads (issue #143)

DISPUTE_WINDOW = 5  # sessions around a split the store's table and Alpaca's bars disagree about: no entries
SPLIT_TOL = 0.01  # the share basis of the two sources differs beyond 1% (the store's check uses the same)
EXPOSURE_TOLERANCE_PTS = 2.0  # a random run's average exposure may differ from the rule's by this much
TERMINAL_SLACK = 7  # sessions: bars that stop this close to the end of the panel are the data's end, not a hole


def spy_total_return(panel, spy: str = "SPY") -> tuple[np.ndarray, dict]:
    """SPY's total return per session of a RAW panel, and where it came from. Never lets a missing dividend make
    SPY look worse (a weaker benchmark flatters every rule):

    1. Alpaca's own all-adjusted factor (`ref_total`, stored beside the bars) when the store has it for SPY,
       unless the corporate-actions table gives a higher total (then the table);
    2. otherwise the table, if every full calendar year has its four quarterly dividends;
    3. otherwise the table completed: each missing quarter of a full year gets a dividend on the session nearest
       the 20th of the quarter's last month (SPY goes ex near the third Friday of March, June, September and
       December), as large as the largest of the four recorded dividends nearest to it.
    `info` says which, the dividends per year, what was added and the yearly points over price only."""
    j = panel.col(spy)
    c = np.asarray(panel.c[:, j], dtype=float)
    dates = np.asarray(panel.dates, dtype="datetime64[D]")
    prev = pd.Series(c).ffill().shift(1).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        table = panel.total_return(spy)
        price = c * np.asarray(panel.split[:, j], dtype=float) / prev - 1.0
        ref = np.asarray(getattr(panel, "ref_total", np.full(c.shape, np.nan)), dtype=float)[:, j]
        r_ref = None
        if np.isfinite(ref[np.isfinite(c)]).all() and np.isfinite(c).sum() > 1:
            adj = c * ref
            r_ref = adj / pd.Series(adj).ffill().shift(1).to_numpy() - 1.0
    div = np.asarray(panel.div[:, j], dtype=float)
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    months = dates.astype("datetime64[M]").astype(int) % 12 + 1
    per_year = {int(y): int(((years == y) & (div > 0)).sum()) for y in np.unique(years)}
    full = {int(y) for y in np.unique(years) if ((years == y) & (months == 1)).any() and
            ((years == y) & (months == 12)).any() and (years == y).sum() >= 240}

    def growth(r) -> float:
        return float(np.nanprod(1.0 + np.asarray(r)[1:]))

    info: dict = {"dividends_per_year": per_year, "added": []}
    if r_ref is not None and growth(r_ref) >= growth(table):
        out, info["source"] = r_ref, "alpaca_adjusted (Alpaca's all-adjusted SPY factor)"
    elif all(per_year[y] >= 4 for y in full):
        out = table
        info["source"] = "table (every full year has four dividends)" + (
            "; above Alpaca's factor" if r_ref is not None else "")
    else:
        out = np.array(table, dtype=float)
        paid = np.flatnonzero(div > 0)
        for y in sorted(full):
            for q in range(4):
                inq = (years == y) & ((months - 1) // 3 == q)
                if (inq & (div > 0)).any() or not len(paid):
                    continue
                target = np.datetime64(f"{y}-{3 * q + 3:02d}-20", "D")
                t = int(np.argmin(np.abs(dates - target).astype(int)))
                near = paid[np.argsort(np.abs(paid - t))[:4]]
                amount = float(div[near].max())
                if np.isfinite(prev[t]) and prev[t] > 0:
                    out[t] += amount / prev[t]
                    info["added"].append({"date": str(dates[t]), "dividend": round(amount, 4)})
        info["source"] = ("table completed: " + str(len(info["added"])) + " missing quarterly dividends added at "
                          "the largest nearby amount")
    n = int(np.isfinite(out[1:]).sum())
    if n > 250:
        tr, po = growth(out), growth(price)
        info["points_per_year_over_price"] = round(((tr ** (252 / n)) - (po ** (252 / n))) * 100, 2)
    return out, info


@dataclass
class Market:
    """The data of one ten-year test. `raw` is the market side (orders, costs, share counts, the price and dollar
    filters, the marks), `sig` the signal side (scores, averages, returns), `ind` the engine's indicators in the
    dollars of `raw`, `signals` the shared `Signals`. `actions`, `terminal`: see `multiday.Engine`. `spy_tr`: SPY's
    total return per session (None: not available, so the benchmark is price only). `member`: the point-in-time
    membership mask the signals were built with (None: today's list)."""

    raw: DailyBars
    sig: DailyBars
    ind: Indicators
    signals: Signals
    stocks: np.ndarray
    spy: int
    universe: str = "today"  # "today" | "point_in_time"
    bars_source: str = "minute"  # "minute" (data/bars_1m, as in #113 / #127) | "store" (data/daily, #124)
    actions: tuple[np.ndarray, np.ndarray] | None = None
    terminal: dict[int, tuple[int, float]] = dataclasses.field(default_factory=dict)
    exits: dict[int, list[int]] = dataclasses.field(default_factory=dict)
    spy_tr: np.ndarray | None = None
    member: np.ndarray | None = None
    notes: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_bars(cls, bars: DailyBars, stocks: np.ndarray, spy: int, spy_tr: np.ndarray | None = None) -> Market:
        """Today's harness: one set of split-adjusted bars without dividends for everything (#113, #127)."""
        ind = indicators(bars)
        return cls(bars, bars, ind, Signals(bars, ind, stocks, spy), np.asarray(stocks, dtype=int), spy,
                   spy_tr=spy_tr)

    @classmethod
    def from_panel(cls, panel, stocks: Sequence[str], spy: str, *, member: np.ndarray | None = None,
                   terminal_returns: Mapping[str, float] | None = None, exclude: Sequence[str] = (),
                   disputed: Sequence[str] = (), default_terminal: float = -0.30) -> Market:
        """From a RAW `kaibot.ingest.daily_store.DailyPanel` (any duck-typed equal). The session calendar is
        SPY's. `member` is (panel sessions x panel symbols) bool, `exclude` keys that may never be bought,
        `terminal_returns` key -> return booked when a held company's bars stop more than `TERMINAL_SLACK`
        sessions before the end (default -30%).

        `disputed`: keys whose split the store's check found the table and Alpaca's bars disagree about
        (`split_basis_days > 0`), so their share count cannot be carried through it. The sessions on the other
        share basis are located from Alpaca's own volume factor (`ref_split` against the table's `pf_split`); no
        entry from `DISPUTE_WINDOW` sessions before the first of them to `DISPUTE_WINDOW` after the last, and a
        position still held is sold at the market at the close before the first (`exits`). A disputed key
        without Alpaca's factor cannot be located and is never bought."""
        if getattr(panel, "kind", "raw") != "raw":
            raise ValueError("pass the raw panel: the market side reads raw prices")
        j_spy = panel.col(spy)
        keep = np.isfinite(panel.c[:, j_spy])
        if not keep.any():
            raise ValueError(f"no {spy} bars in the panel")
        adj = panel.adjusted("split", source="auto")  # no dividends in the signals (both pre-registrations, item 1)
        lost = int(((panel.split[~keep] != 1.0) | (panel.div[~keep] > 0)).sum())

        def bars_of(pn) -> DailyBars:
            return DailyBars(panel.dates[keep], list(panel.symbols), *(getattr(pn, k)[keep] for k in FIELDS))

        raw, sig = bars_of(panel), bars_of(adj)
        factor = adj.factor[keep]
        split, div = panel.split[keep], panel.div[keep]
        cols = raw.cols(stocks)
        allowed = np.ones(raw.c.shape, dtype=bool)
        allowed[:, raw.cols(exclude)] = False
        exits: dict[int, list[int]] = {}
        windows: dict[str, object] = {}
        n_s = len(raw.dates)
        for key in disputed:
            if key not in raw._col:
                continue
            j = raw.col(key)
            ref, tab = np.asarray(panel.ref_split, dtype=float)[keep, j], np.asarray(panel.pf_split, dtype=float)[
                keep, j]
            with np.errstate(invalid="ignore", divide="ignore"):
                off = np.flatnonzero(np.isfinite(raw.c[:, j]) & (np.abs(ref / tab - 1.0) > SPLIT_TOL))
            if not np.isfinite(ref).any() or not len(off):
                allowed[:, j] = False
                windows[key] = "never bought (no Alpaca factor to locate the disputed sessions)"
                continue
            a, b = int(off[0]), int(off[-1])
            allowed[max(a - DISPUTE_WINDOW, 0):min(b + DISPUTE_WINDOW + 1, n_s), j] = False
            if a > 0:
                exits[j] = [a - 1]
            windows[key] = (f"no entries {raw.dates[max(a - DISPUTE_WINDOW, 0)]} to "
                            f"{raw.dates[min(b + DISPUTE_WINDOW, n_s - 1)]}; "
                            + (f"a holding is sold at the close of {raw.dates[a - 1]}" if a > 0 else
                               "the other basis starts with the data"))
        mem = None if member is None else (np.asarray(member, dtype=bool)[keep] & allowed)
        if mem is None and not allowed.all():
            mem = allowed
        sig_ind = indicators(sig)
        with np.errstate(invalid="ignore"):
            adv = (closes(raw) * pd.DataFrame(raw.v)).rolling(20, min_periods=15).mean().to_numpy()
            ind = Indicators(sig_ind.atr / factor, sig_ind.sigma, adv, sig_ind.ret)
        signals = Signals(sig, sig_ind, cols, raw.col(spy), raw=raw, factor=factor, adv=adv, split=split, member=mem)
        has = np.isfinite(raw.c)
        n = len(raw.dates)
        terminal, tr = {}, dict(terminal_returns or {})
        for j in cols.tolist():
            seen = np.flatnonzero(has[:, j])
            if len(seen) and seen[-1] < n - 1 - TERMINAL_SLACK:
                terminal[j] = (int(seen[-1]), float(tr.get(raw.symbols[j], default_terminal)))
        spy_full, spy_info = spy_total_return(panel, spy)
        spy_tr = spy_full[keep]
        notes = {"sessions_dropped_off_spy_calendar": int((~keep).sum()), "actions_on_dropped_sessions": lost,
                 "excluded_keys": sorted(set(exclude) & set(raw.symbols)), "terminal_closes": len(terminal),
                 "disputed_splits": windows, "spy_total_return": spy_info,
                 "adjustment_sources": dict(sorted(pd.Series(adj.adj_source).value_counts().items()))
                 if adj.adj_source else {}, **{f"panel_{k}": v for k, v in getattr(panel, "notes", {}).items()}}
        return cls(raw, sig, ind, signals, cols, raw.col(spy), "point_in_time" if member is not None else "today",
                   "store", (split, div), terminal, exits, spy_tr, mem, notes)

    def run(self, pol: RiskPolicy, spec: CoreSpec, i0: int, i1: int, **kw) -> tuple[MBook, CoreRule]:
        return run_rule(self.raw, self.ind, pol, self.signals, spec, i0, i1, actions=self.actions,
                        terminal=self.terminal, exits=self.exits, **kw)

    def spy_price_only(self, i0: int, i1: int) -> np.ndarray:
        """SPY close-to-close returns on split-adjusted closes, no dividends."""
        c = self.sig.c[:, self.spy]
        return c[i0:i1 + 1] / c[i0 - 1:i1] - 1

    def spy_total_return(self, i0: int, i1: int) -> np.ndarray | None:
        if self.spy_tr is None:
            return None
        r = np.asarray(self.spy_tr[i0:i1 + 1], dtype=float)
        return r if len(r) == i1 - i0 + 1 and np.isfinite(r).all() else None

    def equal_weight(self, i0: int, i1: int, equity0: float = 100_000.0, costs=None) -> np.ndarray:
        """The equal-weight benchmark on the same universe option: today's list, or the members of each month."""
        from kaibot.replay.multiday import equal_weight_benchmark

        return equal_weight_benchmark(self.raw, self.ind, self.stocks, i0, i1, equity0, costs, eligible=self.member,
                                      actions=self.actions, terminal=self.terminal or None, exits=self.exits)


# ---------------------------------------------------------------- exposure-matched random control (Gate B mark 3)


@dataclass(frozen=True)
class MatchedSlot:
    """One position of the rule as the control copies it: the session it was bought, the session it was sold
    (its own exit, a terminal close or the end of data), its value over the account's equity at entry, and every
    resize as (session, value over equity after it). The name is what the control replaces."""

    entry: int
    exit: int
    weight: float
    resizes: tuple[tuple[int, float], ...]
    col: int  # the rule's own name

    def events(self) -> list[tuple[int, float]]:
        return [(self.entry, self.weight), *self.resizes, (self.exit, 0.0)]


def committed_exposure(events: Sequence[Sequence[tuple[int, float]]], i0: int, i1: int) -> np.ndarray:
    """Share of equity committed on each session i0..i1: each position counts at its value over equity at its
    last fill (entry or resize) from that session until the session it is sold. What "the same money at work"
    means for the control; the marked exposure also moves with the names' prices, which is what is tested."""
    out = np.zeros(i1 - i0 + 1)
    for ev in events:
        ev = sorted(ev, key=lambda x: x[0])
        for (a, w), (b, _) in zip(ev, [*ev[1:], (i1 + 1, 0.0)], strict=True):
            lo, hi = max(a, i0), min(b, i1 + 1)
            if hi > lo:
                out[lo - i0:hi - i0] += w
    return out


def matched_schedule(book: MBook, rule: CoreRule, bars: DailyBars) -> list[MatchedSlot]:
    """The rule's realised positions in the order they were opened. Weights are the order's value at its sizing
    price (the close before the fill) over the equity it was sized with, exactly what the rule committed."""
    exits: dict[tuple[str, np.datetime64], int] = {}
    for t in book.trades:
        if t.reason != "resize":
            exits[(t.symbol, np.datetime64(t.date, "D"))] = bars.index_from(t.exit_date)
    out = []
    for i, j, w in rule.opened:
        x = exits[(bars.symbols[j], np.datetime64(bars.dates[i], "D"))]
        rs = tuple((r, wr) for r, jj, wr in rule.resized if jj == j and i < r <= x)
        out.append(MatchedSlot(i, x, float(w), rs, j))
    return out


class SlotReplay:
    """The control's rule: slot k buys `selection[k]` at the slot's fill, sized to the slot's share of the equity
    at the previous close (the rule's own sizing price convention), resizes it to the rule's share on the rule's
    resize sessions, and sells it on the slot's exit session (waiting for a bar like the rule). Same engine, fill
    model, costs and caps as the rule's run. Order in a session: exits, resizes (reductions first), entries."""

    name = "exposure_matched"

    def __init__(self, sig: Signals, slots: Sequence[MatchedSlot], selection: Sequence[int], fill: str,
                 caps: Caps) -> None:
        self.sig, self.fill, self.caps = sig, fill, caps
        self.sel = [int(x) for x in selection]
        self.slots = list(slots)
        self.enter_at: dict[int, list[int]] = {}
        self.exit_at: dict[int, list[int]] = {}
        self.resize_at: dict[int, list[tuple[int, float]]] = {}
        for k, sl in enumerate(self.slots):
            self.enter_at.setdefault(sl.entry, []).append(k)
            self.exit_at.setdefault(sl.exit, []).append(k)
            for r, w in sl.resizes:
                self.resize_at.setdefault(r, []).append((k, w))
        self.held: dict[int, int] = {}  # slot -> column held
        self.pending: dict[int, int] = {}  # slot -> column whose entry came to under one share: a resize buys it
        self.slot_of: dict[int, int] = {}  # column -> slot (while held)
        self.waiting: set[int] = set()
        self.unfilled = 0  # slots whose name could not be bought on the fill day
        self.events: dict[int, list[tuple[int, float]]] = {}  # slot -> (session, value / equity) of each fill

    def at_open(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "open":
            self._session(i, eng, eq)

    def at_close(self, i: int, eng: Engine, cfg, eq: float) -> None:
        if self.fill == "close":
            self._session(i, eng, eq)

    def _shares(self, i: int, j: int, w: float, eq: float) -> float:
        px = self._ref(i, j)
        # to the nearest whole share: flooring would leave every random position half a share short of the rule's
        return float(math.floor(w * eq / px + 0.5)) if math.isfinite(px) and px > 0 else 0.0

    def _sell(self, i: int, eng: Engine, j: int) -> bool:
        return eng.sell_open(i, j, "slot_exit") if self.fill == "open" else eng.sell_close(i, j, "slot_exit")

    def _session(self, i: int, eng: Engine, eq: float) -> None:
        for k in self.exit_at.get(i, ()):
            self.pending.pop(k, None)
            j = self.held.pop(k, None)
            if j is not None and j in eng.pos:
                self.waiting.add(j)
            elif j is not None:  # already gone (its bars stopped: closed at the terminal return)
                self.slot_of.pop(j, None)
                self.events[k].append((i, 0.0))
        for j in list(self.waiting):
            if j not in eng.pos or self._sell(i, eng, j):
                self.waiting.discard(j)
                self.events[self.slot_of.pop(j)].append((i, 0.0))
        todo = []
        for k, w in self.resize_at.get(i, ()):
            j = self.held.get(k)
            if j is not None and j in eng.pos and j not in self.waiting:
                t = self._shares(i, j, w, eq)
                todo.append((t - eng.pos[j].qty, j, t, k))
            elif k in self.pending and self.pending[k] not in eng.pos:
                j = self.pending[k]  # the rule's entry was smaller than one share of this name: buy it now
                t = self._shares(i, j, w, eq)
                todo.append((t, j, t, k))
        for _, j, t, k in sorted(todo, key=lambda x: x[0]):  # reductions first: they make room
            if t < 1:
                continue
            if j in eng.pos:
                if eng.adjust(i, j, t, eq, self.caps, at=self.fill) and j in eng.pos:
                    self.events[k].append((i, eng.pos[j].qty * self._ref(i, j) / eq))
            else:
                q = eng.enter(i, j, eq, "random", self.caps, at=self.fill, shares=t, prev_close=True)
                if q:
                    del self.pending[k]
                    self.held[k], self.slot_of[j] = j, k
                    self.events[k] = [(i, q * self._ref(i, j) / eq)]
        for k in self.enter_at.get(i, ()):
            j = self.sel[k]
            if j < 0 or j in eng.pos:
                self.unfilled += 1
                continue
            t = self._shares(i, j, self.slots[k].weight, eq)
            q = eng.enter(i, j, eq, "random", self.caps, at=self.fill, shares=t, prev_close=True) if t >= 1 else 0
            if q:
                self.held[k], self.slot_of[j] = j, k
                self.events[k] = [(i, q * self._ref(i, j) / eq)]
            else:
                self.unfilled += 1
                if self.slots[k].resizes:
                    self.pending[k] = j

    def _ref(self, i: int, j: int) -> float:
        return float(self.sig.px[i - 1, j]) / self.sig.shares_basis(i, j)


def exposure_matched_control(market: Market, pol: RiskPolicy, spec: CoreSpec, book: MBook, rule: CoreRule,
                             i0: int, i1: int, *, runs: int = 500, seed: int = 127, fill: str = "base",
                             cost: float = 1.0, exposure_pct: float = 100.0, equity0: float = 100_000.0,
                             progress: Callable[[int], None] | None = None, costs=None) -> dict:
    """Gate B mark 3 (decision 15 sections 7 and 10, docs/ops/gate_v2.md hand-off 4): `runs` seeded random
    portfolios that copy the rule's positions slot by slot and draw only the names, from the stocks that pass the
    price and volume filter on the decision's data (`Signals.ok` of the session before the fill: price >= $5,
    20-day dollar volume >= $50M, and index membership on the point-in-time universe) and have a bar to fill on.
    The rule's own names are run through the same replay as a check (`simulated_strategy_sharpe`); the lower of
    the two percentiles counts. Returns `kaibot.evaluation.random_control.ControlResult` as a dict plus the per-run
    numbers of the report (Sharpe, CAGR, drawdown, return, average exposure) and the CAGR percentile."""
    where, factor = FILLS[fill]
    caps = Caps(max_positions=spec.max_names, max_exposure_pct=float(exposure_pct))
    slots = matched_schedule(book, rule, market.raw)
    sig, raw = market.signals, market.raw
    fill_px = raw.c if where == "close" else raw.o
    per_run: list[dict] = []
    record = [True]

    def eligible(e: int) -> np.ndarray:
        return np.flatnonzero(sig.ok[e - 1] & np.isfinite(fill_px[e]) & np.isfinite(sig.px[e - 1]))

    def simulate(selection) -> np.ndarray:
        ctl = SlotReplay(sig, slots, selection, where, caps)
        b = Engine(raw, market.ind, pol, ctl, lambda i: None, equity0, cost_mult=cost * factor, costs=costs,
                   actions=market.actions, terminal=market.terminal, exits=market.exits).run(i0, i1)
        r = daily_returns(b)
        if record[0]:
            got = committed_exposure(list(ctl.events.values()), i0, i1)
            per_run.append({**curve_stats(r), "avg_exposure_pct": round(float(np.mean(
                [x["exposure"] for x in b.daily])) * 100, 2), "unfilled": ctl.unfilled,
                "committed_gap_pts": float(np.mean(np.abs(got - want))) * 100})
            if progress:
                progress(len(per_run))
        return r

    mine_r = daily_returns(book)
    mine = curve_stats(mine_r)
    rule_expo = float(np.mean([x["exposure"] for x in book.daily])) * 100
    want = committed_exposure([sl.events() for sl in slots], i0, i1)
    schedule = [rc.Slot(sl.entry, sl.exit - sl.entry, sl.weight) for sl in slots]
    out = {"kind": "exposure_matched", "runs": int(runs), "seed": int(seed), "slots": len(slots),
           "resizes": int(sum(len(sl.resizes) for sl in slots)), "rule_avg_exposure_pct": round(rule_expo, 2)}
    if not slots:
        return {**out, "matched": False, "sharpe_percentile": None, "cagr_percentile": None, "median": {},
                "note": "the rule opened no position: nothing to match"}
    # random_control runs the random portfolios first (recorded here), then the rule's own names once (not recorded)
    calls = {"n": 0}

    def sim_all(selection) -> np.ndarray:
        calls["n"] += 1
        record[0] = calls["n"] <= runs
        return simulate(selection)

    res = rc.matched_random_control(mine_r, schedule, eligible, sim_all, runs=runs, seed=seed,
                                    strategy_selection=[sl.col for sl in slots])
    d = res.to_dict()
    sharpes = [x["sharpe"] for x in per_run]
    gaps = [x["committed_gap_pts"] for x in per_run]
    expo = [x["avg_exposure_pct"] for x in per_run]
    used = min(d["percentile"], d["percentile_simulated"]) if d["percentile_simulated"] is not None else d[
        "percentile"]
    out.update({
        # matched: every slot found a free eligible name in every run, and in every run the money committed per
        # session differs from the rule's by at most EXPOSURE_TOLERANCE_PTS of equity on average (a cut to
        # whole shares, the cash or the cap, or a slot left unfilled, makes it smaller)
        "control_result": d, "matched": bool(res.matched and per_run and max(gaps) <= EXPOSURE_TOLERANCE_PTS),
        "exposure_tolerance_pts": EXPOSURE_TOLERANCE_PTS,
        "committed_exposure_gap_pts": {"mean": round(float(np.mean(gaps)), 3), "max": round(float(max(gaps)), 3)},
        "rule_avg_committed_pct": round(float(want.mean()) * 100, 2),
        "unfilled_in_replay": int(sum(x["unfilled"] for x in per_run)),
        "sharpe_percentile": round(float(used), 1), "sharpe_percentile_rule_run": round(d["percentile"], 1),
        "sharpe_percentile_replayed_names": None if d["percentile_simulated"] is None else round(
            d["percentile_simulated"], 1),
        "cagr_percentile": percentile_of(mine["cagr_pct"], [x["cagr_pct"] for x in per_run]),
        "sharpe": sharpes, "cagr_pct": [x["cagr_pct"] for x in per_run],
        "max_drawdown_pct": [x["max_drawdown_pct"] for x in per_run], "return_pct": [x["return_pct"] for x in per_run],
        "avg_exposure_pct": expo,
        # marked at the close: also moves with the names' prices (the rule's winners grow), information only
        "marked_exposure_gap_pts": round(float(max(abs(e - rule_expo) for e in expo)), 2) if expo else None,
        "median": {k: round(float(np.median([x[k] for x in per_run])), 2) if per_run else None
                   for k in ("sharpe", "cagr_pct", "max_drawdown_pct", "return_pct", "avg_exposure_pct")}})
    return out


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
                           equity0: float = 100_000.0, progress: Callable[[int], None] | None = None,
                           costs=None, actions: tuple[np.ndarray, np.ndarray] | None = None,
                           terminal: Mapping[int, tuple[int, float]] | None = None) -> dict:
    """DIAGNOSTIC since #143 (Amendment 1 of both pre-registrations): the entries-matched control of #127. It
    does not match exposure, so its Sharpe percentile is biased low and Gate B mark 3 is never judged on it; the
    mark uses `exposure_matched_control`. `runs` seeded accounts with the same
    decision dates, the same number of new entries per date, the same sizing, caps, fill model and costs, random
    stocks of the same universe filter, exits after holding periods drawn from the rule's own (every closed
    position of the rule, resizes excluded). Returns each run's net Sharpe, CAGR and drawdown and the percentile
    of the rule's Sharpe and CAGR among them (share of runs strictly below the rule).

    The report labels it "entries-matched (diagnostic)"."""
    holds = [t.hold_days for t in book.trades if t.reason != "resize"]
    where, factor = FILLS[fill]
    mine = curve_stats(daily_returns(book))
    out = {"kind": "entries_matched", "runs": int(runs), "seed": int(seed),
           "entries": int(sum(rule.entries.values())),
           "entry_dates": len(rule.entries), "sharpe": [], "cagr_pct": [], "max_drawdown_pct": [],
           "return_pct": [], "avg_exposure_pct": []}
    for n in range(runs):
        ctl = RandomEntries(sig, pol, spec, where, exposure_pct, rule.entries, holds, seed + n)
        b = Engine(bars, ind, pol, ctl, lambda i: None, equity0, cost_mult=cost * factor, costs=costs,
                   actions=actions, terminal=terminal).run(i0, i1)
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
NOT_A_PASS = ("every result of this research harness stays provisional (the harness of #113, not the engine with "
              "`SimBroker`; cost model v1): it can fail Gate B, it cannot pass it")


def mark1(s: Mapping) -> bool:
    return bool(s["return_pct"] > 0 and s["positive_years"] >= MIN_POSITIVE_YEARS)


def mark2_price_only(s: Mapping, spy: Mapping) -> bool:
    """Mark 2 against the SPY series given (price-only SPY: information, not a verdict; total-return SPY: the
    mark)."""
    a = s["cagr_pct"] >= spy["cagr_pct"] - 2 and s["max_drawdown_pct"] <= 0.75 * spy["max_drawdown_pct"]
    b = s["cagr_pct"] >= spy["cagr_pct"] + 1 and s["max_drawdown_pct"] <= 1.1 * spy["max_drawdown_pct"]
    return bool(a or b)


mark2 = mark2_price_only  # the same arithmetic; what makes it the mark is SPY's total return


def gate_b(base: Mapping, at_2x: Mapping | None, at_3x: Mapping | None, spy: Mapping, control: Mapping | None,
           neighbour_stats: Mapping[str, Mapping], break_even: Mapping | None, *, spy_tr: Mapping | None = None,
           universe: str = "today", pit_base: Mapping | None = None) -> list[dict]:
    """Each mark of Gate B for the base version of a rule: pass / fail where the inputs exist, otherwise
    "cannot be judged yet (provisional data)" with what is known. Whatever the marks say, the result is
    provisional (`NOT_A_PASS`).

    spy: price-only SPY over the same sessions; spy_tr: SPY total return (the mark's benchmark; None: not in
    the data, so marks 2 and 4 stay unjudged). control: `exposure_matched_control` (judged with >= 500 runs and
    `matched`) or the entries-matched diagnostic (never judged, Amendment 1). universe: "point_in_time" when this
    run is on the point-in-time universe (mark 5 is then marks 1-2 of this base row); pit_base: the base row of a
    point-in-time run, for a run on today's list."""
    word = {True: "pass", False: "fail"}
    yrs = f"{base['positive_years']} of {base['n_years']} calendar years positive"
    out = [{"mark": 1, "verdict": word[mark1(base)], "detail": f"net return {base['return_pct']}%, {yrs}"}]
    po = (f"against price-only SPY (CAGR {spy['cagr_pct']}%, max drawdown {spy['max_drawdown_pct']}%) it would "
          f"{word[mark2_price_only(base, spy)]}")
    if spy_tr is None:
        out.append({"mark": 2, "verdict": CANNOT,
                    "detail": f"no SPY total return in the data (daily store, S84). CAGR {base['cagr_pct']}%, max "
                              f"drawdown {base['max_drawdown_pct']}%; " + po})
    else:
        out.append({"mark": 2, "verdict": word[mark2(base, spy_tr)],
                    "detail": f"CAGR {base['cagr_pct']}%, max drawdown {base['max_drawdown_pct']}% against SPY total "
                              f"return CAGR {spy_tr['cagr_pct']}%, max drawdown {spy_tr['max_drawdown_pct']}%; "
                              + po})
    if control is None or control.get("sharpe_percentile") is None:
        out.append({"mark": 3, "verdict": CANNOT, "detail": "no control runs"})
    elif control.get("kind") != "exposure_matched":
        out.append({"mark": 3, "verdict": CANNOT,
                    "detail": f"only the entries-matched control (diagnostic, Amendment 1): net Sharpe at percentile "
                              f"{control['sharpe_percentile']} of {control['runs']} runs"})
    else:
        ok = control["sharpe_percentile"] >= CONTROL_PERCENTILE
        few = control["runs"] < MIN_CONTROL_RUNS
        gap = control.get("committed_exposure_gap_pts") or {}
        why = (f"; the mark needs >= {MIN_CONTROL_RUNS} runs, this many would {word[ok]}" if few else
               f"; not matched (a slot without a free name, or committed exposure off by up to {gap.get('max')} "
               f"points, more than {control.get('exposure_tolerance_pts')})" if not control.get("matched") else "")
        out.append({"mark": 3, "verdict": CANNOT if few or not control.get("matched") else word[ok],
                    "detail": f"net Sharpe at percentile {control['sharpe_percentile']} of {control['runs']} "
                              f"exposure-matched random portfolios (lower of the rule's run "
                              f"{control.get('sharpe_percentile_rule_run')} and its names replayed "
                              f"{control.get('sharpe_percentile_replayed_names')}; CAGR at percentile "
                              f"{control['cagr_percentile']}; committed exposure within {gap.get('max')} points)"
                              + why})
    if at_2x is None:
        out.append({"mark": 4, "verdict": CANNOT, "detail": "no 2x run"})
    else:
        be = "" if not break_even else (f"; break-even at {break_even['multiple']}x costs ({break_even['note']}; "
                                        f"{break_even['trades_fixed_multiple']}x with the 1x trades held fixed)"
                                        if break_even.get("multiple") is not None
                                        else f"; break-even: {break_even['note']}")
        x3 = "" if at_3x is None else f"; at 3x: net return {at_3x['return_pct']}%, CAGR {at_3x['cagr_pct']}%"
        m1 = mark1(at_2x)
        head = (f"at 2x costs mark 1 {'holds' if m1 else 'fails'} (net return {at_2x['return_pct']}%, "
                f"{at_2x['positive_years']} of {at_2x['n_years']} years positive)")
        if spy_tr is None:
            out.append({"mark": 4, "verdict": CANNOT if m1 else "fail",
                        "detail": head + f" and mark 2 against price-only SPY would "
                                         f"{word[mark2_price_only(at_2x, spy)]}" + x3 + be})
        else:
            m2 = mark2(at_2x, spy_tr)
            out.append({"mark": 4, "verdict": word[m1 and m2],
                        "detail": head + f" and mark 2 against SPY total return {'holds' if m2 else 'fails'} "
                                         f"(CAGR {at_2x['cagr_pct']}%, max drawdown {at_2x['max_drawdown_pct']}%)"
                                  + x3 + be})
    pit = base if universe == "point_in_time" else pit_base
    if pit is None:
        out.append({"mark": 5, "verdict": CANNOT,
                    "detail": "today's stock list only; run it on the point-in-time universe (`--universe pit`)"})
    elif spy_tr is None:
        out.append({"mark": 5, "verdict": CANNOT if mark1(pit) else "fail",
                    "detail": f"point-in-time base row: mark 1 {'holds' if mark1(pit) else 'fails'}; mark 2 needs "
                              "SPY total return"})
    else:
        m1, m2 = mark1(pit), mark2(pit, spy_tr)
        out.append({"mark": 5, "verdict": word[m1 and m2],
                    "detail": f"point-in-time base row: mark 1 {'holds' if m1 else 'fails'} (net return "
                              f"{pit['return_pct']}%, {pit['positive_years']} of {pit['n_years']} years positive), "
                              f"mark 2 {'holds' if m2 else 'fails'} (CAGR {pit['cagr_pct']}%, max drawdown "
                              f"{pit['max_drawdown_pct']}%)"})
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
