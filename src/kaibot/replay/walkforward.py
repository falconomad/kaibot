"""Walk-forward evaluation (story S08, tech design 6): tune on 12 months (<= 3 parameters), freeze, test on the next
month, roll monthly.

Design:
- Every calendar month is simulated ONCE for every grid configuration, from flat equity (`equity0`) on its first
  session. A fold's train score is the daily Sharpe of its 12 train months concatenated; its test result is the
  chosen configuration's month. The strategy is flat every night, so a month's daily returns do not depend on
  earlier months except through the equity level (sizing is a % of equity) and the run-drawdown stop, which
  therefore resets monthly (stated in the report).
- Months are processed in calendar order. The choice for test month M is made from the stats of months < M before
  M is simulated, so no test-window bar can reach the choice (and a test checks it by perturbing test prices).
- Memory: bars are loaded per block of `block_months` months plus a `warmup_days` warm-up (the longest daily
  window is the 60-session beta), features are computed once per block at the loosest z_max of the grid and the
  candidate set of each stricter z_max is a filter on the `z` column (exact: the candidate mask is a conjunction).
Pure: no I/O, clock or randomness here; the loader is injected.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kaibot.evaluation.metrics import sharpe
from kaibot.replay.data import SymbolDays
from kaibot.replay.events import events_digest, input_digest
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import Book, run_portfolio
from kaibot.risk.gate import RiskPolicy

WARMUP_DAYS = 120
D1 = np.timedelta64(1, "D")
M1 = np.timedelta64(1, "M")


@dataclass(frozen=True, order=True)
class Params:
    """The <= 3 tunable parameters. Defaults are today's pre-registered variant A values. The last three fields
    belong to the named experiments (issue #109); 0 switches each off, which is variant A as it always ran."""

    z_max: float = -1.5  # dip threshold, sigma since the open
    atr_mult: float = 1.5  # stop distance in 5-minute ATRs (1R)
    target_r: float = 2.0  # profit target in R
    entry_cutoff_m: int = 0  # last minute an entry may fill, counted from 09:30 (60 = 10:30 ET); 0 = no cutoff
    vix_min: float = 0.0  # enter only when the previous day's VIX close is above this; 0 = no filter
    size_atr: float = 0.0  # size as if the stop were this many ATRs away (same $ risk with a wider stop); 0 = off

    @property
    def label(self) -> str:
        base = f"z{self.z_max:g}_atr{self.atr_mult:g}_tr{self.target_r:g}"
        extra = ((f"_cut{self.entry_cutoff_m}" if self.entry_cutoff_m else "")
                 + (f"_vix{self.vix_min:g}" if self.vix_min else "")
                 + (f"_size{self.size_atr:g}" if self.size_atr else ""))
        return base + extra


DEFAULT = Params()

GRIDS: dict[str, dict[str, tuple[float, ...]]] = {
    # 3 x 3 x 3 = 27 configurations around the pre-registered values
    "default": {"z_max": (-1.5, -1.75, -2.0), "atr_mult": (1.0, 1.5, 2.0), "target_r": (1.5, 2.0, 3.0)},
    "small": {"z_max": (-1.5, -2.0), "atr_mult": (1.5, 2.0), "target_r": (2.0,)},
}


TUNABLE = ("z_max", "atr_mult", "target_r", "entry_cutoff_m", "vix_min")  # size_atr is fixed per experiment


def make_grid(spec: Mapping[str, Sequence[float]], default: Params = DEFAULT) -> list[Params]:
    """Cartesian grid in a fixed order with `default` first (ties in the selection go to the earliest entry).
    Axes not in `spec` stay at `default`'s value."""
    if len(spec) > 3 or set(spec) - set(TUNABLE):
        raise ValueError(f"at most 3 parameters out of: {', '.join(TUNABLE)}")
    grid = [default]
    for k in TUNABLE:
        if k in spec:
            grid = [dataclasses.replace(p, **{k: v}) for p in grid for v in spec[k]]
    if default not in grid:
        raise ValueError(f"grid must contain the pre-registered default {default}")
    return [default] + [p for p in grid if p != default]


@dataclass(frozen=True)
class Experiment:
    """A named, pre-declared walk-forward: its fixed default and the small grid tuned on the train months."""

    default: Params
    axes: Mapping[str, tuple[float, ...]]
    what: str

    @property
    def grid(self) -> list[Params]:
        return make_grid(self.axes, self.default)


# Issue #109. Everything not named stays at variant A's defaults (z -1.5, stop 1.5 ATR, target 2R). Minutes count
# from 09:30 ET: 30 = 10:00, 60 = 10:30, 90 = 11:00.
EXPERIMENTS: dict[str, Experiment] = {
    "control": Experiment(DEFAULT, GRIDS["default"], "variant A as run in docs/m0/walk_forward_10y.md"),
    "mornings": Experiment(Params(entry_cutoff_m=60), {"entry_cutoff_m": (30, 60, 90)},
                           "no entry after 10:30 ET (cutoff tuned over 10:00 / 10:30 / 11:00)"),
    "wide_stop": Experiment(Params(atr_mult=3.0, size_atr=1.5), {"atr_mult": (2.5, 3.0, 4.0)},
                            "stop 3 ATR (tuned over 2.5 / 3 / 4) with the dollar risk of the 1.5 ATR stop"),
    "high_vix": Experiment(Params(vix_min=22.0), {"vix_min": (18.0, 22.0, 26.0)},
                           "entries only when yesterday's VIX close is above 22 (tuned over 18 / 22 / 26)"),
    "mornings_wide_stop": Experiment(Params(atr_mult=3.0, size_atr=1.5, entry_cutoff_m=60),
                                     {"entry_cutoff_m": (30, 60, 90), "atr_mult": (2.5, 3.0, 4.0)},
                                     "mornings and wide_stop together (3 x 3 configs)"),
}


def filter_candidates(cands: pd.DataFrame, regime: Mapping, p: Params) -> pd.DataFrame:
    """The candidates configuration `p` may enter. Each rule only removes rows, using the decision minute and the
    previous day's VIX close (both known before the entry), so the simulator sees a plain, smaller candidate set."""
    if not len(cands):
        return cands
    keep = cands["z"].to_numpy() <= p.z_max
    if p.entry_cutoff_m:  # decided at the close of minute m, filled at the open of m + 1
        keep = keep & (cands["m"].to_numpy() + 1 <= p.entry_cutoff_m)
    if p.vix_min:
        ok = np.array([d for d, (_, vix) in regime.items() if vix > p.vix_min], dtype="datetime64[D]")  # NaN: no
        keep = keep & np.isin(cands["date"].to_numpy().astype("datetime64[D]"), ok)
    return cands[keep]


# ---------------------------------------------------------------- folds


def _month(d) -> np.datetime64:
    return np.datetime64(d, "M")


def month_bounds(m: np.datetime64) -> tuple[np.datetime64, np.datetime64]:
    m = _month(m)
    return m.astype("datetime64[D]"), (m + M1).astype("datetime64[D]") - D1


@dataclass(frozen=True)
class Fold:
    index: int
    train_months: tuple[np.datetime64, ...]  # datetime64[M], consecutive
    test_month: np.datetime64  # datetime64[M]

    @property
    def train_start(self) -> np.datetime64:
        return month_bounds(self.train_months[0])[0]

    @property
    def train_end(self) -> np.datetime64:
        return month_bounds(self.train_months[-1])[1]

    @property
    def test_start(self) -> np.datetime64:
        return month_bounds(self.test_month)[0]

    @property
    def test_end(self) -> np.datetime64:
        return month_bounds(self.test_month)[1]


def first_train_month(data_start, warmup_days: int = WARMUP_DAYS) -> np.datetime64:
    """First whole month that starts after the warm-up (so the first fold's features are fully defined)."""
    d = np.datetime64(data_start, "D") + np.timedelta64(warmup_days, "D")
    m = _month(d)
    return m if m.astype("datetime64[D]") == d else m + M1


def make_folds(first_train, end, train_months: int = 12) -> list[Fold]:
    """Rolling folds: train on `train_months` whole months, test on the next month, roll by one month.
    The last test month may be partial (it ends at `end`); test months never overlap and never touch train."""
    if train_months < 1:
        raise ValueError("train_months >= 1")
    m0, last = _month(first_train), _month(end)
    out = []
    test = m0 + np.timedelta64(train_months, "M")
    while test <= last:
        out.append(Fold(len(out), tuple(test - np.timedelta64(train_months - k, "M") for k in range(train_months)),
                        test))
        test = test + M1
    return out


# ---------------------------------------------------------------- per-month simulation


@dataclass(frozen=True)
class MonthStats:
    month: np.datetime64
    dates: np.ndarray  # datetime64[D] sessions simulated
    rets: np.ndarray  # daily return on the session's start equity, after costs
    trades: int
    events_sha256: str


def month_stats(month: np.datetime64, book: Book) -> MonthStats:
    dates = np.array([np.datetime64(r["date"], "D") for r in book.daily], dtype="datetime64[D]")
    rets = np.array([r["pnl"] / r["start"] for r in book.daily], dtype=float)
    return MonthStats(_month(month), dates, rets, len(book.trades), events_digest(book))


def simulate_months(bars: Mapping[str, SymbolDays], cands: pd.DataFrame, regime: Mapping, fomc: set,
                    pol: RiskPolicy, months: Sequence[np.datetime64], grid: Sequence[Params], variant: str,
                    equity0: float, end=None) -> Iterator[tuple[np.datetime64, dict[Params, Book]]]:
    """Yield (month, {params: Book}) for each month, each config from flat `equity0`. `cands` must be computed at
    the loosest z_max in the grid (stricter ones are filters on `z`)."""
    for m in months:
        lo, hi = month_bounds(m)
        if end is not None:
            hi = min(hi, np.datetime64(end, "D"))
        reg = {d: v for d, v in regime.items() if lo <= d <= hi}
        cm = cands[(cands["date"] >= lo) & (cands["date"] <= hi)] if len(cands) else cands
        subset: dict[tuple, pd.DataFrame] = {}
        books = {}
        for p in grid:
            key = (p.z_max, p.entry_cutoff_m, p.vix_min)
            if key not in subset:
                subset[key] = filter_candidates(cm, reg, p)
            books[p] = run_portfolio(bars, subset[key], reg, fomc, pol, equity0=equity0, atr_mult=p.atr_mult,
                                     variant=variant, target_r=p.target_r, size_atr=p.size_atr or None)
        yield m, books



# ---------------------------------------------------------------- selection


@dataclass(frozen=True)
class Selection:
    params: Params
    train_sharpe_ann: float
    train_trades: int
    eligible: int  # configurations that met the trade floor
    fallback: bool  # no configuration met the floor -> the default was used


def train_score(stats: Sequence[MonthStats]) -> tuple[float, int]:
    rets = np.concatenate([s.rets for s in stats]) if stats else np.array([])
    return sharpe(rets) * math.sqrt(252), int(sum(s.trades for s in stats))


def choose(train: Mapping[Params, Sequence[MonthStats]], grid: Sequence[Params], min_trades: int,
           default: Params = DEFAULT) -> Selection:
    """Highest train daily Sharpe (after costs) among configs with >= `min_trades` train trades; ties -> grid
    order. Reads only `train` (the fold's train months), so the choice cannot see the test month."""
    best, best_s, best_n, eligible = None, -math.inf, 0, 0
    for p in grid:
        s, n = train_score(train[p])
        if n < min_trades:
            continue
        eligible += 1
        if s > best_s:
            best, best_s, best_n = p, s, n
    if best is None:
        s, n = train_score(train[default])
        return Selection(default, s, n, 0, True)
    return Selection(best, best_s, best_n, eligible, False)


# ---------------------------------------------------------------- driver


@dataclass
class Window:
    stocks: dict[str, SymbolDays | None]
    etfs: dict[str, SymbolDays | None]
    spy: SymbolDays


@dataclass
class FoldResult:
    fold: Fold
    selection: Selection
    tuned: Book
    default: Book
    tuned_stats: MonthStats
    default_stats: MonthStats


@dataclass
class WalkForward:
    folds: list[FoldResult]
    grid: list[Params]
    stats: dict[np.datetime64, dict[Params, MonthStats]]  # every simulated month x config
    block_input_sha256: list[str] = field(default_factory=list)
    spy_close: dict[np.datetime64, float] = field(default_factory=dict)  # session -> SPY close (OOS + prior)
    candidates: int = 0

    @property
    def input_sha256(self) -> str:
        return hashlib.sha256("\n".join(self.block_input_sha256).encode()).hexdigest()

    def events_sha256(self, which: str = "tuned") -> str:
        h = hashlib.sha256()
        for f in self.folds:
            h.update((f.tuned_stats if which == "tuned" else f.default_stats).events_sha256.encode() + b"\n")
            if which == "tuned":
                h.update(f.selection.params.label.encode() + b"\n")
        return h.hexdigest()

    def oos_matrix(self) -> tuple[np.ndarray, np.ndarray]:
        """(sessions x configs) daily returns over the test months, for PBO across the grid."""
        dates = np.concatenate([self.stats[f.fold.test_month][self.grid[0]].dates for f in self.folds])
        m = np.column_stack([np.concatenate([self.stats[f.fold.test_month][p].rets for f in self.folds])
                             for p in self.grid])
        return dates, m


def run_walk_forward(load: Callable[[np.datetime64, np.datetime64], Window], sector_of: Mapping[str, str],
                     news: Mapping[str, np.ndarray], regime_fn: Callable[[SymbolDays], Mapping], fomc: set,
                     pol: RiskPolicy, folds: Sequence[Fold], grid: Sequence[Params], *, variant: str = "A",
                     default: Params = DEFAULT, equity0: float = 100_000.0, min_train_trades: int = 100,
                     block_months: int = 3, warmup_days: int = WARMUP_DAYS, end=None,
                     digest_params: Mapping | None = None) -> WalkForward:
    """Simulate every month from the first train month to the last test month for every config (loading
    `block_months` at a time), choosing each fold's parameters from its train months before its test month runs."""
    if not folds:
        raise ValueError("no folds: the range is shorter than train + 1 month")
    grid = list(grid)
    if default not in grid:
        raise ValueError("grid must contain the default")
    by_test = {f.test_month: f for f in folds}
    first, last = folds[0].train_months[0], folds[-1].test_month
    months = [first + np.timedelta64(k, "M") for k in range(int((last - first).astype(int)) + 1)]
    z_loose = max(p.z_max for p in grid)
    wf = WalkForward([], grid, {})
    pending: dict[np.datetime64, Selection] = {}
    for b in range(0, len(months), block_months):
        block = months[b:b + block_months]
        lo = month_bounds(block[0])[0]
        hi = month_bounds(block[-1])[1]
        if end is not None:
            hi = min(hi, np.datetime64(end, "D"))
        win = load(lo - np.timedelta64(warmup_days, "D"), hi)
        news_w = {s: t[(t >= (lo - np.timedelta64(warmup_days + 1, "D")).astype("datetime64[s]"))
                       & (t <= (hi + D1).astype("datetime64[s]"))]
                  for s, t in news.items()}
        prep = prepare(win.stocks, win.etfs, sector_of, news_w, lo, hi, z_max=z_loose, arrays=False)
        wf.candidates += len(prep.cands)
        regime = regime_fn(win.spy)
        sdates, sclose = win.spy.dates, win.spy.c[:, -1]
        for i, d in enumerate(sdates):
            if lo <= d <= hi:
                if i > 0:
                    wf.spy_close[sdates[i - 1]] = float(sclose[i - 1])
                wf.spy_close[d] = float(sclose[i])
        wf.block_input_sha256.append(input_digest(
            {**prep.bars, "SPY": win.spy, **{f"ETF:{e}": sd for e, sd in win.etfs.items() if sd is not None}},
            news={s: news_w[s] for s in prep.bars if s in news_w},
            params={"block": [str(lo), str(hi)], **dict(digest_params or {})}))
        for m in block:
            fold = by_test.get(m)
            if fold is not None:  # choose BEFORE the test month is simulated, from train months only
                pending[m] = choose({p: [wf.stats[t][p] for t in fold.train_months] for p in grid}, grid,
                                    min_train_trades, default)
            ((_, books),) = simulate_months(prep.bars, prep.cands, regime, fomc, pol, [m], grid, variant, equity0,
                                            end=end)
            wf.stats[m] = {p: month_stats(m, bk) for p, bk in books.items()}
            if fold is not None:
                sel = pending.pop(m)
                wf.folds.append(FoldResult(fold, sel, books[sel.params], books[default], wf.stats[m][sel.params],
                                           wf.stats[m][default]))
            # keep only what later folds and the report need: per-config stats (small), not the books
            del books
        del win, prep
    return wf


def pbo_blocks(t: int, wanted: int = 16) -> int:
    s = wanted
    while s > 2 and t < 2 * s:
        s -= 2
    return s
