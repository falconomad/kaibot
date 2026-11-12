"""The daily runner path (issue #161, story S94; decision 15 sections 3 and 5): multi-day `Strategy` plug-ins on
daily bars, their targets executed at the next session's closing auction through `SimBroker`.

Research and engine-simulation only. The live engine (`engine/core.py`) refuses multi-day strategies; nothing
here is imported by it.

One session `i` (times ET; the one `SimClock` drives the strategy, the venue and this runner):

    16:30 of i - 1  AFTER_CLOSE: each strategy reads the daily bars up to the close of i - 1 (`DailyView`) and,
                    on its decision day, returns a `TargetList` (keep / buy candidates / sells with reasons)
    08:00 of i      the venue applies the session's corporate actions (split: shares x ratio; the ledger also
                    books the cash dividend of the previous close's shares)
    09:00 of i      `atr_risk_sizing` turns the target list into whole-share targets (equity marked at the close
                    of i - 1), the runner sends the difference to the venue as `cls` day orders (closing auction;
                    Alpaca accepts them before 15:50 ET and SimBroker enforces that, whole shares only); a sale
                    that found no bar earlier is sent again every session until it fills
    09:30 / 15:59   the venue trades the session's opening print and its closing print; `cls` orders fill at the
                    official close at cost model v1 (half a spread plus impact, as the pre-registrations price it:
                    `broker.sim.AuctionImpactCosts`); the stress fill uses `opg` orders at the open
    16:30 of i      fills are booked to the ledger; a held company whose bars stop is closed at its terminal
                    return; every position is marked at the close; the strategies decide for session i + 1

Pieces that later stories replace, each behind one seam:
- `atr_risk_sizing` is the shared ATR rule exactly as the research harness applies it (shares = equity x risk /
  ATR(20), whole, under the level's per-name cap and the exposure cap, sells first, resizes with reductions
  first, buys best-first until the room runs out). The portfolio builder (#119) replaces this one function.
- `StandInLedger` books fills, dividends, splits and the close's marks as entries in the ledger's journal
  shapes of #117 / PR #160 (`run`, `fill`, `dividend`, `split`, `mark`) and folds them into a book with the
  harness's trade rows and daily rows. When #160 merges, `engine.ledger.Ledger.fold(entries)` is the source of
  truth and this class keeps only the research statistics (trade rows) it does not have.
- `_venue_actions` applies splits and delistings to `SimBroker`'s positions. Alpaca does this itself; #160's
  `SimBroker.non_trade` activities and `ledger.actions_from_activities` replace it.

The venue runs without a cash book (`SimBroker(cash=None)`): Kaibot's paper account is a margin account, so a
closing-auction buy funded by sales at the same print is accepted live, while `SimBroker`'s cash rule would refuse
it at submission. "Never more than the cash" is the sizing rule's job; the harness also cuts the last buy to the
cash at the fill price, which an order sent before the auction cannot know. When that would have bitten, the
ledger shows a negative cash balance (`overdrafts`), reported, never hidden.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.broker.shadow_costs import half_spread
from kaibot.broker.sim import NY, AuctionImpactCosts, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.replay.history import BarHistory
from kaibot.risk.gate import RiskPolicy
from kaibot.strategies.base import Event, TargetList
from kaibot.strategies.momentum import DailyContext

FILL_SESSIONS = 5  # a missing close carries the last one for at most 5 sessions (both pre-registrations)
MIN_PRICE, MIN_ADV = 5.0, 50e6  # the universe filter: price >= $5, 20-day average dollar volume >= $50M
ATR_SESSIONS, ATR_MIN = 20, 15
ATR14, ATR14_MIN, GAP_ATR = 14, 10, 3.0  # the harness's entry also needs a 14-session ATR (its risk column)
SIGMA_FALLBACK = 0.02 / math.sqrt(390)  # the cost model's 1-minute sigma without 20 returns
AFTER_CLOSE, CORP_ACTIONS, SEND = time(16, 30), time(8, 0), time(9, 0)
OPEN_BAR, CLOSE_BAR = time(9, 30), time(15, 59)
FILLS = {"base": ("close", 1.0), "stress": ("open", 2.0)}  # where orders fill, spread / impact factor


def _ffill(a: np.ndarray) -> np.ndarray:
    return pd.DataFrame(a).ffill(limit=FILL_SESSIONS).to_numpy()


# ---------------------------------------------------------------- point-in-time daily data


@dataclass
class DailyData:
    """(sessions x symbols) daily arrays of one test: the signal side (split-adjusted, no dividends) and the
    market side (raw prices of each session), plus what the venue and the ledger need. Every derived array's row t
    uses sessions <= t only; strategies read them through `view(k)`, which shows rows <= k."""

    dates: np.ndarray  # datetime64[D]
    symbols: list[str]
    sig_c: np.ndarray  # adjusted closes, a missing one carried <= 5 sessions
    sig_h: np.ndarray
    sig_l: np.ndarray
    raw_o: np.ndarray  # raw prices (NaN: no bar that session)
    raw_c: np.ndarray
    raw_h: np.ndarray
    raw_l: np.ndarray
    v_first: np.ndarray  # shares in the first / last minute: the impact term's volume at the open / the close
    v_last: np.ndarray
    raw_v: np.ndarray
    stocks: np.ndarray  # columns that are stocks (the universe file or the point-in-time candidates)
    spy: int
    member: np.ndarray | None = None  # point-in-time index membership (and the split-dispute blocks)
    split: np.ndarray | None = None  # shares per share at the session's open
    div: np.ndarray | None = None  # cash per share of the previous close's shares, ex-date this session
    terminal: dict[int, tuple[int, float]] = field(default_factory=dict)  # column -> (last bar, return)
    exits: dict[int, list[int]] = field(default_factory=dict)  # column -> sessions it is sold at the close
    spy_tr: np.ndarray | None = None  # SPY total return per session (None: price only)
    universe: str = "today"
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._col = {s: j for j, s in enumerate(self.symbols)}
        self._row = {d: n for n, d in enumerate(self.dates.astype(object))}
        raw_cf = _ffill(self.raw_c)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.px = raw_cf  # what an order sized on the close pays per share (raw dollars of that session)
            self.adv = pd.DataFrame(raw_cf * self.raw_v).rolling(20, min_periods=15).mean().to_numpy()
            mask = np.zeros(len(self.symbols), dtype=bool)
            mask[np.asarray(self.stocks, dtype=int)] = True
            self.ok = (np.isfinite(self.raw_c) & (self.raw_c >= MIN_PRICE) & (self.adv >= MIN_ADV) & mask[None, :])
            if self.member is not None:
                self.ok &= self.member
            prev = pd.DataFrame(self.sig_c).shift(1).to_numpy()
            h, lo = self.sig_h, self.sig_l
            tr = np.fmax(np.fmax(h - lo, np.abs(h - prev)), np.abs(lo - prev))
            # adjusted level / factor = raw level of that session; a session without a bar keeps the last factor
            factor = pd.DataFrame(self.sig_unfilled_c / self.raw_c).ffill().to_numpy()
            self.atr20 = pd.DataFrame(tr).rolling(ATR_SESSIONS, min_periods=ATR_MIN).mean().to_numpy() / factor
            self.atr14 = pd.DataFrame(tr).rolling(ATR14, min_periods=ATR14_MIN).mean().to_numpy() / factor
            ret = pd.DataFrame(self.sig_c / prev - 1)
            self.sigma = ret.rolling(20, min_periods=20).std().to_numpy() / math.sqrt(390)

    sig_unfilled_c: np.ndarray = None  # set by the constructors (the adjusted close without the carry)

    # -- builders

    @classmethod
    def from_market(cls, m) -> DailyData:
        """From the research data bundle `Market` of the harness (today's list on minute or store bars, or
        the point-in-time S&P 500 on the daily store). Only its arrays are read; signals, sizing, orders and fills
        are this module's and the strategies' own."""
        sig, raw = m.sig, m.raw
        split, div = (m.actions if m.actions is not None else (None, None))
        return cls(dates=np.asarray(raw.dates, dtype="datetime64[D]"), symbols=list(raw.symbols),
                   sig_c=_ffill(sig.c), sig_h=sig.h, sig_l=sig.l, raw_o=raw.o, raw_c=raw.c, raw_h=raw.h,
                   raw_l=raw.l, v_first=raw.v_first, v_last=raw.v_last, raw_v=raw.v,
                   stocks=np.asarray(m.stocks, dtype=int), spy=int(m.spy), member=m.member, split=split, div=div,
                   terminal=dict(m.terminal), exits={int(j): list(v) for j, v in m.exits.items()}, spy_tr=m.spy_tr,
                   universe=m.universe, notes=dict(m.notes), sig_unfilled_c=sig.c)

    # -- reads

    def col(self, symbol: str) -> int:
        return self._col[symbol]

    def index_of(self, day: date) -> int:
        return self._row[day]

    def day(self, i: int) -> date:
        return self.dates[i].astype(object)

    def view(self, k: int) -> DailyView:
        return DailyView(self, k)

    def split_at(self, i: int, j: int) -> float:
        if self.split is None:
            return 1.0
        s = float(self.split[i, j])
        return s if math.isfinite(s) and s > 0 else 1.0

    def sigma_at(self, i: int, j: int) -> float:
        """The cost model's 1-minute sigma for a fill on session i: 20 daily returns up to i - 1, / sqrt(390)."""
        s = self.sigma[i - 1, j] if i > 0 else math.nan
        return float(s) if math.isfinite(s) and s > 0 else SIGMA_FALLBACK

    def bar_history(self) -> BarHistory:
        """The venue's bars: two per session with a bar, the opening print (09:30) and the closing print (15:59,
        the last minute, where `SimBroker` fills `cls` orders), with the first / last minute's volume."""
        days = pd.DatetimeIndex(self.dates.astype("datetime64[ns]"))
        t_open = (days + pd.Timedelta(hours=9, minutes=30)).tz_localize(NY).tz_convert(UTC)
        t_close = (days + pd.Timedelta(hours=15, minutes=59)).tz_localize(NY).tz_convert(UTC)
        frames = {}
        for j, sym in enumerate(self.symbols):
            has = np.flatnonzero(np.isfinite(self.raw_c[:, j]))
            if not len(has):
                continue
            o = np.where(np.isfinite(self.raw_o[has, j]), self.raw_o[has, j], self.raw_c[has, j])
            c = self.raw_c[has, j]
            h = np.where(np.isfinite(self.raw_h[has, j]), self.raw_h[has, j], np.fmax(o, c))
            lo = np.where(np.isfinite(self.raw_l[has, j]), self.raw_l[has, j], np.fmin(o, c))
            vf, vl = self.v_first[has, j], self.v_last[has, j]
            f = pd.DataFrame({"ts": np.r_[t_open[has], t_close[has]], "o": np.r_[o, o], "h": np.r_[o, h],
                              "l": np.r_[o, lo], "c": np.r_[o, c], "v": np.r_[vf, vl], "vw": np.r_[o, c]})
            frames[sym] = f.sort_values("ts", kind="stable").reset_index(drop=True)
        return BarHistory(frames)

    def calendar(self) -> dict[str, tuple[str, str]]:
        return {str(d): ("09:30", "16:00") for d in self.dates}


class DailyView:
    """The daily bars as they stood after the close of session k: nothing after row k is reachable."""

    def __init__(self, data: DailyData, k: int) -> None:
        self.d, self.k = data, k
        self.symbols, self.spy, self.col = data.symbols, data.spy, data.col

    def close(self, n: int) -> np.ndarray:
        """The last n adjusted closes (rows k - n + 1 .. k), NaN rows before the first session."""
        lo = self.k - n + 1
        w = self.d.sig_c[max(lo, 0):self.k + 1]
        return np.vstack([np.full((-lo, w.shape[1]), np.nan), w]) if lo < 0 else w

    def ok(self) -> np.ndarray:
        """The universe filter on row k: price >= $5, 20-day dollar volume >= $50M (and a member, point-in-time)."""
        return self.d.ok[self.k]


# ---------------------------------------------------------------- the sizing rule (the stand-in for #119)


@dataclass
class Plan:
    sell: dict[str, str]
    resize: list[tuple[str, float]]
    buy: list[tuple[str, float]]
    skipped: int = 0  # buys left out because one share costs more than the position size


def target_shares(data: DailyData, pol: RiskPolicy, atr_risk: float, k: int, j: int, eq: float,
                  s: float = 1.0) -> float | None:
    """shares = equity x risk / ATR(20), whole, under the level's per-name cap; None without an ATR or a close.
    Read on the close of k, counted in the shares of session k + 1 (`s`: a split between them)."""
    atr, px = data.atr20[k, j] / s, data.px[k, j] / s
    if not (math.isfinite(atr) and atr > 0 and math.isfinite(px) and px > 0):
        return None
    return float(min(math.floor(eq * atr_risk / atr), math.floor(eq * pol.max_position_pct / 100 / px)))


def atr_risk_sizing(tl: TargetList, held: Mapping[str, tuple[float, float]], pending: Mapping[str, str],
                    data: DailyData, i: int, eq: float, pol: RiskPolicy, exposure_pct: float) -> Plan:
    """The shared ATR sizing rule of decision 15 as the research harness applies it (`CoreRule.plan`), behind one
    function the portfolio builder (#119) replaces. `held`: symbol -> (shares, last mark) after the session's
    corporate actions; `pending`: sales still waiting for a bar (they stay sales); `eq`: equity marked at the
    close of i - 1. Room is counted at that close's prices, as if every order of the plan fills."""
    f, k = tl.features, i - 1
    sell = {s: pending.get(s, r) for s, r in f["sell"].items()}
    sell.update({s: r for s, r in pending.items() if s in held and s not in sell})
    sell = {s: sell[s] for s in held if s in sell}  # in the book's order
    cap = exposure_pct / 100 * eq
    exposure = sum(q * last for s, (q, last) in held.items() if s not in sell)
    names = len(held) - len(sell)
    resize: list[tuple[str, float]] = []
    if f.get("resize"):
        want = []
        for s, (q, last) in held.items():
            j = data.col(s)
            t = None if s in sell else target_shares(data, pol, f["atr_risk"], k, j, eq, data.split_at(i, j))
            if t is not None and t >= 1 and abs(q - t) > f.get("resize_buffer", 0.10) * t:
                want.append((s, q, last, t))
        for s, q, last, t in sorted(want, key=lambda w: w[3] - w[1]):  # reductions first: they make room
            if t > q:
                t = q + min(t - q, math.floor(max(cap - exposure, 0.0) / last))
                if t <= q:
                    continue
            exposure += (t - q) * last
            resize.append((s, float(t)))
    buy: list[tuple[str, float]] = []
    skipped = 0
    for s in f["buy"]:
        if names >= f["max_names"]:
            break
        j = data.col(s)
        sj = data.split_at(i, j)
        t = target_shares(data, pol, f["atr_risk"], k, j, eq, sj)
        if t is None:
            continue
        if t < 1:
            skipped += 1  # one share is more than the position size (decision 15: skipped, counted)
            continue
        px = data.px[k, j] / sj
        room = math.floor(max(cap - exposure, 0.0) / px)
        if room < 1:
            break  # no room left: buying stops here
        exposure += min(t, room) * px
        names += 1
        buy.append((s, float(min(t, room))))
        if room < t:
            break  # cut to the room left under the exposure cap, and buying stops there
    # a name without a close or a 14-session ATR on the decision's data is not bought (its room stays counted,
    # as in the harness, whose entry refuses it at the fill)
    buy = [(s, t) for s, t in buy if math.isfinite(data.raw_c[k, data.col(s)])
           and math.isfinite(data.atr14[k, data.col(s)]) and data.atr14[k, data.col(s)] > 0]
    return Plan(sell, resize, buy, skipped)


# ---------------------------------------------------------------- the ledger (stand-in for #117 / PR #160)


@dataclass
class Trade:
    """A closed position or a partial sale, in the research harness's trade-row shape (`MTrade`)."""

    symbol: str
    date: np.datetime64
    exit_date: np.datetime64
    hold_days: int
    qty: float
    entry: float
    exit: float
    stop: float
    reason: str
    pnl: float
    risk: float
    gross_pnl: float
    cost_spread: float
    cost_impact: float
    cost_slippage: float
    cost_fees: float
    gap: bool
    equity: float
    rule: str
    config: str
    entry_at: str
    dividends: float = 0.0


@dataclass
class _Sleeve:
    qty: float
    entry_i: int
    fill: float
    mid: float
    half: float
    imp: float
    risk: float
    equity: float
    last: float
    dividends: float = 0.0


@dataclass
class Book:
    """The ledger's view of one run: what the Gate B report and the parity test read."""

    equity: float
    trades: list[Trade] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    bought: float = 0.0
    sold: float = 0.0
    dividends: float = 0.0


class StandInLedger:
    """Positions of ONE strategy sleeve, cash, the close's marks, dividends and splits, as a fold over entries in
    the journal shapes of PR #160 (`run`, `fill`, `dividend`, `split`, `mark`; a delisting is a `fill` without
    fees). `entries` is that journal. See the module docstring for what replaces it."""

    def __init__(self, strategy: str, rule: str, data: DailyData, amount: float, mult: float) -> None:
        self.strategy, self.rule, self.d, self.mult = strategy, rule, data, mult
        self.entries: list[tuple[str, dict]] = []
        self.cash = float(amount)
        self.equity = float(amount)
        self.pos: dict[str, _Sleeve] = {}
        self.book = Book(float(amount))
        self.overdrafts: list[tuple[str, float]] = []
        self._day = {"cost": 0.0, "traded": 0.0, "dividends": 0.0, "exits": 0}
        self.apply("run", {"amount": float(amount), "strategy": strategy})

    def apply(self, kind: str, body: dict) -> None:
        self.entries.append((kind, body))
        getattr(self, f"_{kind}")(body)

    def _run(self, e: dict) -> None:
        pass

    def _fill(self, e: dict) -> None:
        i, s, qty, px, mid = e["i"], e["symbol"], float(e["qty"]), float(e["px"]), float(e["mid"])
        fees = float(e.get("fees", 0.0))
        costless = e.get("reason") in ("delisted", "cash_in_lieu")
        half = 0.0 if costless else half_spread(mid) * self.mult
        if e["side"] == "buy":
            imp = px - mid - half
            p = self.pos.get(s)
            if p is None:
                j = self.d.col(s)
                atr = self.d.atr14[i - 1, j] / self.d.split_at(i, j)
                self.pos[s] = _Sleeve(qty, i, px, mid, half, imp, qty * GAP_ATR * atr, e["equity"],
                                      float(e["ref"]))
            else:
                q = p.qty + qty
                p.half, p.imp = (p.half * p.qty + half * qty) / q, (p.imp * p.qty + imp * qty) / q
                p.fill, p.mid = (p.fill * p.qty + px * qty) / q, (p.mid * p.qty + mid * qty) / q
                p.risk *= q / p.qty
                p.qty = q
            self.cash -= px * qty
            self.book.bought += px * qty
            self._day["cost"] += (px - mid) * qty
            self._day["traded"] += px * qty
            return
        p = self.pos[s]
        lot = min(qty, p.qty)
        risk, divs = p.risk * (lot / p.qty), p.dividends * (lot / p.qty)
        if lot >= p.qty - 1e-12:
            del self.pos[s]
        else:
            p.qty -= lot
            p.risk -= risk
            p.dividends -= divs
        x_imp = 0.0 if costless else abs(px - mid) - half
        self.cash += px * lot - fees
        self.book.sold += px * lot
        self._day["cost"] += (mid - px) * lot + fees
        self._day["traded"] += px * lot
        d = self.d.dates
        self.book.trades.append(Trade(
            s, d[p.entry_i], d[i], i - p.entry_i, lot, p.fill, px, math.nan, e["reason"], (px - p.fill) * lot - fees,
            risk, (mid - p.mid) * lot, (p.half + half) * lot, (p.imp + x_imp) * lot, 0.0, fees, False, p.equity,
            self.rule, self.strategy, e.get("at", "close"), divs))
        self._day["exits"] += 1

    def _dividend(self, e: dict) -> None:
        p = self.pos.get(e["symbol"])
        if p is not None:
            paid = p.qty * float(e["per_share"])
            self.cash += paid
            p.dividends += paid
            self.book.dividends += paid
            self._day["dividends"] += paid

    def _split(self, e: dict) -> None:
        p, r = self.pos.get(e["symbol"]), float(e["ratio"])
        if p is None:
            return
        p.qty *= r
        p.fill, p.mid, p.last, p.half, p.imp = (x / r for x in (p.fill, p.mid, p.last, p.half, p.imp))

    def _mark(self, e: dict) -> None:
        for s, px in e["prices"].items():
            if s in self.pos and math.isfinite(px):
                self.pos[s].last = float(px)
        eq = self.equity
        self.equity = self.cash + self.exposure()
        if self.cash < -1e-6:
            self.overdrafts.append((str(e["session"]), self.cash))
        self.book.daily.append({"date": self.d.dates[e["i"]], "start": eq, "end": self.equity,
                                "pnl": self.equity - eq, "trades": self._day["exits"], "positions": len(self.pos),
                                "exposure": self.exposure() / self.equity if self.equity > 0 else 0.0,
                                "cost": self._day["cost"], "traded": self._day["traded"],
                                "dividends": self._day["dividends"]})
        self._day = {"cost": 0.0, "traded": 0.0, "dividends": 0.0, "exits": 0}

    def exposure(self) -> float:
        return sum(p.qty * p.last for p in self.pos.values())

    def held(self) -> dict[str, tuple[float, float]]:
        return {s: (p.qty, p.last) for s, p in self.pos.items()}


# ---------------------------------------------------------------- the runner


@dataclass
class DailyRun:
    """What one run of one strategy through the engine path produced."""

    strategy: str
    book: Book
    ledger: StandInLedger
    orders: list[tuple[int, str, str, object]]  # (session, sell|resize|buy, symbol, reason or target shares)
    fills: list[dict]  # every fill: session, symbol, side, qty, px, fees, reason
    targets: dict[int, TargetList]  # fill session -> the target list decided after the close before it
    rejected: list[tuple[int, str, str]]  # (session, symbol, the venue's reason)
    skipped: int
    opened: list[tuple[int, int, float]]  # (session, column, value / equity) of every new position
    resized: list[tuple[int, int, float]]  # (session, column, value / equity after the resize)
    broker: SimBroker
    i0: int
    i1: int

    @property
    def entries(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for i, _, _ in self.opened:
            out[i] = out.get(i, 0) + 1
        return out


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, NY).astimezone(UTC)


def _venue_actions(broker: SimBroker, symbol: str, *, split: float = 1.0, delist: bool = False) -> None:
    """The venue's corporate actions on its positions (stand-in; Alpaca applies these itself)."""
    p = broker.pos.get(symbol)
    if p is None:
        return
    if delist:
        broker.pos.pop(symbol)
        return
    q = float(p["qty"]) * split
    p["qty"] = int(q) if q.is_integer() else q
    p["avg_entry_price"] = float(p["avg_entry_price"]) / split


def run_daily(strategy, data: DailyData, pol: RiskPolicy, i0: int, i1: int, *, fill: str = "base",
              cost: float = 1.0, exposure_pct: float = 100.0, equity0: float = 100_000.0,
              on_session: Callable[[int], None] | None = None) -> DailyRun:
    """One continuous account of one multi-day strategy over sessions i0..i1, starting flat: the strategy decides
    after each close, `atr_risk_sizing` sizes, `SimBroker` fills at the next close (`fill="base"`) or open
    (`"stress"`), `StandInLedger` books. What is still held after i1 is valued at its last mark less the cost
    model's sale (the harness's `end_of_data`), so every dollar of the equity curve is in a trade row."""
    if i0 < 1 or i1 >= len(data.dates) or i0 > i1:
        raise ValueError("sessions i0..i1 must lie inside the data, with i0 >= 1")
    where, factor = FILLS[fill]
    mult = cost * factor
    tif = "cls" if where == "close" else "opg"
    costs = AuctionImpactCosts(sigma=lambda sym, day: data.sigma_at(data.index_of(day), data.col(sym)), mult=mult)
    clock = SimClock(_at(data.day(i0 - 1), AFTER_CLOSE) - timedelta(minutes=1))
    broker = SimBroker(data.bar_history(), clock, costs=costs, cash=None, calendar=data.calendar())
    led = StandInLedger(strategy.name, strategy.rule, data, equity0, mult)
    run = DailyRun(strategy.name, led.book, led, [], [], {}, [], 0, [], [], broker, i0, i1)
    pending: dict[str, str] = {}
    n_seen = 0
    exits_on: dict[int, list[int]] = {}
    for j, sessions in data.exits.items():
        for s in sessions:
            exits_on.setdefault(int(s), []).append(j)

    def decide(k: int) -> None:
        """After the close of session k: the strategy's target list for session k + 1."""
        clock.set(_at(data.day(k), AFTER_CLOSE))
        ctx = DailyContext(Event.AFTER_CLOSE, data.day(k), data.day(k + 1), data.view(k),
                           {s: q for s, (q, _) in led.held().items()})
        tl = strategy.on_event(ctx)
        if tl is not None:
            run.targets[k + 1] = tl

    def send(i: int, symbol: str, side: str, qty: float, kind: str, why, at: str = tif) -> None:
        if qty < 1:
            return
        coid = f"kb-{strategy.name}-{data.dates[i]}-{kind}-{symbol}"
        try:
            broker.submit(OrderRequest(symbol, side, int(qty), "market", at, coid))
        except BrokerError as e:
            run.rejected.append((i, symbol, str(e)[:120]))
            return
        sent[coid] = (kind, symbol, why)

    decide(i0 - 1)
    for i in range(i0, i1 + 1):
        d = data.day(i)
        # -- before the open: corporate actions of the session
        clock.set(_at(d, CORP_ACTIONS))
        for s in list(led.pos):
            j = data.col(s)
            dv = float(data.div[i, j]) if data.div is not None else 0.0
            if math.isfinite(dv) and dv > 0:
                led.apply("dividend", {"symbol": s, "ex_date": str(d), "per_share": dv, "i": i})
            r = data.split_at(i, j)
            if r != 1.0:
                _venue_actions(broker, s, split=r)
                led.apply("split", {"symbol": s, "ex_date": str(d), "ratio": r, "i": i})
                frac = led.pos[s].qty - math.floor(led.pos[s].qty + 1e-9)
                if frac > 1e-9:  # the fraction of a share is paid in cash (Alpaca), at the previous close / ratio
                    px = led.pos[s].last
                    broker.pos[s]["qty"] = int(math.floor(float(broker.pos[s]["qty"]) + 1e-9))
                    led.apply("fill", {"i": i, "symbol": s, "side": "sell", "qty": frac, "px": px, "mid": px,
                                       "fees": 0.0, "reason": "cash_in_lieu", "at": "open"})
                    run.fills.append({"i": i, "symbol": s, "side": "sell", "qty": frac, "px": px, "fees": 0.0,
                                      "reason": "cash_in_lieu"})
                    if not broker.pos[s]["qty"]:
                        broker.pos.pop(s)
        # -- 09:00: size the target list, send the difference as closing-auction (or opening) orders
        clock.set(_at(d, SEND))
        sent: dict[str, tuple[str, str, object]] = {}
        eq = led.equity
        tl = run.targets.get(i)
        held = led.held()
        pending = {s: r for s, r in pending.items() if s in held}
        forced = {data.symbols[j] for j in exits_on.get(i, ()) if data.symbols[j] in held}
        if tl is not None:
            plan = atr_risk_sizing(tl, held, pending, data, i, eq, pol, exposure_pct)
            run.skipped += plan.skipped
            run.orders += [(i, "sell", s, r) for s, r in plan.sell.items()]
            run.orders += [(i, "resize", s, t) for s, t in plan.resize] + [(i, "buy", s, t) for s, t in plan.buy]
            for s, r in plan.sell.items():
                send(i, s, "sell", held[s][0], "sell", r)
            for s, t in plan.resize:
                if s in forced:
                    continue  # sold at this close anyway (a split the data disagree about is next)
                q = held[s][0]
                send(i, s, "buy" if t > q else "sell", abs(t - q), "resize", t)
            for s, t in plan.buy:
                send(i, s, "buy", t, "buy", t)
            pending = {}
        else:
            for s, r in pending.items():  # a sale whose stock had no bar: try again every session
                send(i, s, "sell", held[s][0], "sell", r)
        for s in forced:
            if not any(v[1] == s and v[0] == "sell" for v in sent.values()):
                send(i, s, "sell", held[s][0], "split_dispute", "split_dispute", at="cls")  # at the close, always
        # -- the session: opening print, closing print
        broker.advance(_at(d, OPEN_BAR))
        clock.set(_at(d, CLOSE_BAR))
        broker.advance(_at(d, CLOSE_BAR))
        # -- after the close: book the fills, terminal closes, marks
        clock.set(_at(d, AFTER_CLOSE))
        filled: set[str] = set()
        by_id = {o["id"]: o for o in broker.orders.values()}
        for f in broker.fills[n_seen:]:
            o = by_id[f["order_id"]]
            kind, s, why = sent.get(o["client_order_id"], ("sell", o["symbol"], "sell"))
            j = data.col(s)
            qty, px = float(f["qty"]), float(f["price"])
            at = "close" if o.get("time_in_force") == "cls" else "open"
            mid = float(data.raw_c[i, j] if at == "close" else data.raw_o[i, j])
            ref = float(data.px[i - 1, j]) / data.split_at(i, j)
            reason = why if kind != "resize" else "resize"
            led.apply("fill", {"i": i, "symbol": s, "side": f["side"], "qty": qty, "px": px, "mid": mid,
                               "fees": float(f["fees"]), "reason": reason, "at": at, "equity": eq, "ref": ref})
            run.fills.append({"i": i, "symbol": s, "side": f["side"], "qty": qty, "px": px, "fees": float(f["fees"]),
                              "reason": reason})
            filled.add(o["client_order_id"])
            if kind == "buy":
                run.opened.append((i, j, qty * ref / eq))
            elif kind == "resize" and s in led.pos:
                run.resized.append((i, j, led.pos[s].qty * ref / eq))
        n_seen = len(broker.fills)
        for coid, (kind, s, why) in sent.items():
            if coid not in filled and kind in ("sell", "split_dispute") and s in led.pos:
                pending[s] = why  # a sale without a bar waits for the next session that has one
        for s in list(led.pos):
            j = data.col(s)
            term = data.terminal.get(j)
            if term is not None and term[0] == i and math.isfinite(data.raw_c[i, j]):
                px = max(float(data.raw_c[i, j]) * (1.0 + term[1]), 0.0)  # bars stop: closed at the terminal return
                _venue_actions(broker, s, delist=True)
                run.fills.append({"i": i, "symbol": s, "side": "sell", "qty": led.pos[s].qty, "px": px, "fees": 0.0,
                                  "reason": "delisted"})
                led.apply("fill", {"i": i, "symbol": s, "side": "sell", "qty": led.pos[s].qty, "px": px, "mid": px,
                                   "fees": 0.0, "reason": "delisted", "at": "close"})
                pending.pop(s, None)
        led.apply("mark", {"i": i, "session": str(d),
                           "prices": {s: float(data.raw_c[i, data.col(s)]) for s in led.pos}})
        if on_session:
            on_session(i)
        if i < i1:
            decide(i)
    # -- end of data: what is still held is valued at its last mark less the modelled sale
    if led.pos:
        row = led.book.daily[-1]
        cost0, traded0 = led._day["cost"], led._day["traded"]
        for s, p in list(led.pos.items()):
            j = data.col(s)
            vol = float(data.v_last[i1, j]) if math.isfinite(data.v_last[i1, j]) and data.v_last[i1, j] > 0 else 1.0
            px = costs.price("sell", p.last, p.qty, data.sigma_at(i1, j), vol)
            fees = costs.fees("sell", p.qty, px)
            run.fills.append({"i": i1, "symbol": s, "side": "sell", "qty": p.qty, "px": px, "fees": fees,
                              "reason": "end_of_data"})
            led.apply("fill", {"i": i1, "symbol": s, "side": "sell", "qty": p.qty, "px": px, "mid": p.last,
                               "fees": fees, "reason": "end_of_data", "at": "close"})
        led.equity = led.cash
        row.update(end=led.equity, pnl=led.equity - row["start"], positions=0, exposure=0.0,
                   trades=led._day["exits"], cost=row["cost"] + led._day["cost"] - cost0,
                   traded=row["traded"] + led._day["traded"] - traded0)
    led.book.equity = led.equity
    return run


def sessions_window(data: DailyData, start, end) -> tuple[int, int]:
    """(i0, i1): the first session on or after `start` (at least 1) and the last on or before `end`."""
    i0 = max(int(np.searchsorted(data.dates, np.datetime64(start, "D"), side="left")), 1)
    i1 = int(np.searchsorted(data.dates, np.datetime64(end, "D"), side="right")) - 1
    return i0, i1


def daily_returns(book: Book) -> np.ndarray:
    return np.array([r["pnl"] / r["start"] for r in book.daily], dtype=float)


__all__: Sequence[str] = ("DailyData", "DailyView", "Plan", "atr_risk_sizing", "target_shares", "StandInLedger",
                          "Book", "Trade", "DailyRun", "run_daily", "sessions_window", "daily_returns")
