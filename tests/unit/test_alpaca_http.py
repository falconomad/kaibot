"""engine.alpaca._Http: pacing goes through the shared budget; retry/backoff/AlpacaError behaviour is unchanged."""

import io
import urllib.error

import pytest

from kaibot.common.ratelimit import Budget, BudgetSpec
from kaibot.engine import alpaca
from kaibot.engine.alpaca import AlpacaError, _Http


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(round(s, 6))
        self.t += s


class _Resp(io.BytesIO):
    status = 200
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _err(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(b'{"message":"no"}'))


UNLIMITED = BudgetSpec()


def _http(script, spec=UNLIMITED):
    clock = FakeClock()
    budget = Budget("alpaca", spec, clock)
    calls = []

    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, timeout))
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _Resp(step)

    h = _Http("k", "s", budget=budget, opener=opener, sleep=clock.sleep)
    return h, clock, calls, budget


def test_success_returns_json_and_sends_auth_headers():
    h, clock, calls, _ = _http([b'{"ok": 1}'])
    assert h.call("GET", "https://paper-api.alpaca.markets/v2/account", {"a": 1}) == {"ok": 1}
    assert calls == [("GET", "https://paper-api.alpaca.markets/v2/account?a=1", 30)]
    assert h.h["APCA-API-KEY-ID"] == "k" and clock.sleeps == []


def test_empty_body_is_none():
    h, *_ = _http([b""])
    assert h.call("DELETE", "https://paper-api.alpaca.markets/v2/orders/1") is None


def test_429_backs_off_then_succeeds_and_holds_the_whole_source():
    h, clock, calls, budget = _http([_err(429), _err(429), b"[]"])
    assert h.call("GET", "https://data.alpaca.markets/v2/x") == []
    assert len(calls) == 3
    assert clock.sleeps == [1.0, 2.0]  # same 2**attempt backoff as before, now via the shared budget
    budget.backoff(5)
    t0 = clock.t
    budget.acquire()
    assert clock.t == t0 + 5  # any other caller of the source waits too


def test_5xx_and_network_errors_retry_with_backoff():
    h, clock, calls, _ = _http([_err(503), urllib.error.URLError("down"), TimeoutError(), b"{}"])
    assert h.call("GET", "https://data.alpaca.markets/v2/x") == {}
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_4xx_raises_alpaca_error_without_retry():
    h, clock, calls, _ = _http([_err(422)])
    with pytest.raises(AlpacaError) as e:
        h.call("POST", "https://paper-api.alpaca.markets/v2/orders", body={"x": 1})
    assert e.value.status == 422 and "no" in e.value.body and len(calls) == 1


def test_gives_up_after_five_attempts():
    h, clock, calls, _ = _http([_err(500)] * 5)
    with pytest.raises(AlpacaError) as e:
        h.call("GET", "https://data.alpaca.markets/v2/x")
    assert e.value.status == 0 and len(calls) == 5
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_budget_paces_calls():
    h, clock, calls, _ = _http([b"{}"] * 4, BudgetSpec(((2, 60.0),)))
    for _ in range(4):
        h.call("GET", "https://data.alpaca.markets/v2/x")
    assert clock.sleeps == [60.0]  # 2 immediately, then the window: 2 more at t+60


def test_clients_share_one_budget_per_source(monkeypatch):
    from kaibot.common import ratelimit

    reg = ratelimit.Registry(loader=lambda: {"alpaca": BudgetSpec(((150, 60.0),)),
                                             "alpaca_trading": BudgetSpec(((150, 60.0),))})
    monkeypatch.setattr(ratelimit, "REGISTRY", reg)
    d1, d2 = alpaca.AlpacaData("k", "s"), alpaca.AlpacaData("k", "s")
    p = alpaca.AlpacaPaper("k", "s")
    assert d1.http.budget is d2.http.budget is reg.get("alpaca")
    assert p.http.budget is reg.get("alpaca_trading") and p.http.budget is not d1.http.budget
