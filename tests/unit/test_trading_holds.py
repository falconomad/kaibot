"""M10 1.21 (#446) step 4: every entry block is a hold on the one trading switch, and `entry_block` reads the switch.
The first trigger of `halted`: the lost engine lease, where the lease's fence already refuses every order call.

Product's ruling of 25 Nov: no owner "halted" switch. Nothing here may change what reaches the venue, so the tests
compare with the derivation `entry_block` had before step 4 (`_before`, copied from main at 0f9c249): the same code,
the same words, the same state, the daily-data check asked exactly as often, on any sequence of conditions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kaibot.broker.protocol import OrderRequest
from kaibot.engine import order_path
from kaibot.engine.core import Engine
from kaibot.engine.fence import Fenced, FencedBroker
from kaibot.engine.state import ENTRY_SOURCES, HALT, LEASE_LOST, Trading, TradingState, from_entry_block


# ---------------------------------------------------------------- the derivation before step 4 (main @ 0f9c249)
def _before(e) -> tuple[str, str] | None:
    d = e.day
    if e.run is None or e.run.get("status") != "running":
        st_ = (e.run or {}).get("status")
        return ("no_run" if e.run is None else "paused" if st_ == "paused" else "run_not_running", "no running run")
    if e._cmd_waiting:
        return "command_selling", next(iter(e._cmd_waiting.values()))
    if not e.account_clean:
        return "account_not_clean", "account not clean"
    if not e._reconciled() and e.recon.blocks_every_buy():
        return "reconcile_difference", "ledger and broker differ (reconciliation)"
    if e.kill:
        return "halted", f"halted: {e.kill}"
    if not e.lease_ok():
        return "lease_unverified", "lease unverified"
    if e.clock_skew is not None:
        return "clock_skew", e._clock_block()
    if d is not None and d.halted:
        return "daily_stop", "daily stop"
    if d is not None and d.refused:
        return "config_refused", f"config refused: {d.refused}"
    fn = getattr(getattr(e, "daily", None), "data_block", None)
    if fn is not None and getattr(e.daily, "entry_checks", None) is not None:
        try:
            why = fn()
        except Exception as x:  # noqa: BLE001
            return "data_check", f"data check checks_failed: {type(x).__name__}"
        if why:
            return "data_check", why
    return None


def _state_before(e, why) -> Trading:
    held = e.trading.state()
    if held.state is TradingState.HALTED:
        return held
    derived = from_entry_block(why[1] if why else None)
    return derived if derived.state is not TradingState.ACTIVE else held


class Recon:
    def __init__(self):
        self.diffs, self.every = [], False
        self.last = SimpleNamespace(diffs=self.diffs)

    def blocks_every_buy(self):
        return self.every


class Daily:
    def __init__(self):
        self.entry_checks, self.text, self.boom, self.asked = None, None, False, 0

    def data_block(self):
        self.asked += 1
        if self.boom:
            raise RuntimeError("x")
        return self.text


class Lease:
    def __init__(self):
        self.ok, self.lost = True, False

    def fence(self, reducing):
        from kaibot.engine.lease import LeaseLost

        if self.lost:
            raise LeaseLost("The engine lease is no longer ours: no order is sent.")
        return None


def _engine() -> Engine:
    e = Engine.__new__(Engine)  # only what entry_block and the switch read
    e.run, e._cmd_waiting, e.account_clean, e.recon = {"status": "running"}, {}, True, Recon()
    e.lease, e.clock_skew, e.day, e.daily = None, None, None, Daily()
    return e


CONDITIONS = st.fixed_dictionaries({
    "run": st.sampled_from([None, "running", "running", "paused", "stopped", "scheduled"]),
    "cmd": st.sampled_from([None, "flatten and stop: selling the book"]),
    "clean": st.booleans(), "diff": st.booleans(), "every": st.booleans(),
    "kill": st.sampled_from([None, None, "drift", "broker_rejects", "engine_errors"]),
    "lease": st.sampled_from(["none", "ok", "unverified", "lost", "lost_unfenced"]),
    "skew": st.sampled_from([None, {"state": "unreadable"}, {"state": "skewed", "seconds": -7.0}]),
    "day": st.sampled_from([None, "plain", "halted", "refused", "halted_refused"]),
    "checks": st.booleans(), "text": st.sampled_from([None, "data check benchmark_bar_missing SPY: late"]),
    "boom": st.booleans(),
})


def _apply(e: Engine, c: dict, twin: Engine | None = None) -> None:
    for x in (e, twin) if twin is not None else (e,):
        x.run = None if c["run"] is None else {"status": c["run"]}
        x._cmd_waiting = {"c1": c["cmd"]} if c["cmd"] else {}
        x.account_clean = c["clean"]
        x.recon.diffs[:] = ["B"] if c["diff"] else []
        x.recon.every = c["every"]
        x.kill = c["kill"]
        x.clock_skew = c["skew"]
        x.day = None if c["day"] is None else SimpleNamespace(halted="halted" in c["day"],
                                                              refused="owner config unreadable"
                                                              if "refused" in c["day"] else None)
        x.daily.entry_checks = object() if c["checks"] else None
        x.daily.text, x.daily.boom = c["text"], c["boom"]
        if c["lease"] == "none":
            x.lease, x.broker = None, None
        else:
            x.lease = x.lease if isinstance(x.lease, Lease) else Lease()
            x.lease.ok = c["lease"] == "ok"
            x.lease.lost = c["lease"].startswith("lost")
            x.broker = FencedBroker(object(), x.lease if c["lease"] != "lost_unfenced" else None)


@settings(max_examples=400, deadline=None)
@given(steps=st.lists(CONDITIONS, min_size=1, max_size=8))
def test_entry_block_from_the_switch_is_the_derivation_it_replaced(steps):
    """On any sequence of conditions: the same code and words, the same trading state (but `halted` exactly while
    the lease is lost behind its fence), and the daily-data check (which journals) asked exactly as often."""
    e, ref = _engine(), _engine()
    for c in steps:
        _apply(e, c, ref)
        want = _before(ref)
        assert e.entry_reason() == want
        assert e.entry_block() == (want[1] if want else None)
        lost = c["lease"] == "lost"
        got = e.trading_state()
        if lost:
            assert got.state is TradingState.HALTED and e.trading.held(LEASE_LOST)
        else:
            assert got == _state_before(ref, _before(ref)) and e.trading.held(LEASE_LOST) is None
        # the switch holds the first block only, under its source; the halt is its trigger's own hold (held exactly
        # while `kill` is set, whatever blocks before it)
        held = [s for s in ENTRY_SOURCES if s != HALT and e.trading.held(s) is not None]
        assert (e.trading.held(HALT) is not None) == bool(c["kill"])
        if want is None or want[0] == "halted":
            assert held == []
        else:
            assert len(held) == 1 and e.trading.held(held[0]) == want[1]


@settings(max_examples=300, deadline=None)
@given(c=CONDITIONS)
def test_the_daily_data_check_is_asked_exactly_as_before(c):
    e, ref = _engine(), _engine()
    _apply(e, c, ref)
    for _ in range(3):
        e.entry_reason()
        e.trading_state()
        _before(ref)
        _before(ref)  # trading_state asks entry_block once
    assert e.daily.asked == ref.daily.asked


def test_a_duck_typed_engine_still_works():
    """`Engine.entry_block(obj)` on a plain object (tests/unit/test_entry_checks.py), its `kill` a plain attribute."""
    daily = SimpleNamespace(entry_checks=object(), data_block=lambda: "data check benchmark_bar_missing SPY: x")
    eng = SimpleNamespace(day=None, run={"status": "running"}, _cmd_waiting={}, account_clean=True,
                          _reconciled=lambda: True, kill=None, lease_ok=lambda: True, clock_skew=None, daily=daily)
    assert Engine.entry_block(eng) == "data check benchmark_bar_missing SPY: x"
    eng.kill = "drift"
    assert Engine.entry_reason(eng) == ("halted", "halted: drift")
    eng.kill = None
    assert Engine.entry_block(eng) == "data check benchmark_bar_missing SPY: x"
    eng.daily = None
    assert Engine.entry_block(eng) is None


def test_pause_daily_stop_and_data_check_are_holds_and_let_go():
    e = _engine()
    e.run = {"status": "paused"}
    assert e.entry_reason() == ("paused", "no running run") and e.trading.held("run") == "no running run"
    e.run = {"status": "running"}
    e.day = SimpleNamespace(halted=True, refused=None)
    assert e.entry_reason() == ("daily_stop", "daily stop") and e.trading.held("run") is None
    assert e.trading.held("daily_stop") == "daily stop"
    e.day.halted = False
    e.daily.entry_checks, e.daily.text = object(), "data check universe_stale: old"
    assert e.entry_reason() == ("data_check", "data check universe_stale: old")
    assert e.trading.held("daily_stop") is None and e.trading.held("data_check")
    e.daily.text = None
    assert e.entry_reason() is None and e.trading.state() == Trading(TradingState.ACTIVE)


# ---------------------------------------------------------------- the first `halted` trigger: the lost lease
class Venue:
    def __init__(self):
        self.calls: list[str] = []

    def submit(self, order):
        self.calls.append(f"submit:{order.side}")
        return {"id": "o1", "status": "new"}

    def replace(self, order_id, **kw):
        self.calls.append("replace")
        return {"id": order_id}

    def cancel(self, order_id):
        self.calls.append("cancel")

    def close_position(self, symbol):
        self.calls.append("close")

    def order(self, order_id):
        return {"id": order_id, "side": "sell"}


SELL = OrderRequest("B", "sell", 10, "market", "day", "kb1-x-B-20260803-exit-1")
BUY = OrderRequest("B", "buy", 10, "market", "day", "kb1-x-B-20260803-target-1")


def _calls(e, broker):
    out = []
    for name, call in (("buy", lambda: order_path.submit(broker, BUY, gate=e.trading_state)),
                       ("sell", lambda: order_path.submit(broker, SELL, gate=e.trading_state)),
                       ("stop", lambda: order_path.replace_stop(broker, "stop1", 9.0, gate=e.trading_state)),
                       ("close", lambda: order_path.close_position(broker, "B", gate=e.trading_state)),
                       ("cancel", lambda: order_path.cancel(broker, "o1"))):
        try:
            call()
            out.append((name, "sent"))
        except order_path.NotAllowed:
            out.append((name, "gate"))
        except Fenced:
            out.append((name, "fence"))
    return out


def test_a_lost_lease_halts_and_the_venue_sees_exactly_what_it_saw_before():
    """Before step 4 the fence refused every call of an engine whose lease was lost, cancels included. Now the
    state is `halted`: the gate refuses all but the cancel, which the fence still refuses. Nothing reaches the
    venue either way."""
    v = Venue()
    lease = Lease()
    e = _engine()
    e.lease, e.broker = lease, FencedBroker(v, lease)
    assert e.trading_state().state is TradingState.ACTIVE
    assert [r for _, r in _calls(e, e.broker)] == ["sent"] * 5
    v.calls.clear()
    lease.ok, lease.lost = False, True
    assert e.trading_state() == Trading(TradingState.HALTED, "the engine lease is no longer ours: no order is sent")
    assert e.entry_block() == "lease unverified"  # its words unchanged (the heartbeat's state says halted)
    assert _calls(e, e.broker) == [("buy", "gate"), ("sell", "gate"), ("stop", "gate"), ("close", "gate"),
                                   ("cancel", "fence")]
    assert v.calls == []
    # before step 4 the state was reducing_only ("lease unverified"): the fence refused every call itself
    old_gate = SimpleNamespace(trading_state=lambda: Trading(TradingState.REDUCING_ONLY, "lease unverified"))
    assert _calls(old_gate, e.broker) == [("buy", "gate"), ("sell", "fence"), ("stop", "fence"),
                                          ("close", "fence"), ("cancel", "fence")]
    assert v.calls == []


@pytest.mark.parametrize("how", ["no fence", "another lease", "not lost"])
def test_no_halt_where_the_fence_would_not_refuse(how):
    """Only where the fence refuses everything today: a lease not behind this engine's fence, or not lost, is no
    `halted` hold (the lease block stays reducing only: sales and stops go)."""
    lease = Lease()
    lease.ok, lease.lost = False, how != "not lost"
    e = _engine()
    e.lease = lease
    e.broker = Venue() if how == "no fence" else FencedBroker(Venue(), Lease() if how == "another lease" else lease)
    assert e.trading_state() == Trading(TradingState.REDUCING_ONLY, "lease unverified")
    assert e.trading.held(LEASE_LOST) is None


def test_the_halt_lets_go_when_the_lease_is_ours_again():
    lease = Lease()
    e = _engine()
    e.lease, e.broker = lease, FencedBroker(Venue(), lease)
    lease.ok, lease.lost = False, True
    assert e.trading_state().state is TradingState.HALTED
    lease.ok, lease.lost = True, False  # EngineLease.acquire
    assert e.trading_state() == Trading(TradingState.ACTIVE) and e.trading.held(LEASE_LOST) is None


def test_the_market_filter_is_not_a_hold():
    """It holds back one rule's carried buys, not every buy: the state stays active, the heartbeat reports it."""
    e = _engine()
    e.daily.market_filter_off = lambda: [("trend_momentum", "2026-11-24")]
    d = e.trading_detail()
    assert d["trading_state"] == "active" and d["entry_block"] == "market_filter_off"
    assert e.trading.state() == Trading(TradingState.ACTIVE)
