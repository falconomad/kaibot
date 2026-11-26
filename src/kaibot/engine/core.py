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
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta

from kaibot.broker import payloads
from kaibot.broker.alpaca import CANCEL_POLL_S, CANCEL_POLLS
from kaibot.broker.payloads import PayloadError
from kaibot.broker.protocol import Broker, BrokerError, OrderRequest
from kaibot.engine import book_loop, order_path
from kaibot.engine.clock import session_minutes
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
from kaibot.engine.dividends import match_payment, open_accruals, receivable
from kaibot.engine.events import from_fomc_dates, in_blackout
from kaibot.engine.execution import flatten_at_close
from kaibot.engine.journal import Journal, JournalCorrupt, canon
from kaibot.engine.ledger import (
    CORPORATE_ACTIVITIES,
    CORPORATE_ACTIVITY_GROUPS,
    FEE_ACTIVITIES,
    LEDGER_KINDS,
    Action,
    Ledger,
    actions_from_activities,
    fees_from_activities,
    price_key,
)
from kaibot.engine.orders import St, _day_over
from kaibot.engine.reconcile import (
    ACCEPT_COMMAND,
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
    drawdown_pct,
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


class Engine:
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

    def _book_pnl_checked(self, at_mark: Sequence[str] = ()) -> tuple[float, list[str]]:
        """M9 1.1: the book's P&L for its run-end check, on confirmed prices only (`_md_price_checked`), and the
        symbols whose live price was not used (measured at the last close mark instead). `_book_pnl` is unchanged:
        the run's intraday end subtracts it from the run's equity, and both must be at the same prices.
        `at_mark`: symbols measured at their last close mark whatever their price says (their shares are in
        doubt, `_book_doubt`); they are not in the returned list."""
        if self.daily is None:
            return 0.0, []
        rid = self._rid() or (self.day.run_id if self.day else None)
        b = self.ledger.book(rid) if rid is not None else None
        if b is None:
            return 0.0, []
        from kaibot.engine.failsafe import multi_day_names

        names = set(multi_day_names())
        prices, rejected = {}, set()
        for k, sl in b.sleeves.items():
            if k[0] in names and sl.qty > 1e-9 and k[1] not in at_mark:
                px, ok = self._md_price_checked(k[1], sl)
                if px:
                    prices[k] = px
                if not ok:
                    rejected.add(k[1])
        return b.strategy_pnl(names, prices), sorted(rejected)

    def _md_price_checked(self, symbol: str, sl) -> tuple[float | None, bool]:
        """M9 1.1 (audit R1): the price of a multi-day holding for the book's run end, and whether a live price was
        accepted (False: there was one and it is not used; the caller measures at the last close mark).

        - A minute bar that ended at most MD_BAR_MAX_AGE_MIN minutes ago, within MD_FAR_PCT % of the last close
          mark: used.
        - A bar that is older or further away: used only when the broker's price of this tick agrees with it
          (MD_AGREE_PCT %). An old bar is one print read again and again; a far one may be a bad print, or a real
          move, which the broker then shows too.
        - No minute bar at all (a holding outside the live universe): the broker's price of this tick is the only
          one there is and is used; the two checks of the book end then read it twice, a minute apart.
        - A bar older than MD_BAR_DEAD_MIN minutes that the broker's price of this tick does not agree with is no
          price any more: the broker's price is used alone, as for a holding without bars (QA-Ops, #527: with the
          market data down, a fall the broker shows must still be counted).
        - Otherwise nothing (the last close mark). That includes a holding with no live price at all: it is
          named too (QA-Ops, #527), because the check does not see what it does today."""
        px = self._md_price(symbol)
        if not px:
            return None, False
        fresh_read = self._bpos_ts is not None and \
            abs((self.now() - self._bpos_ts).total_seconds()) <= MD_BROKER_MAX_AGE_S
        cp = (self._bpos.get(symbol) or {}).get("current_price") if fresh_read else None
        cp = float(cp) if cp else None
        if not self.market.last_close(symbol):  # no bar: `px` is the broker's price as last read
            return (float(px), True) if cp else (None, False)
        ref = sl.mark if sl.mark is not None else sl.avg_cost
        end = getattr(self.market, "last_bar_end", None)
        t = end(symbol) if end is not None else None
        age_ok = t is not None and (self.now() - t).total_seconds() <= MD_BAR_MAX_AGE_MIN * 60
        near = bool(ref) and abs(px / ref - 1) * 100 <= MD_FAR_PCT
        if age_ok and near:
            return float(px), True
        if cp and abs(cp / px - 1) * 100 <= MD_AGREE_PCT:
            return float(px), True
        if cp and t is not None and (self.now() - t).total_seconds() > MD_BAR_DEAD_MIN * 60:
            return cp, True
        return None, False

    def _book_actions_today(self, symbols) -> dict:
        """Held symbols with a corporate action today: an announced split, reverse split, spin-off, merger or symbol
        change with today's date (what the corporate-action guard already read: no call is made here), and a split
        the engine booked today."""
        today = str(self.day.day) if self.day else str(self.now().astimezone(_et()).date())
        out: dict = {}
        ca, ev = getattr(self.daily, "ca", None), getattr(self.daily, "_ca_evening", None)
        if ca is not None and ev is not None:
            try:
                out = {s: a for s, a in ca.known(ev, symbols).items() if str(a.on) == today}
            except Exception:
                log.exception("book end: the announced corporate actions could not be read from the guard")
        if self._splits_booked[0] == today:
            for s in self._splits_booked[1] & set(symbols):
                out.setdefault(s, "a split booked today")
        return out

    def _book_check_blocked(self, b) -> str | None:
        """M9 1.1: why holdings of the book are not measured at today's prices at this tick (None: all are)."""
        return self._book_doubt(b)[0]

    def _book_doubt(self, b) -> tuple[str | None, list[str]]:
        """M9 1.1: the held symbols whose live price says nothing about the book at this tick, and why (None, []:
        none). A split the ledger has not booked yet reads as a fall (the old share count at the new price), in the
        bars and at the broker alike. So a held symbol is in doubt while its shares differ between the ledger and
        the broker's positions of this tick (a difference the owner accepted aside; a symbol with an order under
        way is compared once that settled, as the drift check does), or while it has a corporate action today.

        QA-Ops second key on #527: the check is NOT skipped for the whole book while a symbol is in doubt (a
        difference that stays open for days would leave the book without its run end for days). The symbols in
        doubt are measured at their last close mark, which a split booked today has already divided, and every
        other holding at its confirmed price: `_book_end_check`."""
        from kaibot.engine.failsafe import multi_day_names

        names = set(multi_day_names())
        held = sorted({k[1] for k, sl in b.sleeves.items() if k[0] in names and sl.qty > 1e-9})
        if not held:
            return None, []
        acts = self._book_actions_today(held)
        why = ["a corporate action today: " + ", ".join(
            f"{s} ({getattr(a, 'action', a)})" for s, a in sorted(acts.items()))] if acts else []
        if self._bpos_ts is None or abs((self.now() - self._bpos_ts).total_seconds()) > MD_BROKER_MAX_AGE_S:
            return ("; ".join(why) or None), sorted(acts)  # no positions read of this tick to compare with
        unsettled = {p.symbol for p in self.pos.values() if p.status != "open" or p.exit_order_id}
        unsettled |= {k[1] for x in self._md_exits.values() for k, _ in x["keys"]}
        with contextlib.suppress(Exception):
            unsettled |= set(self.daily.working_symbols())
        mine, intraday = b.qty_by_symbol(), {}
        for p in self.pos.values():
            intraday[p.symbol] = intraday.get(p.symbol, 0.0) + p.qty
        diff = {s: float((self._bpos.get(s) or {}).get("qty") or 0) - intraday.get(s, 0.0) - mine.get(s, 0.0)
                for s in held if s not in unsettled and s not in acts}
        diff = {s: d for s, d in diff.items() if abs(d) > 1e-6}
        if diff:
            accepted = self.recon.accepted
            diff = {s: d for s, d in diff.items() if abs(d - accepted.get(("quantity", s), 0.0)) > 1e-6}
        if diff:
            why.append("shares differ between the ledger and the broker: " + ", ".join(
                f"{s} ({d:+g})" for s, d in sorted(diff.items())))
        return ("; ".join(why) or None), sorted(set(acts) | set(diff))

    def _book_peak(self, rid: int) -> float | None:
        peak = None
        for _, _, _, e in self.journal.entries(kinds=("md_book_peak",)):
            if e.get("run_id") == rid:
                peak = float(e["peak"])
                self._book_peak_ts = e.get("ts")
                if self._book_start is None or self._book_start[0] != rid:
                    self._book_start = (rid, str(e.get("ts")))  # #461: the book's start, its first peak
        return peak

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

    def _book_verdict(self, rid: int, b, rules: Sequence[str], peak: float, equity: float, lim, now: datetime):
        """#461 (decision 17 sections 3 and 7): the book's stop at this check, `risk/book_stop.verdict`. Mode fixed
        (the default) is `gate.book_run_ended` exactly. Mode band judges the book by its band table, point in time:
        a table whose research window has not ended before today is refused, and the horizon counts the sessions
        closed since the book's start (its first journalled peak) before today. The measured drawdown is the
        current one from the journalled peak: a past fall that was not a confirmed breach then never ends the book
        later (the line only grows with the horizon, and the end needs two checks a minute apart, now)."""
        cfg, _, _ = self._book_stop()
        table, why, sessions = None, None, 0
        if cfg.mode == "band":
            table, why = self._band_table(rules, self.level())
            day = now.astimezone(_et()).date()
            if table is not None and (why := bstop.table_unusable(table, day)) is not None:
                table = None
            if table is not None:
                start = day
                if self._book_start is not None and self._book_start[0] == rid:
                    with contextlib.suppress(ValueError, TypeError):
                        start = datetime.fromisoformat(self._book_start[1]).astimezone(_et()).date()
                sessions = bstop.sessions_traded((c.session for c in b.closes), start, day)
        v = bstop.verdict(peak, equity, lim, cfg=cfg, table=table, sessions=sessions)
        if why is not None and v.mode == "fixed":
            v = replace(v, why=why)
        return v

    def _book_line_marked(self) -> None:
        """Product, 19 Nov 2026: the book line (and its peak) at the close marks, after the close and for a heartbeat
        that has none (a start with the market closed). The minute ticks compute it on live prices only while the
        session runs, before the day's last daily fills and the close's marks are booked, so after the 18 Nov close
        it still showed the pot. The line and the peak only: the book end is still checked (and confirmed) by the
        session's minute ticks on live prices, so nothing is sold with the market closed. Never raises."""
        try:
            self._book_end_check(self._book_pnl(live=False), at_close=True)
        except Exception:
            log.exception("book line at the close marks not computed")

    def _book_end_check(self, book_pnl: float, at_close: bool = False, *, unconfirmed: Sequence[str] = ()) -> None:
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
            if self.book_line is not None and self.book_line.get("run_id") == rid:
                self.book_line = {**self.book_line, "ended": True}
            # closed: whatever is still held is sold (a sale under way is not repeated); never with the market closed
            if self._md_sleeves() and not at_close:
                self._flatten_md("book_drawdown", {}, self.monotonic())
            return
        rules = self.daily.rules
        if not rules:
            return
        end = min(r.builder.limits.run_drawdown_end_pct for r in rules)
        now = self.now()
        why = None
        if not at_close:  # M9 1.1: holdings whose shares are in doubt (an unbooked split) are not measured live
            why, doubt = self._book_doubt(b)
            if why is not None:
                # QA-Ops second key on #527: the check goes on for the rest of the book. The symbols in doubt
                # stand at their last close mark; journalled and raised as an event once per run, day and reason
                book_pnl, unconfirmed = self._book_pnl_checked(at_mark=doubt)
                key = (rid, str(now.astimezone(_et()).date()), tuple(doubt))
                if self._book_skip != key:
                    self._book_skip = key
                    log.warning("the multi-day book's run end does not see today's prices of %s: %s",
                                ", ".join(doubt), why)
                    self.journal.append("md_book_check_skipped", {"run_id": rid, "why": why, "symbols": doubt,
                                                                  "ts": now.isoformat()})
                    self.store.event("multi_day_book_unchecked", {
                        "run_id": rid, "why": why, "symbols": doubt, "ts": now.isoformat(),
                        "action": "these holdings are measured at their last close mark until the difference is "
                                  "cleared; the book's run end is still checked on every other holding"}, rid)
            self._unconfirmed_alarm(rid, now, unconfirmed)
        equity = float(b.amount) + book_pnl
        peak = self._book_peak(rid)
        if peak is None:  # the pot: the book's equity before its first trade
            peak = float(b.amount)
            self._book_peak_ts = now.isoformat()
            self._book_start = (rid, self._book_peak_ts)
            self.journal.append("md_book_peak", {"run_id": rid, "peak": round(peak, 2), "ts": self._book_peak_ts})
        if equity > peak + 0.005:
            # M9 1.1 (audit R1): the peak never comes down, so one high reading must not set it. At the close
            # marks it is taken at once; during the session only from two readings above it at least
            # BREACH_CONFIRM_S apart (the lower of the two), as the breach itself needs two checks.
            t = self._peak_try if self._peak_try is not None and self._peak_try[0] == rid else None
            new = None
            if at_close:
                new = equity
            elif t is None:
                self._peak_try = (rid, now, equity)
            elif (now - t[1]).total_seconds() >= BREACH_CONFIRM_S:
                new = min(t[2], equity)
            else:
                self._peak_try = (rid, t[1], min(t[2], equity))
            if new is not None:
                peak, self._peak_try = new, None
                self._book_peak_ts = now.isoformat()
                self.journal.append("md_book_peak", {"run_id": rid, "peak": round(peak, 2),
                                                     "ts": self._book_peak_ts})
        elif not at_close:
            self._peak_try = None  # a reading at or below the peak in between: start over
        # #461: the stop at this check (mode fixed: the level's line `end`, exactly as before)
        lim = replace(rules[0].builder.limits, run_drawdown_end_pct=end)
        try:
            v = self._book_verdict(rid, b, [r.name for r in rules], peak, equity, lim, now)
        except Exception as e:  # never "no stop": any failure of the band is today's fixed line
            log.exception("the multi-day book's stop: the band failed, the fixed line")
            v = replace(bstop.verdict(peak, equity, lim, cfg=bstop.FIXED), why=f"the band failed ({e}): the fixed line")
        band = v.mode == "band"
        end_pct = round(v.line * 100, 3) if band else end
        # #229, reporting only: the book's distance to its stop line, repeated in every heartbeat (`_daily_status`)
        self.book_line = {"run_id": rid, "equity": round(equity, 2), "peak": round(peak, 2),
                          "peak_ts": self._book_peak_ts, "drawdown_pct": round(drawdown_pct(peak, equity), 2),
                          "end_pct": end_pct, "ended": False}
        if self._book_stop()[0].mode == "band":  # mode fixed: the heartbeat is unchanged
            self.book_line["stop"] = {"mode": v.mode, "why": v.why, "horizon": v.horizon,
                                      "alert_pct": None if v.alert_line is None else round(v.alert_line * 100, 3)}
        if unconfirmed and not at_close:  # M9 1.1: holdings whose live price was not used (at the last close mark)
            self.book_line["unconfirmed"] = list(unconfirmed)
        if why is not None:
            self.book_line["unchecked"] = why
        if self._breach_rid != rid:  # QA-Ops second key on #213: an unconfirmed breach survives a restart
            self._breach, self._breach_rid = self._journalled_breach(rid), rid
        if at_close:  # after the close: the line and the peak only (see `_book_line_marked`)
            return
        if band and v.alert and not v.end:  # decision 17 section 3: the 95% line, a record only; nothing is sold
            key = (rid, str(now.astimezone(_et()).date()))
            if self._band_alerted != key:
                self._band_alerted = key
                log.warning("the multi-day book is past its band's 95%% line: %s", v.why)
                self.journal.append("md_book_band_alert", {
                    "run_id": rid, "drawdown_pct": round(v.drawdown * 100, 3),
                    "alert_pct": round(float(v.alert_line or 0) * 100, 3), "end_pct": end_pct, "horizon": v.horizon,
                    "ts": now.isoformat()})
        if not v.end:
            if self._breach is not None:
                self.journal.append("md_book_breach_clear", {"run_id": rid, "peak": round(peak, 2),
                                                             "equity": round(equity, 2), "ts": self.now().isoformat()})
            self._breach = None
            return
        # #211 (QA-Ops review of #207): a retirement is durable, so one bad print must never cause it. The breach
        # must hold on two checks at least BREACH_CONFIRM_S apart (two engine ticks, i.e. two minute bars) before
        # the book ends; a check in between that is not a breach starts over (`md_book_breach_clear`). The first
        # breach is journalled and read back after a restart, so an engine that restarts between the checks (a
        # crash loop in a falling market) still ends the book at its next check: a restart never delays the end.
        now = self.now()
        if self._breach is not None and self._breach[1].astimezone(_et()).date() != now.astimezone(_et()).date():
            self._breach = None  # both checks fall in one session: an old breach (before a night) confirms nothing
        if self._breach is None or self._breach[0] != rid:
            self._breach = (rid, now)
            self.journal.append("md_book_breach", {"run_id": rid, "peak": round(peak, 2), "equity": round(equity, 2),
                                                   "drawdown_pct": round(drawdown_pct(peak, equity), 3),
                                                   "end_pct": end_pct, "ts": now.isoformat(),
                                                   "action": "confirmed or dropped at the next check"})
            return
        if (now - self._breach[1]).total_seconds() < BREACH_CONFIRM_S:
            return
        self._breach = None
        self._book_end(rid, peak, equity, end_pct, [r.name for r in rules], stop=v if band else None)

    def _unconfirmed_alarm(self, rid: int, now: datetime, unconfirmed: Sequence[str]) -> None:
        """QA-Ops second key on #527: a holding measured at its last close mark (its live price was rejected, or it
        has none) is a holding whose fall the book's run end does not see. The heartbeat names it at once; after
        MD_UNCONFIRMED_ALARM_MIN minutes of checks on end it is an event, once per run, day and symbol."""
        if self._unconf_since[0] != rid:
            self._unconf_since = (rid, {})
        since = self._unconf_since[1]
        for s in [s for s in since if s not in unconfirmed]:
            del since[s]
        for s in unconfirmed:
            since.setdefault(s, now)
        day = str(now.astimezone(_et()).date())
        if self._unconf_alarmed[:2] != (rid, day):
            self._unconf_alarmed = (rid, day, set())
        due = sorted(s for s, t in since.items() if s not in self._unconf_alarmed[2]
                     and (now - t).total_seconds() >= MD_UNCONFIRMED_ALARM_MIN * 60)
        if not due:
            return
        self._unconf_alarmed[2].update(due)
        log.error("the multi-day book's run end measures %s at the last close mark: no confirmed price for %g "
                  "minutes", ", ".join(due), MD_UNCONFIRMED_ALARM_MIN)
        self.store.event("multi_day_book_unconfirmed", {
            "run_id": rid, "symbols": due, "since": {s: since[s].isoformat() for s in due}, "ts": now.isoformat(),
            "action": "measured at the last close mark: no minute bar that counts and no broker price that "
                      "agrees; the book's run end does not see today's move of these holdings"}, rid)

    def _journalled_breach(self, rid: int) -> tuple[int, datetime] | None:
        """The run's unconfirmed book breach from the journal (`md_book_breach` not followed by a
        `md_book_breach_clear`), or None: the confirmation state of `_book_end_check` across a restart."""
        last = None
        for _, _, kind, b in self.journal.entries(kinds=("md_book_breach", "md_book_breach_clear")):
            if b.get("run_id") == rid:
                last = (kind, b)
        if last is None or last[0] != "md_book_breach":
            return None
        try:
            return rid, datetime.fromisoformat(str(last[1]["ts"]))
        except (KeyError, ValueError):
            return None

    def _book_end(self, rid: int, peak: float, equity: float, end: float, rules: list[str],
                  stop: bstop.StopVerdict | None = None) -> None:
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
        if stop is not None:  # #461: the band ended it (decision 17 section 4); mode fixed: the body is unchanged
            body.update({"why": "band_99", "horizon": stop.horizon, "line_pct": round(stop.line * 100, 3)})
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
    def _md_sleeves(self, run_id=None) -> dict:
        """The run's multi-day sleeves that are not a `Pos` of this engine: the ledger's (strategy, symbol) books of
        rules that hold across sessions (their fills come from the order manager's daily path). Empty for an
        intraday-only book, so nothing in the intraday path changes while no multi-day rule holds anything.
        `run_id`: that run's, for a caller with no started run (`daily_path_refusal` under `--check`)."""
        rid = run_id if run_id is not None else (self._rid() or (self.day.run_id if self.day else None))
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
        if by_sym and (self.daily is None or rid is None):
            # M10 1.20 step 4 (#445): a sleeve is sold only through the daily path's order manager (`_md_exit_submit`),
            # and an engine with sleeves and no daily path does not start (step 2). Should this be reached all the
            # same, nothing is cancelled or sent: the sleeves keep their broker-held stops and the flatten is
            # incomplete (fail closed). No broker call and no journal entry before this check.
            why = f"no order manager for run {rid} (no daily path): the multi-day sale is not sent"
            log.error("flatten (%s): %s (%s)", reason, why, ", ".join(sorted(by_sym)))
            for sym in by_sym:
                failed[sym] = f"{sym}: {why}"
            return
        have = None
        if by_sym:  # #266: never sell more than the venue holds (that would be a short); no read without sleeves
            try:
                have = {s: float(p.get("qty") or 0) for s, p in self.broker.positions().items()}
            except Exception as e:  # #694: the broker's read, open-ended; each sale then asks per symbol (below)
                log.warning("flatten (%s): broker positions unreadable (%s): no venue cap read for this pass", reason,
                            type(e).__name__)
                have = None
        for sym, keys in by_sym.items():
            if have is not None and have.get(sym, 0.0) <= 0:  # nothing to sell (as close_position answered before)
                log.warning("flatten (%s): %s is in the ledger but not held at the broker: not sold", reason, sym)
                continue
            if have is not None and self.pos.on(sym):
                left = have[sym] - sum(float(p.qty) for p in self.pos.on(sym))
                capped = []
                for k, q in keys:
                    q = max(0.0, min(float(q), left))
                    left -= q
                    if q > 0:
                        capped.append((k, q))
                keys = capped
                if not keys:
                    failed[sym] = f"{sym}: the broker holds no multi-day shares to sell"
                    continue
            elif have is not None:
                held = sum(q for _, q in keys)
                if held > have[sym] + 1e-9:  # close_position sells what the venue holds: book only that
                    left, capped = have[sym], []
                    for k, q in keys:
                        q = max(0.0, min(float(q), left))
                        left -= q
                        capped.append((k, q))
                    keys = [(k, q) for k, q in capped if q > 0]
            if self.monotonic() - t0 > FLATTEN_BUDGET_S - CLOSE_MAX_S:
                failed.setdefault(sym, f"not tried: flatten time budget ({FLATTEN_BUDGET_S:g} s) used")
                continue
            self._flatten_beat(reason, sym, len(by_sym))
            try:
                if not self.pos.on(sym):
                    # #252 follow-up: no `close_position` (it carries no client id). The symbol's open orders (its
                    # stops) are cancelled; once none is left the sleeves' shares are sold through the order
                    # manager, whose client id is journalled first (`om_intent`), so a crash after the call is
                    # matched at start-up
                    # QA-Ops (#275 on #282): the flatten's one read of the venue's positions (above) when it worked;
                    # its own name, so the next symbol still finds the whole map
                    venue_qty = (have.get(sym, 0.0) if have is not None
                                 else float((self.broker.positions().get(sym) or {}).get("qty") or 0))
                    if venue_qty <= 0:  # nothing at the venue to sell (as close_position answered before)
                        log.warning("flatten (%s): %s is in the ledger but not held at the broker", reason, sym)
                        continue
                    if sum(q for _, q in keys) > venue_qty:  # never more than the venue holds
                        left_q, capped = venue_qty, []
                        for k, q in keys:
                            capped.append((k, min(q, left_q)))
                            left_q -= capped[-1][1]
                        keys = [(k, q) for k, q in capped if q > 0]
                    for oo in self.broker.open_orders():
                        if oo.get("symbol") == sym and oo.get("status") != "pending_cancel":
                            self._order("cancel", lambda oid=oo["id"]: order_path.cancel_order(self.broker, oid),
                                        order_id=oo["id"], symbol=sym, strategy=",".join(k[0] for k, _ in keys))
                    left = [oo for oo in self.broker.open_orders() if oo.get("symbol") == sym]
                    # QA-Ops (#282): Alpaca confirms a cancel within a moment; wait for it here, inside the flatten's
                    # time budget, as close_position's cancel-and-wait did (CANCEL_POLLS x CANCEL_POLL_S), so the
                    # sale goes out on this tick, not one minute later
                    polls = 0
                    while left and polls < CANCEL_POLLS and self.monotonic() - t0 < FLATTEN_BUDGET_S - CLOSE_MAX_S:
                        self.sleep(CANCEL_POLL_S)
                        polls += 1
                        left = [oo for oo in self.broker.open_orders() if oo.get("symbol") == sym]
                    if left:
                        failed[sym] = f"{sym}: waiting for {len(left)} cancel(s) before the sale"
                        continue
                    self._md_exit_submit(sym, keys, reason, rid)
                    failed.pop(sym, None)
                    continue
                for k, q in keys:
                    for oo in self.broker.open_orders():
                        pc = parse_coid(oo.get("client_order_id"))
                        if oo.get("symbol") == sym and oo.get("side") == "sell" and pc and pc[0] == clean(k[0]):
                            self._order("cancel", lambda oid=oo["id"]: order_path.cancel_order(self.broker, oid),
                                        order_id=oo["id"], symbol=sym, strategy=k[0])
                    self._md_exit_submit(sym, [(k, q)], reason, rid)
                failed.pop(sym, None)
            except Exception as e:
                log.warning("flatten (%s): selling the multi-day sleeves in %s failed: %s", reason, sym, e)
                failed[sym] = e

    def _md_om(self, rid):
        """M9 1.8 (#433): the daily path's order manager for run `rid`. None without the daily path or a run id:
        then no flatten sale is sent (M10 1.20 step 4, #445: the older direct sale is gone; see `_md_exit_submit`)."""
        if self.daily is None or rid is None:
            return None
        return self.daily.manager_for(rid)

    def _md_exit_submit(self, sym: str, keys: list, reason: str, rid) -> None:
        """The sale, through the order manager (M9 1.8, #433): one sale per sleeve under the manager's deterministic
        id `kb<run>-<rule>-<symbol>-<day>-flatten-<n>` (an open sale of the same day is reused, never doubled; a
        sale without an answer is `unknown` and looked up by its id; its intent is journalled before the call). The
        manager books every fill from the account activities at its own quantity and price, so a sale cancelled
        after a partial fill has its filled shares in the ledger. `_md_exits` holds the sale under its client id
        (`om`) until the manager has finished it (`_sync_md_exits`). A sale the venue refuses raises, as a refused
        broker call did.

        M10 1.20 step 4 (#445, Product ruling 24 Nov): there is no other way to sell a sleeve. The older direct sale
        (`kbfl-` client id, `md_exit_intent`), sent when there was no order manager, is deleted: an engine whose
        run holds multi-day sleeves and that has no daily path does not start (step 2, `DailyPathMissing`), and the
        run id is never None here (`_flatten_md` finds sleeves only for a run). Should it be reached all the same,
        `_flatten_md` cancels and sends nothing and counts the symbol as failed (`FlattenIncomplete`), and this raises
        before any call: fail closed. Sales the older way already journalled are still booked when they fill
        (`_rebuild_md_exits`, `_sync_md_exits`)."""
        om = self._md_om(rid)
        if om is None:
            raise RuntimeError(f"{sym}: no order manager for run {rid} (no daily path): the multi-day sale is not "
                               "sent (M10 1.20 step 4, #445)")
        session = str(self.now().astimezone(_et()).date())
        for k, q in keys:
            c = om.next_coid(rid, k[0], sym, session, "flatten")
            self._md_exits[c] = {"run_id": rid, "keys": [(k, q)], "reason": reason, "om": c}
            r = om.submit(OrderRequest(sym, "sell", q, "market", "day", c), strategy=k[0], purpose="flatten",
                          session=session, meta={"reason": reason}, override_block=True)
            if str(r.state) == "rejected":
                raise BrokerError(403, f"{sym}: the venue refused the flatten sale {c} ({r.reason})")

    def _md_exit_sent(self, o: dict, rid, keys: list, reason: str) -> None:
        """#252: a multi-day flatten sale is journalled as soon as the venue has given it an id (`md_exit`), so a
        restart before its fill still books it (`_rebuild_md_exits`)."""
        self._md_exits[o["id"]] = {"run_id": rid, "keys": keys, "reason": reason}
        self.journal.append("md_exit", {"order_id": o["id"], "run_id": rid, "coid": o.get("client_order_id"),
                                        "keys": [[list(k), q] for k, q in keys], "reason": reason,
                                        "ts": self.now().isoformat()})

    def _rebuild_md_exits(self) -> None:
        """#252: the multi-day flatten sales journalled as sent and not yet booked or dropped (start-up). Part 1 of
        `recover`. The order manager's sales (`om_intent`, purpose `flatten`) and, from a journal written before
        M10 1.20 step 4 (#445), the older direct sales (`md_exit_intent`, `md_exit`, a `kbfl-` intent): no engine
        sends those any more, but one already journalled is still booked when it fills."""
        done = {b.get("order_id") for _, _, _, b in self.journal.entries(kinds=("md_exit_done",))}
        closed = {b.get("coid") for _, _, _, b in self.journal.entries(kinds=("md_exit_done",)) if b.get("coid")}
        sent = {b.get("coid") for _, _, _, b in self.journal.entries(kinds=("md_exit",))}
        for _, _, _, b in self.journal.entries(kinds=("md_exit_intent",)):
            if b["coid"] in sent or b["coid"] in closed:
                continue
            try:
                o = self.broker.order_by_client_id(b["coid"])  # the venue has it: the crash came after the call
            except Exception:  # unreadable now: looked up again at the next start; the reconciliation still checks
                log.exception("flatten sale %s: lookup by client id failed", b["coid"])
                continue
            keys = [((k[0], k[1]), float(q)) for k, q in b.get("keys") or []]
            if o is None:
                self.journal.append("md_exit_done", {"coid": b["coid"], "order_id": None, "state": "never_sent"})
                continue
            self._md_exit_sent(o, b.get("run_id"), keys, b.get("reason"))
            self.store.event("multi_day_exit_recovered", {"coid": b["coid"], "order_id": o["id"]}, b.get("run_id"))
        done = {b.get("order_id") for _, _, _, b in self.journal.entries(kinds=("md_exit_done",))}
        sent = set()
        for _, _, _, b in self.journal.entries(kinds=("md_exit",)):
            sent.add(b.get("coid"))
            if b.get("order_id") in done:
                continue
            self._md_exits[b["order_id"]] = {"run_id": b.get("run_id"), "reason": b.get("reason"),
                                             "keys": [((k[0], k[1]), float(q)) for k, q in b.get("keys") or []]}
        # M9 1.8 (#433): the flatten sales the order manager holds (purpose `flatten`) that have no `md_exit_done`
        # yet: in flight, or finished while the engine was down. Each is held under its client id until
        # `_sync_md_exits` has seen the manager finish it; the manager itself looks an unconfirmed one up by its id
        from kaibot.engine.failsafe import COID

        for _, _, _, b in self.journal.entries(kinds=("om_intent",)):
            m = COID.match(str(b.get("coid") or ""))
            if b.get("purpose") != "flatten" or m is None or b["coid"] in closed or self.daily is None:
                continue
            self._md_exits[b["coid"]] = {"run_id": int(m.group(1)), "om": b["coid"],
                                         "keys": [((b.get("strategy"), b.get("symbol")), float(b["qty"]))],
                                         "reason": str((b.get("meta") or {}).get("reason") or "flatten")}
        # QA-Ops (#279): a sleeve's own market sale (`kbfl-` client id) whose intent is journalled but whose md_exit
        # is not (the process died between the broker call and the entry) is found at the venue by its client id
        # and rebuilt, so it is booked when it fills and the next flatten does not send it again. (A close_position
        # has no client id: that window stays, and the reconciliation shows it as a share difference.)
        for _, _, _, b in self.journal.entries(kinds=("intent",)):
            coid = str(b.get("coid") or "")
            if b.get("action") != "sell" or not coid.startswith("kbfl-") or coid in sent:
                continue
            try:
                o = self.broker.order_by_client_id(coid)
            except Exception:
                log.exception("multi-day flatten sale %s: unreadable at the venue (the reconciliation decides)", coid)
                continue
            if o and o.get("id") and o["id"] not in self._md_exits and o["id"] not in done:
                self._md_exit_sent(o, b.get("run_id"), [((b.get("strategy"), b.get("symbol")), float(b["qty"]))],
                                   str(b.get("reason") or "flatten"))
                sent.add(coid)

    def _md_exit_booked(self, oid: str, strategy: str, symbol: str) -> bool:
        """The ledger already holds this sale's fill for this sleeve (a crash after its booking, before
        `md_exit_done`; QA-Ops: per sleeve, so a crash between two sleeves of one order books the second)."""
        return any(b.get("order_id") == oid and b.get("side") == "sell" and b.get("strategy") == strategy
                   and b.get("symbol") == symbol for _, _, _, b in self.journal.entries(kinds=("fill",)))

    def _sync_md_exits(self) -> None:
        synced = set()
        for oid, x in list(self._md_exits.items()):
            if x.get("om"):  # M9 1.8 (#433): the order manager's sale; it books the fills, this only closes it
                om = self._md_om(x["run_id"])
                if om is None:
                    continue
                if id(om) not in synced:  # once per pass: unconfirmed orders looked up, fills booked, orders polled
                    synced.add(id(om))
                    om.sync()
                r = om.get(oid)
                if r is not None and (r.open or r.cum > r.booked + 1e-9):
                    continue  # working, or a fill the venue reported is not booked yet
                state = "never_sent" if r is None else "booked" if str(r.state) == "filled" else str(r.state)
                if r is not None and r.booked > 1e-9:
                    self.store.event("multi_day_sold", {"order_id": r.order_id, "coid": oid, "px": r.avg,
                                                        "qty": r.booked, "reason": x["reason"],
                                                        "keys": [f"{k[0]}|{k[1]}" for k, _ in x["keys"]]},
                                     self._rid() or x["run_id"])
                self.journal.append("md_exit_done", {"order_id": r.order_id if r is not None else None, "coid": oid,
                                                     "state": state, "filled": r.booked if r is not None else 0.0})
                self._md_exits.pop(oid)  # what is left unsold: the next full flatten sells it (a new attempt)
                continue
            o = self.broker.order(oid)
            if o["status"] == "filled":
                try:  # QA-Ops on #545: a price that is not a finite number above zero is never booked
                    px, ts = payloads.fill_price(o), o.get("filled_at")
                except PayloadError as e:
                    self._payload_refused(f"order:{oid}", f"multi-day sale {oid}: {e}")
                    continue  # stays in `_md_exits` (a new run and "Flatten and stop" wait): read again next pass
                fresh = [(k, q) for k, q in x["keys"] if not self._md_exit_booked(oid, k[0], k[1])]
                if fresh:
                    fees = (self._venue_fees(oid, ts, o.get("submitted_at") or o.get("created_at"))
                            if x["run_id"] is not None else 0.0)  # #238: the venue's fee
                    total = sum(q for _, q in x["keys"]) or 1.0
                    for (strategy, sym), q in fresh:
                        if x["run_id"] is not None:  # one order for every sleeve of the symbol: its fee by shares
                            self._book("fill", {"run_id": x["run_id"], "strategy": strategy, "symbol": sym,
                                                "side": "sell", "qty": q, "px": px, "fees": fees * q / total,
                                                "coid": o.get("client_order_id"), "order_id": oid, "ts": ts,
                                                "reason": x["reason"]})
                    keys = [f"{k[0]}|{k[1]}" for k, _ in x["keys"]]
                    self.store.event("multi_day_sold", {"order_id": oid, "keys": keys, "px": px,
                                                        "reason": x["reason"]}, self._rid())
                self.journal.append("md_exit_done", {"order_id": oid, "state": "booked"})
                self._md_exits.pop(oid)
            elif o["status"] in ("canceled", "expired", "rejected"):
                self.journal.append("md_exit_done", {"order_id": oid, "state": o["status"]})
                self._md_exits.pop(oid)  # not sold: the next full flatten tries again

    def _md_exits_closed(self) -> None:
        """M9 1.9: the multi-day flatten sales with the market closed (they are DAY orders: filled before the close,
        or expired at it). Never raises: an unreadable order is tried again at the next tick, and the closed
        branch's other steps (the daily decision, the preparation of the next session) still run."""
        if not self._md_exits:
            return
        try:
            self._sync_md_exits()
        except Exception:
            log.exception("multi-day flatten sales not synced (market closed); tried again at the next tick")

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

    # ================= commands =================
    def apply_commands(self) -> None:
        """#551: a command's failure is written on the command and never stops the engine, except a damaged journal
        entry read on the way: the command whose handling read it stays pending, no command after it runs, and
        JournalCorrupt goes to the main loop (`engine_error`, stop, exit code 4)."""
        self._journal_intact()
        for c in self.store.pending_commands():
            self._journal_intact()
            kind, pl = c["kind"], c.get("payload") or {}
            try:
                why = self._command_refusal(kind, pl)
            except Exception as e:  # QA-Ops (#513): a row the check itself cannot read is refused, never applied,
                self._journal_intact(cause=e)
                log.exception("command %r: the check failed", kind)  # and never raises out of the tick
                why = f"the command could not be checked: {str(e)[:120]}"
            if why is not None:  # M9 1.15: refused before anything is done (a bad new_run sells nothing)
                self._reject_command(c, why)
                continue
            try:
                if kind == "new_run":
                    if not self._flat_for_command(c, kind):  # close the old run's positions first (multi-day
                        continue  # sleeves too, S80); the command waits until flat
                    self._new_run(float(pl["amount_usd"]), pl["level"])
                elif kind == "pause" and self.run:
                    self._set_run_status("paused")
                elif kind == "resume" and self.run:
                    self._set_run_status("running")
                elif kind == "flatten_and_stop" and self.run:
                    if not self._flat_for_command(c, kind):  # M9 1.2: pending until nothing is left to sell
                        continue
                    self._end_run("stopped", "stopped by user")
                elif kind == ACCEPT_COMMAND and self.run:  # S78: the owner accepts the open differences
                    self._accept_reconcile(pl)
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
                self._journal_intact()  # damage a handler caught itself: the command is not applied
                self.store.mark_applied(c["id"])
                self._cmd_done(str(c["id"]))
                self.store.event(f"command_{kind}", pl, self._rid())
            except Exception as e:  # a bad command must never stop the engine (a damaged journal does)
                self._journal_intact(cause=e)
                log.exception("command %s failed", kind)
                self._cmd_done(str(c.get("id")))
                self.store.mark_applied(c["id"], {**pl, "error": str(e)[:200]})

    def _cmd_wait(self, cid: str, kind: str, why: str) -> None:
        """QA-Ops second key on #525: the entry block of a waiting flatten command is journalled when it starts
        (and raised as one `command_waiting` event), so that a restart keeps it. Without it, an engine that came
        back while Supabase was unreachable (`pending_commands` answers none) would open positions in a book the
        owner had told it to sell."""
        if cid not in self._cmd_waiting:
            rid = self._rid()
            self.journal.append("command_waiting", {"id": cid, "kind": kind, "why": why, "run_id": rid,
                                                    "ts": self.now().isoformat()})
            self.store.event("command_waiting", {"command_id": cid, "kind": kind, "why": why,
                                                 "action": "no position is opened until the book is flat; the "
                                                           "sales are sent while the market is open and tried "
                                                           "again on every pass"}, rid)
        self._cmd_waiting[cid] = why

    def _cmd_done(self, cid: str) -> None:
        self._cmd_note.pop(cid, None)
        self._cmd_aged.pop(cid, None)
        if self._cmd_waiting.pop(cid, None) is not None:
            self.journal.append("command_done", {"id": cid, "ts": self.now().isoformat()})

    def _restore_cmd_waiting(self) -> None:
        """The waiting flatten commands of the running run, from the journal (`command_waiting` without its
        `command_done`). The command itself is read again from the store; until it is, the block stands. It ends
        with the command (applied, or failed) or with the run."""
        rid = self._rid()
        waiting: dict[str, str] = {}
        if rid is not None:
            for _, _t, kind, b in self.journal.entries(kinds=("command_waiting", "command_done")):
                if kind == "command_done":
                    waiting.pop(str(b.get("id")), None)
                elif b.get("run_id") == rid:
                    waiting[str(b.get("id"))] = str(b.get("why") or "a flatten command is pending")
        self._cmd_waiting = waiting
        if waiting:
            log.warning("a flatten command was waiting before the restart (%s): no position is opened until it "
                        "is completed", ", ".join(sorted(waiting)))

    def _flat_for_command(self, c: dict, kind: str) -> bool:
        """M9 1.2 (audit R2): is the book flat, so that this `flatten_and_stop` or `new_run` can be completed?

        While it is not, the command stays pending and every loop pass (about 10 s) comes back here: the flatten is
        run again (`_exit` skips a position that has its exit order, `_flatten_md` a sleeve whose sale is out), and
        what it could not close (`FlattenIncomplete`) is tried again by the next pass instead of being written on
        the command as an error. Sales are sent only while the market is open: a DAY market order sent after the
        close would wait for the next open (`_book_end_check` avoids the same); with the clock unreadable nothing
        is sent. As long as the command waits no position is opened (`entry_block`, `_may_enter`); the block is
        kept in memory until the command is completed, so a pass that could not read the commands (Supabase
        unreachable: `pending_commands` answers none) does not lift it."""
        cid = str(c.get("id"))
        new = kind == "new_run"
        flat = not (self.pos or self._md_sleeves() or self._md_exits) if new else self._nothing_to_sell()
        if flat and not new:  # nothing to sell: the flatten still cancels the daily path's working orders (#207),
            try:  # once, at any hour (it sends no sale), before the run is ended
                self.flatten_all("manual")
            except Exception as e:
                log.warning("command %s (id %s): the last flatten failed (%s): tried again", kind, cid, e)
                flat = False
        if flat:
            return True  # the caller completes the command (`_cmd_done` lifts the block)
        self._cmd_wait(cid, kind, "new run pending: the old book is being sold" if new else "flatten and stop pending")
        note = "the market is closed (or its clock could not be read): the sales wait for the open"
        if self._market_open_now():
            note = "sales sent, waiting for their fills"
            try:
                self.flatten_all("manual")
            except FlattenIncomplete as e:
                note = str(e)[:300]
            except Exception as e:  # nothing here may lose the command
                note = f"flatten failed: {type(e).__name__}: {e}"[:300]
        self._cmd_unbooked_aged(cid, kind)
        if self._cmd_note.get(cid) != note:  # once per change, not every 10 s
            self._cmd_note[cid] = note
            log.warning("command %s (id %s) stays pending: %s", kind, cid, note)
        return False

    def _nothing_to_sell(self) -> bool:
        """For "Flatten and stop": no intraday position, no multi-day sale still to book, and no multi-day sleeve
        the venue still holds shares of. A sleeve the venue holds nothing of (a ledger/broker difference, which
        `_flatten_md` does not sell either) does not keep the run from ending; an unreadable venue does.

        #533: the ledger alone does not know every share of the run. Its daily path's order manager also must show
        (`_om_unsettled`) no order still open (working, unconfirmed, or being cancelled: it can still fill, and once
        the run is ended no sync sends its cancel again or books its fill), and no finished order whose filled shares
        the ledger has not booked yet while the venue holds more of that symbol than the ledger (those shares are
        sold once the fill is booked; a fill that is never reported is the owner's to book, #529). Unbooked shares
        the venue does not hold (sold by a fail-safe meanwhile) leave nothing to sell and do not keep the run.

        #591: a finished SALE of the run whose fill is not booked yet keeps the run too (`_om_unbooked_sale`). The
        venue holds less than the ledger then, so the check below passed it, and once the run is ended no sync
        books that fill: the ended run's ledger kept the sold shares and missed the proceeds for good. It waits as
        an unbooked buy does, with the same bound (`_cmd_unbooked_aged`: from the order's next day the owner books
        or writes off the fill, #529)."""
        if self.pos or self._md_exits:
            return False
        symbols = {k[1] for k in self._md_sleeves()}
        unsettled = self._om_unsettled()
        if unsettled is None:
            return False
        open_orders, unbooked = unsettled
        if open_orders or self._om_unbooked_sale():
            return False
        if not symbols and not unbooked:
            return True
        try:
            have = self.broker.positions()
        except Exception as e:
            log.warning("flatten and stop: the broker's positions could not be read (%s): not ended yet", e)
            return False
        if not all(float((have.get(s) or {}).get("qty") or 0) <= 0 for s in symbols):
            return False
        rid = self._rid()
        b = self.ledger.book(rid) if rid is not None else None
        booked = b.qty_by_symbol() if b is not None else {}
        return all(float((have.get(s) or {}).get("qty") or 0) <= float(booked.get(s, 0.0)) + 1e-9 for s in unbooked)

    def _cmd_unbooked_aged(self, cid: str, kind: str) -> None:
        """QA-Ops on #544: the bound of a command's wait for an unbooked fill (#533), as #524 bounds a sale's. Its
        activity is expected within the order's New York day; from the next day on it may never come. The command
        does not end the run then (the shares would stay at the venue with no sleeve, no stop and no sync: #533
        itself), it stays pending with entries blocked, and one more `command_waiting` event (with `aged`; an ERROR
        log line; no migration: kb_health lists the kind since 0039) names the orders and the owner's action
        (book or write off the fill, #529). Raised again when the set of aged orders changes; cleared with the
        command. Never raises."""
        try:
            om = getattr(self.daily, "om", None) if self.daily is not None else None
            rid = self._rid()
            if om is None or rid is None or om.run_id != rid:
                return
            now = self.now()
            aged = sorted((r for r in list(om.settling.values()) if _day_over(r, now)), key=lambda r: r.coid)
            key = tuple(r.coid for r in aged)
            if self._cmd_aged.get(cid, ()) == key:
                return
            self._cmd_aged[cid] = key
            if not aged:
                return
            orders = [{"coid": r.coid, "symbol": r.symbol, "side": r.side, "unbooked": round(r.cum - r.booked, 6),
                       "session": r.session} for r in aged]
            log.error("command %s (id %s): fills of an earlier day are still not booked (%s): the command waits "
                      "until the owner books or writes them off", kind, cid, ", ".join(x["coid"] for x in orders))
            self.store.event("command_waiting", {  # a kind the Health page already shows (kb_health, 0039)
                "command_id": cid, "kind": kind, "why": "a fill of an earlier day was never booked", "aged": orders,
                "action": "the venue reported these fills but their account activity never arrived: the command "
                          "stays pending (no position is opened) until the owner books or writes off the fill "
                          "(issue #529); the run is not ended with the shares at the venue"}, rid)
        except Exception as e:  # an alarm must never lose the command
            log.warning("command %s (id %s): the aged-fill check failed (%s)", kind, cid, e)

    def _om_unbooked_sale(self) -> bool:
        """#591: does the run's daily-path order manager hold a finished sale whose filled shares are not booked
        yet? True also when it cannot be read (never end a run on a read that failed)."""
        om = getattr(self.daily, "om", None) if self.daily is not None else None
        rid = self._rid()
        if om is None or rid is None or om.run_id != rid:
            return False
        try:
            return any(r.side == "sell" for r in list(om.settling.values()))
        except Exception as e:
            log.warning("flatten and stop: the order manager could not be read (%s): not ended yet", e)
            return True

    def _om_unsettled(self) -> tuple[list[str], set[str]] | None:
        """#533: (client ids of the run's open non-stop orders, symbols of its finished orders with fills not booked
        yet) in the daily path's order manager; ([], set()) without one, None when it cannot be read."""
        om = getattr(self.daily, "om", None) if self.daily is not None else None
        rid = self._rid()
        if om is None or rid is None or om.run_id != rid:
            return [], set()
        try:
            open_orders = [r.coid for r in om.working() if r.purpose != "stop"]
            # QA-Ops on #544 with #535: an order called lost ("rejected", reason "lost: ...") is a guess, and the
            # venue may still fill it; its day order can trade only on its own New York day, so until that day is
            # over it keeps the run as an open order does (else the run ends and a late fill, adopted or foreign,
            # is left at the venue: the hole of #533 again)
            now = self.now()
            open_orders += [r.coid for r in list(getattr(om, "orders", {}).values())
                            if r.state is St.REJECTED and str(r.reason or "").startswith("lost")
                            and r.purpose != "stop" and not _day_over(r, now)]
            unbooked = {r.symbol for r in list(om.settling.values())}
        except Exception as e:  # never end a run on a read that failed
            log.warning("flatten and stop: the order manager could not be read (%s): not ended yet", e)
            return None
        return open_orders, unbooked

    def _market_open_now(self) -> bool:
        """The broker's clock, for a command's sales. A "closed" answer is kept for 30 s (a pending command asks on
        every loop pass, all weekend); an "open" one is never kept, so no sale follows the close by a stale read."""
        now = self.now()
        if self._closed_seen is not None and timedelta(0) <= now - self._closed_seen < timedelta(seconds=30):
            return False
        try:
            is_open = bool(self.broker.clock()["is_open"])
        except Exception as e:
            log.warning("the broker's clock could not be read (%s): no sale is sent", e)
            return False
        self._closed_seen = None if is_open else now
        return is_open
    def _command_refusal(self, kind, pl) -> str | None:
        """M9 1.15 (audit S1): why this command row is not applied (None: it is). The command table is written with
        the service-role key by more than the dashboard's RPC, so the engine checks the row itself: the kind against
        COMMAND_KINDS, and a `new_run`'s pot and level (`run_refusal`) BEFORE the old run's book is sold."""
        if not isinstance(kind, str) or kind not in COMMAND_KINDS:
            return "unknown command kind"
        if not isinstance(pl, Mapping):
            return "the payload is not an object"
        if kind == "new_run":
            return run_refusal(pl.get("amount_usd"), pl.get("level"), self.policies)
        return None

    def _reject_command(self, c: dict, why: str) -> None:
        """A refused command: marked applied with the reason (it is never looked at again) and one
        `command_rejected` event. The event's type never carries the row's own kind."""
        kind, pl = c.get("kind"), c.get("payload")
        log.error("command %r (id %s) rejected: %s", kind, c.get("id"), why)
        try:
            self.store.mark_applied(c["id"], {**(pl if isinstance(pl, Mapping) else {}), "error": why[:200]})
            self.store.event("command_rejected", {"command_id": c.get("id"), "kind": str(kind)[:40], "reason": why,
                                                  "payload": _brief(pl)}, self._rid())
        except Exception:  # a bad command must never stop the engine
            log.exception("command %r: the rejection could not be recorded", kind)

    def _new_run(self, amount: float, level: str) -> None:
        why = run_refusal(amount, level, self.policies)  # M9 1.15: never a run outside the limits, whoever calls
        if why is not None:
            raise ValueError(why)
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
        self._book("run", {"run_id": run["id"], "amount": amount, "broker_cash0": self._broker_cash(),
                           "started": self.now().astimezone(UTC).isoformat()})
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

    def _corporate_actions(self, today: date) -> None:
        """S77: dividends and splits of the symbols held at the open, booked to every rule that holds them. Research
        passes the daily store's table; live reads Alpaca's non-trade activities. Never raises: a failure is an
        event, and a split the ledger missed shows as drift (entries stop).
        Failure behaviour (#392, kept as it was and as decision 15 sections 3 and 6 describe it): a failed read
        does not by itself block entries or skip buys. The guard for a missed split is the share-count check
        (account guard, reconciliation, drift halt), which compares the ledger with the broker's positions and
        stops entries on any difference; a missed dividend payment leaves its ex-date accrual open until a later
        read matches it (`_late_actions` and the next session start read again)."""
        b = self.ledger.book(self._rid())
        if b is None:
            return
        due = self.corporate_actions is None and bool(open_accruals(self.journal.entries(kinds=("div_accrual",
                                                                                                "div_paid"))))
        if not b.sleeves and not due:  # nothing held and no accrued dividend waiting for its payment
            return
        shares0 = {k: s.qty for k, s in b.sleeves.items()}
        try:
            if self.corporate_actions is not None:
                acts = list(self.corporate_actions(today) or ())
            else:
                if self.ex_dividends is not None:
                    self._accrue_dividends(b, today)
                rows = self._match_dividend_payments(self._corporate_activities(today))
                bq = {s: float(p["qty"]) for s, p in self.broker.positions().items()}
                acts = actions_from_activities(rows, b.qty_by_symbol(), bq)
        except Exception as e:
            log.exception("corporate actions unavailable")
            self.store.event("corporate_actions_error", {"day": str(today), "error": str(e)[:200]}, self._rid())
            return
        for a in acts:
            self._book_action(b, a)
        if self.corporate_actions is None and self.daily is not None and self.recent_actions is not None:
            self._adopt_actions(b, today)
        if self.daily is not None and {k: s.qty for k, s in b.sleeves.items()} != shares0:
            self._write_positions(b, today)

    def _account_fees(self, today: date) -> None:
        """#238: the fees the venue took apart from the fills, booked to the run's cash once each, so the ledger's
        cash follows the broker's to the cent. Alpaca puts no fee on a FILL activity (the fill books 0, #241); its
        regulatory fees (TAF, SEC, CAT) come as FEE activities the evening after the trades. Read at every session
        start, from the run's start (the broker's cash baseline), each booked once by its activity id. Research
        (`corporate_actions` given) has none: `SimBroker` charges its fees on the fill. Never raises: a failed read
        is one `venue_fee_unread` event, and the fees are read again at the next session start; until then a large
        unbooked fee shows as a cash difference in the reconciliation, as before."""
        if self.corporate_actions is not None or self.run is None:
            return
        b = self.ledger.book(self._rid())
        if b is None or b.broker_cash0 is None:  # no cash baseline: the reconciliation does not compare cash
            return
        started = self._run_started()
        try:  # two days early: Alpaca dates a fee by its trade day and posts it the evening after
            rows = self.broker.activities(started - timedelta(days=2), types=FEE_ACTIVITIES)
            fees = fees_from_activities([r for r in rows if _posted_since(r, started)])  # QA-Ops: a bad row too
        except Exception as e:
            log.exception("account fee activities unavailable")
            self.store.event("venue_fee_unread", {"day": str(today), "what": "FEE activities",
                                                  "error": f"{type(e).__name__}: {e}"[:200]}, self._rid())
            return
        for body in fees:
            if body["ref"] not in b.booked:
                self._book("fee", {"run_id": b.run_id, **body})

    def _run_started(self) -> datetime:
        """When the run's ledger began, the moment the broker's cash baseline was read: the `run` entry's `started`
        (the engine's clock; #238), else the time the journal wrote it (runs begun before #238; live, that is the
        same moment). Without one (a journal older than the ledger): 10 days back, like the corporate-actions read."""
        rid = self._rid()
        for _, ts, _, body in self.journal.entries(kinds=("run",)):
            if int(body.get("run_id", -1)) == int(rid):
                t = datetime.fromisoformat(body.get("started") or ts)
                return (t if t.tzinfo else t.replace(tzinfo=UTC)) - timedelta(minutes=1)
        return self.now() - timedelta(days=10)
    def _corporate_activities(self, today: date) -> list[dict]:
        """#392: Alpaca's dividend and split activities of the last 10 days. One request for both groups; if Alpaca
        refuses it (one bad code refuses the whole request), each group (`CORPORATE_ACTIVITY_GROUPS`) is asked on its
        own, so the dividends still come in when the split code is refused and the other way round. A refused group
        is one `corporate_actions_error` event naming it (`refused`); when every group is refused this raises, and
        the caller's error path runs (nothing is booked that session, as before)."""
        after = self.now() - timedelta(days=10)
        try:
            return self.broker.activities(after, types=CORPORATE_ACTIVITIES)
        except Exception as e:
            first = e
        rows: list[dict] = []
        refused: dict[str, str] = {}
        for name, types in CORPORATE_ACTIVITY_GROUPS:
            try:
                rows.extend(self.broker.activities(after, types=types))
            except Exception as e:
                refused[name] = str(e)[:200]
        if len(refused) == len(CORPORATE_ACTIVITY_GROUPS):
            raise first
        rows.sort(key=lambda r: str(r.get("date") or r.get("transaction_time") or ""))
        if not refused:  # QA-Ops: the combined request failed once (e.g. a timeout) and both groups came in
            log.warning("corporate actions: the combined read failed (%s); both groups read on their own", first)
            return rows
        log.warning("corporate actions: only part of the read was accepted: %s", refused)
        self.store.event("corporate_actions_error", {
            "day": str(today), "error": str(first)[:200], "refused": refused,
            "read": [n for n, _ in CORPORATE_ACTIVITY_GROUPS if n not in refused],
            "action": "the groups read are booked; the refused ones are not (a missed split shows as drift)"},
            self._rid())
        return rows

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

    def _write_positions(self, b, today: date, why: str = "corporate action at the open",
                         qty: Mapping[tuple[str, str], float] | None = None) -> None:
        """#266: the share count changed at this open (a split, spin-off, symbol change or cash merger booked). The
        fail-safes (the dead-man of migration 0019 and the watchdog's store view) read the last session's
        `ledger_position` rows, so today's rows are written now, from the ledger as it stands: they never act on
        a pre-split count. The close overwrites them (and zeroes a row whose sleeve was sold in between)."""
        md_stops = self._md_stops() if self._md_sleeves() else {}
        from kaibot.engine.failsafe import clean

        rows = {k: sl.qty for k, sl in b.sleeves.items()} if qty is None else dict(qty)
        for (strategy, sym), q in rows.items():
            if q <= 1e-9:
                continue
            sl = b.sleeves.get((strategy, sym))
            ms = md_stops.get((clean(strategy), clean(sym)))
            if sl is not None and sl.qty > 1e-9:
                cost, mark, opened, divs = sl.avg_cost, sl.mark if sl.mark is not None else sl.avg_cost, sl.opened, \
                    sl.dividends
            else:  # follow-up of #333: a planned buy not held yet, at its last price (unpriced)
                px = self._md_price(sym) or 0.0
                cost, mark, opened, divs = px, px, str(today), 0.0
            self._position_row({
                "run_id": b.run_id, "session": str(today), "strategy": strategy, "symbol": sym, "qty": q,
                "avg_cost": round(cost, 4), "mark": round(mark, 4), "priced": False,
                "opened_session": opened, "dividends": round(divs, 2),
                "stop_px": round(ms, 2) if ms is not None else None,
                "stop_state": self._stop_state(strategy, ms is not None)})
        self.store.event("positions_rewritten", {"session": str(today), "why": why,
                                                 "sleeves": len(b.sleeves)}, b.run_id)

    LATE_RETRY_MIN = 5  # a difference that stays: the venue's record is read again at most every 5 minutes

    def _late_actions(self, m: int) -> None:
        """#266: the share count at the broker changed after the session start (Alpaca credited a split or a
        spin-off late). The session's corporate-actions step runs again (idempotent: every action is booked once
        by its ref) when a difference first shows, before the drift check halts entries; if the books then match,
        a drift halt it caused is cleared and the account is checked again. Only with the daily path."""
        if self.daily is None or self.corporate_actions is not None or self.day is None or self.run is None:
            return
        b = self.ledger.book(self._rid())
        if b is None or not b.sleeves:
            return
        try:
            bpos = self.broker.positions()
        except Exception as e:  # #694: the broker's read, open-ended; the drift check holds buys while it fails
            log.info("late corporate actions: broker positions unreadable (%s): checked again next minute",
                     type(e).__name__)
            return
        unsettled = {p.symbol for p in self.pos.values() if p.status != "open" or p.exit_order_id}
        unsettled |= {k[1] for x in self._md_exits.values() for k, _ in x["keys"]} | self.daily.working_symbols()
        mine = b.qty_by_symbol()
        intraday: dict[str, float] = {}
        for p in self.pos.values():
            intraday[p.symbol] = intraday.get(p.symbol, 0.0) + p.qty
        diff = tuple(sorted(s for s in set(mine) | set(bpos) if s not in unsettled
                            and abs(float((bpos.get(s) or {}).get("qty") or 0) - intraday.get(s, 0.0)
                                    - mine.get(s, 0.0)) > 1e-9))
        if not diff:
            self._late_seen = None
            return
        last = getattr(self, "_late_seen", None)
        if last is not None and last[0] == diff and m - last[1] < self.LATE_RETRY_MIN:
            return
        self._late_seen = (diff, m)
        before = dict(mine)
        self._corporate_actions(self.day.day)
        if b.qty_by_symbol() == before:
            return
        self.check_account()
        self._startup_reconcile("recheck")
        # QA-Ops (#285): the drift halt is lifted only when the books really agree again (the account guard and the
        # reconciliation both clean), not merely because one symbol's count moved
        if self.kill == "drift" and self.account_clean and self._reconciled():
            self._drift_seen = {}
            self.kill = None
            self.store.event("halt_cleared", {"kind": "drift", "day": str(self.day.day),
                                              "why": "a late corporate action was adopted"}, self._rid())

    def _adopt_actions(self, b, today: date) -> None:
        """#266: what Alpaca did before the open to a held multi-day position, adopted from the venue's positions and
        a sourced record (Alpaca's corporate actions of the last days), never guessed:
        - a spin-off: the new symbol is at the broker, not in the ledger, and the record names a held parent. Its
          shares (checked against the record's ratio when it has one: whole shares, +-1) are booked to the parent's
          holders at Alpaca's cost basis, which moves from the parent (`spinoff`); `origin` makes the daily path
          sell them at the next closing auction;
        - a symbol change: the old symbol is gone at the broker, the new one holds exactly the ledger's shares.
        Anything else is left as it is (the account guard and the reconciliation keep entries blocked) and said."""
        try:
            bpos = self.broker.positions()
        except Exception:
            log.exception("corporate actions: positions unreadable")
            return
        mine = b.qty_by_symbol()
        intraday = {p.symbol for p in self.pos.values()}
        foreign = {s: p for s, p in bpos.items() if s not in mine and s not in intraday}
        gone = {s for s, q in mine.items() if q > 0 and s not in bpos}
        if not foreign and not gone:
            return
        try:
            links = self.recent_actions(today, sorted(mine))
        except Exception as e:
            log.exception("corporate actions: Alpaca's record unreadable")
            self.store.event("corporate_actions_error", {"day": str(today), "error": str(e)[:200],
                                                         "action": "nothing adopted; entries stay blocked"},
                             self._rid())
            return
        for x in [y for y in links if y.kind == "cash_merger" and y.symbol in gone]:
            self._book_cash_merger(b, x, today)
            gone.discard(x.symbol)
        for x in links:
            p = foreign.get(x.new_symbol)
            why = None
            if p is None:
                continue
            q = float(p.get("qty") or 0)
            if x.kind == "spin_off" and mine.get(x.symbol, 0) > 0:
                if x.ratio is None:  # QA-Ops (#275): no ratio, no count to match the venue's shares against
                    why = (f"the record gives no share ratio for {x.new_symbol}: the venue's {q:g} shares cannot "
                           "be checked")
                elif abs(q - math.floor(mine[x.symbol] * x.ratio + 1e-9)) > 1:
                    why = f"the venue credited {q:g} shares, the record's ratio gives {mine[x.symbol] * x.ratio:g}"
                else:
                    basis = p.get("cost_basis")
                    cost = float(basis) if basis not in (None, "") else q * float(p.get("avg_entry_price") or 0)
                    self._book("spinoff", {"run_id": b.run_id, "symbol": x.symbol, "new_symbol": x.new_symbol,
                                           "ex_date": x.on, "qty": q, "cost": round(cost, 6), "ref": x.ref})
                    self.store.event("spin_off_adopted", {"parent": x.symbol, "symbol": x.new_symbol, "qty": q,
                                                          "cost_basis": round(cost, 2), "ex_date": x.on,
                                                          "ref": x.ref, "action": "sold at the next closing auction"},
                                     self._rid())
                    foreign.pop(x.new_symbol)
                    continue
            elif x.kind == "symbol_change" and x.symbol in gone:
                if abs(q - mine[x.symbol]) > 1e-9:
                    why = f"the venue holds {q:g} shares of {x.new_symbol}, the ledger {mine[x.symbol]:g} of {x.symbol}"
                else:
                    self._book("symbol_change", {"run_id": b.run_id, "symbol": x.symbol, "new_symbol": x.new_symbol,
                                                 "ex_date": x.on, "ref": x.ref})
                    self.store.event("symbol_change_adopted", {"symbol": x.symbol, "new_symbol": x.new_symbol,
                                                               "qty": q, "ex_date": x.on, "ref": x.ref}, self._rid())
                    foreign.pop(x.new_symbol)
                    gone.discard(x.symbol)
                    continue
            said = getattr(self, "_not_adopted_said", set())
            if why and (str(today), x.ref, why) not in said:  # #285: the late retry says it once a session
                self._not_adopted_said = said | {(str(today), x.ref, why)}
                self.store.event("corporate_action_not_adopted", {"kind": x.kind, "symbol": x.symbol,
                                                                  "new_symbol": x.new_symbol, "why": why,
                                                                  "action": "entries stay blocked: check the "
                                                                            "paper account"}, self._rid())

    def _book_cash_merger(self, b, x, today: date) -> None:
        """#266: Alpaca removed a held position and paid cash for it under a sourced cash-merger record: every
        sleeve of the stock is closed at the cash price (a `fill` sale with no fee, reason `cash_merger`), so the
        cash is in the ledger, the sleeve is gone and nothing tries to sell it. The price is Alpaca's own cash
        (its MA cash row over the shares held) when it has posted it, else the record's rate."""
        qty = sum(s.qty for s in b.holders(x.symbol))
        px = float(x.ratio)
        try:
            rows = self.broker.activities(self.now() - timedelta(days=10), types=("MA",))
            paid = 0.0
            for r in rows:
                if r.get("symbol") != x.symbol:
                    continue
                try:  # QA-Ops on #545: only a finite amount is Alpaca's cash (else the record's rate is used)
                    amt = payloads.number(r.get("net_amount"), "net_amount")
                except PayloadError as e:
                    self._payload_refused(f"alpaca:{r.get('id')}", f"cash merger {x.symbol}: {e}")
                    paid = 0.0
                    break
                paid += amt if amt > 0 else 0.0
            if paid > 0 and qty > 0:
                px = paid / qty
        except Exception:
            log.exception("cash merger %s: Alpaca's MA rows unreadable (the record's rate is used)", x.symbol)
        for s in list(b.holders(x.symbol)):
            self._book("fill", {"run_id": b.run_id, "strategy": s.strategy, "symbol": x.symbol, "side": "sell",
                                "qty": s.qty, "px": px, "fees": 0.0, "reason": "cash_merger", "ref": x.ref,
                                "session": str(today), "ts": self.now()})
        self.store.event("cash_merger_booked", {"symbol": x.symbol, "qty": qty, "price": round(px, 6),
                                                "cash": round(qty * px, 2), "ex_date": x.on, "ref": x.ref},
                         self._rid())

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
            try:  # QA-Ops on #545: a payment that is not a finite amount does not close its accrual
                net = payloads.dividend_amount(r)
            except PayloadError as e:
                self._payload_refused(aid, f"dividend payment {aid} of {r.get('symbol')}: {e}")
                continue  # the accrual stays a receivable; the row is not booked either
            accrued = float(acc[ref]["amount"])
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
            today = str(self.day.day) if self.day else str(self.now().astimezone(_et()).date())
            if self._splits_booked[0] != today:
                self._splits_booked = (today, set())
            self._splits_booked[1].add(a.symbol)  # M9 1.1: no book-end check on that symbol's prices today
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

    def _venue_fees(self, order_id: str | None, ts, since=None) -> float:
        """#238: the fees the venue charged on this order's fills: the `fees` of its FILL activities from a minute
        before the order was sent (`since`, its `submitted_at`) or else a minute before the fill (`ts`) on, each
        activity once. `SimBroker` reports them there; Alpaca's FILL activities carry none (its regulatory fees are
        separate FEE activities at the end of the day), so nothing is booked on the fill. Never an estimate: an
        estimate the venue does not charge piles up in the ledger's cash with every sale until the reconciliation
        blocks entries. A failed read books 0 and says so (the reconciliation then shows any difference).
        QA-Ops (#241): from the order's sending, not from its last fill, so the parts of a partly filled order that
        traded more than a minute before the last part are counted; and by activity id, so an activity the venue
        reports twice (a repeated page) is counted once."""
        if order_id is None:
            return 0.0
        after = None
        for t in (ts, since):
            if t:
                t = t if isinstance(t, datetime) else datetime.fromisoformat(str(t))
                t = t - timedelta(minutes=1)
                after = t if after is None or t < after else after
        try:
            rows = self.broker.activities(after, types=("FILL",))
        except Exception as e:  # a fee unread must not stop booking the fill
            log.warning("venue fee of order %s unread: %s", order_id, e)
            self.store.event("venue_fee_unread", {"order_id": order_id, "error": f"{type(e).__name__}: {e}"[:160]},
                             (self.run or {}).get("id"))
            return 0.0
        seen: set = set()
        total = 0.0
        for a in rows:
            if str(a.get("order_id")) != str(order_id):
                continue
            try:  # QA-Ops on #545: a refused activity's fee is not one (the order manager keeps the fill out too)
                if a.get(payloads.INVALID):
                    raise PayloadError(str(a[payloads.INVALID]))
                fee = 0.0 if a.get("fees") in (None, "") else payloads.number(a.get("fees"), "fees", nonneg=True)
            except PayloadError as e:
                self._payload_refused(str(a.get("id")), f"fee of order {order_id}: {e}")
                continue
            aid = a.get("id")
            if aid is not None:
                if aid in seen:
                    continue
                seen.add(aid)
            total += fee
        return total




    # ================= exits =================





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
        if self.intraday:  # M10 1.19: the intraday path's positions
            self._flatten_positions(reason, failed, t0)
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
        try:
            md = self._md_minute()
        except Exception:  # QA-Ops on #384: the block is a report; it must never fail the minute (and its retry)
            log.exception("minute row: multi_day block unreadable, row written without it")
            md = None
        # #384: `cash` is the row's equity less everything the row lists (day positions at `last`, multi-day sleeves
        # at their marks), in whole cents, so cash + multi_day.invested + sum(qty x last) is the equity exactly
        listed = sum(round(100 * (v["last"] or 0) * v["qty"]) for v in pos.values())
        listed += sum(round(100 * x["value"]) for x in (md or {}).get("positions", ()))
        row = {
            "run_id": d.run_id,
            "ts": self.now().replace(microsecond=0),
            "cash": (round(100 * eq) - listed) / 100,
            "positions": pos,  # day positions only: a multi-day sleeve never goes here (#384 contract)
            "equity_venue": round(d.start_equity + d.realized_venue + unreal, 2),
            "equity_shadow": round(d.start_equity + d.realized_shadow + unreal, 2),
        }
        if md is not None:
            row["multi_day"] = md
        self.store.insert("ledger", row, returning=False)
        if self.run and self.peak and self.peak > float(self.run.get("peak_equity") or 0):
            self.store.update("run", {"id": self.run["id"]}, {"peak_equity": round(self.peak, 2)})
            self.run["peak_equity"] = round(self.peak, 2)

    def _md_mark(self, symbol: str, sl, b) -> tuple[float, str | None, str]:
        """#384: the price a multi-day sleeve is counted at in this minute's equity (`equity_now`: the same order),
        when that price is from, and where it comes from."""
        px = self.market.last_close(symbol)
        if px:
            end = getattr(self.market, "last_bar_end", None)
            t = end(symbol) if end is not None else None
            return float(px), _iso(t), "minute_bar"
        cp = (self._bpos.get(symbol) or {}).get("current_price")
        if cp and float(cp):
            return float(cp), _iso(self._bpos_ts), "broker"
        if sl.mark is not None:
            last = b.closes[-1].session if b.closes else None
            return float(sl.mark), _close_ts(last), "last_close"
        return float(sl.avg_cost), None, "cost"

    def _md_minute(self) -> dict | None:
        """#384: the minute row's `multi_day` block: the ledger's cash and every multi-day sleeve with its shares and
        the mark this row's equity counts it at. None (no key in the row) for an engine without the daily path that
        holds no multi-day sleeve: its rows stay exactly what they were. Contract: issue #384, docs/ops/ledger.md."""
        sleeves = self._md_sleeves()
        if self.daily is None and not sleeves:
            return None
        rid = self._rid() or (self.day.run_id if self.day else None)
        b = self.ledger.book(rid) if rid is not None else None
        if b is None:
            return None
        out = []
        for (rule, sym), sl in sorted(sleeves.items()):
            mark, ts, src = self._md_mark(sym, sl, b)
            out.append({"rule": rule, "symbol": sym, "qty": sl.qty, "avg_cost": round(sl.avg_cost, 4),
                        "mark": round(mark, 4), "mark_ts": ts, "mark_from": src, "value": round(sl.qty * mark, 2)})
        settling = {k[1] for x in self._md_exits.values() for k, _ in x["keys"]}
        if self.daily is not None:
            try:
                settling |= set(self.daily.working_symbols())
            except Exception:  # the row is a report: an unreadable order book must not stop the minute
                log.exception("multi-day working orders unreadable (minute row)")
        return {"v": 1, "cash_ledger": round(b.cash, 2), "invested": sum(round(100 * x["value"]) for x in out) / 100,
                "settling": sorted(settling), "positions": out}

    def _finalize_day(self) -> None:
        d = self.day
        if self.intraday:  # M10 1.19: the agent room's counterfactuals
            self._day_end_room()
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
            self._book_line_marked()  # Product, 19 Nov 2026: the book line at the close's marks
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
        if self.intraday:  # M10 1.19: the shadow rules' counterfactual P&L
            self._day_end_shadow()
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
        self._md_plan = (None, {})  # QA-Ops on #342: the day's rows below replace the planned buys
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
            self._position_row({
                "run_id": d.run_id, "session": str(d.day), "strategy": r["strategy"], "symbol": r["symbol"],
                "qty": r["qty"], "avg_cost": round(r["avg_cost"], 4), "mark": round(r["mark"], 4),
                "priced": r["priced"], "opened_session": r["opened"], "dividends": round(r["dividends"], 2),
                "stop_px": round(p.stop, 2) if p is not None else (round(ms, 2) if ms is not None else None),
                "stop_state": self._stop_state(r["strategy"], (p is not None and bool(p.stop_order_id))
                                               or ms is not None)})
        # #266: a row written at this session's open (`_write_positions`) for a sleeve sold since is zeroed
        held_now = {(r["strategy"], r["symbol"]) for r in c.positions}
        if self.daily is not None:
            for r in self.store.select("ledger_position", {"run_id": f"eq.{d.run_id}", "session": f"eq.{d.day}"}):
                if (r["strategy"], r["symbol"]) not in held_now and float(r.get("qty") or 0) != 0:
                    self.store.upsert("ledger_position", {**r, "qty": 0.0, "priced": False},
                                      "run_id,session,strategy,symbol")

    def _stop_state(self, strategy: str, resting: bool) -> str:
        """`ledger_position.stop_state` (Product, 19 Nov 2026): "resting" with a stop at the broker; without one,
        "none_by_design" for a sleeve of a rule that exits by its target list (live_daily.exit_kind: no stop in its
        pre-registration), else "missing" (a stop that should be there: the dashboard's alarm)."""
        if resting:
            return "resting"
        if self.daily is not None:
            from kaibot.engine.live_daily import exit_kind

            if exit_kind(strategy) == "target_list":
                return "none_by_design"
        return "missing"

    def _position_row(self, row: dict) -> None:
        """Upsert one `ledger_position` row. Until migration 0028 is applied the database refuses "none_by_design"
        (its check constraint): the row is then written as "missing", as before, and never lost (the fail-safes read
        these rows); one `stop_state_fallback` event says so, and this process keeps writing "missing". In production
        `ledger_position` is a local table of the JournalStore: the refusal reaches the replicator, which sends the
        row as "missing" and reports it the same way (QA-Ops on #339, `journal.Replicator`)."""
        key = "run_id,session,strategy,symbol"
        if row.get("stop_state") == "none_by_design":
            if not getattr(self, "_nbd_refused", False):
                try:
                    self.store.upsert("ledger_position", row, key)
                    return
                except Exception as e:
                    if "stop_state_check" not in str(e) and "23514" not in str(e):
                        raise
                    self._nbd_refused = True
                    self.store.event("stop_state_fallback", {
                        "error": str(e)[:200], "action": "apply migrations/0028_stop_none_by_design.sql, then restart "
                                                         "the engine; until then rows say 'missing'"}, self._rid())
            row = {**row, "stop_state": "missing"}
        self.store.upsert("ledger_position", row, key)

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
        dc = getattr(self.market, "data_block_status", None)
        dcs = dc() if dc is not None else None
        if isinstance(dcs, dict):  # M12 3.11: only with KAIBOT_DATA_CHECKS_BLOCK on (absent otherwise: no change)
            st["data_checks"] = dcs
        self._daily_status(st)
        if self.run is None and d is not None and d.run_id:
            st["run_ended"] = d.run_id  # S74b (#137): today's run has ended; the engine only winds it down
        return st


def run_refusal(amount, level, policies) -> str | None:
    """M9 1.15: why a paper run with this pot and level may not be started (None: it may). The pot is a finite
    number from RUN_AMOUNT_MIN_USD to RUN_AMOUNT_MAX_USD (the database's own constraint on `run.amount_usd`), the
    level one of the loaded profiles."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float, str)):
        return f"amount_usd is not a number: {_brief(amount)}"
    try:
        x = float(amount)
    except (ValueError, OverflowError):  # OverflowError: a whole number too large for a float (QA-Ops, #513)
        return f"amount_usd is not a number: {_brief(amount)}"
    if not math.isfinite(x) or not RUN_AMOUNT_MIN_USD <= x <= RUN_AMOUNT_MAX_USD:
        return (f"amount_usd {_brief(amount)} is outside the limits "
                f"({RUN_AMOUNT_MIN_USD:,.0f} to {RUN_AMOUNT_MAX_USD:,.0f} dollars)")
    if not isinstance(level, str) or level not in policies:
        return f"unknown level {_brief(level)}"
    return None


