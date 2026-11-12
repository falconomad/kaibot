"""Issue #115: one synchronous `Broker` protocol. `AlpacaPaper` implements it, the engine is typed against it,
and the engine's order calls are one-line wrappers over `submit`, `replace` and `cancel`."""

from __future__ import annotations

import ast
import importlib
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest

import kaibot.broker.alpaca as broker_alpaca
import kaibot.engine.alpaca as engine_alpaca
from kaibot.broker import Broker, BrokerAdapter, BrokerBase, BrokerError, OrderRequest
from kaibot.broker.alpaca import AlpacaError, AlpacaPaper
from kaibot.engine.core import Engine
from kaibot.engine.watchdog import Watchdog

SRC = Path(__file__).resolve().parents[2] / "src" / "kaibot"
PRIMITIVES = {"submit", "cancel", "replace", "open_orders", "positions", "account", "activities", "clock",
              "calendar"}


def members(proto) -> set[str]:
    return {n for n in vars(proto) if not n.startswith("_") and callable(getattr(proto, n))}


def test_protocol_has_the_nine_primitives_of_decision_15():
    assert members(Broker) >= PRIMITIVES
    assert BrokerAdapter is Broker  # the name CLAUDE.md protects


def test_alpaca_paper_implements_the_protocol():
    b = AlpacaPaper("k", "s")
    assert isinstance(b, Broker) and isinstance(b, BrokerBase) and b.name == "alpaca_paper"
    assert all(callable(getattr(b, n)) for n in members(Broker))


def test_the_async_broker_base_is_gone_and_the_old_import_path_still_works():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("kaibot.broker.base")
    assert engine_alpaca.AlpacaPaper is broker_alpaca.AlpacaPaper
    assert engine_alpaca._Http is broker_alpaca._Http and engine_alpaca.AlpacaError is AlpacaError


def test_engine_and_watchdog_are_typed_against_the_protocol():
    assert typing.get_type_hints(Engine.__init__)["broker"] is Broker
    assert typing.get_type_hints(Watchdog.__init__)["broker"] is Broker


def test_everything_the_engine_calls_on_the_broker_is_in_the_protocol():
    """The protocol was shaped from the real call sites: a new call on `self.broker` must be added to it."""
    called = set()
    for f in sorted((SRC / "engine").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "broker"):
                called.add(node.func.attr)
    assert called, "the scan found no broker call: it is broken"
    assert called <= members(Broker), f"not in the Broker protocol: {sorted(called - members(Broker))}"


# ---------------------------------------------------------------- order requests
def test_order_request_bodies():
    bracket = OrderRequest("AAPL", "buy", 3, "market", "gtc", "kb-1-2-1", order_class="bracket", take_profit=200.129,
                           stop_loss=190.0)
    assert bracket.body() == {"symbol": "AAPL", "qty": "3", "side": "buy", "type": "market", "time_in_force": "gtc",
                              "order_class": "bracket", "client_order_id": "kb-1-2-1",
                              "take_profit": {"limit_price": "200.13"}, "stop_loss": {"stop_price": "190.00"}}
    assert list(bracket.body()) == ["symbol", "qty", "side", "type", "time_in_force", "order_class",
                                    "client_order_id", "take_profit", "stop_loss"]
    limit = OrderRequest("SPY", "buy", 10, "limit", "day", limit_price=501.239)
    assert limit.body() == {"symbol": "SPY", "qty": "10", "side": "buy", "type": "limit", "time_in_force": "day",
                            "limit_price": "501.24"}
    stop = OrderRequest("SPY", "sell", 10, "stop", "gtc", "c1", stop_price=480)
    assert stop.body()["stop_price"] == "480.00" and "order_class" not in stop.body()
    part = OrderRequest("SPY", "buy", 0.25)
    assert part.fractional and part.body()["qty"] == "0.25" and not limit.fractional


class Recorder(BrokerBase):
    def __init__(self):
        self.calls = []

    def submit(self, order):
        self.calls.append(("submit", order))
        return {"id": "o1"}

    def replace(self, order_id, *, stop_price=None, limit_price=None):
        self.calls.append(("replace", order_id, stop_price, limit_price))
        return {"id": order_id}

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))


def test_engine_call_sites_are_wrappers_over_the_primitives():
    r = Recorder()
    r.submit_bracket("kb-1-2-1", "AAPL", 3, 190.0, 200.0)
    r.submit_oto_stop("kb-1-3-1", "SPY", 22, 641.5)
    r.submit_moc_sell("kb-1-3-1-moc", "SPY", 22)
    r.replace_stop("o1s", 191.0)
    r.replace_limit("o1t", 201.5)
    r.cancel_order("o1")
    assert r.calls == [
        ("submit", OrderRequest("AAPL", "buy", 3, "market", "gtc", "kb-1-2-1", order_class="bracket",
                                take_profit=200.0, stop_loss=190.0)),
        ("submit", OrderRequest("SPY", "buy", 22, "market", "gtc", "kb-1-3-1", order_class="oto", stop_loss=641.5)),
        ("submit", OrderRequest("SPY", "sell", 22, "market", "cls", "kb-1-3-1-moc")),
        ("replace", "o1s", 191.0, None),
        ("replace", "o1t", None, 201.5),
        ("cancel", "o1"),
    ]


# ---------------------------------------------------------------- AlpacaPaper specifics
def test_alpaca_error_is_a_broker_error_with_the_same_message():
    e = AlpacaError(422, '{"message":"no"}')
    assert isinstance(e, BrokerError) and isinstance(e, RuntimeError)
    assert e.status == 422 and e.body == '{"message":"no"}' and str(e) == 'Alpaca 422: {"message":"no"}'
    assert str(BrokerError(403, "insufficient buying power")) == "broker 403: insufficient buying power"


def test_alpaca_submit_sends_any_order_request(monkeypatch):
    sent = []
    monkeypatch.setattr(broker_alpaca._Http, "call",
                        lambda self, method, url, params=None, body=None: sent.append((method, url, body)) or {})
    AlpacaPaper("k", "s").submit(OrderRequest("SPY", "sell", 5, "stop", "gtc", "kb-9", stop_price=480.0))
    assert sent == [("POST", "https://paper-api.alpaca.markets/v2/orders",
                     {"symbol": "SPY", "qty": "5", "side": "sell", "type": "stop", "time_in_force": "gtc",
                      "stop_price": "480.00", "client_order_id": "kb-9"})]


def test_alpaca_activities_reads_fills_page_by_page(monkeypatch):
    pages = [[{"id": f"a{i}", "activity_type": "FILL"} for i in range(100)], [{"id": "a100", "activity_type": "FILL"}]]
    sent = []

    def call(self, method, url, params=None, body=None):
        sent.append((method, url, dict(params)))
        return pages[len(sent) - 1]

    monkeypatch.setattr(broker_alpaca._Http, "call", call)
    rows = AlpacaPaper("k", "s").activities(after=datetime(2026, 9, 15, 13, 30, tzinfo=UTC))
    assert [r["id"] for r in rows] == [f"a{i}" for i in range(101)]
    first = {"activity_types": "FILL", "direction": "asc", "page_size": 100, "after": "2026-09-15T13:30:00Z"}
    assert sent == [("GET", "https://paper-api.alpaca.markets/v2/account/activities", first),
                    ("GET", "https://paper-api.alpaca.markets/v2/account/activities", {**first, "page_token": "a99"})]
