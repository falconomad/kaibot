"""The live engine on Alpaca paper (or `SimBroker`), one paper run at a time. It knows no rule by name.

Per completed minute: sync orders/fills -> mark to market -> daily stop / run drawdown -> each open position's
strategy manages its stop -> time exit -> each live strategy that is due proposes entries (strategies/base.py,
in registry order), every one through the risk gate -> each position's strategy may close it out (MOC) ->
ledger -> shadow strategies. Prices, stops, targets and exits come from the strategies (strategies/registry.py:
which ones a level runs is config/profiles.yaml), sizes from `risk/gate.py`; no LLM sets any of them.

Books (S77): positions are keyed by (strategy, symbol) (`Positions`), so two rules can hold one stock. Every order
call is journaled before it is sent (`_order`: intent, then ack) and every fill, dividend, split and session close
goes to the ledger (engine/ledger.py), a fold over the local journal (engine/journal.py). Start equity is
yesterday's mark-to-market close; a restart with the market closed finalises the last session.

Safety:
- Every entry is a bracket order, so the stop and the 2R target live AT THE BROKER and fire even if this
  process (or the laptop) stops.
- On startup, positions the engine doesn't know about block all entries (account not clean); intraday positions it
  knows from an earlier session are closed at the first tick of the next open. Multi-day sleeves (the ledger's,
  S80 #120) are known positions, are never "carried", and keep their protective stops over the close.
- The run's pot (onboarding amount) caps sizing and buying power, whatever the paper account holds, and so
  does the account's own buying power (the smaller wins).
- Fail closed (S16/S18): unknown open orders or positions, position drift against the broker, three loop errors
  in a row, three entry orders in a row refused by the broker (S74), an event calendar that has run out, or three
  daily stops in ten sessions all stop new entries.
  Nothing here ever adds risk: protection stays at the broker and exits keep being managed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import numpy as np

from kaibot.broker.alpaca import CANCEL_WAIT_S
from kaibot.broker.protocol import Broker, OrderRequest
from kaibot.broker.shadow_costs import QuoteCtx, sell_fees, shadow_fill_price
from kaibot.engine import (
    shadow,  # S14 shadow strategies: never trade, never raise
    tickercheck,
)
from kaibot.engine.config import ROOT
from kaibot.engine.dividends import match_payment, open_accruals, receivable
from kaibot.engine.events import from_fomc_dates, in_blackout
from kaibot.engine.execution import flatten_at_close
from kaibot.engine.journal import Journal, canon
from kaibot.engine.ledger import (
    CORPORATE_ACTIVITIES,
    LEDGER_KINDS,
    Action,
    Ledger,
    actions_from_activities,
    price_key,
)
from kaibot.engine.market import LiveMarket, session_minutes
from kaibot.engine.reconcile import ACCEPT_COMMAND, Difference, Reconciler, Reconciliation
from kaibot.engine.roomflow import RoomFlow
from kaibot.engine.userconfig import ConfigError, Rules, defaults, tighten
from kaibot.evaluation import gate_report
from kaibot.replay.sim import half_spread, sell_px
from kaibot.risk.gate import (
    Proposal,
    RiskPolicy,
    Side,
    Snapshot,
    book_run_ended,
    drawdown_pct,
    evaluate,
    intraday_equity,
    run_drawdown_reached,
)
from kaibot.strategies import registry
from kaibot.strategies.base import CloseOut, Context, Event, Holding, MoveStop, Strategy, due, new_scan, note_scan

__all__ = ["Engine", "FlattenIncomplete", "new_scan", "note_scan"]  # the scan counters live in strategies/base.py

log = logging.getLogger("kaibot.engine")

FIRST_ENTRY_M = 5  # 09:35
PAUSE_AFTER_LOSSES, PAUSE_MINUTES = 3, 60
MAX_ERROR_STREAK = 3  # consecutive failed loop iterations before the kill switch trips for the session
STOPS_TO_PAUSE, STOPS_WINDOW_DAYS = 3, 14  # 3 daily stops within ~10 sessions pause the run (PRD v2)
LEVEL_NAMES = {"conservative": "Steady", "balanced": "Balanced", "aggressive": "Hunter"}
# S74 (#114). A 403 or 422 on an entry order means the venue refused it: there is no order. One refusal is about one
# candidate (the stop is no longer below the market, the symbol cannot be traded right now). Three in a row with no
# accepted order in between, the same "repeated" as MAX_ERROR_STREAK, is about the account or the order format.
BROKER_REJECT_STATUS = (403, 422)
MAX_REJECT_STREAK = 3
# S80 (#120, decision 15 sections 4 and 6): the close-minus-5 flatten and the daily stop sell intraday sleeves only;
# multi-day sleeves keep their protective stops. Run drawdown, a new run and "Flatten and stop" sell everything.
INTRADAY_ONLY = ("time", "daily_stop")
FLATTEN_TRIES = 2  # a close the venue refused is tried once more inside the same flatten; the loop does the rest
# S74 review (#142): one time limit for a whole flatten, retries included, well inside the watchdog's 90 s heartbeat
# staleness window (watchdog.STALE_S). A close is only started while CLOSE_MAX_S is left: the adapter's own
# bounded cancel wait (CANCEL_WAIT_S) plus its reads. What is left when the budget runs out is reported and retried
# by the next loop pass, never dropped. The heartbeat is written between positions (Engine.on_progress).
FLATTEN_BUDGET_S = 30.0
CLOSE_MAX_S = CANCEL_WAIT_S + 3.0
CANCEL_UNCONFIRMED = 409  # the adapter's "cancel not confirmed in time": already waited, so not retried at once
# S77: the ledger's cash change may differ from the broker's by fees the engine does not model; above this it says so
CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT = 1.0, 0.05
RECHECK_MINUTES = 15  # S78: a blocking reconciliation difference is checked again this often
BREACH_CONFIRM_S = 60.0  # #211: a book breach must hold on two checks at least this far apart


class FlattenIncomplete(RuntimeError):
    """flatten_all tried every position and some are still open. `left` is {symbol: why}; the message names the
    symbols first, so they survive wherever the text is cut (heartbeat, engine_error event, halt reason)."""

    def __init__(self, reason: str, left: dict[str, str]):
        self.reason, self.left = reason, dict(left)
        why = "; ".join(f"{s}: {w}" for s, w in left.items())
        super().__init__(f"flatten ({reason}) incomplete, still open: {', '.join(left)} ({why})")


def _fingerprint() -> str:
    try:
        return gate_report.fingerprint(ROOT, gate_report.Bar.load(ROOT).cfg["fingerprint"])
    except Exception:
        log.exception("strategy fingerprint unavailable")
        return "unknown"


def code_sha() -> str:
    import os

    if os.environ.get("KAIBOT_CODE_SHA"):
        return os.environ["KAIBOT_CODE_SHA"]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


@dataclass
class Pos:
    trade_id: int
    symbol: str
    qty: int
    coid: str
    order_id: str
    ref: float
    risk_ps: float
    atr: float
    sigma: float
    stop: float
    target: float
    status: str = "pending"  # pending -> open -> exiting -> closed
    fill: float | None = None
    shadow_entry: float | None = None
    stop_order_id: str | None = None
    target_order_id: str | None = None
    exit_order_id: str | None = None
    exit_reason: str | None = None
    armed: bool = False
    hi_close: float = -1e18
    db_order_id: int | None = None
    entry_ts: str | None = None
    carried: bool = False  # left over from an earlier session
    strategy: str = registry.DEFAULT_LIVE[0]

    def features(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("trade_id", "symbol", "qty")}

    @property
    def key(self) -> tuple[str, str]:
        return self.strategy, self.symbol


class Positions:
    """The engine's open positions, keyed by (strategy, symbol) (S77): two rules can hold the same stock.

    It still answers by symbol where a question is about the stock, not the rule: `"AAPL" in pos` (any rule holds
    it), `pos["AAPL"]` (the one position in it; ambiguous if two rules hold it), iteration yields symbols and
    `items()` (symbol, position) pairs, in the order the positions were opened."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], Pos] = {}

    def add(self, p: Pos) -> None:
        self._d[p.key] = p

    def pop(self, key, default=None):
        if isinstance(key, Pos):
            key = key.key
        if isinstance(key, str):
            key = next((k for k in self._d if k[1] == key), None)
        return self._d.pop(key, default) if key is not None else default

    def sleeves(self):
        return self._d.items()

    def on(self, symbol: str) -> list[Pos]:
        return [p for (_, s), p in self._d.items() if s == symbol]

    def __getitem__(self, key) -> Pos:
        if isinstance(key, tuple):
            return self._d[key]
        found = self.on(key)
        if len(found) != 1:
            raise KeyError(key if not found else f"{key} is held by {len(found)} rules: ask by (strategy, symbol)")
        return found[0]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key) -> bool:
        return key in self._d if isinstance(key, tuple) else any(s == key for _, s in self._d)

    def __iter__(self):
        return (s for _, s in self._d)

    def items(self):
        return [(s, p) for (_, s), p in self._d.items()]

    def values(self):
        return list(self._d.values())

    def __len__(self) -> int:
        return len(self._d)

    def __bool__(self) -> bool:
        return bool(self._d)

    def __eq__(self, other) -> bool:
        if isinstance(other, Positions):
            return self._d == other._d
        if isinstance(other, dict):
            return dict(self.items()) == other and len(other) == len(self._d)
        return NotImplemented

    def __repr__(self) -> str:
        return f"Positions({ {f'{k[0]}|{k[1]}': p.status for k, p in self._d.items()} })"


@dataclass
class Day:
    run_id: int
    day: date
    open_utc: datetime
    n_min: int
    start_equity: float
    realized_venue: float = 0.0
    realized_shadow: float = 0.0
    realized_reported: float = 0.0
    entries: int = 0
    trades_closed: int = 0
    losses_in_row: int = 0
    pause_until: int = -1
    halted: bool = False
    flattened: bool = False
    finalized: bool = False
    last_m: int = -1
    done_m: int = -1  # S74: the last minute whose work completed (last_m: the last one started). See Engine.tick
    # S74: {strategy: the last minute whose entry decision finished or acted}; a retry never decides it again
    decided: dict = field(default_factory=dict)
    rejects: int = 0  # S74: entry orders the broker refused in a row; an accepted order resets it
    events: list = field(default_factory=list)
    rules: Rules | None = None  # M5: the owner's config for this session (None before the open)
    refused: str | None = None  # M5: entries refused for this session only (invalid config); next day re-checks
    custom_ok: frozenset = frozenset()  # S68: custom tickers that passed today's point-in-time check
    scan: dict = field(default_factory=lambda: new_scan())  # dashboard-only counters, never read by decisions
    # S77: what moves today's equity besides realised and unrealised trade P&L: dividends booked today, and for a
    # position held over the last close and closed today, minus its gain to that close (already in start_equity)
    adjust: float = 0.0

    @property
    def flatten_m(self) -> int:
        return self.n_min - 5  # 15:55 on a normal day, 12:55 on an early close

    @property
    def last_entry_m(self) -> int:
        return self.n_min - 15  # 15:45


def gate_peak(peak: float | None, eq: float, book_pnl: float) -> float | None:
    """#206 (QA-Ops second key on #207): the run peak the gate's `run_drawdown_pct` check gets. `_risk_halts`
    measures the intraday run end on the run's equity without the multi-day book's P&L (`intraday_equity`) against
    the intraday peak; the gate gets the run's whole equity (its sizing needs it), so the peak is scaled such that
    the gate's drawdown is exactly the intraday one: a book in profit cannot hide an intraday drawdown and a book in
    loss (it has its own end) does not block intraday entries. Without multi-day P&L: the peak, unchanged."""
    if not book_pnl or peak is None:
        return peak
    eq_i = intraday_equity(eq, book_pnl)
    if not (eq_i > 0 and eq > 0):  # (#211: a non-positive run equity keeps the peak as it is)
        return peak
    return eq * peak / eq_i


class Engine:
    def __init__(
        self,
        broker: Broker,
        market: LiveMarket,
        store,
        policies: dict[str, RiskPolicy],
        fomc: set,
        process: str = "engine",
        now=lambda: datetime.now(UTC),
        calendar_until: date | None = None,
        events: dict | None = None,
        strategies: dict[str, list[str]] | None = None,
        room=None,
        pack_fn=None,
        rules_extra=None,
        journal: Journal | None = None,
        corporate_actions=None,
        ex_dividends=None,
        daily=None,
    ):
        self.broker, self.market, self.store = broker, market, store
        self.policies, self.fomc, self.process, self.now = policies, fomc, process, now
        self.calendar_until = calendar_until  # last date the event calendar covers; None = not checked
        self.events = events if events is not None else from_fomc_dates(fomc)  # {day: [(event, minute)]}
        self.strategies = strategies or {}  # {level: [live strategies]}; missing level -> registry.DEFAULT_LIVE
        for names in self.strategies.values():  # #161: a multi-day rule never loads on the live path
            registry.refuse_multi_day(names)
        # S76: which registered shadow strategies run (None = all of them; the research command picks one)
        self.shadow_only: tuple[str, ...] | None = None
        # S34: the agent room (None = rules-only). It can only veto or halve a candidate the rules approved.
        self.roomflow = RoomFlow(self, room, pack_fn) if room is not None else None
        self.rules_extra = rules_extra  # extra rules-only veto (S37 pump risk): (engine, symbol, m) -> str | None
        self.kill: str | None = None  # set by a fail-closed halt; blocks entries until the next session
        # Issue #81: the cross-machine lease (engine/lease.py), set by __main__. None = not enforced (tests,
        # replays). When set, entries need a lease that is provably ours right now.
        self.lease = None
        self.error_streak = 0
        self._drift_seen: dict = {}
        self.run: dict | None = None
        self.day: Day | None = None
        self.pos = Positions()
        self.experiments: list[dict] = []  # #133: set at each session start (_experiments)
        # S77: the local journal (the store's own when it journals, else an in-memory one) and the ledger folded
        # from it: positions per (strategy, symbol), cash, the mark-to-market close of every session.
        self.journal = journal or getattr(store, "journal", None) or Journal()
        self.ledger = Ledger.fold(self.journal.entries(kinds=LEDGER_KINDS))
        # S77: corporate actions for a session: research passes the daily store's table (day -> [Action]); None
        # reads the broker's non-trade activities (Alpaca: dividends DIV*, splits SSP) when a position is held
        self.corporate_actions = corporate_actions
        # S78 (#118): live, dividends are accrued at the ex-date (engine/dividends.ExDateDividends: (day, symbols)
        # -> ex-dividends) and Alpaca's later payment is matched to the accrual; None: booked when paid
        self.ex_dividends = ex_dividends
        # S78: the ledger against the broker (engine/reconcile.py); accepted offsets fold from the journal
        self.recon = Reconciler(lambda: self._recon_inputs(), self.journal)
        self._finalize_checked: date | None = None
        self.account_clean = True
        self.code = code_sha()
        self.fingerprint = _fingerprint()  # S38: strategy-code hash; trades count toward the gate only on frozen code
        self.peak: float | None = None
        self.on_progress = lambda status: None
        self.monotonic = time.monotonic  # S74: the flatten's time budget (a fake clock in tests)
        self._flatten_slow: set[str] = set()  # S74: symbols the last flatten could not close; tried last next time
        # S80 (#120): sales of multi-day sleeves sent by a full flatten: venue order id -> {run_id, keys, reason}
        self._md_exits: dict[str, dict] = {}
        self._bpos: dict = {}  # the broker's positions as last read (drift check): a price for multi-day sleeves
        # #206: the live daily path (engine/live_daily.LiveDaily: multi-day rules through engine/daily.py's decision
        # core), built by `daily(self)`. None (the default, and always with KAIBOT_MULTIDAY off): the engine is the
        # intraday engine it was, line for line.
        if daily is not None and not registry.multi_day_enabled():
            raise registry.RefusedStrategy(f"the live daily path needs {registry.MULTIDAY_FLAG}=on")
        self.daily = daily(self) if daily is not None else None
        self._closed_cache: tuple | None = None
        self._breach: tuple[int, datetime] | None = None  # #211: (run, first check) of an unconfirmed book breach

    # ================= lifecycle =================
    def startup(self) -> None:
        self.run = self.store.active_run()
        self._restore_positions()
        self._attach_ledger()
        try:
            self._adopt_orphans()
        except Exception:  # adoption is best effort: if it fails the order stays unknown and blocks entries
            log.exception("order adoption failed")
        self._daily_step("startup", lambda: self.daily.startup())  # #206: its orders recovered, its fills booked
        self.check_account()
        self._startup_reconcile("startup")
        self.store.event(
            "engine_start",
            {"code_sha": self.code, "fingerprint": self.fingerprint, "run_id": self.run and self.run["id"],
             "positions": list(self.pos)},
            self._rid(),
        )

    def check_account(self) -> None:
        """Positions or open orders the engine doesn't know about block all entries (fail closed).

        An open order counts as known if it is on a symbol the engine holds (its bracket legs and exits). An
        unknown one is e.g. a bracket accepted just before a crash, before its row was written: adopting it
        blindly could double the position, so we stop instead and say so. (One whose client_order_id is exactly
        ours for an approved signal has already been adopted by `_adopt_orphans` at startup.)"""
        broker_pos = self.broker.positions()
        known = set(self.pos) | {sym for _, sym in self._md_sleeves()}  # S80: the ledger's multi-day sleeves too
        if self.daily is not None:  # #206: a closing-auction order of the daily path is the engine's own
            known |= self.daily.working_symbols()
        foreign = sorted(set(broker_pos) - known)
        orders = sorted({o["symbol"] for o in self.broker.open_orders() if o["symbol"] not in known})
        self.account_clean = not foreign and not orders
        if not self.account_clean:
            msg = {"foreign_positions": foreign, "unknown_open_orders": orders,
                   "action": "entries blocked until the paper account is clean"}
            log.warning("account not clean: %s", msg)
            self.store.event("account_not_clean", msg, self._rid())

    def _recon_inputs(self) -> dict:
        """What `reconcile.compare` needs: the ledger's shares per settled symbol against the broker's, and the cash
        change since the run began against the ledger's, net of dividends accrued and not yet paid. A symbol with
        an entry or exit under way is left to the per-minute drift check until it settles."""
        b = self.ledger.book(self._rid()) if self.run else None
        if b is None:
            return {"ledger_qty": {}, "broker_positions": {}}
        unsettled = {p.symbol for p in self.pos.values() if p.status != "open" or p.exit_order_id}
        acct = self.broker.account()
        bpos = {s: p for s, p in self.broker.positions().items() if s not in unsettled}
        cash = acct.get("cash")
        return {"ledger_qty": {s: q for s, q in b.qty_by_symbol().items() if q and s not in unsettled},
                "broker_positions": bpos, "ledger_cash": b.cash, "ledger_cash0": b.amount,
                "broker_cash": float(cash) if cash is not None and b.broker_cash0 is not None else None,
                "broker_cash0": b.broker_cash0,
                "receivable": receivable(self.journal.entries(kinds=("div_accrual", "div_paid"))),
                "equity": b.amount}

    def _startup_reconcile(self, when: str = "startup") -> None:
        """S78 (#118, audit D8; QA-Ops review B1): the ledger against the broker, shares and cash, at start-up and
        at every session start. Its own flag (`recon`), not `account_clean` (which the session start recomputes):
        entries stay blocked until a check succeeds or the owner accepts the difference with a reason (command
        `accept_reconcile`, or `python -m kaibot.engine.reconcile --accept`). A failed read counts as a difference
        (fail closed). Exits, stops and flattens never look at it."""
        try:
            self.recon.check(when)
        except Exception as e:
            log.exception("reconciliation read failed")
            self.recon.last = Reconciliation([Difference("unavailable", None, {"error": f"{type(e).__name__}: "
                                                                                       f"{e}"[:160]})])
            self.recon.checked = True
        if self.recon.last.diffs:
            self.store.event("reconcile_blocked", {"when": when, **self.recon.last.summary(),
                                                   "action": "entries blocked until the book matches the broker "
                                                             "or the owner accepts the difference"}, self._rid())

    def _recheck_reconcile(self, m: int | None = None, force: bool = False) -> None:
        """While entries are blocked by a difference: check again every RECHECK_MINUTES (a late activity heals)."""
        if self.recon.last.diffs and (force or (m is not None and m % RECHECK_MINUTES == 0)):
            self._startup_reconcile("recheck")

    def _reconciled(self) -> bool:
        return not self.recon.last.diffs

    # ================= fail-closed halts =================
    def _halt_entries(self, kind: str, reason: str, detail: dict | None = None) -> None:
        if self.kill:
            return
        self.kill = kind
        log.error("entries halted: %s (%s)", kind, reason)
        self.store.insert(
            "halt",
            {"run_id": self._rid(), "kind": kind, "reason": reason,
             "session": str(self.day.day) if self.day else None},
            returning=False,
        )
        self.store.event("halt", {"kind": kind, "reason": reason, **(detail or {})}, self._rid())

    def on_tick_error(self, err: Exception) -> None:
        """Called by the main loop when an iteration raises. Repeated failures trip the kill switch."""
        self.error_streak += 1
        if self.error_streak >= MAX_ERROR_STREAK:
            self._halt_entries("engine_errors", f"{self.error_streak} loop errors in a row: {str(err)[:120]}")

    def on_tick_ok(self) -> None:
        self.error_streak = 0

    def lease_ok(self) -> bool:
        """Issue #81: no lease object means the lease is not enforced; otherwise it must be verified and fresh."""
        return self.lease is None or bool(self.lease.ok)

    def _check_drift(self) -> None:
        """S18: the engine's book must match the broker's. Any difference stops new entries."""
        broker_pos = self.broker.positions()
        self._bpos = broker_pos
        diffs = {}
        # S77: per symbol, the sum over the rules holding it; a symbol with an entry or an exit under way is
        # compared once that has settled (as a single position was before)
        unsettled = {p.symbol for p in self.pos.values() if p.status != "open" or p.exit_order_id}
        unsettled |= {k[1] for x in self._md_exits.values() for k, _ in x["keys"]}
        if self.daily is not None:  # #206: a symbol with a daily-path order is compared once it has settled
            unsettled |= self.daily.working_symbols()
        mine: dict[str, float] = {}
        for s, p in self.pos.items():
            if s not in unsettled:
                mine[s] = mine.get(s, 0) + p.qty
        for (_, s), sl in self._md_sleeves().items():  # S80: multi-day sleeves are the engine's too
            if s not in unsettled:
                mine[s] = mine.get(s, 0) + sl.qty
        for s, qty in mine.items():
            q = broker_pos.get(s)
            bq = float(q["qty"]) if q else 0
            if abs(bq - qty) > 1e-9:
                diffs[s] = {"engine": qty, "broker": bq}
        for s in set(broker_pos) - set(self.pos) - set(mine) - unsettled:
            diffs[s] = {"engine": 0, "broker": int(float(broker_pos[s]["qty"]))}
        # A bracket leg can fill between our order sync and this read; that heals on the next minute's sync.
        # Only a difference seen on two consecutive minutes is drift.
        persistent = {s: v for s, v in diffs.items() if self._drift_seen.get(s) == v}
        self._drift_seen = diffs
        if persistent:
            self._halt_entries("drift", "positions differ from the broker", {"diffs": persistent})

    def _restore_positions(self) -> None:
        self.pos = Positions()
        if not self.run:
            return
        for t in self.store.open_trades(self.run["id"]):
            f = t.get("features") or {}
            known = {k: f[k] for k in Pos.__dataclass_fields__ if k in f}
            p = Pos(trade_id=t["id"], symbol=t["symbol"], qty=int(float(t["qty"])), **known)
            self.pos.add(p)

    # ================= ledger (S77) =================
    def _book(self, kind: str, body: dict) -> None:
        """Journal first, then fold: the ledger is always what the journal says."""
        body = canon(body)
        if kind == "fill":
            self._md_realized(body)
        self.journal.append(kind, body)
        self.ledger.apply(kind, body)

    # ================= the live daily path (#206) =================
    def _daily_step(self, step: str, call) -> None:
        """One step of the daily path (engine/live_daily.py), only when it is wired. A failure is logged and an
        event, never a stop of the intraday engine: the daily path then sends nothing (fail closed), and what it has
        journalled is picked up at the next call."""
        if self.daily is None:
            return
        try:
            call()
        except Exception as e:
            log.exception("daily path: %s failed", step)
            self.store.event("multi_day_error", {"step": step, "error": f"{type(e).__name__}: {e}"[:200]},
                             self._rid())

    def _md_symbols(self) -> set[str]:
        """#206: symbols the multi-day book holds or has a working order of the daily path in (empty without the
        daily path: the intraday path is unchanged)."""
        if self.daily is None:
            return set()
        return {sym for _, sym in self._md_sleeves()} | self.daily.working_symbols()

    def _daily_status(self, st: dict) -> dict:
        """#206: the heartbeat's `multi_day` block (`LiveDaily.status`), e.g. "no decision today: daily store not
        refreshed". Absent without the daily path."""
        if self.daily is not None:
            try:
                st["multi_day"] = self.daily.status(self.now().astimezone(_et()).date())
            except Exception:
                log.exception("daily path status unavailable")
        return st

    def _daily_after_close(self, now: datetime) -> None:
        """After a session's close: the rules decide for the next session (once the daily store holds the close)."""
        if self.run is None or self.run.get("status") not in ("running", "paused"):
            return
        key = (now.astimezone(_et()).date(), now.astimezone(_et()).hour)
        if self._closed_cache is None or self._closed_cache[0] != key:
            self._closed_cache = (key, self._last_closed_session(now))
        closed = self._closed_cache[1]
        if closed is not None:
            self.daily.after_close(closed)

    def _book_pnl(self, live: bool = True) -> float:
        """#206: the multi-day book's P&L since the run began (realised, dividends, open sleeves at the last price,
        or at the last close mark with `live=False`). 0 without the daily path: nothing changes then."""
        if self.daily is None:
            return 0.0
        rid = self._rid() or (self.day.run_id if self.day else None)
        b = self.ledger.book(rid) if rid is not None else None
        if b is None:
            return 0.0
        from kaibot.engine.failsafe import multi_day_names

        names = set(multi_day_names())
        prices = {}
        if live:
            for k in b.sleeves:
                if k[0] in names:
                    px = self._md_price(k[1])
                    if px:
                        prices[k] = px
        return b.strategy_pnl(names, prices)

    def _book_peak(self, rid: int) -> float | None:
        peak = None
        for _, _, _, e in self.journal.entries(kinds=("md_book_peak",)):
            if e.get("run_id") == rid:
                peak = float(e["peak"])
        return peak

    def _book_end_check(self, book_pnl: float) -> None:
        """#206 (stakeholder decision 13 Nov 2026, decision 15 section 11): the multi-day book's own run end.
        Book equity = the run's pot + the book's P&L since the run began; its peak is journalled (`md_book_peak`),
        so a restart measures from the same peak. At `BookLimits.run_drawdown_end_pct` below the peak (the level's
        book end tightened by the rule's retirement trigger: Careful 13%, Balanced 21%, Hunter 27%) `_book_end`
        runs once. While the book is closed and still holds shares, the sale is retried at every tick."""
        if self.daily is None or self.run is None:
            return
        rid = self._rid()
        b = self.ledger.book(rid)
        if b is None:
            return
        ended = self.daily.account.book_ended()
        if ended:
            if self._md_sleeves():  # closed: whatever is still held is sold (a sale under way is not repeated)
                self._flatten_md("book_drawdown", {}, self.monotonic())
            return
        rules = self.daily.rules
        if not rules:
            return
        end = min(r.builder.limits.run_drawdown_end_pct for r in rules)
        equity = float(b.amount) + book_pnl
        peak = self._book_peak(rid)
        if peak is None or equity > peak + 0.005:
            peak = max(peak or float(b.amount), equity)
            self.journal.append("md_book_peak", {"run_id": rid, "peak": round(peak, 2), "ts": self.now().isoformat()})
        if not book_run_ended(peak, equity, replace(rules[0].builder.limits, run_drawdown_end_pct=end)):
            self._breach = None
            return
        # #211 (QA-Ops review of #207): a retirement is durable, so one bad print must never cause it. The breach
        # must hold on two checks at least BREACH_CONFIRM_S apart (two engine ticks, i.e. two minute bars) before
        # the book ends; a check in between that is not a breach starts over. A restart starts over too.
        now = self.now()
        if self._breach is None or self._breach[0] != rid:
            self._breach = (rid, now)
            self.journal.append("md_book_breach", {"run_id": rid, "peak": round(peak, 2), "equity": round(equity, 2),
                                                   "drawdown_pct": round(drawdown_pct(peak, equity), 3),
                                                   "end_pct": end, "ts": now.isoformat(),
                                                   "action": "confirmed or dropped at the next check"})
            return
        if (now - self._breach[1]).total_seconds() < BREACH_CONFIRM_S:
            return
        self._breach = None
        self._book_end(rid, peak, equity, end, [r.name for r in rules])

    def _book_end(self, rid: int, peak: float, equity: float, end: float, rules: list[str]) -> None:
        """The multi-day book reached its run end. Exactly this, in this order:
        1. `md_book_end` in the journal (from now on the daily path decides and sends nothing in this run);
        2. a `book_drawdown` halt row and a `multi_day_book_ended` event;
        3. every working order of the daily path is cancelled (closing-auction buys and sales);
        4. every multi-day sleeve is sold at market now (`_flatten_md`, the run-drawdown flatten's sale), retried at
           each tick until none is left.
        The intraday rules, their run end (4/8/12%) and the run itself go on; nothing intraday is sold or blocked.
        5. each rule of the book is RETIRED (`LiveDaily.retire`: journal `md_retired` and a `multi_day_retired` halt
           row): it trades in no later run either, until the stakeholder re-enables it (KAIBOT_REENABLE)."""
        dd = drawdown_pct(peak, equity)
        body = {"run_id": rid, "rules": rules, "peak": round(peak, 2), "equity": round(equity, 2),
                "drawdown_pct": round(dd, 3), "end_pct": end, "ts": self.now().isoformat()}
        self.journal.append("md_book_end", body)
        reason = (f"multi-day book drawdown {dd:.1f}% reached its end of {end:g}% (peak {peak:,.2f}, now "
                  f"{equity:,.2f}): no new multi-day trades in this run, the book is sold at market")
        log.error("%s", reason)
        self.store.insert("halt", {"run_id": rid, "kind": "book_drawdown", "reason": reason,
                                   "session": str(self.day.day) if self.day else None}, returning=False)
        self.store.event("multi_day_book_ended", {**body, "action": reason}, rid)
        for name in rules:  # Product call on #207: retired across runs and restarts until re-enabled explicitly
            self.daily.retire(name, run_id=rid, session=str(self.day.day) if self.day else None, drawdown_pct=dd,
                              end_pct=end, peak=peak, equity=equity)
        try:
            self.daily.sync(force=True)  # a stop that fired at the open is booked first: never sold twice
            self.daily.close_book()
        except Exception:
            log.exception("book end: cancelling the daily path's orders failed (the sale goes on)")
        failed: dict = {}
        self._flatten_md("book_drawdown", failed, self.monotonic())
        if failed:
            log.warning("book end: not sold yet %s (retried at the next tick)", list(failed))

    # ================= multi-day sleeves (S80, #120) =================
    def _md_sleeves(self) -> dict:
        """The run's multi-day sleeves that are not a `Pos` of this engine: the ledger's (strategy, symbol) books of
        rules that hold across sessions (their fills come from the order manager's daily path). Empty for an
        intraday-only book, so nothing in the intraday path changes while no multi-day rule holds anything."""
        rid = self._rid() or (self.day.run_id if self.day else None)
        b = self.ledger.book(rid) if rid is not None else None
        if b is None:
            return {}
        return {k: sl for k, sl in b.sleeves.items()
                if sl.qty > 1e-9 and k not in self.pos and not flatten_at_close(_holding(k[0]))}

    def _md_price(self, symbol: str) -> float | None:
        """The last price of a multi-day holding: the minute bars if the symbol is in the live universe, else the
        broker's own `current_price` (Alpaca marks positions)."""
        px = self.market.last_close(symbol)
        if px:
            return float(px)
        cp = (self._bpos.get(symbol) or {}).get("current_price")
        return float(cp) if cp else None

    def _md_realized(self, body: dict) -> None:
        """A sale of a multi-day sleeve: its result against cost is today's realised P&L (as an intraday exit's is),
        less the gain up to the last close mark, which start_equity already holds (audit B1 item 4)."""
        d = self.day
        key = (body.get("strategy"), body.get("symbol"))
        if d is None or body.get("side") != "sell" or key in self.pos or flatten_at_close(_holding(key[0])):
            return
        b = self.ledger.book(body.get("run_id"))
        sl = b.sleeves.get(key) if b is not None else None
        if sl is None:
            return
        qty, px, fees = float(body["qty"]), float(body["px"]), float(body.get("fees") or 0.0)
        pnl = qty * px - fees - sl.avg_cost * qty
        d.realized_venue += pnl
        d.realized_shadow += pnl
        d.realized_reported += pnl
        if sl.mark is not None:
            d.adjust -= (sl.mark - sl.avg_cost) * qty

    def entry_block(self) -> str | None:
        """S80: why no new position may be opened now (None: it may), for the multi-day target path as for the
        intraday entries: no running run, the account not clean, a fail-closed halt, the lease, the daily stop,
        an invalid owner config. Sales and protective stops are never blocked."""
        d = self.day
        if self.run is None or self.run.get("status") != "running":
            return "no running run"
        if not self.account_clean:
            return "account not clean"
        if not self._reconciled():
            return "ledger and broker differ (reconciliation)"
        if self.kill:
            return f"halted: {self.kill}"
        if not self.lease_ok():
            return "lease unverified"
        if d is not None and d.halted:
            return "daily stop"
        if d is not None and d.refused:
            return f"config refused: {d.refused}"
        return None

    def _md_stops(self) -> dict:
        """(strategy, symbol) -> the price of the multi-day protective stop resting at the broker now."""
        from kaibot.engine.failsafe import _md_stop, multi_day_names

        names = multi_day_names()
        out: dict = {}
        try:
            orders = self.broker.open_orders()
        except Exception:
            log.exception("open orders unreadable (stop state of the multi-day sleeves)")
            return out
        for o in orders:
            k = _md_stop(o, names)
            if k is not None and o.get("stop_price") is not None:
                out[k] = float(o["stop_price"])
        return out

    def _flatten_md(self, reason: str, failed: dict, t0: float) -> None:
        """A full flatten (run drawdown, new run, "Flatten and stop") also sells every multi-day sleeve: a symbol
        held by no `Pos` is closed at the broker (its stops are cancelled first); in a symbol a `Pos` also holds,
        the sleeve's own stops are cancelled and its shares sold at market. Booked when the venue reports the
        fill (`_sync_md_exits`)."""
        from kaibot.engine.failsafe import clean, parse_coid

        pending = {k for x in self._md_exits.values() for k, _ in x["keys"]}
        by_sym: dict[str, list] = {}
        for k, sl in self._md_sleeves().items():
            if k not in pending:
                by_sym.setdefault(k[1], []).append((k, sl.qty))
        rid = self._rid() or (self.day.run_id if self.day else None)
        for sym, keys in by_sym.items():
            if self.monotonic() - t0 > FLATTEN_BUDGET_S - CLOSE_MAX_S:
                failed.setdefault(sym, f"not tried: flatten time budget ({FLATTEN_BUDGET_S:g} s) used")
                continue
            self._flatten_beat(reason, sym, len(by_sym))
            try:
                if not self.pos.on(sym):
                    o = self._order("close_position", lambda sym=sym: self.broker.close_position(sym), symbol=sym,
                                    strategy=",".join(k[0] for k, _ in keys), qty=sum(q for _, q in keys),
                                    reason=reason)
                    if o:
                        self._md_exits[o["id"]] = {"run_id": rid, "keys": keys, "reason": reason}
                    continue
                for k, q in keys:
                    for oo in self.broker.open_orders():
                        pc = parse_coid(oo.get("client_order_id"))
                        if oo.get("symbol") == sym and oo.get("side") == "sell" and pc and pc[0] == clean(k[0]):
                            self._order("cancel", lambda oid=oo["id"]: self.broker.cancel_order(oid),
                                        order_id=oo["id"], symbol=sym, strategy=k[0])
                    coid = f"kbfl-{clean(k[0])[:40]}-{clean(sym)[:12]}-{self.now():%Y%m%d%H%M%S}"
                    o = self._order("sell", lambda sym=sym, q=q, coid=coid: self.broker.submit(
                        OrderRequest(sym, "sell", q, "market", "day", coid)), coid=coid, symbol=sym,
                        strategy=k[0], qty=q, reason=reason)
                    self._md_exits[o["id"]] = {"run_id": rid, "keys": [(k, q)], "reason": reason}
                failed.pop(sym, None)
            except Exception as e:
                log.warning("flatten (%s): selling the multi-day sleeves in %s failed: %s", reason, sym, e)
                failed[sym] = e

    def _sync_md_exits(self) -> None:
        for oid, x in list(self._md_exits.items()):
            o = self.broker.order(oid)
            if o["status"] == "filled":
                px, ts = float(o["filled_avg_price"]), o.get("filled_at")
                for (strategy, sym), q in x["keys"]:
                    if x["run_id"] is not None:
                        self._book("fill", {"run_id": x["run_id"], "strategy": strategy, "symbol": sym,
                                            "side": "sell", "qty": q, "px": px, "fees": sell_fees(q, px),
                                            "coid": o.get("client_order_id"), "ts": ts, "reason": x["reason"]})
                keys = [f"{k[0]}|{k[1]}" for k, _ in x["keys"]]
                self.store.event("multi_day_sold", {"order_id": oid, "keys": keys, "px": px,
                                                    "reason": x["reason"]}, self._rid())
                self._md_exits.pop(oid)
            elif o["status"] in ("canceled", "expired", "rejected"):
                self._md_exits.pop(oid)  # not sold: the next full flatten tries again

    def _attach_ledger(self) -> None:
        """A run the ledger has not seen (it began before the ledger, or the journal is new) is imported from the
        store: its pot, its reported daily results so far (the old start equity, to the last bit), the cash its
        closed trades left and its open positions at their fills."""
        if not self.run or self.ledger.book(self.run["id"]) is not None:
            return
        rid, amount = self.run["id"], float(self.run["amount_usd"])
        days = self.store.select("daily_result", {"run_id": f"eq.{rid}"})
        last = max((str(r["session"]) for r in days), default="")
        late = [t for t in self.store.select("trade", {"run_id": f"eq.{rid}"})
                if t.get("exit_ts") and t.get("pnl_usd") is not None and str(_et_date(t["exit_ts"])) > last]
        cash = amount + sum(float(r["pnl_venue"]) for r in days) + sum(
            float((t.get("features") or {}).get("pnl_venue", t["pnl_usd"])) for t in late)
        self._book("run", {"run_id": rid, "amount": amount, "imported": True, "prior_cash": cash,
                           "prior_days": [min(float(r["pnl_venue"]), float(r["pnl_shadow"])) for r in days]})
        for p in self.pos.values():
            if p.status == "open" and p.fill is not None:
                self._book("position", {"run_id": rid, "strategy": p.strategy, "symbol": p.symbol, "qty": p.qty,
                                        "px": p.fill, "session": str(_et_date(p.entry_ts)) if p.entry_ts else None})

    def _order(self, action: str, call, **body):
        """Every order the engine sends, changes or cancels: an `intent` in the journal BEFORE the broker call
        (audit D5), its `ack` after. A crash in between leaves an intent without an answer, which says exactly
        which client order id to look up."""
        seq = self.journal.append("intent", canon({"action": action, "run_id": self._rid(), **body}))
        try:
            out = call()
        except Exception as e:
            self.journal.append("ack", {"intent": seq, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]})
            raise
        self.journal.append("ack", {"intent": seq, "ok": True,
                                    "order_id": out.get("id") if isinstance(out, dict) else None})
        return out

    def _basis(self, p: Pos) -> float:
        """The price today's unrealised P&L counts from: the last close mark for a position held over it (its
        gain up to that close is already in start_equity, audit B1 item 4), else the fill."""
        b = self.ledger.book(self._rid()) if self.run else self.ledger.book(self.day.run_id if self.day else None)
        s = b.sleeves.get(p.key) if b is not None else None
        return s.mark if s is not None and s.mark is not None else p.fill

    def _broker_cash(self) -> float | None:
        try:
            c = self.broker.account().get("cash")
            return float(c) if c is not None else None
        except Exception:
            log.exception("account read failed (cash baseline)")
            return None

    def _adopt_orphans(self) -> None:
        """S40: a crash between the broker accepting an entry and the engine writing its order/trade rows leaves an
        order the engine doesn't know. If its client_order_id is exactly the one this run gave an approved signal
        (kb-<run>-<signal>-1), it is ours beyond doubt: write the missing rows and manage it. Anything else still
        counts as unknown and blocks entries (check_account)."""
        if not self.run:
            return
        rid = self.run["id"]
        sigs = self.store.select("signal", {"run_id": f"eq.{rid}", "mode": "eq.live", "order": "id.desc",
                                            "limit": "50"})
        if not sigs:
            return
        ids = ",".join(str(r["id"]) for r in sigs)
        traded = {r["signal_id"] for r in self.store.select("trade", {"signal_id": f"in.({ids})"})}
        approved = {g["signal_id"]: g for g in self.store.select("gate_run", {"signal_id": f"in.({ids})"})
                    if g.get("approved")}
        rows = {r["signal_id"]: r for r in self.store.select("order", {"signal_id": f"in.({ids})"})}
        for sig in sigs:
            sid, s = sig["id"], sig["symbol"]
            if sid in traded or sid not in approved or (sig.get("strategy") or registry.DEFAULT_LIVE[0], s) in self.pos:
                continue
            coid = f"kb-{rid}-{sid}-1"
            o = self.broker.order_by_client_id(coid)
            if not o or o.get("symbol") != s or o.get("side") != "buy" or o.get("status") in (
                    "canceled", "expired", "rejected"):
                continue
            self._adopt(sig, o, coid, rows.get(sid))

    def _adopt(self, sig: dict, o: dict, coid: str, db_order: dict | None) -> None:
        f = sig.get("features") or {}
        ref, stop, target = float(sig["entry"]), float(sig["stop"]), sig.get("target")
        qty = int(float(o["qty"]))
        sigma = f.get("sigma_1m")
        if sigma is None and f.get("sigma") is not None:  # a rule that logs the daily sigma
            sigma = float(f["sigma"]) / np.sqrt(390)
        if db_order is None:
            db_order = self.store.insert("order", {
                "signal_id": sig["id"], "client_order_id": coid, "venue": "alpaca_paper",
                "legs": {"stop": round(stop, 2), "target": None if target is None else round(float(target), 2),
                         "qty": qty},
                "status": o.get("status", "new"), "submitted_at": sig.get("ts") or self.now()})
        p = Pos(trade_id=0, symbol=sig["symbol"], qty=qty, coid=coid, order_id=o["id"], ref=ref, risk_ps=ref - stop,
                atr=float(f.get("atr5") or 0), sigma=float(sigma or 0), stop=stop,
                target=float(target) if target is not None else 0.0, db_order_id=db_order["id"],
                entry_ts=_iso(sig.get("ts")) or self.now().isoformat(),
                strategy=sig.get("strategy") or registry.DEFAULT_LIVE[0])
        trade = self.store.insert("trade", {
            "run_id": self._rid(), "config_version": self._cfg_version(), "signal_id": sig["id"],
            "strategy": p.strategy, "symbol": p.symbol,
            "entry_ts": p.entry_ts, "qty": qty, "entry_px": ref, "stop_px": stop, "target_px": target,
            "risk_usd": p.risk_ps * qty, "features": {**f, **p.features(), "adopted": True}})
        p.trade_id = trade["id"]
        self.pos.add(p)  # pending: the next order sync books the fill and aligns the legs
        log.warning("adopted %s order %s (%s) left by a crash", p.symbol, coid, o.get("status"))
        self.store.event("order_adopted", {"symbol": p.symbol, "client_order_id": coid,
                                           "broker_status": o.get("status")}, self._rid())

    def _restore_session(self, d: Day) -> None:
        """S40: a restart mid-session rebuilds today's risk state from the store instead of starting from zero:
        entries placed, realized P&L, the loss streak / pause and a daily stop already hit all survive."""
        trades = self.store.select("trade", {"run_id": f"eq.{d.run_id}", "order": "id.desc", "limit": "200"})
        closed = []
        for t in trades:
            if _et_date(t.get("entry_ts")) == d.day:
                d.entries += 1
            if _et_date(t.get("exit_ts")) == d.day and t.get("pnl_usd") is not None:
                closed.append(t)
        for t in sorted(closed, key=lambda t: _iso(t["exit_ts"])):
            f, pnl = t.get("features") or {}, float(t["pnl_usd"])
            d.realized_venue += float(f.get("pnl_venue", pnl))
            d.realized_shadow += float(f.get("pnl_shadow", pnl))
            d.realized_reported += pnl
            d.trades_closed += 1
            d.losses_in_row = d.losses_in_row + 1 if pnl < 0 else 0
            pl, pm = self._pause_rule()
            if d.losses_in_row >= pl:
                exit_m = int((_parse_ts(t["exit_ts"]) - d.open_utc).total_seconds() // 60)
                d.pause_until, d.losses_in_row = exit_m + pm, 0
        halts = self.store.select("halt", {"run_id": f"eq.{d.run_id}", "kind": "eq.daily_stop",
                                           "session": f"eq.{d.day}"})
        d.halted = bool(halts)
        if d.entries or closed or d.halted:
            self.store.event("session_restored", {"day": str(d.day), "entries": d.entries, "closed": len(closed),
                                                  "realized": round(d.realized_reported, 2), "halted": d.halted},
                             self._rid())

    def _rid(self) -> int | None:
        return self.run["id"] if self.run else None

    # ================= commands =================
    def apply_commands(self) -> None:
        for c in self.store.pending_commands():
            kind, pl = c["kind"], c.get("payload") or {}
            try:
                if kind == "new_run":
                    if self.pos or self._md_sleeves() or self._md_exits:  # close the old run's positions first
                        self.flatten_all("manual")  # (multi-day sleeves too, S80); the command waits until flat
                        continue
                    self._new_run(float(pl["amount_usd"]), pl["level"])
                elif kind == "pause" and self.run:
                    self._set_run_status("paused")
                elif kind == "resume" and self.run:
                    self._set_run_status("running")
                elif kind == "flatten_and_stop" and self.run:
                    self.flatten_all("manual")
                    self._end_run("stopped", "stopped by user")
                elif kind == ACCEPT_COMMAND and self.run:  # S78: the owner accepts the open differences
                    self.recon.resolve(str(pl.get("reason") or ""), by="owner (command)")
                    self._startup_reconcile("accepted")
                elif kind == "set_level" and self.run and pl.get("level") in self.policies:
                    nxt = self._next_session_date()
                    self.store.insert(
                        "segment",
                        {
                            "run_id": self.run["id"],
                            "level": pl["level"],
                            "from_session": str(nxt),
                            "policy_hash": self._policy_hash(pl["level"]),
                        },
                        returning=False,
                    )
                self.store.mark_applied(c["id"])
                self.store.event(f"command_{kind}", pl, self._rid())
            except Exception as e:  # a bad command must never stop the engine
                log.exception("command %s failed", kind)
                self.store.mark_applied(c["id"], {**pl, "error": str(e)[:200]})

    def _new_run(self, amount: float, level: str) -> None:
        if level not in self.policies:
            raise ValueError(f"unknown level {level}")
        if self.run:
            self._end_run("ended", "replaced by a new run")
        run = self.store.insert(
            "run",
            {
                "amount_usd": amount,
                "status": "scheduled",
                "kind": "paper",
                "level": level,
                "label": f"Paper · {LEVEL_NAMES[level]} · ${amount:,.0f}",
                "peak_equity": amount,
            },
        )
        self.store.insert(
            "segment",
            {
                "run_id": run["id"],
                "level": level,
                "from_session": str(self.now().date()),
                "policy_hash": self._policy_hash(level),
            },
            returning=False,
        )
        self.run, self.peak = run, amount
        self._book("run", {"run_id": run["id"], "amount": amount, "broker_cash0": self._broker_cash()})
        self.day = None  # if a session is in progress, the new run starts trading at the next 5-minute check

    def _set_run_status(self, status: str, reason: str | None = None) -> None:
        vals = {"status": status}
        if reason:
            vals["end_reason"] = reason
        self.store.update("run", {"id": self.run["id"]}, vals)
        self.run = {**self.run, **vals}

    def _end_run(self, status: str, reason: str) -> None:
        self._set_run_status(status, reason)
        self.store.update("run", {"id": self.run["id"]}, {"ended_on": str(self.now().date())})
        self.run = None

    def _policy_hash(self, level: str) -> str:
        return hashlib.sha256(json.dumps(asdict(self.policies[level]), sort_keys=True).encode()).hexdigest()[:12]

    def _next_session_date(self) -> date:
        clk = self.broker.clock()
        return datetime.fromisoformat(clk["next_open"]).date()

    def level(self) -> str:
        return (self.run or {}).get("level") or "balanced"

    def policy(self) -> RiskPolicy:
        d = self.day
        if d is not None and d.rules is not None:
            return d.rules.policy  # M5: the level tightened by the owner's config, fixed at the session open
        return self.policies[self.level()]

    def _user_veto(self, r: Rules, s: str, ref: float, vix: float | None) -> str | None:
        """M5 tighten-only filters from the owner's config (the level's own gates run regardless)."""
        if vix is not None and np.isfinite(vix) and vix > r.vix_max:
            return f"VIX above your limit ({r.vix_max:g})"
        if ref < r.min_price_usd:
            return f"price below your minimum (${r.min_price_usd:g})"
        q = (self._quotes or {}).get(s) or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if 0 < bid <= ask and (ask - bid) / ((ask + bid) / 2) * 1e4 > r.max_spread_bp:
            return f"spread above your limit ({r.max_spread_bp:g} bp)"
        return None

    def _applied_config(self, run_id: int, open_utc: datetime) -> dict | None:
        """The owner's latest applied config created before this session's open (None = the level's defaults)."""
        rows = [c for c in self.store.select("config", {"run_id": f"eq.{run_id}", "status": "eq.applied"})
                if _parse_ts(c["created_at"]) < open_utc]
        return max(rows, key=lambda c: int(c["version"])) if rows else None

    def _load_rules(self, d: Day) -> None:
        """M5 S43: the owner's latest applied config created before this session's open, tightened onto the level.
        Invalid -> entries refused for the session (fail closed); management, stops and the flatten still run."""
        lv_pol, lv_strats = self.policies[self.level()], self.level_strategies()
        d.rules = defaults(lv_pol, lv_strats)
        try:
            c = self._applied_config(d.run_id, d.open_utc)
        except Exception as e:
            self._refuse_session(f"config unreadable: {type(e).__name__}")
            return
        if c is None:
            self.market.min_adv = d.rules.min_adv_usd
            return
        uni, themes = self._universe_and_themes()
        try:
            d.rules = tighten(lv_pol, lv_strats, c.get("body") or {}, version=int(c["version"]),
                              universe=uni, themes=themes)
        except (ConfigError, ValueError) as e:
            d.rules = Rules(int(c["version"]), lv_pol, tuple(lv_strats))  # keep the version for the record
            self._refuse_session(f"config v{c['version']} rejected: {e}")
            return
        self.market.min_adv = d.rules.min_adv_usd
        self.store.event("config_applied", {"day": str(d.day), "version": d.rules.version,
                                            "config_sha": d.rules.config_sha}, self._rid())

    def _custom_before_open(self, open_utc: datetime) -> dict[str, str]:
        """S68: the custom tickers of the config that will apply at `open_utc` (Only mode; none otherwise), so their
        history is loaded ahead of the open.
        Anything unreadable or invalid means none (the session open decides again and fails closed)."""
        try:
            c = self._applied_config(self.run["id"], open_utc)
            if c is None:
                return {}
            uni, themes = self._universe_and_themes()
            lv = self.level()
            return tighten(self.policies[lv], self.level_strategies(), c.get("body") or {},
                           version=int(c["version"]), universe=uni, themes=themes).custom_universe()
        except Exception:  # noqa: BLE001
            return {}

    def _vet_custom(self, d: Day) -> None:
        """S68: every custom ticker must pass the SAME safety filters as any other symbol, point-in-time, on the
        history this session actually loaded: tradable at the broker, enough history, min price, min $ volume.
        A ticker that fails (or cannot be checked) is not scanned today; the owner cannot override it. The spread
        and quote checks stay per entry."""
        custom = dict(self.market.custom)
        self.market.accept_custom(())
        d.custom_ok = frozenset()
        if not custom:
            return
        ok, refused = [], {}
        for s in sorted(custom):
            try:
                good, why = tickercheck.verdict(
                    s, self.broker.asset(s), tickercheck.stats(self.market.history.get(s), d.day),
                    min_adv=d.rules.min_adv_usd, min_price=d.rules.min_price_usd)
            except Exception as e:  # noqa: BLE001 - unknown is refused
                good, why = False, f"{s} could not be checked today ({type(e).__name__})."
            if good:
                ok.append(s)
            else:
                refused[s] = why
        d.custom_ok = frozenset(ok)
        self.market.accept_custom(ok)
        self.store.event("custom_tickers", {"day": str(d.day), "version": d.rules.version, "accepted": ok,
                                            "refused": refused}, self._rid())

    def _refuse_session(self, reason: str) -> None:
        """Entries off for this session only; stops, management and the 15:55 flatten keep running."""
        d = self.day
        d.refused = reason[:300]
        log.error("session refused: %s", reason)
        self.store.event("engine_error", {"kind": "config", "reason": d.refused}, self._rid())
        self.store.insert("halt", {"run_id": self._rid(), "kind": "config", "reason": d.refused,
                                   "session": str(d.day)}, returning=False)

    def _universe_and_themes(self) -> tuple[frozenset[str], dict[str, list[str]]]:
        if not hasattr(self, "_uni_cache"):
            import yaml

            u = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
            self._uni_cache = (frozenset(x for v in u["sectors"].values() for x in v),
                               {k: list(v["symbols"]) for k, v in (u.get("themes") or {}).items()})
        return self._uni_cache

    def _cfg_version(self) -> int:
        return self.day.rules.version if self.day and self.day.rules else 0

    def _pause_rule(self) -> tuple[int, int]:
        r = self.day.rules if self.day else None
        return (r.pause_losses, r.pause_minutes) if r else (PAUSE_AFTER_LOSSES, PAUSE_MINUTES)

    # ================= per tick =================
    def tick(self) -> dict:
        now = self.now()
        clk = self.broker.clock()
        if not clk["is_open"]:
            self._daily_step("sync", lambda: self.daily.sync())  # #206: the closing auction's fills, before the close
            if self.day and not self.day.finalized:
                self._sync_orders()
                self._finalize_day()
            elif self.day is None:
                self._finalize_missed(now)  # S77 (audit D8): a restart after the close still closes the day
            self._daily_step("after_close", lambda: self._daily_after_close(now))  # #206: decide for the next session
            self._maybe_prepare(clk, now)
            return self._daily_status({"state": "market_closed", "next_open": clk.get("next_open"),
                                       "account_clean": self.account_clean})
        if self.run is None and not self.pos and not self._md_exits:  # S80: a multi-day sale still to book
            return {"state": "idle", "note": "no paper run: start one from the dashboard",
                    "account_clean": self.account_clean}
        if self.run is not None and (self.day is None or self.day.day != now.astimezone(_et()).date()):
            self._start_day(now)
        d = self.day
        if d is None:
            return {"state": "idle", "account_clean": self.account_clean}
        self.market.update(now)
        m = self.market.last_complete_minute(now)
        if m < 0 or m == d.done_m:
            return self._status(m)  # this minute's work is complete: it is never run twice
        # S74 (#114, audit D6): `last_m` says the minute was started, `done_m` that it was finished. A step below
        # that raises leaves `done_m` behind, so the main loop's retry (about 10 s later) runs the minute again
        # instead of getting "already done" and resetting the error streak. Every failed attempt therefore counts
        # (on_tick_error) and a failure that recurs trips the kill switch. Each step is safe to run again, except
        # an entry decision that has acted: `decided` (set below and in _entries) keeps a retry from repeating it.
        d.last_m = m
        self._sync_orders()
        self._daily_step("sync", lambda: self.daily.sync())  # #206: the daily path's orders and fills
        self._recheck_reconcile(m)
        self._check_drift()
        if any(p.carried for p in self.pos.values()):
            for p in [p for p in self.pos.values() if p.carried and p.status in ("open", "pending")]:
                self._exit(p, "carried_over")
        eq, unreal = self.equity_now()
        self._risk_halts(m, eq)
        self._manage_stops(m)
        self._daily_step("send", lambda: self.daily.send(d.day))  # #206: closing-auction orders, 09:31-15:45 ET
        if m >= d.flatten_m and not d.flattened:
            self.flatten_all("time")  # S78: intraday sleeves only (holding policy per rule); see flatten_all
            d.flattened = True
        if self.roomflow is not None and self.roomflow.jobs:
            self._quotes = self.broker_quotes(sorted(self.roomflow.jobs))
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
            self.roomflow.collect(m)
        for strat in self.live_strategies():  # S76: every live strategy the session runs, in registry order
            if due(strat.schedule, Event.BAR_CLOSE, m, d.n_min) and d.decided.get(strat.name) != m \
                    and self._may_enter(m):
                self._entries(strat, m)
                d.decided[strat.name] = m
        self._close_outs(m)
        self._ledger(eq)
        shadow.on_minute(self, m)  # S14: shadow ORB / news continuation (signal rows only; exceptions contained)
        d.done_m = m
        return self._status(m)

    def _maybe_prepare(self, clk: dict, now: datetime) -> None:
        """Load history ahead of the open (the first load takes a few minutes; later ones are incremental)."""
        if self.run is None:
            return
        nxt = datetime.fromisoformat(clk["next_open"])
        if timedelta(0) < nxt - now <= timedelta(minutes=45) and self.market.day != nxt.date():
            cal = self.broker.calendar(str(nxt.date()))
            open_utc, _ = session_minutes(cal, nxt.date())
            self.market.set_custom(self._custom_before_open(open_utc))  # S68: backfill custom tickers before the open
            self.market.prepare_day(
                nxt.date(),
                open_utc,
                progress=lambda n, tot: self.on_progress(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                ),
            )

    def _start_day(self, now: datetime) -> None:
        today = now.astimezone(_et()).date()
        cal = self.broker.calendar(str(today))
        open_utc, n_min = session_minutes(cal, today)
        if self.run["status"] == "scheduled":
            self._set_run_status("running")
            self.store.update("run", {"id": self.run["id"]}, {"started_on": str(today)})
        segs = self.store.select(
            "segment",
            {
                "run_id": f"eq.{self.run['id']}",
                "from_session": f"lte.{today}",
                "order": "from_session.desc,id.desc",
                "limit": "1",
            },
        )
        if segs and segs[0]["level"] != self.run.get("level"):
            self.store.update("run", {"id": self.run["id"]}, {"level": segs[0]["level"]})
            self.run["level"] = segs[0]["level"]
        start_eq = self._start_equity()  # S77: yesterday's mark-to-market close (audit B1 item 5)
        base = intraday_equity(start_eq, self._book_pnl(live=False))  # #206: without the multi-day book's P&L
        self.peak = max(float(self.run.get("peak_equity") or base), base)
        self.day = Day(self.run["id"], today, open_utc, n_min, start_eq)
        self._load_rules(self.day)
        self._restore_session(self.day)
        self._corporate_actions(today)
        # S68: custom tickers are loaded only in 'Only these' mode (decision 12); All / Prefer = the catalogue alone
        custom = self.day.rules.custom_universe() if self.day.rules and not self.day.refused else {}
        if self.market.set_custom(custom):  # S68: the custom list changed since the history was loaded
            self.market.day = None
        if self.market.day != today:
            self.on_progress({"state": "preparing", "note": "loading price history (a few minutes the first time)"})
            self.market.prepare_day(
                today,
                open_utc,
                progress=lambda n, tot: self.on_progress(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                ),
            )
        self._vet_custom(self.day)
        for p in self.pos.values():  # intraday positions opened on an earlier session are closed at the first tick
            p.carried = (flatten_at_close(_holding(p.strategy)) and bool(p.entry_ts)
                         and datetime.fromisoformat(p.entry_ts).astimezone(_et()).date() < today)
        if self.kill:
            self.store.event("halt_cleared", {"kind": self.kill, "day": str(today)}, self._rid())
        self.kill, self.error_streak = None, 0
        self._check_recent_stops(today)
        if self.calendar_until is not None and today > self.calendar_until:
            self.store.event("calendar_stale", {"day": str(today), "covers_until": str(self.calendar_until),
                                                "action": "update config/macro_calendar.yaml"}, self._rid())
            self._halt_entries("calendar_stale", f"event calendar ends {self.calendar_until}")
        self.check_account()
        self._startup_reconcile("session_start")  # S78: run again, never reset
        # #133 (decision 15 section 11): a multi-day rule with a Gate B verdict (an experiment that failed it) is
        # named in the journal and the heartbeat so the dashboard labels it. Absent for intraday-only sessions.
        self.experiments = self._experiments()
        start = {"day": str(today), "start_equity": start_eq, "level": self.level(), "minutes": n_min}
        if self.experiments:
            start["experiments"] = self.experiments
        self.store.event("session_start", start, self._rid())

    def _start_equity(self) -> float:
        se = self.ledger.start_equity(self.run["id"])
        return se if se is not None else float(self.run["amount_usd"]) + self.store.run_realized(self.run["id"])

    def _corporate_actions(self, today: date) -> None:
        """S77: dividends and splits of the symbols held at the open, booked to every rule that holds them. Research
        passes the daily store's table; live reads Alpaca's non-trade activities. Never raises: a failure is an
        event, and a split the ledger missed shows as drift (entries stop)."""
        b = self.ledger.book(self._rid())
        if b is None:
            return
        due = self.corporate_actions is None and bool(open_accruals(self.journal.entries(kinds=("div_accrual",
                                                                                                "div_paid"))))
        if not b.sleeves and not due:  # nothing held and no accrued dividend waiting for its payment
            return
        try:
            if self.corporate_actions is not None:
                acts = list(self.corporate_actions(today) or ())
            else:
                if self.ex_dividends is not None:
                    self._accrue_dividends(b, today)
                rows = self.broker.activities(self.now() - timedelta(days=10), types=CORPORATE_ACTIVITIES)
                rows = self._match_dividend_payments(rows)
                bq = {s: float(p["qty"]) for s, p in self.broker.positions().items()}
                acts = actions_from_activities(rows, b.qty_by_symbol(), bq)
        except Exception as e:
            log.exception("corporate actions unavailable")
            self.store.event("corporate_actions_error", {"day": str(today), "error": str(e)[:200]}, self._rid())
            return
        for a in acts:
            self._book_action(b, a)

    def _accrue_dividends(self, b, today: date) -> None:
        """S78 (#118): the dividends going ex today, booked to the sleeves holding the stock at this open (they held
        it over the last close) and journalled as receivables until Alpaca pays them."""
        held = sorted({sym for (_, sym), sl in b.sleeves.items() if sl.qty > 0})
        known = {e[3]["ref"] for e in self.journal.entries(kinds=("div_accrual",))}  # ever accrued (review B3)
        for x in self.ex_dividends(today, held) or ():
            if x.ref in known or not b.holders(x.symbol):
                continue
            amount = x.per_share * b.qty_by_symbol().get(x.symbol, 0.0)
            self._book_action(b, Action(x.symbol, x.ex_date, "dividend", per_share=x.per_share, ref=x.ref))
            self.journal.append("div_accrual", {"ref": x.ref, "symbol": x.symbol, "ex_date": x.ex_date,
                                                "pay_date": x.pay_date, "per_share": x.per_share,
                                                "amount": round(amount, 6), "run_id": b.run_id})

    def _match_dividend_payments(self, rows: list[dict]) -> list[dict]:
        """S78: a DIV payment that pays an accrual is not booked again (journalled as `div_paid`); every other row
        goes on to be booked as before (a payment with no accrual is booked when paid)."""
        entries = self.journal.entries(kinds=("div_accrual", "div_paid"))
        paid = {e[3].get("activity") for e in entries if e[2] == "div_paid"}
        acc = open_accruals(entries)
        out = []
        for r in rows:
            aid = f"alpaca:{r.get('id')}"
            if r.get("activity_type") != "DIV":
                out.append(r)
                continue
            if aid in paid:
                continue  # matched on an earlier day
            ref = match_payment(r, acc)
            if ref is None:
                out.append(r)
                continue
            accrued, net = float(acc[ref]["amount"]), float(r.get("net_amount") or 0.0)
            self.journal.append("div_paid", {"ref": ref, "activity": aid, "net_amount": net, "accrued": accrued})
            acc.pop(ref)
            if abs(net - accrued) > 0.01:
                self.store.event("dividend_difference", {"ref": ref, "accrued": round(accrued, 2),
                                                         "paid": round(net, 2)}, self._rid())
        return out

    def _book_action(self, b, a: Action) -> None:
        if not b.holders(a.symbol):
            return
        div0, qty0 = b.dividends_total, b.qty_by_symbol().get(a.symbol, 0.0)
        self._book(*a.entry(b.run_id))
        if a.kind == "dividend" and self.day is not None:
            self.day.adjust += b.dividends_total - div0
        if a.kind == "split" and b.qty_by_symbol().get(a.symbol, 0.0) != qty0:
            r = float(a.ratio)
            for p in self.pos.on(a.symbol):
                s = b.sleeves.get(p.key)
                if s is None:
                    continue
                p.qty = int(round(s.qty))
                for f in ("fill", "ref", "stop", "target", "risk_ps", "atr", "shadow_entry"):
                    if getattr(p, f) is not None:
                        setattr(p, f, getattr(p, f) / r)
                self.store.update("trade", {"id": p.trade_id}, {"qty": p.qty, "features": p.features()})
        if b.qty_by_symbol().get(a.symbol, 0.0) != qty0 or b.dividends_total != div0:
            self.store.event(f"{a.kind}_booked", {"symbol": a.symbol, "ex_date": a.ex_date, "ref": a.ref,
                                                  "ratio": a.ratio, "cash": round(b.dividends_total - div0, 4),
                                                  "note": "protective orders are re-created by the order manager"
                                                  if a.kind == "split" else None}, self._rid())

    def _check_recent_stops(self, today: date) -> None:
        rows = self.store.select("halt", {"run_id": f"eq.{self.run['id']}", "kind": "eq.daily_stop"})
        lo = today - timedelta(days=STOPS_WINDOW_DAYS)
        recent = sorted({r["session"] for r in rows if r.get("session") and lo <= date.fromisoformat(r["session"])})
        if len(recent) >= STOPS_TO_PAUSE and self.run["status"] == "running":
            reason = f"{len(recent)} daily stops since {lo}: paused until you resume"
            self.store.insert("halt", {"run_id": self._rid(), "kind": "three_stops", "reason": reason,
                                       "session": str(today)}, returning=False)
            self._set_run_status("paused", reason)
            self.store.event("run_paused", {"reason": reason, "sessions": recent}, self._rid())

    def equity_now(self) -> tuple[float, float]:
        d = self.day
        unreal = 0.0
        for p in self.pos.values():
            if p.status == "open" and p.fill is not None:
                base = self._basis(p)
                last = self.market.last_close(p.symbol) or base
                unreal += (last - base) * p.qty
        for (_, sym), sl in self._md_sleeves().items():  # S80: multi-day sleeves, from the last close mark (the gap
            base = sl.mark if sl.mark is not None else sl.avg_cost  # since that close counts: audit B1 item 4)
            last = self._md_price(sym) or base
            unreal += (last - base) * sl.qty
        return d.start_equity + d.realized_reported + unreal + d.adjust, unreal

    def _risk_halts(self, m: int, eq: float) -> None:
        if self.run is None:
            # S74b (#137): the run has ended (run drawdown) and its flatten is out; until the closes are booked the
            # engine only manages them. Nothing to halt any more: a second pass wrote the halt again and raised.
            return
        d, pol = self.day, self.policy()
        if not d.halted and eq <= d.start_equity * (1 - pol.max_daily_loss_pct / 100):
            # S80 (#120): the day's change in mark-to-market equity, overnight gaps included. Entries are blocked;
            # intraday sleeves are flattened as before, multi-day sleeves keep their protective stops.
            self.flatten_all("daily_stop")
            d.halted = True
            kept = sorted({k[1] for k in self._md_sleeves()})
            self.store.insert(
                "halt",
                {"run_id": self._rid(), "kind": "daily_stop",
                 "reason": f"day P&L reached -{pol.max_daily_loss_pct}%"
                           + (f"; entries blocked, multi-day positions keep their stops: {', '.join(kept)}"
                              if kept else ""),
                 "session": str(d.day)},
                returning=False,
            )
        # #206: with the live daily path the multi-day book has its own run end (`_book_end_check`), and the run's
        # intraday end (unchanged: RiskPolicy.run_drawdown_end_pct, the same formula) is measured without the book's
        # P&L. Without the daily path the book's P&L is 0 and this is exactly the check it always was.
        book_pnl = self._book_pnl()
        self._book_end_check(book_pnl)
        eq_i = intraday_equity(eq, book_pnl)
        self.peak = max(self.peak or eq_i, eq_i)
        if self.peak and run_drawdown_reached(self.peak, eq_i, pol.run_drawdown_end_pct):
            self.flatten_all("run_drawdown")
            self.store.insert(
                "halt",
                {
                    "run_id": self._rid(),
                    "kind": "run_drawdown",
                    "reason": f"drawdown reached {pol.run_drawdown_end_pct}% from peak",
                },
                returning=False,
            )
            self._end_run("ended", f"drawdown {pol.run_drawdown_end_pct}% from peak")

    def _may_enter(self, m: int) -> bool:
        d = self.day
        if self.run is None or self.run["status"] != "running" or not self.account_clean or self.kill:
            return False
        if not self._reconciled():  # S78: an open ledger/broker difference blocks entries (exits go on)
            return False
        if not self.lease_ok():  # fail closed: the lease could not be renewed, so we may not be the only engine
            return False
        if d.halted or d.flattened or d.refused or m < d.pause_until:
            return False
        r = d.rules
        first, last = (max(FIRST_ENTRY_M, r.first_entry_m), min(d.last_entry_m, r.last_entry_m)) if r else (
            FIRST_ENTRY_M, d.last_entry_m)
        if not (first <= m + 1 <= last):
            return False
        return in_blackout(self.events, d.day, m, r.event_window_min if r else None) is None

    def _runs(self, strategy: str) -> bool:
        r = self.day.rules if self.day else None
        if r is not None:
            return strategy in r.strategies
        return strategy in self.level_strategies()

    # ================= strategies (S76) =================
    def level_strategies(self) -> list[str]:
        """The live strategies the run's level lists (config/profiles.yaml); the owner's config can only remove."""
        return list(self.strategies.get(self.level(), registry.DEFAULT_LIVE))

    def _experiments(self) -> list[dict]:
        """The label rows of the session's multi-day rules that carry a Gate B verdict (risk/profiles.experiments).
        Never raises: a label that cannot be built is logged and left out (it never stops trading or a stop)."""
        r = self.day.rules if self.day else None
        names = r.strategies if r is not None else self.level_strategies()
        if not registry.multi_day_names(names):
            return []
        try:
            from kaibot.risk.profiles import experiments

            return experiments(names, self.level())
        except Exception:
            log.exception("experiment label unavailable")
            return []

    def live_strategies(self) -> tuple[Strategy, ...]:
        """The registered live strategies this session runs, in registry order."""
        r = self.day.rules if self.day else None
        strats = registry.live(r.strategies if r is not None else self.level_strategies())
        held = [s.name for s in strats if s.holding is Holding.MULTI_DAY]
        if held and self.corporate_actions is None and self.ex_dividends is None:
            # S78 (#118): a rule held over an ex-date earns the dividend at the ex-date, so it needs the ex-date
            # source (engine/dividends.ExDateDividends, wired by __main__). Without one: fail closed.
            raise ExDateSourceMissing(
                f"{', '.join(held)} hold(s) across sessions: dividends must be accrued at the ex-date, and this "
                "engine has no ex-date source (ex_dividends)")
        return strats

    def context(self, m: int, event: Event = Event.BAR_CLOSE, strategy: str | None = None) -> Context:
        """`strategy`: whose positions `has_position` asks about (S77: a rule sees its own book)."""
        d = self.day

        def held(s: str) -> bool:
            return (strategy, s) in self.pos if strategy is not None else s in self.pos

        def prefetch(symbols: list[str]) -> None:
            self._quotes = self.broker_quotes(symbols)
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
            ctx.prefetched = True

        ctx = Context(event, m, d.day, d.n_min, self.market, d.rules, self.now, held, prefetch,
                      d.scan, lambda s, ref, vix: self._user_veto(d.rules, s, ref, vix) if d.rules else None,
                      lambda s, mm: self.rules_extra(self, s, mm) if self.rules_extra is not None else None)
        return ctx

    # ================= entries =================
    def _entries(self, strat: Strategy, m: int) -> None:
        """Ask one due strategy for its signals and decide each in turn, through the owner's filters, the room
        (if the strategy uses it) and the risk gate. Signals come one at a time, so what an earlier one opened is
        already in the book when the strategy computes the next."""
        sc = self.day.scan
        ctx = self.context(m, strategy=strat.name)
        rf = self.roomflow if strat.room else None
        for sig in strat.on_event(ctx) or ():
            s, ref, stop, target = sig.symbol, sig.entry, sig.stop, sig.target
            veto = sig.veto
            if strat.owner_filters:
                r = self.day.rules
                if veto is None and r is not None:
                    veto = self._user_veto(r, s, ref, sig.vix)
                if veto is None and self.rules_extra is not None:
                    try:
                        veto = self.rules_extra(self, s, m)
                    except Exception:
                        log.exception("extra rules veto failed for %s: skipping the candidate", s)
                        veto = "extra rules unavailable"
                if veto is not None:
                    sc["vetoed"][veto] = sc["vetoed"].get(veto, 0) + 1
            # S74: from here on this decision acts (a room session, a signal row, an order), so a retry of the
            # minute must not decide it again: a second decision would write a new signal row and, with it, a new
            # client order id (kb-<run>-<signal>-1) that the venue's duplicate check cannot recognise. A decision
            # that failed before this line (features, quotes, the news lookup) has done nothing and is repeated.
            self.day.decided[strat.name] = m
            if veto is None and rf is not None:
                mode = rf.mode(m)
                if mode == "ok":
                    sc["to_room"] += 1
                    rf.submit(s, sig.room, ref, stop, target, sig.features, sig.reasons, m, strategy=strat.name)
                    continue
                if mode == "no_entries":
                    return
            sig_id, full_qty = self._decide(s, strat.name, ref, stop, target, sig.features, sig.reasons, veto,
                                            sigma_1m=sig.sigma_1m, atr=sig.atr, vix=sig.vix,
                                            fresh_reads=not ctx.prefetched)
            if veto is None and rf is not None:
                rf.record_skipped(sig_id, s, m, ref, ref - stop, sig.atr, sig.sigma_1m, full_qty,
                                  "daily LLM budget reached: rules-only")
            if self.day.entries >= self.policy().max_entries_per_day:
                break

    def _decide(self, s, strategy, ref, stop, target, feats, reasons, veto, sigma_1m, atr, vix=None,
                fresh_reads=True, size_mult: float = 1.0) -> tuple[int | None, int]:
        """Gate one proposal, log the signal and the gate run (reason codes, S12), and place it if approved.
        size_mult: 0.5 when the room downgrades. Returns (signal id, the gate's full-size qty) for the A/B."""
        pol, d = self.policy(), self.day
        decision = self.now()
        if not (s in self.market.base or s == self.market.spy or s in d.custom_ok):
            veto = "not an accepted ticker today"  # S68: a refused or unchecked custom ticker can never be entered
        if veto is None and s in self._md_symbols():
            # #206 (QA-Ops): Alpaca refuses an order while an opposite-side market or stop order is open in the
            # symbol, and one position per symbol holds both books' shares. An intraday entry in a stock the
            # multi-day book holds or has a closing-auction order in could leave its 15:55 flatten (or the cls order)
            # refused, so the intraday rule leaves that stock alone (the multi-day book goes first: it sends at 09:31)
            veto = "multi_day_symbol"
        if fresh_reads:  # single decisions read quotes and buying power themselves
            self._quotes = self.broker_quotes([s])
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
        if vix is None:
            vix = self.market.regime_at(int(feats.get("m", 0)))[1]
        q = self._quotes.get(s) or {}
        q_age = _age_s(q.get("t"), decision)
        eq, _ = self.equity_now()
        live = [p for p in self.pos.values() if p.status in ("pending", "open")]
        exposure = sum((p.fill or p.ref) * p.qty for p in live)
        for (_, sym), sl in self._md_sleeves().items():  # S80: the cash multi-day sleeves hold is not buying power
            exposure += (self._md_price(sym) or sl.avg_cost) * sl.qty
        bp = self._bp_read
        snap = Snapshot(
            equity=eq,
            day_start_equity=d.start_equity,
            open_positions=len(live),
            symbol_exposure=0.0,
            open_risk=sum(p.risk_ps * p.qty for p in live),
            quote_age_s=q_age,
            kill_switch=bool(self.kill) or not self.lease_ok(),  # second lock on the same door (#81)
            reconciled=self.account_clean,
            market_open=True,
            data_ok=(self.market.last_bar_minute(s) or -99) >= int(feats.get("m", 0)) - 2,
            in_entry_window=True,
            entries_today=d.entries,
            run_peak_equity=gate_peak(self.peak, eq, self._book_pnl()),  # #206: the intraday run end, as _risk_halts
            # the account's figure already nets its open positions; subtract what this loop has added since
            buying_power=eq - exposure if bp is None else min(eq - exposure, bp - self._bp_added),
        )
        feats = {**feats, "quote_age_s": q_age, "vix": vix}
        v = evaluate(Proposal(s, Side.BUY, ref, stop, target), snap, pol)
        full_qty = int(np.floor(v.qty * (0.5 if vix > 25 else 1.0))) if v.approved else 0
        qty = int(np.floor(full_qty * size_mult)) if not veto else 0
        sig = self.store.insert(
            "signal",
            {
                "run_id": self._rid(),
                "strategy": strategy,
                "mode": "live",
                "symbol": s,
                "ts": decision,
                "features_hash": hashlib.sha256(json.dumps(feats, sort_keys=True).encode()).hexdigest()[:16],
                "features": feats,
                "entry": ref,
                "stop": stop,
                "target": target,
                "reasons": reasons,
                "rules_veto": veto,
            },
        )
        self.store.insert(
            "gate_run",
            {
                "signal_id": sig["id"],
                "policy_hash": self._policy_hash(self.level()),
                "code_sha": self.code,
                "checks": {
                    ch.rule: {"state": ch.state.value, "actual": ch.actual, "limit": ch.limit} for ch in v.checks
                },
                "approved": bool(v.approved and not veto and qty > 0),
                "qty": qty,
                "binding": v.binding,
            },
            returning=False,
        )
        if qty <= 0:
            return sig["id"], full_qty
        if not self._open(s, qty, ref, stop, target, ref - stop, atr, sigma_1m, q, sig["id"], feats, strategy):
            return sig["id"], full_qty  # S74: the broker refused it (now on the signal); no entry, the scan goes on
        self._bp_added += qty * ref
        d.entries += 1
        return sig["id"], full_qty

    def _broker_buying_power(self) -> float | None:
        """The paper account's own buying power; None if unknown (then the pot alone caps it)."""
        try:
            bp = self.broker.account().get("buying_power")
            return float(bp) if bp is not None else None
        except Exception:
            log.exception("account read failed")
            return 0.0  # fail closed: no buying power if we can't read it

    def broker_quotes(self, symbols: list[str]) -> dict:
        try:
            return self.market.data.latest_quotes(symbols)
        except Exception:
            log.exception("quotes failed")
            return {}

    def _open(self, s, qty, ref, stop, target, risk_ps, atr, sigma, quote, signal_id, feats, strategy: str) -> bool:
        """Send the entry and write its order and trade rows. False: the broker refused the order (S74)."""
        coid = f"kb-{self._rid()}-{signal_id}-1"
        bid, ask = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
        if not (0 < bid <= ask):
            hs = half_spread(ref)
            bid, ask = ref - hs, ref + hs
        shadow_entry = shadow_fill_price("buy", qty, QuoteCtx(bid, ask, sigma, max(self.market.bar_volume(s), 1)))
        try:
            if target is None:  # exit timed by the engine (MOC): stop only, still held at the broker
                order = self._order("entry", lambda: self.broker.submit_oto_stop(coid, s, qty, round(stop, 2)),
                                    coid=coid, strategy=strategy, symbol=s, qty=qty, stop=round(stop, 2),
                                    signal_id=signal_id)
            else:
                order = self._order("entry", lambda: self.broker.submit_bracket(coid, s, qty, round(stop, 2),
                                                                                round(target, 2)),
                                    coid=coid, strategy=strategy, symbol=s, qty=qty, stop=round(stop, 2),
                                    target=round(target, 2), signal_id=signal_id)
        except Exception as e:
            if not _order_refused(e):
                raise  # network error, 5xx, anything unknown: the order may be at the venue. A loop error, as before
            if self._broker_rejected(s, qty, coid, signal_id, feats, e):
                raise  # it keeps happening: a loop error again, and entries are halted
            return False
        self.day.rejects = 0
        db_order = self.store.insert(
            "order",
            {
                "signal_id": signal_id,
                "client_order_id": coid,
                "venue": "alpaca_paper",
                "legs": {"stop": round(stop, 2), "target": None if target is None else round(target, 2), "qty": qty},
                "status": order.get("status", "new"),
                "submitted_at": self.now(),
            },
        )
        p = Pos(
            trade_id=0,
            symbol=s,
            qty=qty,
            coid=coid,
            order_id=order["id"],
            ref=ref,
            risk_ps=risk_ps,
            atr=atr,
            sigma=sigma,
            stop=stop,
            target=target if target is not None else 0.0,
            shadow_entry=shadow_entry,
            db_order_id=db_order["id"],
            entry_ts=self.now().isoformat(),
            strategy=strategy,
        )
        trade = self.store.insert(
            "trade",
            {
                "run_id": self._rid(),
                "config_version": self._cfg_version(),
                "signal_id": signal_id,
                "strategy": strategy,
                "symbol": s,
                "entry_ts": self.now(),
                "qty": qty,
                "entry_px": ref,
                "stop_px": stop,
                "target_px": target,
                "risk_usd": risk_ps * qty,
                "features": {**feats, **p.features(), "room": "off (M1)"},
            },
        )
        p.trade_id = trade["id"]
        self.pos.add(p)
        return True

    def _broker_rejected(self, s: str, qty: int, coid: str, signal_id, feats: dict, err: Exception) -> bool:
        """S74 (#114, audit D4): the venue refused this entry (403 / 422), so no order exists. The signal was
        written as approved a moment ago: say on it that the broker rejected it and why, and take the approval
        back, so nothing downstream (the tape, "setups taken", order adoption at the next start) counts it as an
        entry. True when it was the MAX_REJECT_STREAK-th refusal in a row: entries halt for the session and the
        caller raises it as a loop error again. The count is per session; an accepted order resets it."""
        d = self.day
        d.rejects += 1
        status, reason = getattr(err, "status", None), _reject_reason(err)
        log.warning("broker rejected the entry for %s (%s): %s", s, status, reason)
        self.store.update("signal", {"id": signal_id}, {
            "rules_veto": f"broker rejected: {reason}",
            "features": {**feats, "broker_reject": {"status": status, "reason": reason, "qty": qty,
                                                    "client_order_id": coid}}})
        self.store.update("gate_run", {"signal_id": signal_id}, {"approved": False, "qty": 0})
        if d.rejects < MAX_REJECT_STREAK:
            return False
        self._halt_entries("broker_rejects", f"{d.rejects} entry orders in a row rejected by the broker: {reason}")
        return True

    # ================= order sync =================
    def _sync_orders(self) -> None:
        if self._md_exits:
            self._sync_md_exits()
        for p in list(self.pos.values()):
            if p.status == "closed":
                continue
            o = self.broker.order(p.exit_order_id or p.order_id)
            if p.status == "pending":
                if o["status"] == "filled":
                    self._on_entry_fill(p, o)
                elif o["status"] in ("canceled", "expired", "rejected"):
                    self._close_trade(p, None, "not_filled")
                continue
            if p.exit_order_id:  # we sent a market sell (time / daily stop / manual)
                if o["status"] == "filled":
                    self._on_exit_fill(p, float(o["filled_avg_price"]), p.exit_reason or "manual", o.get("filled_at"))
                continue
            for leg in o.get("legs") or []:
                if leg["status"] == "filled":
                    is_target = leg.get("type") == "limit"
                    reason = "target" if is_target else ("trail" if p.armed else "stop")
                    self._on_exit_fill(p, float(leg["filled_avg_price"]), reason, leg.get("filled_at"))
                    break

    def _on_entry_fill(self, p: Pos, o: dict) -> None:
        p.fill = float(o["filled_avg_price"])
        p.status = "open"
        self._book("fill", {"run_id": self._rid() or (self.day and self.day.run_id), "strategy": p.strategy,
                            "symbol": p.symbol, "side": "buy", "qty": p.qty, "px": p.fill, "fees": 0.0,
                            "coid": p.coid, "ts": o.get("filled_at"),
                            "session": str(self.day.day) if self.day else None})
        strat = registry.get(p.strategy)
        p.stop, target = strat.on_fill(p.fill, p.risk_ps) if strat is not None else (p.fill - p.risk_ps, None)
        p.target = target if target is not None else 0.0
        for leg in o.get("legs") or []:
            if leg.get("type") == "limit":
                p.target_order_id = leg["id"]
                if abs(float(leg["limit_price"]) - p.target) >= 0.01:
                    oid, px = leg["id"], round(p.target, 2)
                    p.target_order_id = self._order(
                        "replace_limit", lambda oid=oid, px=px: self.broker.replace_limit(oid, px),
                        order_id=oid, symbol=p.symbol, coid=p.coid, price=px)["id"]
            elif leg.get("type") in ("stop", "stop_limit"):
                p.stop_order_id = leg["id"]
                if abs(float(leg["stop_price"]) - p.stop) >= 0.01:
                    oid, px = leg["id"], round(p.stop, 2)
                    p.stop_order_id = self._order(
                        "replace_stop", lambda oid=oid, px=px: self.broker.replace_stop(oid, px),
                        order_id=oid, symbol=p.symbol, coid=p.coid, price=px)["id"]
        self.store.insert(
            "fill",
            {
                "order_id": p.db_order_id,
                "ts": o.get("filled_at") or self.now(),
                "side": "buy",
                "qty": p.qty,
                "venue_px": p.fill,
                "shadow_px": p.shadow_entry,
                "fees": 0,
            },
            returning=False,
        )
        self.store.update(
            "trade",
            {"id": p.trade_id},
            {
                "entry_px": p.fill,
                "stop_px": p.stop,
                "target_px": p.target,
                "entry_ts": o.get("filled_at") or self.now(),
                "features": p.features(),
            },
        )

    def _on_exit_fill(self, p: Pos, px: float, reason: str, ts) -> None:
        fees = sell_fees(p.qty, px)
        shadow_exit = px if reason == "target" else sell_px(px, p.qty, p.sigma, self.market.bar_volume(p.symbol))
        pnl_v = (px - p.fill) * p.qty - fees
        pnl_s = (shadow_exit - (p.shadow_entry or p.fill)) * p.qty - fees
        self.store.insert(
            "fill",
            {
                "order_id": p.db_order_id,
                "ts": ts or self.now(),
                "side": "sell",
                "qty": p.qty,
                "venue_px": px,
                "shadow_px": shadow_exit,
                "fees": round(fees, 4),
                "tag": reason,
            },
            returning=False,
        )
        d = self.day
        base = self._basis(p)
        if d and base != p.fill:  # held over the last close: its gain to that mark is already in start_equity
            d.adjust -= (base - p.fill) * p.qty
        rid = self._rid() or (d.run_id if d else None)
        if rid:
            self._book("fill", {"run_id": rid, "strategy": p.strategy, "symbol": p.symbol, "side": "sell",
                                "qty": p.qty, "px": px, "fees": fees, "coid": p.coid, "ts": ts, "reason": reason,
                                "pnl_venue": pnl_v, "pnl_shadow": pnl_s})
        if d:
            d.realized_venue += pnl_v
            d.realized_shadow += pnl_s
            d.realized_reported += min(pnl_v, pnl_s)
            d.trades_closed += 1
            d.losses_in_row = d.losses_in_row + 1 if min(pnl_v, pnl_s) < 0 else 0
            pl, pm = self._pause_rule()
            if d.losses_in_row >= pl:
                d.pause_until, d.losses_in_row = d.last_m + pm, 0
        p.exit_reason = reason
        self._close_trade(p, px, reason, min(pnl_v, pnl_s), ts, {"pnl_venue": pnl_v, "pnl_shadow": pnl_s})

    def _close_trade(self, p: Pos, px, reason, pnl=None, ts=None, extra=None) -> None:
        p.status = "closed"
        self.store.update(
            "trade",
            {"id": p.trade_id},
            {
                "exit_ts": ts or self.now(),
                "exit_px": px,
                "exit_reason": reason,
                "pnl_usd": None if pnl is None else round(pnl, 2),
                "features": {**p.features(), **(extra or {})},
            },
        )
        self.pos.pop(p)

    # ================= exits =================
    def _open_by_strategy(self):
        """(position, its registered strategy) for every open position without an exit order, in book order."""
        for p in list(self.pos.values()):
            if p.status != "open" or p.exit_order_id:
                continue
            strat = registry.get(p.strategy)
            if strat is not None:
                yield p, strat

    def _manage_stops(self, m: int) -> None:
        """Each open position's strategy may move its protective stop (dip reversal: the trailing stop)."""
        ctx = None
        for p, strat in self._open_by_strategy():
            ctx = ctx or self.context(m)
            act = strat.manage(ctx, p)
            if isinstance(act, MoveStop) and p.stop_order_id:
                try:
                    oid = p.stop_order_id
                    px = round(act.price, 2)
                    p.stop_order_id = self._order("replace_stop", lambda oid=oid, px=px: self.broker.replace_stop(
                        oid, px), order_id=oid, symbol=p.symbol, strategy=p.strategy,
                        stop=round(act.price, 2))["id"]
                    p.stop = act.price
                    self.store.update("trade", {"id": p.trade_id}, {"stop_px": act.price, "features": p.features()})
                except Exception:
                    log.exception("trail replace failed for %s", p.symbol)

    def _close_outs(self, m: int) -> None:
        """Each open position's strategy may ask for its own exit (SPY momentum: market-on-close from 15:45).
        A MOC cancels the stop first (it reserves the shares); if the MOC is refused, the position stays and the
        15:55 market flatten closes it."""
        ctx = None
        for p, strat in self._open_by_strategy():
            ctx = ctx or self.context(m, Event.PRE_CLOSE)
            act = strat.close_out(ctx, p)
            if not isinstance(act, CloseOut):
                continue
            if act.how != "moc":
                self._exit(p, act.reason)
                continue
            if p.stop_order_id:
                oid = p.stop_order_id
                self._order("cancel", lambda oid=oid: self.broker.cancel_order(oid), order_id=oid, symbol=p.symbol,
                            strategy=p.strategy)
                p.stop_order_id = None
            try:
                o = self._order("moc_sell", lambda p=p: self.broker.submit_moc_sell(f"{p.coid}-moc", p.symbol, p.qty),
                                coid=f"{p.coid}-moc", symbol=p.symbol, strategy=p.strategy, qty=p.qty)
                p.exit_order_id, p.exit_reason = o["id"], act.reason
            except Exception as e:
                log.warning("MOC refused for %s (%s); the 15:55 flatten will close it", p.symbol, e)
                self.store.event("moc_refused", {"symbol": p.symbol, "error": str(e)[:200]}, self._rid())
                p.exit_reason = None

    def _exit(self, p: Pos, reason: str) -> None:
        if p.status == "pending":
            self._order("cancel", lambda: self.broker.cancel_order(p.order_id), order_id=p.order_id,
                        symbol=p.symbol, strategy=p.strategy)
            self._close_trade(p, None, "not_filled")
            return
        if p.exit_order_id or p.status != "open":
            return
        if any(q is not p and q.symbol == p.symbol and q.status in ("open", "pending") for q in self.pos.values()):
            o = self._sell_sleeve(p, reason)  # S77: another rule holds the stock too: sell this rule's shares only
        else:
            o = self._order("close_position", lambda: self.broker.close_position(p.symbol), symbol=p.symbol,
                            strategy=p.strategy, qty=p.qty, reason=reason)
        p.exit_reason = reason
        if o:
            p.exit_order_id = o["id"]
        else:  # nothing to close at the broker: already flat (a leg filled meanwhile)
            self._sync_orders()

    def _sell_sleeve(self, p: Pos, reason: str) -> dict:
        """Close one rule's position in a stock another rule also holds: cancel this rule's own protective legs
        (they reserve its shares), then sell its shares at market. The other rule's legs and shares stay."""
        for oid in (p.stop_order_id, p.target_order_id):
            if oid:
                self._order("cancel", lambda oid=oid: self.broker.cancel_order(oid), order_id=oid, symbol=p.symbol,
                            strategy=p.strategy)
        p.stop_order_id = p.target_order_id = None
        coid = f"{p.coid}-x"
        return self._order("sell", lambda: self.broker.submit(OrderRequest(p.symbol, "sell", int(p.qty), "market",
                                                                           "day", coid)),
                           coid=coid, symbol=p.symbol, strategy=p.strategy, qty=p.qty, reason=reason)

    def flatten_all(self, reason: str) -> None:
        """Close every position, each one on its own (S74, #114, audit D7), inside one time limit.

        A close that fails no longer stops the loop: the other positions still go out. A close the venue refused
        (4xx, typically while a cancel is still pending) is tried again at once, FLATTEN_TRIES in all; not an
        unreachable venue (the adapter has already retried it for about 30 s) nor a cancel the adapter already
        waited CANCEL_WAIT_S for (409). The whole flatten, retries included, starts no close once less than
        CLOSE_MAX_S of FLATTEN_BUDGET_S is left, and writes the heartbeat before each close, so a slow venue
        cannot make the heartbeat go stale (watchdog, pg_cron dead-man). What is still open after that is logged
        and raised as FlattenIncomplete, once every position was tried or the budget ran out. So no caller
        carries on as if the book were flat (the session is not marked flattened, the halt is not written, the run
        is not ended), and the caller's own retry comes back for the rest: the main loop runs the tick again about
        10 s later, `_exit` skips the positions that already have an exit order, and the positions this flatten
        could not close are tried after the others, so a few stuck ones cannot use up every budget."""
        t0 = self.monotonic()
        if self.daily is not None and reason != "time":  # #206 (QA-Ops review): the daily path's resting `cls`
            # orders go first: buys on a halt (daily stop), buys and sales on a full flatten (else a resting sale
            # fills after the flatten's own sale: a short). The close-minus-5 flatten leaves them alone.
            sides = ("buy",) if reason in INTRADAY_ONLY else ("buy", "sell")
            self._daily_step("cancel", lambda: self.daily.cancel_working(sides, reason))
        failed: dict[str, Exception | str] = {}
        todo = sorted(self.pos.values(), key=lambda p: p.symbol in self._flatten_slow)  # stable: book order kept
        if reason in INTRADAY_ONLY:  # S78/S80: the close-minus-5 flatten and the daily stop: intraday sleeves only
            todo = [p for p in todo if flatten_at_close(_holding(p.strategy))]
        for _ in range(FLATTEN_TRIES):
            again = []
            for k, p in enumerate(todo):
                if self.monotonic() - t0 > FLATTEN_BUDGET_S - CLOSE_MAX_S:
                    for q in todo[k:]:  # not attempted: reported, and the next loop pass starts with the others
                        failed.setdefault(q.symbol, f"not tried: flatten time budget ({FLATTEN_BUDGET_S:g} s) used")
                    again = []
                    break
                self._flatten_beat(reason, p.symbol, len(todo) - k)
                try:
                    self._exit(p, reason)
                    failed.pop(p.symbol, None)
                except Exception as e:  # one failed close must not stop the others
                    log.warning("flatten (%s): closing %s failed: %s: %s", reason, p.symbol, type(e).__name__, e)
                    failed[p.symbol] = e
                    if _refused(e) and getattr(e, "status", None) != CANCEL_UNCONFIRMED:
                        again.append(p)
            todo = again
            if not todo:
                break
        if reason not in INTRADAY_ONLY:  # S80: run drawdown, new run, "Flatten and stop": multi-day sleeves too
            self._flatten_md(reason, failed, t0)
        self._flatten_slow = {s for s, e in failed.items() if isinstance(e, Exception)}  # tried and failed
        if failed:
            left = {s: (f"{type(e).__name__}: {e}" if isinstance(e, Exception) else e)[:160] for s, e in failed.items()}
            log.error("flatten (%s) incomplete after %.1f s, still open: %s", reason, self.monotonic() - t0, left)
            cause = next((e for e in failed.values() if isinstance(e, Exception)), None)
            raise FlattenIncomplete(reason, left) from cause

    def _flatten_beat(self, reason: str, symbol: str, left: int) -> None:
        """S74: the heartbeat between the closes of a flatten (the main loop writes it only after the tick)."""
        try:
            self.on_progress({"state": "flattening", "reason": reason, "closing": symbol, "left": left})
        except Exception:  # a heartbeat that fails must not stop the flatten
            log.exception("flatten heartbeat failed")

    # ================= bookkeeping =================
    def _ledger(self, eq: float) -> None:
        d = self.day
        if self.run is None:
            return
        pos: dict = {}
        for s, p in self.pos.items():
            pos[s if s not in pos else f"{s}|{p.strategy}"] = {
                "qty": p.qty,
                "fill": p.fill,
                "stop": round(p.stop, 2),
                "target": round(p.target, 2),
                "status": p.status,
                "last": self.market.last_close(s),
                "trade_id": p.trade_id,
            }
        unreal = eq - d.start_equity - d.realized_reported
        self.store.insert(
            "ledger",
            {
                "run_id": d.run_id,
                "ts": self.now().replace(microsecond=0),
                "cash": round(eq - sum((v["last"] or 0) * v["qty"] for v in pos.values()), 2),
                "positions": pos,
                "equity_venue": round(d.start_equity + d.realized_venue + unreal, 2),
                "equity_shadow": round(d.start_equity + d.realized_shadow + unreal, 2),
            },
            returning=False,
        )
        if self.run and self.peak and self.peak > float(self.run.get("peak_equity") or 0):
            self.store.update("run", {"id": self.run["id"]}, {"peak_equity": round(self.peak, 2)})
            self.run["peak_equity"] = round(self.peak, 2)

    def _finalize_day(self) -> None:
        d = self.day
        if self.roomflow is not None:
            try:
                self.roomflow.finalize()  # S36: rules-only counterfactuals for today's room sessions
            except Exception:
                log.exception("room finalize failed")
        left = [p for p in self.pos.values() if flatten_at_close(_holding(p.strategy))]
        if left:  # S80: intraday sleeves only; a multi-day sleeve is meant to be held over the close
            log.warning("session ended with open intraday positions %s; they will be closed at the next open",
                        [p.symbol for p in left])
            for p in left:
                p.carried = True
        rid = d.run_id
        if rid:
            self.store.upsert(
                "daily_result",
                {
                    "run_id": rid,
                    "session": str(d.day),
                    "pnl_venue": round(d.realized_venue, 2),
                    "pnl_shadow": round(d.realized_shadow, 2),
                    "spy_ret": self.market.spy_return(),
                    "trades": d.trades_closed,
                    "config_version": d.rules.version if d.rules else 0,
                },
                "run_id,session",
            )
            try:
                self._close_ledger(d)
            except Exception:  # the day's trading result is written; the close is recomputable from the journal
                log.exception("ledger close failed")
        self.store.event(
            "session_end",
            {
                "day": str(d.day),
                "pnl_reported": round(d.realized_reported, 2),
                "trades": d.trades_closed,
                "halted": d.halted,
            },
            rid,
        )
        shadow.on_day_end(self)  # S14: counterfactual P&L of today's shadow signals (exceptions contained)
        gate_report.on_day_end(self, ROOT)  # S38: random baseline + append-only gate report (exceptions contained)
        d.finalized = True

    def _close_price(self, p: Pos) -> float:
        px = self.market.last_close(p.symbol)
        if px:
            return float(px)
        try:
            q = self.broker.positions().get(p.symbol) or {}
            if q.get("current_price"):
                return float(q["current_price"])
        except Exception:
            log.exception("positions read failed (close mark)")
        return float(self._basis(p))

    def _close_ledger(self, d: Day) -> None:
        """S77: mark every open position at the close, close the day in the ledger, compare cash and positions
        with the broker, and write the day's mark-to-market rows (ledger_day, ledger_position)."""
        prices = {price_key(p.strategy, p.symbol): self._close_price(p) for p in self.pos.values()
                  if p.status == "open" and p.fill is not None}
        md = self._md_sleeves()
        for (strategy, sym) in md:  # S80: multi-day sleeves are marked at the close too (unpriced: last mark)
            px = self._md_price(sym)
            if px:
                prices[price_key(strategy, sym)] = px
        md_stops = self._md_stops() if md else {}
        b = self.ledger.book(d.run_id)
        if b is None:
            return
        self._book("mark", {"run_id": d.run_id, "session": str(d.day), "prices": prices, "ts": self.now(),
                            "pnl_venue": round(d.realized_venue, 2), "pnl_shadow": round(d.realized_shadow, 2)})
        c = b.closes[-1]
        rec = self._reconcile(b)
        gross = c.invested / c.close_equity_venue if c.close_equity_venue > 0 else 0.0
        self.store.upsert("ledger_day", {
            "run_id": d.run_id, "session": str(d.day), "start_equity": round(c.start_equity, 2),
            "close_equity": round(c.close_equity, 2), "close_equity_venue": round(c.close_equity_venue, 2),
            "cash": round(c.cash, 2), "invested": round(c.invested, 2), "realized_venue": round(c.realized_venue, 2),
            "pnl_venue": c.pnl_venue, "pnl_shadow": c.pnl_shadow, "dividends": round(c.dividends, 2),
            "gross": round(gross, 6), "overnight": round(gross, 6), "positions": len(c.positions),
            "reconciled": rec, "ts": self.now()}, "run_id,session")
        held = {p.key: p for p in self.pos.values()}
        for r in c.positions:
            p = held.get((r["strategy"], r["symbol"]))
            from kaibot.engine.failsafe import clean

            ms = md_stops.get((clean(r["strategy"]), clean(r["symbol"])))
            self.store.upsert("ledger_position", {
                "run_id": d.run_id, "session": str(d.day), "strategy": r["strategy"], "symbol": r["symbol"],
                "qty": r["qty"], "avg_cost": round(r["avg_cost"], 4), "mark": round(r["mark"], 4),
                "priced": r["priced"], "opened_session": r["opened"], "dividends": round(r["dividends"], 2),
                "stop_px": round(p.stop, 2) if p is not None else (round(ms, 2) if ms is not None else None),
                "stop_state": "resting" if (p is not None and p.stop_order_id) or ms is not None else "missing"},
                "run_id,session,strategy,symbol")

    def _reconcile(self, b) -> dict:
        """Cash and positions against the broker. Positions: the ledger's shares per symbol (all rules) against
        the broker's. Cash: the change since the run began (the paper account holds more than the run's pot)."""
        try:
            bpos, acct = self.broker.positions(), self.broker.account()
        except Exception as e:
            return {"ok": None, "error": f"{type(e).__name__}: {e}"[:160], "ts": self.now().isoformat()}
        mine = {s: q for s, q in b.qty_by_symbol().items() if q}
        theirs = {s: float(p["qty"]) for s, p in bpos.items()}
        pos = {s: {"ledger": mine.get(s, 0), "broker": theirs.get(s, 0)} for s in sorted(set(mine) | set(theirs))
               if abs(mine.get(s, 0) - theirs.get(s, 0)) > 1e-9}
        cash_diff = None
        due = receivable(self.journal.entries(kinds=("div_accrual", "div_paid")))  # S78: accrued, not yet paid
        if b.broker_cash0 is not None and acct.get("cash") is not None:
            cash_diff = round((float(acct["cash"]) - float(b.broker_cash0)) - (b.cash - due - b.amount), 2)
        tol = max(CASH_TOLERANCE_USD, CASH_TOLERANCE_PCT / 100 * b.amount)
        ok = not pos and (cash_diff is None or abs(cash_diff) <= tol)
        out = {"ok": ok, "positions": pos, "cash_diff": cash_diff, "tolerance": tol, "ts": self.now().isoformat()}
        if not ok:
            log.warning("ledger and broker differ: %s", out)
            self.store.event("ledger_mismatch", out, b.run_id)
        return out

    def _finalize_missed(self, now: datetime) -> None:
        """S77 (audit D8): after a restart with the market closed, the last session this run traded is finalised
        if nobody did: today's result, the ledger's close and the day-end jobs, as if the engine had been up."""
        run = self.run
        if run is None or run.get("status") not in ("running", "paused") or not run.get("started_on"):
            return
        day = self._last_closed_session(now)
        if day is None or day == self._finalize_checked or str(day) < str(run["started_on"]):
            return
        self._finalize_checked = day
        if self.store.select("daily_result", {"run_id": f"eq.{run['id']}", "session": f"eq.{day}"}):
            return
        b = self.ledger.book(run["id"])
        if b is not None and b.closes and b.closes[-1].session >= str(day):
            return
        open_utc, n_min = session_minutes(self.broker.calendar(str(day)), day)
        d = Day(run["id"], day, open_utc, n_min, self._start_equity())
        d.last_m = d.done_m = n_min - 1
        d.flattened = True
        self.day = d
        self._restore_session(d)
        self.store.event("session_finalized_late", {"day": str(day), "positions": list(self.pos)}, self._rid())
        self._sync_orders()
        self._finalize_day()

    def _last_closed_session(self, now: datetime) -> date | None:
        today = now.astimezone(_et()).date()
        for k in range(8):
            day = today - timedelta(days=k)
            cal = self.broker.calendar(str(day))
            if not cal:
                continue
            open_utc, n_min = session_minutes(cal, day)
            if now >= open_utc + timedelta(minutes=n_min):
                return day
        return None

    def _status(self, m: int) -> dict:
        d = self.day
        st = {
            "state": "trading" if self._may_enter(max(m, 0)) else "managing",
            "minute": m,
            "run_id": self._rid(),
            "level": self.level(),
            "positions": {f"{s}|{p.strategy}" if len(self.pos.on(s)) > 1 else s: p.status
                          for s, p in self.pos.items()},
            "entries_today": d.entries if d else 0,
            "halted": bool(d and d.halted),
            "account_clean": self.account_clean,
            "code_sha": self.code,
            "scan": dict(d.scan) if d else None,
        }
        if self.experiments:
            st["experiments"] = self.experiments  # #133: the dashboard's experiment label (heartbeat detail)
        self._daily_status(st)
        if self.run is None and d is not None and d.run_id:
            st["run_ended"] = d.run_id  # S74b (#137): today's run has ended; the engine only winds it down
        return st


def _refused(err: Exception) -> bool:
    """S74: the venue answered and said no (HTTP 4xx), as opposed to not answering (network error, 5xx: the
    adapter raises those without a 4xx status after its own retries)."""
    status = getattr(err, "status", None)
    return isinstance(status, int) and 400 <= status < 500


class ExDateSourceMissing(RuntimeError):
    """A multi-day rule on an engine without an ex-date dividend source (S78, #118: accrual at the ex-date)."""


def _holding(strategy: str | None):
    s = registry.get(strategy) if strategy else None
    return getattr(s, "holding", Holding.INTRADAY)


def _order_refused(err: Exception) -> bool:
    """S74: a 403 or 422 on an entry order: the venue refused it and no order exists. Told by the status the
    adapter's error carries, so this does not depend on which adapter raised it. One 422 is not a refusal: "client
    order id must be unique" says an order with our id IS at the venue (an earlier attempt got through and the
    adapter could not read it back). That stays a loop error, and the next start adopts the order."""
    body = str(getattr(err, "body", None) or err)
    return getattr(err, "status", None) in BROKER_REJECT_STATUS and "client_order_id" not in body


def _reject_reason(err: Exception) -> str:
    """S74: the venue's own words for a refused order. Alpaca answers {"code": ..., "message": ...}."""
    body = str(getattr(err, "body", None) or err)
    try:
        msg = json.loads(body).get("message")
    except (ValueError, AttributeError):
        msg = None
    return str(msg or body)[:160]


def _et():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


def _age_s(t: str | None, now: datetime) -> float:
    if not t:
        return 1e9
    try:
        ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return max(0.0, (now - ts).total_seconds())
    except ValueError:
        return 1e9


def _parse_ts(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _iso(v) -> str | None:
    return None if v is None else _parse_ts(v).astimezone(UTC).isoformat()


def _et_date(v) -> date | None:
    return None if v is None else _parse_ts(v).astimezone(_et()).date()


def _num(v):
    v = float(v)
    return None if not np.isfinite(v) else round(v, 6)


def new_idempotency_key() -> str:
    return uuid.uuid4().hex
