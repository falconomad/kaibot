"""Audit 2 7.2 (#712): the helpers that only need market data hold a data-only key pair of a second paper account,
never the real paper account's trading pair (common/secret_files.py, scripts/run_ticker_checks.py,
scripts/data_key_check.py; docs/ops/secrets.md)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import urllib.parse
from pathlib import Path

import pytest

from kaibot.common.secret_files import data_keys, names_in, read_names

ROOT = Path(__file__).resolve().parents[2]
TRADING = ("PKTRADING123", "tradingsecret456")
DATA = ("PKDATA789", "datasecret012")


def load(name):
    spec = importlib.util.spec_from_file_location(f"kb_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def files(tmp_path):
    engine = tmp_path / ".env"
    engine.write_text(f"ALPACA_API_KEY={TRADING[0]}\nALPACA_SECRET={TRADING[1]}\nSUPABASE_URL=https://x.supabase.co\n"
                      "SUPABASE_SERVICE_KEY=sb_secret_engine\nFINNHUB_API_KEY=fh  # comment\n")
    data = tmp_path / "data.env"
    data.write_text(f'ALPACA_DATA_KEY={DATA[0]}\nexport ALPACA_DATA_SECRET="{DATA[1]}"\n')
    helper = tmp_path / "helper.env"
    helper.write_text("SUPABASE_PUBLISHABLE_KEY=sb_publishable_x\n")
    return engine, data, helper


def test_read_names_keeps_only_what_is_asked(files):
    engine, _, _ = files
    got = read_names(engine, ("SUPABASE_SERVICE_KEY", "FINNHUB_API_KEY"))
    assert got == {"SUPABASE_SERVICE_KEY": "sb_secret_engine", "FINNHUB_API_KEY": "fh"}
    assert names_in(engine) >= {"ALPACA_API_KEY", "ALPACA_SECRET"} and read_names(engine.with_name("nope"), ["X"]) == {}


def test_the_data_file_holds_the_data_pair_only(files, monkeypatch):
    _, data, _ = files
    assert data_keys(str(data)) == DATA
    monkeypatch.setenv("KAIBOT_DATA_ENV_FILE", str(data))
    assert data_keys() == DATA
    for extra in (f"ALPACA_API_KEY={TRADING[0]}", f"ALPACA_SECRET={TRADING[1]}", "SUPABASE_SERVICE_ROLE_KEY=x"):
        bad = data.with_name("bad.env")
        bad.write_text(data.read_text() + extra + "\n")
        with pytest.raises(SystemExit, match="remove it") as e:
            data_keys(str(bad))
        assert TRADING[1] not in str(e.value)
    with pytest.raises(SystemExit, match="no market-data key file"):
        data_keys(str(data.with_name("none.env")))
    data.write_text(f"ALPACA_DATA_KEY={DATA[0]}\n")
    with pytest.raises(SystemExit, match="missing ALPACA_DATA_SECRET"):
        data_keys(str(data))


def test_the_ticker_worker_never_holds_the_trading_pair(files, monkeypatch):
    """Its Alpaca pair is the data pair; from the engine's .env it keeps the Supabase key and Finnhub only, and puts
    nothing in the environment."""
    engine, data, _ = files
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "SUPABASE_SERVICE_KEY", "KAIBOT_ENV_FILE"):
        monkeypatch.delenv(k, raising=False)
    tc = load("run_ticker_checks")
    k = tc.load_keys(str(engine), str(data))
    assert (k["alpaca_key"], k["alpaca_secret"]) == DATA and k["service_key"] == "sb_secret_engine"
    assert k["finnhub"] == "fh" and k["supabase_url"] == "https://x.supabase.co"
    assert not any(t in json.dumps(k) for t in TRADING)
    assert "ALPACA_API_KEY" not in os.environ and "KAIBOT_ENV_FILE" not in os.environ
    src = (ROOT / "scripts" / "run_ticker_checks.py").read_text()
    body = src[src.index("def load_keys"):src.index("def legacy_keys")]
    assert "Settings.from_env" not in body and "load_env_file" not in body and "os.environ" not in body
    main = src[src.index("def main"):]
    assert main.index("data_env_path(a.data_env).exists()") < main.index("load_keys(") < main.index("legacy_keys(")
    with pytest.raises(SystemExit, match="no market-data key file"):
        tc.load_keys(str(engine), str(data.with_name("none.env")))


def test_without_data_env_the_ticker_worker_runs_as_before(files, monkeypatch):
    """Product (26 Nov 2026): no data.env yet: the worker keeps the engine's .env and its paper keys, as before."""
    engine, _, _ = files
    monkeypatch.setattr(os, "environ", dict(os.environ))  # legacy_keys fills the environment, as before #712
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "SUPABASE_SERVICE_KEY", "KAIBOT_ENV_FILE", "FRED_API_KEY"):
        os.environ.pop(k, None)
    os.environ["FRED_API_KEY"] = "fred"
    tc = load("run_ticker_checks")
    k = tc.legacy_keys(str(engine))
    assert (k["alpaca_key"], k["alpaca_secret"]) == TRADING and k["service_key"] == "sb_secret_engine"


class Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def alpaca(accounts):
    """A fake Alpaca: /v2/account answers per key; the data account holds nothing; the latest bar exists."""
    calls = []

    def opener(req, timeout):
        key = req.get_header("Apca-api-key-id")
        path = urllib.parse.urlsplit(req.full_url).path
        calls.append((req.get_method(), path, key))
        assert req.get_method() == "GET"
        if path == "/v2/account":
            return Resp(200, accounts[key])
        if path == "/v2/positions":
            return Resp(200, [])
        return Resp(200, {"bar": {"c": 600.0}})
    return opener, calls


def test_the_check_passes_only_for_a_second_account_and_prints_no_secret(files, capsys):
    engine, data, helper = files
    dk = load("data_key_check")
    opener, calls = alpaca({DATA[0]: {"id": "acct-data", "account_number": "PA1111DATA"},
                            TRADING[0]: {"id": "acct-trade", "account_number": "PA2222TRAD"}})
    rows = dk.check(str(data), str(helper), str(engine), opener)
    assert all(ok for _, ok, _ in rows), rows
    assert {m for m, _, _ in calls} == {"GET"}  # it sends no order
    text = json.dumps(rows)
    assert not any(s in text for s in (*TRADING, *DATA)) and "...DATA" in text and "...TRAD" in text
    same, _ = alpaca({DATA[0]: {"id": "acct-trade"}, TRADING[0]: {"id": "acct-trade"}})
    rows = dk.check(str(data), str(helper), str(engine), same)
    assert [n for n, ok, _ in rows if not ok] == ["the data account is not the trading account (its key cannot "
                                                  "trade the real run)"]
    helper.write_text(helper.read_text() + f"ALPACA_API_KEY={TRADING[0]}\n")
    rows = dk.check(str(data), str(helper), str(engine), opener)
    assert [n for n, ok, _ in rows if not ok] == ["helper.env holds no trading pair and no service key"]
