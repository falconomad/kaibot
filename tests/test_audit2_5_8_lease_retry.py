"""Engine audit 2, 5.8: a failed lease renewal no longer drops a buy for the day.

Before: the fence refused a buy when the renewal before it failed ("lease not proved (renewal failed)"), the order
manager marked the order rejected, and the daily path never sent that symbol again that session (the venue's "no").
Now (engine/lease.py `fence`, engine/fence.py `unproved`, engine/live_daily.py `send`):
- a renewal that failed quickly is tried once more before the entry is refused;
- a buy the fence refused for a failed renewal never reached the venue: it is sent again at the next tick of the
  window under a new attempt, like an order called lost. A refusal for a LOST lease stays a refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "dst"))

import world  # noqa: E402
from fakes import FakeStore  # noqa: E402
from scenarios import quiet  # noqa: E402
from test_engine_lease import Time  # noqa: E402
from world import D1, NY, World, et  # noqa: E402

from kaibot.broker.protocol import OrderRequest  # noqa: E402
from kaibot.engine import lease as lease_mod  # noqa: E402
from kaibot.engine.core import Engine  # noqa: E402
from kaibot.engine.fence import Fenced, FencedBroker, unproved  # noqa: E402
from kaibot.engine.lease import NOT_PROVED, EngineLease, machine_key  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402


@pytest.fixture(params=["book_loop", "intraday"])
def engine_kind(request, monkeypatch):
    """The DST world builds the intraday engine; the live process runs `Engine(intraday=False)` (book_loop.py).
    The daily path under test is the same object in both: each engine-level test runs on both."""
    if request.param == "book_loop":
        none = {level: [] for level in load_profiles()}
        monkeypatch.setattr(world, "Engine", lambda *a, **k: Engine(*a, intraday=False, strategies=none, **k))
    return request.param


class Venue:
    def __init__(self):
        self.calls = []

    def submit(self, order):
        self.calls.append((order.side, order.symbol))
        return {"id": f"o{len(self.calls)}", "client_order_id": order.client_order_id, "status": "accepted"}


def buy():
    return OrderRequest("B", "buy", 10, "market", "day", "kb1-x-B-20260803-buy-1")


class Flaky(FakeStore):
    """Supabase whose next `fails` lease renewals raise; each takes `slow` seconds of the lease's clocks."""

    def __init__(self, t, fails=0, slow=0.0):
        super().__init__()
        self.t, self.fails, self.slow, self.renews = t, fails, slow, 0
        self.lease_now = t.db

    def lease_renew(self, holder, ttl_s, machine="", epoch=None):
        self.renews += 1
        if self.fails > 0:
            self.fails -= 1
            self.t.s += self.slow
            raise ConnectionError("supabase: 503")
        return super().lease_renew(holder, ttl_s, machine, epoch)


def lease_on(store, t):
    lease = EngineLease(store, "H", "mac", mono=t.mono, wall=t.wall, machine=machine_key("mac", "UUID-A"))
    lease.acquire()
    return lease


# ---------------------------------------------------------------- the fence: one more try
def test_a_renewal_that_failed_once_is_tried_again_and_the_buy_goes_out():
    t = Time()
    store = Flaky(t, fails=1)
    fb = FencedBroker(Venue(), lease_on(store, t))
    fb.submit(buy())
    assert store.renews == 2 and fb.inner.calls == [("buy", "B")] and fb.lease.ok and fb.refused == 0


def test_a_renewal_that_fails_twice_refuses_the_entry_and_the_refusal_is_told_apart():
    t = Time()
    store = Flaky(t, fails=5)
    fb = FencedBroker(Venue(), lease_on(store, t))
    with pytest.raises(Fenced) as e:
        fb.submit(buy())
    assert store.renews == 2 and fb.inner.calls == []  # one more try, not more
    assert NOT_PROVED in str(e.value) and unproved(f"{e.value.status}: {e.value.body}")
    assert not unproved("403: fenced: the engine lease is no longer ours")  # a lost lease is a refusal
    assert not unproved("403: insufficient qty available") and not unproved(None)


def test_a_renewal_that_failed_slowly_is_not_tried_again():
    """The bound: a first try that took longer than FENCE_RETRY_WITHIN_S (a timeout) is not repeated."""
    t = Time()
    store = Flaky(t, fails=1, slow=lease_mod.FENCE_RETRY_WITHIN_S + 1)
    fb = FencedBroker(Venue(), lease_on(store, t))
    with pytest.raises(Fenced):
        fb.submit(buy())
    assert store.renews == 1 and fb.inner.calls == []


def test_a_sale_is_not_held_by_the_retry_either():
    t = Time()
    store = Flaky(t, fails=5)
    fb = FencedBroker(Venue(), lease_on(store, t))
    fb.submit(OrderRequest("B", "sell", 10, "market", "day", "kb1-x-B-20260803-sell-1"))
    assert fb.inner.calls == [("sell", "B")] and store.renews == 1  # out at once: no second try before a sale


# ---------------------------------------------------------------- the engine: the buy goes out once at 15:52
def run_fenced(fail_from: str, fail_until: str):
    """The quiet scenario (one buy of B on D1) with the engine's broker behind the lease's fence (as
    engine/__main__.py builds it) and every lease renewal failing from `fail_from` to `fail_until` ET on D1."""
    w = World(quiet({D1: ("B",)}))
    clock, replica = w.clock, w.replica
    lo, hi = et(D1, fail_from), et(D1, fail_until)
    renew = replica.lease_renew
    tries = []

    def lease_renew(*a, **kw):
        now = clock()
        tries.append(now)
        if lo <= now < hi:
            raise ConnectionError("supabase: 503")
        return renew(*a, **kw)

    replica.lease_renew = lease_renew
    lease = EngineLease(replica, "H", "dst", mono=lambda: clock().timestamp(), wall=lambda: clock().timestamp(),
                        machine=machine_key("dst", "UUID-DST"))
    lease.acquire()
    w.broker = FencedBroker(w.broker, lease)
    res = w.run()
    return res, w, [x for x in tries if lo <= x < hi]


def _orders(res, coid_part):
    return [t for t in res.trace if " om_intent " in t and coid_part in t]


def test_a_renewal_failure_through_15_51_sends_the_buy_once_at_15_52(engine_kind):
    res, w, failed = run_fenced("15:50", "15:52")
    assert res.ok, res.report()
    assert w.eng.intraday is (engine_kind == "intraday")
    assert failed and all(x.astimezone(NY).strftime("%H:%M") in ("15:50", "15:51") for x in failed)
    intents = _orders(res, "-B-20260803-buy-")
    assert [t[6:11] for t in intents] == ["15:50", "15:51", "15:52"], intents  # two refused attempts, then the buy
    states = [t for t in res.trace if " om_state " in t and "-B-20260803-buy-" in t]
    assert sum("state=rejected" in t and "renewal failed" in t for t in states) == 2
    sent = [o for o in w.sim.orders.values() if o["symbol"] == "B" and o["side"] == "buy"]
    assert len(sent) == 1 and sent[0]["client_order_id"].endswith("-buy-3")  # the venue got it once, at 15:52
    assert sent[0]["status"] == "filled"
    fills = [t for t in res.trace if " fill " in t and "-B-20260803-buy-" in t]
    assert len(fills) == 1


def test_a_renewal_failure_once_in_the_minute_is_absorbed_by_the_second_try(engine_kind):
    """One failed renewal, the next one fine: the buy goes out at 15:50 as on a normal day, on its first attempt."""
    w = World(quiet({D1: ("B",)}))
    replica, n = w.replica, {"left": 1}
    renew = replica.lease_renew

    def lease_renew(*a, **kw):
        if w.clock() >= et(D1, "15:50") and n["left"]:
            n["left"] -= 1
            raise ConnectionError("supabase: 503")
        return renew(*a, **kw)

    replica.lease_renew = lease_renew
    lease = EngineLease(replica, "H", "dst", mono=lambda: w.clock().timestamp(),
                        wall=lambda: w.clock().timestamp(), machine=machine_key("dst", "UUID-DST"))
    lease.acquire()
    w.broker = FencedBroker(w.broker, lease)
    res = w.run()
    assert res.ok, res.report()
    intents = _orders(res, "-B-20260803-buy-")
    assert len(intents) == 1 and intents[0][6:11] == "15:50" and "buy-1" in intents[0]
