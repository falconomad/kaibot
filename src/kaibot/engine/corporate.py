"""Corporate actions, dividends and the account's fees (#694, audit 2 ticket 5.14): the session start's read of
the broker's activities, the splits, spin-offs and cash mergers booked to the ledger (also when Alpaca credits
them after the start), the dividends accrued at the ex-date and matched to their payments, the venue's fees, and
the positions rows a corporate action rewrites.

`CorporateActions` is a mixin of `core.Engine` and holds no state of its own: every method reads and writes the
engine's attributes as before. The methods moved from engine/core.py unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from kaibot.broker import payloads
from kaibot.broker.payloads import PayloadError
from kaibot.engine.core_common import _et, _posted_since, log
from kaibot.engine.dividends import match_payment, open_accruals
from kaibot.engine.ledger import (
    CORPORATE_ACTIVITIES,
    CORPORATE_ACTIVITY_GROUPS,
    FEE_ACTIVITIES,
    Action,
    actions_from_activities,
    fees_from_activities,
)


class CorporateActions:
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
