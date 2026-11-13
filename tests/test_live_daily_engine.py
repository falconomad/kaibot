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
