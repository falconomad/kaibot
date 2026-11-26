"""The engine on Alpaca paper (or `SimBroker`), one paper run at a time. It knows no rule by name.

Since M10 1.19 (#444) this file is the multi-day book's engine and nothing else: the state of a run, its ledger and
reconciliation, the commands, the fail-closed halts, the book's run end and the full flatten. Its minute is the
book's own small loop (engine/book_loop.py):

    clock -> sync -> send -> reconcile -> risk checks -> ledger

The intraday path (bracket entries of rules that are flat by the close, their stops and exits, the agent room,
the shadow rules, the minute scan of the intraday universe) lives in engine/intraday_legacy.py as `IntradayEngine`, a
subclass that adds its steps around the same ones. `Engine(..., intraday=False)` is this class: what the live
process builds while `config/profiles.yaml` has `intraday.enabled: false`, and it never imports that module.
Every other `Engine(...)` call (tests, replays, SimBroker research) builds `IntradayEngine`, the engine as it
always was.

Since #694 (audit 2, 5.14) `Engine`'s methods are split by concern into mixins, each in its own module and moved
there unchanged: engine/book_risk.py (`BookRisk`: the book's P&L, its run-end and band checks) and
engine/flatten.py (`Flatten`: the full flatten and the multi-day sleeves' sales), engine/commands.py
(`Commands`: the owner's commands and the run's status), engine/bookkeeping.py (`Bookkeeping`: the ledger, the
session's close and the reconciliation with the broker) and engine/corporate.py (`CorporateActions`: splits,
spin-offs, cash mergers, dividends and the venue's fees). What they share
(the logger, the limits, `Pos`, `Day`, the time helpers) is in engine/core_common.py; core.py imports it back.

Books (S77): every order call is journaled before it is sent and every fill, dividend, split and session close
goes to the ledger (engine/ledger.py), a fold over the local journal (engine/journal.py). Start equity is
yesterday's mark-to-market close; a restart with the market closed finalises the last session.

Safety:
- On startup, positions the engine doesn't know about block all entries (account not clean). Multi-day sleeves (the
  ledger's, S80 #120) are known positions and keep their protective stops over the close. They need the daily
  path: with sleeves in the ledger and no daily path (KAIBOT_MULTIDAY off) the engine does not start (#445 step 2).
- The run's pot (onboarding amount) caps sizing and buying power, whatever the paper account holds.
- Fail closed (S16/S18): unknown open orders or positions, position drift against the broker, three loop errors
  in a row, an event calendar that has run out, or three daily stops in ten sessions all stop new entries.
  Nothing here ever adds risk: protection stays at the broker and sales keep going out.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

from kaibot.broker.protocol import Broker
from kaibot.engine import book_loop
from kaibot.engine.book_risk import BookRisk
from kaibot.engine.bookkeeping import Bookkeeping
from kaibot.engine.clock import session_minutes
from kaibot.engine.commands import Commands, run_refusal  # noqa: F401  (run_refusal: `core.run_refusal`)
from kaibot.engine.config import ROOT
from kaibot.engine.core_common import (  # noqa: F401  (#694: moved there; imported back for `core.<name>`)
    BREACH_CONFIRM_S,
    CALENDAR_ALERT_DAYS,
    CANCEL_UNCONFIRMED,
    CASH_TOLERANCE_PCT,
    CASH_TOLERANCE_USD,
    CLEAN_TICKS_TO_CLEAR,
    CLOCK_SKEW_MAX_S,
    CLOSE_MAX_S,
    COMMAND_KINDS,
    CONFIG_UNREADABLE,
    FIRST_ENTRY_M,
    FLATTEN_BUDGET_S,
    FLATTEN_TRIES,
    INTRADAY_ONLY,
    LEVEL_NAMES,
    MAX_ERROR_STREAK,
    MD_AGREE_PCT,
    MD_BAR_DEAD_MIN,
    MD_BAR_MAX_AGE_MIN,
    MD_BROKER_MAX_AGE_S,
    MD_FAR_PCT,
    MD_UNCONFIRMED_ALARM_MIN,
    PAUSE_AFTER_LOSSES,
    PAUSE_MINUTES,
    RECHECK_MINUTES,
    RULES_RETRY_S,
    RUN_AMOUNT_MAX_USD,
    RUN_AMOUNT_MIN_USD,
    STOPS_TO_PAUSE,
    STOPS_WINDOW_DAYS,
    DailyPathMissing,
    Day,
    ExDateSourceMissing,
    FlattenIncomplete,
    Pos,
    Positions,
    _brief,
    _clock_ts,
    _close_ts,
    _et,
    _et_date,
    _holding,
    _iso,
    _num,
    _parse_ts,
    _posted_since,
    _refused,
    log,
    new_idempotency_key,
)
from kaibot.engine.corporate import CorporateActions
from kaibot.engine.dividends import receivable
from kaibot.engine.events import from_fomc_dates, in_blackout
from kaibot.engine.execution import flatten_at_close
from kaibot.engine.flatten import Flatten
from kaibot.engine.journal import Journal, JournalCorrupt, canon
from kaibot.engine.ledger import (
    LEDGER_KINDS,
    Ledger,
)
from kaibot.engine.reconcile import (
    Difference,
    Reconciler,
    Reconciliation,
    apply_unbooked,
    unbooked_plan,
)
from kaibot.engine.userconfig import ConfigError, Rules, defaults, tighten
from kaibot.evaluation import gate_report
from kaibot.risk import book_stop as bstop
from kaibot.risk.gate import (
    RiskPolicy,
    intraday_equity,
    run_drawdown_reached,
)
from kaibot.strategies import registry
from kaibot.strategies.base import Holding, Strategy, new_scan, note_scan

__all__ = ["Engine", "FlattenIncomplete", "new_scan", "note_scan"]  # the scan counters live in strategies/base.py

# #461 (decision 17 section 7 step 3): the multi-day book's stop switch and band tables (risk/book_stop.py), read
# once per engine at its first book check, so an edit takes effect at the next restart. Missing or invalid: mode fixed.
BOOK_STOP_PATH = bstop.DEFAULT_PATH
BOOK_STOP_ROOT = ROOT

def _fingerprint() -> str:
    try:
        return gate_report.fingerprint(ROOT, gate_report.Bar.load(ROOT).cfg["fingerprint"])
    except Exception:
        log.exception("strategy fingerprint unavailable")
        return "unknown"


def code_sha() -> str:
    from kaibot.common import switches  # M10 1.23 (#448): as loaded at start

    sha = switches.get("KAIBOT_CODE_SHA")
    if sha:
        return sha
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError, ValueError) as e:  # #694: no git, not a checkout, undecodable
        log.info("code version: git rev-parse failed (%s): reported as unknown", type(e).__name__)
        return "unknown"


# #694: the methods of each mixin are in its own module (see the module's docstring)
class Engine(BookRisk, Flatten, Commands, Bookkeeping, CorporateActions):
    intraday = False  # M10 1.19: True on engine/intraday_legacy.IntradayEngine only
    CONTAIN_DRIFT_READ = True  # audit 2 5.10: a failed positions read of the drift check holds buys, not the minute

    def __new__(cls, *args, intraday: bool | None = None, **kw):
        """M10 1.19 (#444): `Engine(..., intraday=False)` is the multi-day book's engine, this class: what the
        live process builds while `intraday.enabled` is off in config/profiles.yaml. Every other call builds
        engine/intraday_legacy.IntradayEngine, imported here and only then: an engine as it always was, for tests,
        replays and SimBroker research (a research run hands its rule to an engine whose levels list none)."""
        if cls is Engine and intraday is not False:
            from kaibot.engine.intraday_legacy import IntradayEngine

            cls = IntradayEngine
        return super().__new__(cls)

    def __init__(
        self,
        broker: Broker,
        market,
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
        recent_actions=None,
        market_alarm=None,
        intraday: bool | None = None,
    ):
        self.broker, self.market, self.store = broker, market, store
        if not self.intraday:  # the book's engine: a level that lists an intraday rule would never run it
            listed = sorted({n for names in (strategies or {}).values() for n in names
                             if n not in registry.RESEARCH_ONLY})
            if listed or not strategies:
                raise ValueError("an engine without the intraday path (intraday=False) needs a level map that "
                                 f"lists multi-day rules only; got {listed or 'no map'}")
        # S103 (#377): (session, now) -> the alarm's flags, written as a `market_alarm` event at session start. A
        # notice only: nothing in the engine reads it back, so it cannot change an order (None = not written).
        self.market_alarm = market_alarm
        self.policies, self.fomc, self.process, self.now = policies, fomc, process, now
        self.calendar_until = calendar_until  # last date the event calendar covers; None = not checked
        self.clock_skew: dict | None = None  # M9 1.10: set while the host clock and the broker's disagree
        self.events = events if events is not None else from_fomc_dates(fomc)  # {day: [(event, minute)]}
        self.strategies = strategies or {}  # {level: [live strategies]}; missing level -> registry.DEFAULT_LIVE
        for names in self.strategies.values():  # #161: a multi-day rule never loads on the live path
            registry.refuse_multi_day(names)
        # S76: which registered shadow strategies run (None = all of them; the research command picks one)
        self.shadow_only: tuple[str, ...] | None = None
        # S34: the agent room (None = rules-only). It can only veto or halve a candidate the rules approved.
        self.roomflow = self._roomflow(room, pack_fn)
        self.rules_extra = rules_extra  # extra rules-only veto (S37 pump risk): (engine, symbol, m) -> str | None
        self.kill = None  # set by a fail-closed halt; blocks entries until the next session (#446: the switch's hold)
        # Issue #81: the cross-machine lease (engine/lease.py), set by __main__. None = not enforced (tests,
        # replays). When set, entries need a lease that is provably ours right now.
        self.lease = None
        self.error_streak = 0
        self.clean_ticks = 0  # M9 1.7: ticks in a row that did not raise while the `engine_errors` kill is set
        self._drift_seen: dict = {}
        self._positions_unread: str | None = None  # audit 2 5.10: why the drift check's last read failed
        self.run: dict | None = None
        self.day: Day | None = None
        self.pos = Positions()
        self.experiments: list[dict] = []  # #133: set at each session start (_experiments)
        # S77: the local journal (the store's own when it journals, else an in-memory one) and the ledger folded
        # from it: positions per (strategy, symbol), cash, the mark-to-market close of every session.
        self.journal = journal or getattr(store, "journal", None) or Journal()
        self.ledger = Ledger.fold(self.journal.entries(kinds=LEDGER_KINDS))
        # S77: corporate actions for a session: research passes the daily store's table (day -> [Action]); None
        # reads the broker's non-trade activities (Alpaca: dividends DIV*, splits SPLIT) when a position is held
        self.corporate_actions = corporate_actions
        # S78 (#118): live, dividends are accrued at the ex-date (engine/dividends.ExDateDividends: (day, symbols)
        # -> ex-dividends) and Alpaca's later payment is matched to the accrual; None: booked when paid
        self.ex_dividends = ex_dividends
        # #266: (day, symbols) -> recent spin-offs and symbol changes (engine/corp_guard.RecentActions), the sourced
        # record by which the multi-day path adopts what Alpaca did to a held position; None: nothing adopted
        self.recent_actions = recent_actions
        # S78: the ledger against the broker (engine/reconcile.py); accepted offsets fold from the journal
        self.recon = Reconciler(lambda: self._recon_inputs(), self.journal)
        self._recon_ts: datetime | None = None  # #439: when the last check ran (heartbeat `reconcile.ts`)
        self._recon_refused: dict | None = None  # the last refused accept_reconcile command, until it is moot
        self._finalize_checked: date | None = None
        self.account_clean = True
        self.code = code_sha()
        self.fingerprint = _fingerprint()  # S38: strategy-code hash; trades count toward the gate only on frozen code
        self.peak: float | None = None
        self.on_progress = lambda status: None
        self.monotonic = time.monotonic  # S74: the flatten's time budget (a fake clock in tests)
        self._rules_tried = -math.inf  # M10 1.25 (#536): the last read of an unreadable session config
        self.sleep = time.sleep  # QA-Ops (#282): the short wait for a cancel inside a flatten (a no-op in tests)
        self._flatten_slow: set[str] = set()  # S74: symbols the last flatten could not close; tried last next time
        # M9 1.2: a `flatten_and_stop` / `new_run` command waiting for a flat book: id -> why no position is opened
        self._cmd_waiting: dict[str, str] = {}
        self._cmd_note: dict[str, str] = {}  # id -> the last logged reason it waits (logged once per change)
        self._cmd_aged: dict[str, tuple[str, ...]] = {}  # QA-Ops on #544: id -> the aged unbooked orders last raised
        self._closed_seen: datetime | None = None  # when the broker's clock last said "closed" (_market_open_now)
        # M9 1.1: the book's run end. A reading above the peak waiting for its second (run, when, equity); the last
        # journalled reason the check was skipped; the splits booked today (day, symbols)
        self._peak_try: tuple[int, datetime, float] | None = None
        self._book_skip: tuple | None = None
        self._splits_booked: tuple[str, set[str]] = ("", set())
        # QA-Ops (#527): holdings measured at their last close mark: since when (run, symbol -> first check), and
        # the ones already named in an event today (run, day, symbols)
        self._unconf_since: tuple[int | None, dict[str, datetime]] = (None, {})
        self._unconf_alarmed: tuple[int | None, str, set[str]] = (None, "", set())
        # S80 (#120): sales of multi-day sleeves sent by a full flatten: venue order id -> {run_id, keys, reason}
        self._md_exits: dict[str, dict] = {}
        # M9 1.13 (#438): venue order ids of orders this engine placed outside the order manager (intraday entries,
        # their legs and exits; full-flatten sales), kept in the journal so their fills are never taken for fills on
        # unknown orders (`foreign_fill`) once the position is gone, also after a restart. Folded at the first use.
        self._own_ids: set[str] | None = None
        self._bpos: dict = {}  # the broker's positions as last read (drift check): a price for multi-day sleeves
        self._bpos_ts: datetime | None = None
        # #206: the live daily path (engine/live_daily.LiveDaily: multi-day rules through engine/daily.py's decision
        # core), built by `daily(self)`. None (the default, and always with KAIBOT_MULTIDAY off): the engine is the
        # intraday engine it was, line for line.
        if daily is not None and not registry.multi_day_enabled():
            raise registry.RefusedStrategy(f"the live daily path needs {registry.MULTIDAY_FLAG}=on")
        self.daily = daily(self) if daily is not None else None
        self._closed_cache: tuple | None = None
        self._breach: tuple[int, datetime] | None = None  # #211: (run, first check) of an unconfirmed book breach
        self._breach_rid: int | None = None  # the run whose journalled breach `_breach` was read for
        self.book_line: dict | None = None  # #229: the multi-day book's peak and stop line (heartbeat)
        self._book_peak_ts: str | None = None
        self._book_start: tuple[int, str] | None = None  # #461: (run, ts of its first journalled peak)
        # #461: (config, (rule, level) -> table or why not, why the file was not usable), read at the first check
        self._bstop: tuple[bstop.BookStopConfig, dict, str | None] | None = None
        self._band_alerted: tuple[int, str] | None = None  # #461: (run, day) of the last band alert

    # ================= lifecycle =================
    def startup(self) -> None:
        self.run = self.store.active_run()
        self._restore_positions()
        self._attach_ledger()
        self._require_daily_path()  # M10 1.20 step 2 (#445): sleeves without the daily path: no start
        self.recover()  # M10 1.20 step 3 (#445): the one start-up recovery routine
        self.check_account()
        self._startup_reconcile("startup")
        self.store.event(
            "engine_start",
            {"code_sha": self.code, "fingerprint": self.fingerprint, "run_id": self.run and self.run["id"],
             "positions": list(self.pos)},
            self._rid(),
        )

    def recover(self) -> None:
        """M10 1.20 step 3 (#445): the engine's one start-up recovery routine. It runs once, from `startup`, after
        the ledger is attached and the daily-path check has passed, and before the account guard and the
        reconciliation. Its parts run in this order, each exactly as it ran from `startup` before:

        1. `_rebuild_md_exits` (#252): the flatten sales journalled as sent and not yet booked, so they are booked
           when they fill; an intent without its `md_exit` is looked up at the venue by its client id.
        2. `_restore_cmd_waiting` (M9 1.2, #525): a flatten command that was waiting still blocks entries.
        3. `_recover_intraday` (M10 1.19): the intraday path's orphaned entries (nothing without that path).
        4. The daily path's start-up (#206): its order manager recovers its orders from the venue, the sleeves'
           stops are adopted and the fills since the last start are booked (`LiveDaily.startup`; a failure is an
           event, never a stop: `_daily_step`).

        A failure of parts 1 and 2 stops the start, as before; parts 3 and 4 log and go on, as before.
        `tests/unit/test_one_recovery.py` checks that nothing else calls them."""
        self._rebuild_md_exits()
        self._restore_cmd_waiting()
        if self.intraday:  # M10 1.19: the intraday path's orphaned entries
            self._recover_intraday()
        self._daily_step("startup", lambda: self.daily.startup())

    def _require_daily_path(self) -> None:
        """M10 1.20 step 2 (#445): fail closed when the run's multi-day sleeves exist and the daily path does not.
        Only reads the ledger: nothing is sent, journalled or written before it."""
        refusal = self.daily_path_refusal(self._rid())
        if refusal is not None:
            raise refusal

    def daily_path_refusal(self, run_id) -> DailyPathMissing | None:
        """The refusal `startup` would raise for run `run_id` (exit code 5), or None when it would start. Reads only
        the ledger folded from the journal, so `--check` asks it too, before any start (#643 follow-up)."""
        if self.daily is not None:
            return None
        held = self._md_sleeves(run_id)
        if not held:
            return None
        return DailyPathMissing(run_id, sorted({sym for _, sym in held}), registry.multi_day_enabled())

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

    def _own(self) -> set[str]:
        """M9 1.13: the remembered ids, folded from the journal the first time they are asked for."""
        ids = getattr(self, "_own_ids", None)
        if ids is None:
            ids = self._own_ids = {str(i) for *_, b in self.journal.entries(kinds=("own_order_ids", "md_exit"))
                                   for i in (b.get("ids") or [b.get("order_id")]) if i}
        return ids

    def _remember_ids(self, *ids) -> None:
        """M9 1.13: these venue order ids are the engine's own (journalled once each)."""
        own = self._own()
        new = sorted({str(i) for i in ids if i} - own)
        if new:
            self.journal.append("own_order_ids", {"ids": new})
            own.update(new)

    def own_order_ids(self) -> set[str]:
        """Every venue order id the engine knows as its own outside the order manager: the open intraday positions'
        orders and legs (remembered here, so a leg cancelled or replaced later stays known), the full-flatten sales
        and everything remembered before."""
        self._remember_ids(*(getattr(p, a, None) for p in self.pos.values()
                             for a in ("order_id", "stop_order_id", "target_order_id", "exit_order_id")),
                           *self._md_exits)
        return self._own()

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
        # M9 1.13 (#438, audit D2): the order manager's view, so the fill checks run live (a fill on an order the
        # engine does not know, an order with more filled shares than booked). None without a daily path.
        om = self.daily.recon_view() if self.daily is not None else None
        return {"ledger_qty": {s: q for s, q in b.qty_by_symbol().items() if q and s not in unsettled},
                "om": om, "broker_positions": bpos, "ledger_cash": b.cash, "ledger_cash0": b.amount,
                "broker_cash": float(cash) if cash is not None and b.broker_cash0 is not None else None,
                "broker_cash0": b.broker_cash0,
                "receivable": receivable(self.journal.entries(kinds=("div_accrual", "div_paid"))),
                "equity": b.amount, "now": self.now()}

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
        self._recon_ts = self.now()
        if self.recon.last.diffs:
            aged = sorted(str(d.detail.get("coid")) for d in self.recon.last.diffs
                          if d.kind == "unbooked" and d.detail.get("aged"))
            action = "entries blocked until the book matches the broker or the owner accepts the difference"
            if aged:  # #529: an activity that has not come by the day after: the owner books it or accepts it
                action += "; a fill no activity reported after its day: book it or accept it on the Engine page"
            self.store.event("reconcile_blocked", {"when": when, **self.recon.last.summary(), "action": action,
                                                   **({"unbooked_aged": aged} if aged else {})}, self._rid())

    def _accept_reconcile(self, pl: dict) -> None:
        """#439 (M9 1.14): the owner's `accept_reconcile` command. The dashboard sends what its screen showed
        (`shown`); the reconciler accepts only that, read again at the venue. A refusal is an event and stays in
        the heartbeat (`reconcile.refused`) until an acceptance or a clean check, so the owner sees why buys are
        still stopped."""
        try:
            self._accept_rows(pl)
        except Exception as e:
            if self.recon.last.diffs:  # nothing open (healed, or a second command after an acceptance): no event
                self._recon_refused = {"ts": self.now().isoformat(), "why": str(e)[:200]}
                self.store.event("reconcile_blocked", {"when": "accept_refused", **self.recon.last.summary(),
                                                       "refused": str(e)[:200],
                                                       "action": "entries stay blocked: look at the difference again"},
                                 self._rid())
            raise
        self._recon_refused = None
        self._startup_reconcile("accepted")

    def _accept_rows(self, pl: dict) -> None:
        """#529: `shown` rows of kind "unbooked" (an order whose filled shares no activity booked, after its day)
        carry the owner's choice, "book" or "accept" (reconcile.unbooked_plan): all of them are read again at the
        venue before anything is booked or waived. Such a command carries only those rows: a booking changes the
        books' shares and cash, so the other differences on the same screen heal or change with it, and are
        accepted afterwards, as before (#439), for what is left."""
        reason, shown = str(pl.get("reason") or ""), pl.get("shown")
        rows = list(shown) if isinstance(shown, list) else None
        un = [x for x in rows or () if isinstance(x, Mapping) and x.get("kind") == "unbooked"]
        if not un:
            self.recon.resolve(reason, by="owner (command)", shown=shown)
            return
        if not reason.strip():
            raise ValueError("a resolution needs a reason")
        if len(un) != len(rows):
            raise ValueError("nothing accepted: decide the unbooked fills first, then accept what is left")
        om = getattr(self.daily, "om", None) if self.daily is not None else None
        plan = unbooked_plan(om, self.recon.last.diffs, un, self.now())
        done = apply_unbooked(om, plan, by="owner (command)", reason=reason)
        self.journal.append("reconcile_resolved", {"reason": reason.strip(), "by": "owner (command)", "offsets": [],
                                                   "foreign": [], "unbooked": done})
        log.warning("unbooked fills resolved by the owner: %s", done)

    def _recon_status(self) -> dict:
        """The heartbeat's `reconcile` block (#439): the last check's differences, as the dashboard shows them."""
        st = self.recon.status()
        if st["ok"]:
            self._recon_refused = None
        elif self._recon_refused:
            st["refused"] = self._recon_refused
        if self._recon_ts is not None:
            st["ts"] = self._recon_ts.isoformat()
        return st

    def _recheck_reconcile(self, m: int | None = None, force: bool = False) -> None:
        """While entries are blocked by a difference: check again every RECHECK_MINUTES (a late activity heals)."""
        if self.recon.last.diffs and (force or (m is not None and m % RECHECK_MINUTES == 0)):
            self._startup_reconcile("recheck")

    def _reconciled(self) -> bool:
        return not self.recon.last.diffs

    # ================= fail-closed halts =================
    def _halt_entries(self, kind: str, reason: str, detail: dict | None = None) -> None:
        # QA-Ops (#483): `engine_errors` is the one kill that clean ticks clear (`on_tick_ok`), so it must not hide
        # another halt raised while it is set (drift, broker rejects, the LLM budget): that halt takes over, with
        # its own row and event, and stays until the next session as it always did.
        if self.kill and not (self.kill == "engine_errors" and kind != "engine_errors"):
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
        self.clean_ticks = 0
        if self.error_streak >= MAX_ERROR_STREAK:
            self._halt_entries("engine_errors", f"{self.error_streak} loop errors in a row: {str(err)[:120]}")

    def on_tick_ok(self) -> None:
        """Called by the main loop after a tick that did not raise (one a minute). M9 1.7 (audit M5): the
        `engine_errors` kill is cleared after CLEAN_TICKS_TO_CLEAR such ticks in a row, so a minute of API trouble
        in the morning no longer blocks the 15:50 buys. Its halt row stays for the record; a `halt_cleared` event
        says why. A failed pass in between starts the count again. No other kill is ever cleared here."""
        self.error_streak = 0
        if self.kill != "engine_errors":
            self.clean_ticks = 0
            return
        self.clean_ticks += 1
        if self.clean_ticks < CLEAN_TICKS_TO_CLEAR:
            return
        self.kill, self.clean_ticks = None, 0
        log.warning("halt engine_errors cleared after %d clean ticks in a row", CLEAN_TICKS_TO_CLEAR)
        self.store.event("halt_cleared", {"kind": "engine_errors", "clean_ticks": CLEAN_TICKS_TO_CLEAR,
                                          "day": str(self.day.day) if self.day else None,
                                          "why": f"{CLEAN_TICKS_TO_CLEAR} clean ticks in a row"}, self._rid())

    def lease_ok(self) -> bool:
        """Issue #81: no lease object means the lease is not enforced; otherwise it must be verified and fresh."""
        return self.lease is None or bool(self.lease.ok)

    def _check_drift(self) -> None:
        """S18: the engine's book must match the broker's. Any difference stops new entries.

        Audit 2, 5.10 (the 1.5 residual): a read of the broker's positions that fails no longer fails the minute
        (which kept the whole send, sales included, behind this step while the endpoint was down). It is contained
        here: the `positions` hold stops new buys until a read works again (fail closed for buys), sales and stops
        go on, and the minute completes. One `engine_error` event per spell; the hold is let go at the next read.
        The retired intraday path (`CONTAIN_DRIFT_READ` False) still fails its minute, as before."""
        try:
            broker_pos = self.broker.positions()
        except Exception as e:  # noqa: BLE001 - contained: buys held (below), the tick goes on
            if not self.CONTAIN_DRIFT_READ:  # the retired intraday path (engine/intraday_legacy.py): as before
                raise
            why = f"broker positions unreadable ({type(e).__name__}: {str(e)[:120]}): no new buys"
            if self._positions_unread is None:
                log.error("drift check: %s", why)
                self.store.event("engine_error", {"step": "drift_check", "error": why[:300]}, self._rid())
            self._positions_unread = why
            return
        if self._positions_unread is not None:
            log.warning("drift check: the broker's positions read again: buys allowed")
            self._positions_unread = None
        self._bpos = broker_pos
        self._bpos_ts = self.now()  # #384: the time of the read, for the minute row's `mark_ts` of a broker price
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
        if step == "send" and self.clock_skew is not None:
            return  # M9 1.10: the send window is read off the host clock, which disagrees with the broker's
        try:
            call()
        except Exception as e:
            self._journal_intact(cause=e)  # #551: a damaged journal stops the tick here, at once
            why = f"{type(e).__name__}: {e}"[:200]
            seen = getattr(self, "_daily_errors", None)
            if seen is None:
                seen = self._daily_errors = {}
            if seen.get(step) == why:  # QA-Ops: the same failure again (every tick): logged, one event only
                log.warning("daily path: %s still failing: %s", step, why)
                return
            seen[step] = why
            log.exception("daily path: %s failed", step)
            self.store.event("multi_day_error", {"step": step, "error": why}, self._rid())
        else:
            self._journal_intact()  # #551: damage the daily path read and caught itself stops the tick too
            errs = getattr(self, "_daily_errors", None)
            if errs:
                errs.pop(step, None)  # recovered: the next failure is an event again

    def _md_symbols(self) -> set[str]:
        """#206: symbols the multi-day book holds or has a working order of the daily path in (empty without the
        daily path: the intraday path is unchanged)."""
        if self.daily is None:
            return set()
        return {sym for _, sym in self._md_sleeves()} | self.daily.working_symbols()

    def _daily_status(self, st: dict) -> dict:
        """#206: the heartbeat's `multi_day` block (`LiveDaily.status`), e.g. "no decision today: daily store not
        refreshed". Absent without the daily path. #211: with KAIBOT_MULTIDAY=on every heartbeat (closed, idle,
        preparing, after the close) also carries `experiments`, so the dashboard's label and limits never vanish.
        #439: every heartbeat also carries `reconcile` (the last ledger/broker check) once a check has run."""
        if self.recon.checked:
            st["reconcile"] = self._recon_status()
        if registry.multi_day_enabled() and "experiments" not in st:
            if not self.experiments:  # e.g. a start with the market closed: no session start yet
                self.experiments = self._experiments()
            if self.experiments:
                st["experiments"] = self.experiments
        if self.daily is not None:
            try:
                st["multi_day"] = self.daily.status(self.now().astimezone(_et()).date())
                if self.book_line is None or self.book_line.get("run_id") != self._rid():
                    self._book_line_marked()  # a start with the market closed: the line at the close marks
                if self.book_line is not None and self.book_line.get("run_id") == self._rid():
                    st["multi_day"]["book_line"] = dict(self.book_line)  # #229
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

    def _book_stop(self) -> tuple[bstop.BookStopConfig, dict, str | None]:
        """#461: config/book_stop.yaml, read once (fail closed: a missing or invalid file is mode fixed)."""
        if self._bstop is None:
            try:
                cfg, why = bstop.load_or_fixed(BOOK_STOP_PATH)
            except Exception as e:  # QA-Ops on #652: never "no stop", even if the loader itself regresses
                cfg, why = bstop.FIXED, f"book_stop.yaml loader failed, the fixed line: {type(e).__name__}: {e}"
            if why is not None:
                log.error("the multi-day book's stop: %s", why)
            self._bstop = (cfg, {}, why)
        return self._bstop

    def _band_table(self, rules: Sequence[str], level: str) -> tuple[bstop.BandTable | None, str | None]:
        """#461: the committed band table of the book (one rule) at the run's level, read once; or None and why not.
        Never raises: a missing or unreadable file means the fixed line."""
        cfg, tables, _ = self._book_stop()
        if len(rules) != 1:
            return None, f"mode band, but the book has {len(rules)} rules and a table is per rule: the fixed line"
        key = (rules[0], level)
        if key not in tables:
            try:
                t = bstop.table_for(cfg, *key, root=BOOK_STOP_ROOT)
                tables[key] = t if t is not None else (
                    f"mode band, but no band table is committed for {key[0]} at {key[1]}: the fixed line")
            except Exception as e:
                tables[key] = f"mode band, but the band table of {key[0]} at {key[1]} is unusable ({e}): the fixed line"
                log.error("the multi-day book's stop: %s", tables[key])
        t = tables[key]
        return (t, None) if isinstance(t, bstop.BandTable) else (None, t)

    def entry_block(self) -> str | None:
        """S80: why no new position may be opened now (None: it may), for the multi-day target path as for the
        intraday entries: no running run, the account not clean, a fail-closed halt, the lease, the daily stop,
        an invalid owner config. Sales and protective stops are never blocked. The words of `entry_reason`."""
        r = Engine.entry_reason(self)  # also for a duck-typed engine (unit tests)
        return r[1] if r is not None else None

    def entry_reason(self) -> tuple[str, str] | None:
        """#446: `entry_block` as (code, words): the code is short and stable (engine/state.py `BLOCK_CODES`, for the
        heartbeat and the dashboard), the words are `entry_block`'s, unchanged. No broker call; with
        KAIBOT_DAILY_CHECKS_BLOCK on, the daily-data check journals a new block once a day (`LiveDaily.data_block`).
        Step 4: every block is a hold on the trading switch (`_hold_entry_blocks`), and this reads the first one held
        (engine/state.py `ENTRY_SOURCES`): the one place `entry_block` comes from."""
        from kaibot.engine.state import ENTRY_SOURCES, HALT

        Engine._hold_entry_blocks(self)  # also for a duck-typed engine (unit tests)
        got = Engine.trading.fget(self).first(ENTRY_SOURCES)
        if got is None:
            return None
        source, code, why = got
        if source == HALT:
            return "halted", f"halted: {why}"
        return code or source, why

    def _hold_entry_blocks(self) -> None:
        """#446 step 4: take or let go of each entry block's hold on the trading switch, reading each condition as
        `entry_block` always read it, in its order, stopping at the first that blocks (so the daily-data check is
        asked only when nothing before it blocks, as before). The holds after that one are let go (not known now).
        The fail-closed halt is its trigger's own hold (`kill`); it is only read here. Also the first `halted`
        trigger: the lost lease (`_hold_lease_lost`). Every entry hold is `reducing_only`: sales and protective
        stops are never blocked by them."""
        from kaibot.engine.state import ENTRY_SOURCES, HALT, TradingState

        sw = Engine.trading.fget(self)
        Engine._hold_lease_lost(self, sw)
        r = TradingState.REDUCING_ONLY
        d = self.day

        def run():
            if self.run is None or self.run.get("status") != "running":
                st = (self.run or {}).get("status")
                return ("no_run" if self.run is None else "paused" if st == "paused" else "run_not_running",
                        "no running run")
            return None

        def command():  # M9 1.2: "Flatten and stop" (or a new run) is selling the book
            return ("command_selling", next(iter(self._cmd_waiting.values()))) if self._cmd_waiting else None

        def account():
            return None if self.account_clean else ("account_not_clean", "account not clean")

        def reconcile():
            # QA-Ops (#522): a fill difference alone (a fill on an unknown order, a fill not booked yet) stops buys in
            # its own stock (`Reconciler.allows`, asked per order by the daily path), not the whole rebalance
            if not self._reconciled() and self.recon.blocks_every_buy():
                return "reconcile_difference", "ledger and broker differ (reconciliation)"
            return None

        def halt():  # the fail-closed halt: `kill` is the switch's own `halt` hold (step 2)
            kind = self.kill
            if kind and sw.held(HALT) != kind:  # a duck-typed engine's plain `kill` (unit tests)
                sw.hold(HALT, r, kind)
            elif not kind:
                sw.release(HALT)  # nothing to let go on an Engine: its `kill` is the hold
            return ("halted", kind) if kind else None

        def lease():
            return None if self.lease_ok() else ("lease_unverified", "lease unverified")

        def clock():  # M9 1.10
            return ("clock_skew", self._clock_block()) if self.clock_skew is not None else None

        def daily_stop():
            return ("daily_stop", "daily stop") if d is not None and d.halted else None

        def config():
            return ("config_refused", f"config refused: {d.refused}") if d is not None and d.refused else None

        def data_check():
            # M12 3.11 (#473): the daily-data market checks (SPY's bar late, the universe log stale, ...): None unless
            # KAIBOT_DAILY_CHECKS_BLOCK is on (the daily path's `entry_checks`); the text starts "data check <code>"
            fn = getattr(getattr(self, "daily", None), "data_block", None)
            if fn is not None and getattr(self.daily, "entry_checks", None) is not None:
                try:
                    why = fn()
                except Exception as e:  # noqa: BLE001 - fail closed (LiveDaily.data_block never raises; belt and braces)
                    return "data_check", f"data check checks_failed: {type(e).__name__}"
                if why:
                    return "data_check", why
            return None

        def positions():  # audit 2 5.10: the drift check could not read the broker's positions
            why = getattr(self, "_positions_unread", None)
            return ("positions_unread", why) if why else None

        checks = {"run": run, "command": command, "account": account, "reconcile": reconcile, HALT: halt,
                  "lease": lease, "clock": clock, "daily_stop": daily_stop, "config": config, "data_check": data_check,
                  "positions": positions}
        for i, source in enumerate(ENTRY_SOURCES):
            hit = checks[source]()
            if hit is None:
                if source != HALT:
                    sw.release(source)
                continue
            if source != HALT:
                sw.hold(source, r, hit[1], code=hit[0])
            for later in ENTRY_SOURCES[i + 1:]:
                if later != HALT:
                    sw.release(later)
            return

    def _hold_lease_lost(self, sw) -> None:
        """#446 step 4, the first `halted` trigger: held while the engine lease is lost and the engine's broker is
        fenced by that lease (engine/fence.FencedBroker). Then the fence already refuses every order call, cancels
        included (`EngineLease.fence` raises LeaseLost), and the main loop stops: the gate refuses the same calls
        before the fence, a cancel still reaches the fence and is refused there. Let go when the lease is not lost."""
        from kaibot.engine.state import LEASE_LOST, TradingState

        lease = getattr(self, "lease", None)
        broker = getattr(self, "broker", None)
        if lease is not None and getattr(lease, "lost", False) is True and getattr(broker, "lease", None) is lease:
            sw.hold(LEASE_LOST, TradingState.HALTED, "the engine lease is no longer ours: no order is sent")
        else:
            sw.release(LEASE_LOST)

    @property
    def trading(self):
        """M10 1.21 (#446) step 2: the trading switch the triggers set (engine/state.py `TradingSwitch`)."""
        from kaibot.engine.state import TradingSwitch

        sw = self.__dict__.get("_trading_switch")
        if sw is None:  # also for an Engine made without __init__ (some unit tests)
            sw = self.__dict__["_trading_switch"] = TradingSwitch()
        return sw

    @property
    def kill(self) -> str | None:
        """The fail-closed halt's kind (None: none): the trading switch's `halt` hold (#446 step 2). Setting it holds
        the engine `reducing_only` (sales and stops still go, as before); setting None releases the hold."""
        return self.trading.held("halt")

    @kill.setter
    def kill(self, kind: str | None) -> None:
        from kaibot.engine.state import TradingState

        if kind:
            self.trading.hold("halt", TradingState.REDUCING_ONLY, kind)
        else:
            self.trading.release("halt")

    def trading_state(self):
        """M10 1.21 (#446): the one trading state. The strictest of the trading switch's holds (step 2) and the state
        derived from `entry_block` (step 1): a `halted` hold wins, then any entry block (`reducing_only`), else the
        switch's own state. Reads only. The order path's gate asks it before every order call (step 3)."""
        from kaibot.engine.state import TradingState, from_entry_block

        # step 4: entry_block first (it takes and lets go of the holds, the lost lease's too), then the switch
        derived = from_entry_block(self.entry_block())
        held = self.trading.state()
        if held.state is TradingState.HALTED:
            return held
        return derived if derived.state is not TradingState.ACTIVE else held

    def trading_detail(self) -> dict:
        """#446 (E1 of docs/design/ui_after_engine_v3.md; #604, #626's follow-up): what the heartbeat's detail
        carries about trading, on every beat. `trading_state`: active, reducing_only or halted. `entry_block`: why
        no new position may be opened now as a short code (engine/state.py `BLOCK_CODES`), or None, with its words
        in `entry_block_why`. Only when nothing in the engine blocks: `market_filter_off` when the latest send of a
        multi-day rule of this run found the market filter off (its carried buys held back; the daily path's
        `market_filter_off`). Reads only; a part that cannot be read is left out, never guessed."""
        out: dict = {}
        try:
            out["trading_state"] = self.trading_state().state.value
            r = self.entry_reason()
            if r is None and self.daily is not None:
                off = getattr(self.daily, "market_filter_off", None)
                hit = off() if off is not None else None
                if hit:
                    r = ("market_filter_off", "market filter off: " + "; ".join(
                        f"{rule} (read at the close before {session})" for rule, session in hit)
                         + ": carried buys on hold")
            out["entry_block"], out["entry_block_why"] = (r[0], r[1]) if r is not None else (None, None)
        except Exception:
            log.exception("trading state unreadable (heartbeat)")
        return out



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

    def _payload_refused(self, key: str, why: str) -> None:
        """QA-Ops on #545: a broker payload a path of this engine did not book (broker/payloads.py): an ERROR log
        line and one `multi_day_error` event (step `broker_payload`, a Health type) per key and reason."""
        said = self.__dict__.setdefault("_payload_said", {})
        if said.get(key) == why:
            return
        said[key] = why
        log.error("broker payload refused: %s", why)
        with contextlib.suppress(Exception):  # an alarm never stops the path
            self.store.event("multi_day_error", {"step": "broker_payload", "error": why[:200],
                                                 "action": "not booked; read again, the reconciliation shows "
                                                           "any difference"}, self._rid())

    def _rid(self) -> int | None:
        return self.run["id"] if self.run else None

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


    def _applied_config(self, run_id: int, open_utc: datetime) -> dict | None:
        """The owner's latest applied config created before this session's open (None = the level's defaults)."""
        rows = [c for c in self.store.select("config", {"run_id": f"eq.{run_id}", "status": "eq.applied"})
                if _parse_ts(c["created_at"]) < open_utc]
        return max(rows, key=lambda c: int(c["version"])) if rows else None

    def _load_rules(self, d: Day) -> None:
        """M5 S43: the owner's latest applied config created before this session's open, tightened onto the level.
        Invalid -> entries refused for the session (fail closed); management, stops and the flatten still run."""
        d.rules = defaults(self.policies[self.level()], self.level_strategies())
        try:
            c = self._applied_config(d.run_id, d.open_utc)
        except Exception as e:
            self._refuse_session(f"{CONFIG_UNREADABLE}: {type(e).__name__}")  # asked again: `retry_rules`
            return
        self._apply_rules(d, c)

    def _apply_rules(self, d: Day, c: dict | None, late: bool = False) -> None:
        """The session's rules from the applied config `c` (None: the level's defaults). Invalid: refused."""
        lv_pol, lv_strats = self.policies[self.level()], self.level_strategies()
        d.rules = defaults(lv_pol, lv_strats)
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
                                            "config_sha": d.rules.config_sha, **({"late": True} if late else {})},
                         self._rid())

    def retry_rules(self, force: bool = False) -> str | None:
        """M10 1.25 (#536, test-eng): a session whose owner config could not be READ at its start (Supabase was
        unreachable, for example during a degraded start) reads it again, at most every `RULES_RETRY_S` (`force`:
        now). The caller asks only while the lease is ours. It is the same config the open would have read (the
        latest applied one created before this session's open: point-in-time, never a newer one), so from the next
        tick the session runs as if it had been read at the open, and every other check (the entry hours, the
        near-close send window, the risk gate, the lease) still applies. Custom tickers ('Only these') were not
        loaded at the open: they are not traded this session (said in the event). A config that is read and
        invalid is refused as at the open (its own `halt` row); one that is still unreadable stays refused, with no
        new row. Returns the session's config refusal still in force, or None when there is none."""
        d = self.day
        if d is None or not (d.refused or "").startswith(CONFIG_UNREADABLE):
            return None if d is None else d.refused  # none, or a config that was read and rejected: final today
        now = self.monotonic()
        if not force and now - self._rules_tried < RULES_RETRY_S:
            return d.refused
        self._rules_tried = now
        try:
            c = self._applied_config(d.run_id, d.open_utc)
        except Exception as e:
            log.warning("owner config still unreadable (%s): entries stay off this session", type(e).__name__)
            return d.refused
        was, d.refused = d.refused, None
        self._apply_rules(d, c, late=True)
        if d.refused:
            return d.refused
        detail = {"kind": "config", "day": str(d.day), "was": was, "version": d.rules.version}
        custom = sorted(d.rules.custom_universe())
        if custom:  # not loaded at the open (the refused session loaded none): never scanned or traded today
            detail["custom_not_loaded"] = custom
        self.store.event("halt_cleared", detail, self._rid())
        log.info("owner config v%s read again: the session's config refusal is lifted%s", d.rules.version,
                 f" (custom tickers {', '.join(custom)} were not loaded at the open: not traded today)"
                 if custom else "")
        return None

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
        except Exception:  # noqa: BLE001 - #694: open-ended (store, owner config); the session starts without them
            log.exception("custom tickers: the owner's list could not be read before the open (none backfilled)")
            return {}


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

    def _pause_rule(self) -> tuple[int, int]:
        r = self.day.rules if self.day else None
        return (r.pause_losses, r.pause_minutes) if r else (PAUSE_AFTER_LOSSES, PAUSE_MINUTES)

    # ================= per tick =================
    def _broker_clock(self) -> dict:
        """M9 1.10 (#435, audit M9): the broker's clock, read at every tick, and its timestamp compared with the
        host's. Every window (the near-close send, the entry hours, the flatten) is computed from the host clock; a
        Mac a few minutes slow would send the day's market orders after the close. The broker stamps its answer
        while the call is under way, so the timestamp may lie anywhere between the host's time before and after
        the call; beyond that, more than CLOCK_SKEW_MAX_S apart is a skew: `clock_skew` is set, no new order is
        sent (`entry_block`, `_may_enter`, the daily path's send) and one `clock_skew` event is written; it clears,
        with a second event, at the first tick the clocks agree. An answer without a readable timestamp counts as
        a skew (fail closed). Exits, protective stops, cancels and the flatten are not held."""
        t0 = self.now()
        clk = self.broker.clock()
        t1 = self.now()
        ts = _clock_ts(clk.get("timestamp"))
        if ts is None:
            skew = {"state": "unreadable", "broker": str(clk.get("timestamp"))[:40], "host": t1.isoformat(),
                    "limit_s": CLOCK_SKEW_MAX_S}
        else:
            off = (t0 - ts).total_seconds() if ts < t0 else (t1 - ts).total_seconds() if ts > t1 else 0.0
            skew = None if abs(off) <= CLOCK_SKEW_MAX_S else {
                "state": "skewed", "seconds": round(off, 1), "broker": ts.isoformat(), "host": t1.isoformat(),
                "limit_s": CLOCK_SKEW_MAX_S}  # seconds: host minus broker (negative: the host clock is slow)
        was, self.clock_skew = self.clock_skew, skew
        if skew is not None and (was is None or was["state"] != skew["state"]):
            log.error("clock skew: the host clock and the broker's disagree (%s): no new orders until they agree; "
                      "set this machine's clock (System Settings, Date & Time, set automatically)", skew)
            self.store.event("clock_skew", {**skew, "action": "no new orders until the clocks agree; exits, stops "
                                                              "and the flatten go on"}, self._rid())
        elif skew is None and was is not None:
            log.warning("clock skew over: the host clock and the broker's agree again")
            self.store.event("clock_skew", {"state": "ok", "host": t1.isoformat(), "limit_s": CLOCK_SKEW_MAX_S},
                             self._rid())
        return clk

    def _clock_block(self) -> str | None:
        s = self.clock_skew
        if s is None:
            return None
        if s["state"] == "unreadable":
            return "clock skew: the broker's clock gave no timestamp to compare with"
        return (f"clock skew: this machine's clock is {abs(s['seconds']):.0f} s "
                f"{'behind' if s['seconds'] < 0 else 'ahead of'} the broker's")


    def tick(self) -> dict:
        """One pass (`_tick`). #551: a damaged journal entry read anywhere in it stops it with JournalCorrupt, also
        when a broad handler on the way caught the error: the main loop then writes `engine_error`, stops and exits
        with code 4 (docs/ops/ledger.md, "Checksums"). The book's loop and the intraday path (engine/intraday_legacy.py,
        which overrides `_tick`) both run under this check."""
        self._journal_intact()
        try:
            st = self._tick()
        except Exception as e:
            self._journal_intact(cause=e)
            raise
        self._journal_intact()
        return st

    def _journal_intact(self, cause: BaseException | None = None) -> None:
        """#551: raise the damage the journal found (Journal.damage), if any."""
        damage = getattr(self.journal, "damage", None)
        if damage is not None:
            raise JournalCorrupt(str(damage)) from (cause or damage)

    def _tick(self) -> dict:
        """One pass of the multi-day book's loop (M10 1.19, #444): engine/book_loop.py."""
        return book_loop.tick(self)

    def _tick_closed(self, clk: dict, now: datetime) -> dict:
        """A tick with the market closed: the day is finalised, the rules decide for the next session, the next
        session is prepared. The same for the book's loop and for the intraday path."""
        self._md_exits_closed()  # M9 1.9: a flatten sale that filled before the close is booked after it too
        self._daily_step("sync", lambda: self.daily.sync())  # #206: the closing auction's fills, before the close
        if self.day and not self.day.finalized:
            self._sync_orders()
            self._finalize_day()
        elif self.day is None:
            self._finalize_missed(now)  # S77 (audit D8): a restart after the close still closes the day
        self._daily_step("after_close", lambda: self._daily_after_close(now))  # #206: decide for the next session
        if getattr(self.daily, "review", None) is not None:  # #131: off by default (review None)
            self._daily_step("preopen", lambda: self.daily.preopen())  # the pre-open review, from 08:30 ET
        self._maybe_prepare(clk, now)
        return self._daily_status({"state": "market_closed", "next_open": clk.get("next_open"),
                                   "account_clean": self.account_clean})

    # ---- M10 1.19 (#444): the intraday path (engine/intraday_legacy.IntradayEngine) adds its steps around the book's;
    # #694: its hooks are defined there only, and called here only when `self.intraday`. These two run on both.
    def _roomflow(self, room, pack_fn):
        """The agent room reviews intraday candidates only: without the intraday path there is none."""
        if room is not None:
            log.warning("the agent room is not built: no intraday rule is enabled (config/profiles.yaml "
                        "`intraday.enabled`)")
        return None

    def _restore_positions(self) -> None:
        """Without the intraday path the engine holds no `Pos`: its book is the ledger's multi-day sleeves. An open
        intraday trade left in the store (the intraday path was switched off while it held something) is not
        managed here: its shares are at the venue and not in the multi-day book, so the account guard below finds
        them, says so (`account_not_clean`) and blocks every buy until the account is clean (fail closed)."""
        self.pos = Positions()
        if not self.run:
            return
        left = sorted({str(t.get("symbol")) for t in self.store.open_trades(self.run["id"])})
        if left:
            log.error("open intraday trades in the store (%s) and no intraday rule is enabled: they are not "
                      "managed by this engine. Close them at the broker, or start with `intraday.enabled: true` "
                      "(config/profiles.yaml) until they are flat", ", ".join(left))

    def _md_send_steps(self, d: Day, sync: bool = False) -> None:
        """The daily path's steps of a minute: its order sync (`sync`: when they run first in the tick), the pre-open
        review and the send. Each is contained (`_daily_step`): a failure never stops the tick."""
        if sync:
            self._daily_step("sync", lambda: self.daily.sync())  # #206: the daily path's orders and fills
        if getattr(self.daily, "review", None) is not None:  # #131: off by default (review None)
            self._daily_step("preopen", lambda: self.daily.preopen())  # 09:30: the review before the send window
        self._daily_step("send", lambda: self.daily.send(d.day))  # #206, #333: near-close orders, 15:50-15:57 ET

    def data_block(self, symbol: str, index: bool = True) -> str | None:
        """M12 3.11 (#473): why the minute-bar data checks block a new position in `symbol` now (None: they do
        not). Always None unless KAIBOT_DATA_CHECKS_BLOCK is on (`LiveMarket.data_block`); a market without the
        checks (a replay's) never blocks. Never blocks a sale, a stop or a flatten: only entries ask."""
        fn = getattr(self.market, "data_block", None)
        if fn is None:
            return None
        try:
            why = fn(symbol, index=index)
        except Exception as e:  # noqa: BLE001 - fail closed: an entry is not opened on data nobody could check
            return f"data checks failed ({type(e).__name__})"
        return why if isinstance(why, str) and why else None

    def _data_status(self, st: dict, fresh: bool) -> dict:
        """QA-Ops (#484): a failed market-data read is no loop error any more, so the heartbeat says it itself
        (`market_data`), every minute it lasts."""
        return st if fresh else {**st, "market_data": f"read failed: {getattr(self, '_market_error', None)}"[:160]}

    def _market_update(self, now: datetime) -> bool:
        """M9 1.5 (audit M3): the minute's market-data read, which no longer stops the tick. A failed read leaves
        the bars as they were: the multi-day steps, the order sync, the drift check, the risk halts and the time
        flatten go on, and no intraday entry is scanned that minute (the caller). One `engine_error` event per
        distinct failure (step `market_update`), not one a minute. Returns whether the read worked."""
        try:
            self.market.update(now)
        except Exception as e:
            why = f"{type(e).__name__}: {e}"[:200]
            if getattr(self, "_market_error", None) == why:
                log.warning("market data: the minute read is still failing: %s", why)
                return False
            self._market_error = why
            log.exception("market data: the minute read failed (the tick goes on without new bars)")
            try:
                self.store.event("engine_error", {"step": "market_update", "error": why}, self._rid())
            except Exception:  # the event must never be what stops the tick
                log.exception("market data: the engine_error event was not written")
            return False
        self._market_error = None
        return True

    def _calendar_alerts(self, today: date) -> None:
        """M9 1.17 (#442): the two calendars someone extends by hand every autumn, announced CALENDAR_ALERT_DAYS
        before they run out, at every session start until they are extended (one `calendar_expiring` event and an
        ERROR log line each, once per session):

        - `macro_calendar`: `covers_until` of config/macro_calendar.yaml. After it every session halts new entries
          (`calendar_stale`), multi-day buys included.
        - `deadman_holidays`: the last row of `kaibot.market_calendar_exception` (migration 0017), read from
          Supabase. After it the off-box dead-man knows no holiday and no early close.

        An alert only: nothing is halted or blocked here. A table that cannot be read, or has no row, is not an
        expiry: it is logged and read again at the next session start; the session starts either way."""
        ends: dict[str, tuple[date | None, str]] = {
            "macro_calendar": (self.calendar_until, "extend config/macro_calendar.yaml (next year's FOMC and CPI "
                               "dates) and move covers_until: after it every session halts new entries")}
        try:
            rows = self.store.select("market_calendar_exception", {"select": "day"})  # a few dozen rows
            days = [date.fromisoformat(str(r["day"])[:10]) for r in rows or [] if r.get("day")]
            ends["deadman_holidays"] = (max(days) if days else None, "add next year's holidays and early closes to "
                                        "kaibot.market_calendar_exception (docs/ops/failsafe.md): after its last "
                                        "row the dead-man knows no holiday and no early close")
        except Exception as e:
            log.warning("calendar check: the dead-man's holiday table could not be read (%s); read again at the "
                        "next session start", f"{type(e).__name__}: {e}"[:160])
        seen = getattr(self, "_calendar_alerted", None)
        if seen is None:
            seen = self._calendar_alerted = set()
        for name, (until, action) in ends.items():
            if until is None or (until - today).days > CALENDAR_ALERT_DAYS or (name, today) in seen:
                continue
            seen.add((name, today))
            left = (until - today).days
            log.error("calendar expiring: %s ends %s (%s): %s", name, until,
                      f"in {left} days" if left >= 0 else f"{-left} days ago", action)
            try:
                self.store.event("calendar_expiring", {"calendar": name, "until": str(until), "days_left": left,
                                                       "action": action}, self._rid())
            except Exception:  # the alert must never be what stops the session start
                log.exception("calendar_expiring event not written")

    def _maybe_prepare(self, clk: dict, now: datetime) -> None:
        """Load history ahead of the open (the first load takes a few minutes; later ones are incremental)."""
        if self.run is None:
            return
        nxt = datetime.fromisoformat(clk["next_open"])
        if timedelta(0) < nxt - now <= timedelta(minutes=45) and self.market.day != nxt.date():
            open_utc, _ = self._session_hours(nxt.date(), "prepare")
            self.market.set_custom(self._custom_before_open(open_utc))  # S68: backfill custom tickers before the open
            self.market.prepare_day(
                nxt.date(),
                open_utc,
                progress=lambda n, tot: self.on_progress(self._daily_status(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                )),
            )

    def calendar_local(self, day: date, why: str, step: str = "sessions") -> None:
        """#746: a read of the broker's calendar failed and the local NYSE calendar answered for `day`
        (live_daily.local_session / local_hours). One `calendar_local` event per session (the ET date of now) and
        process, whichever read it was; the log says it at every new day asked. Never raises."""
        today = self.now().astimezone(_et()).date()
        said = getattr(self, "_calendar_local_said", None)
        if said is None:
            said = self._calendar_local_said = set()
        if (today, day) not in said:
            said.add((today, day))
            log.warning("broker calendar unreadable for %s (%s): the local NYSE calendar answers (%s)", day, why, step)
        if today in said:
            return
        said.add(today)
        try:
            self.store.event("calendar_local", {"day": str(today), "asked": str(day), "step": step, "error": why,
                                                "action": "the session's days and hours come from the engine's "
                                                          "local NYSE calendar until the broker's reads again"},
                             self._rid())
        except Exception:
            log.exception("calendar_local event not written")

    def _session_hours(self, day: date, step: str) -> tuple[datetime, int]:
        """(open UTC, regular minutes) of `day` from the broker's calendar; #746: when that read raises, from the
        local NYSE calendar (journalled once, `calendar_local`). A day the local calendar has no session for, or
        outside its years, raises the broker's error as before. A broker answer (a row, or none) is never
        overruled."""
        try:
            cal = self.broker.calendar(str(day))
        except Exception as e:
            from kaibot.engine.live_daily import local_hours

            hours = local_hours(day)
            if hours is None:
                raise
            self.calendar_local(day, f"{type(e).__name__}: {e}"[:160], step)
            return hours
        return session_minutes(cal, day)

    def _start_day(self, now: datetime) -> None:
        today = now.astimezone(_et()).date()
        open_utc, n_min = self._session_hours(today, "session_start")
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
        self._account_fees(today)  # #238: before the account check and the reconciliation below
        # S68: custom tickers are loaded only in 'Only these' mode (decision 12); All / Prefer = the catalogue alone
        custom = self.day.rules.custom_universe() if self.day.rules and not self.day.refused else {}
        if self.market.set_custom(custom):  # S68: the custom list changed since the history was loaded
            self.market.day = None
        if self.market.day != today:
            self.on_progress(self._daily_status({"state": "preparing",
                                                 "note": "loading price history (a few minutes the first time)"}))
            self.market.prepare_day(
                today,
                open_utc,
                progress=lambda n, tot: self.on_progress(self._daily_status(
                    {"state": "preparing", "note": f"loading price history {n}/{tot}"}
                )),
            )
        if self.intraday:  # M10 1.19: the owner's custom tickers (intraday rules only)
            self._vet_custom(self.day)
        for p in self.pos.values():  # intraday positions opened on an earlier session are closed at the first tick
            p.carried = (flatten_at_close(_holding(p.strategy)) and bool(p.entry_ts)
                         and datetime.fromisoformat(p.entry_ts).astimezone(_et()).date() < today)
        if self.kill:
            self.store.event("halt_cleared", {"kind": self.kill, "day": str(today)}, self._rid())
        self.kill, self.error_streak, self.clean_ticks = None, 0, 0
        self._check_recent_stops(today)
        if self.calendar_until is not None and today > self.calendar_until:
            self.store.event("calendar_stale", {"day": str(today), "covers_until": str(self.calendar_until),
                                                "action": "update config/macro_calendar.yaml"}, self._rid())
            self._halt_entries("calendar_stale", f"event calendar ends {self.calendar_until}")
        self._calendar_alerts(today)  # M9 1.17: said 30 days before, not found out on 1 January
        # #333: the daily path's near-close orders can fill while the engine is down (a restart inside 15:50-16:00):
        # their fills are booked before the account check and the reconciliation compare the ledger with the venue
        self._daily_step("sync", lambda: self.daily.sync(force=True))
        self.check_account()
        self._startup_reconcile("session_start")  # S78: run again, never reset
        # #133 (decision 15 section 11): a multi-day rule with a Gate B verdict (an experiment that failed it) is
        # named in the journal and the heartbeat so the dashboard labels it. Absent for intraday-only sessions.
        self.experiments = self._experiments()
        start = {"day": str(today), "start_equity": start_eq, "level": self.level(), "minutes": n_min}
        if self.experiments:
            start["experiments"] = self.experiments
        self.store.event("session_start", start, self._rid())
        self._log_market_alarm(today)

    def _log_market_alarm(self, today: date) -> None:
        """S103 (#377): one `market_alarm` event per session start; any failure is a `market_alarm_error` event."""
        if self.market_alarm is None:
            return
        try:
            self.store.event("market_alarm", self.market_alarm(today, self.now()), self._rid())
        except Exception as e:  # noqa: BLE001 - a notice must never stop a session
            log.warning("market alarm failed: %s", type(e).__name__)
            try:
                self.store.event("market_alarm_error", {"day": str(today), "error": f"{type(e).__name__}: {e}"[:200]},
                                 self._rid())
            except Exception:  # noqa: BLE001
                log.exception("market alarm error event failed")

    def _start_equity(self) -> float:
        se = self.ledger.start_equity(self.run["id"])
        return se if se is not None else float(self.run["amount_usd"]) + self.store.run_realized(self.run["id"])

    def _md_positions_after_fills(self, today: date) -> None:
        """#333 (QA-Ops second key): the near-close orders fill from 15:50 ET, before the fail-safes' flatten window
        (watchdog 15:55, dead-man 15:56-16:00). The dead-man (migration 0019/0022) counts as multi-day only the
        shares in the last session's `ledger_position` rows; without today's rows a buy that filled at 15:5x would
        be taken for intraday shares and sold if the engine then went silent. So once daily-path fills are booked,
        today's rows are written from the ledger as it stands, plus the shares today's daily buys may still add
        (`_md_rows`; the close overwrites them). Never raises."""
        if not getattr(self, "_md_filled", False) or self.daily is None or self.run is None:
            return
        self._md_filled = False
        try:
            self._md_rows(today, "daily-path fills booked")
        except Exception as e:  # the next booked fill writes them again; the close writes them in any case
            self._md_filled = True
            log.exception("ledger_position rows after the daily fills not written")
            self.store.event("multi_day_error", {"step": "positions_after_fills",
                                                 "error": f"{type(e).__name__}: {e}"[:200]}, self._rid())

    def _md_positions_planned(self, today: date, strategy: str, planned: Mapping[str, int]) -> None:
        """Follow-up of #333 (the window QA-Ops left open on #337): called by the daily path just before its buys go
        out. Today's rows are written with each planned buy at its target shares, so a fill that lands after the
        engine died (nobody books it) is multi-day shares to the dead-man too. Raises (the caller reports it)."""
        if self.daily is None or self.run is None:
            return
        # QA-Ops on #342: the plan is kept for the session, so a rewrite after another sleeve's fill (`_md_rows`
        # without a plan) never drops a planned buy the target loop sends on a later tick (the send hook runs once
        # per rule and session). The close forgets it (`_close_ledger`).
        day, kept = getattr(self, "_md_plan", (None, {}))
        plan = dict(kept) if day == today else {}
        for sym, q in planned.items():
            plan[(strategy, sym)] = max(plan.get((strategy, sym), 0.0), float(q))
        self._md_plan = (today, plan)
        self._md_rows(today, "daily-path buys about to be sent", plan)

    def _md_pending(self, today: date) -> dict[tuple[str, str], float]:
        """Shares today's daily-path buys may still add to their sleeves: an open order's unfilled rest, a finished
        order's fills the ledger has not booked yet (`OrderManager.settling`)."""
        om = getattr(self.daily, "om", None)
        out: dict[tuple[str, str], float] = {}
        for r in list(om.orders.values()) if om is not None else []:
            if r.session != str(today) or r.side != "buy" or r.purpose == "stop":
                continue
            left = (r.qty - r.booked) if r.open else (r.cum - r.booked)
            if left > 1e-9:
                out[(r.strategy, r.symbol)] = out.get((r.strategy, r.symbol), 0.0) + left
        return out

    def _md_rows(self, today: date, why: str, planned: Mapping[tuple[str, str], float] | None = None) -> None:
        """Today's ledger_position rows for the fail-safes, mid-session: each sleeve at its booked shares plus what
        today's daily buys may still add, at least a planned buy's target; a planned buy of a stock not held yet
        gets its own unpriced row. A row of today for a sleeve with no such shares is zeroed. The fail-safes never
        keep more than the broker holds, and the close overwrites every row of the day."""
        b = self.ledger.book(self._rid())
        if b is None:
            return
        if planned is None:  # QA-Ops on #342: the session's planned buys (`_md_positions_planned`) still count
            day, kept = getattr(self, "_md_plan", (None, {}))
            planned = kept if day == today else None
        want = {k: sl.qty for k, sl in b.sleeves.items() if sl.qty > 1e-9}
        for k, q in self._md_pending(today).items():
            want[k] = want.get(k, 0.0) + q
        for k, q in (planned or {}).items():
            want[k] = max(want.get(k, 0.0), float(q))
        self._write_positions(b, today, why=why, qty=want)
        for r in self.store.select("ledger_position", {"run_id": f"eq.{b.run_id}", "session": f"eq.{today}"}):
            if (r["strategy"], r["symbol"]) not in want and float(r.get("qty") or 0) != 0:  # sold since
                self.store.upsert("ledger_position", {**r, "qty": 0.0, "priced": False},
                                  "run_id,session,strategy,symbol")

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
        checked, unconfirmed = self._book_pnl_checked()  # M9 1.1: the book's end on confirmed prices only
        self._book_end_check(checked, unconfirmed=unconfirmed)
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
        if self._cmd_waiting:  # M9 1.2: nothing is opened while a flatten command waits
            return False
        if not self._reconciled():  # S78: an open ledger/broker difference blocks entries (exits go on)
            return False
        if not self.lease_ok():  # fail closed: the lease could not be renewed, so we may not be the only engine
            return False
        if self.clock_skew is not None:  # M9 1.10: the entry hours are read off a clock that disagrees
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

    # ================= order sync =================
    def _sync_orders(self) -> None:
        self.own_order_ids()  # M9 1.13: each tick, before a leg's id can be dropped (cancel, replace)
        if self._md_exits:
            self._sync_md_exits()
        if self.intraday:  # M10 1.19: the intraday path's entries, legs and exits
            self._sync_positions()

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
        dc = getattr(self.market, "data_block_status", None)
        dcs = dc() if dc is not None else None
        if isinstance(dcs, dict):  # M12 3.11: only with KAIBOT_DATA_CHECKS_BLOCK on (absent otherwise: no change)
            st["data_checks"] = dcs
        self._daily_status(st)
        if self.run is None and d is not None and d.run_id:
            st["run_ended"] = d.run_id  # S74b (#137): today's run has ended; the engine only winds it down
        return st


