"""M12 ticket 3.1 (#463): the stateful property test over the order manager and the ledger (om_machine.py), its
example budget, and the proof that it finds planted bugs.

Budget. `KAIBOT_STATEFUL_EXAMPLES` (default 15) action sequences of up to `KAIBOT_STATEFUL_STEPS` (default 40)
steps. The default is what every pull request's ordinary test run pays (a few seconds); the `stateful` workflow
runs 300 examples with branch coverage of the modules under test. A failure prints the shrunk sequence of rule calls,
which can be pasted into a test as it stands.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis.stateful import run_state_machine_as_test
from om_machine import DAYS, KNOWN, FusedJournal, FusedVenue, Kill, OrdersAndLedger, et
from reference_ledger import ReferenceLedger, dec

from kaibot.broker.protocol import OrderRequest
from kaibot.engine import execution as execution_mod
from kaibot.engine import ledger as ledger_mod
from kaibot.engine import orders as orders_mod
from kaibot.engine.orders import OrderManager, St, client_order_id

EXAMPLES = int(os.environ.get("KAIBOT_STATEFUL_EXAMPLES", "15"))
STEPS = int(os.environ.get("KAIBOT_STATEFUL_STEPS", "40"))
BUDGET = settings(max_examples=EXAMPLES, stateful_step_count=STEPS, deadline=None,
                  suppress_health_check=list(HealthCheck))
# The planted-bug runs: fixed seed and no example database, so "found" does not depend on luck or on an earlier run;
# no shrinking (the first failing sequence is proof enough, and shrinking is most of the cost).
HUNT = settings(max_examples=60, stateful_step_count=40, deadline=None, derandomize=True, database=None,
                suppress_health_check=list(HealthCheck), phases=[Phase.generate])


def test_the_machine_holds_on_the_engine_as_it_is():
    run_state_machine_as_test(OrdersAndLedger, settings=BUDGET)


# ---------------------------------------------------------------- the reference ledger itself
def test_the_reference_ledger_books_average_cost_fees_and_each_execution_once():
    ref = ReferenceLedger(1000)
    assert ref.execute("e1", "s", "A", "buy", 4, "100.10")
    assert ref.execute("e2", "s", "A", "buy", 4, "99.90")
    assert not ref.execute("e2", "s", "A", "buy", 4, "99.90")  # the same execution again
    assert ref.execute("e3", "s", "A", "sell", 2, 110, "0.03")
    assert ref.cash == dec("419.97") and ref.qty("s", "A") == 6
    assert ref.realized == dec("19.97") and ref.fees == dec("0.03")
    assert ref.equity({"A": 110}) == dec("1079.97") and ref.identity_gap({"A": 110}) == 0
    assert ref.execute("e4", "s", "A", "sell", 6, 100)
    assert not ref.holdings and ref.cash == dec("1019.97") and ref.realized == dec("19.97")
    assert ref.execute("e5", "s", "A", "sell", 1, 100) and ref.oversold == ["e5"]


# ---------------------------------------------------------------- planted bugs: the machine must find each
def _no_dedupe(monkeypatch):
    """The manager forgets which activities it has booked: a fill read twice is booked twice."""
    class Forgetful(set):
        def __contains__(self, item) -> bool:
            return False

    real = orders_mod.OrderManager.__init__

    def init(self, *a, **kw):
        real(self, *a, **kw)
        self.seen = Forgetful(self.seen)

    monkeypatch.setattr(orders_mod.OrderManager, "__init__", init)


def _sell_fees_dropped(monkeypatch):
    """The ledger books a sale's proceeds without its regulatory fees."""
    real = ledger_mod.Ledger._fill

    def fill(self, b, e):
        real(self, b, {**e, "fees": 0.0} if e["side"] == "sell" else e)

    monkeypatch.setattr(ledger_mod.Ledger, "_fill", fill)


def _blind_resend(monkeypatch):
    """An order without an answer is written off as lost without the lookup by its client id: the next pass sends
    the decision again while the first order may be working at the venue."""
    def resolve(self):
        out = [r for r in list(self.live.values()) if r.state in orders_mod.UNCONFIRMED]
        for r in out:
            self._state(r, orders_mod.St.REJECTED, reason="lost: never reached the venue")
        return out

    monkeypatch.setattr(orders_mod.OrderManager, "resolve", resolve)


def _intent_after_send(monkeypatch):
    """The venue is called before the intent is journalled (the write-ahead rule, audit D5, broken)."""
    real = orders_mod.OrderManager._append

    def append(self, kind, body):
        if kind == "om_intent":
            body = {k: v for k, v in body.items() if v is not None}
            self._fold(kind, body)  # in memory only; the journal gets it with the first state
            self._late = (kind, body)
            return
        late = self.__dict__.pop("_late", None)
        if late is not None:
            self.journal.append(*late)
        real(self, kind, body)

    monkeypatch.setattr(orders_mod.OrderManager, "_append", append)


def _terminal_reopened(monkeypatch):
    """A cancelled order is reported as accepted again (a state leaves a terminal state)."""
    real = orders_mod.OrderManager.cancel

    def cancel(self, coid):
        r = real(self, coid)
        if r.state is orders_mod.St.CANCELED:
            self._state(r, orders_mod.St.ACCEPTED)
        return r

    monkeypatch.setattr(orders_mod.OrderManager, "cancel", cancel)


def _window_ignored(monkeypatch):
    """The execution policy builds every order as if it were inside its style's window (the window check gone)."""
    inside = {"close": "12:00", "near_close": "15:55", "marketable_limit": "12:00"}
    real = execution_mod.ExecutionPolicy.request

    def request(self, symbol, side, qty, now, coid, **kw):
        return real(self, symbol, side, qty, et(DAYS[0], inside[self.style]), coid, **kw)

    monkeypatch.setattr(execution_mod.ExecutionPolicy, "request", request)


PLANTED = {
    "a duplicate fill is booked twice": (_no_dedupe, "booked twice|reconciliation finds differences|reference"),
    "a sale's fees are not booked": (_sell_fees_dropped, "reference|identity|venue"),
    "an unanswered order is sent again blind": (_blind_resend,
                                                "fills never attributed|two working orders|above their cap"),
    "the venue is called before the intent is journalled": (_intent_after_send, "intent"),
    "a terminal state is left": (_terminal_reopened, "illegal transition|the manager says"),
    "an order leaves outside its window": (_window_ignored, "order sent at|limit sent at"),
}


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_the_machine_finds_a_planted_bug(name, monkeypatch):
    plant, expected = PLANTED[name]
    plant(monkeypatch)
    with pytest.raises(AssertionError, match=expected):
        run_state_machine_as_test(OrdersAndLedger, settings=HUNT)


# ---------------------------------------------------------------- engine defects the machine found (KNOWN)
def _resting_buy():
    """A manager with one limit buy resting at the venue, far below the market."""
    m = OrdersAndLedger()
    m.clock.set(et(DAYS[0], "10:00", 7))
    req = OrderRequest("A", "buy", 5, "limit", "day", client_order_id(1, "s1", "A", DAYS[0], "target"), limit_price=90)
    r = m.om.submit(req, strategy="s1", purpose="target", session=DAYS[0])
    assert r.state is St.ACCEPTED
    return m, r.coid


def _cancel_again_later(m: OrdersAndLedger, coid: str) -> None:
    m.sim.down = False
    for _ in range(3):  # minutes pass; the engine syncs and still wants the order gone
        m._tick(1)
        m.om.sync()
        m.om.cancel(coid)
    m.om.sync()


@pytest.mark.xfail(strict=True, reason="#491: a cancel that never reached the venue is never sent again")
def test_491_a_cancel_lost_to_a_network_error_is_sent_again():
    assert KNOWN["cancel_unanswered"] == 491
    m, coid = _resting_buy()
    try:
        m.sim.down = True
        m.om.cancel(coid)  # BrokerError 0: the request never arrived
        _cancel_again_later(m, coid)
        venue_order = m.sim.order_by_client_id(coid)
        assert venue_order["status"] == "canceled", f"still {venue_order['status']} at the venue"
        assert m.om.get(coid).state is St.CANCELED
    finally:
        m.teardown()


@pytest.mark.xfail(strict=True, reason="#491: a cancel that never reached the venue is never sent again")
def test_491_a_cancel_cut_by_a_kill_before_the_venue_call_is_sent_again():
    m, coid = _resting_buy()
    try:
        m.fuse.arm(1, "after")  # the pending_cancel entry is journalled, then the process dies
        with pytest.raises(Kill):
            m.om.cancel(coid)
        m._restart()
        assert m.om.get(coid).state is St.PENDING_CANCEL
        _cancel_again_later(m, coid)
        venue_order = m.sim.order_by_client_id(coid)
        assert venue_order["status"] == "canceled", f"still {venue_order['status']} at the venue"
        assert m.om.get(coid).state is St.CANCELED
    finally:
        m.teardown()


def test_the_fuse_kills_before_and_after_the_numbered_effect():
    """The crash rule's own mechanics: `before` leaves the effect undone, `after` leaves it done."""
    m, coid = _resting_buy()
    try:
        assert isinstance(m.journal, FusedJournal) and isinstance(m.venue, FusedVenue)
        assert isinstance(m.om, OrderManager)
        n = m.journal.last_seq()
        m.fuse.arm(1, "before")
        with pytest.raises(Kill):
            m.om.cancel(coid)
        assert m.journal.last_seq() == n
        m.fuse.arm(2, "after")  # effect 1: the journal entry; effect 2: the venue's cancel
        with pytest.raises(Kill):
            m.om.cancel(coid)
        assert m.journal.last_seq() == n + 1 and m.sim.order_by_client_id(coid)["status"] == "canceled"
        m._restart()
        assert m.om.get(coid).state is St.CANCELED and m.restarts == 1
    finally:
        m.teardown()
