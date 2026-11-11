"""S54: smoke_test_keys.py Databento auth check and free-plan SIP history probe (network faked)."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
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


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test_keys", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sip_probe_pass_uses_window_older_than_15_min(monkeypatch, capsys):
    m = _load()
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append(url)
        return 200, {"bars": [{"t": "2026-11-10T14:30:00Z", "c": 1.0}]}

    monkeypatch.setattr(m, "get", fake_get)
    assert m.check_sip_history({"APCA-API-KEY-ID": "k"}, now=NOW)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(seen[0]).query))
    assert q["feed"] == "sip" and q["timeframe"] == "1Min"
    end = datetime.fromisoformat(q["end"].replace("Z", "+00:00"))
    assert (NOW - end).total_seconds() >= 15 * 60
    assert capsys.readouterr().out.startswith("PASS")


def test_sip_probe_fail_on_403_or_no_bars(monkeypatch, capsys):
    m = _load()
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (403, None))
    assert not m.check_sip_history({}, now=NOW)
    assert "FAIL" in capsys.readouterr().out
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (200, {"bars": None}))
    assert not m.check_sip_history({}, now=NOW)


def test_databento_check_is_zero_cost_and_never_prints_key(monkeypatch, capsys):
    m = _load()
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append((url, headers))
        return 200, ["XNAS.ITCH", "GLBX.MDP3"]

    monkeypatch.setattr(m, "get", fake_get)
    assert m.check_databento(KEY)
    url, headers = seen[0]
    assert url.endswith("/v0/metadata.list_datasets") and KEY not in url
    assert headers["Authorization"].startswith("Basic ")
    out = capsys.readouterr().out
    assert out.startswith("PASS") and KEY not in out
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (401, None))
    assert not m.check_databento(KEY)
    assert "key rejected" in capsys.readouterr().out


def test_main_reads_kaibot_env_file_and_reports_databento(monkeypatch, tmp_path, capsys):
    m = _load()
    env = tmp_path / "secrets.env"
    env.write_text(f"DATABENTO_API_KEY={KEY}\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(env))

    def fake_get(url, headers=None, data=None):
        return (200, ["XNAS.ITCH"]) if "databento" in url else (200, {})

    monkeypatch.setattr(m, "get", fake_get)
    assert m.main(["smoke_test_keys.py"]) == 0
    out = capsys.readouterr().out
    assert "Databento auth" in out and KEY not in out
    assert "SKIP  Alpaca" in out


def _empty_env(tmp_path, monkeypatch):
    env = tmp_path / "secrets.env"
    env.write_text("SEC_USER_AGENT=kaibot test@example.com\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(env))


def test_main_sets_certifi_bundle_before_the_first_request(monkeypatch, tmp_path, capsys):
    m = _load()
    _empty_env(tmp_path, monkeypatch)
    seen = []

    def fake_get(url, headers=None, data=None):
        seen.append(os.environ.get("SSL_CERT_FILE"))
        return 200, {}

    monkeypatch.setattr(m, "get", fake_get)
    m.main(["smoke_test_keys.py"])
    assert seen and set(seen) == {BUNDLE}
    assert HINT not in capsys.readouterr().out


def test_main_keeps_an_existing_ssl_cert_file(monkeypatch, tmp_path):
    m = _load()
    _empty_env(tmp_path, monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", "/my/own/roots.pem")
    monkeypatch.setattr(m, "get", lambda url, headers=None, data=None: (200, {}))
    m.main(["smoke_test_keys.py"])
    assert os.environ["SSL_CERT_FILE"] == "/my/own/roots.pem"


def test_status_0_prints_the_certificate_hint_once_at_the_end(monkeypatch, tmp_path, capsys):
    m = _load()
    env = tmp_path / "secrets.env"
    env.write_text(f"DATABENTO_API_KEY={KEY}\nFINNHUB_API_KEY=fh-SECRET-not-real\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(env))

    def no_roots(req, timeout=None):
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")

    monkeypatch.setattr(urllib.request, "urlopen", no_roots)
    assert m.main(["smoke_test_keys.py"]) == 1
    out = capsys.readouterr().out
    assert "http 0" in out and out.count(HINT) == 1
    assert out.rstrip().splitlines()[-1] == HINT
    assert KEY not in out and "fh-SECRET-not-real" not in out


def test_no_hint_when_failures_are_real_http_statuses(monkeypatch, tmp_path, capsys):
    m = _load()
    env = tmp_path / "secrets.env"
    env.write_text(f"DATABENTO_API_KEY={KEY}\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(env))

    def unauthorised(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", unauthorised)
    assert m.main(["smoke_test_keys.py"]) == 1
    assert HINT not in capsys.readouterr().out
