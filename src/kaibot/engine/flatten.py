"""The full flatten and the multi-day sleeves' sales (#694, audit 2 ticket 5.14): `flatten_all` inside its time
limit, the daily path's own sales of the book (`_flatten_md`), their heartbeat, and the flatten sales' recovery
and sync after a restart.

`Flatten` is a mixin of `core.Engine` and holds no state of its own: every method reads and writes the engine's
attributes as before. The methods moved from engine/core.py unchanged.
"""

from __future__ import annotations

from kaibot.broker import payloads
from kaibot.broker.alpaca import CANCEL_POLL_S, CANCEL_POLLS
from kaibot.broker.payloads import PayloadError
from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine import order_path
from kaibot.engine.core_common import CLOSE_MAX_S, FLATTEN_BUDGET_S, INTRADAY_ONLY, FlattenIncomplete, _et, log


class Flatten:
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
