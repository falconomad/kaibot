"""#266, Product's priority 0 (trend_momentum.md Amendment 6, a live-safety exclusion): the live daily path does not
buy, or add to, a stock with an announced split, reverse split, spin-off, merger or symbol change in the next 35
sessions; a held one is not sold for it but named on the heartbeat; a failed read buys nothing. On the simulated
venue of the #206 tests, with a fake Alpaca corporate-actions feed."""

from __future__ import annotations

from test_failsafe_drills import DAY2, MD
from test_live_daily_engine import DailyAccount

from kaibot.engine import live_daily as ld
from kaibot.engine.corp_guard import CorporateActionGuard


class Feed:
    def __init__(self, block: dict | None = None, fail: bool = False) -> None:
        self.block, self.fail, self.calls = block or {}, fail, 0

    def __call__(self, path, params):
        self.calls += 1
        if self.fail:
            raise OSError("network down")
        return {"corporate_actions": self.block, "next_page_token": None}


SPIN_B = {"spin_offs": [{"source_symbol": "B", "new_symbol": "BN", "ex_date": "2026-08-20"}]}


class GuardedAccount(DailyAccount):
    def __init__(self, tmp_path, monkeypatch, feed: Feed, **kw):
        self.feed = feed
        super().__init__(tmp_path, monkeypatch, **kw)

    def engine(self, journal=None):
        eng = super().engine(journal)
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule], ca=CorporateActionGuard(get=self.feed))
        return eng


def _sends(a):
    return [b for *_, b in a.eng.journal.entries(kinds=("md_send",)) if b["session"] == DAY2]


def test_an_announced_spin_off_skips_the_buy_and_journals_why(tmp_path, monkeypatch):
    a = GuardedAccount(tmp_path, monkeypatch, Feed(SPIN_B))
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 2)  # 09:31: the send step
    assert a.cls_orders() == []  # B is not bought
    (send,) = _sends(a)
    assert send["session"] == DAY2 and send["targets"] == []
    assert send["ca_skipped"] == [["B", "spin-off", "2026-08-20", "skipped: corporate action on 2026-08-20"]]
    (ev,) = [e for e in a.store.tables["event"] if e["type"] == "multi_day_ca_skip"]
    assert "skipped: corporate action on 2026-08-20" in str(ev)
    a.ticks(a.open2, 2, 390)
    assert a.cls_orders() == []  # and never later in the session


def test_no_announcement_buys_as_before(tmp_path, monkeypatch):
    feed = Feed({"forward_splits": [{"symbol": "ZZZ", "ex_date": "2026-08-10"}]})
    a = GuardedAccount(tmp_path, monkeypatch, feed)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 2)
    (o,) = a.cls_orders()
    assert o["symbol"] == "B" and o["side"] == "buy"
    assert "ca_skipped" not in _sends(a)[0]
    assert feed.calls >= 1


def test_a_failed_read_buys_nothing_and_says_why_on_the_heartbeat(tmp_path, monkeypatch):
    a = GuardedAccount(tmp_path, monkeypatch, Feed(fail=True))
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 2)
    assert a.cls_orders() == []
    (send,) = _sends(a)
    assert send["targets"] == [] and "corporate-actions read failed" in send["ca_unread"]
    note = a.eng.daily.status(a.open2.date())["note"]
    assert "no new buys" in note and "corporate-actions read failed" in note


def test_a_held_stock_with_an_announcement_is_kept_and_named_on_the_heartbeat(tmp_path, monkeypatch):
    a = GuardedAccount(tmp_path, monkeypatch, Feed(SPIN_B))
    a.hold_b()  # 40 shares of B held by the multi-day rule since DAY1
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)  # the rule keeps B (it holds it); the evening's read names B's spin-off
    st = a.eng.daily.status(a.open2.date())
    assert "B: spin-off on 2026-08-20 — the engine does not handle this yet, check before then" in st["note"]
    a.ticks(a.open2, 0, 390)
    assert not [o for o in a.broker.orders.values() if o["symbol"] == "B" and o["side"] == "sell"
                and o["time_in_force"] == "cls"]  # not sold because of the guard
    assert a.eng.ledger.book(a.rid).sleeves[(MD, "B")].qty == 40


def test_a_held_stocks_note_is_dropped_once_its_date_has_passed(tmp_path, monkeypatch):
    """QA-Ops follow-up: the note comes from the last evening that read. On the action's date it is still shown,
    after it, it is gone (the read may be days old on evenings without a decision)."""
    from datetime import date

    a = GuardedAccount(tmp_path, monkeypatch, Feed(SPIN_B))
    a.hold_b()
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    line = "B: spin-off on 2026-08-20"
    assert line in a.eng.daily.status(date(2026, 8, 20))["note"]
    assert line not in (a.eng.daily.status(date(2026, 8, 21)).get("note") or "")
    assert not any(line in n for n in a.eng.daily.status(date(2026, 8, 21)).get("corporate_actions", []))
