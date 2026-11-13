"""The live daily path (issue #206; decision 15 sections 3, 5 and 11): multi-day rules in the live engine on Alpaca
paper, through the SAME decision core as the research runner (engine/daily.py). Only with KAIBOT_MULTIDAY=on (the
engine builds this object only then; with the flag off nothing here is imported and the engine is unchanged).

Nothing here is a second implementation of the rule or of its sizing. What it reuses:

    the daily view           engine/daily.DailyData / DailyView (point-in-time daily bars, the research loader
                             engine/daily_report.load_store on the daily store, rows <= the decision's close only)
    the decision             engine/daily.decide_after_close -> strategies/momentum (the frozen rule)
    the sizing               engine/daily.plan_session -> risk/portfolio.PortfolioBuilder (ranked fill, 3% cash
                             reserve, 40% sector cap at most), at the live book's limits (`live_limits`)
    the orders               engine/targets.reconcile_targets -> engine/execution.ExecutionPolicy("close") (`cls`
                             day orders, whole shares, before 15:50 ET) -> engine/orders.OrderManager (deterministic
                             client ids, intent journalled before the call, unknown orders looked up by client id)
    the fills                OrderManager(run_id=...): every FILL activity of its orders becomes the ledger's own
                             `fill` entry in one journal append; the engine folds it into its ledger (`book_fill`)
    reconciliation, stops    the engine's Reconciler (engine/reconcile.py) and ProtectiveStops (engine/protect.py:
                             fail-safe stops of the watchdog / dead-man are adopted and released before a sale)

The steps, per session (times ET):

    after the close of k     `after_close`: once the daily store holds session k (its bars carry `available_ts`,
                             20:15 ET; nothing is read before it) each enabled rule decides for the next session
                             and the target list is journalled (`md_decision`), once per (rule, session)
    session i, 09:31-15:45   `send`: the target list decided after k (i's previous session) is sized by the
                             portfolio builder against the ledger (equity at the last close, cash) and the targets
                             are journalled (`md_send`) BEFORE any order; the target loop sends the difference as
                             `cls` orders; `md_sent` closes the step. Sales go out even when entries are blocked;
                             buys need `entry_block() is None` and a clean reconciliation
    every tick               `sync`: the order manager books the venue's fills (the closing auction's at 16:00)

Restart safety: a restart between the decision and the send finds `md_decision` and sends from it (the rule is not
asked again); a restart in the middle of a send finds `md_send` without `md_sent` and runs the target loop again
on the SAME journalled targets (the open orders cover them, so nothing is sent twice; client ids are deterministic
and the venue refuses a second copy); a restart between order and fill finds the orders in the journal, looks
unconfirmed ones up by client id and books each fill once by its activity id.

Overnight: the book's limits come from config/profiles.yaml (#205): the level's multi-day book, the rule's exposure
cap, BOOK_HARD_CEILINGS, and `max_overnight_pct` = 0 while `all_levels.allow_overnight` is false, so then nothing
is bought (decisions are still journalled, sales still go out). The close-minus-5 flatten of the engine sells
intraday sleeves only (core.INTRADAY_ONLY); these sleeves are never `Pos` objects of the engine.

Protective stops: neither momentum pre-registration has a stop ("No fixed stop", docs/prereg/*.md), so none is
placed (`PREREG_STOP_ATR`). A rule whose pre-registration has one is refused here until the stop path is wired.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from kaibot.broker.protocol import Broker
from kaibot.engine.daily import CASH_RESERVE, DailyData, decide_after_close, momentum_limits, plan_session
from kaibot.engine.execution import NY, ExecutionPolicy
from kaibot.engine.orders import UNCONFIRMED, Fill, OrderBlocked, OrderManager, OrderRecord, St
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.risk.gate import BOOK_FLOORS, BOOK_HARD_CEILINGS, BookLimits, RiskPolicy, validate_book
from kaibot.risk.portfolio import PortfolioBuilder
from kaibot.strategies.base import TargetList
from kaibot.strategies.momentum import RESIZE_BUFFER

log = logging.getLogger("kaibot.engine.daily")

SEND_FROM, SEND_UNTIL = time(9, 31), time(15, 45)  # Alpaca refuses `cls` orders 15:50-19:00: five minutes' margin
DAILY_KINDS = ("md_decision", "md_send", "md_sent", "md_cancel")
FOREIGN_MAX_AGE = timedelta(days=3)  # see LiveDaily._prune
AUCTION, POLL_EVERY = time(15, 59), timedelta(minutes=15)  # see LiveDaily.sync
# The protective stop each rule's pre-registration declares, in ATR(20) below the entry (None: the rule has none).
PREREG_STOP_ATR: dict[str, float | None] = {"trend_momentum": None, "mom_12_1_band": None,
                                            "mom_12_1_band_nofilter": None}


class DataNotReady(RuntimeError):
    """The daily store does not hold the decision's session yet (or not as available at this time)."""


class Account(Protocol):
    """What the daily path needs from the engine (or, in the parity test, from a SimBroker account and a ledger)."""

    def run_id(self) -> int | None: ...

    def equity(self) -> float: ...  # the run's venue equity at the last close (what the builder sizes against)

    def cash(self) -> float: ...  # the ledger's cash now

    def held(self, strategy: str) -> dict[str, tuple[float, float | None]]: ...  # symbol -> (shares, last mark)

    def book_fill(self, body: dict) -> None: ...  # fold a `fill` entry the order manager journalled

    def entry_block(self) -> str | None: ...  # why no position may be opened now (None: it may)

    def allows(self, symbol: str, side: str, purpose: str, qty: float | None) -> str | None: ...  # reconciliation

    def splits(self, day: date) -> dict[str, float]: ...  # splits the ledger booked for this session: symbol -> ratio

    def book_ended(self) -> bool: ...  # the multi-day book reached its own run end this run (no more trading)


# ---------------------------------------------------------------- the live book's limits


def live_limits(strategy, pol: RiskPolicy, book: BookLimits) -> BookLimits:
    """The pre-registered limits of the rule (engine/daily.momentum_limits: k, per-name cap, max names, 40% sector)
    tightened field by field by the live book of #205 (`risk/profiles.rule_book_limits`: the level's multi-day book,
    the rule's exposure cap per level, and overnight 0 while allow_overnight is false). Never looser than either,
    never looser than BOOK_HARD_CEILINGS (the cash floor: the larger floor)."""
    pre = momentum_limits(strategy, pol, book.max_gross_pct)
    vals = {}
    for f in BookLimits.__dataclass_fields__:
        a, b = getattr(pre, f), getattr(book, f)
        vals[f] = max(a, b) if f in BOOK_FLOORS else min(a, b)
    out = BookLimits(**{**vals, "max_names": int(vals["max_names"])})
    validate_book(out)
    validate_book(out, BOOK_HARD_CEILINGS)
    return out


def live_builder(strategy, limits: BookLimits) -> PortfolioBuilder:
    """`engine/daily.momentum_builder`'s construction (ranked fill, the rule's k, the 10% resize buffer, the 3% cash
    reserve) at `limits`. The rule's k is never clamped: a book whose k is below the rule's refuses to build."""
    b = PortfolioBuilder(limits, k=float(strategy.atr_risk), band=RESIZE_BUFFER, fill="ranked",
                         cash_reserve=CASH_RESERVE)
    if b.k > b.limits.atr_risk_pct / 100 + 1e-12:
        raise ValueError(f"{strategy.name}: the book's atr_risk_pct {limits.atr_risk_pct} is below the rule's k")
    return b


# ---------------------------------------------------------------- the journal shapes


def target_to_json(tl: TargetList | None) -> dict | None:
    """A target list as the journal keeps it. Orders matter (the builder buys in `weights` order), so mappings are
    written as lists of pairs (the journal's JSON keeps lists in order)."""
    if tl is None:
        return None
    f = dict(tl.features)
    sell = f.pop("sell", {}) or {}
    return {"strategy": tl.strategy, "session": str(tl.session), "weights": [[s, float(w)] for s, w in
                                                                            tl.weights.items()],
            "sizing": tl.sizing, "reasons": list(tl.reasons), "index_filter": bool(tl.index_filter),
            "resize": bool(tl.resize), "features": f, "sell": [[s, r] for s, r in sell.items()]}


def target_from_json(d: dict | None) -> TargetList | None:
    if d is None:
        return None
    features = {**(d.get("features") or {}), "sell": {s: r for s, r in d.get("sell") or []}}
    return TargetList(d["strategy"], date.fromisoformat(d["session"]), {s: float(w) for s, w in d["weights"]},
                      sizing=d.get("sizing", "atr_risk"), reasons=tuple(d.get("reasons") or ()), features=features,
                      index_filter=bool(d.get("index_filter")), resize=bool(d.get("resize", True)))


# ---------------------------------------------------------------- the daily data, point-in-time


def with_session(data: DailyData, day: date, splits: Mapping[str, float] | None = None) -> DailyData:
    """`data` (rows up to the decision's close) with one more row for the fill session `day`: no prices (nothing of
    that session is known yet), only the splits the ledger booked at its open (shares per share). The builder reads
    row i only for the split; every other read is of row i - 1."""
    n = len(data.symbols)
    nan = np.full((1, n), np.nan)

    def add(a):
        return None if a is None else np.vstack([a, nan.astype(a.dtype) if a.dtype.kind == "f" else
                                                 np.zeros((1, n), dtype=a.dtype)])

    split = np.ones((len(data.dates) + 1, n)) if data.split is None else np.vstack([data.split, np.ones((1, n))])
    for s, r in (splits or {}).items():
        if s in data._col and r and math.isfinite(r) and r > 0:
            split[-1, data.col(s)] = float(r)
    div = None if data.div is None else np.vstack([data.div, np.zeros((1, n))])
    member = None if data.member is None else np.vstack([data.member, data.member[-1:]])
    return DailyData(
        dates=np.r_[data.dates, np.array([np.datetime64(day, "D")])], symbols=list(data.symbols),
        sig_c=add(data.sig_c), sig_h=add(data.sig_h), sig_l=add(data.sig_l), raw_o=add(data.raw_o),
        raw_c=add(data.raw_c), raw_h=add(data.raw_h), raw_l=add(data.raw_l), v_first=add(data.v_first),
        v_last=add(data.v_last), raw_v=add(data.raw_v), stocks=data.stocks, spy=data.spy, member=member, split=split,
        div=div, terminal={}, exits={}, spy_tr=None, universe=data.universe, notes=dict(data.notes),
        sig_unfilled_c=add(data.sig_unfilled_c), sector=dict(data.sector))


def as_of(data: DailyData, k: int) -> DailyData:
    """The same data as it stood after the close of row k: rows > k are not there (the parity test's loader: what the
    live loader reads from the daily store on the evening of session k)."""
    cut = slice(0, k + 1)

    def c(a):
        return None if a is None else a[cut]

    return DailyData(
        dates=data.dates[cut], symbols=list(data.symbols), sig_c=c(data.sig_c), sig_h=c(data.sig_h),
        sig_l=c(data.sig_l), raw_o=c(data.raw_o), raw_c=c(data.raw_c), raw_h=c(data.raw_h), raw_l=c(data.raw_l),
        v_first=c(data.v_first), v_last=c(data.v_last), raw_v=c(data.raw_v), stocks=data.stocks, spy=data.spy,
        member=c(data.member), split=c(data.split), div=c(data.div), terminal={},
        exits={j: [s for s in v if s <= k] for j, v in data.exits.items()}, spy_tr=c(data.spy_tr),
        universe=data.universe, notes=dict(data.notes), sig_unfilled_c=c(data.sig_unfilled_c),
        sector=dict(data.sector))


class StoreLoader:
    """The daily view of the evening of session k from the daily store (`<root>/daily`, written by
    scripts/fetch_daily.sh on the Mac; free Alpaca history under the shared budget). The same preparation as the
    ten-year test (`engine/daily_report.load_store`, `mode` "pit": the S&P 500 members of each date), read only up to
    k and only once k's bars are available (`available_ts`, 20:15 ET): otherwise DataNotReady (fail closed: no
    decision, no order). Live, a held company whose bars stop is the venue's business (Alpaca books a delisting),
    so the research runner's terminal closes are dropped."""

    LOOKBACK_DAYS = 600  # ~410 sessions: the longest signal window (SPY's 200-day average) plus ATR and ADV

    def __init__(self, root: Path, universe_file: Path, mode: str = "pit", market: str = "SPY") -> None:
        self.root, self.universe_file, self.mode, self.market = Path(root), Path(universe_file), mode, market

    def available(self, k: date, now: datetime) -> str | None:
        """None when session k's bars are in the store and were available at `now`; else why not."""
        from kaibot.ingest.daily_store import DailyStore

        store = DailyStore(self.root / "daily")
        try:
            b = store.bars(self.market, start=k, end=k)
        except (FileNotFoundError, OSError) as e:
            return f"no daily store at {self.root / 'daily'} ({type(e).__name__})"
        if not len(b):
            return f"the daily store has no {self.market} bar for {k} (run ./scripts/fetch_daily.sh after 20:15 ET)"
        ts = b["available_ts"].iloc[-1] if "available_ts" in b else None
        if ts is None or pd.isna(ts):
            return f"{self.market} {k}: the bar has no available time (not written by scripts/fetch_daily.py)"
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        if ts.to_pydatetime() > now:
            return f"{self.market} {k}: the bar is available from {ts.isoformat()}, not yet at {now.isoformat()}"
        return None

    def __call__(self, k: date, now: datetime) -> DailyData:
        why = self.available(k, now)
        if why:
            raise DataNotReady(why)
        from kaibot.engine import daily_report as dr

        try:
            m, _ = dr.load_store(self.root, self.universe_file, self.mode, str(k - timedelta(days=self.LOOKBACK_DAYS)),
                                 str(k))
        except (SystemExit, ValueError, FileNotFoundError) as e:
            raise DataNotReady(f"daily store unreadable: {e}") from None
        data = DailyData.from_market(m)
        if data.dates[-1].astype(object) != k:
            raise DataNotReady(f"the daily view ends {data.dates[-1]}, not {k}")
        why = self.incomplete(data)
        if why:
            raise DataNotReady(why)
        data.terminal = {}
        return data

    MIN_COVERAGE = 0.97  # of the stocks that had a bar the session before: a normal day misses a few halted names

    @classmethod
    def incomplete(cls, data: DailyData) -> str | None:
        """QA-Ops review of #207: SPY's bar of k says only that the refresh got as far as SPY. A refresh that stopped
        part-way (HTTP error, budget) leaves most stocks without k, and the view would carry their last close
        forward: a decision on yesterday's prices. So the view is ready only when at least MIN_COVERAGE of the stocks
        that had an (unfilled) close at k - 1 also have one at k; otherwise DataNotReady (no decision)."""
        c = data.sig_unfilled_c if data.sig_unfilled_c is not None else data.raw_c
        if c is None or len(data.dates) < 2 or not len(data.stocks):
            return None
        cols = np.asarray(data.stocks, dtype=int)
        before = np.isfinite(c[-2, cols])
        if data.member is not None:
            before &= np.asarray(data.member[-2, cols], dtype=bool)
        n = int(before.sum())
        if n == 0:
            return None
        have = int((before & np.isfinite(c[-1, cols])).sum())
        if have < cls.MIN_COVERAGE * n:
            return (f"the daily store holds {data.dates[-1]} for only {have} of {n} stocks: the refresh has not "
                    "finished (run ./scripts/fetch_daily.sh again)")
        return None


def store_state(root: Path, market: str = "SPY") -> dict:
    """`--check`: where the daily store is and the last session it holds."""
    from kaibot.ingest.daily_store import DailyStore

    try:
        b = DailyStore(Path(root) / "daily").bars(market)
        last = str(b["date"].iloc[-1])[:10] if len(b) else None
    except Exception as e:
        return {"path": str(Path(root) / "daily"), "last_session": None, "error": type(e).__name__}
    return {"path": str(Path(root) / "daily"), "last_session": last}


# ---------------------------------------------------------------- the path


@dataclass
class Rule:
    strategy: object
    builder: PortfolioBuilder

    @property
    def name(self) -> str:
        return self.strategy.name


class _Gate:
    """The target loop's `reconciler`: the account's reconciliation, plus no buy while entries are blocked."""

    def __init__(self, account: Account) -> None:
        self.account = account

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        if side == "buy":
            why = self.account.entry_block()
            if why:
                return f"entries blocked: {why}"
        return self.account.allows(symbol, side, purpose, qty)


class LiveDaily:
    """One engine's daily path. `load(k, now)`: the DailyData of the evening of session k (`StoreLoader`), raising
    DataNotReady. `next_session(day)`: the session after `day` (the broker's calendar). `prev_session(day)`: the one
    before. Every step is safe to call on every tick: what is done is in the journal and is never done twice."""

    def __init__(self, broker: Broker, journal, now: Callable[[], datetime],
                 rules: Sequence[Rule] | Callable[[], Sequence[Rule]],
                 load: Callable[[date, datetime], DailyData], account: Account, *,
                 next_session: Callable[[date], date], prev_session: Callable[[date], date],
                 event: Callable[[str, dict], None] = lambda kind, body: None,
                 known_order_ids: Callable[[], set[str]] = set) -> None:
        """`rules`: the enabled rules with their builders, or a callable giving them now (the engine's: the run's
        level can change between sessions, and with it the book's caps)."""
        self._rules = rules if callable(rules) else (lambda r=list(rules): r)
        if not callable(rules):
            self.rules  # noqa: B018 - a rule whose pre-registration has a stop is refused at once
        self.broker, self.journal, self.now = broker, journal, now
        self.load, self.account, self.event = load, account, event
        self.next_session, self.prev_session = next_session, prev_session
        self.known_order_ids = known_order_ids
        self.policy = ExecutionPolicy("close")
        self.gate = _Gate(account)
        self.om: OrderManager | None = None
        self.stops: ProtectiveStops | None = None
        self._rid: int | None = None
        self._data: dict[date, DailyData] = {}  # the day's view, read once per decision session
        self._waiting: dict[str, str] = {}  # rule -> why the last decision could not be taken (event once)
        self._said: dict[str, str] = {}  # rule -> the last orders event (a retry that changes nothing is silent)
        self._missed: dict[str, tuple[str, str]] = {}  # rule -> (session, the heartbeat's plain note)
        self._synced: datetime | None = None

    # ---------------------------------------------------------------- state
    @property
    def rules(self) -> list[Rule]:
        rules = list(self._rules())
        for r in rules:
            if PREREG_STOP_ATR.get(r.name.split("~", 1)[0], "unknown") is not None:
                raise ValueError(f"{r.name}: its pre-registration's protective stop is not wired on the live daily "
                                 "path")
        return rules

    def _manager(self) -> OrderManager | None:
        rid = self.account.run_id()
        if rid is None:
            return self.om  # no run: the open orders and fills are still managed
        if self.om is None or self._rid != rid:
            self._rid = rid
            self.om = OrderManager(self.broker, self.journal, self.now, run_id=rid, on_fill=self._on_fill)
            self.stops = ProtectiveStops(self.om, run=rid)
        return self.om

    def _on_fill(self, f: Fill, r: OrderRecord) -> None:
        """The manager journalled the ledger's `fill` entry; fold the same body into the engine's ledger."""
        body = {"run_id": self.om.run_id, "coid": f.coid, "activity": f.activity, "strategy": f.strategy,
                "symbol": f.symbol, "side": f.side, "qty": f.qty, "px": f.px, "fees": f.fees, "ts": f.ts,
                "purpose": f.purpose, "session": r.session}
        self.account.book_fill({k: v for k, v in body.items() if v is not None})
        self.event("multi_day_fill", {"strategy": f.strategy, "symbol": f.symbol, "side": f.side, "qty": f.qty,
                                      "px": f.px, "coid": f.coid})

    def _entries(self, kind: str) -> list[dict]:
        return [b for _, _, _, b in self.journal.entries(kinds=(kind,))]

    def decision(self, strategy: str, session: date) -> dict | None:
        """The journalled decision taken after the close of `session` (None: not taken)."""
        out = None
        for b in self._entries("md_decision"):
            if b.get("strategy") == strategy and b.get("session") == str(session):
                out = b
        return out

    def _decision_for(self, strategy: str, fill: date) -> dict | None:
        out = None
        for b in self._entries("md_decision"):
            if b.get("strategy") == strategy and b.get("fill_session") == str(fill):
                out = b
        return out

    def _send_entry(self, strategy: str, session: date, kind: str = "md_send") -> dict | None:
        out = None
        for b in self._entries(kind):
            if b.get("strategy") == strategy and b.get("session") == str(session):
                out = b
        return out

    def working_symbols(self) -> set[str]:
        """Symbols with an open order of this path (not a protective stop): the engine treats them as known and
        leaves them to this path until they settle."""
        om = self.om
        return {r.symbol for r in om.working() if r.purpose != "stop"} if om is not None else set()

    # ---------------------------------------------------------------- steps
    def startup(self) -> None:
        """Before any decision: the manager recovers its orders from the venue (unconfirmed ones by client id), the
        fail-safe stops of the sleeves are adopted, the fills since the last start are booked."""
        om = self._manager()
        if om is None:
            return
        sleeves = {(r.name, s): q for r in self.rules for s, (q, _) in self.account.held(r.name).items()}
        self.stops.startup(sleeves, str(self.now().astimezone(NY).date()))
        self._prune()
        self._cancel_due()  # a halt or a full flatten journalled before the restart still holds today

    def sync(self, force: bool = False) -> None:
        """Book the venue's fills of this path's orders (each once, by activity id) and poll its open orders. Only
        while it has open orders; before the closing auction (15:59 ET) at most every POLL_EVERY (a `cls` order
        does not change until the auction), from then on at every call."""
        om = self._manager()
        if om is None or not (om.live or force):
            return
        now = self.now()
        # looked up at every call: an unconfirmed order, or any order that can fill before the closing auction (a
        # protective or fail-safe stop): its fill must be in the ledger before the next drift check
        urgent = any(r.state in UNCONFIRMED or r.tif != "cls" for r in om.live.values())
        if not (force or urgent) and now.astimezone(NY).time() < AUCTION and self._synced is not None \
                and now - self._synced < POLL_EVERY:
            return
        self._synced = now
        om.sync()
        self._prune()
        self._cancel_due()  # an order that was unconfirmed when the halt came is cancelled once it is confirmed

    # ---------------------------------------------------------------- halts and full flattens (QA-Ops, #207 review)
    def cancel_working(self, sides: Sequence[str], reason: str) -> list[str]:
        """The engine halts entries (`daily_stop`: sides ("buy",)) or flattens everything (run drawdown, new run,
        "Flatten and stop": ("buy", "sell")): this path's working closing-auction orders are cancelled, so a resting
        `cls` buy does not open a position after the halt, and a resting `cls` sale does not sell the shares the
        flatten sells at market again (that would be a short) or hold them so the flatten cannot sell them. The
        instruction is journalled (`md_cancel`, per run and New York day), so it survives a restart, and an order
        still unconfirmed now is cancelled at the next sync. Protective stops are not touched."""
        om = self._manager()
        if om is None:
            return []
        self.journal.append("md_cancel", {"run_id": om.run_id, "day": str(self.now().astimezone(NY).date()),
                                          "sides": sorted(set(sides)), "reason": reason})
        return self._cancel_due()

    def _cancel_sides(self) -> set[str]:
        om, day = self.om, str(self.now().astimezone(NY).date())
        out: set[str] = set()
        for b in self._entries("md_cancel"):
            if om is not None and b.get("run_id") == om.run_id and b.get("day") == day:
                out |= set(b.get("sides") or ())
        if self.account.run_id() is None:  # the run has ended: nothing new may be bought for it
            out.add("buy")
        return out

    def _cancel_due(self) -> list[str]:
        om = self.om
        if om is None or not om.live:
            return []
        sides = self._cancel_sides()
        out = []
        for r in om.working():
            if r.purpose == "stop" or r.side not in sides or r.state is St.PENDING_CANCEL:
                continue
            try:
                om.cancel(r.coid)
            except OrderBlocked:  # the venue has not confirmed it yet: the next sync cancels it
                continue
            out.append(r.coid)
        if out:
            self.event("multi_day_canceled", {"coids": out, "sides": sorted(sides)})
        return out

    def _prune(self) -> None:
        """The account's FILL activities include the intraday engine's own fills, which this manager does not know
        (`foreign`). The manager reads activities again from the oldest unattributed one, so they are dropped here:
        fills of orders the engine knows, and anything older than FOREIGN_MAX_AGE (a fail-safe stop's fill is
        adopted at start-up, long before)."""
        om = self.om
        if om is None:
            return
        known = self.known_order_ids()
        cut = self.now() - FOREIGN_MAX_AGE
        for aid, a in list(om.foreign.items()):
            ts = a.get("transaction_time")
            old = False
            if ts:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                old = t < cut
            if str(a.get("order_id")) in known or old:
                om.foreign.pop(aid, None)

    def after_close(self, closed: date) -> list[str]:
        """After the close of session `closed`: each rule that has not decided for it decides once the daily view of
        that evening is available, and the target list is journalled (`md_decision`). Returns the rules decided now."""
        rid = self.account.run_id()
        if rid is None or self.account.book_ended():
            return []
        done = []
        for r in self.rules:
            if self.decision(r.name, closed) is not None:
                continue
            now = self.now()
            fill = self.next_session(closed)
            day = getattr(r.strategy, "decision_day", None)
            if day is not None and not day(closed, fill):  # not its decision day: no target list, no data needed
                self.journal.append("md_decision", {"run_id": rid, "strategy": r.name, "session": str(closed),
                                                    "fill_session": str(fill), "decided_at": now.isoformat(),
                                                    "target": None, "note": "not a decision day"})
                self._waiting.pop(r.name, None)
                done.append(r.name)
                continue
            try:
                data = self._view(closed, now)
            except DataNotReady as e:
                if self._waiting.get(r.name) != str(e):
                    self._waiting[r.name] = str(e)
                    self.event("multi_day_waiting", {"strategy": r.name, "session": str(closed), "why": str(e)[:200]})
                continue
            self._waiting.pop(r.name, None)
            held = {s: q for s, (q, _) in self.account.held(r.name).items()}
            tl = decide_after_close(r.strategy, data, data.index_of(closed), fill, held)
            body = {"run_id": rid, "strategy": r.name, "session": str(closed), "fill_session": str(fill),
                    "decided_at": now.isoformat(), "held": held, "target": target_to_json(tl)}
            self.journal.append("md_decision", body)
            self.event("multi_day_decision", {"strategy": r.name, "session": str(closed), "fill_session": str(fill),
                                              "decides": tl is not None,
                                              "buy": list((tl.features.get("buy") or [])[:10]) if tl else [],
                                              "sell": dict(tl.features.get("sell") or {}) if tl else {}})
            done.append(r.name)
        return done

    def _view(self, k: date, now: datetime) -> DailyData:
        if k not in self._data:
            self._data = {k: self.load(k, now)}  # one evening's view at a time
        return self._data[k]

    def in_window(self, now: datetime) -> bool:
        t = now.astimezone(NY).time()
        return SEND_FROM <= t < SEND_UNTIL

    def send(self, session: date) -> list[str]:
        """Session `session`, inside the send window: each rule's targets, journalled before any order, sent once
        through the target loop. Returns the rules whose send step completed now."""
        om = self._manager()
        rid = self.account.run_id()
        if om is None or rid is None or not self.in_window(self.now()) or self.account.book_ended():
            return []
        done = []
        for r in self.rules:
            if self._send_entry(r.name, session, "md_sent") is not None:
                continue
            planned = self._send_entry(r.name, session)
            if planned is None:
                planned = self._plan(r, session, rid)
                if planned is None:
                    continue
                self.journal.append("md_send", planned)
            targets = [Target(r.name, s, int(q), p, {"why": w}) for s, q, p, w in planned["targets"]]
            if not targets:
                self.journal.append("md_sent", {"run_id": rid, "strategy": r.name, "session": str(session),
                                                "sent": [], "skipped": []})
                done.append(r.name)
                continue
            refused = {x.symbol for x in self.orders_of(session) if x.strategy == r.name and x.state is St.REJECTED
                       and not str(x.reason or "").startswith("lost")}  # the venue said no: not sent again today
            targets = [t for t in targets if t.symbol not in refused]  # (an order that never reached it is)
            held_now = {(r.name, s): q for s, (q, _) in self.account.held(r.name).items()}
            res = reconcile_targets(om, self.policy, targets, held_now, now=self.now(), session=str(session),
                                    run=rid, reconciler=self.gate, stops=self.stops, sells_first=False)
            sent = [{"coid": x.coid, "symbol": x.symbol, "side": x.side, "qty": x.qty, "state": str(x.state),
                     "reason": x.reason} for x in res.sent]
            skipped = [[k[1], why[:160]] for k, why in res.skipped]
            # complete unless something may still go out today: an order nobody has confirmed yet, a stop still
            # holding the shares, a cancel under way, or a target the loop skipped (entries blocked, an unresolved
            # reconciliation difference): the next tick runs the loop again on the same journalled targets
            retry = bool(res.deferred or res.canceled or skipped) or any(x.state in UNCONFIRMED for x in res.sent)
            said = repr((sent, skipped, res.deferred, res.canceled))
            if self._said.get(r.name) != said:
                self._said[r.name] = said
                self.event("multi_day_orders", {"strategy": r.name, "session": str(session), "sent": sent,
                                                "skipped": skipped, "deferred": [k[1] for k in res.deferred],
                                                "canceled": res.canceled, "retry": retry})
            if not retry:
                self.journal.append("md_sent", {"run_id": rid, "strategy": r.name, "session": str(session),
                                                "sent": [x["coid"] for x in sent], "skipped": skipped})
                done.append(r.name)
        return done

    def _pending(self, rule: str, session: date, held: Mapping[str, object]) -> dict[str, str]:
        """Sales sent on an earlier session that did not fill (no closing print): sent again, with their reason."""
        last = None
        for b in self._entries("md_send"):
            if b.get("strategy") == rule and b.get("session") < str(session):
                last = b
        if last is None:
            return {}
        return {s: str(w) for s, q, _, w in last["targets"] if int(q) == 0 and s in held}

    def _plan(self, r: Rule, session: date, rid: int) -> dict | None:
        """The builder's targets for `session` from the decision taken after the previous close."""
        prev = self.prev_session(session)
        dec = self._decision_for(r.name, session)
        held = self.account.held(r.name)
        pending = self._pending(r.name, session, held)
        tl = target_from_json(dec["target"]) if dec is not None else None
        if dec is None:
            self._no_decision(r.name, session, prev)
        if dec is None and not pending:
            return {"run_id": rid, "strategy": r.name, "session": str(session), "decided": None, "targets": [],
                    "note": "no decision"}
        if tl is None:
            plan, targets = plan_session(r.builder, None, r.name, {}, pending, None, 0, 0.0, 0.0)
            return self._planned(r, session, rid, prev, targets, None)
        try:
            data = self._view(prev, self.now())
        except DataNotReady as e:  # the evening's view went away (store moved): only the pending sales
            self.event("multi_day_waiting", {"strategy": r.name, "session": str(session), "why": str(e)[:200]})
            _, targets = plan_session(r.builder, None, r.name, {}, pending, None, 0, 0.0, 0.0)
            return self._planned(r, session, rid, prev, targets, None)
        live = with_session(data, session, self.account.splits(session))
        i = len(live.dates) - 1
        sized = {}
        for s, (q, mark) in held.items():  # held names are sized at the daily close (the research runner's mark)
            j = live._col.get(s)
            px = float(live.px[i - 1, j]) / live.split_at(i, j) if j is not None else math.nan
            sized[s] = (q, px if math.isfinite(px) else (mark if mark is not None else math.nan))
        unknown = [s for s in held if s not in live._col]
        if unknown:  # a held name outside the daily view (it left the store): only sold, never sized
            pending = {**pending, **{s: "not_in_daily_view" for s in unknown}}
        eq, cash = float(self.account.equity()), float(self.account.cash())
        plan, targets = plan_session(r.builder, tl, r.name, {s: v for s, v in sized.items() if s not in unknown},
                                     {s: w for s, w in pending.items() if s not in unknown}, live, i, eq, cash)
        targets += [Target(r.name, s, 0, "sell", {"why": "not_in_daily_view"}) for s in unknown]
        if r.builder.limits.max_overnight_pct <= 0:  # allow_overnight false: nothing may be held over the close
            cut = [t for t in targets if t.shares > float(held.get(t.symbol, (0.0, None))[0])]
            if cut:
                self.event("multi_day_overnight_off", {"strategy": r.name, "session": str(session),
                                                       "not_bought": [t.symbol for t in cut][:30]})
            targets = [t for t in targets if t not in cut]
        return self._planned(r, session, rid, prev, targets, plan, eq=eq, cash=cash)

    def _no_decision(self, rule: str, session: date, prev: date) -> None:
        """No decision for this session: say why, plainly (heartbeat `multi_day.note`, event)."""
        why = self._waiting.get(rule)
        avail = getattr(self.load, "available", None)
        if why is None and avail is not None:
            try:
                why = avail(prev, self.now())
            except Exception as e:  # the note must never stop the send step
                why = f"{type(e).__name__}"
        first = not any(b.get("strategy") == rule for b in self._entries("md_decision"))
        note = ("no decision today: daily store not refreshed (see logs/fetch_daily.log)" if why else
                "no decision yet: the first one is taken after a close" if first else
                "no decision today: none was taken after the last close (engine off then?)")
        self._missed[rule] = (str(session), note + (f" ({why})" if why else ""))
        self.event("multi_day_no_decision", {"strategy": rule, "session": str(session), "after_close_of": str(prev),
                                             "note": self._missed[rule][1][:240]})

    def close_book(self) -> list[str]:
        """The book's run end (engine/core.Engine._book_end): cancel every working order of this path (its
        closing-auction buys and sales; the engine then sells the sleeves at market). Returns the cancelled ids."""
        om = self.om
        out = []
        for x in (om.working() if om is not None else []):
            if x.purpose != "stop" and x.order_id is not None:
                om.cancel(x.coid)
                out.append(x.coid)
        return out

    def status(self, today: date | None = None) -> dict:
        """The heartbeat's `multi_day` block: the book, each rule's last decision, and a plain note when today has no
        decision (for example "no decision today: daily store not refreshed")."""
        ended = self.account.book_ended()
        rules: dict[str, dict] = {}
        notes = []
        for r in self.rules:
            last = None
            for b in self._entries("md_decision"):
                if b.get("strategy") == r.name:
                    last = b.get("session")
            row: dict = {"last_decision": last}
            miss = self._missed.get(r.name)
            if miss is not None and (today is None or miss[0] == str(today)):
                row["note"] = miss[1]
                notes.append(f"{r.name}: {miss[1]}")
            if r.name in self._waiting:  # the next decision cannot be taken yet
                row["waiting"] = self._waiting[r.name][:200]
            rules[r.name] = row
        out: dict = {"book": "ended" if ended else "open", "rules": rules}
        if ended:
            notes.insert(0, "multi-day book closed: its run-drawdown end was reached (no new multi-day trades)")
        if notes:
            out["note"] = "; ".join(notes)[:300]
        return out

    @staticmethod
    def _planned(r: Rule, session: date, rid: int, prev: date, targets: list[Target], plan, *, eq=None,
                 cash=None) -> dict:
        body = {"run_id": rid, "strategy": r.name, "session": str(session), "decided": str(prev),
                "targets": [[t.symbol, int(t.shares), t.purpose, t.meta.get("why")] for t in targets]}
        if plan is not None:
            body.update(equity=eq, cash=cash, skipped=plan.skipped, guarded=dict(plan.guarded),
                        basis_gaps=list(plan.basis_gaps))
        return body

    def orders_of(self, session: date) -> list[OrderRecord]:
        om = self.om
        return [x for x in om.orders.values() if x.session == str(session) and x.purpose != "stop"] if om else []


__all__ = ["LiveDaily", "Rule", "StoreLoader", "DataNotReady", "live_limits", "live_builder", "with_session",
           "as_of", "target_to_json", "target_from_json", "SEND_FROM", "SEND_UNTIL", "PREREG_STOP_ATR"]


# ---------------------------------------------------------------- the engine's side


class EngineAccount:
    """`Account` over the live engine (engine/core.Engine): its run, its ledger (folded from the same journal the
    order manager writes), its reconciliation and its entry block. Duck-typed: this module never imports core."""

    def __init__(self, engine) -> None:
        self.e = engine

    def _book(self):
        rid = self.run_id()
        return self.e.ledger.book(rid) if rid is not None else None

    def run_id(self) -> int | None:
        return self.e._rid()

    def equity(self) -> float:
        b = self._book()
        if b is None:
            return 0.0
        return float(b.closes[-1].close_equity_venue) if b.closes else float(b.amount)

    def cash(self) -> float:
        b = self._book()
        return float(b.cash) if b is not None else 0.0

    def held(self, strategy: str) -> dict[str, tuple[float, float | None]]:
        b = self._book()
        if b is None:
            return {}
        return {sym: (float(s.qty), s.mark) for (st, sym), s in b.sleeves.items() if st == strategy and s.qty > 1e-9}

    def book_fill(self, body: dict) -> None:
        self.e._md_realized(body)  # today's realised P&L of a sale (before the fold changes the sleeve)
        self.e.ledger.apply("fill", body)

    def entry_block(self) -> str | None:
        return self.e.entry_block()

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        return self.e.recon.allows(symbol, side, purpose, qty)

    def book_ended(self) -> bool:
        rid = self.run_id()
        return rid is not None and any(b.get("run_id") == rid for _, _, _, b in
                                       self.e.journal.entries(kinds=("md_book_end",)))

    def splits(self, day: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for _, _, _, b in self.e.journal.entries(kinds=("split",)):
            if str(b.get("ex_date"))[:10] == str(day) and b.get("run_id") == self.run_id() and b.get("ratio"):
                out[str(b["symbol"])] = float(b["ratio"])
        return out


def sessions_from_calendar(calendar: Callable[[str], dict | None]) -> tuple[Callable[[date], date],
                                                                           Callable[[date], date]]:
    """next_session / prev_session from the broker's calendar (Alpaca: GET /v2/calendar), cached."""
    cache: dict[str, bool] = {}

    def trades(d: date) -> bool:
        k = str(d)
        if k not in cache:
            cache[k] = calendar(k) is not None
        return cache[k]

    def step(d: date, sign: int) -> date:
        for n in range(1, 11):
            x = d + timedelta(days=sign * n)
            if trades(x):
                return x
        raise RuntimeError(f"no session within ten days of {d}")

    return (lambda d: step(d, 1)), (lambda d: step(d, -1))


def for_engine(engine, load: Callable[[date, datetime], DailyData],
               rules: Sequence[Rule] | Callable[[], Sequence[Rule]] | None = None) -> LiveDaily:
    """The engine's daily path: its broker, its journal, its clock, its ledger and reconciliation. `rules` default:
    the multi-day rules the session runs (`Engine.live_strategies`, which loads them only with KAIBOT_MULTIDAY=on)
    at the run's level and its policy."""
    nxt, prev = sessions_from_calendar(engine.broker.calendar)
    cache: dict = {}

    def current() -> list[Rule]:
        from kaibot.strategies.base import Holding

        names = tuple(s.name for s in engine.live_strategies() if s.holding is Holding.MULTI_DAY)
        key = (names, engine.level())
        if key not in cache:
            cache.clear()
            cache[key] = rules_for(names, engine.policy(), engine.level())
        return cache[key]

    def known_ids() -> set[str]:
        ids = {str(x) for p in engine.pos.values() for x in (p.order_id, p.stop_order_id, p.target_order_id,
                                                             p.exit_order_id) if x}
        return ids | set(engine._md_exits)

    return LiveDaily(engine.broker, engine.journal, engine.now, rules if rules is not None else current, load,
                     EngineAccount(engine), next_session=nxt, prev_session=prev,
                     event=lambda kind, body: engine.store.event(kind, body, engine._rid()),
                     known_order_ids=known_ids)


def rules_for(names: Sequence[str], pol: RiskPolicy, level: str) -> list[Rule]:
    """The enabled multi-day rules at the run's level, each with its live builder (#205's book: risk/profiles)."""
    from kaibot.risk.profiles import rule_book_limits
    from kaibot.strategies import registry

    out = []
    for n in registry.multi_day_names(names):
        s = registry.get(n)
        out.append(Rule(s, live_builder(s, live_limits(s, pol, rule_book_limits(n, level)))))
    return out
