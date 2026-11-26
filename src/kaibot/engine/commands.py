"""The owner's commands (#694, audit 2 ticket 5.14): `apply_commands` and what it runs for each kind (new run,
pause, resume, set level, flatten and stop, accept reconcile), the check of a command row before anything is done
(`run_refusal`, `_command_refusal`), a command that waits for a flat book or for the market, and the run's status.

`Commands` is a mixin of `core.Engine` and holds no state of its own: every method reads and writes the engine's
attributes as before. The methods and `run_refusal` moved from engine/core.py unchanged; core.py imports
`run_refusal` back.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, timedelta

from kaibot.engine.core_common import (
    COMMAND_KINDS,
    LEVEL_NAMES,
    RUN_AMOUNT_MAX_USD,
    RUN_AMOUNT_MIN_USD,
    FlattenIncomplete,
    _brief,
    log,
)
from kaibot.engine.orders import St, _day_over
from kaibot.engine.reconcile import ACCEPT_COMMAND


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


class Commands:
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
