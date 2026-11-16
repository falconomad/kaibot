"""scripts/alpaca_fee_activities.py (#238): the FEE activities of the PAPER account, read-only, with a fake Alpaca.
The keys come from the engine's .env through the engine's loader and are never printed; any base URL other than the
paper endpoint is refused before anything is sent; only GET /v2/account/activities is ever called."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaibot.engine.config import PAPER_TRADING_URL

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SCRIPT = SCRIPTS / "alpaca_fee_activities.py"
KEY, SECRET = "PKFAKEKEY123456", "fakeSecretValue987654"
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)


def load():
    spec = importlib.util.spec_from_file_location("alpaca_fee_activities", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fee(n: int) -> dict:
    return {"id": f"2026110{n % 9 + 1}000000000::secret-activity-{n}", "activity_type": "FEE",
            "date": f"2026-11-{n % 9 + 1:02d}", "net_amount": f"-0.{n % 9 + 1}3", "description": "TAF fee",
            "status": "executed", "qty": "0", "per_share_amount": None}


class FakeAlpaca:
    def __init__(self, rows=()):
        self.rows = list(rows)  # newest first, as Alpaca returns them with direction=desc
        self.calls: list[tuple] = []

    def call(self, method, url, params=None, body=None):
        self.calls.append((method, url, dict(params or {}), body))
        assert method == "GET" and url == f"{PAPER_TRADING_URL}/v2/account/activities" and body is None
        rows, token = self.rows, (params or {}).get("page_token")
        if token:
            rows = rows[[r["id"] for r in rows].index(token) + 1:]
        return [dict(r) for r in rows[:params["page_size"]]]


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


def test_it_prints_the_count_the_total_and_the_shape_of_three_entries_never_a_key_or_an_id(env, capsys):
    fake = FakeAlpaca([fee(n) for n in range(5)])
    assert load().main([], http=fake, now=NOW) == 0
    out = capsys.readouterr().out
    (call,) = fake.calls
    assert call[2] == {"activity_types": "FEE", "direction": "desc", "page_size": 100,
                       "after": "2026-08-18T15:00:00Z"}  # 90 days back
    assert "last 90 days: 5" in out and "net_amount: total -1.65 over 5 entries" in out
    assert "from 2026-11-01 to 2026-11-05" in out
    assert out.count("shown (newest first)") == 3 and "entry 3 of 3" in out
    assert "net_amount (str)" in out and "per_share_amount (NoneType)" in out and "id (str)" in out
    assert "net_amount = -0.13" in out and "description = TAF fee" in out and "date = 2026-11-01" in out
    assert KEY not in out and SECRET not in out and "secret-activity" not in out


def test_no_entries_is_said_plainly_and_is_not_a_failure(env, capsys):
    assert load().main([], http=FakeAlpaca(), now=NOW) == 0
    out = capsys.readouterr().out
    assert "last 90 days: 0" in out and "None posted" in out


def test_it_reads_every_page(env, capsys):
    fake = FakeAlpaca([fee(n) for n in range(250)])
    assert load().main([], http=fake, now=NOW) == 0
    assert len(fake.calls) == 3 and fake.calls[1][2]["page_token"] == fee(99)["id"]
    out = capsys.readouterr().out
    assert "last 90 days: 250" in out and "there are more" not in out


def test_only_gets_are_ever_sent():
    src = SCRIPT.read_text()
    assert src.count("http.call(") == 1 and 'http.call("GET"' in src
    for word in ("PATCH", "POST", "DELETE", "PUT"):
        assert f'"{word}"' not in src
    sh = (SCRIPTS / "alpaca_fee_activities.sh").read_text()
    assert "alpaca_fee_activities.py" in sh and "curl" not in sh and "cat " not in sh


@pytest.mark.parametrize("url", ["https://api.alpaca.markets", "https://paper-api.alpaca.markets.evil.example"])
def test_a_non_paper_base_url_is_refused_before_anything_is_sent(env, url, capsys):
    env.write_text(env.read_text() + f"ALPACA_BASE_URL={url}\n")
    fake = FakeAlpaca([fee(1)])
    with pytest.raises(SystemExit, match="paper"):
        load().main([], http=fake, now=NOW)
    assert fake.calls == [] and KEY not in capsys.readouterr().out


def test_missing_keys_are_named_not_shown(tmp_path, monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET", "ALPACA_API_SECRET", "ALPACA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / ".env"
    f.write_text("FRED_API_KEY=x\n")
    monkeypatch.setenv("KAIBOT_ENV_FILE", str(f))
    with pytest.raises(SystemExit, match="ALPACA_API_KEY"):
        load().main([], http=FakeAlpaca(), now=NOW)


def test_an_http_error_prints_its_status_only(env, capsys):
    from kaibot.broker.alpaca import AlpacaError

    class Down(FakeAlpaca):
        def call(self, method, url, params=None, body=None):
            raise AlpacaError(403, f"forbidden {KEY}")

    assert load().main([], http=Down(), now=NOW) == 2
    out = capsys.readouterr().out
    assert "403" in out and KEY not in out
