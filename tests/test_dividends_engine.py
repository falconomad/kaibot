"""Issue #118 (S78): dividends accrued at the ex-date on the live engine (replacing #117's pay-date guard), on
fakes. Pure parts (parsing Alpaca's corporate actions, matching a payment) are in tests/unit/test_execution.py."""

from __future__ import annotations

import pytest
from test_ledger_engine import D2, carried, offer, rows, session

from kaibot.engine.dividends import ExDividend


def test_the_live_engine_accrues_a_dividend_at_the_ex_date_and_does_not_book_the_payment_again(tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    qty = rows(store, "trade")[0]["qty"]
    eng.ex_dividends = lambda day, syms: [ExDividend("A", str(day), 0.5, "2026-08-20")] if "A" in syms else []
    offer(eng, D2, at=())
    seen = {}

    def each(k):
        eng.tick()
        if k == 0:
            seen["adjust"] = eng.day.adjust

    session(eng, broker, clock, D2, each=each)
    assert seen["adjust"] == pytest.approx(0.5 * qty)  # booked at the ex-date, to the sleeve held over the close
    acc = [b for _, _, k, b in eng.journal.entries(kinds=("div_accrual",))]
    assert len(acc) == 1 and acc[0]["amount"] == pytest.approx(0.5 * qty)
    div0 = eng.ledger.book(eng._rid()).dividends_total
    # the pay date: Alpaca's DIV activity is tied to the accrual, not booked a second time (the book is flat now)
    broker.non_trade.append({"activity_type": "DIV", "id": "20260820::1", "date": "2026-08-20",
                             "net_amount": str(0.5 * qty), "symbol": "A", "qty": str(qty)})
    eng._corporate_actions(clock().date())
    eng._corporate_actions(clock().date())
    paid = [b for _, _, k, b in eng.journal.entries(kinds=("div_paid",))]
    assert len(paid) == 1 and paid[0]["ref"] == acc[0]["ref"]
    assert eng.ledger.book(eng._rid()).dividends_total == div0
    assert not any(e["type"] == "dividend_difference" for e in rows(store, "event"))


def test_no_phantom_receivable_when_the_dividend_is_accrued_again_after_its_payment(tmp_path):
    """Review B3: pay date = ex-date (or a restart the same day): the paid accrual must not be accrued again."""
    from kaibot.engine.dividends import receivable

    eng, broker, clock, store = carried(tmp_path)
    qty = rows(store, "trade")[0]["qty"]
    eng.ex_dividends = lambda day, syms: [ExDividend("A", str(day), 1.0, str(day))] if "A" in syms else []
    day = clock().date()
    b = eng.ledger.book(eng._rid())
    eng._accrue_dividends(b, day)
    broker.non_trade.append({"activity_type": "DIV", "id": "pay::1", "date": str(day), "net_amount": str(qty),
                             "symbol": "A", "qty": str(qty)})
    eng._match_dividend_payments(broker.activities(types=("DIV",)))
    eng._accrue_dividends(b, day)  # again the same day (a restart)
    entries = eng.journal.entries(kinds=("div_accrual", "div_paid"))
    assert sum(1 for e in entries if e[2] == "div_accrual") == 1 and receivable(entries) == 0.0
