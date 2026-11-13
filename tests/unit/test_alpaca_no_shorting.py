"""scripts/alpaca_no_shorting.py (#206, decision 15 section 11): shorting off on the PAPER account, with a fake
Alpaca. The keys come from the engine's .env through the engine's loader and are never printed; any base URL other
than the paper endpoint is refused before anything is sent."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from kaibot.engine.config import PAPER_TRADING_URL

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "alpaca_no_shorting.py"
KEY, SECRET = "PKFAKEKEY123456", "fakeSecretValue987654"


def load():
    spec = importlib.util.spec_from_file_location("alpaca_no_shorting", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeAlpaca:
    def __init__(self, applies: bool = True):
        self.calls: list[tuple] = []
        self.cfg = {"no_shorting": False, "dtbp_check": "entry"}
        self.applies = applies

    def call(self, method, url, params=None, body=None):
        self.calls.append((method, url, body))
        assert url.startswith(PAPER_TRADING_URL)
        if method == "PATCH" and url.endswith("/v2/account/configurations"):
            if self.applies:
                self.cfg.update(body)
            return dict(self.cfg)
        if url.endswith("/v2/account/configurations"):
            return dict(self.cfg)
        if url.endswith("/v2/account"):
            return {"status": "ACTIVE", "shorting_enabled": not self.cfg["no_shorting"]}
        raise AssertionError(url)


@pytest.fixture(autouse=True)
def own_environ(monkeypatch):
    """load_env_file writes os.environ: each test gets its own copy, so no fake key leaks into other tests."""
    import os

    monkeypatch.setattr(os, "environ", dict(os.environ))


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text(f"ALPACA_API_KEY={KEY}\nALPACA_SECRET={SECRET}\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(f))
    return f


def test_it_turns_shorting_off_and_prints_what_alpaca_says_never_a_key(env, capsys):
    fake = FakeAlpaca()
    assert load().main([], http=fake) == 0
    out = capsys.readouterr().out
    assert fake.calls[0] == ("PATCH", f"{PAPER_TRADING_URL}/v2/account/configurations", {"no_shorting": True})
    assert [c[:2] for c in fake.calls[1:]] == [("GET", f"{PAPER_TRADING_URL}/v2/account/configurations"),
                                               ("GET", f"{PAPER_TRADING_URL}/v2/account")]
    assert "no_shorting = true" in out and "shorting_enabled = false" in out and "OK" in out
    assert KEY not in out and SECRET not in out


def test_it_says_so_when_alpaca_does_not_confirm(env, capsys):
    assert load().main([], http=FakeAlpaca(applies=False)) == 1
    assert "NOT CONFIRMED" in capsys.readouterr().out


@pytest.mark.parametrize("url", ["https://api.alpaca.markets", "https://paper-api.alpaca.markets.evil.example"])
def test_a_non_paper_base_url_is_refused_before_anything_is_sent(env, monkeypatch, url, capsys):
    env.write_text(env.read_text() + f"ALPACA_BASE_URL={url}\n")
    fake = FakeAlpaca()
    with pytest.raises(SystemExit, match="paper"):
        load().main([], http=fake)
    assert fake.calls == [] and KEY not in capsys.readouterr().out


def test_missing_keys_are_named_not_shown(tmp_path, monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text("FRED_API_KEY=x\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(f))
    with pytest.raises(SystemExit, match="ALPACA_API_KEY"):
        load().main([], http=FakeAlpaca())


def test_an_http_error_prints_its_status_only(env, capsys):
    from kaibot.broker.alpaca import AlpacaError

    class Down(FakeAlpaca):
        def call(self, method, url, params=None, body=None):
            raise AlpacaError(403, f"forbidden {KEY}")

    assert load().main([], http=Down()) == 2
    out = capsys.readouterr().out
    assert "403" in out and KEY not in out
