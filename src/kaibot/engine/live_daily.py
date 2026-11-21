"""The live daily path (issue #206; decision 15 sections 3, 5 and 11): multi-day rules in the live engine on Alpaca
paper, through the SAME decision core as the research runner (engine/daily.py). Only with KAIBOT_MULTIDAY=on (the
engine builds this object only then; with the flag off nothing here is imported and the engine is unchanged).

Nothing here is a second implementation of the rule or of its sizing. What it reuses:

    the daily view           engine/daily.DailyData / DailyView (point-in-time daily bars, the research loader
                             engine/daily_report.load_store on the daily store, rows <= the decision's close only)
    the decision             engine/daily.decide_after_close -> strategies/momentum (the frozen rule)
    the sizing               engine/daily.plan_session -> risk/portfolio.PortfolioBuilder (ranked fill, 3% cash
                             reserve, 40% sector cap at most), at the live book's limits (`live_limits`)
    the orders               engine/targets.reconcile_targets -> engine/execution.ExecutionPolicy("near_close")
                             (DAY market orders, whole shares, 15:50-15:57 ET; issue #333) ->
                             engine/orders.OrderManager (deterministic client ids, intent journalled before the
                             call, unknown orders looked up by client id)
    the fills                OrderManager(run_id=...): every FILL activity of its orders becomes the ledger's own
                             `fill` entry in one journal append; the engine folds it into its ledger (`book_fill`)
    reconciliation, stops    the engine's Reconciler (engine/reconcile.py) and ProtectiveStops (engine/protect.py:
                             fail-safe stops of the watchdog / dead-man are adopted and released before a sale)

The steps, per session (times ET):

    after the close of k     `after_close`: once the daily store holds session k (its bars carry `available_ts`,
                             20:15 ET; nothing is read before it) each enabled rule decides for the next session
                             and the target list is journalled (`md_decision`), once per (rule, session)
    session i, 15:50-15:57   `send`: the target list decided after k (i's previous session) is sized by the
                             portfolio builder against the ledger (equity at the last close, cash) and the targets
                             are journalled (`md_send`) BEFORE any order; the target loop sends the difference as
                             near-close DAY market orders; `md_sent` closes the step. Sales go out even when entries
                             are blocked; buys need `entry_block() is None` and a clean reconciliation. On an early
                             close the window is the close minus 10 to the close minus 3 minutes
    every tick               `sync`: the order manager books the venue's fills; a remainder the venue let expire at
                             the close is journalled once (`md_unfilled`) and reported (`multi_day_unfilled`)

Why not the closing auction (issue #333): Alpaca paper does not run one. It works a `cls` order as a market order in
the last seconds before 16:00 and lets the rest expire ("Paper trading treats these simply like market orders placed
at the close"); on 18 Nov 2026 23 of 30 `cls` buys got nothing. A DAY market order from 15:50 gives paper minutes to
fill, and a partial fill's remainder is worked again while the order is marketable. The research venue fills it at
the closing print at cost model v1, as it did the `cls` order (SimBroker `near_close`), so the fills of the ten-year
runs do not change (Amendment 10 of trend_momentum, Amendment 8 of mom_12_1_band). A remainder that still expires is
not sent again that session; a sale is sent again at the next session, and so is a buy (below).

Carry-over (issue #363; Amendments 11 to 13 of trend_momentum, 9 to 11 of mom_12_1_band). On a session that is not a
decision's fill session, `send` plans again what the latest decision's buys still lack: the plan's own target shares
(`md_send` of the fill session, of this run) less the shares held, through the same target loop, so never above the
target. Left out, each with its reason in `md_send.carry.skipped` and the `multi_day_carry` event: while the market
filter is off, a stock outside the rule's ranked universe or that it could not buy now, one without a bar on the fill
day, one whose last order the venue cancelled or refused, one that lost shares since the decision (by any path), one
with a fill not booked yet, a split, an announced corporate action, the overnight cap, and whatever does not fit the
cash less the reserve or the book's caps at the previous close (`_carry_room`: the paper account lends, so the engine
checks). A failure in the carry-over carries nothing and never holds back the session's sales. No decision record for
the session, no send of the decision, or no daily view of the previous close: nothing is carried that session.

Pre-send gate (M9 ticket 1.16, issue #441; Amendment 14 of trend_momentum, 12 of mom_12_1_band). With a quote source
(`quotes`: the engine's IEX latest quotes; None on the research path and in replays: no gate, nothing read), each
order the target loop is about to send is checked by engine/execution.PreSendGate against the stock's latest quote
and its sizing close (the previous session's raw close, on today's share basis): a quote that is not fresh (halted or
not trading), a move outside the collar, or a buy above the book's per-name cap is WITHHELD: no order, one
`md_withheld` record per (rule, session, stock, side), and the reason in the `multi_day_orders` event. The loop asks
again on every tick of the window. What is still withheld when the window ends is carried to the next session: a
buy by the carry-over above (it has no order, so `ended_buys` has nothing against it), a sale to zero as a pending
sale (`_pending`), a withheld reduction as the same reduction (`_withheld_trims`). Without a quote (the read failed
or timed out, or the feed has none for the stock) the order is checked at the prior close and goes out.

Pre-open review (#131, OFF by default: `review` is None unless KAIBOT_ROOM=on and KAIBOT_PREOPEN_REVIEW=on, and then
every step above is unchanged line for line). When on: from 08:30 ET `preopen` hands each rule's plan to the
review (agents/preopen_daily.PreopenDesk, a worker thread); its outcome is journalled (`md_review`) before the send;
`send` applies it to the plan (veto drops a buy, downgrade halves the increase, nothing else) before `md_send`.

Shadow pre-open review (#310, OFF by default: `shadow` is None unless KAIBOT_PREOPEN=shadow). An observer only: after
`md_send` is journalled, `send` hands a deep copy of it and of the holdings to agents/preopen_shadow.ShadowDesk (a
worker thread) and goes on; `sync` journals a finished record (`md_review_shadow`) and its event; `status` shows it.
Nothing on this path reads a shadow record back, and no step waits for it: the targets, the orders and the fills are
the same with it on or off (tests/unit/test_preopen_shadow_sim.py). Its errors only log.

Daily note (#311, OFF by default: `note` is None unless KAIBOT_DAILY_NOTE=on). After 16:10 ET `sync` hands the
day's records (plan, orders, fills, holdings, book) to agents/daily_note.NoteDesk, which writes one plain-words
paragraph in a worker thread; `sync` journals it (`daily_note`) with its event, `status` shows the latest. Nothing on
this path reads it back.

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
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from kaibot.broker.protocol import Broker
from kaibot.engine.daily import (
    CARRY,
    CASH_RESERVE,
    DailyData,
    carry_targets,
    decide_after_close,
    ended_buys,
    momentum_limits,
    plan_session,
)
from kaibot.engine.execution import (
    NEAR_CLOSE_MIN,
    NY,
    REGULAR_CLOSE,
    ExecutionPolicy,
    PreSendGate,
    minus,
    quote_from_alpaca,
)
from kaibot.engine.orders import UNCONFIRMED, Fill, OrderBlocked, OrderManager, OrderRecord, St
from kaibot.engine.protect import ProtectiveStops
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.risk.gate import BOOK_FLOORS, BOOK_HARD_CEILINGS, BookLimits, RiskPolicy, validate_book
from kaibot.risk.portfolio import PortfolioBuilder
from kaibot.strategies.base import TargetList
from kaibot.strategies.momentum import RESIZE_BUFFER

log = logging.getLogger("kaibot.engine.daily")

# Issue #333: near-close DAY market orders, from the close minus 10 minutes; the last three minutes are left for
# the venue to fill them (and to work a partial fill's remainder) before the close.
SEND_FROM, SEND_UNTIL = minus(REGULAR_CLOSE, NEAR_CLOSE_MIN), minus(REGULAR_CLOSE, 3)  # 15:50 / 15:57 ET
SEND_LAST_MIN = 3
DAILY_KINDS = ("md_decision", "md_send", "md_sent", "md_cancel", "md_review", "md_unfilled")
PREOPEN_FROM, PREOPEN_UNTIL = time(8, 30), time(9, 31)  # #131: the pre-open review runs before the session
REVIEW_WAIT_UNTIL_S = 300.0  # the send stops waiting for a running review 5 minutes before the window ends
# Product call on #207 (13 Nov 2026): a rule whose book reached its run end stays RETIRED across runs and restarts,
# until the stakeholder re-enables it explicitly: KAIBOT_REENABLE=<rule>:<retirement date>, read once at start-up.
RETIRE_KINDS = ("md_retired", "md_reenabled")
REENABLE_FLAG = "KAIBOT_REENABLE"
FOREIGN_MAX_AGE = timedelta(days=3)  # see LiveDaily._prune
AUCTION, POLL_EVERY = time(15, 59), timedelta(minutes=15)  # see LiveDaily.sync
# The protective stop each rule's pre-registration declares, in ATR(20) below the entry (None: the rule has none).
PREREG_STOP_ATR: dict[str, float | None] = {"trend_momentum": None, "mom_12_1_band": None,
                                            "mom_12_1_band_nofilter": None}


def exit_kind(rule: str) -> str:
    """How a rule's positions leave (Product, 18 Nov 2026, for the dashboard's stop alarm): "target_list" when its
    pre-registration has no protective stop (the position is sold when it drops out of the rule's target list, so a
    missing broker stop is expected), "broker_stop" when it has one (a missing stop is a real alarm). An unknown rule
    is "broker_stop" (fail closed: its sleeve is shown as wanting a stop)."""
    base = rule.split("~", 1)[0]
    return "target_list" if base in PREREG_STOP_ATR and PREREG_STOP_ATR[base] is None else "broker_stop"


class DataNotReady(RuntimeError):
    """The daily store does not hold the decision's session yet (or not as available at this time)."""


class StoreFailedCheck(DataNotReady):
    """The daily store holds the session, but a bar inside the decision's look-back fails a hard integrity check
    (kaibot.ingest.daily_integrity): no decision on those prices. The message is the check's plain reason."""


class Account(Protocol):
    """What the daily path needs from the engine (or, in the parity test, from a SimBroker account and a ledger)."""

    def run_id(self) -> int | None: ...

    def equity(self) -> float: ...  # the run's venue equity at the last close (what the builder sizes against)

    def cash(self) -> float: ...  # the ledger's cash now

    def held(self, strategy: str) -> dict[str, tuple[float, float | None]]: ...  # symbol -> (shares, last mark)

    def book_fill(self, body: dict) -> None: ...  # fold a `fill` entry the order manager journalled

    # optional (follow-up of #333): `before_send(strategy, session, planned)` with planned = {symbol: target shares}
    # of the buys about to go out; the engine writes today's ledger_position rows for the fail-safes with them

    def entry_block(self) -> str | None: ...  # why no position may be opened now (None: it may)

    def allows(self, symbol: str, side: str, purpose: str, qty: float | None) -> str | None: ...  # reconciliation

    def splits(self, day: date) -> dict[str, float]: ...  # splits the ledger booked for this session: symbol -> ratio

    def book_ended(self) -> bool: ...  # the multi-day book reached its own run end this run (no more trading)

    def adopted(self, strategy: str) -> dict[str, str]: ...  # #266: held shares a corporate action credited -> why


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
        sig_unfilled_c=add(data.sig_unfilled_c), sector=dict(data.sector), dropped=dict(data.dropped))


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
    decision, no order). Before the view is read, the bars of the keys it loads, over its look-back, pass the live
    integrity gate (kaibot.ingest.daily_integrity.StoreGate, #230): a structural error, a bad bar in SPY or a sector
    ETF, more than 1% of the stocks failing, or an unreadable file: StoreFailedCheck (no decision for any rule).
    Otherwise a stock with a bad bar is dropped from the evening's view (never bought; `data.dropped`, key ->
    reason), and a rule that holds a dropped stock does not decide (`LiveDaily.after_close`). Live, a held company
    whose bars stop is the venue's business (Alpaca books a delisting), so the research runner's terminal closes
    are dropped. docs/ops/daily_bars.md says what to do when the gate trips."""

    LOOKBACK_DAYS = 600  # ~410 sessions: the longest signal window (SPY's 200-day average) plus ATR and ADV

    def __init__(self, root: Path, universe_file: Path, mode: str = "pit", market: str = "SPY") -> None:
        self.root, self.universe_file, self.mode, self.market = Path(root), Path(universe_file), mode, market
        self._gate = None  # kaibot.ingest.daily_integrity.StoreGate, made at the first decision

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

        start = k - timedelta(days=self.LOOKBACK_DAYS)
        if self._gate is None:
            from kaibot.ingest.daily_integrity import StoreGate

            self._gate = StoreGate(self.root / "daily", self.market)
        dropped: dict[str, str] = {}

        def screen(symbols, stocks):
            v = self._gate(start, k, symbols, stocks)
            if v.block:
                raise StoreFailedCheck(v.block)
            dropped.update(v.dropped)
            return sorted(v.dropped)

        try:
            m, _ = dr.load_store(self.root, self.universe_file, self.mode, str(start), str(k), screen=screen)
        except (SystemExit, ValueError, FileNotFoundError) as e:
            raise DataNotReady(f"daily store unreadable: {e}") from None
        data = DailyData.from_market(m)
        if data.dates[-1].astype(object) != k:
            raise DataNotReady(f"the daily view ends {data.dates[-1]}, not {k}")
        why = self.incomplete(data)
        if why:
            raise DataNotReady(why)
        data.terminal = {}
        data.dropped = dropped
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


def retired_note(b: dict) -> str:
    """The heartbeat's line for a retired rule, e.g. "trend_momentum retired: book fell 21.3% from its peak on
    2026-11-20"."""
    dd = b.get("drawdown_pct")
    fell = f"book fell {float(dd):.1f}% from its peak" if dd is not None else "book reached its drawdown end"
    return f"{b.get('rule')} retired: {fell} on {b.get('session')}"


def reenable_value(b: dict) -> str:
    """The one value that re-enables this retirement (#211: single-use, by its id)."""
    return f"{REENABLE_FLAG}={b.get('rule')}:{b.get('retirement_id') or b.get('session')}"


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


ACTIVE = frozenset({"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding", "pending_replace"})


BLOCKING = frozenset({"market", "stop", "limit", "stop_limit"})  # order types a new market order cannot pass


def opposite_orders(open_orders: Sequence[dict], mine: set[str]) -> dict[str, set[str]]:
    """symbol -> the sides of open market, stop or limit orders (and armed legs) that are not this path's: Alpaca
    refuses a new order of the other side in that symbol ("potential wash trade detected"; QA-Ops review of #207).
    Every order this path sends is a market (`cls`) order, which an opposite limit order blocks too (#211)."""
    out: dict[str, set[str]] = {}
    for o in open_orders:
        for x in [o, *(o.get("legs") or [])]:
            if str(x.get("id")) in mine or x.get("status") not in ACTIVE or x.get("type") not in BLOCKING:
                continue
            out.setdefault(str(x.get("symbol") or o.get("symbol")), set()).add(str(x.get("side") or o.get("side")))
    return out


class _Gate:
    """The target loop's `reconciler`: the account's reconciliation, no buy while entries are blocked, and no order
    against an open opposite-side market or stop order in the symbol (Alpaca would refuse it)."""

    def __init__(self, account: Account) -> None:
        self.account = account
        self.opposite: dict[str, set[str]] = {}
        self.shorting: bool | None = False  # set by LiveDaily from the account before each send

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        other = {"buy": "sell", "sell": "buy"}[side]
        if other in self.opposite.get(symbol, ()):
            return f"an open {other} market or stop order in {symbol} (Alpaca refuses a wash trade): waiting"
        if side == "buy" and self.shorting is not False:
            return "shorting is on at the paper account (or unread): run ./scripts/alpaca_no_shorting.sh"
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
                 known_order_ids: Callable[[], set[str]] = set,
                 session_close: Callable[[date], time | None] = lambda d: None,
                 store=None, reenable: str | None = None, ca=None, review=None,
                 shadow=None, note=None, filing_flags=None,
                 quotes: Callable[[Sequence[str]], Mapping[str, dict]] | None = None) -> None:
        """`rules`: the enabled rules with their builders, or a callable giving them now (the engine's: the run's
        level can change between sessions, and with it the book's caps). `ca`: the live-safety exclusion of
        announced corporate actions (engine/corp_guard.CorporateActionGuard; #266, Amendment 6); None: none."""
        self._rules = rules if callable(rules) else (lambda r=list(rules): r)
        if not callable(rules):
            self.rules  # noqa: B018 - a rule whose pre-registration has a stop is refused at once
        self.broker, self.journal, self.now = broker, journal, now
        self.load, self.account, self.event = load, account, event
        self.next_session, self.prev_session = next_session, prev_session
        self.known_order_ids = known_order_ids
        self.session_close = session_close  # the session's close (ET wall time), for early-close days
        self._closes: dict[date, time | None] = {}  # the session's close, read once (send_until)
        self.store = store  # the Supabase replica: a second, durable record of a retirement (halt rows)
        self.reenable = reenable  # KAIBOT_REENABLE as read from the environment at start-up (used once)
        self._store_cache: dict[str, dict | None] = {}
        self.shorting: bool | None = None  # the paper account's shorting_enabled, read at start-up and each session
        self._shorting_day: date | None = None
        self._unfilled: set[str] | None = None  # client ids already reported as expired remainders (md_unfilled)
        self._presend: set[tuple[str, str]] = set()  # (rule, session) whose planned buys the fail-safes were given
        self.gate = _Gate(account)
        self.om: OrderManager | None = None
        self.stops: ProtectiveStops | None = None
        self._rid: int | None = None
        self._data: dict[date, DailyData] = {}  # the day's view, read once per decision session
        self._waiting: dict[str, str] = {}  # rule -> why the last decision could not be taken (event once)
        self._failed_check: set[str] = set()  # rules whose last wait was a store failing the integrity check
        self._dropped: dict[str, tuple[str, str]] = {}  # rule -> (fill session, note): stocks the gate left out
        self._said: dict[str, str] = {}  # rule -> the last orders event (a retry that changes nothing is silent)
        self._missed: dict[str, tuple[str, str]] = {}  # rule -> (session, the heartbeat's plain note)
        self._synced: datetime | None = None
        self.ca = ca
        self._ca_unread: dict[str, tuple[str, str]] = {}  # rule -> (session, why the read failed): no buys then
        self._ca_evening: date | None = None  # the decision evening whose held names the heartbeat notes cover
        self._preopen_day: date | None = None
        self._preopen_ok = False
        self.review = review  # #131: agents/preopen_daily.PreopenDesk, or None (off: nothing below changes)
        self.shadow = shadow  # #310: agents/preopen_shadow.ShadowDesk, or None. An observer: never read back
        self.note = note  # #311: agents/daily_note.NoteDesk, or None. Writes the day's plain-words report; never read
        # #375: (symbols, as_of, session) -> a `filing_flag_shadow` event body, or None (KAIBOT_FILING_FLAG=shadow).
        # A logged observer: nothing on this path reads its answer, so targets and orders are the same on or off
        self.filing_flags = filing_flags
        # M9 1.16 (#441): symbols -> Alpaca latest-quote rows, for the pre-send gate. None: no gate (research, replays)
        self.quotes = quotes
        self.presend_gate = PreSendGate()
        self._withheld: set[tuple] | None = None  # (rule, session, symbol, side) already journalled (md_withheld)

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
    # ---------------------------------------------------------------- retirement (Product call on #207)
    def _store_state(self, rule: str, fresh: bool = False) -> dict | None:
        """The latest retirement or re-enabling of `rule` in the store's halt rows (kinds multi_day_retired /
        multi_day_reenabled, highest id last), or None. A second record: the journal is the engine's state, but a
        new journal (a new machine, a lost file) must not bring a retired rule back. Read at start-up (and after
        this engine writes one), never per tick."""
        if self.store is None:
            return None
        if not fresh and rule in self._store_cache:
            return self._store_cache[rule]
        try:
            rows = [r for k in ("multi_day_retired", "multi_day_reenabled")
                    for r in self.store.select("halt", {"kind": f"eq.{k}"}) or []]
        except Exception:
            log.exception("halt rows unreadable (retirement check); the journal decides")
            return None
        rows = [r for r in rows if f"rule={rule};" in str(r.get("reason") or "")]
        self._store_cache[rule] = max(rows, key=lambda r: int(r.get("id") or 0)) if rows else None
        return self._store_cache[rule]

    def retirement(self, rule: str) -> dict | None:
        """The rule's retirement in force, or None. Retired when the journal's latest record says so, or the store's
        does (fail closed: either record keeps it retired)."""
        last = None
        for _, _, kind, b in self.journal.entries(kinds=RETIRE_KINDS):
            if b.get("rule") == rule:
                last = (kind, b)
        if last is not None and last[0] == "md_retired":
            return last[1]
        st = self._store_state(rule)
        if st is not None and st.get("kind") == "multi_day_retired":
            if last is not None and int(last[1].get("store_id") or -1) > int(st.get("id") or 0):
                return None  # re-enabled after that row (journalled with the id of its own re-enabling row)
            reason = str(st.get("reason") or "")
            rid = next((x.strip()[3:] for x in reason.split(";") if x.strip().startswith("id=")), None)
            return {"rule": rule, "session": st.get("session"), "drawdown_pct": None, "source": "store",
                    "reason": reason, "retirement_id": rid or st.get("session")}
        return None

    def retire(self, rule: str, *, run_id, session: str | None, drawdown_pct: float, end_pct: float, peak: float,
               equity: float) -> dict:
        """Record the retirement durably: the journal (`md_retired`) and a `multi_day_retired` halt row."""
        # #211: each retirement has its own id (the session and a random part), the only key a re-enable accepts, so
        # a re-enable is single-use: a later retirement, even on the same date, needs its own
        ident = f"{session}.{secrets.token_hex(3)}"
        body = {"rule": rule, "run_id": run_id, "session": session, "retirement_id": ident,
                "drawdown_pct": round(drawdown_pct, 3), "end_pct": end_pct, "peak": round(peak, 2),
                "equity": round(equity, 2), "ts": self.now().isoformat()}
        self.journal.append("md_retired", body)
        if self.store is not None:
            try:
                self.store.insert("halt", {"run_id": run_id, "kind": "multi_day_retired", "session": session,
                                           "reason": f"rule={rule}; id={ident}; {retired_note(body)}"},
                                  returning=False)
                self._store_state(rule, fresh=True)
            except Exception:
                log.exception("retirement halt row not written (the journal holds it)")
        self.event("multi_day_retired", {**body, "note": retired_note(body), "reenable": reenable_value(body)})
        return body

    def _apply_reenable(self) -> None:
        """KAIBOT_REENABLE=<rule>:<retirement id>, read once at start-up: re-enables exactly that retirement
        (journalled `md_reenabled`, a `multi_day_reenabled` halt row, an event). The id is the retirement's own
        (`retirement_id`, shown on the heartbeat): anything else is refused with an event and changes nothing, so a
        value left in the .env can never re-enable a later retirement, not even one on the same date (#211)."""
        raw, self.reenable = (self.reenable or "").strip(), None
        if not raw:
            return
        rule, _, token = raw.partition(":")
        cur = self.retirement(rule)
        ident = None if cur is None else str(cur.get("retirement_id") or cur.get("session"))
        if cur is None or not token or token != ident:
            why = ("not retired" if cur is None else
                   f"the value must name this retirement: {reenable_value(cur)}")
            log.warning("%s=%s ignored: %s", REENABLE_FLAG, raw, why)
            self.event("multi_day_reenable_refused", {"value": raw[:80], "why": why})
            return
        store_id = None
        if self.store is not None:
            try:
                self.store.insert("halt", {"run_id": self.account.run_id(), "kind": "multi_day_reenabled",
                                           "session": str(self.now().astimezone(NY).date()),
                                           "reason": f"rule={rule}; re-enabled by the stakeholder ({REENABLE_FLAG})"},
                                  returning=False)
                st = self._store_state(rule, fresh=True)
                store_id = st.get("id") if st is not None else None
            except Exception:
                log.exception("re-enabling halt row not written")
        body = {"rule": rule, "retired_session": cur.get("session"), "retirement_id": ident, "by": REENABLE_FLAG,
                "value": raw[:80], "store_id": store_id,
                "ts": self.now().isoformat()}
        self.journal.append("md_reenabled", body)
        log.warning("%s re-enabled by %s=%s", rule, REENABLE_FLAG, raw)
        self.event("multi_day_reenabled", body)

    def active(self) -> list[Rule]:
        """The enabled rules that are not retired."""
        return [r for r in self.rules if self.retirement(r.name) is None]

    def startup(self) -> None:
        """Before any decision: the manager recovers its orders from the venue (unconfirmed ones by client id), the
        fail-safe stops of the sleeves are adopted, the fills since the last start are booked. Retirements are read
        from the store and KAIBOT_REENABLE is applied (once), with or without a run."""
        for r in self.rules:
            self._store_state(r.name, fresh=True)
        self._apply_reenable()
        om = self._manager()
        if om is None:
            return
        sleeves = {(r.name, s): q for r in self.rules for s, (q, _) in self.account.held(r.name).items()}
        self.stops.startup(sleeves, str(self.now().astimezone(NY).date()))
        self._read_shorting()
        self._report_unfilled(om)  # #333: an expiry the last run saw but did not report (or from before #333)
        self._prune()
        self._cancel_due()  # a halt or a full flatten journalled before the restart still holds today

    def sync(self, force: bool = False) -> None:
        """Book the venue's fills of this path's orders (each once, by activity id) and poll its open orders. Only
        while it has open orders; before the closing auction (15:59 ET) at most every POLL_EVERY (a `cls` order
        does not change until the auction), from then on at every call."""
        self._shadow_collect()  # #310: an observer's finished records (never read back by this path)
        self._sync(force)
        # #311: the day's plain-words report, from 16:10 ET, after this tick's fills are booked (also after the early
        # returns). Not after a failed sync: its facts could miss fills, and a note is written once (never read back
        # by this path; never raises). A sync that keeps failing leaves the note to the engine's stop (note_final).
        self._note_step()

    def _sync(self, force: bool) -> None:
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
        self._report_unfilled(om)
        self._prune()
        self._cancel_due()  # an order that was unconfirmed when the halt came is cancelled once it is confirmed

    def _before_send(self, rule: str, session: date, targets: Sequence[Target],
                     held: Mapping[tuple[str, str], float]) -> None:
        """Follow-up of #333: just before the first orders of (rule, session) go out, the account (the engine) is
        given each planned buy at its target shares, so today's ledger_position rows carry them before the venue
        can fill anything: a fill that lands after the engine died is then multi-day shares to the dead-man, not
        intraday shares it sells. Once per (rule, session) per process (a restart writes them again: idempotent).
        A failure is an event, and the orders still go out (the fail-safes then sell such a fill: a round trip,
        never an unprotected position)."""
        planned = {t.symbol: int(t.shares) for t in targets
                   if t.shares > float(held.get((rule, t.symbol), 0.0)) + 1e-9}
        hook = getattr(self.account, "before_send", None)
        if not planned or hook is None or (rule, str(session)) in self._presend:
            return
        try:
            hook(rule, session, planned)
            self._presend.add((rule, str(session)))
        except Exception as e:
            log.exception("planned buys not written for the fail-safes")
            self.event("multi_day_error", {"step": "before_send", "strategy": rule,
                                           "error": f"{type(e).__name__}: {e}"[:200]})

    def _report_unfilled(self, om: OrderManager) -> None:
        """Issue #333, question 4: an order of this path (not a protective stop) the venue let expire with shares
        left is journalled once by client id (`md_unfilled`) and reported as `multi_day_unfilled`, once its fills
        are booked (the venue's filled quantity is in the ledger). Nothing is sent again for it here."""
        if self._unfilled is None:
            self._unfilled = {str(b.get("coid")) for b in self._entries("md_unfilled")}
        for r in om.orders.values():
            if r.purpose == "stop" or r.state is not St.EXPIRED or r.coid in self._unfilled:
                continue
            if r.booked + 1e-9 < r.cum or r.booked + 1e-9 >= r.qty:
                continue  # a fill still to book, or nothing left
            body = {"run_id": om.run_id, "strategy": r.strategy, "symbol": r.symbol, "side": r.side,
                    "qty": float(r.qty), "filled": float(r.booked), "left": float(r.qty - r.booked), "coid": r.coid,
                    "session": r.session, "status": str(r.state), "purpose": r.purpose, "type": r.type,
                    "tif": r.tif}
            self.journal.append("md_unfilled", body)
            self._unfilled.add(r.coid)
            self.event("multi_day_unfilled", {k: v for k, v in body.items() if k != "run_id"})

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
        for r in self.active():
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
                if isinstance(e, StoreFailedCheck):
                    self._failed_check.add(r.name)
                else:
                    self._failed_check.discard(r.name)
                if self._waiting.get(r.name) != str(e):
                    self._waiting[r.name] = str(e)
                    self.event("multi_day_waiting", {"strategy": r.name, "session": str(closed), "why": str(e)[:200]})
                continue
            held = {s: q for s, (q, _) in self.account.held(r.name).items()}
            hit = sorted(set(data.dropped) & set(held))
            if hit:  # a held stock failed the check: never through the "not in the view" path (it would sell it)
                why = "held " + "; ".join(data.dropped[s] for s in hit)
                self._failed_check.add(r.name)
                if self._waiting.get(r.name) != why[:200]:
                    self._waiting[r.name] = why[:200]
                    self.event("multi_day_waiting", {"strategy": r.name, "session": str(closed), "why": why[:200]})
                continue
            self._waiting.pop(r.name, None)
            self._failed_check.discard(r.name)
            if data.dropped:
                text = "decided without " + ", ".join(sorted(data.dropped)) + ": daily prices failed the check ("
                text += "; ".join(data.dropped[s] for s in sorted(data.dropped)) + ")"
                self._dropped[r.name] = (str(fill), text[:240])
                self.event("multi_day_view_dropped", {"strategy": r.name, "session": str(closed),
                                                      "dropped": {s: w[:160] for s, w in data.dropped.items()}})
            else:
                self._dropped.pop(r.name, None)
            tl = decide_after_close(r.strategy, data, data.index_of(closed), fill, held)
            body = {"run_id": rid, "strategy": r.name, "session": str(closed), "fill_session": str(fill),
                    "decided_at": now.isoformat(), "held": held, "target": target_to_json(tl)}
            self.journal.append("md_decision", body)
            buy = list(tl.features.get("buy") or []) if tl else []
            self._ca_prewarm(closed, fill, set(held) | set((tl.weights if tl else {}) or {}))
            self.event("multi_day_decision", {"strategy": r.name, "session": str(closed), "fill_session": str(fill),
                                              "decides": tl is not None,
                                              "buy": buy[:10],
                                              "sell": dict(tl.features.get("sell") or {}) if tl else {},
                                              # #229, reporting only: the dashboard's plan, sell readings, sectors
                                              "decided_at": now.isoformat(), "held": held,
                                              "buy_candidates": buy[:60],
                                              "ranked": tl.features.get("ranked") if tl else None,
                                              "sell_when": dict(tl.features.get("sell_when") or {}) if tl else {},
                                              "sectors": {s: data.sector.get(s) for s in [*held, *buy[:60]]}})
            self._filing_observe(r.name, closed, now, [*held, *buy])
            done.append(r.name)
        return done

    def _filing_observe(self, rule: str, session: date, now: datetime, symbols: list[str]) -> None:
        """#375: the 8-K do-not-buy flags of the decision's names, logged once per decision. Never raises."""
        if self.filing_flags is None:
            return
        try:
            body = self.filing_flags(sorted(set(symbols)), now, session)
            self.event("filing_flag_shadow", {"strategy": rule, "session": str(session), **body})
        except Exception:
            log.exception("filing flag observer failed (no effect on the plan or the orders)")

    def _view(self, k: date, now: datetime) -> DailyData:
        if k not in self._data:
            self._data = {k: self.load(k, now)}  # one evening's view at a time
        return self._data[k]

    def _close(self, day: date) -> time | None:
        """The session's close from the calendar (None: unreadable, or a normal 16:00 close not given)."""
        if day in self._closes:  # one calendar read per session (QA-Ops second key: not one per tick)
            close = self._closes[day]
        else:
            try:
                close = self.session_close(day)
            except Exception:
                close = None  # unreadable: 15:45, read again at the next call
            else:
                self._closes = {day: close}
        return close

    def policy_for(self, day: date) -> ExecutionPolicy:
        """The session's execution style: near-close DAY market orders (issue #333), against its close."""
        close = self._close(day)
        return ExecutionPolicy("near_close", close=min(close or REGULAR_CLOSE, REGULAR_CLOSE))

    def send_from(self, day: date) -> time:
        """15:50 ET, or on an early-close day the close minus 10 minutes (issue #333)."""
        return minus(self.policy_for(day).close, NEAR_CLOSE_MIN)

    def send_until(self, day: date) -> time:
        """15:57 ET, or on an early-close day the close minus 3 minutes (issue #333: the last minutes are the
        venue's, to fill the orders before the close)."""
        return minus(self.policy_for(day).close, SEND_LAST_MIN)

    def in_window(self, now: datetime) -> bool:
        et = now.astimezone(NY)
        return self.send_from(et.date()) <= et.time() < self.send_until(et.date())

    def _read_shorting(self, day: date | None = None) -> None:
        """QA-Ops review of #207: multi-day buys need shorting OFF at the paper account (a late stop must never open a
        short). Read at start-up and once per session; unreadable counts as on (fail closed)."""
        try:
            v = (self.broker.account() or {}).get("shorting_enabled")
            on = True if v is None else (v is True or str(v).lower() == "true")
        except Exception:
            on = True
        if on and self.shorting is not True:
            self.event("multi_day_shorting_on", {"action": "multi-day buys refused until shorting is off at the "
                                                           "paper account: ./scripts/alpaca_no_shorting.sh"})
        self.shorting, self._shorting_day = on, day

    def send(self, session: date) -> list[str]:
        """Session `session`, inside the send window: each rule's targets, journalled before any order, sent once
        through the target loop. Returns the rules whose send step completed now."""
        om = self._manager()
        rid = self.account.run_id()
        if om is None or rid is None or not self.in_window(self.now()) or self.account.book_ended():
            return []
        done = []
        for r in self.active():
            if self._send_entry(r.name, session, "md_sent") is not None:
                continue
            if self._shorting_day != session:
                self._read_shorting(session)
            self.gate.shorting = self.shorting
            if not self._drop_failsafe_stops(r.name, session):
                continue  # a fail-safe stop still holds shares (or blocks a buy): the next tick goes on
            planned = self._send_entry(r.name, session)
            if planned is None:
                if self.review is not None and self._review_record(r.name, session) is None \
                        and self.review.pending(r.name, session):
                    if self._window_left(self.now()) > REVIEW_WAIT_UNTIL_S:
                        continue  # #131: the pre-open review is still running (it has its own hard deadline)
                    self._review_cut(r.name, session, rid)  # too close to the window's end: sales must go out
                planned = self._plan(r, session, rid)
                if planned is None:
                    continue
                if self.review is not None:
                    planned = self._reviewed(r, session, rid, planned)
                    if planned is None:
                        continue
                self.journal.append("md_send", planned)
                rv = planned.get("review") or {}
                if rv.get("vetoed") or rv.get("downgraded"):
                    self.event("multi_day_review_applied", {"strategy": r.name, "session": str(session), **rv})
                held = {s: q for s, (q, _) in self.account.held(r.name).items()}  # #229: the plan, for the dashboard
                self.event("multi_day_plan", {"strategy": r.name, "session": str(session),
                                              "decided": planned.get("decided"), "note": planned.get("note"),
                                              "skipped": planned.get("skipped"),
                                              "targets": [[s, float(held.get(s, 0.0)), q, p, w]
                                                          for s, q, p, w in planned["targets"]]})
            self._shadow_observe(r.name, session, rid, planned)  # #310: a copy, to an observer; nothing comes back
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
            try:
                mine = {i for x in om.live.values() if x.purpose != "stop" for i in x.ids}  # a stop counts
                self.gate.opposite = opposite_orders(self.broker.open_orders(), mine)
            except Exception:  # unreadable: send nothing this tick (fail closed), try again at the next
                self.event("multi_day_waiting", {"strategy": r.name, "session": str(session),
                                                 "why": "open orders unreadable"})
                continue
            self._before_send(r.name, session, targets, held_now)
            res = reconcile_targets(om, self.policy_for(session), targets, held_now, now=self.now(),
                                    session=str(session), run=rid, reconciler=self.gate, stops=self.stops,
                                    sells_first=False, presend=self._gate_check(r, session, targets))
            withheld = self._note_withheld(r.name, session, rid, res.withheld)
            sent = [{"coid": x.coid, "symbol": x.symbol, "side": x.side, "qty": x.qty, "state": str(x.state),
                     "reason": x.reason} for x in res.sent]
            skipped = [[k[1], why[:160]] for k, why in res.skipped]
            # complete unless something may still go out today: an order nobody has confirmed yet, a stop still
            # holding the shares, a cancel under way, or a target the loop skipped (entries blocked, an unresolved
            # reconciliation difference): the next tick runs the loop again on the same journalled targets
            retry = bool(res.deferred or res.canceled or skipped or withheld) \
                or any(x.state in UNCONFIRMED for x in res.sent)
            said = repr((sent, skipped, res.deferred, res.canceled, withheld))
            if self._said.get(r.name) != said:
                self._said[r.name] = said
                self.event("multi_day_orders", {"strategy": r.name, "session": str(session), "sent": sent,
                                                "skipped": skipped, "deferred": [k[1] for k in res.deferred],
                                                "canceled": res.canceled, "retry": retry,
                                                **({"withheld": withheld} if withheld else {})})
            if not retry:
                self.journal.append("md_sent", {"run_id": rid, "strategy": r.name, "session": str(session),
                                                "sent": [x["coid"] for x in sent], "skipped": skipped})
                done.append(r.name)
        return done

    # ---------------------------------------------------------------- the pre-send gate (M9 1.16, issue #441)
    def _sizing_closes(self, rule: str, session: date, symbols: Sequence[str]) -> dict[str, float]:
        """The close each order's shares were sized at: the previous session's raw close from the evening's daily
        view, on today's share basis (a split the ledger booked for today divides it), else the ledger's mark of a
        held name. A stock without either has no entry (the gate then has no move to check). Never raises."""
        out: dict[str, float] = {}
        try:
            prev = self.prev_session(session)
            data = self._view(prev, self.now())
            k, splits = data.index_of(prev), self.account.splits(session)
            for s in symbols:
                j = data._col.get(s)
                px = float(data.raw_c[k, j]) if j is not None else math.nan
                if math.isfinite(px) and px > 0:
                    out[s] = px / float(splits.get(s) or 1.0)
        except Exception as e:
            log.warning("pre-send gate: no daily view of the sizing close (%s): the ledger's marks", e)
        try:
            for s, (_, mark) in self.account.held(rule).items():
                if s in symbols and s not in out and mark is not None and math.isfinite(mark) and mark > 0:
                    out[s] = float(mark)
        except Exception:
            log.exception("pre-send gate: the ledger's marks are unreadable")
        return out

    def _gate_check(self, r: Rule, session: date, targets: Sequence[Target]):
        """The target loop's `presend` for this tick, or None without a quote source. The quotes are read once,
        only when the loop has an order to send, for every stock of the plan in one request; a failed read is one
        `multi_day_error` event (step `presend_quotes`) and every order is then checked at its prior close."""
        if self.quotes is None:
            return None
        symbols = sorted({t.symbol for t in targets})
        state: dict = {}

        def check(t: Target, side: str, qty: float) -> str | None:
            if not state:
                state["closes"] = self._sizing_closes(r.name, session, symbols)
                state["cap"] = None
                try:
                    lim = r.builder.limits
                    state["cap"] = float(self.account.equity()) * float(lim.max_name_pct) / 100 \
                        * (1 + self.presend_gate.collar)
                except Exception:
                    log.exception("pre-send gate: no value cap (the book's limits or equity are unreadable)")
                try:
                    state["quotes"] = dict(self.quotes(symbols) or {})
                    self._said.pop(f"{r.name}:quotes", None)
                except Exception as e:
                    state["quotes"] = {}
                    why = f"{type(e).__name__}: {e}"[:200]
                    if self._said.get(f"{r.name}:quotes") != str(session):  # once per session, not per tick
                        self._said[f"{r.name}:quotes"] = str(session)
                        self.event("multi_day_error", {"step": "presend_quotes", "strategy": r.name,
                                                       "session": str(session), "error": why,
                                                       "action": "orders are checked at the prior close"})
            return self.presend_gate.check(side, qty, close=state["closes"].get(t.symbol),
                                           quote=quote_from_alpaca(state["quotes"].get(t.symbol)), now=self.now(),
                                           max_value=state["cap"])

        return check

    def _note_withheld(self, rule: str, session: date, rid: int, withheld) -> list[list]:
        """Journal each withheld order once per (rule, session, stock, side) (`md_withheld`: the record the next
        session's carry of a reduction reads) and return [[symbol, why]] for the orders event."""
        out = [[k[1], why[:160]] for k, _, why in withheld]
        if not out:
            return out
        if self._withheld is None:
            self._withheld = {(b.get("strategy"), b.get("session"), b.get("symbol"), b.get("side"))
                              for b in self._entries("md_withheld")}
        plan = self._send_entry(rule, session) or {}
        for (_, sym), side, why in withheld:
            held = float(self.account.held(rule).get(sym, (0.0, None))[0])
            target = next((int(q) for s, q, _, _ in plan.get("targets", []) if s == sym), None)
            key = (rule, str(session), sym, side)
            if key in self._withheld:
                continue
            self._withheld.add(key)
            self.journal.append("md_withheld", {"run_id": rid, "strategy": rule, "session": str(session),
                                                "symbol": sym, "side": side, "target": target, "held": held,
                                                "why": why[:160]})
        return out

    def _withheld_trims(self, rule: str, session: date, held: Mapping[str, object]) -> list[Target]:
        """Reductions (a resize down, not to zero) the pre-send gate withheld on the last send before `session`:
        sent again as the same reduction, to the same target shares, while the sleeve still holds more. A sale to
        zero is `_pending`'s; a buy is the carry-over's."""
        last = None
        for b in self._entries("md_send"):
            if b.get("strategy") == rule and b.get("session") < str(session):
                last = b
        if last is None:
            return []
        gated = {b.get("symbol") for b in self._entries("md_withheld")
                 if b.get("strategy") == rule and b.get("session") == last["session"] and b.get("side") == "sell"}
        return [Target(rule, s, int(q), "resize", {"why": "withheld"}) for s, q, _, _ in last["targets"]
                if s in gated and s in held and 0 < int(q) < float(held[s][0])]

    # ---------------------------------------------------------------- the pre-open review (#131, off by default)
    def _window_left(self, now: datetime) -> float:
        """Seconds left in today's send window (15:57 ET, or the early close minus 3 minutes)."""
        et = now.astimezone(NY)
        end = datetime.combine(et.date(), self.send_until(et.date()), tzinfo=NY)
        return (end - et).total_seconds()

    def _review_cut(self, rule: str, session: date, rid: int) -> None:
        """Backend-quant key on #248: a review still running this close to the end of the send window must not
        hold back the rule's SALES (the send waits for the whole plan). It is journalled as a timeout: fail closed,
        no buys this session, sales go out now. A late outcome from the worker is ignored (the journal's wins)."""
        from kaibot.agents.preopen_daily import EVENT, event_body

        rec = {"run_id": rid, "strategy": rule, "session": str(session), "status": "timeout",
               "error": f"still running {REVIEW_WAIT_UNTIL_S:.0f} s before the send window closes: no buys, sales "
                        "go out", "verdicts": {}, "answer": None, "latency_ms": None, "cost_usd": 0.0, "arms": {},
               "candidates": [], "targets": [], "vetoed": [], "downgraded": [], "allowed": [],
               "pass_through": False}
        self.journal.append("md_review", rec)
        self.event(EVENT, event_body(rec))

    def _review_record(self, rule: str, session: date) -> dict | None:
        """The journalled `md_review` of (rule, session), collecting a finished one first (journal, then event)."""
        rec = self._send_entry(rule, session, "md_review")
        if rec is not None or self.review is None:
            return rec
        rec = self.review.collect(rule, session)
        if rec is not None:
            from kaibot.agents.preopen_daily import EVENT, event_body

            self.journal.append("md_review", rec)
            self.event(EVENT, event_body(rec))
        return rec

    def _reviewed(self, r: Rule, session: date, rid: int, planned: dict) -> dict | None:
        """The plan with the review's verdicts applied, or None while the review runs (it is started here when the
        pre-open step did not, e.g. an engine started after 09:31)."""
        from kaibot.agents.preopen_daily import reviewed_plan

        held = self.account.held(r.name)
        rec = self._review_record(r.name, session)
        if rec is None and self._window_left(self.now()) <= REVIEW_WAIT_UNTIL_S:
            # QA-Ops key on #248: no review yet and too close to the window's end to wait for one (an engine started
            # in the last minutes, whose next tick may be past the window): no call is started, the review is a
            # timeout (no buys) and the sales go out on this tick
            self._review_cut(r.name, session, rid)
            rec = self._review_record(r.name, session)
        if rec is None:
            if not self.review.pending(r.name, session):
                dec = self._decision_for(r.name, session)
                tl = target_from_json(dec["target"]) if dec is not None else None
                self.review.submit(r.name, session, planned, tl, held, self.now(), rid)
            rec = self._review_record(r.name, session)  # an inline review (replays) is done now
            if rec is None:
                return None
        return reviewed_plan(r.name, planned, held, rec)[0]

    # ---------------------------------------------------------------- the shadow review (#310, off by default)
    def _shadow_observe(self, rule: str, session: date, rid: int, planned: dict) -> None:
        """Hand the journalled plan to the shadow observer (a deep copy is taken there). Never raises."""
        if self.shadow is None:
            return
        try:
            if self._send_entry(rule, session, "md_review_shadow") is not None or self.shadow.pending(rule, session):
                return
            dec = self._decision_for(rule, session)
            tl = target_from_json(dec["target"]) if dec is not None else None
            self.shadow.observe(rule, session, planned, tl, self.account.held(rule), self.now(), rid)
        except Exception:
            log.exception("shadow pre-open review not started (no effect on the plan or the orders)")

    def _shadow_collect(self) -> None:
        """Journal every finished shadow record once (`md_review_shadow`) and send its event. Never raises."""
        if self.shadow is None:
            return
        try:
            from kaibot.agents.preopen_shadow import EVENT, KIND, event_body

            for rule, session in self.shadow.finished():
                rec = self.shadow.collect(rule, date.fromisoformat(session))
                if rec is None or self._send_entry(rule, date.fromisoformat(session), KIND) is not None:
                    continue
                self.journal.append(KIND, rec)
                self.event(EVENT, event_body(rec))
        except Exception:
            log.exception("shadow pre-open record not journalled (no effect on the plan or the orders)")

    def note_final(self) -> None:
        """#311: the engine is stopping. From the close on, today's note is written now if it was not yet (the last
        tick may come before NOTE_FROM); it changes nothing else. Never raises."""
        self._note_step(final=True)

    def _note_step(self, final: bool = False) -> None:
        """#311: start the day's plain-words report once and journal it (`daily_note`) with its event. Never raises."""
        if self.note is None:
            return
        try:
            from kaibot.agents.daily_note import EVENT, KIND, event_body

            for rec in self.note.tick(self, final=final):
                if any(b.get("session") == rec.get("session") for b in self._entries(KIND)):
                    continue
                self.journal.append(KIND, rec)
                self.event(EVENT, event_body(rec))
        except Exception:
            log.exception("daily note not journalled (no effect on trading)")

    def preopen(self) -> list[str]:
        """#131: from 08:30 ET until 09:31 ET, start the review of each rule's plan for today's
        session and journal finished reviews. Nothing at all when the review is off. Returns the rules started."""
        if self.review is None or not self.review.enabled:
            return []
        now = self.now()
        et = now.astimezone(NY)
        if not (PREOPEN_FROM <= et.time() < PREOPEN_UNTIL):
            return []
        rid = self.account.run_id()
        if rid is None or self.account.book_ended() or self._manager() is None:
            return []
        session = et.date()
        if self._preopen_day != session:  # one calendar read per day: is today a session?
            try:
                self._preopen_day, self._preopen_ok = session, self.next_session(self.prev_session(session)) == session
            except Exception:
                return []  # unreadable: the send step starts the review instead
        if not self._preopen_ok:
            return []
        started = []
        for r in self.active():
            if self._send_entry(r.name, session) is not None or self._review_record(r.name, session) is not None:
                continue
            if self.review.pending(r.name, session):
                continue
            planned = self._plan(r, session, rid)
            if planned is None:
                continue
            self._reviewed(r, session, rid, planned)  # started (an inline review is journalled at once)
            started.append(r.name)
        return started

    def _drop_failsafe_stops(self, rule: str, session: date) -> bool:
        """QA-Ops review of #207: a stop the watchdog or the dead-man placed while the engine was silent (hung, not
        restarted) is adopted into the order manager and, because the rule's pre-registration has no stop
        (`PREREG_STOP_ATR`), cancelled, before anything is sent: it would hold the shares of a sale and make
        Alpaca refuse a buy of the same stock (wash trade). True once no such stop is open (or none existed)."""
        sleeves = {(rule, s): q for s, (q, _) in self.account.held(rule).items()}
        if not sleeves:
            return True
        if self.stops.adopt(sleeves, str(session)) is None:
            return False  # open orders unreadable: nothing is sent this tick
        left = []
        for key in sleeves:
            for x in self.stops.stops_of(key):
                if x.state in UNCONFIRMED:
                    left.append(x.coid)
                    continue
                if x.state is not St.PENDING_CANCEL and x.order_id is not None:
                    self.om.cancel(x.coid)
                if x.open:
                    left.append(x.coid)
        if left:
            said = repr(left)
            if self._said.get(f"{rule}:stops") != said:
                self._said[f"{rule}:stops"] = said
                self.event("multi_day_stops_cancelled", {"strategy": rule, "session": str(session), "coids": left,
                                                         "why": "the rule has no stop: a fail-safe stop is cancelled "
                                                                "before the rule's orders go out"})
        return not left

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
        pending = {**self._pending(r.name, session, held),
                   **{s: w for s, w in self._adopted(r.name).items() if s in held}}
        tl = target_from_json(dec["target"]) if dec is not None else None
        if dec is None:
            self._no_decision(r.name, session, prev)
        if dec is None and not pending:
            return {"run_id": rid, "strategy": r.name, "session": str(session), "decided": None, "targets": [],
                    "note": "no decision"}
        if tl is None:
            plan, targets = plan_session(r.builder, None, r.name, {}, pending, None, 0, 0.0, 0.0)
            if dec is None:  # no decision was taken after the last close: nothing is carried on an old plan
                return self._planned(r, session, rid, prev, targets, None)
            targets = targets + self._withheld_trims(r.name, session, held)  # M9 1.16: a withheld reduction
            sales = list(targets)  # the pending sales: they go out whatever happens to the carry-over
            ca: dict = {}
            try:
                carry, note = self._carry(r, session, rid, prev, held, {t.symbol for t in sales})
                if carry:
                    targets = self._overnight_cut(r, session, sales + carry, held)
                    targets, ca = self._ca_cut(r.name, session, prev, targets, held)
                    kept = {t.symbol for t in targets}
                    for sym in sorted({t.symbol for t in carry} - kept):
                        note["skipped"][sym] = "corporate action or overnight cap"
                    for sym, why in self._carry_room(r, prev, [t for t in targets if t.purpose == CARRY],
                                                     held).items():
                        note["skipped"][sym] = why
                    targets = [t for t in targets if t.purpose != CARRY or t.symbol not in note["skipped"]]
                    kept = {t.symbol for t in targets if t.purpose == CARRY}
                    note = {**note, "carried": [c for c in note["carried"] if c[0] in kept]}
            except Exception as e:  # QA-Ops on #366: a failed carry-over never holds back the session's sales
                log.exception("carry-over failed: nothing is carried this session")
                why = f"{type(e).__name__}: {e}"[:200]
                self.event("multi_day_error", {"step": "carry", "strategy": r.name, "session": str(session),
                                               "error": why})
                targets, ca, note = sales, {}, {"carried": [], "skipped": {}, "risk_off": False, "error": why}
            if note:  # every carry-over and every skip, with its reason (also when nothing is carried)
                self.event("multi_day_carry", {"strategy": r.name, "session": str(session), **note})
            return {**self._planned(r, session, rid, prev, targets, None), **ca, **({"carry": note} if note else {})}
        try:
            data = self._view(prev, self.now())
        except DataNotReady as e:  # the evening's view went away (store moved): only the pending sales
            self.event("multi_day_waiting", {"strategy": r.name, "session": str(session), "why": str(e)[:200]})
            _, targets = plan_session(r.builder, None, r.name, {}, pending, None, 0, 0.0, 0.0)
            return self._planned(r, session, rid, prev, targets, None)
        hit = sorted(set(data.dropped) & (set(held) | set(tl.weights)))
        if hit:  # the store changed since the decision (restart) and a name of it now fails the check: no new
            # sizing on those prices, only the sales already pending (as when the view went away)
            self.event("multi_day_waiting", {"strategy": r.name, "session": str(session),
                                             "why": ("failed the check since the decision: "
                                                     + "; ".join(data.dropped[s] for s in hit))[:200]})
            _, targets = plan_session(r.builder, None, r.name, {}, pending, None, 0, 0.0, 0.0)
            return self._planned(r, session, rid, prev, targets, None)
        live = with_session(data, session, self.account.splits(session))
        i = len(live.dates) - 1
        sized = {}
        for s, (q, mark) in held.items():  # held names are sized at the daily close (the research runner's mark)
            j = live._col.get(s)
            px = float(live.px[i - 1, j]) / live.split_at(i, j) if j is not None else math.nan
            sized[s] = (q, px if math.isfinite(px) else (mark if mark is not None else math.nan))
        adopted = {s: w for s, w in self._adopted(r.name).items() if s in held}
        unknown = [s for s in held if s not in live._col and s not in adopted]
        if unknown:  # a held name outside the daily view (it left the store): only sold, never sized
            pending = {**pending, **{s: "not_in_daily_view" for s in unknown}}
        eq, cash = float(self.account.equity()), float(self.account.cash())
        plan, targets = plan_session(r.builder, tl, r.name, {s: v for s, v in sized.items() if s not in unknown},
                                     {s: w for s, w in pending.items() if s not in unknown}, live, i, eq, cash)
        targets += [Target(r.name, s, 0, "sell", {"why": "not_in_daily_view"}) for s in unknown]
        if adopted:  # #266: shares a spin-off credited are sold at this closing auction, whatever the rule decided
            targets = [t for t in targets if t.symbol not in adopted]
            targets = [Target(r.name, s, 0, "sell", {"why": w}) for s, w in adopted.items()] + targets
        targets = self._overnight_cut(r, session, targets, held)
        targets, ca = self._ca_cut(r.name, session, prev, targets, held)
        return {**self._planned(r, session, rid, prev, targets, plan, eq=eq, cash=cash), **ca}

    def _overnight_cut(self, r: Rule, session: date, targets: list[Target],
                       held: Mapping[str, tuple[float, float | None]]) -> list[Target]:
        """allow_overnight false: nothing may be held over the close, so no buy goes out."""
        if r.builder.limits.max_overnight_pct > 0:
            return targets
        cut = [t for t in targets if t.shares > float(held.get(t.symbol, (0.0, None))[0])]
        if cut:
            self.event("multi_day_overnight_off", {"strategy": r.name, "session": str(session),
                                                   "not_bought": [t.symbol for t in cut][:30]})
        return [t for t in targets if t not in cut]

    def _carry(self, r: Rule, session: date, rid: int, prev: date,
               held: Mapping[str, tuple[float, float | None]], busy: set[str]) -> tuple[list[Target], dict]:
        """#363 (trend_momentum Amendment 11, mom_12_1_band Amendment 9): on a session that is not a decision's fill
        session, the latest decision's buys that are still short of their targets (engine/daily.carry_targets, the
        research runner's core). The plan is that decision's journalled send (`md_send` of its fill session: after
        the review, the corporate-action guard and the overnight cap of that day). The evening's daily view gives
        the market filter, the fill day's bar and the rule's buy conditions; without it nothing is carried.

        QA-Ops on #366: only a decision and a send of THIS run count (a plan's share counts were sized on its own
        run's equity: a new run started since never buys the old run's plan); a stock the ledger booked a split
        for today is skipped (the plan's shares are on yesterday's basis); the shares held at the decision go to
        `ended_buys`, so a name that lost shares since (an emergency stop that fired while the engine was down) is
        not bought back."""
        last = None
        for b in self._entries("md_decision"):
            if b.get("run_id") == rid and b.get("strategy") == r.name and b.get("target") is not None \
                    and b.get("fill_session") < str(session):
                last = b
        plan_send = self._send_entry(r.name, date.fromisoformat(last["fill_session"])) if last else None
        if plan_send is None or plan_send.get("run_id") != rid or self.om is None:
            return [], {}
        now_held = {s: float(q) for s, (q, _) in held.items()}
        plan = [(s, int(q), p) for s, q, p, _ in plan_send["targets"] if s not in busy]
        if not any(p in ("buy", "resize") and q > now_held.get(s, 0.0) for s, q, p in plan):
            return [], {}
        note = {"of": last["fill_session"], "carried": [], "skipped": {}, "risk_off": False}
        try:
            data = self._view(prev, self.now())
        except DataNotReady as e:
            self.event("multi_day_waiting", {"strategy": r.name, "session": str(session),
                                             "why": ("no carry-over: " + str(e))[:200]})
            return [], {**note, "unread": str(e)[:200]}
        short = {s for s, q, p in plan if p in ("buy", "resize") and q > now_held.get(s, 0.0)}
        early = {s: "daily prices failed the check" for s in short if s in data.dropped}
        splits = self.account.splits(session)
        early.update({s: "split today" for s in short if s in splits})
        plan = [x for x in plan if x[0] not in early]
        fill = date.fromisoformat(last["fill_session"])
        if fill not in set(data.dates.astype(object)):
            return [], {}
        ended = ended_buys(self.om, r.name, str(fill), str(session),
                           base={s: float(q) for s, q in (last.get("held") or {}).items()}, held=now_held)
        out, got = carry_targets(r.strategy, r.name, plan, now_held, data, data.index_of(fill),
                                 data.index_of(prev), ended)
        note = {**note, **got}
        if early:
            if note["risk_off"]:
                early = {s: "market filter off" for s in early}
            note["skipped"] = {**note["skipped"], **early}
        return out, note

    def _carry_room(self, r: Rule, prev: date, carry: Sequence[Target],
                    held: Mapping[str, tuple[float, float | None]]) -> dict[str, str]:
        """QA-Ops on #366: a carried buy keeps the plan's share count, which was sized at the decision's prices and
        against that day's cash. Nothing downstream sizes it again, and the paper account is a margin account (the
        venue lends, it does not refuse). So before a carried buy goes out it must still fit at the previous close,
        as a decision-day buy must in the builder: the per-name cap, its sector's cap, the cash less the builder's
        reserve, and the book's gross and overnight cap with the cash floor. Best-ranked first (the plan's order);
        as in the ranked fill, a name over its own or its sector's cap is skipped and the next may fit, and once
        the cash or the book's cap is reached buying stops. Returns symbol -> why it is not sent this session (it
        is tried again at the next). Only ever removes a buy; a book that cannot be valued carries nothing."""
        if not carry:
            return {}
        lim = r.builder.limits
        eq, cash = float(self.account.equity()), float(self.account.cash())
        if not (math.isfinite(eq) and eq > 0 and math.isfinite(cash)):
            return {t.symbol: "the book's equity or cash cannot be read" for t in carry}
        data = self._view(prev, self.now())
        k, tol = data.index_of(prev), 1e-9 * eq

        def close(sym: str) -> float:
            j = data._col.get(sym)
            return float(data.px[k, j]) if j is not None else math.nan

        def bucket(sym: str) -> str:
            return data.sector.get(sym) or f"own:{sym}"  # a stock without a sector is its own (Amendment 4)

        exposure, used = 0.0, {}
        for sym, (q, mark) in held.items():
            px = float(mark) if mark is not None and math.isfinite(float(mark)) and float(mark) > 0 else close(sym)
            if not (math.isfinite(px) and px > 0):
                return {t.symbol: f"the book cannot be valued (no price for {sym})" for t in carry}
            exposure += float(q) * px
            used[bucket(sym)] = used.get(bucket(sym), 0.0) + float(q) * px
        money = cash - float(r.builder.cash_reserve or 0.0) * eq
        cap = eq * min(lim.max_gross_pct, lim.max_overnight_pct, 100 - lim.min_cash_pct) / 100
        out: dict[str, str] = {}
        stopped: str | None = None
        for t in carry:
            px, have = close(t.symbol), float(held.get(t.symbol, (0.0, None))[0])
            add = (t.shares - have) * px
            if stopped is not None:
                out[t.symbol] = stopped
            elif not (math.isfinite(px) and px > 0):
                out[t.symbol] = "no price at the last close"
            elif t.shares * px > eq * lim.max_name_pct / 100 + tol:
                out[t.symbol] = "above the per-name cap at the last close"
            elif used.get(bucket(t.symbol), 0.0) + add > eq * lim.max_sector_pct / 100 + tol:
                out[t.symbol] = "its sector is at the sector cap at the last close"
            elif add > money + tol:
                out[t.symbol] = stopped = "no cash room at the last close (the cash reserve is kept)"
            elif exposure + add > cap + tol:
                out[t.symbol] = stopped = "the book is at its exposure cap at the last close"
            else:
                money, exposure = money - add, exposure + add
                used[bucket(t.symbol)] = used.get(bucket(t.symbol), 0.0) + add
        return out

    def _adopted(self, rule: str) -> dict[str, str]:
        f = getattr(self.account, "adopted", None)  # an Account without corporate actions adopts nothing
        return dict(f(rule)) if f is not None else {}

    def _ca_prewarm(self, evening: date, session: date, symbols) -> None:
        """The evening's read of announced corporate actions for the held and decided names (the heartbeat's notes;
        the send step's read uses the same cache). A failure here only means the send step reads again."""
        if self.ca is None or not symbols:
            return
        try:
            self.ca.lookup(evening, session, symbols)
            self._ca_evening = evening
        except Exception:  # never stops a decision
            log.exception("corporate-actions read (evening) failed")

    def _ca_cut(self, rule: str, session: date, prev: date, targets: list[Target],
                held: Mapping[str, tuple[float, float | None]]) -> tuple[list[Target], dict]:
        """#266, trend_momentum.md Amendment 6 (a live-safety exclusion): no buy, and no add to a holding, of a
        stock with an announced split, reverse split, spin-off, merger or symbol change in the next 35 sessions; if
        the read fails, no buy at all this session. Sales and kept holdings are never touched. Returns the targets
        left and what the send entry journals (`ca_skipped`: [symbol, action, date, text]; `ca_unread`: why)."""
        if self.ca is None:
            return targets, {}
        buys = [t for t in targets if t.shares > float(held.get(t.symbol, (0.0, None))[0])]
        if not buys:
            return targets, {}
        found, why = self.ca.lookup(prev, session, {t.symbol for t in buys} | set(held))
        self._ca_evening = prev if why is None else self._ca_evening
        if why is not None:
            self._ca_unread[rule] = (str(session), why)
            self.event("multi_day_ca_unread", {"strategy": rule, "session": str(session), "why": why[:200],
                                               "not_bought": [t.symbol for t in buys][:30],
                                               "text": "no new buys this session: " + why[:160]})
            return [t for t in targets if t not in buys], {"ca_unread": why}
        self._ca_unread.pop(rule, None)
        cut = [t for t in buys if t.symbol.upper() in found]
        if not cut:
            return targets, {}
        out = []
        for t in cut:
            a = found[t.symbol.upper()]
            out.append([t.symbol, a.action, a.on, a.skip_text()])
            self.event("multi_day_ca_skip", {"strategy": rule, "session": str(session), "symbol": t.symbol,
                                             "action": a.action, "on": a.on, "text": a.skip_text()})
        return [t for t in targets if t not in cut], {"ca_skipped": out}

    def _ca_notes(self, today: date | None) -> list[str]:
        """Heartbeat lines: why nothing was bought (a failed read), and each held stock with an announced action."""
        if self.ca is None:
            return []
        out = [f"{r}: no new buys this session: {w}" for r, (s, w) in sorted(self._ca_unread.items())
               if today is None or s == str(today)]
        if self._ca_evening is not None:
            held = {s for r in self.rules for s in self.account.held(r.name)}
            # QA-Ops (#271 follow-up): a note whose date has passed is dropped (the read it came from may be days
            # old on evenings without a decision); an undated one stays
            out += [a.note() for _, a in sorted(self.ca.known(self._ca_evening, held).items())
                    if not a.on or today is None or a.on >= str(today)]
        return out

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
        failed = why is not None and rule in self._failed_check and self._waiting.get(rule) == why
        note = ("no decision today: daily prices failed the check" if failed else
                "no decision today: daily store not refreshed (see logs/fetch_daily.log)" if why else
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
            ret = self.retirement(r.name)
            if ret is not None:
                rules[r.name] = {"retired": True, "note": retired_note(ret), "reenable": reenable_value(ret),
                                 "exit": exit_kind(r.name)}
                notes.append(retired_note(ret))
                continue
            last = None
            for b in self._entries("md_decision"):
                if b.get("strategy") == r.name:
                    last = b.get("session")
            row: dict = {"last_decision": last, "exit": exit_kind(r.name)}
            miss = self._missed.get(r.name)
            if miss is not None and (today is None or miss[0] == str(today)):
                row["note"] = miss[1]
                notes.append(f"{r.name}: {miss[1]}")
            drop = self._dropped.get(r.name)
            if drop is not None and (today is None or drop[0] == str(today)) and "note" not in row:
                row["note"] = drop[1]
                notes.append(f"{r.name}: {drop[1]}")
            if r.name in self._waiting:  # the next decision cannot be taken yet
                row["waiting"] = self._waiting[r.name][:200]
            if self.review is not None:  # #131: absent while the review is off (the heartbeat is unchanged)
                from kaibot.agents.preopen_daily import status_block

                rv = status_block(self._send_entry(r.name, today, "md_review") if today else None, today)
                row["review"] = rv or {"status": "pending" if today and self.review.pending(r.name, today)
                                       else "not_yet", "note": "pre-open review: not run yet today"}
            if self.shadow is not None:  # #310: absent while the shadow review is off
                try:
                    from kaibot.agents.preopen_shadow import KIND
                    from kaibot.agents.preopen_shadow import status_block as shadow_block

                    row["review_shadow"] = shadow_block(
                        self._send_entry(r.name, today, KIND) if today else None, today)
                except Exception:
                    log.exception("shadow review status unreadable")
            rules[r.name] = row
        out: dict = {"book": "ended" if ended else "open", "rules": rules,
                     "exits": {name: row["exit"] for name, row in rules.items()}}  # rule -> exit kind (dashboard)
        if self.review is not None:
            out["preopen_review"] = "on" if self.review.enabled else "off"
        if self.shadow is not None:
            out["preopen_review_shadow"] = "on"
        if self.note is not None:  # #311: the latest plain-words report
            try:
                from kaibot.agents.daily_note import KIND
                from kaibot.agents.daily_note import status_block as note_block

                written = self._entries(KIND)
                out["daily_note"] = note_block(written[-1] if written else None)
            except Exception:
                log.exception("daily note status unreadable")
        if any(v.get("retired") for v in rules.values()):
            out["retired"] = sorted(k for k, v in rules.items() if v.get("retired"))
        ca_notes = self._ca_notes(today)
        if ca_notes:
            out["corporate_actions"] = ca_notes
            notes = ca_notes + notes
        if ended and not any(v.get("retired") for v in rules.values()):
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


__all__ = ["LiveDaily", "Rule", "StoreLoader", "DataNotReady", "StoreFailedCheck", "live_limits", "live_builder",
           "with_session",
           "as_of", "target_to_json", "target_from_json", "SEND_FROM", "SEND_UNTIL", "PREOPEN_UNTIL", "exit_kind",
           "PREREG_STOP_ATR"]


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
        self.e._md_filled = True  # #333 (QA-Ops): today's ledger_position rows are written for the fail-safes

    def before_send(self, strategy: str, session: date, planned: Mapping[str, int]) -> None:
        self.e._md_positions_planned(session, strategy, planned)  # follow-up of #333: rows before the venue call

    def entry_block(self) -> str | None:
        return self.e.entry_block()

    def allows(self, symbol: str, side: str, purpose: str = "target", qty: float | None = None) -> str | None:
        return self.e.recon.allows(symbol, side, purpose, qty)

    def book_ended(self) -> bool:
        rid = self.run_id()
        return rid is not None and any(b.get("run_id") == rid for _, _, _, b in
                                       self.e.journal.entries(kinds=("md_book_end",)))

    def adopted(self, strategy: str) -> dict[str, str]:
        b = self._book()
        if b is None:
            return {}
        return {sym: "spin_off_adopted" for (st, sym), s in b.sleeves.items()
                if st == strategy and s.qty > 1e-9 and str(s.origin or "").startswith("spin_off:")}

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
               rules: Sequence[Rule] | Callable[[], Sequence[Rule]] | None = None, ca=None,
               review=None, shadow=None, note=None, filing_flags=None, quotes=None) -> LiveDaily:
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
                     known_order_ids=known_ids, session_close=lambda d: calendar_close(engine.broker.calendar(str(d))),
                     store=engine.store, reenable=os.environ.get(REENABLE_FLAG), ca=ca, review=review,
                     shadow=shadow, note=note, filing_flags=filing_flags,
                     quotes=quotes if quotes is not None else presend_quotes(engine))


PRESEND_QUOTE_DEADLINE_S = 8.0  # the gate's one quote read per tick ends by then (the send window is seven ticks)


def presend_quotes(engine) -> Callable[[Sequence[str]], Mapping[str, dict]] | None:
    """M9 1.16 (#441): the pre-send gate's quote source for the live engine: the market-data client's latest IEX
    quotes with a deadline (`AlpacaData.quotes_within`). None for any other data client (a replay's stored bars, a
    test fake): no gate there, so research and replay results cannot change."""
    fn = getattr(getattr(getattr(engine, "market", None), "data", None), "quotes_within", None)
    return (lambda symbols: fn(list(symbols), PRESEND_QUOTE_DEADLINE_S)) if callable(fn) else None


def calendar_close(cal: dict | None) -> time | None:
    """The close of a session from the broker's calendar row (Alpaca: "16:00" or "13:00")."""
    v = str((cal or {}).get("close") or "")
    try:
        hh, mm = v.replace(":", "")[:2], v.replace(":", "")[2:4]
        return time(int(hh), int(mm))
    except ValueError:
        return None


def rules_for(names: Sequence[str], pol: RiskPolicy, level: str) -> list[Rule]:
    """The enabled multi-day rules at the run's level, each with its live builder (#205's book: risk/profiles)."""
    from kaibot.risk.profiles import rule_book_limits
    from kaibot.strategies import registry

    out = []
    for n in registry.multi_day_names(names):
        s = registry.get(n)
        out.append(Rule(s, live_builder(s, live_limits(s, pol, rule_book_limits(n, level)))))
    return out
