"""The daily runner path (issue #161, story S94; decision 15 sections 3 and 5): multi-day `Strategy` plug-ins on
daily bars, their targets executed at the next session's closing auction through `SimBroker`.

Research and engine-simulation only. The live engine (`engine/core.py`) refuses multi-day strategies; nothing
here is imported by it.

One session `i` (times ET; the one `SimClock` drives the strategy, the venue and this runner):

    16:30 of i - 1  AFTER_CLOSE: each strategy reads the daily bars up to the close of i - 1 (`DailyView`) and,
                    on its decision day, returns a `TargetList` (keep / buy candidates / sells with reasons)
    08:00 of i      the venue applies the session's corporate actions (split: shares x ratio; the ledger also
                    books the cash dividend of the previous close's shares)
    09:00 of i      the portfolio builder (`risk/portfolio.py`, configured per rule by `momentum_builder`) turns
                    the target list into whole-share targets (equity marked at the close of i - 1), the runner
                    sends the difference to the venue as `cls` day orders (closing auction; Alpaca accepts them
                    before 15:50 ET and SimBroker enforces that, whole shares only); a sale that found no bar
                    earlier is sent again every session until it fills
    09:30 / 15:59   the venue trades the session's opening print and its closing print; `cls` orders fill at the
                    official close at cost model v1 (half a spread plus impact, as the pre-registrations price it:
                    `broker.sim.AuctionImpactCosts`); the stress fill uses `opg` orders at the open
    16:30 of i      fills are booked to the ledger; a held company whose bars stop is closed at its terminal
                    return; every position is marked at the close; the strategies decide for session i + 1

Sizing and booking (#168, replacing the #161 stand-ins):
- Sizing is the portfolio builder's ranked fill (`PortfolioBuilder(fill="ranked")`), configured per rule with the
  pre-registered k, per-name cap, exposure cap, names, 40% sector cap (Amendment 4), 10% resize buffer and 3% cash
  reserve, at the rule's own limits. It reproduces the research harness's sizing (the momentum core of #127)
  exactly: the parity tests compare orders, fills and equity. The other book caps (gap scenario, cash floor,
  per-name across sleeves) are outer guards: every cut
  they make is in `DailyRun.guards`, and a run with any is not the pre-registered sizing.
- `RunLedger` writes the run's entries to the journal and `engine.ledger.Ledger` folds them: cash, shares, marks
  and venue equity come from the ledger; the runner keeps only the harness's trade rows and daily rows.
- `_venue_actions` applies splits and delistings to `SimBroker`'s positions. Alpaca does this itself; #160's
  `SimBroker.non_trade` activities and `ledger.actions_from_activities` replace it.

Never more than the cash (Amendment 3 of both pre-registrations, the same in the research harness): the builder
fits a decision's purchases in the cash plus its sales at the decision's prices, less a reserve of 3% of
equity, and the venue keeps a cash book checked at the print (`SimBroker(cash_at="fill")`): a buy that would still
exceed the cash at the auction price is cut to the whole shares that fit. Sales of the same print fill first, as
they are sent first, so a rotation funds its purchases. The venue's cash and the ledger's agree at every close
(`DailyRun.cash_gap`); `RunLedger.overdrafts` stays empty.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import half_spread
from kaibot.broker.sim import NY, AuctionImpactCosts, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.journal import Journal, canon
from kaibot.engine.ledger import Book as LedgerBook
from kaibot.engine.ledger import Ledger, price_key
from kaibot.engine.orders import OrderManager, St
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.reconcile import Reconciler
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.replay.history import BarHistory
from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, RiskPolicy
from kaibot.risk.portfolio import Instrument, PortfolioBuilder
from kaibot.strategies.base import Event, TargetList
from kaibot.strategies.momentum import RESIZE_BUFFER, DailyContext

FILL_SESSIONS = 5  # a missing close carries the last one for at most 5 sessions (both pre-registrations)
MIN_PRICE, MIN_ADV = 5.0, 50e6  # the universe filter: price >= $5, 20-day average dollar volume >= $50M
ATR_SESSIONS, ATR_MIN = 20, 15
ATR14, ATR14_MIN, GAP_ATR = 14, 10, 3.0  # the harness's entry also needs a 14-session ATR (its risk column)
SIGMA_FALLBACK = 0.02 / math.sqrt(390)  # the cost model's 1-minute sigma without 20 returns
CASH_RESERVE = 0.03  # Amendment 3 of both pre-registrations: buys are sized against cash minus 3% of equity
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
    sector: dict[str, str] = field(default_factory=dict)  # symbol -> sector (Amendment 4; none: its own sector)

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
                   universe=m.universe, notes=dict(m.notes), sig_unfilled_c=sig.c,
                   sector={raw.symbols[j]: x for j, x in getattr(m, "sector", {}).items()})

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


# ---------------------------------------------------------------- sizing: the portfolio builder (#119, #168)


@dataclass
class Plan:
    sell: dict[str, str]
    resize: list[tuple[str, float]]
    buy: list[tuple[str, float]]
    skipped: int = 0  # buys left out because one share costs more than the position size
    guarded: dict[str, str] = field(default_factory=dict)  # purchases an outer guard cut (none in the prereg)
    basis_gaps: list[str] = field(default_factory=list)  # held names whose mark is not the sizing close


def momentum_limits(strategy, pol: RiskPolicy, exposure_pct: float) -> BookLimits:
    """The book limits a momentum rule trades under: its own limits within the hard ceilings, not a level's
    (Amendment 4 of both pre-registrations; the stakeholder confirms level limits separately, #132). The
    pre-registered settings: k = the rule's `atr_risk`, the per-name cap = the level's `max_position_pct` (Balanced:
    15%, in the frozen construction), the exposure cap as the gross and overnight cap, at most the rule's
    `max_names`, the sector cap at 40% (Amendment 4). The cash floor (1%, below the 3% reserve) and the gap-scenario
    cap (15%) are the hard ceilings: outer guards the pre-registered sizing never reaches (`DailyRun.guards`)."""
    return replace(BOOK_HARD_CEILINGS, atr_risk_pct=float(strategy.atr_risk) * 100,
                   max_name_pct=float(pol.max_position_pct), max_gross_pct=float(exposure_pct),
                   max_overnight_pct=float(exposure_pct), max_names=int(strategy.max_names))


def momentum_builder(strategy, pol: RiskPolicy, exposure_pct: float) -> PortfolioBuilder:
    """The portfolio builder configured to size one momentum rule exactly as its pre-registration does: ranked fill
    (sells, resizes with reductions first, buys best first until the room runs out), k = the rule's `atr_risk`, a
    resize only outside a 10% buffer on resize weeks (`TargetList.resize`), Amendment 3's 3% cash reserve."""
    b = PortfolioBuilder(momentum_limits(strategy, pol, exposure_pct), k=float(strategy.atr_risk),
                         band=RESIZE_BUFFER, fill="ranked", cash_reserve=CASH_RESERVE)
    assert min(b.k, b.limits.atr_risk_pct / 100) == b.k  # the rule's k is used as it is, never clamped
    return b


def builder_sizing(builder: PortfolioBuilder, tl: TargetList, held: Mapping[str, tuple[float, float]],
                   pending: Mapping[str, str], data: DailyData, i: int, eq: float, cash: float) -> Plan:
    """The builder's plan for one target list, in the runner's shape. `held`: symbol -> (shares, last mark) after
    the session's corporate actions; `pending`: sales still waiting for a bar (they stay sales); `eq`: equity marked
    at the close of i - 1; `cash`: the account's cash before the session's fills. A held name is priced at its mark
    (what the venue holds it at), a candidate at the close of i - 1 in the shares of session i, both with ATR(20)
    of that close. `basis_gaps`: held names whose mark is not that close (a hole longer than the 5-session carry, or
    on a split day): there the harness sizes the per-name cap on the carried close and the builder on the mark."""
    k, name = i - 1, tl.strategy
    reasons = {s: pending.get(s, r) for s, r in tl.features.get("sell", {}).items()}
    reasons.update({s: r for s, r in pending.items() if s in held and s not in reasons})
    weights = {s: w for s, w in tl.weights.items() if s not in pending}
    market: dict[str, Instrument] = {}
    gaps: list[str] = []
    for s in [*held, *weights]:
        if s in market:
            continue
        j = data.col(s)
        sj = data.split_at(i, j)
        atr, px = float(data.atr20[k, j]) / sj, float(data.px[k, j]) / sj
        if s in held:  # without a carried close the harness does not resize it: no ATR, so the builder keeps it
            last = float(held[s][1])
            if math.isfinite(px) and px != last:
                gaps.append(s)
            market[s] = Instrument(last, atr if math.isfinite(px) else math.nan, data.sector.get(s))
        else:  # the harness refuses an entry without the previous close or a 14-session ATR, after sizing it
            a14 = data.atr14[k, j]
            market[s] = Instrument(px, atr, data.sector.get(s),
                                   can_open=bool(math.isfinite(data.raw_c[k, j]) and math.isfinite(a14) and a14 > 0))
    p = builder.build([replace(tl, weights=weights)], {(name, s): q for s, (q, _) in held.items()}, market,
                      equity=eq, cash=cash)
    sell = {s: reasons.get(s, "rank") for (_, s) in p.sequence if p.targets[(name, s)] == 0}
    resize = [(s, float(p.targets[(name, s)])) for (_, s) in p.sequence if s in held and p.targets[(name, s)] > 0]
    buy = [(s, float(p.targets[(name, s)])) for (_, s) in p.sequence if s not in held]
    skipped = sum(1 for x in p.skipped if x.reason == "unaffordable" and x.symbol not in held)
    return Plan(sell, resize, buy, skipped, {s: why for (_, s), why in p.guarded.items()}, gaps)


# ---------------------------------------------------------------- the ledger (#117) and the trade rows


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
class _Stats:
    """What a trade row needs about an open position that the ledger does not keep."""

    qty: float
    entry_i: int
    fill: float
    mid: float
    half: float
    imp: float
    risk: float
    equity: float
    dividends: float = 0.0


@dataclass
class Book:
    """The run's research view: what the Gate B report and the parity test read."""

    equity: float
    trades: list[Trade] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)
    bought: float = 0.0
    sold: float = 0.0
    dividends: float = 0.0


class RunLedger:
    """One strategy's run: entries in the ledger's journal shapes (`run`, `fill`, `dividend`, `split`, `mark`;
    every body carries `run_id` and `strategy`; marks keyed `strategy|symbol`; cash in lieu of a split's fraction
    through the split entry's `price`; a delisting is a `fill` without fees), written to the journal first and
    folded by `engine.ledger.Ledger`, the source of truth for cash, shares, marks and venue equity
    (`close_equity_venue`). The runner keeps only what the ledger does not have: the harness's trade rows and daily
    rows (`book`). `Ledger.fold(self.entries)` gives the same book."""

    def __init__(self, strategy: str, rule: str, data: DailyData, amount: float, mult: float,
                 journal: Journal | None = None, run_id: int = 1) -> None:
        self.strategy, self.rule, self.d, self.mult, self.run_id = strategy, rule, data, mult, run_id
        self.journal = journal
        self.entries: list[tuple[str, dict]] = []
        self.ledger = Ledger()
        self.equity = float(amount)  # venue equity at the last close
        self.stats: dict[str, _Stats] = {}
        self.book = Book(float(amount))
        self.overdrafts: list[tuple[str, float]] = []
        self._day = {"cost": 0.0, "traded": 0.0, "dividends": 0.0, "exits": 0}
        self.apply("run", {"amount": float(amount)})

    # -- reads (from the ledger)

    @property
    def lb(self) -> LedgerBook:
        return self.ledger.book(self.run_id)

    @property
    def cash(self) -> float:
        return self.lb.cash

    @property
    def pos(self) -> dict[str, float]:
        """symbol -> shares of this strategy's sleeves, in the book's order."""
        return {sym: s.qty for (st, sym), s in self.lb.sleeves.items() if st == self.strategy and s.qty > 0}

    def held(self) -> dict[str, tuple[float, float]]:
        """symbol -> (shares, last mark)."""
        return {sym: (s.qty, s.mark) for (st, sym), s in self.lb.sleeves.items() if st == self.strategy and s.qty > 0}

    def exposure(self) -> float:
        return sum(s.qty * s.mark for s in self.lb.sleeves.values() if s.mark is not None)

    # -- writes

    def apply(self, kind: str, body: dict) -> None:
        body = canon({"run_id": self.run_id, "strategy": self.strategy, **body})
        if self.journal is not None:
            self.journal.append(kind, body)
        self.entries.append((kind, body))
        self.ledger.apply(kind, body)
        getattr(self, f"_{kind}")(body)

    def mark(self, i: int, session: str, prices: Mapping[str, float]) -> None:
        """The close: every open sleeve marked at its finite close (a session without a bar keeps the last mark)."""
        px = {price_key(self.strategy, s): float(p) for s, p in prices.items() if math.isfinite(p) and s in self.pos}
        b = self.lb
        venue = b.cash + sum(s.qty * px.get(price_key(*k), s.mark if s.mark is not None else s.avg_cost)
                             for k, s in b.sleeves.items())
        self.apply("mark", {"i": i, "session": session, "prices": px, "pnl_venue": venue - self.equity,
                            "pnl_shadow": venue - self.equity})

    # -- the trade-row statistics, from the same entries

    def _run(self, e: dict) -> None:
        pass

    def _fill(self, e: dict) -> None:
        i, s, qty, px, mid = e["i"], e["symbol"], float(e["qty"]), float(e["px"]), float(e["mid"])
        fees = float(e.get("fees", 0.0))
        costless = e.get("reason") == "delisted"
        half = 0.0 if costless else half_spread(mid) * self.mult
        if e["side"] == "buy":
            imp = px - mid - half
            p = self.stats.get(s)
            if p is None:
                j = self.d.col(s)
                atr = self.d.atr14[i - 1, j] / self.d.split_at(i, j)
                self.stats[s] = _Stats(qty, i, px, mid, half, imp, qty * GAP_ATR * atr, e["equity"])
            else:
                q = p.qty + qty
                p.half, p.imp = (p.half * p.qty + half * qty) / q, (p.imp * p.qty + imp * qty) / q
                p.fill, p.mid = (p.fill * p.qty + px * qty) / q, (p.mid * p.qty + mid * qty) / q
                p.risk *= q / p.qty
                p.qty = q
            self.book.bought += px * qty
            self._day["cost"] += (px - mid) * qty
            self._day["traded"] += px * qty
            return
        self._sold(i, s, qty, px, mid, fees, half, costless, e["reason"], e.get("at", "close"))

    def _sold(self, i: int, s: str, qty: float, px: float, mid: float, fees: float, half: float, costless: bool,
              reason: str, at: str) -> None:
        p = self.stats[s]
        lot = min(qty, p.qty)
        risk, divs = p.risk * (lot / p.qty), p.dividends * (lot / p.qty)
        if lot >= p.qty - 1e-12:
            del self.stats[s]
        else:
            p.qty -= lot
            p.risk -= risk
            p.dividends -= divs
        x_imp = 0.0 if costless else abs(px - mid) - half
        self.book.sold += px * lot
        self._day["cost"] += (mid - px) * lot + fees
        self._day["traded"] += px * lot
        d = self.d.dates
        self.book.trades.append(Trade(
            s, d[p.entry_i], d[i], i - p.entry_i, lot, p.fill, px, math.nan, reason, (px - p.fill) * lot - fees,
            risk, (mid - p.mid) * lot, (p.half + half) * lot, (p.imp + x_imp) * lot, 0.0, fees, False, p.equity,
            self.rule, self.strategy, at, divs))
        self._day["exits"] += 1

    def _dividend(self, e: dict) -> None:
        p = self.stats.get(e["symbol"])
        if p is not None:
            paid = p.qty * float(e["per_share"])
            p.dividends += paid
            self.book.dividends += paid
            self._day["dividends"] += paid

    def _split(self, e: dict) -> None:
        p, r = self.stats.get(e["symbol"]), float(e["ratio"])
        if p is None:
            return
        p.qty *= r
        p.fill, p.mid, p.half, p.imp = (x / r for x in (p.fill, p.mid, p.half, p.imp))
        frac = p.qty - math.floor(p.qty + 1e-9)
        if frac > 1e-9 and e.get("price") is not None:  # the ledger paid the fraction in cash: its trade row
            px = float(e["price"])
            self._sold(e["i"], e["symbol"], frac, px, px, 0.0, 0.0, True, "cash_in_lieu", "open")

    def _mark(self, e: dict) -> None:
        eq = self.equity
        c = self.lb.closes[-1]
        self.equity = c.close_equity_venue
        if c.cash < -1e-6:
            self.overdrafts.append((str(e["session"]), c.cash))
        n = len(self.pos)
        self.book.daily.append({"date": self.d.dates[e["i"]], "start": eq, "end": self.equity,
                                "pnl": self.equity - eq, "trades": self._day["exits"], "positions": n,
                                "exposure": c.invested / self.equity if self.equity > 0 else 0.0,
                                "cost": self._day["cost"], "traded": self._day["traded"],
                                "dividends": self._day["dividends"]})
        self._day = {"cost": 0.0, "traded": 0.0, "dividends": 0.0, "exits": 0}


# ---------------------------------------------------------------- the runner


@dataclass
class DailyRun:
    """What one run of one strategy through the engine path produced."""

    strategy: str
    book: Book
    ledger: RunLedger
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
    cash_gap: float = 0.0  # largest |venue cash - ledger cash| at any close
    om: OrderManager | None = None  # every order of the run went through it (#118)
    refused: list[tuple[int, list]] = field(default_factory=list)  # sessions reconciliation refused to trade
    stops: dict = field(default_factory=dict)  # (strategy, symbol) -> protective stop price, when protect_atr
    guards: list[tuple[int, str, str]] = field(default_factory=list)  # (session, symbol, guard) an outer cap cut
    basis_gaps: list[tuple[int, str]] = field(default_factory=list)  # (session, held symbol) see builder_sizing

    @property
    def entries(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for i, _, _ in self.opened:
            out[i] = out.get(i, 0) + 1
        return out


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, NY).astimezone(UTC)


def protect_sleeves(stops: ProtectiveStops, strategy: str, pos: Mapping[str, float], prices: Mapping, now: datetime,
                    session: str, only: set[str] | None = None) -> list[str]:
    """One strategy's protective stops (`ensure` for its sleeves `pos`, shares per symbol), scoped to that
    strategy (issue #199): when two strategies share one order manager, this call never cancels the other
    strategy's stops as "gone" (its sleeves are not in `pos`). `only`: these symbols alone (a split)."""
    sleeves = {(strategy, s): q for s, q in pos.items() if only is None or s in only}
    return stops.ensure(sleeves, prices, now, session, only=only, strategies={strategy})


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
              on_session: Callable[[int], None] | None = None, protect_atr: float | None = None) -> DailyRun:
    """One continuous account of one multi-day strategy over sessions i0..i1, starting flat: the strategy decides
    after each close, the portfolio builder sizes (`momentum_builder`: the pre-registered sizing at the rule's own
    limits), the target loop (engine/targets.py) sends
    target minus held minus working through the order manager (engine/orders.py) as closing-auction orders,
    `SimBroker` fills them at the next close (`fill="base"`) or open (`"stress"`, the research cost model), the
    manager books the venue's fills from its account activities and the ledger books them (`RunLedger`). Each
    session starts with a reconciliation of the ledger with the venue (shares and cash); a difference refuses
    that session's orders (`DailyRun.refused`).
    `protect_atr`: a protective GTC stop that far below the entry (in ATR(20)) rests at the venue for every
    position (decision 15; off by default: the pre-registrations have no stop, so the Gate B runs keep none).
    What is still held after i1 is valued at its last mark less the cost model's sale (the harness's
    `end_of_data`), so every dollar of the equity curve is in a trade row."""
    if i0 < 1 or i1 >= len(data.dates) or i0 > i1:
        raise ValueError("sessions i0..i1 must lie inside the data, with i0 >= 1")
    where, factor = FILLS[fill]
    mult = cost * factor
    tif = "cls" if where == "close" else "opg"
    costs = AuctionImpactCosts(sigma=lambda sym, day: data.sigma_at(data.index_of(day), data.col(sym)), mult=mult)
    clock = SimClock(_at(data.day(i0 - 1), AFTER_CLOSE) - timedelta(minutes=1))
    broker = SimBroker(data.bar_history(), clock, costs=costs, cash=equity0, calendar=data.calendar(),
                       cash_at="fill")
    journal = Journal(now=clock)
    led = RunLedger(strategy.name, strategy.rule, data, equity0, mult, journal)
    om = OrderManager(broker, journal, clock)
    builder = momentum_builder(strategy, pol, exposure_pct)
    run = DailyRun(strategy.name, led.book, led, [], [], {}, [], 0, [], [], broker, i0, i1, om=om)
    policy = ExecutionPolicy("close" if tif == "cls" else "open", research_open=tif == "opg")
    at_close = ExecutionPolicy("close")
    stops = ProtectiveStops(om) if protect_atr else None
    recon = Reconciler(lambda: {"ledger_qty": led.pos,
                                "broker_positions": broker.positions(), "om": om, "ledger_cash": led.cash,
                                "ledger_cash0": float(equity0), "broker_cash": broker.cash,
                                "broker_cash0": float(equity0), "equity": led.equity}, om.journal)
    pending: dict[str, str] = {}
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

    def send(i: int, targets: list[Target], pol_: ExecutionPolicy) -> None:
        """The target loop, in the plan's order (the venue fills a print in the order the orders arrived)."""
        held_now = {(strategy.name, s): q for s, (q, _) in led.held().items()}
        res = reconcile_targets(om, pol_, targets, held_now, now=clock(), session=str(data.dates[i]),
                                reconciler=recon, stops=stops, sells_first=False)
        for r in res.sent:
            if r.state is St.REJECTED:
                run.rejected.append((i, r.symbol, (r.reason or "")[:120]))
            else:
                sent[r.coid] = (r.purpose, r.symbol, r.meta.get("why"))
        for (_, sym), why in res.skipped:
            run.rejected.append((i, sym, why[:120]))

    name = strategy.name
    decide(i0 - 1)
    for i in range(i0, i1 + 1):
        d = data.day(i)
        # -- before the open: corporate actions of the session
        clock.set(_at(d, CORP_ACTIONS))
        for s in list(led.pos):
            j = data.col(s)
            dv = float(data.div[i, j]) if data.div is not None else 0.0
            if math.isfinite(dv) and dv > 0:
                broker.cash += led.pos[s] * dv  # the venue credits the dividend too (Alpaca: DIV activity)
                led.apply("dividend", {"symbol": s, "ex_date": str(d), "per_share": dv, "i": i})
            r = data.split_at(i, j)
            if r != 1.0:
                if stops is not None:  # protective orders are cancelled before a split and re-created after it
                    stops.before_split(s)
                    om.sync()
                _venue_actions(broker, s, split=r)
                q, last = led.held()[s]
                exact = q * r
                frac = exact - math.floor(exact + 1e-9)
                px = last / r  # the fraction of a share is paid in cash (Alpaca), at the previous close / ratio
                led.apply("split", {"symbol": s, "ex_date": str(d), "ratio": r, "i": i, "price": px})
                if (name, s) in run.stops:
                    run.stops[(name, s)] = round(run.stops[(name, s)] / r, 2)
                if stops is not None:  # re-protected at once, before the open: no session without a stop
                    stops.after_split(s)
                    protect_sleeves(stops, name, {s: led.pos.get(s, 0.0)}, run.stops, clock(), str(d), only={s})
                if frac > 1e-9:
                    broker.pos[s]["qty"] = int(math.floor(float(broker.pos[s]["qty"]) + 1e-9))
                    broker.cash += frac * px
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
        targets: list[Target] = []
        if recon.check("startup" if i == i0 else "session_start").diffs:  # the ledger and the venue disagree:
            run.refused.append((i, recon.last.summary()["diffs"]))  # the target loop refuses buys, sales go out
        if tl is not None:
            plan = builder_sizing(builder, tl, held, pending, data, i, eq, led.cash)
            run.skipped += plan.skipped
            run.guards += [(i, s, why) for s, why in plan.guarded.items()]
            run.basis_gaps += [(i, s) for s in plan.basis_gaps]
            run.orders += [(i, "sell", s, r) for s, r in plan.sell.items()]
            run.orders += [(i, "resize", s, t) for s, t in plan.resize] + [(i, "buy", s, t) for s, t in plan.buy]
            targets += [Target(name, s, 0, "sell", {"why": r}) for s, r in plan.sell.items()]
            # a resize of a name sold at this close anyway (a split the data disagree about is next) is not sent
            targets += [Target(name, s, int(t), "resize", {"why": t}) for s, t in plan.resize if s not in forced]
            targets += [Target(name, s, int(t), "buy", {"why": t}) for s, t in plan.buy]
            pending = {}
        else:  # a sale whose stock had no bar: try again every session
            targets += [Target(name, s, 0, "sell", {"why": r}) for s, r in pending.items()]
        send(i, targets, policy)
        sold = {t.symbol for t in targets if t.shares == 0}
        send(i, [Target(name, s, 0, "split_dispute", {"why": "split_dispute"}) for s in forced if s not in sold],
             at_close)  # at the close, always
        # -- the session: opening print, closing print
        broker.advance(_at(d, OPEN_BAR))
        clock.set(_at(d, CLOSE_BAR))
        broker.advance(_at(d, CLOSE_BAR))
        # -- after the close: book the fills, terminal closes, marks
        clock.set(_at(d, AFTER_CLOSE))
        filled: set[str] = set()
        for f in om.sync():  # the venue's fills, from its account activities, each booked once
            r = om.get(f.coid)
            if r.purpose == "stop":  # a protective stop fired: a sale like any other, at the stop's fill
                sent.setdefault(f.coid, ("sell", f.symbol, "protective_stop"))
            kind, s, why = sent.get(f.coid, ("sell", f.symbol, "sell"))
            j = data.col(s)
            qty, px = f.qty, f.px
            at = "close" if r.tif == "cls" else "open"
            mid = float(data.raw_c[i, j] if at == "close" else data.raw_o[i, j])
            ref = float(data.px[i - 1, j]) / data.split_at(i, j)
            reason = why if kind != "resize" else "resize"
            led.apply("fill", {"i": i, "session": str(d), "symbol": s, "side": f.side, "qty": qty, "px": px,
                               "mid": mid, "fees": f.fees, "reason": reason, "at": at, "equity": eq, "ref": ref,
                               "coid": f.coid})
            run.fills.append({"i": i, "symbol": s, "side": f.side, "qty": qty, "px": px, "fees": f.fees,
                              "reason": reason})
            filled.add(f.coid)
            if stops is not None and f.side == "buy" and (name, s) not in run.stops:
                run.stops[(name, s)] = round(ref - protect_atr * float(data.atr20[i - 1, j]) / data.split_at(i, j),
                                             2)
            if kind == "buy":
                run.opened.append((i, j, qty * ref / eq))
            elif kind == "resize" and s in led.pos:
                run.resized.append((i, j, led.pos[s] * ref / eq))
        for coid, (kind, s, why) in sent.items():
            if coid not in filled and kind in ("sell", "split_dispute") and s in led.pos:
                pending[s] = why  # a sale without a bar waits for the next session that has one
        for s in list(led.pos):
            j = data.col(s)
            term = data.terminal.get(j)
            if term is not None and term[0] == i and math.isfinite(data.raw_c[i, j]):
                px = max(float(data.raw_c[i, j]) * (1.0 + term[1]), 0.0)  # bars stop: closed at the terminal return
                _venue_actions(broker, s, delist=True)
                broker.cash += led.pos[s] * px
                run.fills.append({"i": i, "symbol": s, "side": "sell", "qty": led.pos[s], "px": px, "fees": 0.0,
                                  "reason": "delisted"})
                led.apply("fill", {"i": i, "session": str(d), "symbol": s, "side": "sell", "qty": led.pos[s],
                                   "px": px, "mid": px, "fees": 0.0, "reason": "delisted", "at": "close"})
                pending.pop(s, None)
        led.mark(i, str(d), {s: float(data.raw_c[i, data.col(s)]) for s in led.pos})
        if stops is not None:  # every position keeps a protective GTC stop at the venue, in whole shares
            run.stops = {k: v for k, v in run.stops.items() if k[1] in led.pos}
            stops.suspended.clear()
            protect_sleeves(stops, name, led.pos, run.stops, clock(), str(d))
        run.cash_gap = max(run.cash_gap, abs(broker.cash - led.cash))  # the venue's cash book against the ledger
        if on_session:
            on_session(i)
        if i < i1:
            decide(i)
    # -- end of data: what is still held is valued at its last mark less the modelled sale
    if led.pos:
        row = led.book.daily[-1]
        cost0, traded0 = led._day["cost"], led._day["traded"]
        for s, (q, last) in list(led.held().items()):
            j = data.col(s)
            vol = float(data.v_last[i1, j]) if math.isfinite(data.v_last[i1, j]) and data.v_last[i1, j] > 0 else 1.0
            px = costs.price("sell", last, q, data.sigma_at(i1, j), vol)
            fees = costs.fees("sell", q, px)
            run.fills.append({"i": i1, "symbol": s, "side": "sell", "qty": q, "px": px, "fees": fees,
                              "reason": "end_of_data"})
            led.apply("fill", {"i": i1, "session": str(data.day(i1)), "symbol": s, "side": "sell", "qty": q,
                               "px": px, "mid": last, "fees": fees, "reason": "end_of_data", "at": "close"})
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


__all__: Sequence[str] = ("DailyData", "DailyView", "Plan", "momentum_limits", "momentum_builder", "builder_sizing",
                          "RunLedger", "Book", "Trade", "DailyRun", "run_daily", "sessions_window", "daily_returns")
