"""M10 1.21 (#446), step 3: the trading state's gate at the order seam (engine/order_path.py), the engine's one
trading state (switch and entry blocks together), and what the heartbeat carries about it (`trading_state`,
`entry_block` and its words, #604 / #626's follow-up).

The issue's check, "no buy leaves in reducing, only cancels in halted", as a property of the seam: whatever the
state, a call reaches the broker exactly when `state.allows` lets it."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine import order_path
from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal
from kaibot.engine.orders import OrderManager, St
from kaibot.engine.state import BLOCK_CODES, Trading, TradingState, allows


class Venue:
    """Records every call that reached it."""

    def __init__(self):
        self.calls: list[str] = []

    def _rec(self, name):
        self.calls.append(name)
        return {"id": f"o{len(self.calls)}", "status": "new", "client_order_id": "c", "filled_qty": "0"}

    def submit(self, order):
        return self._rec(f"submit:{order.side}")

    def submit_bracket(self, *a):
        return self._rec("submit_bracket")

    def submit_oto_stop(self, *a):
        return self._rec("submit_oto_stop")

    def submit_moc_sell(self, *a):
        return self._rec("submit_moc_sell")

    def close_position(self, *a):
        return self._rec("close_position")

    def replace(self, *a, **kw):
        return self._rec("replace")

    def replace_stop(self, *a):
        return self._rec("replace_stop")

    def replace_limit(self, *a):
        return self._rec("replace_limit")

    def cancel(self, *a):
        self._rec("cancel")

    def cancel_order(self, *a):
        self._rec("cancel_order")


BUY = OrderRequest("A", "buy", 3, "market", "day", "kb1-r-A-20261124-buy-1")
SELL = OrderRequest("A", "sell", 3, "market", "day", "kb1-r-A-20261124-sell-1")
# (the call, what it is to the state: action and side)
CALLS = {
    "submit:buy": (lambda b: order_path.submit(b, BUY), "submit", "buy"),
    "submit:sell": (lambda b: order_path.submit(b, SELL), "submit", "sell"),
    "submit_bracket": (lambda b: order_path.submit_bracket(b, "c", "A", 3, 9.0, 11.0), "submit", "buy"),
    "submit_oto_stop": (lambda b: order_path.submit_oto_stop(b, "c", "A", 3, 9.0), "submit", "buy"),
    "submit_moc_sell": (lambda b: order_path.submit_moc_sell(b, "c", "A", 3), "submit", "sell"),
    "close_position": (lambda b: order_path.close_position(b, "A"), "submit", "sell"),
    "replace": (lambda b: order_path.replace(b, "o1", stop_price=9.5), "replace", None),
    "replace_stop": (lambda b: order_path.replace_stop(b, "o1", 9.5), "replace", None),
    "replace_limit": (lambda b: order_path.replace_limit(b, "o1", 10.5), "replace", None),
    "cancel": (lambda b: order_path.cancel(b, "o1"), "cancel", None),
    "cancel_order": (lambda b: order_path.cancel_order(b, "o1"), "cancel", None),
}


@given(st.sampled_from(list(TradingState)), st.sampled_from(sorted(CALLS)))
def test_a_call_reaches_the_venue_exactly_when_the_state_allows_it(state, name):
    call, action, side = CALLS[name]
    b = Venue()
    order_path.attach(b, lambda: Trading(state, "test"))
    if allows(state, action, side):
        call(b)
        assert b.calls == [name]
    else:
        with pytest.raises(order_path.NotAllowed) as e:
            call(b)
        assert b.calls == []  # nothing reached the venue
        assert e.value.status == 403 and isinstance(e.value, BrokerError) and state.value in str(e.value)


def test_no_buy_leaves_in_reducing_only_and_only_cancels_in_halted():
    for state, allowed in [(TradingState.REDUCING_ONLY, {"submit:sell", "submit_moc_sell", "close_position", "replace",
                                                          "replace_stop", "replace_limit", "cancel", "cancel_order"}),
                           (TradingState.HALTED, {"cancel", "cancel_order"}),
                           (TradingState.ACTIVE, set(CALLS))]:
        b = Venue()
        order_path.attach(b, lambda state=state: Trading(state, "x"))
        for call, _, _ in CALLS.values():
            with contextlib.suppress(order_path.NotAllowed):
                call(b)
        assert set(b.calls) == allowed, state


def test_an_unreadable_state_refuses_buys_only():
    b = Venue()

    def boom():
        raise RuntimeError("no state")

    order_path.attach(b, boom)
    for call, _, _ in CALLS.values():
        try:
            call(b)
        except order_path.NotAllowed as e:
            assert "could not be read" in str(e)
    assert "submit:buy" not in b.calls and "submit_bracket" not in b.calls and "submit_oto_stop" not in b.calls
    assert {"submit:sell", "close_position", "replace_stop", "cancel", "cancel_order"} <= set(b.calls)


def test_a_broker_without_an_engine_is_not_gated():
    b = Venue()
    for call, _, _ in CALLS.values():
        call(b)
    assert len(b.calls) == len(CALLS)

    class Slotted:  # no __dict__: attach is a no-op, the call goes through as before
        __slots__ = ()

        def submit(self, order):
            return {"id": "s"}

    s = Slotted()
    order_path.attach(s, lambda: Trading(TradingState.HALTED, "x"))
    assert order_path.submit(s, BUY) == {"id": "s"}


def test_a_later_engine_replaces_the_gate():
    b = Venue()
    order_path.attach(b, lambda: Trading(TradingState.HALTED, "old"))
    order_path.attach(b, lambda: Trading(TradingState.ACTIVE))
    order_path.submit(b, BUY)
    assert b.calls == ["submit:buy"]


def test_the_order_manager_books_a_refused_buy_as_rejected_never_unknown():
    """NotAllowed is a 403: the order manager marks the order rejected (no order at the venue) and never looks for
    it there, as for the lease's fence."""
    b = Venue()
    order_path.attach(b, lambda: Trading(TradingState.REDUCING_ONLY, "daily stop"))
    om = OrderManager(b, Journal(), lambda: datetime(2026, 11, 24, 20, 50, tzinfo=UTC), run_id=1)
    r = om.submit(BUY, strategy="trend_momentum", purpose="buy", session="2026-11-24")
    assert r.state is St.REJECTED and "daily stop" in (r.reason or "") and b.calls == []
    s = om.submit(SELL, strategy="trend_momentum", purpose="sell", session="2026-11-24")
    assert s.state is not St.REJECTED and b.calls == ["submit:sell"]


# ---------------------------------------------------------------- the engine's one state and its heartbeat lines


def _engine(**kw) -> Engine:
    e = Engine.__new__(Engine)  # only the attributes entry_block and the switch need
    e.run, e._cmd_waiting, e.account_clean = {"status": "running"}, {}, True
    e.recon = type("R", (), {"last": type("L", (), {"diffs": []})(), "blocks_every_buy": lambda self: False})()
    e.lease, e.clock_skew, e.day, e.daily = None, None, None, None
    e.__dict__.update(kw)
    return e


def test_a_halted_hold_wins_over_every_entry_block_and_a_block_is_reducing_only():
    e = _engine()
    assert e.trading_state() == Trading(TradingState.ACTIVE)
    e.kill = "drift"
    assert e.trading_state() == Trading(TradingState.REDUCING_ONLY, "halted: drift")
    e.trading.hold("test", TradingState.HALTED, "frozen")
    e.account_clean = False
    assert e.trading_state() == Trading(TradingState.HALTED, "frozen")
    e.trading.release("test")
    assert e.trading_state() == Trading(TradingState.REDUCING_ONLY, "account not clean")


def test_the_engine_gates_its_own_broker():
    b = Venue()
    e = _engine(broker=b)
    e._gate_orders()
    e.kill = "broker_rejects"
    with pytest.raises(order_path.NotAllowed, match="halted: broker_rejects"):
        order_path.submit(b, BUY)
    order_path.submit(b, SELL)
    e.kill = None
    order_path.submit(b, BUY)
    assert b.calls == ["submit:sell", "submit:buy"]


def test_engine_init_attaches_the_gate():
    b = Venue()
    Engine(b, None, None, {}, set(), journal=Journal())
    assert callable(vars(b)[order_path._GATE])


class Day:
    halted, refused = False, None


class DataChecks:  # M12 3.11: the daily path with KAIBOT_DAILY_CHECKS_BLOCK on and a market block
    entry_checks = object()

    def data_block(self):
        return "data check spy_late: SPY's bar is late"

    def market_filter_off(self):
        return []


@pytest.mark.parametrize("setup, code, words", [
    (lambda e: None, None, None),
    (lambda e: setattr(e, "run", None), "no_run", "no running run"),
    (lambda e: setattr(e, "run", {"status": "paused"}), "paused", "no running run"),
    (lambda e: setattr(e, "run", {"status": "scheduled"}), "run_not_running", "no running run"),
    (lambda e: e._cmd_waiting.update(c1="flatten and stop pending"), "command_selling", "flatten and stop pending"),
    (lambda e: setattr(e, "account_clean", False), "account_not_clean", "account not clean"),
    (lambda e: setattr(e, "kill", "drift"), "halted", "halted: drift"),
    (lambda e: setattr(e, "lease", type("L", (), {"ok": False})()), "lease_unverified", "lease unverified"),
    (lambda e: setattr(e, "clock_skew", {"state": "unreadable"}), "clock_skew",
     "clock skew: the broker's clock gave no timestamp to compare with"),
    (lambda e: setattr(e, "day", type("D", (Day,), {"halted": True})()), "daily_stop", "daily stop"),
    (lambda e: setattr(e, "day", type("D", (Day,), {"refused": "bad"})()), "config_refused", "config refused: bad"),
    (lambda e: setattr(e, "daily", DataChecks()), "data_check", "data check spy_late: SPY's bar is late"),
])
def test_each_entry_block_has_its_code_and_its_words_unchanged(setup, code, words):
    e = _engine()
    setup(e)
    assert e.entry_block() == words
    assert (e.entry_reason() or (None, None)) == (code, words)
    d = e.trading_detail()
    assert d == {"trading_state": "active" if code is None else "reducing_only", "entry_block": code,
                 "entry_block_why": words}
    assert code is None or code in BLOCK_CODES


def test_the_reconciliation_block_has_its_code():
    e = _engine()
    e.recon = type("R", (), {"last": type("L", (), {"diffs": ["x"]})(), "blocks_every_buy": lambda self: True})()
    assert e.entry_reason() == ("reconcile_difference", "ledger and broker differ (reconciliation)")


class Daily:
    def __init__(self, hit):
        self.hit = hit

    def market_filter_off(self):
        if isinstance(self.hit, Exception):
            raise self.hit
        return self.hit


def test_the_market_filter_is_reported_only_when_nothing_in_the_engine_blocks():
    e = _engine(daily=Daily([("trend_momentum", "2026-11-24")]))
    d = e.trading_detail()
    assert d["trading_state"] == "active" and d["entry_block"] == "market_filter_off"  # the engine itself allows
    assert "trend_momentum" in d["entry_block_why"] and "2026-11-24" in d["entry_block_why"]
    e.account_clean = False  # an engine block is the reason first
    assert e.trading_detail()["entry_block"] == "account_not_clean"
    e = _engine(daily=Daily([]))
    assert e.trading_detail() == {"trading_state": "active", "entry_block": None, "entry_block_why": None}


def test_an_unreadable_part_is_left_out_not_guessed():
    e = _engine(daily=Daily(RuntimeError("journal")))
    d = e.trading_detail()
    assert d == {"trading_state": "active"}  # no entry_block key: neither null ("nothing blocks") nor a guess


def test_the_heartbeat_carries_the_state_and_the_entry_block(monkeypatch):
    from kaibot.engine import __main__ as entry

    rows = []

    class Store:
        def heartbeat(self, process, detail):
            rows.append(detail)

    class Lease:
        degraded = False

        def renew(self):
            pass

        def status(self):
            return "ok"

    e = _engine()
    e.store, e.process = Store(), "engine"
    beat = entry.make_beat(e, Lease(), "host", {})
    beat({"state": "managing"})
    e.kill = "drift"
    beat({"state": "managing"})
    assert [(r["trading_state"], r["entry_block"], r["entry_block_why"]) for r in rows] == [
        ("active", None, None), ("reducing_only", "halted", "halted: drift")]
    assert rows[0]["state"] == "managing" and rows[0]["lease"] == "ok"
    monkeypatch.setattr(Engine, "trading_detail", lambda self: 1 / 0)  # a failure never stops the beat
    beat({"state": "managing"})
    assert len(rows) == 3 and "trading_state" not in rows[2]
