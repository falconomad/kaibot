"""The multi-day book's risk checks (#694, audit 2 ticket 5.14): its P&L since the run began, the prices it is
measured on, its peak, the run-end and band verdicts, the confirmation of a breach and the book's end.

`BookRisk` is a mixin of `core.Engine` and holds no state of its own: every method reads and writes the engine's
attributes as before. The methods moved from engine/core.py unchanged. `_book_stop` and `_band_table`, which read
the switch file (`core.BOOK_STOP_PATH`, patched by tests and the DST world), stay in core.py.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from kaibot.engine.core_common import (
    BREACH_CONFIRM_S,
    MD_AGREE_PCT,
    MD_BAR_DEAD_MIN,
    MD_BAR_MAX_AGE_MIN,
    MD_BROKER_MAX_AGE_S,
    MD_FAR_PCT,
    MD_UNCONFIRMED_ALARM_MIN,
    _et,
    log,
)
from kaibot.risk import book_stop as bstop
from kaibot.risk.gate import drawdown_pct


class BookRisk:
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
