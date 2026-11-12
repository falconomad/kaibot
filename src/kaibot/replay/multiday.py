"""Multi-day simulator on daily bars (issue #113, decision 14). Research only: nothing here is imported by the
engine, and nothing in the engine, the risk gate or the intraday replay is changed.

Status under decision 15: this is the research harness of the first look (candidate 0). Its results are
provisional and cannot pass Gate B; go/no-go numbers come only from the engine with `SimBroker`. Do not grow it
into another simulator: new rules belong to the Engine v3 strategy interface.

Daily bars come from the 1-minute regular-hours bars: open = the first minute's open, close = the last minute's
close, high / low over the session, volume summed, plus the 15:55 close (what a market-on-close decision can see)
and the volume of the first and last minute (what the cost model's impact term needs at the open and the close).

Order of events in one session `i` (a decision before a fill reads sessions <= i - 1 only; a decision at the close
reads session i up to 15:55 plus the calendar):
1. Open: positions scheduled to leave are sold at the open; then new entries are bought at the open. Their size
   was fixed from the previous close: the loss if the stock opens `gap_atr` (3) daily ATRs lower is at most the
   per-trade risk, and the position, the number of positions and the total held are capped.
2. Session: a resting stop fills at the stop when the low reaches it, or at the OPEN when the open is already
   below it (the gap is reported as `cost_slippage`). A position bought at this open cannot gap.
3. Close: positions past their maximum holding period, or entering an earnings blackout, are sold at the close;
   entries at the close (overnight rule, carried intraday winners) are bought; trailing stops move up to
   `trail_atr` ATRs under the highest close; equity is marked at the close.

Costs reuse the intraday replay's functions (`sim.buy_px`, `sim.sell_px`, `sim.cost_parts`, `shadow_costs.
sell_fees`), so `pnl == gross_pnl - cost_spread - cost_impact - cost_slippage - cost_fees` here too.
Long only, no leverage: a buy never spends more than the cash in the account.

Added for the momentum core (issue #127), all off by default so the first-look rules are unchanged (a test pins
them): a cost multiplier on spread and impact (`cost_mult`; fees are regulatory and stay), a sizing hook
(`enter(shares=...)`: the rule names the shares, the caps still apply), orders sized from the previous close
that fill at the close (`enter(at="close", prev_close=True)`, `sell_close`), and partial buys and sells of a
held position (`adjust`). A partial sale is its own trade row with its share of the entry costs, so
`pnl == gross_pnl - costs` and "the trades explain the equity" still hold.

Added for the honest rerun (issue #143), also off by default (the first-look and today-universe results are
pinned): corporate actions on held positions (`actions=(split, div)`, the raw daily store's arrays: a split
multiplies the shares and divides every price level of the position, a cash dividend is credited to the account
and booked as the trade's own `dividends` line beside `pnl`), and `terminal` (column -> (last bar, return)): a
position still held at the last bar of a company whose bars stop is closed at that close times (1 + return), with
no cost (the hole policy of docs/ops/pit_universe.md). Every daily row now also carries the session's `cost`
(spread, impact and fees charged), `traded` (dollars bought plus sold) and `dividends`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import sell_fees
from kaibot.replay.data import N_MIN, SymbolDays, load_symbol
from kaibot.replay.sim import EXIT_M, buy_px, cost_parts, half_spread, sell_px
from kaibot.risk.gate import RiskPolicy

FIELDS = ("o", "h", "l", "c", "v", "c1555", "v_first", "v_last")
ATR_N = 14
FILL_SESSIONS = 5
SIGMA_FALLBACK = 0.02 / math.sqrt(N_MIN)  # 2% daily vol when a stock has no 20-session history yet


# ---------------------------------------------------------------- daily bars


@dataclass
class DailyBars:
    """(sessions x symbols) arrays, NaN where a symbol has no bar that session."""

    dates: np.ndarray  # datetime64[D], sorted
    symbols: list[str]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    v: np.ndarray
    c1555: np.ndarray  # close of the 15:55 bar (last trade up to 15:55)
    v_first: np.ndarray  # shares in the first regular-hours minute that traded
    v_last: np.ndarray  # shares in the last regular-hours minute that traded

    def __post_init__(self) -> None:
        self._col = {s: j for j, s in enumerate(self.symbols)}

    def col(self, symbol: str) -> int:
        return self._col[symbol]

    def cols(self, symbols: Iterable[str]) -> np.ndarray:
        return np.array([self._col[s] for s in symbols if s in self._col], dtype=int)

    def index_from(self, d) -> int:
        """First session on or after `d`."""
        return int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="left"))

    def index_upto(self, d) -> int:
        """Last session on or before `d`."""
        return int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="right")) - 1

    def to_frame(self) -> pd.DataFrame:
        di, sj = np.nonzero(np.isfinite(self.c))
        out = {"symbol": np.array(self.symbols, dtype=object)[sj], "date": self.dates[di].astype("datetime64[ns]")}
        out.update({k: getattr(self, k)[di, sj] for k in FIELDS})
        return pd.DataFrame(out)

    @classmethod
    def from_frame(cls, df: pd.DataFrame, symbols: Sequence[str] | None = None) -> DailyBars:
        symbols = list(symbols) if symbols is not None else sorted(df["symbol"].unique())
        dates = np.array(sorted(df["date"].unique()), dtype="datetime64[D]")
        col = {s: j for j, s in enumerate(symbols)}
        df = df[df["symbol"].isin(col)]
        di = np.searchsorted(dates, df["date"].to_numpy().astype("datetime64[D]"))
        sj = df["symbol"].map(col).to_numpy(dtype=int)
        arrs = {}
        for k in FIELDS:
            a = np.full((len(dates), len(symbols)), np.nan)
            a[di, sj] = df[k].to_numpy(dtype=float)
            arrs[k] = a
        return cls(dates, symbols, **arrs)

    @classmethod
    def make(cls, dates, symbols: Sequence[str], o, h, l, c, v=None, c1555=None,  # noqa: E741
             v_first=None, v_last=None) -> DailyBars:
        """From plain (sessions x symbols) arrays; volumes default to 5M shares a day, 2% of it in the first and
        in the last minute."""
        c = np.asarray(c, dtype=float)
        v = np.full(c.shape, 5e6) if v is None else np.asarray(v, dtype=float)
        return cls(np.asarray(dates, dtype="datetime64[D]"), list(symbols), np.asarray(o, dtype=float),
                   np.asarray(h, dtype=float), np.asarray(l, dtype=float), c, v,
                   c.copy() if c1555 is None else np.asarray(c1555, dtype=float),
                   v * 0.02 if v_first is None else np.asarray(v_first, dtype=float),
                   v * 0.02 if v_last is None else np.asarray(v_last, dtype=float))

    def keep_sessions(self, mask: np.ndarray) -> DailyBars:
        return DailyBars(self.dates[mask], self.symbols, *(getattr(self, k)[mask] for k in FIELDS))


def daily_from_minutes(sd: SymbolDays) -> dict[str, np.ndarray]:
    """One row per session from the 390-minute arrays. A minute without a bar carries the last close (see
    `data.from_frame`), so the first non-NaN open is the first bar's open and the last column is the last close."""
    has = ~np.isnan(sd.o)
    first = has.argmax(axis=1)
    traded = sd.v > 0
    last = np.where(traded.any(axis=1), N_MIN - 1 - traded[:, ::-1].argmax(axis=1), N_MIN - 1)
    r = np.arange(len(sd.dates))
    return {"o": sd.o[r, first], "h": np.nanmax(sd.h, axis=1), "l": np.nanmin(sd.l, axis=1), "c": sd.c[:, -1],
            "v": sd.v.sum(axis=1), "c1555": sd.c[:, EXIT_M], "v_first": sd.v[r, first], "v_last": sd.v[r, last]}


def build_daily(symbols: Sequence[str], loader: Callable[[str], SymbolDays | None],
                progress: Callable[[int, str], None] | None = None) -> DailyBars:
    """Daily bars of every symbol the loader returns (one symbol's minutes in memory at a time)."""
    frames = []
    for n, s in enumerate(symbols):
        sd = loader(s)
        if progress:
            progress(n, s)
        if sd is None or not len(sd.dates):
            continue
        frames.append(pd.DataFrame({"symbol": s, "date": sd.dates.astype("datetime64[ns]"), **daily_from_minutes(sd)}))
    if not frames:
        raise ValueError("no bars for any symbol")
    return DailyBars.from_frame(pd.concat(frames, ignore_index=True), [s for s in symbols])


def bars_fingerprint(data_dir: Path, symbols: Sequence[str], start: str, end: str) -> str:
    """Changes when a month file of the minute history is added or rewritten, so a stale cache is rebuilt."""
    h = hashlib.sha256(f"{start}|{end}|{','.join(symbols)}".encode())
    lo, hi = str(np.datetime64(start, "M")), str(np.datetime64(end, "M"))
    for s in symbols:
        for f in sorted((Path(data_dir) / "bars_1m" / f"symbol={s}").glob("*.parquet")):
            if lo <= f.stem <= hi:
                st = f.stat()
                h.update(f"{s}/{f.name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def load_or_build_daily(data_dir: Path, symbols: Sequence[str], start: str, end: str, cache: Path | None = None,
                        refresh: bool = False, progress: Callable[[int, str], None] | None = None,
                        ) -> tuple[DailyBars, bool]:
    """Daily bars for [start, end] from the Parquet cache, or built from data/bars_1m and cached (returns
    `built=True`). The cache is one file plus a small .json with the fingerprint of the minute files it came from."""
    data_dir = Path(data_dir)
    cache = cache or data_dir / "multiday" / "daily_bars.parquet"
    meta_f = cache.with_suffix(".json")
    fp = bars_fingerprint(data_dir, symbols, start, end)
    if not refresh and cache.is_file() and meta_f.is_file():
        try:
            if json.loads(meta_f.read_text()).get("fingerprint") == fp:
                return DailyBars.from_frame(pd.read_parquet(cache), list(symbols)), False
        except (OSError, ValueError):
            pass
    lo, hi = np.datetime64(start, "D"), np.datetime64(end, "D")
    bars = build_daily(symbols, lambda s: load_symbol(data_dir, s, lo, hi), progress)
    cache.parent.mkdir(parents=True, exist_ok=True)
    bars.to_frame().to_parquet(cache, index=False)
    meta_f.write_text(json.dumps({"fingerprint": fp, "start": start, "end": end, "symbols": len(symbols),
                                  "sessions": len(bars.dates)}))
    return bars, True


@dataclass
class Indicators:
    """Row t of each array uses sessions <= t only."""

    atr: np.ndarray  # 14-session average true range (at least 10 of the 14 sessions have a bar)
    sigma: np.ndarray  # 1-minute return sigma for the cost model: std of the last 20 daily returns / sqrt(390)
    adv: np.ndarray  # 20-session average dollar volume (at least 15 of the 20)
    ret: np.ndarray  # close-to-close return


def closes(b: DailyBars) -> pd.DataFrame:
    """Closes for rolling statistics: a session without a bar (a halt, a hole in the download) carries the last
    close for at most `FILL_SESSIONS` sessions, so one missing day does not blank a 200-day average for 200 days.
    Only the past is used; nothing can be bought or sold on such a day (its own prices stay NaN)."""
    return pd.DataFrame(b.c).ffill(limit=FILL_SESSIONS)


def indicators(b: DailyBars) -> Indicators:
    c = closes(b)
    h, l, v = (pd.DataFrame(getattr(b, k)) for k in ("h", "l", "v"))  # noqa: E741
    prev = c.shift(1)
    tr = np.fmax(np.fmax((h - l).to_numpy(), (h - prev).abs().to_numpy()), (l - prev).abs().to_numpy())
    atr = pd.DataFrame(tr).rolling(ATR_N, min_periods=ATR_N - 4).mean().to_numpy()
    ret = c / prev - 1
    sigma = ret.rolling(20, min_periods=20).std().to_numpy() / math.sqrt(N_MIN)
    adv = (c * v).rolling(20, min_periods=15).mean().to_numpy()
    return Indicators(atr, sigma, adv, ret.to_numpy())


# ---------------------------------------------------------------- positions, trades, the engine


@dataclass(frozen=True)
class Caps:
    """Pre-declared overnight limits (balanced level): positions held, total held as % of equity, and the gap
    (in daily ATRs) a position is sized on."""

    max_positions: int = 5
    max_exposure_pct: float = 60.0
    gap_atr: float = 3.0


@dataclass
class MTrade:
    symbol: str
    date: np.datetime64  # entry session
    exit_date: np.datetime64
    hold_days: int  # sessions between entry and exit = nights held (0: in and out the same session)
    qty: float
    entry: float  # fill after costs
    exit: float  # fill after costs
    stop: float  # stop in force at the exit (NaN: the rule has none)
    # stop | trail | max_hold | earnings | rebalance | market_filter | open | end_of_data | resize | intraday's
    reason: str
    pnl: float  # $ after all costs
    risk: float  # $ lost if the stock had opened `gap_atr` daily ATRs below the sizing price
    gross_pnl: float  # bought at the open / close print, sold at the stop level or the open / close print
    cost_spread: float
    cost_impact: float
    cost_slippage: float  # gap: the stop level minus the open that jumped it (0 for every other exit)
    cost_fees: float
    gap: bool  # the open was below the stop
    equity: float  # account equity at the start of the entry session (bp per trade are of this)
    rule: str
    config: str
    entry_at: str  # open | close | intraday
    dividends: float = 0.0  # cash dividends received on these shares while held (not in pnl: its own line)


@dataclass
class Pos:
    j: int
    qty: float
    entry_i: int
    fill: float
    mid: float
    entry_costs: tuple[float, float]  # (half spread, impact) per share paid on the buy
    stop: float
    trail_atr: float
    max_hold: int
    risk: float
    equity: float
    config: str
    entry_at: str
    last: float  # last price the position was marked at
    hi_close: float = -math.inf
    raised: bool = False  # the stop has moved above its first level
    exit_next_open: bool = False
    dividends: float = 0.0  # cash dividends received so far on the shares still held


@dataclass
class MBook:
    equity: float
    trades: list[MTrade] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    bought: float = 0.0  # $ paid for shares by the engine's own buys (turnover; not the replayed intraday leg)
    sold: float = 0.0  # $ received for shares, before fees
    dividends: float = 0.0  # cash dividends credited (raw store only)


def _ok(*xs: float) -> bool:
    return all(math.isfinite(x) for x in xs)


class Engine:
    """Runs one rule on daily bars. The rule's hooks decide WHAT to trade; fills, sizing, caps, stops, the earnings
    blackout, costs and the books are here, the same for every rule.

    `rule.at_open(i, eng, cfg, eq)` may call `sell_open` / `enter` and must read sessions <= i - 1 only;
    `rule.at_close(i, eng, cfg, eq)` may call `enter(at="close")` and reads session i up to 15:55.
    `schedule(i)` is the configuration in force on session i (the walk-forward changes it at month starts; a
    position keeps the exit settings it was opened with).

    `actions` = (split, div), (sessions x symbols) arrays of the raw daily store: shares per share held taking
    effect at session i's open, and cash per share held at the previous close (ex-date i). `bars` are then raw
    prices. `terminal` maps a column whose bars stop early to (index of its last bar, return booked at that close).
    """

    def __init__(self, bars: DailyBars, ind: Indicators, pol: RiskPolicy, rule, schedule: Callable[[int], object],
                 equity0: float = 100_000.0, hold_blk: np.ndarray | None = None,
                 entry_blk: np.ndarray | None = None, cost_mult: float = 1.0,
                 actions: tuple[np.ndarray, np.ndarray] | None = None,
                 terminal: Mapping[int, tuple[int, float]] | None = None) -> None:
        self.b, self.ind, self.pol, self.rule, self.schedule = bars, ind, pol, rule, schedule
        self.cost_mult = float(cost_mult)  # scales spread and impact of every fill (fees are not scaled)
        self.cash = float(equity0)
        self.equity = float(equity0)
        self.pos: dict[int, Pos] = {}
        self.book = MBook(float(equity0))
        self.hold_blk, self.entry_blk = hold_blk, entry_blk
        self.actions = actions
        self.terminal = dict(terminal or {})
        self._exits_today = 0
        self._cost_today = self._traded_today = self._div_today = 0.0

    # -- helpers

    def sigma(self, i: int, j: int) -> float:
        s = self.ind.sigma[i - 1, j] if i > 0 else math.nan
        return float(s) if math.isfinite(s) and s > 0 else SIGMA_FALLBACK

    def exposure(self) -> float:
        return sum(p.qty * p.last for p in self.pos.values())

    def split(self, i: int, j: int) -> float:
        """Shares per share held that take effect at session i's open (1.0 without corporate actions). An order
        sized from the close of i - 1 is in shares of that close; divide its price levels by this to get the
        shares of session i."""
        if self.actions is None:
            return 1.0
        s = float(self.actions[0][i, j])
        return s if math.isfinite(s) and s > 0 else 1.0

    def _corporate_actions(self, i: int) -> None:
        """At the open of session i, before anything else: dividends of the previous close's shares, then splits."""
        split, div = self.actions
        for p in self.pos.values():
            d = float(div[i, p.j])
            if math.isfinite(d) and d > 0:
                paid = p.qty * d
                self.cash += paid
                p.dividends += paid
                self.book.dividends += paid
                self._div_today += paid
            s = self.split(i, p.j)
            if s != 1.0:
                p.qty *= s
                p.fill, p.mid, p.last, p.hi_close, p.stop = (x / s for x in (p.fill, p.mid, p.last, p.hi_close,
                                                                              p.stop))
                p.entry_costs = (p.entry_costs[0] / s, p.entry_costs[1] / s)

    def _buy_px(self, mid: float, qty: float, sig: float, vol: float) -> float:
        px = buy_px(mid, qty, sig, vol)
        return px if self.cost_mult == 1.0 else mid + (px - mid) * self.cost_mult

    def _sell_px(self, mid: float, qty: float, sig: float, vol: float) -> float:
        px = sell_px(mid, qty, sig, vol)
        return px if self.cost_mult == 1.0 else mid - (mid - px) * self.cost_mult

    def _parts(self, mid: float, px: float) -> tuple[float, float]:
        """(half spread, impact) per share actually charged between `mid` and the fill `px`."""
        if self.cost_mult == 1.0:
            return cost_parts(mid, px)
        half = half_spread(mid) * self.cost_mult
        return half, abs(px - mid) - half

    def sell(self, i: int, j: int, price: float, reason: str, vol: float, gap: bool = False,
             qty: float | None = None, costless: bool = False) -> None:
        """Market sell of the whole position (or of `qty` shares of it) at `price` (the open, the close or the
        stop level) minus costs. With `gap`, `price` is the open that jumped the stop; the signal is credited the
        stop level and the difference is the gap slippage. A partial sale books its own trade row with its share
        of the entry costs and of the risk; the rest of the position stays as it was. `costless`: the position
        is closed at exactly `price` with no spread, impact or fee (a terminal return, not a trade)."""
        p = self.pos[j]
        lot = p.qty if qty is None else min(float(qty), p.qty)
        risk = p.risk * (lot / p.qty)
        divs = p.dividends * (lot / p.qty)
        if lot >= p.qty:
            del self.pos[j]
        else:
            p.qty -= lot
            p.risk -= risk
            p.dividends -= divs
        vol = vol if math.isfinite(vol) and vol > 0 else 1.0
        if costless:
            px, fees, x_half, x_imp = price, 0.0, 0.0, 0.0
        else:
            px = self._sell_px(price, lot, self.sigma(i, j), vol)
            fees = sell_fees(lot, px)
            x_half, x_imp = self._parts(price, px)
        level = p.stop if gap else price
        self.cash += px * lot - fees
        self.book.sold += px * lot
        self._cost_today += (price - px) * lot + fees
        self._traded_today += px * lot
        e_half, e_imp = p.entry_costs
        self.book.trades.append(MTrade(
            self.b.symbols[j], self.b.dates[p.entry_i], self.b.dates[i], i - p.entry_i, lot, p.fill, px, p.stop,
            reason, (px - p.fill) * lot - fees, risk, (level - p.mid) * lot, (e_half + x_half) * lot,
            (e_imp + x_imp) * lot, (level - price) * lot, fees, gap, p.equity, self.rule.name, p.config,
            p.entry_at, divs))
        self._exits_today += 1

    def sell_open(self, i: int, j: int, reason: str) -> bool:
        o = self.b.o[i, j]
        if j not in self.pos or not _ok(o):
            return False  # no bar today: the order waits (the rule asks again, or the next exit takes it)
        self.sell(i, j, float(o), reason, self.b.v_first[i, j])
        return True

    def sell_close(self, i: int, j: int, reason: str) -> bool:
        """Sell the whole position at the close of session i (an order placed before that close)."""
        c = self.b.c[i, j]
        if j not in self.pos or not _ok(c):
            return False  # no bar today: the order waits
        self.sell(i, j, float(c), reason, self.b.v_last[i, j])
        return True

    def enter(self, i: int, j: int, eq: float, config: str, caps: Caps, *, at: str = "open", stop_atr: float = 0.0,
              trail_atr: float = 0.0, max_hold: int = 0, max_value_pct: float | None = None,
              exit_next_open: bool = False, carry: Mapping | None = None, shares: float | None = None,
              prev_close: bool = False) -> float:
        """Buy at the open of session i (sized from the close of i - 1) or at its close (sized from the 15:55
        price, or with `prev_close` from the close of i - 1: an order placed the evening before). Returns the
        quantity bought, 0 when a cap, the blackout or missing data stops the entry.

        Quantity: the loss if the stock opens `caps.gap_atr` ATRs lower <= the level's per-trade risk; position
        <= the level's max position %; everything held <= `caps.max_exposure_pct` of equity; never more than the
        cash. `carry` (qty, fill, mid) hands over a position bought intraday: at most that quantity, at its fill.
        `shares` is the sizing hook: the rule's own number of shares replaces the gap sizing (the position cap,
        the exposure cap and the cash still apply).
        """
        b, k = self.b, i - 1
        if k < 0 or j in self.pos or len(self.pos) >= caps.max_positions:
            return 0.0
        s = self.split(i, j)  # a level of the close of i - 1 in the shares of session i (1.0: no split)
        atr = float(self.ind.atr[k, j]) / s
        ref, mid, vol = ((b.c[k, j] / s, b.o[i, j], b.v_first[i, j]) if at == "open"
                         else (b.c[k, j] / s if prev_close else b.c1555[i, j], b.c[i, j], b.v_last[i, j]))
        if not _ok(ref, mid, atr) or atr <= 0 or ref <= 0 or mid <= 0:
            return 0.0
        row = k if at == "open" or prev_close else i  # the last session whose news the decision can have read
        if self.entry_blk is not None and (self.entry_blk[row, j] or self.hold_blk[row, j]):
            return 0.0
        if shares is None:
            value = min(eq * self.pol.risk_per_trade_pct / 100 / (caps.gap_atr * atr) * ref,
                        eq * self.pol.max_position_pct / 100, caps.max_exposure_pct / 100 * eq - self.exposure())
        else:
            value = min(eq * self.pol.max_position_pct / 100, caps.max_exposure_pct / 100 * eq - self.exposure())
        if max_value_pct is not None:
            value = min(value, eq * max_value_pct / 100)
        qty = float(math.floor(value / ref)) if value > 0 else 0.0
        if shares is not None:
            qty = min(qty, float(math.floor(shares))) if math.isfinite(shares) else 0.0
        sig = self.sigma(i, j)
        vol = float(vol) if _ok(vol) and vol > 0 else 1.0
        if carry is not None:
            qty = min(qty, float(carry["qty"]))
            fill, mid_e = float(carry["fill"]), float(carry["mid"])
        else:
            fill, mid_e = (self._buy_px(float(mid), qty, sig, vol) if qty >= 1 else math.inf), float(mid)
        if qty >= 1 and fill * qty > self.cash:  # no leverage
            qty = float(math.floor(self.cash / fill))
            if carry is None and qty >= 1:
                fill = self._buy_px(float(mid), qty, sig, vol)
        if qty < 1:
            return 0.0
        self.cash -= fill * qty
        if carry is None:
            self.book.bought += fill * qty
            self._cost_today += (fill - mid_e) * qty
            self._traded_today += fill * qty
        half = half_spread(mid_e) * (1.0 if carry is not None else self.cost_mult)
        self.pos[j] = Pos(j, qty, i, fill, mid_e, (half, fill - mid_e - half),
                          fill - stop_atr * atr if stop_atr else math.nan, trail_atr, max_hold,
                          qty * caps.gap_atr * atr, eq, config, "intraday" if carry is not None else at,
                          float(ref), exit_next_open=exit_next_open)  # marked at the sizing price until the close
        return qty

    def adjust(self, i: int, j: int, target: float, eq: float, caps: Caps, *, at: str = "close",
               reason: str = "resize") -> float:
        """Partial buy or sell of a held position so that it holds `target` whole shares, at the open or the
        close of session i (an order sized from the close of i - 1). Returns the signed shares traded: 0 when
        there is no bar, nothing to do, or no room. A purchase obeys the same position cap, exposure cap and cash
        limit as `enter`; the position keeps its first entry day, and its fill and entry costs become the
        share-weighted averages."""
        p, b = self.pos.get(j), self.b
        if p is None or i < 1 or not math.isfinite(target):
            return 0.0
        mid, vol = (b.o[i, j], b.v_first[i, j]) if at == "open" else (b.c[i, j], b.v_last[i, j])
        target = max(float(math.floor(target)), 0.0)
        if not _ok(mid) or mid <= 0 or target == p.qty:
            return 0.0
        if target < p.qty:
            lot = p.qty - target
            self.sell(i, j, float(mid), reason, vol, qty=lot)
            return -lot
        ref = float(b.c[i - 1, j]) / self.split(i, j) if _ok(b.c[i - 1, j]) and b.c[i - 1, j] > 0 else p.last
        room = min(eq * self.pol.max_position_pct / 100 - p.qty * ref,
                   caps.max_exposure_pct / 100 * eq - self.exposure())
        add = min(target - p.qty, float(math.floor(room / ref))) if room > 0 else 0.0
        if add < 1:
            return 0.0
        sig = self.sigma(i, j)
        vol = float(vol) if _ok(vol) and vol > 0 else 1.0
        fill = self._buy_px(float(mid), add, sig, vol)
        if fill * add > self.cash:  # no leverage
            add = float(math.floor(self.cash / fill))
            if add < 1:
                return 0.0
            fill = self._buy_px(float(mid), add, sig, vol)
        self.cash -= fill * add
        self.book.bought += fill * add
        self._cost_today += (fill - float(mid)) * add
        self._traded_today += fill * add
        half = half_spread(float(mid)) * self.cost_mult
        q = p.qty + add
        p.entry_costs = ((p.entry_costs[0] * p.qty + half * add) / q,
                         (p.entry_costs[1] * p.qty + (fill - float(mid) - half) * add) / q)
        p.fill, p.mid = (p.fill * p.qty + fill * add) / q, (p.mid * p.qty + float(mid) * add) / q
        p.risk *= q / p.qty
        p.qty = q
        return add

    # -- one session

    def step(self, i: int) -> None:
        b, cfg, eq = self.b, self.schedule(i), self.equity
        self._exits_today = 0
        self._cost_today = self._traded_today = self._div_today = 0.0
        if self.actions is not None:  # 0. corporate actions take effect before the open
            self._corporate_actions(i)
        for p in [p for p in self.pos.values() if p.exit_next_open]:  # 1. the open
            self.sell_open(i, p.j, "open")
        self.rule.at_open(i, self, cfg, eq)
        for p in list(self.pos.values()):  # 2. resting stops
            o, lo = b.o[i, p.j], b.l[i, p.j]
            if not _ok(p.stop, o, lo):
                continue
            why = "trail" if p.raised else "stop"
            if p.entry_i < i and o <= p.stop:
                self.sell(i, p.j, float(o), why, b.v_first[i, p.j], gap=True)
            elif lo <= p.stop:
                self.sell(i, p.j, float(p.stop), why, b.v[i, p.j] / N_MIN)
        for p in list(self.pos.values()):  # 3. the close
            c = b.c[i, p.j]
            if not _ok(c):
                continue
            if p.max_hold and i - p.entry_i >= p.max_hold:
                self.sell(i, p.j, float(c), "max_hold", b.v_last[i, p.j])
            elif self.hold_blk is not None and self.hold_blk[i, p.j]:
                self.sell(i, p.j, float(c), "earnings", b.v_last[i, p.j])
        self.rule.at_close(i, self, cfg, eq)
        for p in [p for p in self.pos.values() if self.terminal.get(p.j, (-1,))[0] == i and _ok(b.c[i, p.j])]:
            r = self.terminal[p.j][1]  # 4. the company's bars stop here: closed at the terminal return
            self.sell(i, p.j, max(float(b.c[i, p.j]) * (1.0 + r), 0.0), "delisted", b.v_last[i, p.j],
                      costless=True)
        for p in self.pos.values():
            c, atr = b.c[i, p.j], self.ind.atr[i, p.j]
            if not _ok(c):
                continue
            p.last = float(c)
            p.hi_close = max(p.hi_close, p.last)
            if p.trail_atr and _ok(atr):
                new = p.hi_close - p.trail_atr * float(atr)
                if not _ok(p.stop):
                    p.stop = new
                elif new > p.stop:
                    p.stop, p.raised = new, True
        self.equity = self.cash + self.exposure()
        self.book.daily.append({"date": b.dates[i], "start": eq, "end": self.equity, "pnl": self.equity - eq,
                                "trades": self._exits_today, "positions": len(self.pos),
                                "exposure": self.exposure() / self.equity if self.equity > 0 else 0.0,
                                "cost": self._cost_today, "traded": self._traded_today,
                                "dividends": self._div_today})

    def run(self, i0: int, i1: int, close_at_end: bool = True) -> MBook:
        """Sessions i0..i1 inclusive. What is still open after the last session is sold at its close
        (`end_of_data`), so every dollar of the equity curve is in a trade row."""
        for i in range(i0, i1 + 1):
            self.step(i)
        if close_at_end and self.pos and self.book.daily:
            self._cost_today = self._traded_today = 0.0
            for p in list(self.pos.values()):
                self.sell(i1, p.j, p.last, "end_of_data", self.b.v_last[i1, p.j])
            row = self.book.daily[-1]
            self.equity = self.cash
            row.update(end=self.equity, pnl=self.equity - row["start"], positions=0, exposure=0.0,
                       trades=self._exits_today, cost=row["cost"] + self._cost_today,
                       traded=row["traded"] + self._traded_today)
        self.book.equity = self.equity
        return self.book


# ---------------------------------------------------------------- benchmarks


def spy_buy_and_hold(bars: DailyBars, spy: str, i0: int, i1: int) -> np.ndarray:
    """SPY close-to-close returns over sessions i0..i1 (price only: the bars carry no dividends)."""
    c = bars.c[:, bars.col(spy)]
    return c[i0:i1 + 1] / c[i0 - 1:i1] - 1


def equal_weight_benchmark(bars: DailyBars, ind: Indicators, cols: np.ndarray, i0: int, i1: int,
                           equity0: float = 100_000.0, *, eligible: np.ndarray | None = None,
                           actions: tuple[np.ndarray, np.ndarray] | None = None,
                           terminal: Mapping[int, tuple[int, float]] | None = None) -> np.ndarray:
    """Daily returns of holding every stock of the universe equal-weight, rebalanced at the open of each month's
    first session, after the same spread / impact / fee model (fractional shares, 0.1% left in cash for costs).
    A stock joins at the first rebalance after its first bar. This is what "just hold this hand-picked universe"
    made over the same sessions; it carries the same survivorship bias as the rules.

    Point-in-time (issue #143): `eligible` (sessions x all columns, bool) limits each rebalance to the stocks
    eligible on the previous session (index members); a held stock that is no longer eligible is sold at that
    rebalance when it has a bar. `actions` = (split, div) of a raw panel: dividends are credited and splits
    carried at each open. `terminal`: a stock whose bars stop is closed at its last close x (1 + return)."""
    b = bars
    cols = np.asarray(cols, dtype=int)
    q = np.zeros(len(cols))
    last = np.full(len(cols), np.nan)
    cash, eq = float(equity0), float(equity0)
    ends = {}
    for n, j in enumerate(cols.tolist()):
        if terminal and j in terminal:
            ends.setdefault(terminal[j][0], []).append((n, terminal[j][1]))
    rets = []
    for i in range(i0, i1 + 1):
        o, c = b.o[i, cols], b.c[i, cols]
        if actions is not None:
            split, div = actions[0][i, cols], actions[1][i, cols]
            cash += float(np.nansum(q * np.where(np.isfinite(div), div, 0.0)))
            s = np.where(np.isfinite(split) & (split > 0), split, 1.0)
            q, last = q * s, last / s
        if i == i0 or b.dates[i].astype("datetime64[M]") != b.dates[i - 1].astype("datetime64[M]"):
            ok = np.isfinite(o) & (np.isfinite(b.c[i - 1, cols]) if i > 0 else True)
            want = ok if eligible is None else ok & eligible[max(i - 1, 0), cols]
            last = np.where(np.isfinite(o), o, last)
            value = q * np.nan_to_num(last)
            total = cash + float(value[ok].sum())  # what can be traded today (a stock without a bar keeps its shares)
            if want.any() or (ok & (q > 0)).any():
                n_want = max(int(want.sum()), 1)
                dq = np.where(want, total * 0.999 / n_want / np.where(ok, o, 1.0) - q, np.where(ok, -q, 0.0))
                for n in sorted(np.flatnonzero(np.abs(dq) > 1e-9), key=lambda n: dq[n]):  # sells first
                    j, vol = cols[n], b.v_first[i, cols[n]]
                    sig = ind.sigma[i - 1, j] if i > 0 and np.isfinite(ind.sigma[i - 1, j]) else SIGMA_FALLBACK
                    vol = float(vol) if np.isfinite(vol) and vol > 0 else 1.0
                    if dq[n] < 0:
                        px = sell_px(float(o[n]), -dq[n], sig, vol)
                        cash += -dq[n] * px - sell_fees(-dq[n], px)
                    else:
                        cash -= dq[n] * buy_px(float(o[n]), dq[n], sig, vol)
                    q[n] += dq[n]
        last = np.where(np.isfinite(c), c, last)
        for n, r in ends.get(i, ()):
            if q[n] > 0 and np.isfinite(c[n]):
                cash += q[n] * max(float(c[n]) * (1.0 + r), 0.0)
                q[n] = 0.0
        end = cash + float(np.nansum(q * np.nan_to_num(last)))
        rets.append(end / eq - 1)
        eq = end
    return np.array(rets)
