"""The multi-day book's own loop (M10 1.19, #444; engine audit, Stage 1B): one pass per completed minute.

    clock      the broker's clock, compared with this machine's (M9 1.10); closed: the day is finalised and the
               rules decide for the next session (`Engine._tick_closed`)
    sync       the order manager books the venue's fills (`LiveDaily.sync`)
    send       standing targets (engine/standing.py): the decision in force is taken if it is missing, sent if it
               was not, and its short buys are carried (`LiveDaily.send`); with the pre-open review when it is on
    reconcile  full-flatten sales booked, today's position rows, the ledger against the broker (the recheck of an
               open difference, late corporate actions, the per-minute drift check)
    risk       the marks of the holdings, the daily stop, the book's run end (two confirmed checks, M9 1.1)
    ledger     the minute row

Nothing of the intraday path runs here or before the send: no minute scan of the intraday universe, no bracket
entry, no stop management, no agent room, no shadow rule. That path is engine/intraday.IntradayEngine, which the
live process builds only with `intraday.enabled: true`; its tick runs these same steps in this same order around
its own.

Order of sync and send (M9 1.5, QA-Ops on #484, unchanged): they run first only when the minute just before was
completed (`done_m == m - 1`). On the first tick after a (re)start, or after a minute whose checks raised, the
engine's state is older than a minute or was never checked (a daily stop, a book end or a drift first seen now):
there the send keeps its place behind the reconcile and risk steps (fail closed).

Audit 2, 5.10 (the 1.5 residual): the drift check no longer fails the minute when the broker's positions cannot be
read (`Engine._check_drift` contains it and holds new buys only, engine/state.py `positions`). So a positions
endpoint that is down all window costs no minute: every minute completes, the next one's send runs first, and the
due sales go out while the buys wait for a read that works.

The marks: `Engine.market` here is engine/book_market.BookMarket on the live path (minute bars of the holdings
only), read after the send so a slow or failed read can never cost the send window (`Engine._market_update`: a
failed read leaves the bars as they were and is reported in the heartbeat).
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def tick(eng) -> dict:
    """One pass. Returns the heartbeat's status. Safe to run again for the same minute: a minute whose work is
    complete (`done_m`) is never run twice, and every step is journalled before it acts."""
    now = eng.now()
    clk = eng._broker_clock()  # ---- clock
    if not clk["is_open"]:
        return eng._tick_closed(clk, now)
    if eng.run is None and not eng._md_exits:  # (a flatten sale still to book keeps the loop going)
        return eng._daily_status({"state": "idle", "note": "no paper run: start one from the dashboard",
                                  "account_clean": eng.account_clean})
    if eng.run is not None and (eng.day is None or eng.day.day != now.astimezone(NY).date()):
        eng._start_day(now)
    d = eng.day
    if d is None:
        # M9 1.9 (audit M8): no run and no session (the run ended, then a restart) with a flatten sale still to
        # book: it is booked here, else a `new_run` command would wait for it for ever
        if eng._md_exits:
            eng._sync_md_exits()
        return eng._daily_status({"state": "idle", "account_clean": eng.account_clean})
    m = eng.market.last_complete_minute(now)
    first = m >= 0 and d.done_m == m - 1
    if first:
        eng._md_send_steps(d, sync=True)  # ---- sync, send
    fresh = eng._market_update(now)  # the holdings' marks, for the risk step
    if m < 0 or m == d.done_m:
        return eng._data_status(eng._status(m), fresh)  # this minute's work is complete: never run twice
    d.last_m = m
    eng._sync_orders()  # ---- reconcile: full-flatten sales the order manager finished
    if not first:
        eng._daily_step("sync", lambda: eng.daily.sync())
    eng._md_positions_after_fills(d.day)  # #333: near-close fills land before the fail-safes' flatten window
    eng._recheck_reconcile(m)
    eng._late_actions(m)  # #266: a split or spin-off Alpaca credits after the session start
    eng._check_drift()
    eq, _ = eng.equity_now()  # ---- risk
    eng._risk_halts(m, eq)
    if not first:
        eng._md_send_steps(d)
    if m >= d.flatten_m and not d.flattened:
        # Close minus 5: the intraday path flattens its sleeves here; this book holds none, so nothing is sold.
        # The mark is kept: the heartbeat's state turns from "trading" to "managing" at the same minute as before.
        d.flattened = True
    eng._ledger(eq)  # ---- ledger
    d.done_m = m
    return eng._data_status(eng._status(m), fresh)
