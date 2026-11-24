"""common.http root certificates: certifi's bundle when available, the default context otherwise, and a failed
verification is permanent (raised at once, never retried). No network: urlopen is replaced."""

from __future__ import annotations

import builtins
import ssl
import sys
import urllib.error
import urllib.request

import pytest

from kaibot.common import http
from kaibot.common.ratelimit import Budget, BudgetSpec


class FakeClock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture(autouse=True)
def fresh_context(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    http.ssl_context.cache_clear()
    yield
    http.ssl_context.cache_clear()


def _spy_create(monkeypatch):
    calls = []
    real = ssl.create_default_context

    def spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(http.ssl, "create_default_context", spy)
    return calls


def test_certifi_bundle_is_used_when_installed(monkeypatch):
    import certifi

    calls = _spy_create(monkeypatch)
    ctx = http.ssl_context()
    assert calls == [{"cafile": certifi.where()}]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert http.ssl_context() is ctx  # built once


def test_default_context_when_certifi_is_missing(monkeypatch):
    calls = _spy_create(monkeypatch)
    real_import = builtins.__import__

    def no_certifi(name, *a, **kw):
        if name == "certifi":
            raise ImportError("no certifi")
        return real_import(name, *a, **kw)

    monkeypatch.delitem(sys.modules, "certifi", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_certifi)
    ctx = http.ssl_context()
    assert calls == [{}]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_an_explicit_ssl_cert_file_keeps_the_default_context(monkeypatch, tmp_path):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "bundle.pem"))
    calls = _spy_create(monkeypatch)
    http.ssl_context()
    assert calls == [{}]  # behaviour as before this change (scripts/run_engine.sh exports SSL_CERT_FILE)


def test_default_opener_passes_the_verifying_context(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout, context):
        seen["context"] = context
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http, "_URLOPEN", fake_urlopen)  # stands in for the real urlopen
    with pytest.raises(http.HttpError):
        http.get("https://e/x", source="x", attempts=1, budget=Budget("x", BudgetSpec(), FakeClock()))
    assert seen["context"] is http.ssl_context()
    assert seen["context"].verify_mode == ssl.CERT_REQUIRED


def test_a_replaced_urlopen_is_called_as_before(monkeypatch):
    """Code and tests that swap urllib.request.urlopen for a fake (req, timeout) keep working unchanged."""
    seen = []

    def fake(req, timeout):
        seen.append(timeout)
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(http.HttpError):
        http.get("https://e/x", source="x", attempts=1, timeout=7, budget=Budget("x", BudgetSpec(), FakeClock()))
    assert seen == [7]


def _cert_failure():
    return ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")


@pytest.mark.parametrize("exc", [urllib.error.URLError(_cert_failure()), _cert_failure()])
def test_a_certificate_error_is_raised_at_once_and_not_retried(exc):
    clock = FakeClock()
    tries = []

    def op(req, timeout):
        tries.append(req.full_url)
        raise exc

    with pytest.raises(http.CertificateError) as e:
        http.get("https://e/x", source="x", budget=Budget("x", BudgetSpec(), clock), opener=op)
    assert len(tries) == 1 and clock.sleeps == []
    assert e.value.status == 0 and isinstance(e.value, http.HttpError)
    assert "Install Certificates.command" in str(e.value) and "CERTIFICATE_VERIFY_FAILED" in str(e.value)


def test_other_network_errors_are_still_retried():
    clock = FakeClock()

    def op(req, timeout):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    with pytest.raises(http.HttpError) as e:
        http.get("https://e/x", source="x", budget=Budget("x", BudgetSpec(), clock), opener=op)
    assert not isinstance(e.value, http.CertificateError)
    assert e.value.status == 0 and clock.sleeps == [1, 2, 4, 8, 16]
