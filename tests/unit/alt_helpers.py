"""Fake network for alt-data collector tests: URL substring -> recorded fixture. Never touches the network."""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path

from kaibot.common.ratelimit import Budget, BudgetSpec

FIX = Path(__file__).parent.parent / "fixtures"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict):
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": "application/json", **headers}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """routes: list of (url substring, bytes | int status | Exception, headers). First match wins; logs calls."""

    def __init__(self, routes, spec: BudgetSpec | None = None):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []
        self.clock = FakeClock()
        self.budget = Budget("test", spec or BudgetSpec(), self.clock)

    def __call__(self, req, timeout):
        assert timeout and timeout <= 120
        self.calls.append((req.full_url, dict(req.header_items())))
        for sub, resp, *h in self.routes:
            if sub in req.full_url:
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, int):
                    raise urllib.error.HTTPError(req.full_url, resp, "x", {}, io.BytesIO(b""))
                return _Resp(resp, h[0] if h else {})
        raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b""))

    @property
    def kw(self):
        return {"opener": self, "budget": self.budget}
