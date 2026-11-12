"""Read the daily bar store (story S84, issue #124, audit E4): raw prices for orders and costs, prices adjusted
at read time for signals. Research data only: nothing in the engine imports this module.

Two views of the same stored bars:

- RAW (`DailyStore.panel`): prices and volumes as they traded. Use it for everything that touches the market:
  share counts, fills, stops, spread and fee costs, price filters, dollar volume. Shares held through a split and
  cash dividends are in the `split` and `div` arrays (`DailyPanel.carry`, `DailyPanel.hold`).
- ADJUSTED (`DailyPanel.adjusted`): for anything that compares prices across days (returns, averages, breakouts,
  ATR). `mode="split"` removes splits and stock dividends only; `mode="total"` also removes cash dividends, so
  close-to-close returns are total returns.

Point in time. `adjusted(as_of=D)` shows the history as it looked after the close of session D: sessions after D
are not there, and only corporate actions with an ex-date up to D are applied. The price on D itself is the raw
price of D. For a session t <= D:

    adjusted[t] = raw[t] x product of the factors of every action with  t < ex-date <= D

    split or stock dividend, `ratio` new shares per old share:  factor 1 / ratio   (volumes: x ratio)
    cash dividend `cash` per share:  factor 1 - cash / (last raw close before the ex-date)   (mode "total" only)

An action that goes ex after D changes nothing in that view. It follows that the series adjusted as of the last
session, cut at D, equals the series adjusted as of D times ONE constant per symbol. Ratios (returns, price over
its average, a rank of slopes) are therefore the same in both, and a fast harness may compute them once on
`adjusted()` for the whole window. Levels are not the same: turn an adjusted level into a tradable price of
session i with `level / adjusted_panel.factor[i, j]`.

Total return of a symbol (the SPY benchmark): each session's return is (close x split + dividend) / previous
close - 1, the dividend counted on its ex-date and reinvested at that close (the convention of total-return
indices). `DailyPanel.total_return`, `DailyStore.total_return`.

Conventions the data cannot settle (rare): an action takes effect at the symbol's first bar on or after its
ex-date; when a dividend and a split share an ex-date, the dividend is per share held before the split; a
dividend at least as large as the previous close is ignored and counted in `notes["bad_dividends"]`.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from kaibot.ingest.daily_bars import (
    ADJUSTING_TYPES,
    SHARE_TYPES,
    actions_path,
    bars_path,
    holes_path,
    read_meta,
    ref_path,
    store_keys,
)

FIELDS = ("o", "h", "l", "c", "v", "c1555", "v_first", "v_last")  # the fields of the multi-day harness's DailyBars
PRICES = ("o", "h", "l", "c", "c1555")
VOLUMES = ("v", "v_first", "v_last")
MODES = ("split", "total")
SOURCES = ("table", "reference", "auto")
SCALE_TOL = 0.05  # a minute-derived close further than this from the stored close is on another split basis
MINUTE_SHARE = 0.02  # first / last minute as a share of the day's volume when no minute history is given


def _columns(path: Path, columns: Sequence[str] | None = None) -> dict[str, np.ndarray]:
    t = pq.read_table(path, columns=list(columns) if columns else None)
    out = {}
    for name in t.column_names:
        col = t.column(name).combine_chunks()
        a = col.to_numpy(zero_copy_only=False)
        out[name] = a.astype("datetime64[D]") if pa.types.is_date32(col.type) else a
    return out


def _day(d) -> np.datetime64:
    return np.datetime64(d, "D")


def _prev_close(c: np.ndarray) -> np.ndarray:
    """Last close before each row (carried over sessions without a bar); NaN in the first row."""
    prev = np.full_like(c, np.nan)
    prev[1:] = pd.DataFrame(c).ffill().to_numpy()[:-1]
    return prev


def _after(step: np.ndarray) -> np.ndarray:
    """Product of `step` over the rows AFTER each row (1.0 in the last row)."""
    out = np.ones_like(step)
    if len(step) > 1:
        out[:-1] = np.cumprod(step[::-1], axis=0)[::-1][1:]
    return out


def event_arrays(dates: np.ndarray, has_bar: np.ndarray, events: dict[int, Iterable[tuple]],
                 ) -> tuple[np.ndarray, np.ndarray]:
    """(split, div) arrays, sessions x symbols. `events[j]` is (ex_date, ratio, cash) tuples of column j: `ratio`
    new shares per old share (NaN: none), `cash` per share (NaN: none). An event lands on the column's first bar
    on or after its ex-date; one before the first session or after the last bar is not in the panel."""
    split = np.ones(has_bar.shape)
    div = np.zeros(has_bar.shape)
    for j, evs in events.items():
        rows = np.flatnonzero(has_bar[:, j])
        for ex, ratio, cash in evs:
            ex = _day(ex)
            if not len(rows) or ex < dates[0]:
                continue
            k = np.searchsorted(dates[rows], ex)
            if k >= len(rows):
                continue
            if ratio > 0:
                split[rows[k], j] *= ratio
            if cash > 0:
                div[rows[k], j] += cash
    return split, div


def table_factors(c: np.ndarray, split: np.ndarray, div: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Price factors as of the last row: adjusted = raw x factor. Returns (split only, split and dividends, number
    of dividends ignored because they are not smaller than the close before them)."""
    prev = _prev_close(c)
    paid = div > 0
    usable = paid & np.isfinite(prev) & (div < prev)
    step = np.where(usable, 1.0 - div / np.where(usable, prev, 1.0), 1.0)
    bad = int((paid & np.isfinite(prev) & ~usable).sum())
    return _after(1.0 / split), _after(step / split), bad


@dataclass
class DailyPanel:
    """(sessions x symbols) arrays, NaN where a symbol has no bar. `kind` is "raw", "split" or "total"."""

    dates: np.ndarray  # datetime64[D], sorted
    symbols: list[str]  # store keys
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    c1555: np.ndarray  # 15:55 close from the minute history (NaN without it: never the official close)
    v_first: np.ndarray  # shares in the first / last regular-hours minute (see `DailyStore.panel`)
    v_last: np.ndarray
    split: np.ndarray  # shares per share held, taking effect at this session's open (1.0: nothing)
    div: np.ndarray  # cash per share held at the previous close, ex-date this session (0.0: nothing)
    pf_split: np.ndarray  # price factor of the corporate-actions table: splits and stock dividends
    pf_total: np.ndarray  # ... and cash dividends
    ref_split: np.ndarray  # the same two factors implied by Alpaca's own adjusted bars (NaN: no reference)
    ref_total: np.ndarray
    flagged: frozenset = frozenset()  # keys whose table disagrees with Alpaca's adjusted bars (the last check)
    kind: str = "raw"
    as_of: np.datetime64 | None = None  # adjusted panels: the decision date
    adj_source: dict = field(default_factory=dict)  # adjusted panels: symbol -> "table" | "reference"
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._col = {s: j for j, s in enumerate(self.symbols)}

    @classmethod
    def make(cls, dates, symbols: Sequence[str], o, h, l, c, v=None, actions: Iterable[tuple] = (),  # noqa: E741
             minute_share: float | None = MINUTE_SHARE) -> DailyPanel:
        """A raw panel from plain (sessions x symbols) arrays and (symbol, ex_date, ratio, cash) actions, without
        a store: for tests and synthetic studies. `ratio` and `cash` may be None. Volume defaults to 5M shares."""
        dates = np.asarray(dates, dtype="datetime64[D]")
        symbols = list(symbols)
        c = np.asarray(c, dtype=float)
        arr = {k: np.full(c.shape, np.nan) for k in FIELDS}
        arr.update(o=np.asarray(o, dtype=float), h=np.asarray(h, dtype=float), l=np.asarray(l, dtype=float), c=c,
                   v=np.where(np.isfinite(c), 5e6, np.nan) if v is None else np.asarray(v, dtype=float))
        col = {s: j for j, s in enumerate(symbols)}
        events: dict[int, list[tuple]] = {}
        for sym, ex, ratio, cash in actions:
            events.setdefault(col[sym], []).append((ex, np.nan if ratio is None else ratio,
                                                    np.nan if cash is None else cash))
        return _assemble(dates, symbols, arr, events, np.full(c.shape, np.nan), np.full(c.shape, np.nan),
                         frozenset(), minute_share, {})

    # -- the same helpers as the harness's DailyBars

    def col(self, symbol: str) -> int:
        return self._col[symbol]

    def cols(self, symbols: Iterable[str]) -> np.ndarray:
        return np.array([self._col[s] for s in symbols if s in self._col], dtype=int)

    def index_from(self, d) -> int:
        """First session on or after `d`."""
        return int(np.searchsorted(self.dates, _day(d), side="left"))

    def index_upto(self, d) -> int:
        """Last session on or before `d` (-1: none)."""
        return int(np.searchsorted(self.dates, _day(d), side="right")) - 1

    def to_frame(self) -> pd.DataFrame:
        """Long format (symbol, date, o, h, l, c, v, c1555, v_first, v_last): what `DailyBars.from_frame` reads."""
        di, sj = np.nonzero(np.isfinite(self.c))
        out = {"symbol": np.array(self.symbols, dtype=object)[sj], "date": self.dates[di].astype("datetime64[ns]")}
        out.update({k: getattr(self, k)[di, sj] for k in FIELDS})
        return pd.DataFrame(out)

    # -- adjustment

    @property
    def factor(self) -> np.ndarray:
        """adjusted price = raw price x factor (ones on a raw panel). Divide an adjusted level by `factor[i, j]`
        to get the tradable price of session i."""
        return {"split": self.pf_split, "total": self.pf_total}.get(self.kind, np.ones_like(self.c))

    def _factors(self, i: int, source: str) -> tuple[np.ndarray, np.ndarray, dict]:
        """(split, total) price factors as of row i. The table's are rebuilt from the actions up to row i only,
        so nothing after row i enters the arithmetic at all; the reference's are a ratio to row i."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        later = np.arange(len(self.dates))[:, None] > i
        t_split, t_total, _ = table_factors(self.c, np.where(later, 1.0, self.split), np.where(later, 0.0, self.div))
        has_ref = np.isfinite(self.ref_total).all(axis=0) & np.isfinite(self.ref_split).all(axis=0)
        want = (np.zeros(len(self.symbols), dtype=bool) if source == "table" else
                np.array([source == "reference" or s in self.flagged for s in self.symbols], dtype=bool))
        use = want & has_ref
        with np.errstate(invalid="ignore"):
            r_split, r_total = self.ref_split / self.ref_split[i], self.ref_total / self.ref_total[i]
        return (np.where(use, r_split, t_split), np.where(use, r_total, t_total),
                {s: "reference" if u else "table" for s, u in zip(self.symbols, use, strict=True)})

    def adjusted(self, mode: str = "total", as_of=None, source: str = "table") -> DailyPanel:
        """Prices and volumes as they looked after the close of `as_of` (default: the last session).

        mode    "split": splits and stock dividends; "total": cash dividends too (returns are total returns)
        source  "table": the corporate-actions table (the default and the documented rule above);
                "reference": the factor implied by Alpaca's own `adjustment=all` bars of the same fetch, which
                also covers spin-offs (ratio of two stored series, so it carries their rounding);
                "auto": the table, except for keys the last check flagged, which use the reference.
                A symbol without a reference always uses the table; `adj_source` says what was used.
        """
        if self.kind != "raw":
            raise ValueError("adjust the raw panel, not an adjusted one")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        i = len(self.dates) - 1 if as_of is None else self.index_upto(as_of)
        if i < 0:
            raise ValueError(f"as_of {as_of} is before the first session {self.dates[0]}")
        f_split, f_total, used = self._factors(i, source)
        hidden = np.arange(len(self.dates))[:, None] > i
        price = np.where(hidden, np.nan, f_total if mode == "total" else f_split)
        volume = np.where(hidden, np.nan, 1.0 / f_split)
        arrays = {k: getattr(self, k) * price for k in PRICES}
        arrays.update({k: getattr(self, k) * volume for k in VOLUMES})
        return replace(self, **arrays, split=np.where(hidden, 1.0, self.split), div=np.where(hidden, 0.0, self.div),
                       pf_split=np.where(hidden, np.nan, f_split), pf_total=np.where(hidden, np.nan, f_total),
                       kind=mode, as_of=self.dates[i], adj_source=used)

    # -- positions and total return (raw panel)

    def carry(self, i: int, j: int, qty: float) -> tuple[float, float]:
        """A position of `qty` shares held at the close of session i - 1, at the open of session i:
        (shares now held, cash dividend credited). Divide a resting stop by `split[i, j]` at the same moment."""
        self._raw_only()
        return qty * float(self.split[i, j]), qty * float(self.div[i, j])

    def hold(self, j: int, i_entry: int, i_exit: int, qty: float) -> tuple[float, float]:
        """A position of `qty` shares bought during session i_entry and sold during session i_exit:
        (shares at the exit, cash dividends received). It earns every dividend that goes ex after the entry
        session, up to and including the exit session, on the shares it holds on the eve of that ex-date."""
        self._raw_only()
        cash = 0.0
        for i in range(i_entry + 1, i_exit + 1):
            qty, paid = self.carry(i, j, qty)
            cash += paid
        return qty, cash

    def total_return(self, symbol: str) -> np.ndarray:
        """Daily total return per session: (close x split + dividend) / previous close - 1. NaN where the symbol
        has no bar and in its first session."""
        self._raw_only()
        j = self._col[symbol]
        prev = _prev_close(self.c[:, [j]])[:, 0]
        return (self.c[:, j] * self.split[:, j] + self.div[:, j]) / prev - 1.0

    def total_return_index(self, symbol: str, base: float = 1.0) -> np.ndarray:
        """Growth of `base` held in the symbol with dividends reinvested: `base` at its first bar, NaN before."""
        r = self.total_return(symbol)
        seen = np.isfinite(self.c[:, self._col[symbol]]).cumsum() > 0
        return np.where(seen, base * np.cumprod(1.0 + np.nan_to_num(r)), np.nan)

    def spliced_close(self, symbol: str, fallback: str) -> np.ndarray:
        """Close of `symbol`, with the sessions before its first bar filled from `fallback`'s moves and joined at
        that first bar (XLC, which began trading in June 2018, continued backwards with SPY). The filled levels
        are synthetic: use them for returns, betas and relative strength, never as prices. Use an adjusted panel,
        so that both series are total-return series."""
        a, f = self.c[:, self._col[symbol]], self.c[:, self._col[fallback]]
        both = np.flatnonzero(np.isfinite(a) & np.isfinite(f))
        if not len(both):
            return a.copy()
        k = both[0]
        out = a.copy()
        fill = ~np.isfinite(a[:k])
        out[:k][fill] = (a[k] * f[:k] / f[k])[fill]
        return out

    def _raw_only(self) -> None:
        if self.kind != "raw":
            raise ValueError("positions and total returns are computed on the raw panel")


class DailyStore:
    """The store written by `kaibot.ingest.daily_bars` (scripts/fetch_daily.sh): <data>/daily."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._events_cache: tuple[int, dict[str, list[tuple]]] | None = None

    def keys(self) -> list[str]:
        return store_keys(self.root)

    def meta(self, key: str) -> dict:
        return read_meta(bars_path(self.root, key))

    def bars(self, key: str, start=None, end=None) -> pd.DataFrame:
        """Raw daily bars of one key: date, o, h, l, c, v, n, vw, available_ts."""
        df = pd.DataFrame(_columns(bars_path(self.root, key)))
        keep = np.ones(len(df), dtype=bool)
        if start is not None:
            keep &= df["date"].to_numpy() >= _day(start)
        if end is not None:
            keep &= df["date"].to_numpy() <= _day(end)
        return df[keep].reset_index(drop=True)

    def reference(self, key: str) -> pd.DataFrame | None:
        """Alpaca's `adjustment=all` close and volume as of the fetch (check and fallback factor only)."""
        p = ref_path(self.root, key)
        return pd.DataFrame(_columns(p)) if p.exists() else None

    def actions(self, key: str | None = None, dups: bool = False) -> pd.DataFrame:
        """Corporate actions (all keys, or one). Rows flagged as repeats are left out unless `dups`."""
        p = actions_path(self.root)
        if not p.exists():
            raise FileNotFoundError(f"{p}: no corporate-actions table (run scripts/fetch_daily.sh)")
        df = pd.DataFrame(_columns(p))
        keep = np.ones(len(df), dtype=bool)
        if key is not None:
            keep &= (df["key"] == key).to_numpy()
        if not dups:
            keep &= ~df["dup"].to_numpy(dtype=bool)
        return df[keep].reset_index(drop=True)

    def holes(self) -> pd.DataFrame:
        p = holes_path(self.root)
        return pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else pd.DataFrame()

    def adjustment_report(self) -> pd.DataFrame:
        """The last check's verdict per key (check/adjustment.csv: key, verdict, steps, split_basis_days, ...);
        empty before the first check. For a key with split_basis_days > 0 the table and Alpaca's bars disagree
        about a split: its share count cannot be carried through that date, so do not trade it in a simulation."""
        p = self.root / "check" / "adjustment.csv"
        return pd.read_csv(p) if p.exists() else pd.DataFrame(columns=["key", "verdict", "split_basis_days"])

    def flagged(self) -> frozenset:
        """Keys whose corporate actions disagree with Alpaca's adjusted bars (verdict `mismatch`)."""
        p = self.root / "check" / "adjustment.csv"
        if not p.exists():
            return frozenset()
        with p.open(newline="") as fh:
            return frozenset(r["key"] for r in csv.DictReader(fh) if r.get("verdict") == "mismatch")

    def panel(self, keys: Sequence[str] | None = None, start=None, end=None, intraday: pd.DataFrame | None = None,
              minute_share: float | None = MINUTE_SHARE) -> DailyPanel:
        """Raw bars of `keys` (default: every key in the store) for sessions in [start, end].

        A key without bars in the window is a column of NaN. o, h, l, c, v come from the store. The three fields
        that only exist inside the day come from `intraday`, a frame with the columns symbol, date, c1555,
        v_first, v_last (and c) built from the minute history, for example `DailyBars.to_frame()` of the harness
        or `kaibot.ingest.daily_check.minute_daily`. The minute history is split-adjusted as of its download, so
        its values are put back on the raw basis; a day whose minute close is more than 5% away from the stored
        close (a split the minute files do not know) is dropped and counted in `notes`.
        Without minute data `c1555` stays NaN (the official close must never stand in for a 15:55 price), and
        `v_first` / `v_last` are `minute_share` of the day's volume (None: NaN)."""
        keys = list(keys) if keys is not None else self.keys()
        loaded = {j: _columns(bars_path(self.root, k), ["date", "o", "h", "l", "c", "v"])
                  for j, k in enumerate(keys) if bars_path(self.root, k).exists()}
        if not loaded:
            raise ValueError(f"no daily bars for any of {len(keys)} keys in {self.root} (run scripts/fetch_daily.sh)")
        dates = np.unique(np.concatenate([d["date"] for d in loaded.values()]))
        arr = {k: np.full((len(dates), len(keys)), np.nan) for k in FIELDS}
        for j, d in loaded.items():
            rows = np.searchsorted(dates, d["date"])
            for name in ("o", "h", "l", "c", "v"):
                arr[name][rows, j] = d[name]
        events = self._events(keys)
        notes: dict = {}
        if intraday is not None and len(intraday):
            # on the whole history: the minute files are split-adjusted as of the store's last session, not `end`
            basis = table_factors(arr["c"], *event_arrays(dates, np.isfinite(arr["c"]), events))[0]
            _merge_intraday(arr, dates, keys, basis, intraday, notes)
        keep = np.ones(len(dates), dtype=bool)
        if start is not None:
            keep &= dates >= _day(start)
        if end is not None:
            keep &= dates <= _day(end)
        if not keep.any():
            raise ValueError(f"no daily bars between {start} and {end} in {self.root}")
        dates, arr = dates[keep], {k: a[keep] for k, a in arr.items()}
        ref_split, ref_total = self._reference_factors(keys, dates, arr["c"], arr["v"])
        return _assemble(dates, keys, arr, events, ref_split, ref_total, self.flagged(), minute_share, notes)

    def total_return(self, key: str = "SPY", start=None, end=None) -> pd.DataFrame:
        """Total-return series of one key: date, close (raw), dividend, split, ret, index (1.0 at the first bar)."""
        p = self.panel([key], start, end, minute_share=None)
        return pd.DataFrame({"date": p.dates.astype("datetime64[ns]"), "close": p.c[:, 0], "dividend": p.div[:, 0],
                             "split": p.split[:, 0], "ret": p.total_return(key), "index": p.total_return_index(key)})

    # -- internals

    def _events(self, keys: Sequence[str]) -> dict[int, list[tuple]]:
        """Per column: (ex_date, ratio, cash) of every action the reader applies (repeats left out). The table is
        read once per version of the file."""
        p = actions_path(self.root)
        if not p.exists():
            raise FileNotFoundError(f"{p}: no corporate-actions table, so prices cannot be adjusted "
                                    "(run scripts/fetch_daily.sh)")
        stamp = p.stat().st_mtime_ns
        if self._events_cache is None or self._events_cache[0] != stamp:
            a = _columns(p, ["key", "type", "ex_date", "ratio", "cash", "dup"])
            by_key: dict[str, list[tuple]] = {}
            for key, kind, ex, ratio, cash, dup in zip(a["key"], a["type"], a["ex_date"], a["ratio"], a["cash"],
                                                       a["dup"], strict=True):
                if not dup and kind in ADJUSTING_TYPES:
                    by_key.setdefault(key, []).append((ex, ratio if kind in SHARE_TYPES else np.nan,
                                                       cash if kind == "cash_dividend" else np.nan))
            self._events_cache = (stamp, by_key)
        by_key = self._events_cache[1]
        return {j: by_key[k] for j, k in enumerate(keys) if k in by_key}

    def _reference_factors(self, keys: Sequence[str], dates: np.ndarray, c: np.ndarray, v: np.ndarray,
                           ) -> tuple[np.ndarray, np.ndarray]:
        """Factors implied by Alpaca's adjusted bars: close ratio (everything Alpaca adjusts for) and volume
        ratio (splits only: Alpaca adjusts volume for splits and nothing else), carried over sessions without a
        bar and set to 1.0 at the last bar."""
        ref_split = np.full(c.shape, np.nan)
        ref_total = np.full(c.shape, np.nan)
        for j, k in enumerate(keys):
            p = ref_path(self.root, k)
            if not p.exists():
                continue
            r = _columns(p)
            pos = np.searchsorted(dates, r["date"])
            ok = (pos < len(dates)) & (dates[np.minimum(pos, len(dates) - 1)] == r["date"])
            rc, rv = np.full(len(dates), np.nan), np.full(len(dates), np.nan)
            rc[pos[ok]], rv[pos[ok]] = r["c"][ok], r["v"][ok]
            with np.errstate(divide="ignore", invalid="ignore"):
                total = _fill(np.where((rc > 0) & (c[:, j] > 0), rc / c[:, j], np.nan))
                split = _fill(np.where((rv > 0) & (v[:, j] > 0), v[:, j] / rv, np.nan))
            if np.isfinite(total).all() and np.isfinite(split).all():
                ref_total[:, j], ref_split[:, j] = total / total[-1], split / split[-1]
        return ref_split, ref_total


def _assemble(dates: np.ndarray, keys: list[str], arr: dict[str, np.ndarray], events: dict[int, list[tuple]],
              ref_split: np.ndarray, ref_total: np.ndarray, flagged: frozenset, minute_share: float | None,
              notes: dict) -> DailyPanel:
    split, div = event_arrays(dates, np.isfinite(arr["c"]), events)
    pf_split, pf_total, bad = table_factors(arr["c"], split, div)
    estimated = 0
    if minute_share is not None:
        for name in ("v_first", "v_last"):
            est = np.isnan(arr[name]) & np.isfinite(arr["v"])
            arr[name] = np.where(est, minute_share * arr["v"], arr[name])
            estimated = max(estimated, int(est.sum()))
    notes = {"intraday_rows": 0, "intraday_scale_mismatch": 0, **notes, "bad_dividends": bad,
             "references": int(np.isfinite(ref_total).all(axis=0).sum()), "minute_volume_estimated": estimated}
    return DailyPanel(dates, keys, **arr, split=split, div=div, pf_split=pf_split, pf_total=pf_total,
                      ref_split=ref_split, ref_total=ref_total, flagged=flagged, notes=notes)


def _fill(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).ffill().bfill().to_numpy()


def _merge_intraday(arr: dict[str, np.ndarray], dates: np.ndarray, keys: Sequence[str], pf_split: np.ndarray,
                    intraday: pd.DataFrame, notes: dict) -> None:
    need = {"symbol", "date", "c1555", "v_first", "v_last"}
    if not need <= set(intraday.columns):
        raise ValueError(f"intraday frame needs the columns {sorted(need)} (and c to verify the split basis)")
    col = {k: j for j, k in enumerate(keys)}
    df = intraday[intraday["symbol"].isin(col)]
    day = df["date"].to_numpy().astype("datetime64[D]")
    di = np.searchsorted(dates, day)
    inside = (di < len(dates)) & (dates[np.minimum(di, len(dates) - 1)] == day)
    df, di = df[inside], di[inside]
    sj = df["symbol"].map(col).to_numpy(dtype=int)
    scale = pf_split[di, sj]  # minute bars are raw x this (split-adjusted as of their download)
    ok = np.isfinite(arr["c"][di, sj])
    if "c" in df.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            off = np.abs(df["c"].to_numpy(dtype=float) / (arr["c"][di, sj] * scale) - 1.0)
        ok &= off <= SCALE_TOL
    arr["c1555"][di[ok], sj[ok]] = df["c1555"].to_numpy(dtype=float)[ok] / scale[ok]
    arr["v_first"][di[ok], sj[ok]] = df["v_first"].to_numpy(dtype=float)[ok] * scale[ok]
    arr["v_last"][di[ok], sj[ok]] = df["v_last"].to_numpy(dtype=float)[ok] * scale[ok]
    notes["intraday_rows"] = int(ok.sum())
    notes["intraday_scale_mismatch"] = int((~ok).sum())
