"""Backend-quant key on #248 (#131): a pre-open review that is still running can never hold the rule's SALES past
the send window. The send waits for a running review (bounded by its own deadline), but from 5 minutes before the
window closes it stops waiting: the review is journalled as a timeout (no buys this session, fail closed) and the
sales go out. Here the review never finishes (a worker that hangs): before the cut nothing is sent; at the cut the
sale of B goes out and `md_review` says timeout."""

from __future__ import annotations

from datetime import timedelta

from test_live_daily_engine import MD, DailyAccount, SellsB, _book_b

from kaibot.engine import live_daily as ld


class StuckReview:
    """A review that started and never answers (its worker hangs)."""

    enabled = True

    def __init__(self):
        self.submitted = []

    def pending(self, rule, session) -> bool:
        return True

    def collect(self, rule, session):
        return None

    def submit(self, *a, **kw) -> None:
        self.submitted.append(a)


def test_a_review_that_never_finishes_cannot_hold_the_sales_past_the_send_window(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    a.rule = type(a.rule)(SellsB(), a.rule.builder)
    a.eng.daily._rules = lambda r=[a.rule]: r
    _book_b(a)
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.eng.daily.review = StuckReview()
    cut = 375 - int(ld.REVIEW_WAIT_UNTIL_S // 60)  # 15:45 is minute 375; the cut is 5 minutes earlier
    a.ticks(a.open2, 0, cut - 1)
    assert not [o for o in a.cls_orders() if o["side"] == "sell"]  # it waits for the review while there is time
    assert not a.eng.journal.entries(kinds=("md_review",))
    a.ticks(a.open2, cut - 1, cut + 1)
    (sale,) = [o for o in a.cls_orders() if o["side"] == "sell"]
    assert sale["symbol"] == "B" and float(sale["qty"]) == 40
    (rev,) = [b for *_, b in a.eng.journal.entries(kinds=("md_review",))]
    assert rev["status"] == "timeout" and rev["strategy"] == MD and rev["vetoed"] == []
    (send,) = [b for *_, b in a.eng.journal.entries(kinds=("md_send",)) if b["session"] == str(a.open2.date())]
    assert send["review"]["status"] == "timeout" and [t[0] for t in send["targets"]] == ["B"]
    a.ticks(a.open2, cut + 1, 390)
    a.after_close(a.open2)
    assert "B" not in a.broker.pos and len([o for o in a.cls_orders() if o["side"] == "sell"]) == 1


def test_the_window_left_counts_to_the_send_windows_end(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    t = a.open1 + timedelta(hours=6, minutes=5)  # 15:35 ET
    assert a.eng.daily._window_left(t) == 600.0
