"""#291: the fail-safes on an ex-date with the engine down since before the open (written before the code). The book
(last close's rows) is on the pre-action basis; nothing says what happened, so the plan is conservative:
- a multi-day symbol whose broker count is the book's times a split ratio (2, 3, ... or 3-for-2, or the inverse):
  every broker share of it is kept (none sold as intraday) and stops cover at most the broker's shares (no false
  "unprotected" for a reverse split). Any other excess stays intraday, as decision 15's plan says;
- a multi-day symbol gone at the broker while an unexplained position holds exactly its shares (a symbol change):
  that position is kept, not closed as intraday;
- an intraday-only symbol is still flattened as before; nothing ever sells more than the broker holds."""

from __future__ import annotations

from kaibot.engine import failsafe as fs

MD = "trend_momentum"


def book(*sleeves):
    return fs.BookView([fs.SleeveView(MD, s, q, None) for s, q in sleeves], "store", 1)


def pos(**qty):
    return {s: {"qty": str(q), "current_price": "50"} for s, q in qty.items()}


def test_a_forward_split_sells_no_multi_day_share():
    p = fs.plan(pos(B=80), [], book(("B", 40)), flatten=True, names=(MD,))
    assert p.multi_day["B"] == 80 and not p.sell and not p.close
    assert any("B" in a and "split" in a for a in p.alarms)


def test_a_reverse_split_protects_the_broker_shares_without_a_false_alarm():
    p = fs.plan(pos(B=4), [], book(("B", 40)), flatten=True, names=(MD,))
    assert p.multi_day["B"] == 4 and not p.sell and not p.close
    assert [s.qty for s in p.stops if s.symbol == "B"] == [4] and "B" not in p.unprotected


def test_a_symbol_change_keeps_the_renamed_holding():
    p = fs.plan(pos(B2=40), [], book(("B", 40)), flatten=True, names=(MD,))
    assert "B2" not in p.close and not p.sell and p.multi_day.get("B2") == 40
    assert any("B2" in a for a in p.alarms)


def test_an_intraday_only_symbol_is_still_flattened():
    p = fs.plan(pos(B=40, A=7), [], book(("B", 40)), flatten=True, names=(MD,))
    assert p.close == ["A"] and p.multi_day == {"B": 40}
    p = fs.plan(pos(B2=40, A=7), [], book(("B", 40)), flatten=True, names=(MD,))
    assert p.close == ["A"]  # A does not hold B's 40 shares: not the renamed holding


def test_intraday_shares_with_their_legs_are_still_sold_beside_the_book():
    """A symbol held by both: 40 multi-day shares (the book) and 10 intraday shares, which carry their bracket legs.
    The legs explain the extra shares: they are sold as before. Shares nothing explains are kept."""
    legs = [{"id": "s1", "symbol": "B", "side": "sell", "type": "stop", "qty": "10", "status": "new",
             "client_order_id": "kb-3-17-1-stop"}]
    p = fs.plan(pos(B=50), legs, book(("B", 40)), flatten=True, names=(MD,))
    assert p.multi_day["B"] == 40 and [(x[0], x[1]) for x in p.sell] == [("B", 10)]
    p = fs.plan(pos(B=50), [], book(("B", 40)), flatten=True, names=(MD,))  # 1.25 is no split: intraday, as before
    assert p.multi_day["B"] == 40 and [(x[0], x[1]) for x in p.sell] == [("B", 10)]


def test_unchanged_books_plan_as_before():
    p = fs.plan(pos(B=40), [], book(("B", 40)), flatten=True, names=(MD,))
    assert p.multi_day["B"] == 40 and not p.sell and not p.close and not any("split" in a for a in p.alarms)
