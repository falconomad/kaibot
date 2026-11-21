"""M12 ticket 4.9 (#488): the broker adapter contract. Every adapter behind `kaibot.broker.protocol.Broker` must
pass every test here; today that is `SimBroker` and the Alpaca paper adapter (against a fake of Alpaca's REST API,
no network). An Interactive Brokers adapter would be the third (design note: docs/ops/broker_adapters.md).

The contract is what the engine relies on, not what one venue happens to do. Where venues legitimately differ the
test says which answers are allowed (a replace may keep the order id or give a new one; a lost answer may be
healed inside the adapter or surface as "no answer"), and pins what must hold in every case (one order per client
order id; the order can be found by that id).

Sections: shape of the answers; idempotent submission; lookups; rejections; executions and activities; cancel and
replace; the book (positions, account, close); time (clock, calendar); failures (network, lost answer); and the
order manager run over each adapter, which must see the same order lives and book the same fills.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from adapter_drivers import DAY, DRIVERS, NEXT, PRICE, WEEKEND, Driver

from kaibot.broker.protocol import Broker, BrokerBase, BrokerError, OrderRequest
from kaibot.engine.journal import Journal
from kaibot.engine.ledger import LEDGER_KINDS, Ledger
from kaibot.engine.orders import VENUE, OrderManager, St, client_order_id

REFUSED = (403, 422)
ORDER_KEYS = {"id", "client_order_id", "symbol", "side", "qty", "type", "time_in_force", "status", "filled_qty"}
FILL_KEYS = {"id", "activity_type", "order_id", "symbol", "side", "qty", "price", "transaction_time"}


@pytest.fixture(params=sorted(DRIVERS))
def venue(request) -> Driver:
    return DRIVERS[request.param]()


def coid(tag: str, n: int = 1) -> str:
    return client_order_id(1, "contract", "A", DAY, tag, n)


def market(side: str = "buy", qty: float = 5, tag: str = "buy", symbol: str = "A") -> OrderRequest:
    return OrderRequest(symbol, side, qty, "market", "day", client_order_id(1, "contract", symbol, DAY, tag))


def limit(price: float, side: str = "buy", qty: float = 5, tag: str = "rest") -> OrderRequest:
    return OrderRequest("A", side, qty, "limit", "day", coid(tag), limit_price=price)


def num(x) -> float:
    return float(x)


def when(x: str) -> datetime:
    t = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    assert t.tzinfo is not None, f"a time without a zone: {x}"
    return t


def holding(venue: Driver, symbol: str = "A") -> float:
    p = venue.broker.positions().get(symbol)
    return num(p["qty"]) if p else 0.0


def bought(venue: Driver, qty: float = 5) -> dict:
    o = venue.broker.submit(market("buy", qty))
    venue.trade()
    return venue.broker.order(o["id"])


# ---------------------------------------------------------------- shape
def test_the_adapter_is_a_broker_with_a_name(venue):
    assert isinstance(venue.broker, Broker) and isinstance(venue.broker, BrokerBase)
    assert isinstance(venue.broker.name, str) and venue.broker.name


def test_a_submitted_order_comes_back_in_the_shape_the_engine_reads(venue):
    o = venue.broker.submit(limit(90.0))
    assert set(o) >= ORDER_KEYS, f"missing {ORDER_KEYS - set(o)}"
    assert isinstance(o["id"], str) and o["id"]
    assert o["client_order_id"] == coid("rest") and o["symbol"] == "A" and o["side"] == "buy"
    assert num(o["qty"]) == 5 and num(o["filled_qty"]) == 0 and num(o["limit_price"]) == 90.0
    assert o["type"] == "limit" and o["time_in_force"] == "day"
    assert o["status"] in VENUE, f"a status the order manager cannot map: {o['status']}"
    assert VENUE[o["status"]] is St.ACCEPTED


# ---------------------------------------------------------------- idempotent submission
def test_the_same_client_order_id_twice_is_one_order(venue):
    first = venue.broker.submit(limit(90.0))
    again = venue.broker.submit(limit(90.0))
    assert again["id"] == first["id"]
    assert len(venue.venue_orders()) == 1 and len(venue.broker.open_orders()) == 1


def test_a_client_order_id_stays_taken_after_its_order_ended(venue):
    first = venue.broker.submit(limit(90.0))
    venue.broker.cancel(first["id"])
    again = venue.broker.submit(limit(90.0))
    assert again["id"] == first["id"] and VENUE[again["status"]] is St.CANCELED, "the ended order, not a new one"
    assert len(venue.venue_orders()) == 1


# ---------------------------------------------------------------- lookups
def test_an_order_is_found_by_its_id_and_by_its_client_order_id(venue):
    o = venue.broker.submit(limit(90.0))
    assert venue.broker.order(o["id"])["id"] == o["id"]
    assert venue.broker.order_by_client_id(coid("rest"))["id"] == o["id"]
    assert [x["id"] for x in venue.broker.open_orders()] == [o["id"]]


def test_an_unknown_order_is_404_by_id_and_none_by_client_order_id(venue):
    with pytest.raises(BrokerError) as e:
        venue.broker.order("no-such-order")
    assert e.value.status == 404
    assert venue.broker.order_by_client_id("kb1-never-sent-1") is None


def test_an_unknown_asset_is_none(venue):
    assert venue.broker.asset("NOSUCHTICKER") is None


# ---------------------------------------------------------------- rejections
def test_a_sale_without_shares_is_refused_and_leaves_nothing_at_the_venue(venue):
    with pytest.raises(BrokerError) as e:
        venue.broker.submit(market("sell", 5, "sell"))
    assert e.value.status in REFUSED, "a refusal is 403 or 422: the order manager rejects that order only"
    assert venue.broker.order_by_client_id(client_order_id(1, "contract", "A", DAY, "sell")) is None
    assert venue.venue_orders() == []


def test_a_buy_beyond_the_cash_is_refused(venue):
    with pytest.raises(BrokerError) as e:
        venue.broker.submit(limit(100.0, qty=1_000_000))
    assert e.value.status in REFUSED and venue.venue_orders() == []


def test_a_sale_cannot_use_shares_another_working_sale_holds(venue):
    bought(venue)
    venue.broker.submit(limit(150.0, "sell", 5, "sell1"))
    with pytest.raises(BrokerError) as e:
        venue.broker.submit(limit(151.0, "sell", 1, "sell2"))
    assert e.value.status in REFUSED


def test_an_activity_type_alpaca_does_not_have_is_refused_with_422(venue):
    with pytest.raises(BrokerError) as e:
        venue.broker.activities(types=("FILL", "SSP"))
    assert e.value.status == 422


# ---------------------------------------------------------------- executions and activities
def test_a_fill_shows_on_the_order_the_position_and_as_one_activity(venue):
    o = bought(venue)
    assert VENUE[o["status"]] is St.FILLED and num(o["filled_qty"]) == 5
    assert num(o["filled_avg_price"]) == pytest.approx(PRICE)
    assert holding(venue) == 5
    assert num(venue.broker.positions()["A"]["avg_entry_price"]) == pytest.approx(PRICE)
    assert venue.broker.open_orders() == []
    fills = venue.broker.activities(types=("FILL",))
    assert len(fills) == 1 and set(fills[0]) >= FILL_KEYS, f"missing {FILL_KEYS - set(fills[0])}"
    f = fills[0]
    assert f["activity_type"] == "FILL" and f["order_id"] == o["id"] and f["symbol"] == "A" and f["side"] == "buy"
    assert num(f["qty"]) == 5 and num(f["price"]) == pytest.approx(PRICE)
    assert when(f["transaction_time"]) <= venue.clock()


def test_partial_fills_are_one_activity_each_with_ids_that_never_repeat(venue):
    venue.partials("A", [2, 3])
    o = venue.broker.submit(market("buy", 5))
    venue.trade()
    mid = venue.broker.order(o["id"])
    assert VENUE[mid["status"]] is St.PARTIAL and num(mid["filled_qty"]) == 2 and holding(venue) == 2
    assert [x["id"] for x in venue.broker.open_orders()] == [o["id"]], "a partly filled order is still open"
    venue.trade()
    done = venue.broker.order(o["id"])
    assert VENUE[done["status"]] is St.FILLED and num(done["filled_qty"]) == 5 and holding(venue) == 5
    fills = venue.broker.activities(types=("FILL",))
    assert [num(f["qty"]) for f in fills] == [2, 3], "oldest first, at each execution's own quantity"
    assert len({f["id"] for f in fills}) == 2
    assert when(fills[0]["transaction_time"]) <= when(fills[1]["transaction_time"])
    assert [f["id"] for f in venue.broker.activities(types=("FILL",))] == [f["id"] for f in fills], "ids are stable"


def test_activities_after_a_time_leave_out_what_came_before(venue):
    bought(venue)
    cut = venue.clock()
    venue.trade()
    o = venue.broker.submit(market("buy", 1, "more"))
    venue.trade()
    every = venue.broker.activities(types=("FILL",))
    late = venue.broker.activities(after=cut, types=("FILL",))
    assert len(every) == 2 and [f["order_id"] for f in late] == [o["id"]]
    assert venue.broker.activities(after=venue.clock() + timedelta(hours=1), types=("FILL",)) == []


def test_many_fills_are_all_returned_oldest_first(venue):
    """More executions than one page of Alpaca's activities endpoint (100)."""
    n = 105
    venue.partials("A", [1.0] * n)
    venue.broker.submit(market("buy", n))
    for _ in range(n):
        venue.trade()
    fills = venue.broker.activities(types=("FILL",))
    assert len(fills) == n and len({f["id"] for f in fills}) == n
    times = [when(f["transaction_time"]) for f in fills]
    assert times == sorted(times) and holding(venue) == n


# ---------------------------------------------------------------- cancel and replace
def test_cancel_ends_the_order_and_is_harmless_twice_or_on_an_unknown_id(venue):
    o = venue.broker.submit(limit(90.0))
    assert venue.broker.cancel(o["id"]) is None
    assert VENUE[venue.broker.order(o["id"])["status"]] is St.CANCELED and venue.broker.open_orders() == []
    venue.broker.cancel(o["id"])  # already gone: not an error
    venue.broker.cancel("no-such-order")
    venue.broker.cancel_order(o["id"])  # the engine's wrapper is the same call
    venue.trade()
    assert holding(venue) == 0 and venue.broker.activities(types=("FILL",)) == []


def test_cancelling_a_filled_order_changes_nothing(venue):
    o = bought(venue)
    venue.broker.cancel(o["id"])
    assert VENUE[venue.broker.order(o["id"])["status"]] is St.FILLED and holding(venue) == 5


def test_replace_reprices_under_the_client_order_id_it_was_given(venue):
    o = venue.broker.submit(limit(90.0))
    new = venue.broker.replace(o["id"], limit_price=91.0, client_order_id=coid("rest", 2))
    assert num(new["limit_price"]) == 91.0 and new["client_order_id"] == coid("rest", 2)
    assert VENUE[new["status"]] is St.ACCEPTED
    assert venue.broker.order_by_client_id(coid("rest", 2))["id"] == new["id"]
    assert [x["id"] for x in venue.broker.open_orders()] == [new["id"]], "one working order, the replacement"
    if new["id"] != o["id"]:  # a venue that replaces with a new order (Alpaca): the old one says where it went
        old = venue.broker.order(o["id"])
        assert old["status"] == "replaced" and old["replaced_by"] == new["id"]


def test_a_replacement_fills_and_its_activity_names_the_working_order(venue):
    o = venue.broker.submit(limit(90.0))
    new = venue.broker.replace(o["id"], limit_price=101.0, client_order_id=coid("rest", 2))
    venue.trade()
    assert VENUE[venue.broker.order(new["id"])["status"]] is St.FILLED and holding(venue) == 5
    assert [f["order_id"] for f in venue.broker.activities(types=("FILL",))] == [new["id"]]


def test_replace_refuses_a_client_order_id_already_used_and_an_order_that_ended(venue):
    o = venue.broker.submit(limit(90.0))
    with pytest.raises(BrokerError) as e:
        venue.broker.replace(o["id"], limit_price=91.0, client_order_id=coid("rest"))
    assert e.value.status == 422
    venue.broker.cancel(o["id"])
    with pytest.raises(BrokerError) as e:
        venue.broker.replace(o["id"], limit_price=92.0, client_order_id=coid("rest", 3))
    assert e.value.status in (404, 422)


# ---------------------------------------------------------------- the book
def test_the_account_reports_cash_and_equity_as_numbers(venue):
    a = venue.broker.account()
    cash0 = num(a["cash"])
    assert a["status"] == "ACTIVE" and num(a["equity"]) == pytest.approx(cash0) and num(a["buying_power"]) >= 0
    bought(venue)
    a = venue.broker.account()
    assert num(a["cash"]) == pytest.approx(cash0 - 5 * PRICE) and num(a["equity"]) == pytest.approx(cash0)


def test_a_sale_reduces_the_position_and_the_last_share_removes_it(venue):
    bought(venue)
    venue.broker.submit(market("sell", 2, "sell"))
    venue.trade()
    assert holding(venue) == 3
    venue.broker.submit(market("sell", 3, "sell2"))
    venue.trade()
    assert "A" not in venue.broker.positions()


def test_close_position_sells_everything_and_is_none_without_a_position(venue):
    assert venue.broker.close_position("A") is None
    bought(venue)
    resting = venue.broker.submit(limit(150.0, "sell", 5, "sell"))
    o = venue.broker.close_position("A")
    assert o is not None and o["side"] == "sell" and num(o["qty"]) == 5
    assert VENUE[venue.broker.order(resting["id"])["status"]] is St.CANCELED, "the order holding the shares went first"
    venue.trade()
    assert "A" not in venue.broker.positions()


# ---------------------------------------------------------------- time
def test_the_clock_has_the_four_fields_with_zoned_times(venue):
    c = venue.broker.clock()
    assert set(c) >= {"timestamp", "is_open", "next_open", "next_close"}
    assert c["is_open"] is True, "10:00 ET on a session day"
    now, nxt_open, nxt_close = when(c["timestamp"]), when(c["next_open"]), when(c["next_close"])
    assert abs((now - venue.clock()).total_seconds()) < 5, "the venue's time is the injected clock's"
    assert nxt_close > now and nxt_open > now
    assert nxt_close.astimezone(venue.clock().tzinfo) == datetime.fromisoformat(f"{DAY}T20:00:00+00:00")
    assert str(nxt_open.astimezone(now.tzinfo).date()) == NEXT


def test_the_calendar_gives_a_sessions_hours_and_none_for_a_closed_day(venue):
    assert venue.broker.calendar(DAY) == {"date": DAY, "open": "09:30", "close": "16:00"}
    assert venue.broker.calendar(WEEKEND) is None


def test_a_day_order_is_gone_after_the_close(venue):
    o = venue.broker.submit(limit(90.0))
    for _ in range(361):  # 10:00 to 16:01
        venue.trade()
    assert venue.broker.clock()["is_open"] is False
    assert VENUE[venue.broker.order(o["id"])["status"]] is St.EXPIRED and venue.broker.open_orders() == []


# ---------------------------------------------------------------- failures
def test_no_answer_at_all_is_broker_error_status_0_on_every_call(venue):
    venue.cut_network(True)
    calls = [lambda: venue.broker.submit(limit(90.0)), venue.broker.open_orders, venue.broker.positions,
             venue.broker.account, venue.broker.clock, lambda: venue.broker.activities(types=("FILL",)),
             lambda: venue.broker.order_by_client_id(coid("rest")), lambda: venue.broker.cancel("x")]
    for call in calls:
        with pytest.raises(BrokerError) as e:
            call()
        assert e.value.status == 0, "status 0: the order may or may not exist, look it up by its client order id"
    venue.cut_network(False)
    assert venue.venue_orders() == [] and venue.broker.order_by_client_id(coid("rest")) is None


def test_a_submission_whose_answer_was_lost_is_one_order_found_by_its_client_order_id(venue):
    venue.lose_next_answer()
    try:
        o = venue.broker.submit(limit(90.0))  # an adapter may heal it itself (Alpaca: the retry is refused as a
    except BrokerError as e:  # duplicate and the existing order is read back) ...
        assert e.status == 0  # ... or report that no answer came
        o = venue.broker.order_by_client_id(coid("rest"))
    assert o is not None and o["client_order_id"] == coid("rest")
    assert venue.broker.submit(limit(90.0))["id"] == o["id"]
    assert len(venue.venue_orders()) == 1


# ---------------------------------------------------------------- the order manager over each adapter
def _order_lives(venue: Driver) -> dict:
    """One script through the real order manager: a buy that fills in two parts, a resting order that is re-priced
    and cancelled, a submission whose answer is lost, a refused sale, a sale. Returns what the engine saw."""
    journal = Journal(now=venue.clock)
    journal.append("run", {"run_id": 1, "amount": 100_000.0})
    om = OrderManager(venue.broker, journal, venue.clock, run_id=1)

    def send(req: OrderRequest, purpose: str):
        return om.submit(req, strategy="contract", purpose=purpose, session=DAY)

    venue.partials("A", [2, 3])
    buy = send(market("buy", 5), "buy")
    venue.trade()
    om.sync()
    venue.trade()
    om.sync()
    rest = send(limit(90.0), "rest")
    om.replace(rest.coid, limit=91.0)
    om.cancel(rest.coid)
    venue.lose_next_answer()
    lost = send(OrderRequest("B", "buy", 1, "limit", "day", client_order_id(1, "contract", "B", DAY, "lost"),
                             limit_price=101.0), "lost")
    om.sync()  # the lookup by client order id
    venue.trade()
    om.sync()
    refused = send(market("sell", 50, "toomuch"), "sell")
    sell = send(market("sell", 2, "sell"), "sell")
    venue.trade()
    om.sync()
    states: dict[str, list[str]] = {}
    for _, _, _, b in journal.entries(kinds=("om_state",)):
        seen = states.setdefault(b["coid"], [])
        if not seen or seen[-1] != b["state"]:
            seen.append(b["state"])
    book = Ledger.fold(journal.entries(kinds=LEDGER_KINDS)).book(1)
    fills = [(b["symbol"], b["side"], float(b["qty"]), float(b["px"]))
             for _, _, _, b in journal.entries(kinds=("fill",))]
    assert om.foreign == {} and not om.settling
    return {"states": states, "final": {c: str(r.state) for c, r in om.orders.items()}, "fills": sorted(fills),
            "ledger": {k[1]: s.qty for k, s in book.sleeves.items()}, "cash": round(book.cash, 2),
            "venue": {s: num(p["qty"]) for s, p in venue.broker.positions().items()},
            "venue_cash": round(num(venue.broker.account()["cash"]), 2),
            "coids": (buy.coid, rest.coid, lost.coid, refused.coid, sell.coid)}


def test_the_order_manager_books_the_same_script_correctly_on_this_adapter(venue):
    out = _order_lives(venue)
    buy, rest, lost, refused, sell = out["coids"]
    assert out["final"] == {buy: "filled", rest: "canceled", lost: "filled", refused: "rejected", sell: "filled"}
    assert out["states"][buy] == ["accepted", "partially_filled", "filled"]
    assert out["states"][rest][0] == "accepted" and "pending_replace" in out["states"][rest]
    assert out["states"][rest][-2:] == ["pending_cancel", "canceled"]
    assert out["states"][lost][-1] == "filled"
    assert out["fills"] == sorted([("A", "buy", 2.0, 100.0), ("A", "buy", 3.0, 100.0), ("B", "buy", 1.0, 101.0),
                                   ("A", "sell", 2.0, 100.0)])
    assert out["ledger"] == out["venue"] == {"A": 3.0, "B": 1.0}
    assert out["cash"] == out["venue_cash"] == 100_000.0 - 500.0 - 101.0 + 200.0


def test_every_adapter_gives_the_order_manager_the_same_final_book():
    """Parity: the engine cannot tell the adapters apart by the book it ends with."""
    results = {name: _order_lives(make()) for name, make in sorted(DRIVERS.items())}
    first, *others = results.values()
    for other in others:
        for key in ("final", "fills", "ledger", "cash", "venue", "venue_cash"):
            assert other[key] == first[key], f"{key} differs between adapters: {results}"
