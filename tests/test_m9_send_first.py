"""M9 ticket 1.5 (audit finding M3): the multi-day send must not sit behind the intraday steps of `Engine.tick`.

Before the fix the send was the tenth step of the open-market branch; the market-data read (`market.update`, IEX
minute bars) came first and was not wrapped, so a read that failed on each of the seven ticks of 15:50-15:57 ET
aborted every tick before the send and the decision's orders never went out. Simulated venue of the S80 drills
(`SimBroker` through `FakeBroker`, `SimClock` through `Clock`): DAY1, DAY2, a rule that wants B.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from test_failsafe_drills import DAY1, DAY2, MD
from test_live_daily_engine import SEND, DailyAccount

from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S, AlpacaError
from kaibot.engine import __main__ as entry
from kaibot.engine.alpaca import AlpacaData
from kaibot.engine.market import LiveMarket

WINDOW = range(SEND, SEND + 7)  # the ticks whose clock reads 15:50:07 ... 15:56:07 ET


def _data_down(a) -> list:
    """Every market-data read of the minute update fails, as the unbounded client does when it gives up."""
    calls = []

    def down(now):
        calls.append(now)
        raise AlpacaError(0, "giving up on GET https://data.alpaca.markets/v2/stocks/bars")

    a.eng.market.update = down
    return calls


def test_the_orders_go_out_when_the_market_data_read_fails_on_every_tick_of_the_window(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, SEND)
    assert a.md_orders() == []  # nothing before 15:50
    update = a.eng.market.update
    calls = _data_down(a)
    errors = a.ticks(a.open2, WINDOW.start, WINDOW.stop, catch=True)  # the main loop: count an error and carry on
    assert len(calls) == 7  # the read was tried, and failed, on each of the seven ticks
    (o,) = a.md_orders()  # the decision's order went out all the same, once
    assert o["symbol"] == "B" and o["side"] == "buy"
    assert o["client_order_id"].startswith(f"kb{a.rid}-{MD}-B-20260804-buy-")
    sent_at = pd.Timestamp(o["submitted_at"]).tz_convert("America/New_York")
    assert (sent_at.hour, sent_at.minute) == (15, 50)  # on the first tick of the window, not a later one
    assert errors == [] and a.eng.kill is None  # a failed data read is no loop error: the kill switch stays off
    ev = [e for e in a.store.tables["event"] if e["type"] == "engine_error"]
    assert len(ev) == 1 and ev[0]["payload"]["step"] == "market_update"  # said once, not once a minute
    a.eng.market.update = update  # the data comes back
    a.ticks(a.open2, WINDOW.stop, 390)
    a.after_close(a.open2)
    assert len(a.md_orders()) == 1 and a.md_orders()[0]["status"] == "filled"
    fills = [x for *_, x in a.eng.journal.entries(kinds=("fill",)) if x.get("symbol") == "B"]
    assert len(fills) == 1 and a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == float(o["qty"])
    assert a.eng.recon.check("test").diffs == []
    kinds = [e["type"] for e in a.store.tables["event"]]
    assert "multi_day_error" not in kinds


def test_an_intraday_step_that_raises_no_longer_holds_back_the_send(tmp_path, monkeypatch):
    """The other unwrapped steps before the old send (order sync, drift read, risk halts): one that raises aborts
    the tick as before (the loop counts it), but only after the multi-day sync and send have run."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, SEND)

    def boom():
        raise RuntimeError("positions unreadable")

    a.eng._check_drift = boom
    errors = a.ticks(a.open2, SEND, SEND + 1, catch=True)
    assert len(errors) == 1 and len(a.md_orders()) == 1


def test_a_failed_data_read_scans_no_intraday_entry_that_minute(tmp_path, monkeypatch):
    """Fail closed for the intraday rules: a minute whose bars could not be read offers no candidate to a rule (the
    bars it would decide on are stale). The time flatten and the stops' management still run."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    scanned = []
    a.eng._entries = lambda strat, m: scanned.append(m)
    a.ticks(a.open1, 30, 40)
    assert scanned  # the scan runs while the data is read
    n = len(scanned)
    _data_down(a)
    assert a.ticks(a.open1, 40, 50, catch=True) == []
    assert len(scanned) == n and a.eng.day.done_m == 49  # no scan; every minute still completes


def test_no_intraday_step_runs_when_no_intraday_rule_is_live(tmp_path, monkeypatch):
    """With only multi-day rules on (the owner removed the intraday rule) and no intraday position, the stop
    management, the entry scan and the close-outs are not called at all."""
    a = DailyAccount(tmp_path, monkeypatch)
    a.eng.strategies = {a.eng.level(): []}  # the level lists no intraday rule (the multi-day rule is `a.rule`)
    a.ticks(a.open1, 0, 5)
    assert not a.eng.intraday_live()
    called = []
    for name in ("_manage_stops", "_entries", "_close_outs"):
        setattr(a.eng, name, lambda *args, name=name: called.append(name))
    _data_down(a)
    assert a.ticks(a.open1, 5, 30, catch=True) == [] and called == []
    a.eng.strategies = {a.eng.level(): ["dip_reversal"]}
    a.eng.day.rules = None
    assert a.eng.intraday_live()


def test_the_minute_update_reads_through_the_bounded_client(tmp_path):
    """The per-minute read is on the order path's clock: 6 s an attempt and 20 s in all, like an order call. The
    history load keeps the long client (it runs before the open)."""
    data = AlpacaData("k", "s")
    assert data.http.deadline_s is None
    quick = entry.bounded_data(data)
    assert (quick.http.timeout_s, quick.http.deadline_s) == (ORDER_TIMEOUT_S, ORDER_DEADLINE_S)
    assert quick.http.budget is data.http.budget and data.http.deadline_s is None  # same rate budget; a copy

    class Bars:
        def __init__(self):
            self.calls = []

        def bars(self, symbols, start, end, feed, **kw):
            self.calls.append(feed)
            return {}

    slow, fast = Bars(), Bars()
    mk = LiveMarket(slow, ["A"], {"A": "XLK"}, "SPY", tmp_path, lambda: pd.Series(dtype=float), minute_data=fast)
    mk.open_utc = pd.Timestamp(f"{DAY2} 13:30", tz="UTC").to_pydatetime()
    mk.update(mk.open_utc + timedelta(minutes=5))
    assert fast.calls == ["iex"] and slow.calls == []
    mk = LiveMarket(slow, ["A"], {"A": "XLK"}, "SPY", tmp_path, lambda: pd.Series(dtype=float))
    mk.open_utc = pd.Timestamp(f"{DAY1} 13:30", tz="UTC").to_pydatetime()
    mk.update(mk.open_utc + timedelta(minutes=5))
    assert slow.calls == ["iex"]  # without one: the client it always used


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
