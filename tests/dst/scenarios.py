"""Hand-written scenarios of the deterministic simulation (M12 ticket 3.2, #464), and the planted bugs.

A hand-written scenario answers one "what happens if" question: a fixed plan, no random faults, one or two named
ones (`Forced`), one outage. Each of the engine-safety findings of milestone M9 that were fixed has one here
(`FINDINGS`), with the planted bug that takes the fix away again (`PLANTED`: a patch of one method, applied by the
test and undone after it). The engine as it is passes each scenario; with the fix taken away the scenario breaks an
invariant. That is the proof that the simulation would have found the finding, and that it will notice the day a
refactoring loses the fix.
"""

from __future__ import annotations

from datetime import time, timedelta

from world import D0, D1, D2, D3, THANKSGIVING_NO_CALENDAR, Outage, Scenario, Week, et

from kaibot.broker.faulty import NO_FAULTS, Forced
from kaibot.broker.sim import NY
from kaibot.engine import live_daily as ld
from kaibot.engine import order_path
from kaibot.engine.core import Engine
from kaibot.engine.orders import OrderManager

ON_TIME = {D0: et(D0, "16:10"), D1: et(D1, "16:10"), D2: et(D2, "16:10"), D3: et(D3, "16:10")}


def quiet(plan: dict, **kw) -> Scenario:
    """A scenario without random faults: every daily view on time, no outage, no watchdog, unless `kw` says so."""
    kw.setdefault("ready", ON_TIME)
    kw.setdefault("profile", NO_FAULTS)
    return Scenario(0, {D1: None, D2: None, D3: None, **plan}, **kw)


def quiet_in(week: Week, plan: dict, **kw) -> Scenario:
    """`quiet` in another week (audit 2, 5.16), on the live process's engine (the book loop) unless `kw` says so:
    every evening's view ten minutes after that session's close."""
    kw.setdefault("ready", {d: et(d, week.close(d)) + timedelta(minutes=10) for d in week.evenings})
    kw.setdefault("profile", NO_FAULTS)
    kw.setdefault("book_loop", True)
    return Scenario(0, {**dict.fromkeys(week.sessions), **plan}, week=week, **kw)


# ---------------------------------------------------------------- the planted bugs
def no_late_decision(mp) -> None:
    """M9 1.3 taken away: a session that opens without its decision record goes without one."""
    mp.setattr(ld.LiveDaily, "catch_up", lambda self, session, force=False: [])


def no_late_send(mp) -> None:
    """M9 1.4 taken away: a decision whose window was missed is never sent."""
    mp.setattr(ld.LiveDaily, "_unsent", lambda self, rule, session, rid: None)


def cancel_never_sent_again(mp) -> None:
    """#491 taken away: a cancel request is made once; one the venue never got is not repeated."""
    cancel, ask = OrderManager.cancel, OrderManager._ask_cancel

    def cancel_once(self, coid):
        self._first_request = True
        try:
            return cancel(self, coid)
        finally:
            self._first_request = False

    def ask_once(self, r):
        if getattr(self, "_first_request", False):
            ask(self, r)

    mp.setattr(OrderManager, "cancel", cancel_once)
    mp.setattr(OrderManager, "_ask_cancel", ask_once)


def lost_without_evidence(mp) -> None:
    """M9 1.11 taken away: an order two lookups did not find is called lost, and the decision is sent again. Since
    #535 a fill of an order called lost is adopted, a second guard against the same hole: one guard gone is not
    visible from outside (the point of having two), so the plant takes that away too."""
    mp.setattr(OrderManager, "_lost_evidence", lambda self, r, over=False: (None, None))
    mp.setattr(OrderManager, "_claim", lambda self, a: None)


def unbooked_fill_of_today_only(mp) -> None:
    """#507 taken away: the target loop waits only for an unbooked fill of today's own orders. The live path has a
    second guard against the same double buy (the reconciliation's "unbooked" difference refuses every buy: the
    simulation shows that it holds, see test_dst.py), so the planted bug takes that away too: one guard gone is not
    visible from outside, which is the point of having two."""
    unbooked = OrderManager.unbooked

    def today_only(self, symbol, strategy=None, session=None):
        return unbooked(self, symbol, strategy, session or str(self.now().astimezone(NY).date()))

    mp.setattr(OrderManager, "unbooked", today_only)
    mp.setattr(ld._Gate, "allows", lambda self, symbol, side, purpose="target", qty=None: None)
    # #446 step 3: the order seam's trading-state gate is a third guard (the same reconciliation block, asked at
    # the send itself): it refuses the second buy too, so it is taken away with the other two
    mp.setattr(order_path, "_gate", lambda gate, action, side, what: None)


def watchdog_cancel_not_carried(mp) -> None:
    """M9 1.6 taken away: a buy the watchdog cancelled counts as cancelled by the venue and is never sent again."""
    mp.setattr(ld.LiveDaily, "_watchdog_cancels", lambda self: set())


def calendar_fallback_regular_close(mp) -> None:
    """#733 taken away (audit 2, 5.3): a calendar read that fails gives the regular 16:00 close, so on an early close
    the window is taken to be 15:50-15:57 ET, after the venue has closed at 13:00. (Before #733 `_close` gave None
    and `policy_for` made 16:00 of it, as here; the read was tried again at the next call.)"""
    def old_close(self, day):
        if day in self._closes:
            return self._closes[day]
        try:
            close = self.session_close(day)
        except Exception:
            return time(16, 0)  # "unreadable: 15:45", the comment said; the code gave 16:00
        self._closes = {day: close or time(16, 0)}
        return self._closes[day]

    mp.setattr(ld.LiveDaily, "_close", old_close)


def flatten_outside_the_order_manager(mp) -> None:
    """M9 1.8 taken away: a flatten sale goes straight to the broker and is booked only when it fills whole. Since
    M10 1.20 step 4 (#445) the engine has no such sale any more, so the planted bug puts back the deleted one: the
    older direct sale (`kbfl-` client id, `md_exit_intent`, booked by `_sync_md_exits` on a whole fill)."""
    from kaibot.broker.protocol import OrderRequest
    from kaibot.engine.failsafe import clean

    def direct(self, sym, keys, reason, rid):
        coid = f"kbfl-{clean(keys[0][0][0])[:40]}-{clean(sym)[:12]}-{self.now():%Y%m%d%H%M%S}"
        qty = sum(q for _, q in keys)
        self.journal.append("md_exit_intent", {"coid": coid, "run_id": rid, "symbol": sym, "reason": reason,
                                               "keys": [[list(k), q] for k, q in keys], "ts": self.now().isoformat()})
        o = self._order("sell",
                        lambda: order_path.submit(self.broker, OrderRequest(sym, "sell", qty, "market", "day", coid),
                                                  gate=self.trading_state),
                        coid=coid, symbol=sym, strategy=",".join(k[0] for k, _ in keys), qty=qty, reason=reason)
        self._md_exit_sent(o, rid, keys, reason)

    mp.setattr(Engine, "_md_exit_submit", direct)


# ---------------------------------------------------------------- one scenario per finding
FINDINGS: dict[str, tuple] = {
    # name: (the scenario, the planted bug, a word the violation must contain)
    "late start": (
        # the run starts after D0's close: D1 opens without its decision record
        quiet({D1: ("B",)}),
        no_late_decision, "the latest decision wanted ('B',)"),
    "missed window": (
        # the engine dies at 15:40 on the decision's fill session and is back after the close
        quiet({D1: ("B",)}, outages=(Outage(et(D1, "15:40"), 40),)),
        no_late_send, "the latest decision wanted ('B',)"),
    "lost cancel": (
        # the engine is paused at 10:00 on D2 and the watchdog puts an emergency stop on B; D2's decision wants
        # nothing, so the send cancels that stop before it sells B, and the first cancel request is lost. (Until
        # #533 this was a flatten whose buy cancel was lost; "Flatten and stop" now waits for that buy itself:
        # test_flatten_and_stop_waits_for_a_buy_whose_cancel_was_lost.)
        quiet({D1: ("B",), D2: ()}, watchdog=True, outages=(Outage(et(D2, "10:00"), 5, kill=False),),
              forced=(Forced("cancel_lost", "", 1),)),
        cancel_never_sent_again, "the book holds ('B',)"),
    "lost order sent again": (
        # the order is in, its answer is lost, and for a while the venue cannot find it by its client order id
        # (the open-orders list has it: the read M9 1.11 added)
        quiet({D1: ("B",)}, heal=D2,
              forced=(Forced("submit_timeout_accepted", "|B|buy"), Forced("lookup_lag", "|B|buy", 12),
                      Forced("hold", "|B|buy", 4))),
        lost_without_evidence, "are not the venue's"),
    "double buy": (
        # yesterday's buy filled, its activity arrives a day late, and today's decision wants the name again
        quiet({D1: ("B",), D2: ("B", "C")}, forced=(Forced("fill_dropped", "|B|buy", 1500),)),
        unbooked_fill_of_today_only, "beyond the target"),
    "watchdog cancel carry": (
        # the engine sleeps through the window right after the send; the watchdog cancels the slow buy
        quiet({D1: ("B",)}, watchdog=True, heal=D2, forced=(Forced("hold", "|B|buy", "never"),),
              outages=(Outage(et(D1, "15:51"), 12, kill=False),)),
        watchdog_cancel_not_carried, "the latest decision wanted ('B',)"),
    "calendar unreadable on an early close": (
        # audit 2, 5.16 / 5.3: Fri 27 Nov 2026 closes at 13:00; the broker's calendar goes down right after the
        # session's start has read it (09:31 ET) until 16:30, so the daily path's read of the day's close fails and
        # the close comes from the engine's local table (#733). The decision of 25 Nov wants C as well
        quiet_in(THANKSGIVING_NO_CALENDAR, {"2026-11-25": ("B",), "2026-11-27": ("B", "C")}),
        calendar_fallback_regular_close, "was not sent in its window"),
    "flatten partial fill": (
        # the owner flattens on D2; half of the sale fills, the rest never does that day
        quiet({D1: ("B",)}, flatten=et(D2, "11:00"), forced=(Forced("partial_fill", "|B|sell", 0.5),)),
        flatten_outside_the_order_manager, "the ledger's shares"),
}
