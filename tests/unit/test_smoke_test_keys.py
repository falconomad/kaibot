"""S54: smoke_test_keys.py Databento auth check and free-plan SIP history probe (network faked)."""

from __future__ import annotations

import importlib.util
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_test_keys.py"
KEY = "db-SECRET-not-real"
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)


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
