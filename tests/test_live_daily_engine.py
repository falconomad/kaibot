"""Issue #206: the live daily path inside the engine (engine/core.py + engine/live_daily.py), on the simulated venue
of the S80 drills (test_failsafe_drills.Account: DAY1, DAY2).

A multi-day rule that wants B decides after DAY1's close; on DAY2 the engine sends a `cls` buy at 09:31 through the
order manager; the close-minus-5 flatten (intraday sleeves only) leaves it alone; it fills at the closing print and
the fill is booked once to the engine's ledger as a multi-day sleeve; the account guard, the drift check and the
reconciliation all know it. A restart between the order and the fill books it exactly once. With KAIBOT_MULTIDAY off
the engine has no daily path and refuses one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
import pytest
from test_failsafe_drills import DAY1, DAY2, MD, Account, frames

from kaibot.engine import live_daily as ld
from kaibot.engine.core import Engine
from kaibot.engine.daily import DailyData
from kaibot.engine.journal import Journal
from kaibot.risk.profiles import load_profiles, rule_book_limits
from kaibot.strategies import momentum as mom
from kaibot.strategies import registry
from kaibot.strategies.base import TargetList

POL = load_profiles()["balanced"]


@dataclass(frozen=True)
class WantsB(mom.TrendMomentum):
    """trend_momentum's name, holding policy and sizing settings; its decision: hold B."""

    def decision_day(self, prev, fill) -> bool:
        return True

    def on_event(self, ctx):
        if "B" in ctx.held:
            return None
        return TargetList(self.name, ctx.fill_session, {"B": 1.0}, features={"keep": [], "buy": ["B"], "sell": {}},
                          resize=False)


def daily_view(px_b: float) -> DailyData:
    """300 sessions to DAY1 of B (rising to px_b) and SPY: enough history for ATR and dollar volume."""
    n = 300
    dates = np.busday_offset(np.datetime64(DAY1), -np.arange(n)[::-1], roll="backward").astype("datetime64[D]")
    c = np.column_stack([np.linspace(px_b * 0.8, px_b, n) * (1 + 0.004 * np.sin(np.arange(n))),
                         np.linspace(380, 420, n)])
    c[-1, 0] = px_b
    h, lo, v = c * 1.01, c * 0.99, np.full(c.shape, 2e6)
    return DailyData(dates=dates, symbols=["B", "SPY"], sig_c=c.copy(), sig_h=h, sig_l=lo, raw_o=c.copy(),
                     raw_c=c.copy(), raw_h=h, raw_l=lo, v_first=v * 0.02, v_last=v * 0.02, raw_v=v,
                     stocks=np.array([0]), spy=1, sig_unfilled_c=c.copy())


class DailyAccount(Account):
    def __init__(self, tmp_path, monkeypatch, gap_b=1.0, book_end=None, view=True):
        monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
        self.has_view = view
        f = frames()["B"]
        self.px_b = float(f[f["ts"] < f["ts"].iloc[0] + timedelta(hours=6, minutes=30)]["c"].iloc[-1])
        self.view = daily_view(self.px_b)
        s = WantsB()
        lim = ld.live_limits(s, POL, rule_book_limits(MD, "balanced", research=True))
        if book_end is not None:
            lim = replace(lim, run_drawdown_end_pct=book_end)
        self.rule = ld.Rule(s, ld.live_builder(s, lim))
        super().__init__(tmp_path, gap_b=gap_b)
        self.broker.wash_check = True  # Alpaca's wash-trade rule (QA-Ops review of #207)
        self.broker.shorting_enabled = False  # as Alpaca reports it once shorting is off (paper_start step 3)

    def load(self, k: date, now):
        if str(k) != DAY1 or not self.has_view:
            raise ld.DataNotReady(f"the daily store has no SPY bar for {k} (run ./scripts/fetch_daily.sh)")
        return self.view

    def engine(self, journal=None) -> Engine:
        eng = super().engine(journal)
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule])
        return eng

    def cls_orders(self):
        return [o for o in self.broker.orders.values() if o["time_in_force"] == "cls"]


def test_the_engine_sends_the_daily_decision_at_the_next_session_and_books_its_fill_once(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    dec = [b for *_, b in a.eng.journal.entries(kinds=("md_decision",))]
    assert [(b["session"], b["fill_session"], b["target"]["weights"]) for b in dec] == [(DAY1, DAY2, [["B", 1.0]])]
    assert a.cls_orders() == []  # nothing is sent on the evening of the decision
    a.ticks(a.open2, 0, 2)  # 09:31: the send step
    (o,) = a.cls_orders()
    assert o["symbol"] == "B" and o["side"] == "buy" and float(o["qty"]).is_integer() and float(o["qty"]) >= 1
    assert o["client_order_id"].startswith(f"kb{a.rid}-{MD}-B-20260804-buy-")
    qty = float(o["qty"])
    a.ticks(a.open2, 2, 390)  # the 15:55 flatten is intraday only: the order rests, nothing is sold
    assert a.eng.day.flattened and a.cls_orders()[0]["status"] in ("new", "accepted", "filled")
    assert not [x for x in a.broker.orders.values() if x["symbol"] == "B" and x["side"] == "sell"]
    assert a.eng.account_clean and a.eng.kill is None  # the guard and the drift check know the order
    a.after_close(a.open2)
    b = a.eng.ledger.book(a.rid)
    assert b.sleeves[(MD, "B")].qty == qty and float(a.broker.pos["B"]["qty"]) == qty
    fills = [x for *_, x in a.eng.journal.entries(kinds=("fill",)) if x.get("symbol") == "B"]
    assert len(fills) == 1 and fills[0]["strategy"] == MD and fills[0]["run_id"] == a.rid
    assert a.eng.recon.check("test").diffs == []  # the ledger matches the venue
    for _ in range(3):  # later ticks book nothing again and send nothing
        a.eng.tick()
    assert len([x for *_, x in a.eng.journal.entries(kinds=("fill",)) if x.get("symbol") == "B"]) == 1
    assert len(a.cls_orders()) == 1
    kinds = [e["type"] for e in a.store.tables["event"]]
    assert "multi_day_decision" in kinds and "multi_day_orders" in kinds and "multi_day_fill" in kinds
    assert "multi_day_waiting" in kinds  # DAY2's evening view is not there: no decision (fail closed)
    assert "multi_day_error" not in kinds


def test_a_restart_between_the_order_and_the_fill_books_the_fill_once(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 120)
    (o,) = a.cls_orders()
    eng = a.restart()  # killed mid-session, the closing-auction order resting at the venue
    assert eng.daily is not None and "B" in eng.daily.working_symbols()
    assert eng.account_clean  # the resting order is the engine's own, not "unknown"
    a.ticks(a.open2, 120, 390)
    assert len(a.cls_orders()) == 1  # not sent again
    a.after_close(a.open2)
    fills = [x for *_, x in eng.journal.entries(kinds=("fill",)) if x.get("symbol") == "B"]
    assert len(fills) == 1 and eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == float(o["qty"])


def test_without_the_flag_there_is_no_daily_path(monkeypatch, tmp_path):
    from kaibot.engine import __main__ as entry

    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    assert entry.daily_path() is None
    eng = Engine(None, None, None, {}, set(), journal=Journal())
    assert eng.daily is None
    with pytest.raises(registry.RefusedStrategy, match="KAIBOT_MULTIDAY=on"):
        Engine(None, None, None, {}, set(), journal=Journal(), daily=lambda e: object())
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    assert callable(entry.daily_path())  # trend_momentum is enabled in config/profiles.yaml (#205)


# ---------------------------------------------------------------- the book's own run end (stakeholder, 13 Nov 2026)


def test_the_book_end_closes_the_multi_day_book_and_leaves_the_intraday_run_alone(tmp_path, monkeypatch):
    """B (held by the multi-day book since DAY1) opens DAY2 30% lower. The run's equity falls far below the intraday
    run end (Balanced 8%), but that end is measured without the book's P&L, so the run goes on. The book's own end
    (5% here) is reached: journalled, a halt, the book sold at market, no new multi-day trade."""
    a = DailyAccount(tmp_path, monkeypatch, gap_b=0.7, book_end=5.0)
    a.ticks(a.open1, 0, 30)
    px = float(a.eng.market.last_close("B"))  # the book holds 40 B from DAY1 (no stop: the prereg has none)
    a.broker.pos["B"] = {"qty": 40, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": 40, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-buy-1", "session": DAY1})
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    peak = a.eng._book_peak(a.rid)
    assert peak is not None
    a.ticks(a.open2, 0, 4)
    ends = [b for *_, b in a.eng.journal.entries(kinds=("md_book_end",))]
    assert len(ends) == 1 and ends[0]["drawdown_pct"] >= 5 and ends[0]["end_pct"] == 5.0
    assert any(h["kind"] == "book_drawdown" for h in a.store.tables["halt"])
    assert not any(h["kind"] == "run_drawdown" for h in a.store.tables["halt"])  # the intraday end did not trip
    assert a.eng.run is not None and a.eng.run["status"] == "running"
    assert "B" not in a.broker.pos  # the book is sold
    assert a.eng.daily.account.book_ended() and a.eng.kill is None  # no drift: the sale is booked
    b = a.eng.ledger.book(a.rid)
    assert (MD, "B") not in b.sleeves and b.realized_by[MD] < 0
    assert a.eng._status(4)["multi_day"]["book"] == "ended"
    a.ticks(a.open2, 4, 390)
    a.after_close(a.open2)
    assert a.cls_orders() == []  # the decision of DAY1 is not sent: the book is closed for this run
    assert len([b for *_, b in a.eng.journal.entries(kinds=("md_book_end",))]) == 1  # once
    eng = a.restart()
    assert eng.daily.account.book_ended() and eng._book_peak(a.rid) == peak  # both survive a restart


def test_without_the_daily_path_the_run_end_is_measured_as_before(tmp_path):
    """Flag off: the same gap ends the whole run at the intraday end, multi-day sleeve included (S80)."""
    a = Account(tmp_path, gap_b=0.7)
    a.day1()
    a.ticks(a.open2, 0, 4)
    assert any(h["kind"] == "run_drawdown" for h in a.store.tables["halt"])
    assert a.eng.daily is None and not list(a.eng.journal.entries(kinds=("md_book_end", "md_book_peak")))


def test_the_heartbeat_says_plainly_when_the_daily_store_was_not_refreshed(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch, view=False)
    a.ticks(a.open1, 0, 390)
    st = a.after_close(a.open1) or a.eng.tick()
    assert "daily store" in st["multi_day"]["rules"][MD]["waiting"]
    a.ticks(a.open2, 0, 2)
    st = a.eng._status(1)
    assert st["multi_day"]["note"].startswith(f"{MD}: no decision today: daily store not refreshed")
    kinds = [e["type"] for e in a.store.tables["event"]]
    assert "multi_day_no_decision" in kinds and a.cls_orders() == []


# ---------------------------------------------------------------- QA-Ops review of #207: venue conflicts


@dataclass(frozen=True)
class SellsB(mom.TrendMomentum):
    def decision_day(self, prev, fill) -> bool:
        return True

    def on_event(self, ctx):
        return TargetList(self.name, ctx.fill_session, {}, features={"keep": [], "buy": [], "sell": {"B": "exit"}},
                          resize=False)


def _book_b(a, qty=40):
    """The multi-day book holds `qty` B from DAY1 (booked as the daily path books a fill; no stop: the prereg has
    none)."""
    a.ticks(a.open1, 0, 30)
    px = float(a.eng.market.last_close("B"))
    a.broker.pos["B"] = {"qty": qty, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": qty, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-buy-1", "session": DAY1})
    return px


def test_a_watchdog_stop_placed_while_the_engine_hung_is_adopted_and_cancelled_before_the_sale(tmp_path,
                                                                                             monkeypatch):
    """The engine hung (no restart) and the watchdog put an emergency stop on B. The rule's sale must not be refused
    session after session: the stop is adopted, cancelled (the rule has no stop), and then the sale goes out once."""
    from kaibot.broker.protocol import OrderRequest

    a = DailyAccount(tmp_path, monkeypatch)
    a.rule = type(a.rule)(SellsB(), a.rule.builder)
    a.eng.daily._rules = lambda r=[a.rule]: r
    px = _book_b(a)
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    wd = a.broker.submit(OrderRequest("B", "sell", 40, "stop", "gtc", "kbwd-trend_momentum-B-20260803210000-wdstop",
                                      stop_price=round(px * 0.9, 2)))
    a.ticks(a.open2, 0, 3)
    assert a.broker.orders[wd["id"]]["status"] == "canceled"
    assert any(b["coid"] == wd["client_order_id"] for *_, b in a.eng.journal.entries(kinds=("stop_adopted",)))
    (sale,) = [o for o in a.cls_orders() if o["side"] == "sell"]
    assert float(sale["qty"]) == 40
    a.ticks(a.open2, 3, 390)
    a.after_close(a.open2)
    assert "B" not in a.broker.pos and (MD, "B") not in a.eng.ledger.book(a.rid).sleeves
    assert len([o for o in a.cls_orders() if o["side"] == "sell"]) == 1 and a.eng.kill is None


def test_the_intraday_rule_leaves_a_multi_day_stock_alone(tmp_path, monkeypatch):
    """Alpaca refuses an order against an open opposite-side market or stop order in the symbol (taught to SimBroker),
    and one position holds both books' shares: an intraday entry in B while the book holds it is vetoed, so the
    15:55 flatten can never be refused; A trades as before."""
    from test_engine import fake_candidate

    a = DailyAccount(tmp_path, monkeypatch)
    _book_b(a)
    fake_candidate(a.eng, "B", at=(54,))
    a.ticks(a.open1, 30, 70)
    sig = [s for s in a.store.tables.get("signal", []) if s.get("symbol") == "B" and s.get("mode") == "live"]
    assert sig and all(s.get("rules_veto") == "multi_day_symbol" for s in sig)
    assert not [o for o in a.broker.orders.values() if o["symbol"] == "B" and o.get("order_class") == "bracket"]


def test_simbroker_refuses_a_wash_trade_as_alpaca_does(tmp_path, monkeypatch):
    import pytest as _pt

    from kaibot.broker.protocol import BrokerError, OrderRequest

    a = DailyAccount(tmp_path, monkeypatch)
    _book_b(a)
    a.broker.submit(OrderRequest("B", "sell", 10, "stop", "gtc", "x1", stop_price=1.0))
    with _pt.raises(BrokerError, match="wash trade"):
        a.broker.submit(OrderRequest("B", "buy", 5, "market", "cls", "x2"))
    a.broker.submit(OrderRequest("B", "sell", 5, "market", "cls", "x3"))  # the same side is fine


def test_a_daily_order_waits_while_an_opposite_order_is_open():
    """The daily path's own check, before it sends: an open opposite-side market or stop order (or armed leg) in the
    symbol that is not its own working order refuses the target with a reason; the target loop retries it."""
    orders = [{"id": "s1", "symbol": "B", "side": "sell", "type": "stop", "status": "new", "legs": []},
              {"id": "e1", "symbol": "C", "side": "buy", "type": "market", "status": "filled",
               "legs": [{"id": "e1s", "symbol": "C", "side": "sell", "type": "stop", "status": "new"},
                        {"id": "e1t", "symbol": "C", "side": "sell", "type": "limit", "status": "new"}]},
              {"id": "mine", "symbol": "D", "side": "sell", "type": "market", "status": "new", "legs": []},
              {"id": "lim", "symbol": "E", "side": "sell", "type": "limit", "status": "new", "legs": []}]
    opp = ld.opposite_orders(orders, {"mine"})
    assert opp == {"B": {"sell"}, "C": {"sell"}, "E": {"sell"}}  # #211: an opposite limit blocks a market order

    class Acct:
        def entry_block(self):
            return None

        def allows(self, *a):
            return None

    g = ld._Gate(Acct())
    g.opposite = opp
    assert "wash trade" in g.allows("B", "buy") and g.allows("B", "sell") is None
    assert "wash trade" in g.allows("C", "buy") and g.allows("D", "buy") is None
    assert "wash trade" in g.allows("E", "buy")


def test_multi_day_buys_wait_while_shorting_is_on(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.broker.shorting_enabled = True
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 5)
    assert a.cls_orders() == []
    assert "multi_day_shorting_on" in [e["type"] for e in a.store.tables["event"]]
    a.broker.shorting_enabled = False
    a.eng.daily._shorting_day = None  # (read again at the next session; here: at once)
    a.ticks(a.open2, 5, 7)
    assert [o["side"] for o in a.cls_orders()] == ["buy"]


def test_on_an_early_close_the_send_window_ends_15_minutes_before_the_close():
    from datetime import date, time

    lv = ld.LiveDaily(None, Journal(), lambda: None, [], None, None, next_session=lambda d: d,
                      prev_session=lambda d: d, session_close=lambda d: time(13, 0) if d.day == 27 else time(16, 0))
    assert lv.send_until(date(2026, 11, 27)) == time(12, 45)  # the day after Thanksgiving
    assert lv.send_until(date(2026, 11, 25)) == time(15, 45)  # a normal day: 15:45 (cls refused from 15:50)
    assert ld.calendar_close({"close": "13:00"}) == time(13, 0) and ld.calendar_close(None) is None


# ---------------------------------------------------------------- Product call on #207: retired across runs


def _ended_book(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch, gap_b=0.7, book_end=5.0)
    _book_b(a)
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 4)
    assert a.eng.daily.account.book_ended()
    return a


def test_a_rule_whose_book_ended_stays_retired_across_restarts_new_journals_and_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIBOT_REENABLE", raising=False)
    a = _ended_book(tmp_path, monkeypatch)
    (r,) = [b for *_, b in a.eng.journal.entries(kinds=("md_retired",))]
    assert r["rule"] == MD and r["session"] == DAY2 and r["drawdown_pct"] >= 5
    (h,) = [x for x in a.store.tables["halt"] if x["kind"] == "multi_day_retired"]
    assert f"rule={MD};" in h["reason"]
    note = a.eng._status(4)["multi_day"]["note"]
    assert note.startswith(f"{MD} retired: book fell ") and note.endswith(f"% from its peak on {DAY2}")
    assert a.eng.daily.active() == []
    eng = a.restart()  # a restart
    assert eng.daily.retirement(MD) is not None and eng.daily.active() == []
    fresh = a.engine(journal=Journal(tmp_path / "another.sqlite"))  # a new journal: the store's row keeps it
    fresh.startup()
    assert fresh.daily.retirement(MD)["source"] == "store" and fresh.daily.active() == []
    fresh.journal.close()
    # a new run: the rule is still retired (a new run does not restart it)
    a.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": 10_000, "level": "balanced"},
                               "idempotency_key": "k-new", "applied_at": None})
    a.ticks(a.open2, 4, 20, catch=True)
    a.eng.apply_commands()
    assert a.eng.daily.active() == [] and a.eng.daily.after_close(date.fromisoformat(DAY2)) == []


def test_only_an_explicit_dated_reenable_brings_the_rule_back_once(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIBOT_REENABLE", raising=False)
    a = _ended_book(tmp_path, monkeypatch)
    monkeypatch.setenv("KAIBOT_REENABLE", MD)  # no date: refused
    eng = a.restart()
    assert eng.daily.retirement(MD) is not None
    monkeypatch.setenv("KAIBOT_REENABLE", f"{MD}:2026-01-01")  # the wrong date: refused
    eng = a.restart()
    assert eng.daily.retirement(MD) is not None
    refused = [e["payload"] for e in a.store.tables["event"] if e["type"] == "multi_day_reenable_refused"]
    ident = eng.daily.retirement(MD)["retirement_id"]
    assert ident.startswith(f"{DAY2}.") and len(refused) == 2 and f"{MD}:{ident}" in refused[-1]["why"]
    assert eng.daily.status()["rules"][MD]["reenable"] == f"KAIBOT_REENABLE={MD}:{ident}"
    monkeypatch.setenv("KAIBOT_REENABLE", f"{MD}:{DAY2}")  # the date alone no longer names the retirement (#211)
    eng = a.restart()
    assert eng.daily.retirement(MD) is not None
    monkeypatch.setenv("KAIBOT_REENABLE", f"{MD}:{ident}")
    eng = a.restart()
    assert eng.daily.retirement(MD) is None and [r.name for r in eng.daily.active()] == [MD]
    (re,) = [b for *_, b in eng.journal.entries(kinds=("md_reenabled",))]
    assert re["rule"] == MD and re["retired_session"] == DAY2 and re["by"] == "KAIBOT_REENABLE"
    assert re["retirement_id"] == ident
    assert [x for x in a.store.tables["halt"] if x["kind"] == "multi_day_reenabled"]
    assert "multi_day_reenabled" in [e["type"] for e in a.store.tables["event"]]
    eng = a.restart()  # the flag still in the .env: nothing more is written
    assert len([b for *_, b in eng.journal.entries(kinds=("md_reenabled",))]) == 1
    fresh = a.engine(journal=Journal(tmp_path / "another.sqlite"))  # the store also says re-enabled
    monkeypatch.delenv("KAIBOT_REENABLE")
    fresh.startup()
    assert fresh.daily.retirement(MD) is None


def test_the_send_window_reads_the_calendar_once_per_session_not_per_tick():
    """QA-Ops second key on #207: `in_window` runs at every tick; the session's close is read once (a failed read is
    tried again)."""
    from datetime import date, time

    calls = []

    def close(d):
        calls.append(d)
        if len(calls) == 1:
            raise RuntimeError("calendar unreachable")
        return time(13, 0)

    lv = ld.LiveDaily(None, Journal(), lambda: None, [], None, None, next_session=lambda d: d,
                      prev_session=lambda d: d, session_close=close)
    d = date(2026, 11, 27)
    assert lv.send_until(d) == time(15, 45)  # unreadable: the normal window (Alpaca refuses later orders anyway)
    assert [lv.send_until(d) for _ in range(5)] == [time(12, 45)] * 5
    assert calls == [d, d]


# ---------------------------------------------------------------- #211: follow-ups of the QA-Ops review of #207


def test_a_stale_reenable_line_never_reenables_a_same_date_retirement(tmp_path, monkeypatch):
    """#211 (QA-Ops 3): re-enabled, then retired again on the SAME date (a new run that day): the old line in the .env
    names the first retirement's id only and is refused; the rule stays retired."""
    monkeypatch.delenv("KAIBOT_REENABLE", raising=False)
    a = _ended_book(tmp_path, monkeypatch)
    first = a.eng.daily.retirement(MD)["retirement_id"]
    monkeypatch.setenv("KAIBOT_REENABLE", f"{MD}:{first}")
    eng = a.restart()
    assert eng.daily.retirement(MD) is None
    eng.daily.retire(MD, run_id=a.rid, session=DAY2, drawdown_pct=22.0, end_pct=21.0, peak=10_000.0, equity=7_800.0)
    second = eng.daily.retirement(MD)["retirement_id"]
    assert second != first and second.startswith(f"{DAY2}.")
    eng = a.restart()  # the stale line is still set
    assert eng.daily.retirement(MD)["retirement_id"] == second
    assert len([b for *_, b in eng.journal.entries(kinds=("md_reenabled",))]) == 1
    why = [e["payload"]["why"] for e in a.store.tables["event"] if e["type"] == "multi_day_reenable_refused"][-1]
    assert second in why


def test_one_bad_print_never_retires_the_rule(tmp_path, monkeypatch):
    """#211 (QA-Ops 1): a breach seen on one check only (a bad print) is journalled and dropped; the book ends only
    when the breach holds on two checks at least a minute apart."""
    a = DailyAccount(tmp_path, monkeypatch, book_end=5.0)
    _book_b(a)
    real = a.eng._md_price
    bad = {"on": False}
    a.eng._md_price = lambda sym: real(sym) * 0.5 if bad["on"] else real(sym)  # a bad print of B
    a.ticks(a.open1, 30, 40)
    bad["on"] = True
    a.ticks(a.open1, 40, 41)  # one tick with a bad price
    bad["on"] = False
    a.ticks(a.open1, 41, 45)
    assert [b for *_, b in a.eng.journal.entries(kinds=("md_book_breach",))]
    assert not a.eng.daily.account.book_ended() and a.eng.daily.retirement(MD) is None
    bad["on"] = True
    a.ticks(a.open1, 45, 46)
    assert not a.eng.daily.account.book_ended()  # first check of a new breach
    a.ticks(a.open1, 46, 47)  # the next minute: confirmed
    assert a.eng.daily.account.book_ended() and a.eng.daily.retirement(MD) is not None


def test_simbroker_models_opposite_limit_orders_as_alpaca_does(tmp_path, monkeypatch):
    """#211 (QA-Ops 2): an open opposite limit order refuses a new market or stop order, and a new limit order whose
    price reaches it; a limit that does not reach it is accepted."""
    import pytest as _pt

    from kaibot.broker.protocol import BrokerError, OrderRequest

    a = DailyAccount(tmp_path, monkeypatch)
    _book_b(a)
    a.broker.submit(OrderRequest("B", "sell", 10, "limit", "day", "l1", limit_price=200.0))
    for req in (OrderRequest("B", "buy", 1, "market", "cls", "m1"),
                OrderRequest("B", "buy", 1, "stop", "day", "s1", stop_price=300.0),
                OrderRequest("B", "buy", 1, "limit", "day", "l2", limit_price=200.0)):
        with _pt.raises(BrokerError, match="wash trade"):
            a.broker.submit(req)
    a.broker.submit(OrderRequest("B", "buy", 1, "limit", "day", "l3", limit_price=150.0))  # below the sell: fine


def test_every_heartbeat_carries_the_experiment_label_with_the_flag_on(tmp_path, monkeypatch):
    """#211 (Product): `detail.experiments` was only in the in-session heartbeat; the dashboard's label and limits
    vanished after the close. With KAIBOT_MULTIDAY=on every heartbeat carries it: closed, idle, preparing, after the
    close; with the flag off none does."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.eng.strategies = {lv: ["dip_reversal", MD] for lv in ("conservative", "balanced", "aggressive")}
    a.eng.ex_dividends = lambda day, symbols: []  # a rule that holds across sessions needs an ex-date source
    a.eng.experiments = []  # as after a start with the market closed
    beats = []
    a.eng.on_progress = beats.append
    a.clock.t = a.open1 - timedelta(minutes=20)  # before the open: closed, preparing the history
    st = a.eng.tick()
    assert st["state"] == "market_closed" and st["experiments"][0]["strategy"] == MD
    assert st["experiments"][0]["label"].startswith("Experiment")
    assert all(b.get("experiments") for b in beats if b.get("state") == "preparing")
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    st = a.eng.tick()  # after the close, with the decision step
    assert st["state"] == "market_closed" and st["experiments"][0]["strategy"] == MD
    assert a.eng._daily_status({"state": "idle"})["experiments"]
    monkeypatch.setenv("KAIBOT_MULTIDAY", "off")
    assert "experiments" not in a.eng._daily_status({"state": "market_closed"})
