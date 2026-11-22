"""M9 ticket 1.4 (audit M2), inside the engine (QA-Ops second key on #518): the engine is down over the 15:50-15:57 ET
window of a decision's fill session. Through `Engine.tick` the decision goes out in the next session's window, once,
journalled `late_of`, and a restart or a later tick sends nothing again.

The S80 venue has two sessions. So the decision is the one for DAY1 (taken late at DAY1's first tick, ticket 1.3, on
the daily view of the session before DAY1), DAY1's window is missed, and the late send happens on DAY2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
from test_failsafe_drills import DAY1, DAY2, MD
from test_live_daily_engine import POL, SEND, DailyAccount, WantsB

from kaibot.engine import live_daily as ld
from kaibot.risk.profiles import rule_book_limits

DAY0 = str(date.fromisoformat(DAY1) - timedelta(days=1))  # the fake venue's session before DAY1 (its calendar)


@dataclass(frozen=True)
class WantsBOnce(WantsB):
    """Decides (hold B) for DAY1 only; every other close is no decision day, as in a weekly rule's week."""

    def decision_day(self, prev, fill) -> bool:
        return str(fill) == DAY1


class LateSendAccount(DailyAccount):
    """The engine fixture with the daily view of two evenings: the session before DAY1, and DAY1."""

    def __init__(self, tmp_path, monkeypatch):
        super().__init__(tmp_path, monkeypatch)
        s = WantsBOnce()
        self.rule = ld.Rule(s, ld.live_builder(s, ld.live_limits(s, POL, rule_book_limits(MD, "balanced",
                                                                                         research=True))))
        self.eng.daily = ld.for_engine(self.eng, self.load, rules=[self.rule])
        assert str(self.eng.daily.prev_session(date.fromisoformat(DAY1))) == DAY0
        # the same bars, one session earlier: the evening before DAY1
        self.before = replace(self.view, dates=self.view.dates - np.timedelta64(1, "D"))
        self.loaded: list[str] = []

    def load(self, k: date, now):
        self.loaded.append(str(k))
        if str(k) == DAY0:
            return self.before
        return super().load(k, now)

    def journal(self, kind: str) -> list[dict]:
        return [b for *_, b in self.eng.journal.entries(kinds=(kind,))]


def test_a_window_the_engine_missed_is_sent_at_the_next_session_once_through_the_engine(tmp_path, monkeypatch):
    a = LateSendAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 375)  # DAY1 to 15:45; the engine is then down over the 15:50-15:57 window
    (dec,) = a.journal("md_decision")
    assert (dec["session"], dec["fill_session"], dec["target"]["weights"]) == (DAY0, DAY1, [["B", 1.0]])
    assert dec["late"] is True and a.md_orders() == [] and a.journal("md_send") == []
    a.restart()  # back after the close
    a.after_close(a.open1)
    last = a.journal("md_decision")[-1]
    assert (last["session"], last["fill_session"], last["target"]) == (DAY1, DAY2, None)
    assert a.md_orders() == []
    a.ticks(a.open2, 0, SEND)
    assert a.md_orders() == [] and a.journal("md_send") == []  # nothing before 15:50
    a.ticks(a.open2, SEND, SEND + 1)
    (send,) = a.journal("md_send")
    assert (send["session"], send["late_of"], send["decided"]) == (DAY2, DAY1, DAY0) and "equity" in send
    assert [(s, p) for s, _, p, _ in send["targets"]] == [("B", "buy")]
    (o,) = a.md_orders()
    assert o["symbol"] == "B" and o["side"] == "buy" and float(o["qty"]) == float(send["targets"][0][1])
    assert o["client_order_id"].startswith(f"kb{a.rid}-{MD}-B-20260804-buy-")
    # the builder sized it at DAY1's close (the previous close of the late session), never at a bar of DAY2
    assert DAY2 not in a.loaded and a.loaded[-1] == DAY1
    a.restart()  # killed right after the late send: the same journalled targets, nothing sent twice
    a.ticks(a.open2, SEND + 1, 390)
    a.after_close(a.open2)
    for _ in range(3):
        a.eng.tick()
    assert len(a.md_orders()) == 1 and a.md_orders()[0]["status"] == "filled"
    assert len(a.journal("md_send")) == 1 and len([b for b in a.journal("md_sent") if b["session"] == DAY2]) == 1
    fills = [x for x in a.journal("fill") if x.get("symbol") == "B"]
    assert len(fills) == 1 and a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == float(o["qty"])
    assert a.eng.recon.check("test").diffs == [] and a.eng.account_clean and a.eng.kill is None
    kinds = [e["type"] for e in a.store.tables["event"]]
    assert "multi_day_error" not in kinds and "multi_day_no_decision" not in kinds
