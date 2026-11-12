"""SimBroker (issue #115): the replay's fill rules, the cost model and Alpaca's real order rules behind the
`Broker` protocol, deterministic and scriptable. Every rule the story names has its own test here."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fakes import Clock, FakeBroker, FakeData

from kaibot.broker import Broker, BrokerError, OrderRequest
from kaibot.broker.shadow_costs import buy_px, half_spread, sell_fees, sell_px
from kaibot.broker.sim import COSTS_V1, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.replay.data import ET
from kaibot.replay.history import BarHistory

DAY, NEXT = "2026-09-15", "2026-09-16"
SRC = Path(__file__).resolve().parents[1] / "src" / "kaibot"


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=ET) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def bar(m: int, day: str = DAY) -> datetime:
    return et(day, "09:30") + timedelta(minutes=m)


def flat(day: str, px: float = 100.0, v: float = 10_000.0, edits: dict | None = None, drop=()) -> pd.DataFrame:
    """390 one-minute bars at `px` (range 5 cents each side); `edits` {minute: {column: value}}."""
    ts = pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(np.arange(390), unit="m")
    f = pd.DataFrame({"ts": ts, "o": px, "h": px + 0.05, "l": px - 0.05, "c": px, "v": v, "vw": px})
    for m, kv in (edits or {}).items():
        for k, val in kv.items():
            f.loc[m, k] = val
    return f.drop(index=list(drop)).reset_index(drop=True)


def venue(frames: dict | None = None, start: datetime | None = None, **kw) -> tuple[SimBroker, SimClock]:
    clock = SimClock(start or et(DAY, "10:00", 7))
    frames = frames or {"A": pd.concat([flat(DAY), flat(NEXT)], ignore_index=True)}
    kw.setdefault("costs", None)  # raw bar prices unless a test is about costs
    return SimBroker(BarHistory(frames), clock, **kw), clock


def buy(b: SimBroker, qty=100, **kw):
    return b.submit(OrderRequest("A", "buy", qty, "market", "day", **kw))


def bracket(b: SimBroker, qty=100, stop=99.0, target=102.0, coid="kb-1-1-1"):
    return b.submit_bracket(coid, "A", qty, stop, target)


def run(b: SimBroker, clock: SimClock, m0: int, m1: int, day: str = DAY) -> None:
    """Trade bars m0..m1 (inclusive), moving the clock to 7 s after each bar's end."""
    for m in range(m0, m1 + 1):
        b.advance(bar(m, day))
        clock.set(max(clock(), bar(m, day) + timedelta(minutes=1, seconds=7)))


# ================================================================ protocol and determinism
def test_sim_broker_and_the_test_fake_implement_the_protocol():
    b, clock = venue()
    assert isinstance(b, Broker) and b.name == "sim"
    fake = FakeBroker(FakeData({"A": flat(DAY)}, Clock(et(DAY, "10:00"))), Clock(et(DAY, "10:00")), DAY)
    assert isinstance(fake, Broker) and isinstance(fake, SimBroker) and fake.name == "fake"


def test_no_wall_clock_and_no_randomness_in_the_simulation_modules():
    banned = {"time", "random", "secrets", "uuid"}
    for rel in ("broker/sim.py", "replay/history.py", "engine/clock.py", "engine/runner.py"):
        tree = ast.parse((SRC / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & banned, rel
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned, rel
            if isinstance(node, ast.Attribute) and node.attr in ("now", "utcnow", "today"):
                raise AssertionError(f"{rel} reads the wall clock (.{node.attr})")
            if isinstance(node, ast.Attribute) and node.attr == "random":
                raise AssertionError(f"{rel} uses randomness")


def test_same_script_gives_the_same_book_twice():
    def once():
        b, clock = venue(costs=COSTS_V1)
        bracket(b)
        run(b, clock, 30, 60)
        b.close_position("A")
        run(b, clock, 61, 62)
        return b.orders, b.fills, b.account()

    assert once() == once()


# ================================================================ fill rules (the replay's)
def test_market_order_fills_at_the_open_of_the_bar_it_arrived_in_and_never_earlier():
    frames = {"A": flat(DAY, edits={29: {"o": 90.0}, 30: {"o": 100.02}, 31: {"o": 100.04}})}
    b, clock = venue(frames)  # 10:00:07: the 09:59 bar (m 29) is complete, the 10:00 bar (m 30) has begun
    o = buy(b)
    b.advance(bar(29))
    assert o["status"] == "new", "a bar that began before the order was sent cannot fill it"
    b.advance(bar(30))
    assert o["status"] == "filled" and float(o["filled_avg_price"]) == 100.02 and o["filled_at"] == bar(30).isoformat()
    assert b.positions() == {"A": {"qty": 100, "avg_entry_price": 100.02}}
    b.advance(bar(30))
    b.advance(bar(12))  # a repeated or earlier bar is ignored: nothing fills in the past
    assert len(b.fills) == 1 and b.positions()["A"]["qty"] == 100


def test_market_fills_are_priced_by_the_cost_model_like_the_replay():
    days = pd.bdate_range(end=DAY, periods=25)
    rng = np.random.RandomState(3)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    frames = {"A": pd.concat([flat(str(d.date()), px=round(float(p), 2), v=5_000.0)
                              for d, p in zip(days, px, strict=True)], ignore_index=True)}
    hist = BarHistory(frames)
    close = np.round(px, 2)
    r = np.r_[np.nan, close[1:] / close[:-1] - 1]
    sigma = float(pd.Series(r).shift(1).rolling(20, min_periods=20).std().iloc[-1] / np.sqrt(390))
    assert np.isfinite(sigma) and hist.sigma_1m("A", days[-1].date()) == sigma
    assert np.isnan(hist.sigma_1m("A", days[5].date()))  # fewer than 20 earlier returns
    b = SimBroker(hist, SimClock(et(DAY, "10:00", 7)))
    o = buy(b, 150)
    b.advance(bar(30))
    last = round(float(px[-1]), 2)
    assert float(o["filled_avg_price"]) == buy_px(last, 150, sigma, 5_000.0) > last + half_spread(last)
    b.clk.set(et(DAY, "10:05", 7))
    s = b.close_position("A")
    b.advance(bar(36))
    assert float(s["filled_avg_price"]) == sell_px(last, 150, sigma, 5_000.0) < last - half_spread(last)
    spent = 150 * float(o["filled_avg_price"])
    got = 150 * float(s["filled_avg_price"]) - sell_fees(150, float(s["filled_avg_price"]))
    assert b.cash == pytest.approx(100_000.0 - spent + got) and b.fills[-1]["fees"] > 0


def test_bracket_legs_arm_only_after_a_complete_fill():
    b, clock = venue()
    b.script.fills("A", [40, 60])
    o = bracket(b)
    assert [x["status"] for x in o["legs"]] == ["held", "held"]
    b.advance(bar(30))
    assert o["status"] == "partially_filled" and o["filled_qty"] == 40 and o["filled_at"] is None
    assert [x["status"] for x in o["legs"]] == ["held", "held"], "a partly filled entry has no working exits"
    assert b.positions()["A"]["qty"] == 40
    b.advance(bar(31))
    assert o["status"] == "filled" and o["filled_qty"] == 100 and o["filled_at"] == bar(31).isoformat()
    assert [x["status"] for x in o["legs"]] == ["new", "new"]
    assert [(f["type"], f["leaves_qty"]) for f in b.fills] == [("partial_fill", "60"), ("fill", "0")]


def test_partly_filled_entry_that_is_cancelled_leaves_shares_without_exits():
    b, clock = venue()
    b.script.fills("A", [40])  # the other 60 never fill
    o = bracket(b)
    run(b, clock, 30, 35)
    assert o["status"] == "partially_filled" and o["filled_qty"] == 40
    b.cancel(o["id"])
    assert o["status"] == "canceled" and [x["status"] for x in o["legs"]] == ["canceled", "canceled"]
    assert b.positions()["A"]["qty"] == 40, "Alpaca leaves these shares unprotected: the engine must handle it (D3)"


def test_a_triggered_stop_is_a_market_order_and_a_gap_fills_at_the_open():
    frames = {"A": flat(DAY, edits={40: {"l": 98.9}, 41: {"o": 100.0}})}
    b, clock = venue(frames)
    o = bracket(b, stop=99.0, target=105.0)
    run(b, clock, 30, 40)
    stop = o["legs"][0]
    assert stop["status"] == "filled" and float(stop["filled_avg_price"]) == 99.0  # touched inside the bar
    assert o["legs"][1]["status"] == "canceled" and b.positions() == {}

    frames = {"A": flat(DAY, edits={40: {"o": 97.5, "h": 97.6, "l": 97.2, "c": 97.4}})}
    b, clock = venue(frames)
    o = bracket(b, stop=99.0, target=105.0)
    run(b, clock, 30, 40)
    assert float(o["legs"][0]["filled_avg_price"]) == 97.5, "gapped through: the open, not the stop price"

    b, clock = venue(frames, costs=COSTS_V1)
    o = bracket(b, stop=99.0, target=105.0)
    run(b, clock, 30, 40)
    assert float(o["legs"][0]["filled_avg_price"]) == sell_px(97.5, 100, 0.0, 10_000.0) < 97.5  # market costs apply


def test_target_needs_a_trade_through_and_pays_no_cost():
    frames = {"A": flat(DAY, edits={40: {"h": 102.0}, 45: {"h": 102.01}})}
    b, clock = venue(frames, costs=COSTS_V1)
    o = bracket(b, stop=95.0, target=102.0)
    run(b, clock, 30, 44)
    assert o["legs"][1]["status"] == "new", "touching the limit is not a fill"
    run(b, clock, 45, 45)
    tgt = o["legs"][1]
    assert tgt["status"] == "filled" and float(tgt["filled_avg_price"]) == 102.0
    assert o["legs"][0]["status"] == "canceled"


def test_stop_wins_when_one_bar_touches_stop_and_target():
    frames = {"A": flat(DAY, edits={40: {"h": 103.0, "l": 98.0}})}
    b, clock = venue(frames)
    o = bracket(b, stop=99.0, target=102.0)
    run(b, clock, 30, 40)
    assert o["legs"][0]["status"] == "filled" and o["legs"][1]["status"] == "canceled"


def test_legs_work_from_the_bar_of_the_fill():
    frames = {"A": flat(DAY, edits={30: {"o": 100.0, "l": 98.5}})}
    b, clock = venue(frames)
    o = bracket(b, stop=99.0, target=105.0)
    b.advance(bar(30))
    stop = o["legs"][0]
    assert o["status"] == "filled" and stop["status"] == "filled" and stop["filled_at"] == o["filled_at"]


def test_a_minute_without_trades_carries_the_last_close_and_nothing_fills_before_the_first_print():
    frames = {"A": flat(DAY, edits={29: {"c": 100.3}}, drop=(30, 31)), "B": flat(DAY, drop=range(0, 45))}
    b, clock = venue(frames, costs=COSTS_V1)
    o = buy(b)
    b.advance(bar(30))
    assert o["status"] == "filled" and float(o["filled_avg_price"]) == buy_px(100.3, 100, 0.0, 0.0)  # volume 0 -> 1
    ob = b.submit(OrderRequest("B", "buy", 10, "market", "gtc"))
    run(b, clock, 31, 44)
    assert ob["status"] == "new", "no print yet today: no price to fill at"
    run(b, clock, 45, 45)
    assert ob["status"] == "filled"


def test_limit_and_stop_orders_on_their_own():
    frames = {"A": flat(DAY, edits={35: {"l": 99.5}, 36: {"l": 99.49}, 50: {"h": 101.0}})}
    b, clock = venue(frames)
    lim = b.submit(OrderRequest("A", "buy", 10, "limit", "day", limit_price=99.5))
    stp = b.submit(OrderRequest("A", "buy", 10, "stop", "gtc", stop_price=101.0))
    run(b, clock, 30, 35)
    assert lim["status"] == "new"
    run(b, clock, 36, 36)
    assert lim["status"] == "filled" and float(lim["filled_avg_price"]) == 99.5
    run(b, clock, 37, 50)
    assert stp["status"] == "filled" and float(stp["filled_avg_price"]) == 101.0


# ================================================================ Alpaca's rules
@pytest.mark.parametrize(("when", "ok"), [("09:27", True), ("09:28", False), ("12:00", False), ("18:59", False),
                                         ("19:00", True), ("04:00", True)])
def test_opg_orders_are_accepted_only_between_1900_and_0928_et(when, ok):
    b, clock = venue(start=et(DAY, when))
    req = OrderRequest("A", "buy", 10, "market", "opg")
    if not ok:
        with pytest.raises(BrokerError, match="opg orders must be submitted after 7:00pm and before 9:28am") as e:
            b.submit(req)
        assert e.value.status == 422 and b.orders == {}
        return
    o = b.submit(req)
    day = NEXT if when == "19:00" else DAY
    b.advance(bar(0, day))
    assert o["status"] == "filled" and o["filled_at"] == bar(0, day).isoformat()


@pytest.mark.parametrize(("when", "ok"), [("15:49", True), ("15:50", False), ("16:30", False), ("18:59", False),
                                         ("19:00", True), ("10:00", True)])
def test_cls_orders_are_rejected_between_1550_and_1900_et(when, ok):
    frames = {"A": pd.concat([flat(DAY, edits={389: {"c": 100.4}}), flat(NEXT, edits={389: {"c": 100.6}})],
                             ignore_index=True)}
    b, clock = venue(frames, start=et(DAY, "09:31"), costs=COSTS_V1)
    buy(b, 10)
    b.advance(bar(1))
    clock.set(et(DAY, when))
    req = OrderRequest("A", "sell", 10, "market", "cls", "moc-1")
    if not ok:
        with pytest.raises(BrokerError, match="cls orders must be submitted before 3:50pm or after 7:00pm") as e:
            b.submit(req)
        assert e.value.status == 422
        return
    o = b.submit(req)
    day, close = (NEXT, 100.6) if when == "19:00" else (DAY, 100.4)
    for m in (388, 389):
        b.advance(bar(m, day))
    assert o["status"] == "filled" and o["filled_at"] == bar(389, day).isoformat()
    assert float(o["filled_avg_price"]) == close - half_spread(close), "the closing print less half a spread"


def test_fractional_orders_are_day_only():
    b, clock = venue(start=et(DAY, "08:00"))  # a time at which opg and cls orders are both accepted
    for tif in ("gtc", "opg", "cls"):
        with pytest.raises(BrokerError, match="fractional orders must be DAY orders") as e:
            b.submit(OrderRequest("A", "buy", 0.5, "market", tif))
        assert e.value.status == 422
    assert b.submit(OrderRequest("A", "buy", 2, "market", "opg"))["status"] == "new"  # whole shares may
    o = b.submit(OrderRequest("A", "buy", 0.5, "market", "day"))
    b.advance(bar(0))
    assert o["status"] == "filled" and b.positions()["A"]["qty"] == 2.5


def test_bracket_orders_must_be_day_or_gtc_and_carry_their_legs():
    b, clock = venue(start=et(DAY, "10:00"))
    with pytest.raises(BrokerError, match="bracket orders must be DAY or GTC"):
        b.submit(OrderRequest("A", "buy", 10, "market", "cls", order_class="bracket", take_profit=102, stop_loss=99))
    with pytest.raises(BrokerError, match="wrong legs"):
        b.submit(OrderRequest("A", "buy", 10, "market", "gtc", order_class="bracket", stop_loss=99))
    with pytest.raises(BrokerError, match="not modelled"):
        b.submit(OrderRequest("A", "buy", 10, "stop_limit", "day", limit_price=101, stop_price=100.5))


def test_gtc_orders_are_cancelled_90_days_after_creation_legs_included():
    later = "2026-12-14"  # 90 days after 15 Sep 2026
    frames = {"A": pd.concat([flat(DAY), flat(later)], ignore_index=True)}
    b, clock = venue(frames, calendar={DAY: ("09:30", "16:00"), later: ("09:30", "16:00")})
    held = bracket(b, coid="kb-1-1-1")
    resting = b.submit(OrderRequest("A", "buy", 5, "limit", "gtc", "kb-1-2-1", limit_price=50.0))
    assert held["expires_at"] == resting["expires_at"] == et(later, "16:15").isoformat()
    run(b, clock, 30, 31)
    assert [x["status"] for x in held["legs"]] == ["new", "new"]
    clock.set(et(later, "16:14"))
    assert resting in b.open_orders() and held["legs"][0]["status"] == "new"
    clock.set(et(later, "16:15"))
    assert b.open_orders() == [] and resting["status"] == "canceled"
    assert [x["status"] for x in held["legs"]] == ["canceled", "canceled"]
    assert b.positions()["A"]["qty"] == 100, "the position outlives its protective stop: re-arm before day 90"


def test_day_orders_and_day_legs_expire_at_the_close():
    b, clock = venue()
    o = b.submit(OrderRequest("A", "buy", 10, "market", "day", "kb-1-1-1", order_class="bracket", take_profit=105,
                              stop_loss=95))
    idle = b.submit(OrderRequest("A", "buy", 10, "limit", "day", limit_price=50.0))
    run(b, clock, 30, 31)
    assert o["status"] == "filled" and idle["expires_at"] == et(DAY, "16:00").isoformat()
    clock.set(et(DAY, "16:00"))
    assert b.clock()["is_open"] is False
    assert idle["status"] == "expired" and [x["status"] for x in o["legs"]] == ["expired", "expired"]
    assert "A" in b.positions(), "day legs leave the position unprotected overnight: the engine uses GTC"


def test_a_sell_needs_shares_that_no_other_order_holds():
    b, clock = venue(start=et(DAY, "15:30", 7))
    with pytest.raises(BrokerError, match="insufficient qty") as e:
        b.submit_moc_sell("moc-0", "A", 10)
    assert e.value.status == 403
    o = b.submit_oto_stop("kb-1-1-1", "A", 10, 95.0)
    b.advance(bar(360))
    with pytest.raises(BrokerError, match=r"requested: 10, available: 0"):
        b.submit_moc_sell("moc-1", "A", 10)  # the stop leg holds the shares
    b.cancel_order(o["legs"][0]["id"])  # what Engine._moc_exits does first
    moc = b.submit_moc_sell("moc-1", "A", 10)
    assert moc["status"] == "new" and moc["time_in_force"] == "cls"


def test_a_buy_needs_the_cash_and_the_account_follows_the_book():
    b, clock = venue(cash=5_000.0)
    with pytest.raises(BrokerError, match="insufficient buying power") as e:
        buy(b, 51)
    assert e.value.status == 403 and b.orders == {}
    buy(b, 40)
    b.advance(bar(30))
    a = b.account()
    assert float(a["cash"]) == 1_000.0 and float(a["buying_power"]) == 1_000.0 and float(a["equity"]) == 5_000.0
    assert SimBroker(BarHistory({}), clock, cash=None).account() == {"status": "ACTIVE"}


@pytest.mark.parametrize("kind", ["market", "limit"])
def test_cash_at_fill_still_checks_a_non_auction_buy_at_submission(kind):
    """Review of #164 (B1): `cash_at="fill"` moves only the auction (cls/opg) check to the print. A market or
    limit buy over the cash is refused at submission, as with "submit", so the cash book never goes negative."""
    b, clock = venue(cash=10_000.0, cash_at="fill")
    lim = {"limit_price": 101.0} if kind == "limit" else {}
    with pytest.raises(BrokerError, match="insufficient buying power") as e:
        b.submit(OrderRequest("A", "buy", 1000, kind, "day", **lim))
    assert e.value.status == 403 and b.orders == {}
    b.submit(OrderRequest("A", "buy", 90, kind, "day", **lim))
    run(b, clock, 30, 31)
    assert float(b.account()["cash"]) >= 0


def test_cash_at_fill_cuts_a_closing_auction_buy_to_the_cash_at_the_print():
    b, clock = venue(cash=10_000.0, cash_at="fill", start=et(DAY, "15:00", 7))
    o = b.submit(OrderRequest("A", "buy", 1000, "market", "cls"))  # accepted: checked at the print, not now
    run(b, clock, 330, 389)
    assert o["status"] == "canceled" and float(o["filled_qty"]) == 100.0
    assert float(b.account()["cash"]) >= 0


# ================================================================ scripting
def test_script_reject_then_normal():
    b, clock = venue()
    b.script.reject("A", status=422, message="stop price must be below the market")
    with pytest.raises(BrokerError, match="stop price") as e:
        bracket(b)
    assert e.value.status == 422 and b.orders == {} and b.order_by_client_id("kb-1-1-1") is None
    assert bracket(b)["status"] == "new"


def test_script_timeout_with_the_order_at_the_venue_and_without():
    b, clock = venue()
    b.script.timeout("A", accepted=True)
    with pytest.raises(BrokerError) as e:
        bracket(b, coid="kb-1-7-1")
    assert e.value.status == 0
    o = b.order_by_client_id("kb-1-7-1")
    assert o is not None and o["status"] == "new", "no answer, but the order is there: adopt it by its client id"
    assert bracket(b, coid="kb-1-7-1") is o, "a repeated submission returns the same order (adapter contract)"
    b.advance(bar(30))
    assert o["status"] == "filled"
    b.script.timeout("A", accepted=False)
    with pytest.raises(BrokerError):
        bracket(b, coid="kb-1-8-1")
    assert b.order_by_client_id("kb-1-8-1") is None


def test_script_hold_delays_or_blocks_a_fill():
    b, clock = venue()
    b.script.hold("A", bars=2)
    o = buy(b)
    run(b, clock, 30, 31)
    assert o["status"] == "new"
    run(b, clock, 32, 32)
    assert o["status"] == "filled"
    b.script.hold("A")  # a venue that never answers
    stuck = buy(b)
    run(b, clock, 33, 60)
    assert stuck["status"] == "new"
    b.cancel(stuck["id"])
    assert stuck["status"] == "canceled" and b.open_orders() == []


def test_the_test_fake_raises_on_a_duplicate_client_order_id():
    clock = Clock(et(DAY, "10:00"))
    fake = FakeBroker(FakeData({"A": flat(DAY)}, clock), clock, DAY)
    fake.submit_bracket("kb-1-1-1", "A", 1, 90.0, 110.0)
    with pytest.raises(ValueError, match="client_order_id"):
        fake.submit_bracket("kb-1-1-1", "A", 1, 90.0, 110.0)


# ================================================================ cancel, replace, close
def test_cancel_replace_and_close_position():
    b, clock = venue()
    pending = bracket(b, coid="kb-1-1-1")
    b.cancel_order(pending["id"])
    assert pending["status"] == "canceled" and {x["status"] for x in pending["legs"]} == {"canceled"}
    b.cancel("nope")  # already gone or unknown: not an error
    o = bracket(b, coid="kb-1-2-1")
    with pytest.raises(BrokerError) as e:
        b.replace("o99s", stop_price=99.5)
    assert e.value.status == 404
    run(b, clock, 30, 30)
    stop, tgt = o["legs"]
    assert b.replace_stop(stop["id"], 99.456) is stop and stop["stop_price"] == "99.46"
    assert b.replace_limit(tgt["id"], 103.0)["limit_price"] == "103.00" and b.order(tgt["id"]) is tgt
    assert b.open_orders() == [], "a filled entry is not an open order; its legs are nested under it"
    sell = b.close_position("A")
    assert {x["status"] for x in o["legs"]} == {"canceled"} and sell["side"] == "sell" and sell["qty"] == 100
    with pytest.raises(BrokerError, match="not open"):
        b.replace_stop(stop["id"], 99.0)
    run(b, clock, 31, 31)
    assert sell["status"] == "filled" and b.positions() == {} and b.close_position("A") is None
    again = bracket(b, coid="kb-1-3-1")
    run(b, clock, 32, 32)
    b.cancel(again["legs"][1]["id"])
    assert {x["status"] for x in again["legs"]} == {"canceled"}, "cancelling one bracket leg cancels the other"
    with pytest.raises(BrokerError) as e:
        b.order("o99")
    assert e.value.status == 404


def test_activities_are_the_fills_in_order():
    b, clock = venue({"A": flat(DAY, edits={30: {"h": 100.0}})})
    bracket(b, stop=99.0, target=100.03)
    run(b, clock, 30, 31)
    rows = b.activities()
    assert [(r["side"], r["type"], r["order_id"]) for r in rows] == [("buy", "fill", "o1"), ("sell", "fill", "o1t")]
    assert all(r["activity_type"] == "FILL" and r["symbol"] == "A" for r in rows)
    assert b.activities(after=bar(30)) == rows[1:] and b.activities(after=bar(31)) == []


# ================================================================ clock and calendar
def test_clock_and_calendar_follow_the_stored_sessions():
    frames = {"A": pd.concat([flat("2026-09-11"), flat("2026-09-14")], ignore_index=True)}  # Friday, Monday
    b, clock = venue(frames, start=et("2026-09-11", "09:00"))
    assert b.calendar("2026-09-11") == {"date": "2026-09-11", "open": "09:30", "close": "16:00"}
    assert b.calendar("2026-09-12") is None and b.calendar("2026-09-15") is None
    c = b.clock()
    assert c["is_open"] is False and pd.Timestamp(c["next_open"]) == pd.Timestamp(et("2026-09-11", "09:30"))
    assert pd.Timestamp(c["next_close"]) == pd.Timestamp(et("2026-09-11", "16:00"))
    clock.set(et("2026-09-11", "12:00"))
    c = b.clock()
    assert c["is_open"] is True and pd.Timestamp(c["next_open"]) == pd.Timestamp(et("2026-09-14", "09:30"))
    assert pd.Timestamp(c["next_close"]) == pd.Timestamp(et("2026-09-11", "16:00"))
    clock.set(et("2026-09-11", "16:00"))
    c = b.clock()
    assert c["is_open"] is False and pd.Timestamp(c["next_close"]) == pd.Timestamp(et("2026-09-14", "16:00"))
    clock.set(et("2026-09-14", "17:00"))  # past the last stored session: still a usable answer
    c = b.clock()
    assert c["is_open"] is False and pd.Timestamp(c["next_open"]) == pd.Timestamp(et("2026-09-15", "09:30"))


def test_an_early_close_moves_the_closing_auction():
    frames = {"A": flat(DAY, edits={209: {"c": 100.2}})}
    b, clock = venue(frames, start=et(DAY, "09:31"), calendar={DAY: ("09:30", "13:00")})
    assert b.calendar(DAY)["close"] == "13:00"
    buy(b, 10)
    b.advance(bar(1))
    clock.set(et(DAY, "12:40"))
    moc = b.submit_moc_sell("moc-1", "A", 10)
    for m in (208, 209):
        b.advance(bar(m))
    assert moc["status"] == "filled" and float(moc["filled_avg_price"]) == 100.2
    clock.set(et(DAY, "13:00"))
    assert b.clock()["is_open"] is False


def test_sim_clock_only_moves_forward():
    c = SimClock(datetime(2026, 9, 15, 13, 30))
    assert c().tzinfo is UTC and c.advance(minutes=1) == datetime(2026, 9, 15, 13, 31, tzinfo=UTC)
    with pytest.raises(ValueError, match="cannot go back"):
        c.set(datetime(2026, 9, 15, 13, 30, tzinfo=UTC))
