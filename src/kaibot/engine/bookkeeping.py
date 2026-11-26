"""The engine's books (#694, audit 2 ticket 5.14): the ledger it attaches and journals every order call to, the
minute's ledger and the multi-day sleeves' marks, the venue's fees, the session's close (`_finalize_day`,
`_close_ledger`, a session finalised late after a restart) and the reconciliation of the ledger with the broker.

`Bookkeeping` is a mixin of `core.Engine` and holds no state of its own: every method reads and writes the
engine's attributes as before. The methods moved from engine/core.py unchanged.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from kaibot.broker import payloads
from kaibot.broker.payloads import PayloadError
from kaibot.engine.clock import session_minutes
from kaibot.engine.config import ROOT
from kaibot.engine.core_common import (
    CASH_TOLERANCE_PCT,
    CASH_TOLERANCE_USD,
    Day,
    Pos,
    _close_ts,
    _et,
    _et_date,
    _holding,
    _iso,
    log,
)
from kaibot.engine.dividends import receivable
from kaibot.engine.execution import flatten_at_close
from kaibot.engine.journal import canon
from kaibot.engine.ledger import price_key
from kaibot.evaluation import gate_report


class Bookkeeping:
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
