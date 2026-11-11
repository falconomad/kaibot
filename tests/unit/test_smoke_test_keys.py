"""S54 follow-up: scripts/smoke_test_keys.py Databento auth check, free-plan SIP probe, root certificates and the
"http 0" hint. All network faked; no key value may ever reach the output."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_test_keys.py"
KEY = "db-SECRET-not-real"
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
BUNDLE = "/fake/certifi/cacert.pem"
HINT = "http 0 everywhere usually means missing root certificates: pip install certifi, or run with .venv/bin/python"


@pytest.fixture(autouse=True)
def cert_env(monkeypatch):
    """main() may set SSL_CERT_FILE: start each test without it and restore the real value afterwards."""
    monkeypatch.setenv("SSL_CERT_FILE", "x")
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.setitem(sys.modules, "certifi", types.SimpleNamespace(where=lambda: BUNDLE))


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Anything a test does not fake fails loudly instead of leaving the machine."""
    def refuse(req, timeout=None):
        raise AssertionError(f"unfaked network call to {urllib.parse.urlsplit(req.full_url).hostname}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test_keys", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(tmp_path, text="SEC_USER_AGENT=kaibot test@example.com\n"):
    env = tmp_path / "secrets.env"
    env.write_text(text)
    return ["smoke_test_keys.py", str(env)]


def test_sip_probe_pass_uses_window_older_than_15_min(monkeypatch):
    m = _load()
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append(url)
        return 200, {"bars": [{"t": "2026-11-10T14:30:00Z", "c": 1.0}]}

    monkeypatch.setattr(m, "get", fake_get)
    ok, detail = m.check_alpaca_sip_history({"APCA-API-KEY-ID": "k"}, now=NOW)
    assert ok and "serves SIP history" in detail
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(seen[0]).query))
    assert q["feed"] == "sip" and q["timeframe"] == "1Min"
    end = datetime.fromisoformat(q["end"].replace("Z", "+00:00"))
    assert (NOW - end).total_seconds() >= 15 * 60


def test_sip_probe_fail_on_403_or_no_bars(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (403, None))
    ok, detail = m.check_alpaca_sip_history({}, now=NOW)
    assert not ok and "http 403" in detail and "does NOT" in detail
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (200, {"bars": None}))
    ok, detail = m.check_alpaca_sip_history({}, now=NOW)
    assert not ok and "no bars" in detail


def test_databento_check_is_zero_cost_and_never_shows_key(monkeypatch):
    m = _load()
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append((url, headers))
        return 200, ["XNAS.ITCH", "EQUS.MINI"]

    monkeypatch.setattr(m, "get", fake_get)
    ok, detail = m.check_databento(KEY)
    assert ok and "datasets=2" in detail and "EQUS.MINI=yes" in detail and KEY not in detail
    url, headers = seen[0]
    assert len(seen) == 1 and url.endswith("/v0/metadata.list_datasets") and KEY not in url
    assert headers["Authorization"].startswith("Basic ")
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (401, None))
    assert m.check_databento(KEY) == (False, "http 401 (key rejected)")


def test_main_reads_env_file_argument_and_reports_databento(monkeypatch, tmp_path, capsys):
    m = _load()

    def fake_get(url, headers=None, data=None):
        return (200, ["EQUS.MINI"]) if "databento" in url else (200, {})

    monkeypatch.setattr(m, "get", fake_get)
    assert m.main(_env(tmp_path, f"DATABENTO_API_KEY={KEY}\n")) == 0
    out = capsys.readouterr().out
    assert "PASS  Databento auth" in out and KEY not in out
    assert "SKIP  Alpaca" in out and HINT not in out


def test_main_skips_databento_without_a_key(monkeypatch, tmp_path, capsys):
    m = _load()
    urls = []
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (urls.append(url), (200, {}))[1])
    m.main(_env(tmp_path))
    assert "SKIP  Databento" in capsys.readouterr().out
    assert not any("databento" in u for u in urls)


def test_main_sets_certifi_bundle_before_the_first_request(monkeypatch, tmp_path, capsys):
    m = _load()
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append(os.environ.get("SSL_CERT_FILE"))
        return 200, {}

    monkeypatch.setattr(m, "get", fake_get)
    m.main(_env(tmp_path))
    assert seen and set(seen) == {BUNDLE}
    assert HINT not in capsys.readouterr().out


def test_main_keeps_an_existing_ssl_cert_file(monkeypatch, tmp_path):
    m = _load()
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own/roots.pem")
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (200, {}))
    m.main(_env(tmp_path))
    assert os.environ["SSL_CERT_FILE"] == "/my/own/roots.pem"


def test_status_0_prints_the_certificate_hint_once_at_the_end(monkeypatch, tmp_path, capsys):
    m = _load()

    def no_roots(req, timeout=None):
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")

    monkeypatch.setattr(urllib.request, "urlopen", no_roots)
    assert m.main(_env(tmp_path, f"DATABENTO_API_KEY={KEY}\nFINNHUB_API_KEY=fh-SECRET-not-real\n")) == 1
    out = capsys.readouterr().out
    assert "http 0" in out and out.count(HINT) == 1
    assert out.rstrip().splitlines()[-1] == HINT
    assert KEY not in out and "fh-SECRET-not-real" not in out


def test_no_hint_when_failures_are_real_http_statuses(monkeypatch, tmp_path, capsys):
    m = _load()

    def unauthorised(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", unauthorised)
    assert m.main(_env(tmp_path, f"DATABENTO_API_KEY={KEY}\n")) == 1
    assert HINT not in capsys.readouterr().out


def test_hint_state_does_not_leak_between_runs(monkeypatch, tmp_path, capsys):
    m = _load()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")))
    m.main(_env(tmp_path))
    assert HINT in capsys.readouterr().out
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (200, {}))
    m.main(_env(tmp_path))
    assert HINT not in capsys.readouterr().out
