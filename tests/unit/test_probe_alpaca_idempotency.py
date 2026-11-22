"""scripts/probe_alpaca_idempotency.py (M9 1.11, issue #436) against a fake venue: the probe places one order,
measures the lookups, repeats the client id, cancels, and never leaves an order behind. Nothing here touches the
network; the real run is the stakeholder's, on the Mac."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaibot.broker.protocol import BrokerError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_alpaca_idempotency.py"


def load():
    spec = importlib.util.spec_from_file_location("probe_alpaca_idempotency", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeVenue:
    """Alpaca as documented: a repeated client id is a 422; `lag_s`: the by-client-id lookup sees an order only
    this long after it was accepted; `dupes`: a broken venue that accepts the repeated id as a new order."""

    def __init__(self, clock, lag_s=0.0, dupes=False, held=(), fail_after_submit=False):
        self.clock, self.lag_s, self.dupes, self.held = clock, lag_s, dupes, set(held)
        self.orders: dict[str, dict] = {}
        self.posts = 0
        self.fail_after_submit = fail_after_submit

    def post(self, req):
        self.posts += 1
        known = [o for o in self.orders.values() if o["client_order_id"] == req.client_order_id]
        if known and not self.dupes:
            return 422, '{"code":40010001,"message":"client_order_id must be unique"}'
        oid = f"o{len(self.orders) + 1}"
        self.orders[oid] = {"id": oid, "client_order_id": req.client_order_id, "symbol": req.symbol,
                            "status": "accepted", "at": self.clock()}
        return 200, dict(self.orders[oid])

    def by_client_id(self, coid):
        if self.fail_after_submit:
            raise BrokerError(0, "no answer")
        return next((dict(o) for o in self.orders.values() if o["client_order_id"] == coid
                     and self.clock() - o["at"] >= self.lag_s), None)

    def by_id(self, oid):
        return dict(self.orders[oid]) if oid in self.orders else None

    def open_orders(self):
        return [dict(o) for o in self.orders.values() if o["status"] == "accepted"]

    def positions(self):
        return {s: {"qty": "1"} for s in self.held}

    def cancel(self, oid):
        self.orders[oid]["status"] = "canceled"


def run(v, clock, **kw):
    return load().probe(v, "SPY", 250.0, "20261121180000", clock=clock, sleep=clock.sleep, **kw)


def test_the_probe_measures_and_leaves_nothing_behind():
    clock = Clock()
    v = FakeVenue(clock, lag_s=2.0)
    res = run(v, clock)
    assert res["verdict"] == {"duplicate_refused": True, "slowest_lookup_s": 2.0,
                              "a_lookup_never_saw_the_order": False, "client_id_reusable_after_cancel": False}
    assert res["steps"]["lookup_lag"]["by_client_order_id"]["first_seen_after_s"] == 2.0
    assert res["steps"]["lookup_lag"]["open_orders"]["first_seen_after_s"] == 0.0
    assert res["steps"]["duplicate_client_order_id"]["same_body"]["http"] == 422
    assert res["cleanup"] == {"orders_left_open": [], "position_in_the_symbol": False}
    assert len(v.orders) == 1 and not v.open_orders() and v.posts == 4
    assert res["client_order_id"].startswith("probe-idem-") and not res["client_order_id"].startswith("kb")


def test_a_lookup_that_never_sees_the_order_is_reported_and_the_order_is_still_cancelled():
    clock = Clock()
    v = FakeVenue(clock, lag_s=1e9)
    res = run(v, clock, watch_s=3.0)
    assert res["verdict"]["a_lookup_never_saw_the_order"] and res["verdict"]["slowest_lookup_s"] is None
    assert not v.open_orders() and res["cleanup"]["orders_left_open"] == []


def test_a_venue_that_accepts_the_repeated_id_has_every_order_cancelled():
    clock = Clock()
    v = FakeVenue(clock, dupes=True)
    res = run(v, clock)
    assert res["verdict"]["duplicate_refused"] is False and res["verdict"]["client_id_reusable_after_cancel"]
    assert len(v.orders) == 4 and not v.open_orders() and res["cleanup"]["orders_left_open"] == []


def test_an_error_in_the_middle_still_cancels_the_order():
    clock = Clock()
    v = FakeVenue(clock, fail_after_submit=True)
    with pytest.raises(BrokerError):
        run(v, clock)
    assert len(v.orders) == 1 and not v.open_orders()


def test_it_refuses_to_start_on_a_held_stock_an_open_order_or_in_the_send_window():
    m, clock = load(), Clock()
    noon = datetime(2026, 11, 23, 17, 0, tzinfo=UTC)
    m.check_start(FakeVenue(clock), "SPY", noon)
    with pytest.raises(m.Refused, match="holds SPY"):
        m.check_start(FakeVenue(clock, held=["SPY"]), "SPY", noon)
    v = FakeVenue(clock)
    v.post(m.OrderRequest("SPY", "buy", 1, "limit", "day", "kb1-x-SPY-20261123-buy-1", limit_price=1.0))
    with pytest.raises(m.Refused, match="open order"):
        m.check_start(v, "SPY", noon)
    with pytest.raises(m.Refused, match="send window"):
        m.check_start(FakeVenue(clock), "SPY", datetime(2026, 11, 23, 20, 50, tzinfo=UTC))


def test_without_yes_nothing_is_sent_and_the_report_has_no_secret(capsys):
    m = load()
    assert m.main([]) == 0 and "dry run" in capsys.readouterr().out
    clock = Clock()
    text = m.report(run(FakeVenue(clock), clock), datetime(2026, 11, 23, 17, 0, tzinfo=UTC))
    assert "probe-idem-" in text and "APCA" not in text and "secret" not in text.lower()
