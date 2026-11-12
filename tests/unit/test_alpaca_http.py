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


# --- S74c (#147): one total deadline per trading call, so a stuck venue cannot outlast the heartbeat window ---

from kaibot.broker import alpaca as broker_alpaca  # noqa: E402
from kaibot.broker.alpaca import ORDER_DEADLINE_S, ORDER_TIMEOUT_S  # noqa: E402
from kaibot.broker.protocol import OrderRequest  # noqa: E402
from kaibot.engine.watchdog import STALE_S  # noqa: E402


def _blackhole(clock, calls):
    """A venue that accepts the connection and never answers: each attempt uses its whole timeout."""
    def opener(req, timeout):
        calls.append((req.get_method(), req.full_url, timeout))
        clock.t += timeout
        raise TimeoutError("timed out")
    return opener


def _capped(opener_for, spec=UNLIMITED):
    clock = FakeClock()
    calls: list = []
    h = _Http("k", "s", budget=Budget("alpaca_trading", spec, clock), opener=opener_for(clock, calls),
              sleep=clock.sleep, timeout_s=ORDER_TIMEOUT_S, deadline_s=ORDER_DEADLINE_S)
    return h, clock, calls


def test_the_order_deadline_is_well_inside_the_heartbeat_window():
    assert ORDER_DEADLINE_S <= 20.0 and ORDER_DEADLINE_S * 4 <= STALE_S
    assert ORDER_TIMEOUT_S < ORDER_DEADLINE_S


def test_a_blackholed_trading_call_gives_up_at_the_deadline():
    h, clock, calls = _capped(_blackhole)
    t0 = clock.t
    with pytest.raises(AlpacaError) as e:
        h.call("POST", "https://paper-api.alpaca.markets/v2/orders", body={"x": 1})
    assert e.value.status == 0 and "deadline" in e.value.body
    assert clock.t - t0 <= ORDER_DEADLINE_S  # was 5 x 30 s + 31 s of backoff, about 180 s
    assert len(calls) >= 2  # it still retries inside the deadline
    assert all(t <= ORDER_TIMEOUT_S for *_, t in calls)


def test_no_attempt_or_backoff_runs_past_the_deadline():
    h, clock, calls = _capped(_blackhole)
    t0 = clock.t
    with pytest.raises(AlpacaError):
        h.call("GET", "https://paper-api.alpaca.markets/v2/positions")
    # each attempt's timeout is cut to what is left, and no sleep is started that would end past the deadline
    assert clock.t - t0 == sum(t for *_, t in calls) + sum(clock.sleeps) <= ORDER_DEADLINE_S
    assert calls[-1][2] < ORDER_TIMEOUT_S  # the last attempt only got the time that was left


def test_5xx_on_the_order_path_stops_at_the_deadline():
    def opener_for(clock, calls):
        def opener(req, timeout):
            calls.append(timeout)
            clock.t += 3.0  # a slow 503
            raise _err(503)
        return opener
    h, clock, calls = _capped(opener_for)
    t0 = clock.t
    with pytest.raises(AlpacaError) as e:
        h.call("DELETE", "https://paper-api.alpaca.markets/v2/orders/1")
    assert e.value.status == 0 and clock.t - t0 <= ORDER_DEADLINE_S


def test_429_on_the_order_path_stops_at_the_deadline():
    def opener_for(clock, calls):
        def opener(req, timeout):
            calls.append(timeout)
            raise _err(429)
        return opener
    h, clock, calls = _capped(opener_for)
    t0 = clock.t
    with pytest.raises(AlpacaError):
        h.call("GET", "https://paper-api.alpaca.markets/v2/orders")
    assert clock.t - t0 <= ORDER_DEADLINE_S


def test_a_slow_budget_wait_counts_against_the_deadline():
    # the source is held (another caller's 429) for longer than the deadline: no attempt is sent after it
    h, clock, calls = _capped(_blackhole)
    h.budget.backoff(ORDER_DEADLINE_S + 5)
    t0 = clock.t
    with pytest.raises(AlpacaError):
        h.call("GET", "https://paper-api.alpaca.markets/v2/positions")
    assert calls == [] and clock.t - t0 <= ORDER_DEADLINE_S  # it gives up instead of sleeping past the deadline


def test_the_happy_path_on_the_order_path_is_one_request():
    def opener_for(clock, calls):
        def opener(req, timeout):
            calls.append((req.get_method(), req.full_url, timeout))
            return _Resp(b'{"id": "o1"}')
        return opener
    h, clock, calls = _capped(opener_for)
    assert h.call("POST", "https://paper-api.alpaca.markets/v2/orders", body={"x": 1}) == {"id": "o1"}
    assert calls == [("POST", "https://paper-api.alpaca.markets/v2/orders", ORDER_TIMEOUT_S)] and clock.sleeps == []


def test_data_calls_keep_their_long_retries():
    clock = FakeClock()
    calls: list = []
    h = _Http("k", "s", budget=Budget("alpaca", UNLIMITED, clock), opener=_blackhole(clock, calls), sleep=clock.sleep)
    with pytest.raises(AlpacaError):
        h.call("GET", "https://data.alpaca.markets/v2/stocks/bars")
    assert [t for *_, t in calls] == [30] * 5 and clock.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_the_trading_client_is_capped_and_the_data_client_is_not():
    p, d = alpaca.AlpacaPaper("k", "s"), alpaca.AlpacaData("k", "s")
    assert p.http.deadline_s == ORDER_DEADLINE_S and p.http.timeout_s == ORDER_TIMEOUT_S
    assert d.http.deadline_s is None and d.http.timeout_s == 30


def _paper_blackholed():
    p = broker_alpaca.AlpacaPaper("k", "s")
    clock = FakeClock()
    calls: list = []
    p.http.budget = Budget("alpaca_trading", UNLIMITED, clock)
    p.http._open, p.http._sleep = _blackhole(clock, calls), clock.sleep
    return p, clock, calls


ORDER_PATH = {
    "submit": lambda p: p.submit(OrderRequest(symbol="A", qty=1, side="buy", type="market",
                                              client_order_id="kb-x")),
    "cancel": lambda p: p.cancel("o1"),
    "replace": lambda p: p.replace("o1", stop_price=9.5),
    "close_position": lambda p: p.close_position("A"),
    "positions": lambda p: p.positions(),
    "open_orders": lambda p: p.open_orders(),
}


@pytest.mark.parametrize("name", sorted(ORDER_PATH))
def test_every_order_path_call_ends_within_the_deadline(name):
    p, clock, calls = _paper_blackholed()
    t0 = clock.t
    with pytest.raises(AlpacaError):
        ORDER_PATH[name](p)
    assert calls and clock.t - t0 <= ORDER_DEADLINE_S


def test_a_deadline_abort_on_the_budget_wait_does_not_hold_back_other_callers():
    # S74d (#154): the call that gives up because its slot comes too late must not keep that slot booked
    h, clock, calls = _capped(_blackhole, spec=BudgetSpec(((1, 60.0),)))
    assert h.budget.reserve() == clock.t  # another caller took this minute's slot
    t0 = clock.t
    with pytest.raises(AlpacaError):
        h.call("GET", "https://paper-api.alpaca.markets/v2/positions")
    assert calls == [] and clock.t == t0  # it gave up without waiting
    assert h.budget.reserve() == t0 + 60.0  # the next caller gets the very next slot, not one a minute later
