"""Deterministic daily-data checks that block NEW entries (M12 ticket 3.11, issue #473; the daily half).

`kaibot.ingest.data_checks` checks today's minute bars for the intraday path. This module checks the daily bar store
(<data>/daily, written by scripts/fetch_daily.sh) that the multi-day path sizes and decides on, at the moment a buy
is about to leave. A failing check blocks buys only: sales, exits, protective stops, cancels and the flatten never
ask (engine/live_daily `_Gate.allows` asks for side "buy" only, and `Engine.entry_block` is read for entries only).

The reasons are plain codes in one small registry (`REASONS`), so the heartbeat's `entry_block` field (#446) can
show the code and its words. A block's text is `data check <code>[ <symbol>]: <detail>` (`Block.text`, `parse`).

Market checks (block every buy while they fail)
    benchmark_bar_missing  SPY has no daily bar for the last session whose deadline has passed (`data_deadline`:
                           03:30 ET the night after the session, after the nightly refresh and its catch-up run)
    price_not_positive     SPY has an open, high, low or close that is zero, negative or missing in its last
                           WINDOW bars
    universe_stale         the S&P 500 change log (ingest/sp500/changes.csv, the point-in-time universe) has no
                           change for UNIVERSE_MAX_AGE_DAYS: the index changes at least every quarter (the longest
                           gap since 2016 is 91 days), so the file has not been updated
    checks_failed          a check itself raised: fail closed (always blocks while the switch is on)
Stock checks (block buys of that stock only)
    daily_bar_stale        the stock's newest daily bar is older than SPY's (a refresh that stopped part-way)
    price_not_positive     a zero, negative or missing price in the stock's last WINDOW bars
    split_not_applied      within SPLIT_WINDOW sessions, a close-to-close step at a split ratio (2, 3, 4, 5, 10, 20
                           or the inverse, within SPLIT_TOL) with no split or stock dividend in the store's corporate
                           actions table on that bar: the adjusted series the rule reads is wrong
    price_jump             within WINDOW sessions, a move in excess of SPY's beyond SIGMA_N standard deviations of
                           that excess over the SIGMA_LOOKBACK sessions before it (and at least MIN_JUMP) with no
                           corporate action on that bar. Not in the switch's "on" set: a real news move looks the
                           same, and the pre-send gate already checks every buy against its live quote (M9 1.16)

Every block clears by itself once the data is right: a missing bar arrives, the action is added to the table, the
log is updated, or the bar leaves the window (WINDOW, SPLIT_WINDOW sessions). Nothing here writes or fetches; the
clock is an argument. `DailyEntryChecks` reads the store and caches by file fingerprint, so a refresh is seen at the
next call. On by default since audit 2 5.15 (#695): every check but price_jump blocks buys (KAIBOT_DAILY_CHECKS_BLOCK,
kaibot.common.switches; docs/ops/switches.md). With KAIBOT_DAILY_CHECKS_BLOCK=off nothing is read or blocked.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest.alpaca_history import ET
from kaibot.ingest.daily_bars import SHARE_TYPES, actions_path, bars_path

# ---------------------------------------------------------------- the registry of reasons


@dataclass(frozen=True)
class Reason:
    code: str
    scope: str  # "market": every buy; "stock": that stock's buys; "both": either, by the key it is found in
    words: str  # plain words for the heartbeat and the dashboard


REASONS: dict[str, Reason] = {r.code: r for r in (
    Reason("benchmark_bar_missing", "market", "SPY's daily bar for the last session is not in the store"),
    Reason("daily_bar_stale", "stock", "the stock's daily bars stop before SPY's"),
    Reason("price_not_positive", "both", "a daily price is zero, negative or missing"),
    Reason("split_not_applied", "stock", "a split-sized price step with no split in the corporate-actions table"),
    Reason("universe_stale", "market", "the S&P 500 change log has not been updated"),
    Reason("price_jump", "stock", "a move far beyond the stock's usual range with no corporate action"),
    Reason("checks_failed", "market", "the data checks could not run (fail closed)"),
)}
# the switch's choices (KAIBOT_DAILY_CHECKS_BLOCK); "on" means all but the last (kaibot.common.switches "checks")
CHOICES = ("benchmark_bar_missing", "daily_bar_stale", "price_not_positive", "split_not_applied", "universe_stale",
           "price_jump")
PREFIX = "data check "


@dataclass(frozen=True)
class Block:
    code: str
    detail: str
    symbol: str | None = None

    def __post_init__(self) -> None:
        if self.code not in REASONS:
            raise ValueError(f"unknown data-check reason {self.code!r}")

    def text(self) -> str:
        """The entry-block line, e.g. "data check benchmark_bar_missing SPY: no daily bar for 2026-11-24 ..."."""
        return f"{PREFIX}{self.code}{' ' + self.symbol if self.symbol else ''}: {self.detail}"[:240]

    def as_dict(self) -> dict:
        return {"code": self.code, "symbol": self.symbol, "detail": self.detail, "words": REASONS[self.code].words}


def parse(why: str | None) -> str | None:
    """The reason code of an entry-block line written by `Block.text` (None for any other line)."""
    if not why or not why.startswith(PREFIX):
        return None
    code = why[len(PREFIX):].split(":", 1)[0].split(" ", 1)[0]
    return code if code in REASONS else None


# ---------------------------------------------------------------- the deadline of a session's bars

DEADLINE_ET = time(3, 30)  # the night after the session: the nightly refresh (03:15 Berlin) and its 07:45 catch-up


def data_deadline(k: date) -> datetime:
    """When session k's daily bars must be in the store: DEADLINE_ET on the calendar day after k (aware, ET).
    Before it a missing bar is the normal wait for the nightly refresh; after it, it is late."""
    return datetime.combine(k + timedelta(days=1), DEADLINE_ET, ET)


def expected_session(now: datetime, prev_session: Callable[[date], date]) -> date:
    """The newest session whose deadline has passed at `now`: the session before the ET date of `now` less
    DEADLINE_ET. `prev_session(d)`: the session strictly before d (live_daily.sessions_from_calendar)."""
    shifted = now.astimezone(ET) - timedelta(hours=DEADLINE_ET.hour, minutes=DEADLINE_ET.minute)
    return prev_session(shifted.date())


# ---------------------------------------------------------------- the checks, as pure functions

WINDOW = 5  # sessions: bad prices and jumps are looked for in the newest bars only
SPLIT_WINDOW = 60  # sessions: a split the table lacks stays wrong in the adjusted series, so it is looked for longer
SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 10.0, 20.0)
SPLIT_TOL = 0.04  # relative: a 2-for-1 split is a close ratio of 0.48 to 0.52
SIGMA_N = 8.0
SIGMA_LOOKBACK = 60
MIN_HISTORY = 20  # excess returns needed for a standard deviation
MIN_JUMP = 0.10  # log excess return: smaller moves are never a jump, however quiet the stock
UNIVERSE_MAX_AGE_DAYS = 120
EXPLAINS_JUMP = (*SHARE_TYPES, "spin_off")
PRICE_COLUMNS = ("o", "h", "l", "c")


def _dates(bars: pd.DataFrame) -> np.ndarray:
    return np.asarray(bars["date"]).astype("datetime64[D]") if len(bars) else np.array([], dtype="datetime64[D]")


def _num(bars: pd.DataFrame, col: str) -> np.ndarray:
    if col not in bars:
        return np.full(len(bars), np.nan)
    return pd.to_numeric(bars[col], errors="coerce").to_numpy(dtype=float)


def _sorted(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.sort_values("date", kind="stable").reset_index(drop=True) if len(bars) else bars


def bad_prices(bars: pd.DataFrame, window: int = WINDOW) -> list[str]:
    """Dates (ISO) of the newest `window` bars with an open, high, low or close not finite and above zero."""
    b = _sorted(bars).tail(window)
    if not len(b):
        return []
    px = np.vstack([_num(b, c) for c in PRICE_COLUMNS])
    bad = ~(np.isfinite(px) & (px > 0)).all(axis=0)
    return [str(d) for d in _dates(b)[bad]]


def _acted(actions: pd.DataFrame | None, types: Sequence[str]) -> np.ndarray:
    if actions is None or not len(actions):
        return np.array([], dtype="datetime64[D]")
    a = actions[actions["type"].isin(types)]
    return np.asarray(a["ex_date"]).astype("datetime64[D]")


def _explained(ex: np.ndarray, before: np.datetime64, on: np.datetime64) -> bool:
    """An action with its ex-date after the earlier bar and on or before the later one."""
    return bool(((ex > before) & (ex <= on)).any()) if len(ex) else False


def split_steps(bars: pd.DataFrame, actions: pd.DataFrame | None, window: int = SPLIT_WINDOW,
                tol: float = SPLIT_TOL) -> list[tuple[str, float]]:
    """(date, close ratio) of each step in the newest `window` bars whose ratio is a split ratio (or its inverse)
    within `tol`, with no split or stock dividend of the key in `actions` on that bar."""
    b = _sorted(bars)
    d, c = _dates(b), _num(b, "c")
    ex = _acted(actions, SHARE_TYPES)
    out = []
    for i in range(max(1, len(b) - window), len(b)):
        if not (np.isfinite(c[i]) and np.isfinite(c[i - 1]) and c[i] > 0 and c[i - 1] > 0):
            continue
        q = c[i] / c[i - 1]
        if any(abs(q * n - 1) <= tol or abs(q / n - 1) <= tol for n in SPLIT_RATIOS) \
                and not _explained(ex, d[i - 1], d[i]):
            out.append((str(d[i]), float(q)))
    return out


def sigma_jumps(bars: pd.DataFrame, market: pd.DataFrame, actions: pd.DataFrame | None, window: int = WINDOW,
                n: float = SIGMA_N, lookback: int = SIGMA_LOOKBACK, floor: float = MIN_JUMP
                ) -> list[tuple[str, float, float]]:
    """(date, excess log return, its sigma) of each of the newest `window` sessions on which the stock's log return
    less SPY's (`market`, same columns) is beyond `n` standard deviations of that excess over the `lookback`
    sessions before it, and at least `floor`, with no split, stock dividend or spin-off of the key on that bar.
    Sessions are the dates both have; fewer than MIN_HISTORY earlier excess returns: no verdict."""
    a, m = _sorted(bars), _sorted(market)
    if len(a) < 2 or len(m) < 2:
        return []
    j = pd.merge(pd.DataFrame({"date": _dates(a), "c": _num(a, "c")}),
                 pd.DataFrame({"date": _dates(m), "m": _num(m, "c")}), on="date", how="inner")
    c, mc = j["c"].to_numpy(dtype=float), j["m"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ok = (c > 0) & (mc > 0) & np.isfinite(c) & np.isfinite(mc)
        lc = np.where(ok, np.log(np.where(ok, c, 1.0)), np.nan)
        lm = np.where(ok, np.log(np.where(ok, mc, 1.0)), np.nan)
    e = np.diff(lc) - np.diff(lm)  # e[i-1]: the session j[i] against j[i-1]
    d = np.asarray(j["date"]).astype("datetime64[D]")
    ex = _acted(actions, EXPLAINS_JUMP)
    out = []
    for i in range(max(1, len(j) - window), len(j)):
        x = e[i - 1]
        hist = e[max(0, i - 1 - lookback):i - 1]
        hist = hist[np.isfinite(hist)]
        if not np.isfinite(x) or len(hist) < MIN_HISTORY:
            continue
        sd = float(np.std(hist, ddof=1))
        if abs(x) >= floor and abs(x) > n * sd and not _explained(ex, d[i - 1], d[i]):
            out.append((str(d[i]), float(x), sd))
    return out


def universe_age(changes: Path, today: date) -> tuple[int, date] | None:
    """(days since the newest change, its date) in the S&P 500 change log; None when it has no dated row."""
    newest = None
    for line in Path(changes).read_text().splitlines():
        head = line.split(",", 1)[0].strip()
        if len(head) == 10 and head[:2] == "20":
            try:
                d = date.fromisoformat(head)
            except ValueError:
                continue
            newest = d if newest is None or d > newest else newest
    return None if newest is None else ((today - newest).days, newest)


def market_findings(spy: pd.DataFrame | None, expected: date, *, today: date, changes_age: tuple[int, date] | None,
                    market: str = "SPY", max_age_days: int = UNIVERSE_MAX_AGE_DAYS) -> list[Block]:
    """The market checks' findings. `spy`: SPY's bars (None: no bars file); `expected`: the session whose bars must
    be in by now (`expected_session`); `changes_age`: `universe_age` of the change log."""
    out: list[Block] = []
    last = _dates(spy).max().astype(object) if spy is not None and len(spy) else None
    if last is None or last < expected:
        out.append(Block("benchmark_bar_missing",
                         f"no daily bar for {expected} after its deadline {data_deadline(expected):%Y-%m-%d %H:%M} ET"
                         f" (newest {last or 'none'}; run ./scripts/fetch_daily.sh)", market))
    bad = bad_prices(spy) if spy is not None else []
    if bad:
        out.append(Block("price_not_positive", f"bad price on {', '.join(bad[:3])}", market))
    if changes_age is None:
        out.append(Block("universe_stale", "the S&P 500 change log has no dated change"))
    elif changes_age[0] > max_age_days:
        out.append(Block("universe_stale", f"the S&P 500 change log's newest change is {changes_age[1]}, "
                                           f"{changes_age[0]} days ago (limit {max_age_days})"))
    return out


def stock_findings(symbol: str, bars: pd.DataFrame | None, spy: pd.DataFrame | None,
                   actions: pd.DataFrame | None) -> list[Block]:
    """The stock checks' findings for one key (its bars, SPY's, the key's corporate actions)."""
    if bars is None or not len(bars):
        return [Block("daily_bar_stale", "no daily bars in the store", symbol)]
    out: list[Block] = []
    last = _dates(bars).max()
    spy_last = _dates(spy).max() if spy is not None and len(spy) else None
    if spy_last is not None and last < spy_last:
        out.append(Block("daily_bar_stale", f"newest bar {last}, SPY's {spy_last}", symbol))
    bad = bad_prices(bars)
    if bad:
        out.append(Block("price_not_positive", f"bad price on {', '.join(bad[:3])}", symbol))
    steps = split_steps(bars, actions)
    if steps:
        d, q = steps[-1]
        out.append(Block("split_not_applied", f"close ratio {q:.3f} on {d} and no split in the corporate-actions "
                                              "table (scripts/fetch_daily.sh, ingest/corporate_action_fixes.csv)",
                         symbol))
    split_days = {d for d, _ in steps}
    jumps = [x for x in (sigma_jumps(bars, spy, actions) if spy is not None else []) if x[0] not in split_days]
    if jumps:
        d, x, sd = jumps[-1]
        out.append(Block("price_jump", f"{math.expm1(x):+.1%} beyond SPY on {d} ({abs(x) / sd:.0f} sigma, no "
                                       "corporate action)", symbol))
    return out


# ---------------------------------------------------------------- on the store


def _fingerprint(p: Path) -> tuple:
    try:
        st = p.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return (None, None)


@dataclass
class DailyEntryChecks:
    """The checks on the daily store under `root` (<data>/daily). `blocking`: the codes that block (the switch's
    value); every finding is reported in `status`, only those block. `market_blocks(now, expected)` and
    `stock_blocks(symbol)` return the BLOCKING findings and raise when a file cannot be read (the caller fails
    closed). Each answer is cached until a file it read changes, or the session or the day does."""

    root: Path
    blocking: frozenset[str]
    market: str = "SPY"
    changes: Path | None = None  # the S&P 500 change log (default: kaibot.ingest.sp500_pit.CHANGES)
    max_universe_age_days: int = UNIVERSE_MAX_AGE_DAYS
    _market: tuple | None = field(default=None, repr=False)
    _stock: dict = field(default_factory=dict, repr=False)
    last_market: list[Block] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.changes is None:
            from kaibot.ingest.sp500_pit import CHANGES

            self.changes = CHANGES
        unknown = set(self.blocking) - set(CHOICES)
        if unknown:
            raise ValueError(f"unknown daily checks {sorted(unknown)}")

    def _bars(self, key: str) -> pd.DataFrame | None:
        from kaibot.ingest.daily_store import DailyStore

        if not bars_path(self.root, key).exists():
            return None
        return DailyStore(self.root).bars(key)

    def _actions(self, key: str) -> pd.DataFrame | None:
        from kaibot.ingest.daily_store import DailyStore

        if not actions_path(self.root).exists():
            return None
        return DailyStore(self.root).actions(key)

    def market_findings(self, now: datetime, expected: date) -> list[Block]:
        today = now.astimezone(ET).date()
        key = (expected, today, _fingerprint(bars_path(self.root, self.market)), _fingerprint(Path(self.changes)))
        if self._market is None or self._market[0] != key:
            found = market_findings(self._bars(self.market), expected, today=today,
                                    changes_age=universe_age(Path(self.changes), today), market=self.market,
                                    max_age_days=self.max_universe_age_days)
            self._market = (key, found)
        self.last_market = self._market[1]
        return self._market[1]

    def market_blocks(self, now: datetime, expected: date) -> list[Block]:
        return [b for b in self.market_findings(now, expected) if b.code in self.blocking]

    def stock_findings(self, symbol: str) -> list[Block]:
        key = (_fingerprint(bars_path(self.root, symbol)), _fingerprint(bars_path(self.root, self.market)),
               _fingerprint(actions_path(self.root)))
        hit = self._stock.get(symbol)
        if hit is None or hit[0] != key:
            hit = (key, stock_findings(symbol, self._bars(symbol), self._bars(self.market), self._actions(symbol)))
            self._stock[symbol] = hit
        return hit[1]

    def stock_blocks(self, symbol: str) -> list[Block]:
        return [b for b in self.stock_findings(symbol) if b.code in self.blocking]

    def status(self) -> dict:
        """For the heartbeat: what blocks, and the market findings and stock findings seen at the last calls."""
        stocks = sorted((b for _, fs in self._stock.values() for b in fs), key=lambda b: (b.symbol or "", b.code))
        out: dict = {"blocking": sorted(self.blocking),
                     "market": [b.as_dict() for b in self.last_market],
                     "stocks": [b.as_dict() for b in stocks[:10]]}
        out["blocked"] = sum(b.code in self.blocking for b in (*self.last_market, *stocks))
        return out
