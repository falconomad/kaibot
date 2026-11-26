"""The intraday path of the engine (M10 1.19, #444): `IntradayEngine`, the engine of engine/core.py with the steps
of rules that are flat by the close added around the multi-day book's own (engine/book_loop.py).

`core.Engine(...)` returns this class unless it is called with `intraday=False`, and only then is this module
imported. The live process passes `intraday.enabled` of config/profiles.yaml, which is off: the retired dip rule's
soak test ended with M10 1.19 (Product, 21 Nov 2026, decision D4 of docs/research/engine_audit/decisions.md).
Every other engine (tests, replays, SimBroker research, `scripts/spy_engine.py`) is built here, line for line as
before: every method below was moved from engine/core.py unchanged.

Per completed minute: sync orders/fills -> mark to market -> daily stop / run drawdown -> each open position's
strategy manages its stop -> time exit -> each live strategy that is due proposes entries (strategies/base.py,
in registry order), every one through the risk gate -> each position's strategy may close it out (MOC) ->
ledger -> shadow strategies. Prices, stops, targets and exits come from the strategies (strategies/registry.py:
which ones a level runs is config/profiles.yaml), sizes from `risk/gate.py`; no LLM sets any of them.

This path has its own orders (a bracket entry, its legs, a market exit or `close_position`), its own client ids
(`kb-<run>-<signal>-1`) and its own start-up recovery (`_adopt_orphans`): the second order path of the engine
audit's "Three order paths". It exists only in this class. The multi-day book's orders all go through
engine/orders.OrderManager.

Safety:
- Every entry is a bracket order, so the stop and the 2R target live AT THE BROKER and fire even if this
  process (or the laptop) stops.
- Intraday positions the engine knows from an earlier session are closed at the first tick of the next open.
- Three entry orders in a row refused by the broker (S74) stop new entries.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import numpy as np

from kaibot.broker import payloads
from kaibot.broker.payloads import PayloadError
from kaibot.broker.protocol import OrderRequest
from kaibot.broker.shadow_costs import QuoteCtx, sell_fees, shadow_fill_price
from kaibot.engine import (
    order_path,
    shadow,  # S14 shadow strategies: never trade, never raise
    tickercheck,
)
from kaibot.engine.core import (
    CANCEL_UNCONFIRMED,
    CLOSE_MAX_S,
    FLATTEN_BUDGET_S,
    FLATTEN_TRIES,
    INTRADAY_ONLY,
    Day,
    Engine,
    Pos,
    Positions,
    _et,
    _holding,
    _iso,
    _refused,
    log,
)
from kaibot.engine.execution import flatten_at_close
from kaibot.engine.roomflow import RoomFlow
from kaibot.engine.userconfig import Rules
from kaibot.replay.sim import half_spread, sell_px
from kaibot.risk.gate import Proposal, Side, Snapshot, evaluate, intraday_equity
from kaibot.strategies import registry
from kaibot.strategies.base import CloseOut, Context, Event, MoveStop, Strategy, due

__all__ = ["IntradayEngine"]

# #694: moved from engine/core.py unchanged: what only the intraday path uses.
# S74 (#114). A 403 or 422 on an entry order means the venue refused it: there is no order. One refusal is about one
# candidate (the stop is no longer below the market, the symbol cannot be traded right now). Three in a row with no
# accepted order in between, the same "repeated" as MAX_ERROR_STREAK, is about the account or the order format.
BROKER_REJECT_STATUS = (403, 422)
MAX_REJECT_STREAK = 3


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


def _age_s(t: str | None, now: datetime) -> float:
    if not t:
        return 1e9
    try:
        ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return max(0.0, (now - ts).total_seconds())
    except ValueError:
        return 1e9


class IntradayEngine(Engine):
    intraday = True
    # audit 2 5.10 is the book loop's: this retired path keeps M9 1.5's fail closed, a failed positions read of the
    # drift check fails its minute and the retry does that minute's work (tests/test_engine_soak.py d6)
    CONTAIN_DRIFT_READ = False

    # ================= what the book's engine leaves to this path =================
    def _cfg_version(self) -> int:
        return self.day.rules.version if self.day and self.day.rules else 0

    def broker_quotes(self, symbols: list[str]) -> dict:
        try:
            return self.market.data.latest_quotes(symbols)
        except Exception:
            log.exception("quotes failed")
            return {}

    def _roomflow(self, room, pack_fn):
        # S34: the agent room (None = rules-only). It can only veto or halve a candidate the rules approved.
        return RoomFlow(self, room, pack_fn) if room is not None else None

    def _recover_intraday(self) -> None:
        try:
            self._adopt_orphans()
        except Exception:  # adoption is best effort: if it fails the order stays unknown and blocks entries
            log.exception("order adoption failed")

    def _sync_positions(self) -> None:
        """The intraday positions' entries, bracket legs and exits. QA-Ops on #545: a fill price that is not a
        finite number above zero (broker/payloads.fill_price, also when the adapter marked the view) is never
        booked: the position is read again at the next sync, and the refusal is an event (`_payload_refused`)."""
        for p in list(self.pos.values()):
            if p.status == "closed":
                continue
            o = self.broker.order(p.exit_order_id or p.order_id)
            if p.status == "pending":
                if o["status"] == "filled":
                    try:  # read again at the next sync, never booked at a price that is not one
                        payloads.fill_price(o)
                    except PayloadError as e:
                        self._payload_refused(f"order:{o.get('id')}", f"entry {p.symbol}: {e}")
                        continue
                    self._on_entry_fill(p, o)
                elif o["status"] in ("canceled", "expired", "rejected"):
                    self._close_trade(p, None, "not_filled")
                continue
            if p.exit_order_id:  # we sent a market sell (time / daily stop / manual)
                if o["status"] == "filled":
                    try:
                        px = payloads.fill_price(o)
                    except PayloadError as e:
                        self._payload_refused(f"order:{o.get('id')}", f"exit {p.symbol}: {e}")
                        continue
                    self._on_exit_fill(p, px, p.exit_reason or "manual", o.get("filled_at"),
                                       o.get("id"), o.get("submitted_at") or o.get("created_at"))
                continue
            for leg in o.get("legs") or []:
                if leg["status"] == "filled":
                    try:  # a leg is not marked by the adapter (nested): checked here, with its parent's mark
                        px = payloads.fill_price({**leg, payloads.INVALID: o.get(payloads.INVALID)})
                    except PayloadError as e:
                        self._payload_refused(f"order:{leg.get('id')}", f"exit leg {p.symbol}: {e}")
                        break
                    is_target = leg.get("type") == "limit"
                    reason = "target" if is_target else ("trail" if p.armed else "stop")
                    self._on_exit_fill(p, px, reason, leg.get("filled_at"), leg.get("id"),
                                       leg.get("submitted_at") or o.get("submitted_at") or o.get("created_at"))
                    break

    def _flatten_positions(self, reason: str, failed: dict, t0: float) -> None:
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

    def _day_end_room(self) -> None:
        if self.roomflow is not None:
            try:
                self.roomflow.finalize()  # S36: rules-only counterfactuals for today's room sessions
            except Exception:
                log.exception("room finalize failed")

    def _day_end_shadow(self) -> None:
        shadow.on_day_end(self)  # S14: counterfactual P&L of today's shadow signals (exceptions contained)

    # ================= moved from engine/core.py, unchanged =================
    def _tick(self) -> dict:  # #551: `_tick`, not `tick`: Engine.tick runs it under the journal-damage check
        now = self.now()
        clk = self._broker_clock()  # M9 1.10: the broker's clock, compared with the host's
        if not clk["is_open"]:
            return self._tick_closed(clk, now)
        if self.run is None and not self.pos and not self._md_exits:  # S80: a multi-day sale still to book
            return self._daily_status({"state": "idle", "note": "no paper run: start one from the dashboard",
                                       "account_clean": self.account_clean})
        if self.run is not None and (self.day is None or self.day.day != now.astimezone(_et()).date()):
            self._start_day(now)
        d = self.day
        if d is None:
            # M9 1.9 (audit M8): no run and no session (the run ended, then a restart) with a multi-day flatten sale
            # still to book. `_sync_orders` is never reached from here, so the sale is booked here: else it stays in
            # `_md_exits` for good and a `new_run` command waits for it for ever (`apply_commands`).
            if self._md_exits:
                self._sync_md_exits()
            return self._daily_status({"state": "idle", "account_clean": self.account_clean})
        m = self.market.last_complete_minute(now)
        # M9 1.5 (audit M3): the multi-day sync and send run first, before any market-data read or intraday step.
        # They were the tenth step; a step before them that raised on each tick of 15:50-15:57 ET lost the
        # decision's orders. The send then reads the engine's state as the last completed minute left it.
        # QA-Ops (#484): only when that minute is the one just before (`done_m == m - 1`). On the first tick after a
        # (re)start, or after a minute whose checks raised, the state is older than a minute or was never checked
        # (a daily stop, a book end or a drift first seen now): there the send keeps its old place behind the
        # checks, exactly as before M9 1.5 (fail closed).
        first = m >= 0 and d.done_m == m - 1
        if first:
            self._md_send_steps(d, sync=True)
        fresh = self._market_update(now)
        if m < 0 or m == d.done_m:
            return self._data_status(self._status(m), fresh)  # this minute's work is complete: never run twice
        # S74 (#114, audit D6): `last_m` says the minute was started, `done_m` that it was finished. A step below
        # that raises leaves `done_m` behind, so the main loop's retry (about 10 s later) runs the minute again
        # instead of getting "already done" and resetting the error streak. Every failed attempt therefore counts
        # (on_tick_error) and a failure that recurs trips the kill switch. Each step is safe to run again, except
        # an entry decision that has acted: `decided` (set below and in _entries) keeps a retry from repeating it.
        d.last_m = m
        self._sync_orders()
        if not first:
            self._daily_step("sync", lambda: self.daily.sync())  # #206: the daily path's orders and fills
        self._md_positions_after_fills(d.day)  # #333: near-close fills land before the fail-safes' flatten window
        self._recheck_reconcile(m)
        self._late_actions(m)  # #266: a split or spin-off Alpaca credits after the session start
        self._check_drift()
        if any(p.carried for p in self.pos.values()):
            for p in [p for p in self.pos.values() if p.carried and p.status in ("open", "pending")]:
                self._exit(p, "carried_over")
        eq, unreal = self.equity_now()
        self._risk_halts(m, eq)
        intraday = self.intraday_live()  # M9 1.5: no intraday rule and no intraday position: none of its steps
        if intraday:
            self._manage_stops(m)
        if not first:
            self._md_send_steps(d)
        if m >= d.flatten_m and not d.flattened:
            self.flatten_all("time")  # S78: intraday sleeves only (holding policy per rule); see flatten_all
            d.flattened = True
        if self.roomflow is not None and self.roomflow.jobs:
            self._quotes = self.broker_quotes(sorted(self.roomflow.jobs))
            self._bp_read, self._bp_added = self._broker_buying_power(), 0.0
            self.roomflow.collect(m)
        # S76: every live strategy the session runs, in registry order. M9 1.5: not on a minute whose bars could not
        # be read (a rule would decide on stale bars: fail closed, for that minute only)
        for strat in self.live_strategies() if intraday and fresh else ():
            if due(strat.schedule, Event.BAR_CLOSE, m, d.n_min) and d.decided.get(strat.name) != m \
                    and self._may_enter(m):
                self._entries(strat, m)
                d.decided[strat.name] = m
        if intraday:
            self._close_outs(m)
        self._ledger(eq)
        shadow.on_minute(self, m)  # S14: shadow ORB / news continuation (signal rows only; exceptions contained)
        d.done_m = m
        return self._data_status(self._status(m), fresh)

    def intraday_live(self) -> bool:
        """M9 1.5: whether the session has anything intraday to manage: a live rule that is flat by the close, an
        intraday position, or a room job. False when only multi-day rules are on (their path is `self.daily`)."""
        if len(self.pos) or (self.roomflow is not None and self.roomflow.jobs):
            return True
        return any(flatten_at_close(s.holding) for s in self.live_strategies())

    def _restore_positions(self) -> None:
        self.pos = Positions()
        if not self.run:
            return
        for t in self.store.open_trades(self.run["id"]):
            f = t.get("features") or {}
            known = {k: f[k] for k in Pos.__dataclass_fields__ if k in f}
            p = Pos(trade_id=t["id"], symbol=t["symbol"], qty=int(float(t["qty"])), **known)
            self.pos.add(p)

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
        if veto is None:
            veto = self.data_block(s)  # M12 3.11 (#473): None unless KAIBOT_DATA_CHECKS_BLOCK is on
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
                order = self._order("entry",
                                    lambda: order_path.submit_oto_stop(self.broker, coid, s, qty, round(stop, 2),
                                                                             gate=self.trading_state),
                                    coid=coid, strategy=strategy, symbol=s, qty=qty, stop=round(stop, 2),
                                    signal_id=signal_id)
            else:
                order = self._order("entry", lambda: order_path.submit_bracket(self.broker, coid, s, qty,
                                                                                round(stop, 2), round(target, 2),
                                                                                gate=self.trading_state),
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
                        "replace_limit", lambda oid=oid, px=px: order_path.replace_limit(self.broker, oid, px,
                                                                                       gate=self.trading_state),
                        order_id=oid, symbol=p.symbol, coid=p.coid, price=px)["id"]
            elif leg.get("type") in ("stop", "stop_limit"):
                p.stop_order_id = leg["id"]
                if abs(float(leg["stop_price"]) - p.stop) >= 0.01:
                    oid, px = leg["id"], round(p.stop, 2)
                    p.stop_order_id = self._order(
                        "replace_stop", lambda oid=oid, px=px: order_path.replace_stop(self.broker, oid, px,
                                                                                     gate=self.trading_state),
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

    def _on_exit_fill(self, p: Pos, px: float, reason: str, ts, order_id: str | None = None, since=None) -> None:
        # #238: the ledger and the venue P&L book the fee the venue charged on this order; the shadow P&L keeps the
        # shadow cost model's own estimate (it prices what the trade would cost, not what it cost)
        fees = self._venue_fees(order_id, ts, since)
        fees_shadow = sell_fees(p.qty, px)
        shadow_exit = px if reason == "target" else sell_px(px, p.qty, p.sigma, self.market.bar_volume(p.symbol))
        pnl_v = (px - p.fill) * p.qty - fees
        pnl_s = (shadow_exit - (p.shadow_entry or p.fill)) * p.qty - fees_shadow
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
        self._remember_ids(p.order_id, p.stop_order_id, p.target_order_id, p.exit_order_id)  # M9 1.13
        self.pos.pop(p)

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
                    p.stop_order_id = self._order("replace_stop", lambda oid=oid, px=px: order_path.replace_stop(
                        self.broker, oid, px, gate=self.trading_state), order_id=oid, symbol=p.symbol,
                        strategy=p.strategy, stop=round(act.price, 2))["id"]
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
                self._order("cancel", lambda oid=oid: order_path.cancel_order(self.broker, oid), order_id=oid,
                            symbol=p.symbol, strategy=p.strategy)
                p.stop_order_id = None
            try:
                o = self._order("moc_sell",
                                lambda p=p: order_path.submit_moc_sell(self.broker, f"{p.coid}-moc", p.symbol, p.qty,
                                                                           gate=self.trading_state),
                                coid=f"{p.coid}-moc", symbol=p.symbol, strategy=p.strategy, qty=p.qty)
                p.exit_order_id, p.exit_reason = o["id"], act.reason
            except Exception as e:
                log.warning("MOC refused for %s (%s); the 15:55 flatten will close it", p.symbol, e)
                self.store.event("moc_refused", {"symbol": p.symbol, "error": str(e)[:200]}, self._rid())
                p.exit_reason = None

    def _exit(self, p: Pos, reason: str) -> None:
        if p.status == "pending":
            self._order("cancel", lambda: order_path.cancel_order(self.broker, p.order_id), order_id=p.order_id,
                        symbol=p.symbol, strategy=p.strategy)
            self._close_trade(p, None, "not_filled")
            return
        if p.exit_order_id or p.status != "open":
            return
        if any(q is not p and q.symbol == p.symbol and q.status in ("open", "pending") for q in self.pos.values()):
            o = self._sell_sleeve(p, reason)  # S77: another rule holds the stock too: sell this rule's shares only
        else:
            o = self._order("close_position",
                            lambda: order_path.close_position(self.broker, p.symbol, gate=self.trading_state),
                            symbol=p.symbol, strategy=p.strategy, qty=p.qty, reason=reason)
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
                self._order("cancel", lambda oid=oid: order_path.cancel_order(self.broker, oid), order_id=oid,
                            symbol=p.symbol, strategy=p.strategy)
        p.stop_order_id = p.target_order_id = None
        coid = f"{p.coid}-x"
        return self._order("sell", lambda: order_path.submit(self.broker, OrderRequest(p.symbol, "sell", int(p.qty),
                                                                                       "market", "day", coid),
                                                             gate=self.trading_state),
                           coid=coid, symbol=p.symbol, strategy=p.strategy, qty=p.qty, reason=reason)
