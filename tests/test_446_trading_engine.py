"""M10 1.21 (#446) step 3 on the simulated venue of the S80 drills (test_live_daily_engine.DailyAccount): the gate at
the order seam stops a buy that got past the daily path's own check while the engine is reducing only, and the
daily path reports a market filter its latest send found off (heartbeat `entry_block: market_filter_off`)."""

from __future__ import annotations

from test_failsafe_drills import DAY1, DAY2, MD
from test_live_daily_engine import SEND, DailyAccount

from kaibot.engine import live_daily as ld
from kaibot.engine.orders import St


def _decide_b(a) -> None:
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)  # the rule decides to hold B on DAY2


def test_as_before_the_daily_buy_goes_out_while_active(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _decide_b(a)
    a.ticks(a.open2, 0, SEND + 1)
    (o,) = a.md_orders()
    assert o["side"] == "buy" and a.eng.trading_state().state.value == "active"


def test_a_buy_that_slipped_past_the_daily_check_is_refused_at_the_seam(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _decide_b(a)
    a.ticks(a.open2, 0, SEND)
    # plant the miss: the daily path's own entry check says "go" while the engine is halted (reducing only)
    monkeypatch.setattr(ld.EngineAccount, "entry_block", lambda self: None)
    a.eng.kill = "planted"
    a.ticks(a.open2, SEND, SEND + 1)
    assert a.md_orders() == []  # no buy reached the venue
    om = a.eng.daily.om
    buys = [r for r in om.orders.values() if r.side == "buy"]
    assert buys and all(r.state is St.REJECTED and "reducing_only" in (r.reason or "") for r in buys)
    assert a.eng.trading_detail()["entry_block"] == "halted"


def test_the_market_filter_off_in_the_latest_send_is_reported(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)  # the run is running, nothing blocks entries
    d = a.eng.daily
    assert d.market_filter_off() == [] and a.eng.entry_block() is None
    a.eng.journal.append("md_send", {"run_id": a.rid, "strategy": MD, "session": DAY1, "targets": [],
                                     "carry": {"of": DAY1, "carried": [], "skipped": {"B": "market filter off"},
                                               "risk_off": True}})
    assert d.market_filter_off() == [(MD, DAY1)]
    det = a.eng.trading_detail()
    assert det["trading_state"] == "active" and det["entry_block"] == "market_filter_off"
    # a later send that read the filter on (or read nothing about it) ends it
    a.eng.journal.append("md_send", {"run_id": a.rid, "strategy": MD, "session": DAY2, "targets": [],
                                     "carry": {"of": DAY1, "carried": [["B", 3]], "skipped": {}, "risk_off": False}})
    assert d.market_filter_off() == [] and a.eng.trading_detail()["entry_block"] is None
    # a late send's note counts the same way; another run's send never does
    a.eng.journal.append("md_send", {"run_id": a.rid, "strategy": MD, "session": DAY2, "targets": [],
                                     "late": {"of": DAY1, "skipped": {}, "risk_off": True, "due": []}})
    a.eng.journal.append("md_send", {"run_id": a.rid + 1, "strategy": MD, "session": DAY2, "targets": [],
                                     "carry": {"risk_off": False}})
    assert d.market_filter_off() == [(MD, DAY2)]
