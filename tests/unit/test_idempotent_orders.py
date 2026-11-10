"""Story S09: a retried order submission gives one order at the broker.

Simulates the worst case at the HTTP level: Alpaca accepts the bracket but the response is lost (timeout), so
the client retries the same POST. The retry hits Alpaca's duplicate client_order_id check (422) and the adapter
adopts the existing order instead of placing a second one."""

import io
import json
import urllib.error
import urllib.parse

import kaibot.engine.alpaca as alp


class FakeAlpaca:
    def __init__(self, lose_first_response=True):
        self.orders: dict[str, dict] = {}
        self.lose = lose_first_response
        self.posts = 0

    def urlopen(self, req, timeout=30):
        url, method = req.full_url, req.get_method()
        if method == "POST" and url.endswith("/v2/orders"):
            self.posts += 1
            body = json.loads(req.data)
            coid = body["client_order_id"]
            if coid in self.orders:
                raise urllib.error.HTTPError(url, 422, "dup", {}, io.BytesIO(
                    b'{"code":40010001,"message":"client_order_id must be unique"}'))
            self.orders[coid] = {"id": f"o{len(self.orders) + 1}", "client_order_id": coid, "status": "accepted",
                                 "symbol": body["symbol"], "qty": body["qty"]}
            if self.lose:
                self.lose = False
                raise TimeoutError("response lost")
            return Resp(self.orders[coid])
        if method == "GET" and "orders:by_client_order_id" in url:
            coid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["client_order_id"][0]
            if coid not in self.orders:
                raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"{}"))
            return Resp(self.orders[coid])
        raise AssertionError(f"unexpected {method} {url}")


class Resp:
    def __init__(self, obj):
        self.raw = json.dumps(obj).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def broker(monkeypatch, fake):
    monkeypatch.setattr(alp.urllib.request, "urlopen", fake.urlopen)
    if hasattr(alp, "time"):  # before the shared rate-limit layer (#17)
        monkeypatch.setattr(alp.time, "sleep", lambda s: None)
    b = alp.AlpacaPaper("k", "s")
    if hasattr(b.http, "_open"):  # with the shared layer: the opener and sleep are bound at construction
        b.http._open, b.http._sleep = fake.urlopen, (lambda s: None)
    return b


def test_lost_response_retry_gives_one_order(monkeypatch):
    fake = FakeAlpaca()
    b = broker(monkeypatch, fake)
    o = b.submit_bracket("kb-r1-s42-1", "AAPL", 10, 190.0, 200.0)
    assert fake.posts == 2 and len(fake.orders) == 1
    assert o["id"] == fake.orders["kb-r1-s42-1"]["id"]


def test_resubmitting_the_same_signal_adopts(monkeypatch):
    fake = FakeAlpaca(lose_first_response=False)
    b = broker(monkeypatch, fake)
    first = b.submit_bracket("kb-r1-s7-1", "MSFT", 5, 400.0, 420.0)
    again = b.submit_bracket("kb-r1-s7-1", "MSFT", 5, 400.0, 420.0)
    assert len(fake.orders) == 1 and first["id"] == again["id"]


def test_different_signals_are_different_orders(monkeypatch):
    fake = FakeAlpaca(lose_first_response=False)
    b = broker(monkeypatch, fake)
    b.submit_bracket("kb-r1-s1-1", "MSFT", 5, 400.0, 420.0)
    b.submit_bracket("kb-r1-s2-1", "MSFT", 5, 400.0, 420.0)
    assert len(fake.orders) == 2
