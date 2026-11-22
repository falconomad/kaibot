"""M9 ticket 1.3 (audit M1), inside the engine: no tick ran between DAY1's close and DAY2 (the Mac was off overnight
and came up after the open). The first open tick of DAY2 takes DAY1's decision, and the order goes out at 15:50."""

from __future__ import annotations

from test_failsafe_drills import DAY1, DAY2, MD
from test_live_daily_engine import SEND, DailyAccount


def test_an_engine_up_only_after_the_open_decides_at_its_first_tick_and_sends_in_the_window(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 389)  # DAY1 to 15:59, then nothing after its close: no `after_close` tick
    assert not list(a.eng.journal.entries(kinds=("md_decision",)))
    a.restart()
    a.ticks(a.open2, 30, 32)  # the engine's first ticks, 10:01 ET of DAY2
    (dec,) = [b for *_, b in a.eng.journal.entries(kinds=("md_decision",))]
    assert (dec["session"], dec["fill_session"], dec["target"]["weights"]) == (DAY1, DAY2, [["B", 1.0]])
    assert dec["late"] is True and a.md_orders() == []
    a.ticks(a.open2, 32, SEND + 1)
    (o,) = a.md_orders()
    assert o["symbol"] == "B" and o["side"] == "buy"
    a.ticks(a.open2, SEND + 1, 390)
    a.after_close(a.open2)
    assert a.md_orders()[0]["status"] == "filled" and len(a.md_orders()) == 1
    assert len([b for *_, b in a.eng.journal.entries(kinds=("md_decision",)) if b["session"] == DAY1]) == 1
    ev = [(e["type"], (e.get("payload") or {}).get("session")) for e in a.store.tables["event"]]
    assert ("multi_day_no_decision", DAY2) not in ev and "multi_day_error" not in [k for k, _ in ev]
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == float(o["qty"])
