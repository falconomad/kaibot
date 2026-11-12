"""The four pre-declared multi-day rules of issue #113 and their walk-forward. Research only.

Every rule is a small object with two hooks the engine calls (`at_open`, `at_close`); all its inputs are arrays
whose row t uses sessions <= t, and a hook for session i reads rows <= i - 1 (open) or the 15:55 price of i
(close). Grids are declared here, before any run; nothing is tuned on a test month.

Walk-forward (the same 115 folds as scripts/walk_forward.py: train 12 months, test the next month, roll monthly,
selection by train Sharpe after costs):
- every configuration runs ONCE as its own continuous paper account from the first train month; a fold's train
  score is that account's daily Sharpe over the 12 train months;
- the tuned result is ONE account that starts flat at the first test month: on each month's first session the
  configuration chosen from the 12 months before takes over NEW entries, and positions already open keep the
  stop / holding settings they were opened with. Equity and positions are carried across months; nothing is
  force-closed at a fold boundary.
  Bias of that choice: the months are not independent draws (a position opened in month M can decide part of
  M + 1), and a train score includes the tail of positions opened just before the train window. Both use only
  the past. Force-closing at month ends would have added about 12 extra round trips of cost per position-year
  and cut every hold at about 21 sessions, which is not the rule being tested.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kaibot.replay.multiday import Caps, DailyBars, Engine, Indicators, MBook, MTrade, closes
from kaibot.replay.walkforward import Fold, MonthStats, Selection, choose
from kaibot.risk.gate import RiskPolicy

MIN_PRICE, MIN_ADV = 5.0, 50e6  # the level-independent universe filter of config/profiles.yaml, point-in-time
SESSIONS_PER_MONTH = 21
BALANCED_CAPS = Caps(max_positions=5, max_exposure_pct=60.0)


@dataclass(frozen=True, order=True)
class Cfg:
    rule: str
    params: tuple[tuple[str, float], ...]

    def get(self, k: str) -> float:
        return dict(self.params)[k]

    @property
    def label(self) -> str:
        return self.rule + "".join(f"_{k}{v:g}" for k, v in self.params)


@dataclass(frozen=True)
class MExperiment:
    default: Cfg
    axes: Mapping[str, tuple[float, ...]]
    what: str
    fixed: str  # the pre-declared setting in words

    @property
    def grid(self) -> list[Cfg]:
        grid = [()]
        for k, vals in self.axes.items():
            grid = [g + ((k, float(v)),) for g in grid for v in vals]
        cfgs = [Cfg(self.default.rule, g) for g in grid]
        if self.default not in cfgs or len(cfgs) > 9:
            raise ValueError("grid must contain the default and have at most 9 configurations")
        return [self.default] + [c for c in cfgs if c != self.default]  # ties in the selection go to the default


def _cfg(rule: str, **kw: float) -> Cfg:
    return Cfg(rule, tuple((k, float(v)) for k, v in kw.items()))


EXPERIMENTS: dict[str, MExperiment] = {
    "trend_highs": MExperiment(
        _cfg("trend_highs", high_n=55, trail_atr=3), {"high_n": (20, 55, 100), "trail_atr": (2, 3, 4)},
        "buy at the next open a stock that closes above its highest close of the previous 55 sessions (20 / 55 / "
        "100), above its 200-day average, with a 20-day return above SPY's; stop and trailing stop 3 daily ATRs "
        "(2 / 3 / 4); out after 40 sessions; at most 5 positions and 60% of equity",
        "55-day high, 3 ATR"),
    "rel_strength": MExperiment(
        _cfg("rel_strength", months=6, top_n=5), {"months": (3, 6, 9), "top_n": (5, 10)},
        "on each week's first session hold the top 5 (5 / 10) stocks by 6-month (3 / 6 / 9) return skipping the "
        "last month, equal risk, replaced weekly; flat when SPY closed the week under its 200-day average; a "
        "position is sold after 60 sessions at the latest; at most 60% of equity",
        "6 months, top 5"),
    "winners_run": MExperiment(
        _cfg("winners_run", hold=5), {"hold": (3, 5, 10)},
        "variant A's intraday dip entry unchanged; a trade still open and up at least 0.5R at 15:55 is kept "
        "overnight (the part that fits the gap sizing) with a stop 2 daily ATRs under the highest close, for at "
        "most 5 sessions (3 / 5 / 10); everything else is sold at 15:55 as before; at most 3 carried positions "
        "and 25% of equity (the intraday leg can use the other 75%)",
        "5 sessions"),
    "overnight_spy": MExperiment(
        _cfg("overnight_spy", vix_max=30), {"vix_max": (0, 25, 30)},
        "buy SPY at the close and sell it at the next open, only when the previous day's VIX close is under 30 "
        "(no filter / 25 / 30); one position, sized like every other (a 3 ATR gap costs at most the per-trade risk, "
        "and never more than 15% of equity)",
        "VIX < 30"),
}


# ---------------------------------------------------------------- rules


class TrendHighs:
    name = "trend_highs"
    max_hold = 40

    def __init__(self, bars: DailyBars, ind: Indicators, stocks: np.ndarray, spy: int) -> None:
        c = closes(bars)
        ret20 = c / c.shift(20) - 1
        self.strength = ret20.sub(ret20[spy], axis=0).to_numpy()  # 20-day return minus SPY's
        mask = np.zeros(c.shape[1], dtype=bool)
        mask[stocks] = True
        base = ((c > c.rolling(200, min_periods=200).mean()).to_numpy() & (self.strength > 0)
                & (bars.c >= MIN_PRICE) & (ind.adv >= MIN_ADV) & mask[None, :])
        self.signal = {n: base & (c > c.shift(1).rolling(n, min_periods=n).max()).to_numpy()
                       for n in sorted({int(v) for v in EXPERIMENTS[self.name].axes["high_n"]})}

    def at_open(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        if i == 0:
            return
        k = i - 1
        js = np.flatnonzero(self.signal[int(cfg.get("high_n"))][k])
        for j in js[np.argsort(-self.strength[k, js], kind="stable")]:  # strongest first when slots are short
            eng.enter(i, int(j), eq, cfg.label, BALANCED_CAPS, stop_atr=cfg.get("trail_atr"),
                      trail_atr=cfg.get("trail_atr"), max_hold=self.max_hold)

    def at_close(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        return


class RelStrength:
    name = "rel_strength"
    max_hold = 60

    def __init__(self, bars: DailyBars, ind: Indicators, stocks: np.ndarray, spy: int) -> None:
        c = closes(bars)
        skip = c.shift(SESSIONS_PER_MONTH)
        self.mom = {m: (skip / c.shift(SESSIONS_PER_MONTH * m) - 1).to_numpy()
                    for m in sorted({int(v) for v in EXPERIMENTS[self.name].axes["months"]})}
        mask = np.zeros(c.shape[1], dtype=bool)
        mask[stocks] = True
        self.ok = (bars.c >= MIN_PRICE) & (ind.adv >= MIN_ADV) & mask[None, :]
        spy_c = c[spy]
        self.market_on = (spy_c > spy_c.rolling(200, min_periods=200).mean()).to_numpy()
        self.week = (bars.dates.astype("datetime64[D]").astype(np.int64) + 3) // 7  # Monday-based week number

    def at_open(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        if i == 0 or self.week[i] == self.week[i - 1]:
            return
        k, n = i - 1, int(cfg.get("top_n"))
        target: list[int] = []
        on = bool(self.market_on[k])
        if on:
            m = self.mom[int(cfg.get("months"))][k]
            js = np.flatnonzero(np.isfinite(m) & self.ok[k])
            target = [int(j) for j in js[np.argsort(-m[js], kind="stable")][:n]]
        for j in [j for j in eng.pos if j not in target]:
            eng.sell_open(i, j, "rebalance" if on else "market_filter")
        caps = Caps(max_positions=n, max_exposure_pct=BALANCED_CAPS.max_exposure_pct)
        for j in target:  # equal risk; with 10 names each also gets at most a tenth of the 60%
            eng.enter(i, j, eq, cfg.label, caps, max_hold=self.max_hold,
                      max_value_pct=BALANCED_CAPS.max_exposure_pct / n)

    def at_close(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        return


class OvernightSpy:
    name = "overnight_spy"
    caps = Caps(max_positions=1, max_exposure_pct=60.0)

    def __init__(self, bars: DailyBars, spy: int, vix_prev_close: np.ndarray) -> None:
        self.spy, self.vix, self.n = spy, np.asarray(vix_prev_close, dtype=float), len(bars.dates)

    def at_open(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        return  # the engine sells an `exit_next_open` position at the open

    def at_close(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        thr = cfg.get("vix_max")
        if i + 1 >= self.n or (thr and not self.vix[i] < thr):  # NaN VIX with a threshold: no trade
            return
        eng.enter(i, self.spy, eq, cfg.label, self.caps, at="close", exit_next_open=True)


class WinnersRun:
    """Variant A's intraday trades (from the unchanged `sim.run_portfolio`, one month at a time from flat
    equity, exactly as the control experiment) plus the overnight leg. Simplifications, all stated in the report:
    - the intraday leg does not see the carried positions (its position count and daily stop are its own);
    - every intraday trade is scaled by (this account's equity) / (the intraday replay's own equity that
      morning), so without a carry each day's return is exactly the control's; costs scale with it pro rata;
    - a partly carried trade's 15:55 sale is the intraday trade pro rata."""

    name = "winners_run"
    # The intraday leg can hold 5 x 15% = 75% of equity; the carried positions are bought up to 25%, so the two
    # together fit in the account when bought (the intraday leg is not re-checked against carried gains).
    caps = Caps(max_positions=3, max_exposure_pct=25.0)
    trail_atr = 2.0
    min_profit_r = 0.5

    def __init__(self, bars: DailyBars, intraday: pd.DataFrame, intraday_equity0: float = 100_000.0) -> None:
        self.b = bars
        self.by_i: dict[int, list] = {}
        if not len(intraday):
            return
        d = intraday["date"].to_numpy().astype("datetime64[D]")
        # The intraday replay's own equity at the start of each session: every month starts flat and adds the
        # P&L of the month's earlier sessions (all its trades are closed the day they are opened).
        days, inv = np.unique(d, return_inverse=True)
        day_pnl = np.bincount(inv, weights=intraday["pnl"].to_numpy(dtype=float), minlength=len(days))
        month = days.astype("datetime64[M]")
        earlier = np.zeros(len(days))
        for n in range(1, len(days)):
            if month[n] == month[n - 1]:
                earlier[n] = earlier[n - 1] + day_pnl[n - 1]
        base = (intraday_equity0 + earlier)[inv]
        idx = np.searchsorted(bars.dates, d)
        ok = (idx < len(bars.dates)) & (bars.dates[np.minimum(idx, len(bars.dates) - 1)] == d)
        for i, eq0, t in zip(idx[ok], base[ok], intraday[ok].itertuples(index=False), strict=True):
            self.by_i.setdefault(int(i), []).append((t, float(eq0)))

    def at_open(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        return

    def at_close(self, i: int, eng: Engine, cfg: Cfg, eq: float) -> None:
        rows = []
        for t, base in self.by_i.get(i, ()):
            j = self.b._col.get(t.symbol)
            px = self.b.c1555[i, j] if j is not None else math.nan
            r = ((px - t.entry) * t.qty / t.risk
                 if t.reason == "time" and t.risk > 0 and t.qty > 0 and math.isfinite(px) else -math.inf)
            rows.append((r, j, px, t, eq / base if base > 0 else 0.0))
        for r, j, px, t, k in sorted(rows, key=lambda x: -x[0]):  # the biggest winners get the overnight room first
            qty = t.qty * k  # the intraday trade at this account's size
            if qty <= 0:
                continue
            kept = 0.0
            if r >= self.min_profit_r:
                kept = eng.enter(i, j, eq, cfg.label, self.caps, at="close", trail_atr=self.trail_atr,
                                 max_hold=int(cfg.get("hold")),
                                 carry={"qty": qty, "fill": t.entry, "mid": px - t.gross_pnl / t.qty})
            f = k * (1 - kept / qty)  # the share of the replayed trade that is sold at 15:55 as before
            if f <= 0:
                continue
            eng.cash += t.pnl * f
            eng._exits_today += 1
            eng.book.trades.append(MTrade(
                t.symbol, self.b.dates[i], self.b.dates[i], 0, t.qty * f, t.entry, t.exit, t.stop, t.reason,
                t.pnl * f, t.risk * f, t.gross_pnl * f, t.cost_spread * f, t.cost_impact * f, t.cost_slippage * f,
                t.cost_fees * f, False, eq, self.name, cfg.label, "intraday"))


def make_rule(name: str, bars: DailyBars, ind: Indicators, stocks: Sequence[str], spy: str,
              vix_prev_close: np.ndarray | None = None, intraday: pd.DataFrame | None = None,
              intraday_equity0: float = 100_000.0):
    cols, s = bars.cols(stocks), bars.col(spy)
    if name == "trend_highs":
        return TrendHighs(bars, ind, cols, s)
    if name == "rel_strength":
        return RelStrength(bars, ind, cols, s)
    if name == "overnight_spy":
        if vix_prev_close is None:
            raise ValueError("overnight_spy needs the previous day's VIX close per session")
        return OvernightSpy(bars, s, vix_prev_close)
    if name == "winners_run":
        if intraday is None:
            raise ValueError("winners_run needs variant A's intraday trades")
        return WinnersRun(bars, intraday, intraday_equity0)
    raise ValueError(f"unknown rule {name}")


# ---------------------------------------------------------------- walk-forward


def book_digest(book: MBook) -> str:
    """Hash of every trade and every day's close, floats rounded to 1e-6 (same idea as replay/events.py)."""
    h = hashlib.sha256()
    for t in book.trades:
        h.update(json.dumps([t.symbol, str(t.date), str(t.exit_date), round(t.qty, 6), round(t.entry, 6),
                             round(t.exit, 6), t.reason, round(t.pnl, 6), t.config]).encode() + b"\n")
    for r in book.daily:
        h.update(json.dumps([str(r["date"]), round(r["start"], 6), round(r["end"], 6), r["positions"]]).encode())
    return h.hexdigest()


def month_stats(book: MBook) -> dict[np.datetime64, MonthStats]:
    """Per calendar month: the account's daily returns and the number of trades ENTERED that month."""
    days: dict[np.datetime64, list[dict]] = {}
    for r in book.daily:
        days.setdefault(np.datetime64(r["date"], "M"), []).append(r)
    n: dict[np.datetime64, int] = {}
    for t in book.trades:
        m = np.datetime64(t.date, "M")
        n[m] = n.get(m, 0) + 1
    return {m: MonthStats(m, np.array([r["date"] for r in rows], dtype="datetime64[D]"),
                          np.array([r["pnl"] / r["start"] for r in rows]), n.get(m, 0), "")
            for m, rows in days.items()}


@dataclass
class MultidayWF:
    rule: str
    grid: list[Cfg]
    default: Cfg
    folds: list[tuple[Fold, Selection]]
    stats: dict[Cfg, dict[np.datetime64, MonthStats]]  # every configuration's continuous account, by month
    tuned: MBook
    fixed: MBook  # the default configuration, flat at the first test month, never re-tuned
    i_test0: int
    i1: int
    schedule: dict[np.datetime64, Cfg] = field(default_factory=dict)

    def oos_matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """(test sessions x configurations) daily returns of the continuous accounts, for PBO across the grid."""
        months = [f.test_month for f, _ in self.folds if f.test_month in self.stats[self.grid[0]]]  # with sessions
        dates = np.concatenate([self.stats[self.grid[0]][m].dates for m in months])
        return dates, np.column_stack([np.concatenate([self.stats[p][m].rets for m in months]) for p in self.grid])


def walk_forward(bars: DailyBars, ind: Indicators, pol: RiskPolicy, rule, exp: MExperiment, folds: Sequence[Fold], *,
                 equity0: float = 100_000.0, min_train_trades: int = 20, hold_blk: np.ndarray | None = None,
                 entry_blk: np.ndarray | None = None, end=None, costs=None) -> MultidayWF:
    if not folds:
        raise ValueError("no folds: the range is shorter than train + 1 month")
    grid, default = exp.grid, exp.default
    i_train0 = max(bars.index_from(folds[0].train_start), 1)
    i_test0 = bars.index_from(folds[0].test_start)
    i1 = bars.index_upto(folds[-1].test_end if end is None else min(folds[-1].test_end, np.datetime64(end, "D")))
    if i_test0 > i1:
        raise ValueError(f"no session in the test months: the bars end {bars.dates[-1]}, before the first test "
                         f"month {folds[0].test_month}")

    def account(schedule, i0: int) -> MBook:
        return Engine(bars, ind, pol, rule, schedule, equity0, hold_blk, entry_blk, costs=costs).run(i0, i1)

    stats = {p: month_stats(account(lambda i, p=p: p, i_train0)) for p in grid}
    chosen: list[tuple[Fold, Selection]] = []
    schedule: dict[np.datetime64, Cfg] = {}
    for f in folds:  # reads the 12 train months only; they all end before the test month starts
        sel = choose({p: [stats[p][m] for m in f.train_months if m in stats[p]] for p in grid}, grid,
                     min_train_trades, default)
        chosen.append((f, sel))
        schedule[f.test_month] = sel.params
    months = bars.dates.astype("datetime64[M]")
    tuned = account(lambda i: schedule.get(months[i], default), i_test0)
    fixed = account(lambda i: default, i_test0)
    return MultidayWF(rule.name, grid, default, chosen, stats, tuned, fixed, i_test0, i1, schedule)


# ---------------------------------------------------------------- variant A's intraday trades (for winners_run)


def variant_a_trades(load, sector_of: Mapping[str, str], news: Mapping[str, np.ndarray], regime_fn, fomc: set,
                     pol: RiskPolicy, months: Sequence[np.datetime64], *, equity0: float = 100_000.0,
                     block_months: int = 3, end=None, costs=None) -> pd.DataFrame:
    """Every trade of variant A at its pre-registered defaults, month by month from flat equity: the same calls,
    in the same order, as the default path of `walkforward.run_walk_forward` (a test compares the two). The
    intraday replay itself is not touched."""
    import dataclasses

    from kaibot.replay.pipeline import prepare
    from kaibot.replay.sim import Trade
    from kaibot.replay.walkforward import DEFAULT, WARMUP_DAYS, month_bounds, simulate_months

    rows: list[dict] = []
    for b in range(0, len(months), block_months):
        block = months[b:b + block_months]
        lo, hi = month_bounds(block[0])[0], month_bounds(block[-1])[1]
        if end is not None:
            hi = min(hi, np.datetime64(end, "D"))
        win = load(lo - np.timedelta64(WARMUP_DAYS, "D"), hi)
        news_w = {s: t[(t >= (lo - np.timedelta64(WARMUP_DAYS + 1, "D")).astype("datetime64[s]"))
                       & (t <= (hi + np.timedelta64(1, "D")).astype("datetime64[s]"))] for s, t in news.items()}
        prep = prepare(win.stocks, win.etfs, sector_of, news_w, lo, hi, z_max=DEFAULT.z_max, arrays=False)
        regime = regime_fn(win.spy)
        for m in block:
            ((_, books),) = simulate_months(prep.bars, prep.cands, regime, fomc, pol, [m], [DEFAULT], "A", equity0,
                                            end=end, costs=costs)
            rows += [dataclasses.asdict(t) for t in books[DEFAULT].trades]
        del win, prep
    return pd.DataFrame(rows, columns=[f.name for f in dataclasses.fields(Trade)])
